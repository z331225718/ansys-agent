from __future__ import annotations

import json
from pathlib import Path

import pytest

from aedt_agent.capability_learning.cli import main
from aedt_agent.capability_learning.classifier import classify_trace
from aedt_agent.capability_learning.promoter import CapabilityPromoter, PromotionError
from aedt_agent.capability_learning.trace_store import CapabilityTraceStore


def _plan(*, risk: str = "reversible_edit", steps: list[dict] | None = None) -> dict:
    return {
        "schema_version": "ansys-operation-plan/v1",
        "intent": "parameterize a selected trace width",
        "target": {
            "product": "hfss3dlayout",
            "project_name": "SecretProject2026",
            "design_name": "LayoutDesign2026",
        },
        "risk": risk,
        "evidence": [
            {
                "package": "pyaedt",
                "package_version": "1.0.1",
                "project": "ansys-pyaedt-test",
                "symbol": "Line3dLayout.width",
                "source_path": "C:/private/site-packages/pyaedt/modeler.py",
                "snippet_digest": "a" * 64,
                "query_id": "query-test",
            }
        ],
        "steps": steps
        or [
            {
                "id": "set-width",
                "op": "set_attr",
                "path": "modeler.lines.Line007.width",
                "value": "$trace_width",
            }
        ],
        "readback": [
            {
                "id": "verify-width",
                "path": "modeler.lines.Line007.width",
                "operator": "equals",
                "expected": "$trace_width",
            }
        ],
        "rollback": ["set-width"],
        "password": "sk-must-never-escape",
    }


def _verified_trace(store: CapabilityTraceStore, *, plan: dict | None = None) -> str:
    trace_id = store.create(
        candidate_id="exploration-candidate",
        intent="parameterize a selected trace width",
        plan=plan or _plan(),
        environment={"api_key": "sk-environment-secret"},
    )["trace_id"]
    store.transition(trace_id, "validated", "plan_validated")
    store.transition(trace_id, "previewed", "preview_created")
    store.transition(trace_id, "approved", "host_approved", {"approval_token": "one-use-secret"})
    store.transition(trace_id, "applied", "operation_applied")
    store.transition(trace_id, "verified", "readback_verified")
    return trace_id


def _candidate_root(tmp_path: Path) -> Path:
    return tmp_path / ".aedt-agent" / "capability-candidates"


def test_promoter_generates_only_review_candidate_and_parameterizes_trace_literals(tmp_path):
    store = CapabilityTraceStore(tmp_path / "traces")
    trace_id = _verified_trace(store)
    root = _candidate_root(tmp_path)

    result = CapabilityPromoter(store, root).promote(trace_id)
    candidate_dir = Path(result.candidate_dir)
    candidate = json.loads((candidate_dir / "candidate.json").read_text(encoding="utf-8"))
    all_text = "\n".join(path.read_text(encoding="utf-8") for path in candidate_dir.rglob("*") if path.is_file())

    assert result.kind == "harness"
    assert result.state == "candidate"
    assert candidate["source_trace"]["state"] == "verified"
    assert candidate["activation"] == {
        "auto_apply": False,
        "hot_registration": False,
        "requires_human_review": True,
        "requires_tests_before_approval": True,
    }
    assert candidate["contract_summary"]["operations"] == [
        {
            "id": "set-width",
            "op": "set_attr",
            "member": "width",
            "object_path": "$request.object_path",
            "parameters": "$request.operation_parameters.set-width",
        }
    ]
    assert candidate["contract_summary"]["api_evidence"][0]["package_version"] == "1.0.1"
    assert "source_path" not in candidate["contract_summary"]["api_evidence"][0]
    assert "SecretProject2026" not in all_text
    assert "LayoutDesign2026" not in all_text
    assert "modeler.lines.Line007.width" not in all_text
    assert "sk-must-never-escape" not in all_text
    assert "sk-environment-secret" not in all_text
    assert (candidate_dir / "candidate.patch").is_file()
    generated = (candidate_dir / "generated" / "capability.py").read_text(encoding="utf-8")
    compile(generated, "capability.py", "exec")
    assert not (tmp_path / "src").exists()
    assert not (tmp_path / "workflow_templates").exists()

    repeated = CapabilityPromoter(store, root).promote(trace_id)
    assert repeated.to_dict() == result.to_dict()

    (candidate_dir / "generated" / "capability.py").write_text("tampered\n", encoding="utf-8")
    with pytest.raises(PromotionError) as conflict:
        CapabilityPromoter(store, root).promote(trace_id)
    assert conflict.value.code == "candidate_conflict"


