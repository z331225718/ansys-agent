from __future__ import annotations

import hashlib
import json
from pathlib import Path

from aedt_agent.agent.mission import JobRecord
from aedt_agent.agent.workers.brd_progress_report import (
    BRD_OPTIMIZATION_REPORT_CAPABILITY,
    run_brd_optimization_report_worker,
)
from aedt_agent.agent.workers.registry import WorkerContext


def _job(payload: dict) -> JobRecord:
    return JobRecord.create(
        job_id="job-t3",
        mission_id="mission-t3",
        capability=BRD_OPTIMIZATION_REPORT_CAPABILITY,
        idempotency_key="t3-report",
        input_payload=payload,
        timeout_seconds=30,
        retry_limit=1,
    )


def _write_json(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def test_t3_complete_partial_failed_history_and_artifact_manifest(tmp_path):
    run_root = tmp_path / "run"
    source = run_root / "source" / "reviewed.aedt"
    source.parent.mkdir(parents=True)
    source.write_text("source", encoding="utf-8")
    score_paths = []
    edit_paths = []
    artifacts = []
    for index, (status, rl, cost) in enumerate(
        [("pass", -18.5, 9.5), ("fail", -16.2, 11.0), ("failed", -15.8, 12.2)],
        start=1,
    ):
        touchstone = run_root / "rounds" / f"r{index}.s4p"
        tdr = run_root / "rounds" / f"r{index}_tdr.csv"
        touchstone.parent.mkdir(parents=True, exist_ok=True)
        touchstone.write_text(f"# round {index}\n", encoding="utf-8")
        tdr.write_text(f"Time,TDR\n0,{80 + index}\n", encoding="utf-8")
        evidence = _write_json(
            run_root / "score" / f"r{index}.json",
            {
                "score": {
                    "status": status,
                    "touchstone_kind": "s4p",
                    "return_loss_trace": "SDD11",
                    "insertion_loss_trace": "SDD21",
                    "rl_worst_db": rl,
                    "rl_worst_frequency_ghz": 18.0 + index,
                    "insertion_worst_db_in_band": -1.0 - index / 10,
                    "tdr_observation_port": "Diff1",
                    "tdr_orientation_confirmed": True,
                    "tdr_peak_deviation_ohm": 10.5 - index,
                    "tdr_peak_time_ps": 20.0,
                    "optimization_objective": {"total_cost": cost},
                    "plot_artifacts": {
                        "tdr": str(tdr),
                        "sdd11": str(touchstone),
                        "sdd21": str(touchstone),
                    },
                    "diagnosis": ["round " + status],
                },
                "artifact_refs": [str(touchstone), str(tdr)],
                "evidence_summary": {
                    "status": status,
                    "raw_sparameters": "artifact_only",
                    "raw_tdr": "artifact_only",
                },
            },
        )
        edit = _write_json(
            run_root / "edit" / f"r{index}.json",
            {
                "summary": {
                    "changes": [
                        {
                            "action_type": "anti_pad.enlarge",
                            "requested_layer": "L2_GND",
                            "parameters": {"name": "l02_void_r", "value": f"{18 + index}mil"},
                            "parasitic_target": "l1_ball",
                        }
                    ]
                }
            },
        )
        score_paths.append(evidence)
        edit_paths.append(edit)
        artifacts.extend([touchstone, tdr, evidence, edit])
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    payload = {
        "decision": "complete",
        "loop_context": {
            "round_index": 3,
            "run_root": str(run_root),
            "report_dir": str(run_root / "report"),
            "score_evidence_paths": [str(p) for p in score_paths],
            "model_edit_manifest_paths": [str(p) for p in edit_paths],
            "source_project_path": str(source),
            "source_project_sha256": source_hash,
        },
    }

    output = run_brd_optimization_report_worker(
        _job(payload),
        WorkerContext("worker-t3", artifacts_dir=str(run_root / "artifacts")),
    )

    assert output["status"] == "passed"
    history = Path(output["optimization_history_csv"]).read_text(encoding="utf-8-sig").splitlines()
    assert len(history) == 4
    assert "pass" in history[1] and "fail" in history[2] and "failed" in history[3]
    assert "l02_void_r" in history[2]
    assert "-18.5" in history[1]
    assert "-16.2" in history[2]
    report_json = json.loads(Path(output["report_json"]).read_text(encoding="utf-8"))
    assert report_json["round_count"] == 3
    assert report_json["history_rows"][1]["tdr_observation_port"] == "Diff1"
    assert report_json["history_rows"][1]["tdr_orientation_confirmed"] is True
    assert report_json["history_rows"][1]["rl_worst_db"] == -16.2
    assert report_json["history_rows"][1]["rl_worst_frequency_ghz"] == 20.0
    assert report_json["history_rows"][1]["tdr_peak_deviation_ohm"] == 8.5
    assert report_json["history_rows"][1]["objective_total_cost"] == 11.0
    assert report_json["history_rows"][2]["rl_worst_db"] == -15.8
    html = Path(output["report_html"]).read_text(encoding="utf-8")
    assert "SDD11" in html and "-16.2" in html and "Diff1" in html
    manifest_path = Path(output["artifact_manifest"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["run_root"] == str(run_root.resolve())
    assert manifest["all_within_run_root"] is True
    assert manifest["all_exist"] is True
    referenced = {item["resolved_path"] for item in manifest["artifacts"]}
    assert str(source.resolve()) not in referenced
    for artifact in artifacts:
        assert str(artifact.resolve()) in referenced
        item = next(row for row in manifest["artifacts"] if row["resolved_path"] == str(artifact.resolve()))
        assert item["sha256"] == hashlib.sha256(artifact.read_bytes()).hexdigest()