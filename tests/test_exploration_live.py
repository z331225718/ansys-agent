from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from aedt_agent.capability_learning import CapabilityTraceStore
from aedt_agent.live.approval import HmacApprovalAuthority
from aedt_agent.live.backend import LiveAedtBackend
from aedt_agent.live.broker import LiveAedtError
from aedt_agent.live.manager import LiveAedtSessionManager
from aedt_agent.exploration.validator import OperationValidator


class Desktop:
    aedt_process_id = 42
    port = 50061
    project_list = ["Board"]

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def active_project(self):
        return SimpleNamespace(GetName=lambda: "Board")

    def active_design(self, project):
        return SimpleNamespace(GetName=lambda: "Layout1", GetDesignType=lambda: "HFSS 3D Layout Design")

    def design_list(self, project=None):
        return ["Layout1"]

    def release_desktop(self, **kwargs):
        return True


class Line3dLayout:
    def __init__(self):
        self.name = "line1"
        self.net_name = "N1"
        self.placement_layer = "L1"
        self.width = "0.1mm"


class Layout:
    def __init__(self, **kwargs):
        self.project_name = kwargs["project"]
        self.design_name = kwargs["design"]
        line = Line3dLayout()
        self.modeler = SimpleNamespace(lines={"line1": line}, line_names=["line1"])


class Registry:
    def __init__(self):
        self.backend = LiveAedtBackend(desktop_factory=Desktop, layout_factory=Layout)
        self.calls = []

    def execute(self, target, command, arguments, *, version="2026.1", **kwargs):
        self.calls.append((target, command, arguments))
        return self.backend.execute(target, command, arguments)

    def has_target(self, target, *, version="2026.1"):
        return self.backend._desktop is not None

    def release(self, target, *, version="2026.1"):
        return {"released": self.backend.release()}

    def close(self):
        self.backend.release()


class ReviewOnlyPromoter:
    def __init__(self):
        self.calls = []

    def promote(self, trace_id, *, target_kind="auto"):
        self.calls.append((trace_id, target_kind))
        return SimpleNamespace(
            to_dict=lambda: {
                "candidate_id": "review-candidate-1",
                "trace_id": trace_id,
                "kind": "harness",
                "state": "candidate",
                "candidate_dir": ".aedt-agent/capability-candidates/review-candidate-1",
                "files": ["candidate.patch"],
            }
        )


def _validator():
    return OperationValidator(
        package_versions={"pyaedt": "1.3.0", "pyedb": "0.80.2"},
        evidence_verifier=lambda evidence: {
            "status": "verified",
            "manifest_digest": "test-manifest",
            "evidence": evidence,
        },
    )


def _plan():
    return {
        "schema_version": "ansys-operation-plan/v1",
        "intent": "Parameterize one selected line width through a bounded property edit",
        "target": {"product": "hfss3dlayout", "project_name": "Board", "design_name": "Layout1"},
        "risk": "reversible_edit",
        "evidence": [
            {
                "package": "pyaedt",
                "package_version": "1.3.0",
                "project": "ansys-pyaedt-current",
                "symbol": "Line3dLayout.width",
                "source_path": "modeler/pcb/object_3d_layout.py",
                "snippet_digest": "b" * 64,
                "query_id": "query-current-line-width",
            }
        ],
        "steps": [{"id": "set-width", "op": "set_attr", "path": "modeler.lines.line1.width", "value": "trace_w"}],
        "readback": [
            {
                "id": "check-width",
                "path": "modeler.lines.line1.width",
                "operator": "equals",
                "expected": "trace_w",
            }
        ],
        "rollback": ["set-width"],
    }


def _read_only_plan():
    plan = _plan()
    plan["intent"] = "Read one selected line width through a bounded property lookup"
    plan["risk"] = "read_only"
    plan["steps"] = [{"id": "read-width", "op": "read_attr", "path": "modeler.lines.line1.width"}]
    plan["readback"] = []
    plan["rollback"] = []
    return plan


