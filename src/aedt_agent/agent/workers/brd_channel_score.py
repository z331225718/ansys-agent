from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from aedt_agent.agent.evaluation import build_sparameter_evidence
from aedt_agent.agent.mission import JobRecord
from aedt_agent.agent.workers.registry import WorkerContext
from aedt_agent.layout.channel_scoring import parse_touchstone, score_channel_result
from aedt_agent.reporting.channel_plots import write_channel_plot_artifacts


BRD_CHANNEL_SCORE_CAPABILITY = "brd.channel.score"


def build_brd_channel_score_job_input(
    *,
    touchstone_path: str | Path,
    tdr_path: str | Path,
    artifact_dir: str | Path,
    frequency_start_ghz: float = 0.0,
    frequency_stop_ghz: float = 67.0,
    rl_target_db: float = -20.0,
    tdr_target_ohm: float = 100.0,
    tdr_tolerance_ohm: float = 5.0,
    sparameter_mode: str = "auto",
    tdr_observation_port: str = "",
    bucket_count: int = 128,
    tdr_plot_time_stop_ps: float = 120.0,
) -> dict[str, Any]:
    return {
        "touchstone_path": str(touchstone_path),
        "tdr_path": str(tdr_path),
        "artifact_dir": str(artifact_dir),
        "frequency_start_ghz": frequency_start_ghz,
        "frequency_stop_ghz": frequency_stop_ghz,
        "rl_target_db": rl_target_db,
        "tdr_target_ohm": tdr_target_ohm,
        "tdr_tolerance_ohm": tdr_tolerance_ohm,
        "sparameter_mode": sparameter_mode,
        "tdr_observation_port": tdr_observation_port,
        "bucket_count": bucket_count,
        "tdr_plot_time_stop_ps": tdr_plot_time_stop_ps,
    }


