from __future__ import annotations

from pathlib import Path

from aedt_agent.ansys_agent.case_config import AnsysAgentCase
from aedt_agent.ansys_agent.status import summarize_graph_report


def _case(tmp_path: Path) -> AnsysAgentCase:
    loop_config = tmp_path / "loop.json"
    loop_config.write_text(
        '{"report_dir": "' + str((tmp_path / "progress")).replace("\\", "\\\\") + '"}',
        encoding="utf-8",
    )
    return AnsysAgentCase(
        case_id="case-1",
        db_path=tmp_path / "missions.db",
        loop_config=loop_config,
        execution_profile=tmp_path / "profile.json",
        dashboard_host="0.0.0.0",
        dashboard_port=9876,
        source_path=tmp_path / "case.json",
    )


def test_agent_status_extracts_bounded_metrics_from_history_csv(tmp_path: Path):
    history = tmp_path / "progress" / "optimization_history.csv"
    history.parent.mkdir()
    history.write_text(
        "\n".join(
            [
                "round_index,round_status,score_status,rl_worst_db,"
                "insertion_worst_db_in_band,tdr_observation_port,"
                "tdr_peak_deviation_ohm,objective_total_cost,action_type,"
                "continue_recommendation",
                "1,completed,fail,-16.8,-1.2,Diff1,8.7,123.4,"
                "anti_pad.enlarge,continue",
            ]
        ),
        encoding="utf-8",
    )
    report = {
        "status": "waiting_approval",
        "graph_run": {
            "graph_run_id": "graph-1",
            "mission_id": "mission-1",
            "current_node_id": "action_approval_gate",
        },
        "node_runs": [
            {
                "node_id": "progress_report_worker",
                "sequence": 1,
                "status": "succeeded",
                "artifact_refs": [str(history.parent / "channel.s4p")],
                "output_payload": {
                    "optimization_history_csv": str(history),
                    "report_html": str(history.parent / "optimization_progress.html"),
                    "plot_artifacts": [str(history.parent / "tdr.svg")],
                },
            },
            {
                "node_id": "action_approval_gate",
                "sequence": 2,
                "status": "waiting_approval",
                "output_payload": {
                    "approval_id": "approval-1",
                    "approval_reason": "geometry action needs review",
                },
            },
        ],
    }

    status = summarize_graph_report(_case(tmp_path), report)

    assert status["status"] == "waiting_approval"
    assert status["next_safe_action"] == "ask_user"
    assert status["latest_round"] == "1"
    assert status["latest_action"] == "anti_pad.enlarge"
    assert status["metrics"]["rl_worst_db"] == "-16.8"
    assert status["metrics"]["tdr_observation_port"] == "Diff1"
    assert status["approval"]["approval_id"] == "approval-1"
    assert status["pending_approvals"][0]["approval_id"] == "approval-1"
    assert status["recommended_command"].endswith(
        "status --case " + str(tmp_path / "case.json")
    )
    assert status["available_commands"]["approve"].endswith(
        "--approval-id approval-1 --option-id approve"
    )
    assert status["available_commands"]["reject"].endswith("--approval-id approval-1")
    assert status["dashboard_url"] == "http://localhost:9876"
    artifact_kinds = {item["kind"] for item in status["latest_artifacts"]}
    assert {"history_csv", "touchstone", "plot"}.issubset(artifact_kinds)


def test_agent_status_reports_failure_summary(tmp_path: Path):
    report = {
        "status": "failed",
        "graph_run": {
            "graph_run_id": "graph-1",
            "mission_id": "mission-1",
            "current_node_id": "score",
            "error": {"code": "worker_failed", "message": "score crashed"},
        },
        "node_runs": [
            {
                "node_id": "score",
                "sequence": 1,
                "status": "failed",
                "error": {"code": "worker_failed", "message": "bad csv"},
                "output_payload": {},
            }
        ],
    }

    status = summarize_graph_report(_case(tmp_path), report)

    assert status["status"] == "failed"
    assert status["next_safe_action"] == "inspect_failure"
    assert status["failure"]["graph_error"]["code"] == "worker_failed"
    assert status["failure"]["failed_nodes"][0]["node_id"] == "score"