def test_live_exploration_requires_approval_verifies_readback_and_seals_trace(tmp_path):
    registry = Registry()
    authority = HmacApprovalAuthority("e" * 32)
    promoter = ReviewOnlyPromoter()
    manager = LiveAedtSessionManager(
        registry=registry,
        approval_verifier=authority,
        required_project="Board",
        required_design="Layout1",
        trace_store=CapabilityTraceStore(tmp_path / "traces"),
        capability_promoter=promoter,
        exploration_validator=_validator(),
    )
    session_id = manager.attach(pid=42)["live_session_id"]

    proposed = manager.propose_exploratory_operation(_plan())
    validated = manager.validate_exploratory_operation(proposed["candidate_id"])
    assert validated["mutation_count"] == 1
    preview = manager.preview_exploratory_operation(session_id, candidate_id=proposed["candidate_id"])
    assert preview["approval_required"] is True
    assert registry.backend._apps[("layout", "Board", "Layout1")].modeler.lines["line1"].width == "0.1mm"
    token = authority.issue(**preview["approval_request"])
    result = manager.apply_exploratory_operation(
        session_id,
        preview_id=preview["preview_id"],
        approval_token=token,
    )
    assert result["status"] == "verified"
    inventory = manager.list_layout_paths(
        session_id,
        project_name="Board",
        design_name="Layout1",
    )
    assert inventory["paths"][0]["width_expression"] == "trace_w"
    captured = manager.capture_capability_trace(proposed["candidate_id"])
    assert captured["trace_id"] == proposed["trace_id"]
    assert captured["server_owned"] is True
    assert captured["promotion_eligible"] is True
    assert captured["trace"]["state"] == "verified"
    assert captured["trace"]["sealed"] is True
    assert [item["state"] for item in captured["trace"]["events"]] == [
        "proposed",
        "validated",
        "previewed",
        "approved",
        "applied",
        "verified",
    ]
    assert token not in json.dumps(captured)

    promoted = manager.promote_capability_candidate(proposed["trace_id"])
    assert promoted["status"] == "candidate"
    assert promoted["auto_applied"] is False
    assert promoted["hot_registered"] is False
    assert promoted["committed"] is False
    assert promoter.calls == [(proposed["trace_id"], "auto")]

    with pytest.raises(LiveAedtError) as unknown:
        manager.capture_capability_trace("explore-candidate-not-server-owned")
    assert unknown.value.code == "candidate_not_found"

    with pytest.raises(LiveAedtError) as unowned:
        manager.promote_capability_candidate("trace-" + "0" * 32)
    assert unowned.value.code == "trace_not_owned"


def test_live_exploration_rejects_wrong_design_before_backend_preview_and_seals_failure(tmp_path):
    registry = Registry()
    manager = LiveAedtSessionManager(
        registry=registry,
        approval_verifier=HmacApprovalAuthority("f" * 32),
        required_project="Board",
        required_design="OtherLayout",
        trace_store=CapabilityTraceStore(tmp_path / "traces"),
        exploration_validator=_validator(),
    )
    session_id = manager.attach(pid=42)["live_session_id"]
    proposed = manager.propose_exploratory_operation(_plan())
    manager.validate_exploratory_operation(proposed["candidate_id"])
    try:
        manager.preview_exploratory_operation(session_id, candidate_id=proposed["candidate_id"])
    except Exception as exc:
        assert getattr(exc, "code", None) == "design_forbidden"
    else:
        raise AssertionError("wrong design must fail closed")
    captured = manager.capture_capability_trace(proposed["candidate_id"])
    assert captured["trace"]["state"] == "failed"
    assert captured["trace"]["sealed"] is True
    assert captured["promotion_eligible"] is False


def test_rejected_host_approval_seals_trace_without_apply(tmp_path):
    class RejectingApprovalHost:
        def register(self, action, resource_id, digest, preview):
            return {"status": "pending"}

        def poll(self, resource_id, timeout_seconds=0):
            return {"status": "rejected", "approval_token": None}

        def verify(self, action, resource_id, digest, token):
            raise AssertionError("rejected approval must never be verified")

    manager = LiveAedtSessionManager(
        registry=Registry(),
        approval_verifier=RejectingApprovalHost(),
        required_project="Board",
        required_design="Layout1",
        trace_store=CapabilityTraceStore(tmp_path / "traces"),
        exploration_validator=_validator(),
    )
    session_id = manager.attach(pid=42)["live_session_id"]
    proposed = manager.propose_exploratory_operation(_plan())
    manager.validate_exploratory_operation(proposed["candidate_id"])
    preview = manager.preview_exploratory_operation(session_id, candidate_id=proposed["candidate_id"])

    decision = manager.wait_for_approval(session_id, preview_id=preview["preview_id"])
    assert decision["status"] == "rejected"
    captured = manager.capture_capability_trace(proposed["candidate_id"])
    assert captured["trace"]["state"] == "rejected"
    assert [item["state"] for item in captured["trace"]["events"]][-1] == "rejected"
    with pytest.raises(LiveAedtError) as blocked:
        manager.apply_exploratory_operation(
            session_id,
            preview_id=preview["preview_id"],
            approval_token="invented",
        )
    assert blocked.value.code == "preview_required"


def test_read_only_exploration_trace_skips_approval(tmp_path):
    manager = LiveAedtSessionManager(
        registry=Registry(),
        approval_verifier=HmacApprovalAuthority("r" * 32),
        required_project="Board",
        required_design="Layout1",
        trace_store=CapabilityTraceStore(tmp_path / "traces"),
        exploration_validator=_validator(),
    )
    session_id = manager.attach(pid=42)["live_session_id"]
    proposed = manager.propose_exploratory_operation(_read_only_plan())
    manager.validate_exploratory_operation(proposed["candidate_id"])
    preview = manager.preview_exploratory_operation(session_id, candidate_id=proposed["candidate_id"])
    assert preview["approval_required"] is False

    result = manager.apply_exploratory_operation(session_id, preview_id=preview["preview_id"])
    assert result["status"] == "verified"
    captured = manager.capture_capability_trace(proposed["candidate_id"])
    assert [item["state"] for item in captured["trace"]["events"]] == [
        "proposed",
        "validated",
        "previewed",
        "applied",
        "verified",
    ]
