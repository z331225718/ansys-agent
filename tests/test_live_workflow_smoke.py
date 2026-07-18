from __future__ import annotations

import hashlib
import json

import pytest

from aedt_agent.agent.orchestrator.runtime import AgentRuntime
from aedt_agent.infrastructure.sqlite_mission_store import SQLiteMissionStore
from aedt_agent.interactive.smoke import run_live_layout_audit_smoke
from aedt_agent.interactive.workflows import AssistantWorkflowManager


class _SmokeLive:
    def __init__(self, *, approval_verifier):
        self.authority = approval_verifier
        self.contexts = {}
        self.released = False

    def attach(self, *, port, version):
        return {"live_session_id": "smoke-live", "port": port, "version": version}

    def project_info(self, session_id):
        return {
            "active_project": "Board",
            "active_design": "Layout1",
            "design_type": "HFSS 3D Layout Design",
        }

    def workflow_binding(self, session_id):
        return {
            "version": "2024.2",
            "pid": 42,
            "port": 50051,
            "active_project": "Board",
            "active_design": "Layout1",
        }

    def register_guarded_preview(self, session_id, *, action, result):
        resource_id = f"{session_id}:{result['preview_id']}"
        self.contexts[result["preview_id"]] = (action, resource_id, result["snapshot_digest"])
        return {
            **result,
            "approval_request": {
                "action": action,
                "resource_id": resource_id,
                "digest": result["snapshot_digest"],
            },
        }

    def authorize_guarded_preview(self, session_id, *, action, preview_id, approval_token):
        expected = self.contexts.pop(preview_id)
        assert expected[0] == action
        assert self.authority.verify(action, expected[1], expected[2], approval_token)

    def layout_routing_inventory(self, session_id, **kwargs):
        return {"path_count": 1, "nets": ["N1"], "layers": ["L1"], "design_unchanged": True}

    def layout_object_inventory(self, session_id, **kwargs):
        return {"unavailable_categories": [], "design_unchanged": True}

    def variable_inventory(self, session_id, **kwargs):
        return {"count": 1, "design_unchanged": True}

    def setup_inventory(self, session_id, **kwargs):
        return {"setup_count": 1, "design_unchanged": True}

    def release(self, session_id):
        self.released = True
        return {"released": True}

    def close(self):
        return None


def test_live_workflow_smoke_writes_hashed_read_only_evidence(tmp_path):
    result = run_live_layout_audit_smoke(
        port=50051,
        version="2024.2",
        output_dir=tmp_path,
        expected_project="Board",
        expected_design="Layout1",
        confirmed_read_only=True,
        live_factory=_SmokeLive,
        workflow_factory=lambda **kwargs: AssistantWorkflowManager(
            **kwargs,
            runtime_factory=lambda path: AgentRuntime(SQLiteMissionStore(path)),
        ),
    )

    evidence_path = tmp_path / "live_layout_audit_smoke.json"
    encoded = evidence_path.read_bytes()
    digest = (tmp_path / "live_layout_audit_smoke.json.sha256").read_text(encoding="ascii").split()[0]
    evidence = json.loads(encoded)
    assert result["status"] == "passed"
    assert evidence["read_only"] is True
    assert evidence["project_saved"] is False
    assert evidence["release"]["aedt_closed"] is False
    assert evidence["release"]["projects_closed"] is False
    assert hashlib.sha256(encoded).hexdigest() == digest


def test_live_workflow_smoke_requires_explicit_confirmation(tmp_path):
    with pytest.raises(ValueError, match="confirm-read-only"):
        run_live_layout_audit_smoke(
            port=50051,
            version="2024.2",
            output_dir=tmp_path,
        )
