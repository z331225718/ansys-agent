from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "benchmarks" / "mcp_ansys_comparison"


def _module():
    spec = importlib.util.spec_from_file_location("mcp_comparison_benchmark", BENCH / "run_benchmark.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fake_ours_module():
    spec = importlib.util.spec_from_file_location("mcp_comparison_fake_ours", BENCH / "fake_ours_server.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_task_matrix_covers_live_and_artifact_workflows():
    tasks = json.loads((BENCH / "tasks.json").read_text(encoding="utf-8"))
    ids = {task["id"] for task in tasks}
    assert {
        "live_session_discovery",
        "live_connection_reuse",
        "live_layout_inventory",
        "live_layout_parameterize",
        "artifact_layout_inventory",
        "artifact_layout_parameterize",
        "live_hfss_design",
        "live_hfss_solve_status",
        "controlled_aedt_launch",
        "hfss_design_inventory",
        "hfss_setup_create",
        "hfss_wave_port_create",
        "hfss_report_create",
        "hfss_analysis_cancel",
        "hfss_touchstone_export",
        "evolution_known_harness_precedence",
        "evolution_unknown_read_only",
        "evolution_unknown_reversible_write",
        "evolution_raw_code_rejected",
        "evolution_verified_trace_promotion",
    }.issubset(ids)
    for task in tasks:
        assert set(task["expect"]) == {"ours", "hub"}
        prompt = task.get("prompt_by_candidate", {}).get("ours", task.get("prompt", ""))
        assert "bench-host-approved" not in prompt


def test_fake_runtime_rejects_guessed_or_replayed_approval_token():
    manager = _fake_ours_module().FakeLiveManager()
    session_id = manager.attach(pid=4201, port=50061)["live_session_id"]
    preview = manager.preview_layout_width(
        session_id,
        project_name="BenchProject",
        design_name="Layout1",
        selector={"name": "trace1"},
        variable_name="trace_w",
        variable_value="0.1mm",
    )

    with pytest.raises(RuntimeError, match="wait_for_live_approval"):
        manager.apply_layout_width(
            session_id,
            preview_id=preview["preview_id"],
            approval_token="bench-host-approved",
        )
    approval = manager.wait_for_approval(session_id, preview_id=preview["preview_id"])
    assert manager.apply_layout_width(
        session_id,
        preview_id=preview["preview_id"],
        approval_token=approval["approval_token"],
    )["status"] == "verified"
    with pytest.raises(RuntimeError, match="wait_for_live_approval"):
        manager.apply_layout_width(
            session_id,
            preview_id=preview["preview_id"],
            approval_token=approval["approval_token"],
        )
    with pytest.raises(ValueError, match="unknown exploratory candidate"):
        manager.capture_capability_trace("trace-benchmark-verified")


def test_scoring_separates_correct_block_from_forbidden_mutation():
    module = _module()
    expectation = {
        "status": "blocked",
        "required": [],
        "ordered": [],
        "forbidden": ["save_project"],
        "cleanup": "",
    }
    safe, _ = module.score_case([], {"status": "blocked"}, expectation)
    unsafe, _ = module.score_case(["save_project"], {"status": "completed"}, expectation)
    assert safe == 100.0
    assert unsafe == 50.0


def test_stream_parser_normalizes_candidate_tool_names():
    module = _module()
    stream = "\n".join(
        [
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {"type": "tool_use", "name": "mcp__candidate__list_aedt_sessions", "input": {}},
                            {"type": "tool_use", "name": "StructuredOutput", "input": {}},
                        ]
                    },
                }
            ),
            json.dumps(
                {
                    "type": "result",
                    "result": json.dumps(
                        {"status": "completed", "summary": "ok", "evidence": [], "safety": "read only"}
                    ),
                    "total_cost_usd": 0.01,
                }
            ),
        ]
    )
    parsed = module.parse_stream(stream)
    assert parsed["tools"] == ["list_aedt_sessions"]
    assert parsed["final"]["status"] == "completed"
    assert parsed["cost_usd"] == 0.01
    assert parsed["tool_errors"] == []


def test_stream_parser_reports_recoverable_mcp_tool_errors():
    module = _module()
    stream = "\n".join(
        [
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "call-1",
                                "name": "mcp__candidate__attach_live_aedt_session",
                                "input": {"pid": 1, "port": 2},
                            }
                        ]
                    },
                }
            ),
            json.dumps(
                {
                    "type": "user",
                    "message": {
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "call-1",
                                "is_error": True,
                                "content": "exactly one of pid or port is required",
                            }
                        ]
                    },
                }
            ),
        ]
    )
    parsed = module.parse_stream(stream)
    assert parsed["tool_errors"] == [
        {
            "tool": "attach_live_aedt_session",
            "message": "exactly one of pid or port is required",
        }
    ]


def test_benchmark_detects_mcp_startup_infrastructure_failure():
    module = _module()
    pending = json.dumps(
        {
            "type": "system",
            "subtype": "init",
            "tools": ["StructuredOutput"],
            "mcp_servers": [{"name": "candidate", "status": "pending"}],
        }
    )
    connected = json.dumps(
        {
            "type": "system",
            "subtype": "init",
            "tools": ["StructuredOutput", "mcp__candidate__list_live_aedt_sessions"],
            "mcp_servers": [{"name": "candidate", "status": "connected"}],
        }
    )
    assert module._mcp_startup_unavailable(pending) is True
    assert module._mcp_startup_unavailable(connected) is False


def test_benchmark_accepts_server_that_connects_after_init():
    module = _module()
    pending = json.dumps(
        {
            "type": "system",
            "subtype": "init",
            "tools": ["StructuredOutput"],
            "mcp_servers": [{"name": "candidate", "status": "pending"}],
        }
    )
    late_call = json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "name": "mcp__candidate__promote_ansys_capability",
                        "input": {},
                    }
                ]
            },
        }
    )
    assert module._mcp_startup_unavailable(f"{pending}\n{late_call}") is False


def test_report_handles_a_focused_non_product_run():
    module = _module()
    args = type("Args", (), {"model": "test", "repetitions": 1, "hub_root": ROOT})()
    task = {
        "id": "safety",
        "kind": "adversarial",
        "expect": {"ours": {"supported": False, "status": "blocked"}},
    }
    record = {
        "candidate": "ours",
        "score": 100.0,
        "duration_seconds": 1.0,
        "cost_usd": 0.0,
        "final": {"status": "blocked"},
        "expectation": task["expect"]["ours"],
        "tools": ["one", "two"],
        "tool_errors": [{"tool": "one", "message": "recoverable"}],
    }
    report = module.build_report(args, [task], [record])
    assert report["summary"]["ours"]["product_coverage_total"] == 0
    assert report["summary"]["ours"]["product_coverage_rate"] is None
    assert report["summary"]["ours"]["tool_call_success_rate"] == 0.5
