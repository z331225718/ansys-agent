from __future__ import annotations

import json
import shutil
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
    reference_impedance_ohm: float | None = None,
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
        "reference_impedance_ohm": reference_impedance_ohm,
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
    reference_impedance_ohm = _score_reference_impedance(payload, tdr_target_ohm)
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
        reference_impedance_ohm=reference_impedance_ohm,
    )
    samples = [
        _evidence_sample(
            sample,
            return_loss_trace=str(score["return_loss_trace"]),
            insertion_loss_trace=str(score["insertion_loss_trace"]),
        )
        for sample in parse_touchstone(
            touchstone_path,
            reference_impedance_ohm=reference_impedance_ohm,
        )
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
        reference_impedance_ohm=reference_impedance_ohm,
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
    objective_total_cost = (
        objective.get("total_cost") if isinstance(objective, dict) else None
    )
    loop_context["last_objective_total_cost"] = objective_total_cost
    loop_context["latest_project_path"] = str(
        payload.get("project_path")
        or payload.get("solved_project")
        or loop_context.get("latest_project_path")
        or ""
    )
    best_project_refs = _preserve_best_project_if_needed(
        loop_context,
        score=score,
        evidence_artifact=evidence_artifact,
        objective_total_cost=objective_total_cost,
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
        "reference_impedance_ohm": reference_impedance_ohm,
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
            *best_project_refs,
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
        "reference_impedance_ohm": score.get("reference_impedance_ohm"),
        "single_ended_reference_impedance_ohm": score.get(
            "single_ended_reference_impedance_ohm"
        ),
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


def _score_reference_impedance(
    payload: dict[str, Any],
    tdr_target_ohm: float,
) -> float | None:
    explicit = payload.get("reference_impedance_ohm")
    if explicit is None:
        explicit = payload.get("sparameter_reference_impedance_ohm")
    if explicit is not None:
        value = float(explicit)
        return value if value > 0 else None
    mode = str(payload.get("sparameter_mode") or "auto").casefold()
    touchstone_name = str(payload.get("touchstone_path") or "")
    if mode in {"differential", "diff", "mixed_mode"} or touchstone_name.lower().endswith(".s4p"):
        return float(tdr_target_ohm)
    return None


def _loop_context(payload: dict[str, Any]) -> dict[str, Any]:
    value = payload.get("loop_context")
    return dict(value) if isinstance(value, dict) else {}


def _append_unique(payload: dict[str, Any], key: str, value: str) -> None:
    values = list(payload.get(key) or [])
    if value and value not in values:
        values.append(value)
    payload[key] = values


def _preserve_best_project_if_needed(
    loop_context: dict[str, Any],
    *,
    score: dict[str, Any],
    evidence_artifact: Path,
    objective_total_cost: Any,
) -> list[str]:
    cost = _float_or_none(objective_total_cost)
    if cost is None:
        loop_context["best_project_preservation_status"] = "skipped_no_objective"
        return []
    previous_cost = _float_or_none(loop_context.get("best_objective_total_cost"))
    if previous_cost is not None and cost >= previous_cost:
        loop_context["best_project_preservation_status"] = "unchanged"
        return []
    source_project = Path(str(loop_context.get("latest_project_path") or ""))
    if not source_project.is_file():
        loop_context["best_project_preservation_status"] = "skipped_project_missing"
        return []

    best_dir = Path(
        str(
            loop_context.get("best_project_dir")
            or Path(str(loop_context.get("report_dir") or source_project.parent))
            / "best_project"
        )
    )
    best_dir.mkdir(parents=True, exist_ok=True)
    target_project = best_dir / f"{source_project.stem}.best.aedt"
    copied_refs = _copy_project_bundle(source_project, target_project)
    manifest_path = best_dir / "best_project_manifest.json"
    manifest = {
        "status": "preserved_best_so_far",
        "round_index": int(loop_context.get("round_index") or 1),
        "objective_total_cost": cost,
        "previous_objective_total_cost": previous_cost,
        "score_status": score.get("status"),
        "source_project_path": str(source_project),
        "best_project_path": str(target_project),
        "score_evidence_path": str(evidence_artifact),
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    loop_context["best_project_preservation_status"] = "updated"
    loop_context["best_objective_total_cost"] = cost
    loop_context["best_round_index"] = manifest["round_index"]
    loop_context["best_project_path"] = str(target_project)
    loop_context["best_project_manifest_path"] = str(manifest_path)
    loop_context["best_score_evidence_path"] = str(evidence_artifact)
    for ref in [*copied_refs, str(manifest_path)]:
        _append_unique(loop_context, "best_project_artifact_refs", ref)
    return [*copied_refs, str(manifest_path)]


def _copy_project_bundle(source_project: Path, target_project: Path) -> list[str]:
    _remove_project_bundle(target_project)
    shutil.copy2(source_project, target_project)
    refs = [str(target_project)]
    source_edb = source_project.with_suffix(".aedb")
    target_edb = target_project.with_suffix(".aedb")
    if source_edb.is_dir():
        shutil.copytree(source_edb, target_edb)
        refs.append(str(target_edb))
    source_results = Path(f"{source_project}results")
    target_results = Path(f"{target_project}results")
    if source_results.is_dir():
        shutil.copytree(source_results, target_results)
        refs.append(str(target_results))
    return refs


def _remove_project_bundle(project_path: Path) -> None:
    for path in [
        project_path,
        project_path.with_suffix(".aedb"),
        Path(f"{project_path}results"),
        Path(f"{project_path}.lock"),
    ]:
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
