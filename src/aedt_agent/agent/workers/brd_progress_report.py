from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from aedt_agent.agent.mission import JobRecord
from aedt_agent.agent.workers.registry import WorkerContext
from aedt_agent.reporting.brd_optimization_report import (
    build_brd_optimization_summary,
    render_brd_optimization_report_html,
    write_brd_optimization_history_csv,
)


BRD_OPTIMIZATION_PROGRESS_CAPABILITY = "brd.optimization.progress"
BRD_OPTIMIZATION_REPORT_CAPABILITY = "brd.optimization.report"


def run_brd_optimization_progress_worker(
    job: JobRecord,
    context: WorkerContext,
) -> dict[str, Any]:
    payload = dict(job.input_payload)
    loop_context = _loop_context(payload)
    report = _write_progress_artifacts(loop_context, context=context)
    evidence = dict(payload.get("evidence_summary") or {})
    evidence.update(
        {
            "optimization_history_csv": report["optimization_history_csv"],
            "optimization_report_json": report["report_json"],
            "optimization_report_html": report["report_html"],
        }
    )
    return {
        **payload,
        **report,
        "status": payload.get("status", "succeeded"),
        "evidence_summary": evidence,
        "loop_context": loop_context,
        "artifact_refs": _unique(
            [
                *list(payload.get("artifact_refs") or []),
                report["optimization_history_csv"],
                report["report_json"],
                report["report_html"],
                *list((report.get("best_project") or {}).get("artifact_refs") or []),
            ]
        ),
    }


def run_brd_optimization_report_worker(
    job: JobRecord,
    context: WorkerContext,
) -> dict[str, Any]:
    payload = dict(job.input_payload)
    loop_context = _loop_context(payload)
    report = _write_progress_artifacts(loop_context, context=context)
    final_score = dict(report.get("final_score") or {})
    checks = [
        {
            "id": "raw_trace_policy",
            "status": "passed",
            "message": "raw s-parameters and TDR remain artifact-only",
        },
        {
            "id": "optimization_history_csv",
            "status": "passed" if report.get("optimization_history_csv") else "failed",
            "message": str(report.get("optimization_history_csv") or ""),
        },
        {
            "id": "optimization_report_html",
            "status": "passed" if report.get("report_html") else "failed",
            "message": str(report.get("report_html") or ""),
        },
        {
            "id": "required_plots",
            "status": "passed" if _has_required_plots(final_score) else "failed",
            "message": "final report must include TDR plus SDD11/SDD21 plot artifacts",
        },
    ]
    status = "passed" if all(check["status"] == "passed" for check in checks) else "failed"
    return {
        **payload,
        **report,
        "status": status,
        "checks": checks,
        "loop_context": loop_context,
        "artifact_refs": _unique(
            [
                report["optimization_history_csv"],
                report["report_json"],
                report["report_html"],
                *list((report.get("best_project") or {}).get("artifact_refs") or []),
            ]
        ),
        "evidence_summary": {
            "status": status,
            "raw_sparameters": "artifact_only",
            "raw_tdr": "artifact_only",
            "optimization_history_csv": report["optimization_history_csv"],
            "report_json": report["report_json"],
            "report_html": report["report_html"],
            "final_score": final_score,
            "artifact_refs": [
                report["optimization_history_csv"],
                report["report_json"],
                report["report_html"],
                *list((report.get("best_project") or {}).get("artifact_refs") or []),
            ],
        },
    }


def _write_progress_artifacts(
    loop_context: dict[str, Any],
    *,
    context: WorkerContext,
) -> dict[str, Any]:
    report_dir = _report_dir(loop_context, context)
    summary = build_brd_optimization_summary(
        score_evidence_paths=list(loop_context.get("score_evidence_paths") or []),
        model_edit_manifest_paths=list(
            loop_context.get("model_edit_manifest_paths") or []
        ),
        solve_manifest_paths=list(loop_context.get("solve_manifest_paths") or []),
    )
    summary["best_project"] = _best_project_summary(loop_context)
    history_csv = write_brd_optimization_history_csv(
        summary,
        report_dir / "optimization_history.csv",
    )
    report_html = report_dir / "optimization_progress.html"
    report_json = report_dir / "optimization_progress.json"
    summary["optimization_history_csv"] = str(history_csv)
    report_html.write_text(
        render_brd_optimization_report_html(summary),
        encoding="utf-8",
    )
    report_json.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    loop_context["optimization_history_csv"] = str(history_csv)
    loop_context["report_html"] = str(report_html)
    loop_context["report_json"] = str(report_json)
    return {
        "optimization_history_csv": str(history_csv),
        "optimization_history_rows": list(summary.get("history_rows") or []),
        "report_html": str(report_html),
        "report_json": str(report_json),
        "final_score": summary.get("final_score") or {},
        "best_project": summary.get("best_project") or {},
    }


def _report_dir(
    loop_context: Mapping[str, Any],
    context: WorkerContext,
) -> Path:
    value = str(loop_context.get("report_dir") or "").strip()
    if value:
        report_dir = Path(value)
    elif context.artifacts_dir:
        report_dir = Path(context.artifacts_dir) / "optimization_progress"
    else:
        report_dir = Path("optimization_progress")
    report_dir.mkdir(parents=True, exist_ok=True)
    return report_dir


def _loop_context(payload: Mapping[str, Any]) -> dict[str, Any]:
    value = payload.get("loop_context")
    return dict(value) if isinstance(value, dict) else {}


def _best_project_summary(loop_context: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "status": loop_context.get("best_project_preservation_status"),
        "round_index": loop_context.get("best_round_index"),
        "objective_total_cost": loop_context.get("best_objective_total_cost"),
        "project_path": loop_context.get("best_project_path"),
        "manifest_path": loop_context.get("best_project_manifest_path"),
        "score_evidence_path": loop_context.get("best_score_evidence_path"),
        "artifact_refs": list(loop_context.get("best_project_artifact_refs") or []),
    }


def _unique(values: list[Any]) -> list[str]:
    result = []
    for value in values:
        text = str(value)
        if text and text not in result:
            result.append(text)
    return result


def _has_required_plots(final_score: Mapping[str, Any]) -> bool:
    plots = final_score.get("plot_artifacts")
    if not isinstance(plots, Mapping):
        return False
    required = ["tdr"]
    if str(final_score.get("sparameter_mode") or "").casefold() == "single_ended":
        required.extend(["s11", "s21"])
    else:
        required.extend(["sdd11", "sdd21"])
    return all(str(plots.get(name) or "").strip() for name in required)