def run_brd_channel_score_worker(job: JobRecord, context: WorkerContext) -> dict[str, Any]:
    payload = dict(job.input_payload)
    touchstone_path = Path(str(payload["touchstone_path"]))
    tdr_path = Path(str(payload["tdr_path"]))
    if not touchstone_path.exists():
        raise ValueError(f"touchstone_path does not exist: {touchstone_path}")
    if not tdr_path.exists():
        raise ValueError(f"tdr_path does not exist: {tdr_path}")

    artifact_dir = Path(str(payload["artifact_dir"]))
    artifact_dir.mkdir(parents=True, exist_ok=True)
    frequency_start_ghz = float(payload.get("frequency_start_ghz", 0.0))
    frequency_stop_ghz = float(payload.get("frequency_stop_ghz", 67.0))
    rl_target_db = float(payload.get("rl_target_db", -20.0))
    tdr_target_ohm = float(payload.get("tdr_target_ohm", 100.0))
    tdr_tolerance_ohm = float(payload.get("tdr_tolerance_ohm", 5.0))
    sparameter_mode = str(payload.get("sparameter_mode") or "auto")
    tdr_observation_port = str(payload.get("tdr_observation_port") or "")
    bucket_count = int(payload.get("bucket_count", 128))
    tdr_plot_time_stop_ps = float(payload.get("tdr_plot_time_stop_ps", 120.0))

    score = score_channel_result(
        touchstone_path,
        tdr_path,
        frequency_start_ghz=frequency_start_ghz,
        frequency_stop_ghz=frequency_stop_ghz,
        rl_target_db=rl_target_db,
        tdr_target_ohm=tdr_target_ohm,
        tdr_tolerance_ohm=tdr_tolerance_ohm,
        sparameter_mode=sparameter_mode,
        tdr_observation_port=tdr_observation_port,
    )
    samples = [
        _evidence_sample(
            sample,
            return_loss_trace=str(score["return_loss_trace"]),
            insertion_loss_trace=str(score["insertion_loss_trace"]),
        )
        for sample in parse_touchstone(touchstone_path)
        if frequency_start_ghz <= sample["frequency_ghz"] <= frequency_stop_ghz
    ]
    sparameter_evidence = build_sparameter_evidence(
        trace_id=f"{job.job_id}:{score['return_loss_trace']}",
        samples=samples,
        artifact_ref=str(touchstone_path),
        rl_target_db=rl_target_db,
        bucket_count=bucket_count,
    )
    plot_artifacts = write_channel_plot_artifacts(
        touchstone_path=touchstone_path,
        tdr_path=tdr_path,
        artifact_dir=artifact_dir,
        sparameter_mode=str(score["sparameter_mode"]),
        frequency_start_ghz=frequency_start_ghz,
        frequency_stop_ghz=frequency_stop_ghz,
        rl_target_db=rl_target_db,
        tdr_target_ohm=tdr_target_ohm,
        tdr_tolerance_ohm=tdr_tolerance_ohm,
        tdr_plot_time_stop_ps=tdr_plot_time_stop_ps,
    )
    score["plot_artifacts"] = plot_artifacts
    evidence_summary = _bounded_summary(score, sparameter_evidence)
    evidence_artifact = artifact_dir / "brd_channel_score_evidence.json"
    evidence_payload = {
        "job_id": job.job_id,
        "mission_id": job.mission_id,
        "worker_id": context.worker_id,
        "score": score,
        "sparameter_evidence": sparameter_evidence,
        "evidence_summary": evidence_summary,
    }
    evidence_artifact.write_text(json.dumps(evidence_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    loop_context = _loop_context(payload)
    _append_unique(loop_context, "score_evidence_paths", str(evidence_artifact))
    loop_context["last_score_evidence_path"] = str(evidence_artifact)
    loop_context["last_score_status"] = str(score.get("status") or "")
    objective = score.get("optimization_objective") or {}
    loop_context["last_objective_total_cost"] = (
        objective.get("total_cost") if isinstance(objective, dict) else None
    )
    loop_context["latest_project_path"] = str(
        payload.get("project_path")
        or payload.get("solved_project")
        or loop_context.get("latest_project_path")
        or ""
    )

    return {
        "status": "passed" if score["status"] == "pass" else "failed",
        "project_path": loop_context.get("latest_project_path", ""),
        "solve_manifest": payload.get("solve_manifest", ""),
        "touchstone_path": str(touchstone_path),
        "tdr_path": str(tdr_path),
        "frequency_start_ghz": frequency_start_ghz,
        "frequency_stop_ghz": frequency_stop_ghz,
        "rl_target_db": rl_target_db,
        "tdr_target_ohm": tdr_target_ohm,
        "tdr_tolerance_ohm": tdr_tolerance_ohm,
        "sparameter_mode": sparameter_mode,
        "tdr_observation_port": tdr_observation_port,
        "score": score,
        "sparameter_evidence": sparameter_evidence,
        "evidence_summary": evidence_summary,
        "evidence_artifact": str(evidence_artifact),
        "loop_context": loop_context,
        "artifact_refs": [
            str(touchstone_path),
            str(tdr_path),
            str(evidence_artifact),
            *plot_artifacts.values(),
        ],
    }


def _bounded_summary(score: dict[str, Any], sparameter_evidence: dict[str, Any]) -> dict[str, Any]:
    spectral_summary = dict(sparameter_evidence["summary"])
    spectral_summary.pop("buckets", None)
    summary = {
        "status": score["status"],
        "frequency_start_ghz": score["frequency_start_ghz"],
        "frequency_stop_ghz": score["frequency_stop_ghz"],
        "touchstone_kind": score.get("touchstone_kind"),
        "sparameter_mode": score["sparameter_mode"],
        "return_loss_trace": score["return_loss_trace"],
        "insertion_loss_trace": score["insertion_loss_trace"],
        "rl_target_db": score["rl_target_db"],
        "rl_worst_db": score["rl_worst_db"],
        "rl_worst_frequency_ghz": score["rl_worst_frequency_ghz"],
        "rl_pass_band": score["rl_pass_band"],
        "insertion_worst_db_in_band": score["insertion_worst_db_in_band"],
        "insertion_worst_frequency_ghz": score["insertion_worst_frequency_ghz"],
        "tdr_target_ohm": score["tdr_target_ohm"],
        "tdr_tolerance_ohm": score["tdr_tolerance_ohm"],
        "tdr_observation_port": score.get("tdr_observation_port", ""),
        "tdr_peak_deviation_ohm": score["tdr_peak_deviation_ohm"],
        "tdr_peak_time_ps": score["tdr_peak_time_ps"],
        "tdr_anomaly_window": score["tdr_anomaly_window"],
        "tdr_mean_impedance_ohm": score.get("tdr_mean_impedance_ohm"),
        "tdr_min_impedance_ohm": score.get("tdr_min_impedance_ohm"),
        "tdr_max_impedance_ohm": score.get("tdr_max_impedance_ohm"),
        "tdr_peak_to_peak_ohm": score.get("tdr_peak_to_peak_ohm"),
        "tdr_proximity_mse_ohm2": score.get("tdr_proximity_mse_ohm2"),
        "tdr_proximity_rmse_ohm": score.get("tdr_proximity_rmse_ohm"),
        "tdr_flatness_msd_ohm2": score.get("tdr_flatness_msd_ohm2"),
        "tdr_flatness_rms_step_ohm": score.get("tdr_flatness_rms_step_ohm"),
        "rl_violation_sum_db": score.get("rl_violation_sum_db"),
        "rl_violation_max_db": score.get("rl_violation_max_db"),
        "rl_violation_point_count": score.get("rl_violation_point_count"),
        "optimization_objective": score.get("optimization_objective"),
        "samples": score["samples"],
        "spectral_summary": spectral_summary,
        "plot_artifacts": score.get("plot_artifacts", {}),
        "raw_sparameters": "artifact_only",
        "raw_tdr": "artifact_only",
        "artifact_refs": [
            *list(sparameter_evidence["artifact_refs"]),
            *list((score.get("plot_artifacts") or {}).values()),
        ],
    }
    if score.get("sparameter_mode") == "differential":
        summary.update(
            {
                "sdd11_worst_db": score.get("sdd11_worst_db"),
                "sdd11_worst_frequency_ghz": score.get(
                    "sdd11_worst_frequency_ghz"
                ),
                "sdd21_worst_db_in_band": score.get(
                    "sdd21_worst_db_in_band"
                ),
                "pass_fail_reason": "; ".join(score.get("diagnosis", [])),
            }
        )
    else:
        summary.update(
            {
                "s11_min_db": score.get("s11_worst_db"),
                "s21_worst_db_in_band": score.get("s21_worst_db_in_band"),
                "pass_fail_reason": "; ".join(score.get("diagnosis", [])),
            }
        )
    return summary


def _evidence_sample(
    sample: dict[str, float],
    *,
    return_loss_trace: str,
    insertion_loss_trace: str,
) -> dict[str, float]:
    rl_key = "sdd11_db" if return_loss_trace == "SDD11" else "s11_db"
    il_key = "sdd21_db" if insertion_loss_trace == "SDD21" else "s21_db"
    return {
        "frequency_ghz": sample["frequency_ghz"],
        "s11_db": sample[rl_key],
        "s21_db": sample[il_key],
    }


def _loop_context(payload: dict[str, Any]) -> dict[str, Any]:
    value = payload.get("loop_context")
    return dict(value) if isinstance(value, dict) else {}


def _append_unique(payload: dict[str, Any], key: str, value: str) -> None:
    values = list(payload.get(key) or [])
    if value and value not in values:
        values.append(value)
    payload[key] = values
