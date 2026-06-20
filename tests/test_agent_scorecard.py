from __future__ import annotations

from pathlib import Path

from aedt_agent.agent.graph_template import graph_template_from_mapping
from aedt_agent.agent.graph_runner import run_graph
from aedt_agent.agent.orchestrator import AgentRuntime
from aedt_agent.agent.scorecard import score_mission
from aedt_agent.agent.workers import BRD_LOCAL_CUT_BUILD_CAPABILITY, InMemoryWorkerRegistry, build_brd_local_cut_job_input, run_brd_local_cut_worker
from aedt_agent.infrastructure import SQLiteMissionStore


def _runtime(tmp_path: Path) -> AgentRuntime:
    registry = InMemoryWorkerRegistry()
    registry.register(BRD_LOCAL_CUT_BUILD_CAPABILITY, run_brd_local_cut_worker)
    return AgentRuntime(SQLiteMissionStore(tmp_path / "mission.db"), registry=registry)


def _payload(tmp_path: Path) -> dict:
    layout_file = tmp_path / "case.brd"
    layout_file.write_text("brd", encoding="utf-8")
    return build_brd_local_cut_job_input(
        layout_file=layout_file,
        signal_nets=["56G_TX0_P", "56G_TX0_N"],
        reference_nets=["GND"],
        local_cut_region={"type": "bbox", "unit": "mil", "x_min": 1, "y_min": 2, "x_max": 3, "y_max": 4},
        artifact_dir=tmp_path / "artifacts",
        target_metrics=[{"metric": "s21_db_at_56g", "op": ">=", "value": -8.0}],
    )


def test_scorecard_passes_completed_brd_build_mission(tmp_path):
    runtime = _runtime(tmp_path)
    mission = runtime.create_mission("构建 local cut", [], [])
    runtime.create_job(mission.mission_id, BRD_LOCAL_CUT_BUILD_CAPABILITY, "build", _payload(tmp_path))
    runtime.execute_next_job(mission.mission_id, "worker-1")

    report = score_mission(runtime, mission.mission_id, template_id="brd_local_cut_build")

    assert report["status"] == "passed"
    assert {check["id"] for check in report["checks"]} >= {
        "mission_exists",
        "event_stream_present",
        "job_created",
        "succeeded_jobs_have_artifacts",
        "succeeded_jobs_have_evidence_summary",
    }
    assert all(check["passed"] for check in report["checks"])


def test_scorecard_fails_when_mission_has_no_jobs(tmp_path):
    runtime = _runtime(tmp_path)
    mission = runtime.create_mission("空 mission", [], [])

    report = score_mission(runtime, mission.mission_id, template_id="brd_local_cut_build")

    assert report["status"] == "failed"
    failed = [check["id"] for check in report["checks"] if not check["passed"]]
    assert "job_created" in failed


def test_agent_scorecard_passes_agent_only_graph_without_jobs(
    tmp_path,
    monkeypatch,
):
    runtime = _runtime(tmp_path)
    mission = runtime.create_mission("agent optimize", [], [])
    template = graph_template_from_mapping(
        {
            "id": "brd_channel_optimize",
            "version": 1,
            "nodes": [
                {
                    "id": "decide",
                    "role": "decision_maker",
                    "kind": "agent",
                    "system_prompt": "decide",
                    "output_schema": "next_action",
                    "constraints": {"response_format": "json_object"},
                },
                {
                    "id": "scorecard",
                    "role": "scorecard",
                    "kind": "program",
                    "input_schema": "next_action",
                    "output_schema": "scorecard_report",
                },
            ],
            "edges": [
                {
                    "id": "decide-scorecard",
                    "from": "decide",
                    "to": "scorecard",
                    "on": "complete",
                }
            ],
            "handoffs": {
                "next_action": {"required_fields": ["decision", "reason"]},
                "scorecard_report": {"required_fields": ["status", "checks"]},
            },
        }
    )

    monkeypatch.setenv("AEDT_AGENT_LLM_API_KEY", "test")
    monkeypatch.setattr(
        "aedt_agent.agent.llm.llm_complete",
        lambda *args, **kwargs: '{"decision":"complete","reason":"all good","edge_outcome":"complete","llm_model":"test-model","planning_source":"llm","evidence_summary":{"decision_rule":"target_met"}}',
    )

    report = run_graph(runtime, mission.mission_id, template, initial_payload={})

    assert report["status"] == "succeeded"
    assert runtime.list_jobs(mission.mission_id) == []
    score = score_mission(
        runtime,
        mission.mission_id,
        template_id="brd_channel_optimize",
    )
    assert score["status"] == "passed"
