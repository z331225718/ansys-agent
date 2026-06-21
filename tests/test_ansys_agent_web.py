from __future__ import annotations

from pathlib import Path

from aedt_agent.ansys_agent.case_config import AnsysAgentCase
from aedt_agent.ansys_agent.web import dispatch_action, render_operator_panel


def _case(tmp_path: Path) -> AnsysAgentCase:
    return AnsysAgentCase(
        case_id="web-case",
        db_path=tmp_path / "missions.db",
        loop_config=tmp_path / "loop.json",
        execution_profile=tmp_path / "profile.json",
        poll_interval_seconds=4,
    )


def test_ansys_agent_web_renders_operator_panel(tmp_path: Path):
    html = render_operator_panel(_case(tmp_path))

    assert "ansys-agent / web-case" in html
    assert 'data-poll-seconds="10"' in html
    assert "/api/status" in html
    assert 'id="nodes"' in html
    assert "Approve + Resume" in html
    assert "raw Touchstone" not in html
    assert "raw TDR" not in html


def test_ansys_agent_web_dispatches_approval_resume(tmp_path: Path):
    class FakeSupervisor:
        case = _case(tmp_path)

        def __init__(self):
            self.calls = []

        def approve(
            self,
            *,
            approval_id: str,
            option_id: str = "approve",
            comment: str | None = None,
            resume: bool = False,
            graph_run_id: str = "",
        ):
            self.calls.append(
                {
                    "approval_id": approval_id,
                    "option_id": option_id,
                    "comment": comment,
                    "resume": resume,
                    "graph_run_id": graph_run_id,
                }
            )
            return {"status": "succeeded"}

    supervisor = FakeSupervisor()

    result = dispatch_action(
        supervisor,
        "approve",
        {
            "approval_id": "approval-1",
            "option_id": "approve",
            "comment": "ok",
            "resume": True,
            "graph_run_id": "graph-1",
        },
    )

    assert result["status"] == "succeeded"
    assert supervisor.calls == [
        {
            "approval_id": "approval-1",
            "option_id": "approve",
            "comment": "ok",
            "resume": True,
            "graph_run_id": "graph-1",
        }
    ]