def test_promoter_rejects_unverified_and_tampered_traces(tmp_path):
    store = CapabilityTraceStore(tmp_path / "traces")
    unverified = store.create(candidate_id="candidate", intent="inspect", plan=_plan())["trace_id"]
    promoter = CapabilityPromoter(store, _candidate_root(tmp_path))
    with pytest.raises(PromotionError, match="sealed trace") as unverified_error:
        promoter.promote(unverified)
    assert unverified_error.value.code == "trace_not_verified"

    verified = _verified_trace(store)
    trace_path = store.root / verified / "trace.json"
    payload = json.loads(trace_path.read_text(encoding="utf-8"))
    payload["state"] = "verified-but-edited"
    trace_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(PromotionError) as tampered_error:
        promoter.promote(verified)
    assert tampered_error.value.code in {"trace_not_verified", "trace_tampered"}


def test_classifier_distinguishes_harness_skill_and_workflow():
    harness = {"intent": "set one property", "plan": _plan(), "events": []}
    skill = {
        "intent": "diagnose and compare layout properties",
        "plan": _plan(
            risk="read_only",
            steps=[
                {"id": "read-a", "op": "read_attr", "path": "modeler.lines"},
                {"id": "read-b", "op": "read_attr", "path": "modeler.vias"},
            ],
        ),
        "events": [],
    }
    workflow = {
        "intent": "iterate until accepted",
        "plan": {"loop": {"max_rounds": 3}, "steps": []},
        "events": [],
    }

    assert classify_trace(harness).kind == "harness"
    assert classify_trace(skill).kind == "skill"
    assert classify_trace(workflow).kind == "workflow"
    assert classify_trace(harness, "workflow").confidence == "requested"


@pytest.mark.parametrize(
    ("kind", "generated_name"),
    [("skill", "SKILL.md"), ("workflow", "workflow.yaml")],
)
def test_requested_candidate_kinds_remain_disabled(tmp_path, kind, generated_name):
    store = CapabilityTraceStore(tmp_path / "traces")
    trace_id = _verified_trace(store)
    result = CapabilityPromoter(store, _candidate_root(tmp_path)).promote(trace_id, target_kind=kind)
    candidate_dir = Path(result.candidate_dir)

    assert result.kind == kind
    assert (candidate_dir / "generated" / generated_name).is_file()
    assert "enabled: false" in (candidate_dir / "candidate.patch").read_text(encoding="utf-8") or kind == "skill"


def test_candidate_root_is_confined_to_review_directory(tmp_path):
    store = CapabilityTraceStore(tmp_path / "traces")
    with pytest.raises(ValueError, match="capability-candidates"):
        CapabilityPromoter(store, tmp_path / "output")


def test_promotion_cli_accepts_only_a_trace_id_and_prints_candidate(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AEDT_AGENT_TRACE_SIGNING_KEY", "test-only-trace-signing-key-32-bytes")
    store = CapabilityTraceStore()
    trace_id = _verified_trace(store)
    result = main(["promote", "--trace-id", trace_id])

    payload = json.loads(capsys.readouterr().out)
    assert result == 0
    assert payload["ok"] is True
    assert payload["candidate"]["trace_id"] == trace_id
