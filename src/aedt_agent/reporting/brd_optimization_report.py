from __future__ import annotations

import csv
import html
import json
from pathlib import Path
from typing import Any, Mapping, Sequence


OPTIMIZATION_HISTORY_COLUMNS = [
    "round_index",
    "round_status",
    "continue_recommendation",
    "action_type",
    "layers",
    "parameter_names",
    "target_radius",
    "target_diameter",
    "parasitic_target",
    "center_source",
    "solve_status",
    "touchstone_path",
    "tdr_path",
    "tdr_exported",
    "touchstone_sample_count",
    "tdr_sample_count",
    "score_status",
    "touchstone_kind",
    "return_loss_trace",
    "insertion_loss_trace",
    "rl_worst_db",
    "rl_worst_frequency_ghz",
    "insertion_worst_db_in_band",
    "tdr_observation_port",
    "tdr_peak_deviation_ohm",
    "tdr_peak_time_ps",
    "tdr_proximity_mse_ohm2",
    "tdr_flatness_msd_ohm2",
    "rl_violation_sum_db",
    "objective_total_cost",
    "pass_fail_reason",
    "edit_manifest_path",
    "solve_result_path",
    "solve_manifest_path",
    "score_evidence_path",
    "artifact_refs",
]


def build_brd_optimization_summary(
    *,
    score_evidence_paths: Sequence[str | Path] = (),
    model_edit_manifest_paths: Sequence[str | Path] = (),
    solve_result_paths: Sequence[str | Path] = (),
    solve_manifest_paths: Sequence[str | Path] = (),
) -> dict[str, Any]:
    scores = [_load_json(path) for path in score_evidence_paths]
    edits = [_load_json(path) for path in model_edit_manifest_paths]
    solves = _load_solve_records(
        solve_result_paths=solve_result_paths,
        solve_manifest_paths=solve_manifest_paths,
    )
    rounds = []
    round_count = max(len(scores), len(solves), len(edits))
    for zero_index in range(round_count):
        score_payload = scores[zero_index] if zero_index < len(scores) else {}
        solve_payload = solves[zero_index] if zero_index < len(solves) else {}
        edit_payload = edits[zero_index] if zero_index < len(edits) else {}
        rounds.append(
            _round_summary(
                zero_index + 1,
                score_payload=score_payload,
                score_path=score_evidence_paths[zero_index]
                if zero_index < len(score_evidence_paths)
                else "",
                solve_payload=solve_payload,
                solve_result_path=solve_result_paths[zero_index]
                if zero_index < len(solve_result_paths)
                else "",
                solve_manifest_path=solve_manifest_paths[zero_index]
                if zero_index < len(solve_manifest_paths)
                else "",
                edit_payload=edit_payload,
                edit_path=model_edit_manifest_paths[zero_index]
                if zero_index < len(model_edit_manifest_paths)
                else "",
            )
        )
    changes = []
    for index, payload in enumerate(edits, start=1):
        summary = dict(payload.get("summary") or {})
        for change in summary.get("changes") or []:
            if isinstance(change, Mapping):
                changes.append(
                    {
                        "edit_index": index,
                        "action_type": change.get("action_type"),
                        "layer": change.get("layer"),
                        "requested_layer": change.get("requested_layer"),
                        "property": change.get("property"),
                        "implementation": change.get("implementation"),
                        "parasitic_target": change.get("parasitic_target"),
                        "center_source": change.get("center_source"),
                        "parameters": change.get("parameters") or {},
                        "manifest_path": str(
                            Path(model_edit_manifest_paths[index - 1])
                        ),
                    }
                )
    final = rounds[-1] if rounds else {}
    history_rows = [
        _history_row(round_item, zero_index)
        for zero_index, round_item in enumerate(rounds)
    ]
    return {
        "status": final.get("status", "unknown"),
        "round_count": len(rounds),
        "change_count": len(changes),
        "final_score": final,
        "rounds": rounds,
        "history_rows": history_rows,
        "history_columns": OPTIMIZATION_HISTORY_COLUMNS,
        "changes": changes,
        "score_evidence_paths": [str(Path(path)) for path in score_evidence_paths],
        "model_edit_manifest_paths": [
            str(Path(path)) for path in model_edit_manifest_paths
        ],
        "solve_result_paths": [str(Path(path)) for path in solve_result_paths],
        "solve_manifest_paths": [
            str(Path(path)) for path in solve_manifest_paths
        ],
    }


def write_brd_optimization_history_csv(
    summary: Mapping[str, Any],
    output_path: str | Path,
) -> Path:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    rows = summary.get("history_rows") or []
    with output.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=OPTIMIZATION_HISTORY_COLUMNS)
        writer.writeheader()
        for row in rows if isinstance(rows, list) else []:
            writer.writerow(
                {
                    column: _csv_value(
                        row.get(column) if isinstance(row, Mapping) else ""
                    )
                    for column in OPTIMIZATION_HISTORY_COLUMNS
                }
            )
    return output


def render_brd_optimization_report_html(summary: Mapping[str, Any]) -> str:
    final_score = summary.get("final_score") or {}
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>BRD 差分过孔优化闭环报告</title>
  <style>
    body{{font-family:Arial,"Microsoft YaHei",sans-serif;margin:0;background:#f6f7f9;color:#1f2933}}
    main{{max-width:1180px;margin:0 auto;padding:28px}}
    h1{{font-size:28px;margin:0 0 8px}} h2{{font-size:18px;margin:24px 0 10px}}
    .sub{{color:#5f6b7a;margin-bottom:18px}}
    .grid{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}}
    .card{{background:white;border:1px solid #d8dee8;border-radius:8px;padding:14px;min-width:0}}
    .card b{{display:block;font-size:12px;color:#5f6b7a;margin-bottom:5px}}
    table{{width:100%;border-collapse:collapse;background:white;border:1px solid #d8dee8}}
    th,td{{border-bottom:1px solid #e5e9f0;padding:9px 10px;font-size:13px;text-align:left;vertical-align:top}}
    th{{background:#eef2f6}} code{{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:12px}}
    .plots{{display:grid;grid-template-columns:1fr;gap:14px}}
    .plot{{background:white;border:1px solid #d8dee8;border-radius:8px;padding:10px}}
    .plot img{{width:100%;height:auto;display:block}}
  </style>
</head>
<body><main>
  <h1>BRD 差分过孔优化闭环报告</h1>
  <div class="sub">报告只引用 bounded evidence 与 artifact refs；原始 S 参数/TDR 曲线保持 artifact-only。</div>
  <div class="grid">
    <div class="card"><b>最终状态</b>{_e(summary.get("status"))}</div>
    <div class="card"><b>轮数</b>{_e(summary.get("round_count"))}</div>
    <div class="card"><b>修改数</b>{_e(summary.get("change_count"))}</div>
    <div class="card"><b>TDR 观察端口</b>{_e(final_score.get("tdr_observation_port"))}</div>
  </div>
  <h2>最终指标</h2>
  <table>
    <tr><th>Return Loss</th><th>Worst RL</th><th>Frequency</th><th>Insertion</th><th>TDR Peak</th></tr>
    <tr>
      <td>{_e(final_score.get("return_loss_trace"))}</td>
      <td>{_e(final_score.get("rl_worst_db"))} dB</td>
      <td>{_e(final_score.get("rl_worst_frequency_ghz"))} GHz</td>
      <td>{_e(final_score.get("insertion_worst_db_in_band"))} dB</td>
      <td>{_e(final_score.get("tdr_peak_deviation_ohm"))} ohm @ {_e(final_score.get("tdr_peak_time_ps"))} ps</td>
    </tr>
  </table>
  <h2>修改记录</h2>
  {_changes_table(summary.get("changes"))}
  <h2>优化历史</h2>
  {_history_table(summary.get("history_rows"))}
  <h2>每轮评分</h2>
  {_rounds_table(summary.get("rounds"))}
  <h2>最终曲线</h2>
  {_plot_section(final_score.get("plot_artifacts"))}
</main></body></html>
"""


def _changes_table(changes: Any) -> str:
    rows = []
    for change in changes if isinstance(changes, list) else []:
        rows.append(
            "<tr>"
            f"<td>{_e(change.get('edit_index'))}</td>"
            f"<td>{_e(change.get('action_type'))}</td>"
            f"<td>{_e(change.get('requested_layer') or change.get('layer'))}</td>"
            f"<td>{_e(change.get('property'))}</td>"
            f"<td>{_e(change.get('implementation'))}</td>"
            f"<td>{_e(change.get('parasitic_target'))}</td>"
            f"<td>{_e(_parameter_names(change.get('parameters')))}</td>"
            f"<td><code>{_e(change.get('manifest_path'))}</code></td>"
            "</tr>"
        )
    if not rows:
        rows.append("<tr><td colspan=\"8\">无模型修改记录</td></tr>")
    return (
        "<table><tr><th>#</th><th>Action</th><th>Layer</th><th>Property</th>"
        "<th>Implementation</th><th>Target</th><th>Parameters</th>"
        "<th>Manifest</th></tr>"
        f"{''.join(rows)}</table>"
    )


def _history_table(rows_value: Any) -> str:
    rows = []
    for item in rows_value if isinstance(rows_value, list) else []:
        rows.append(
            "<tr>"
            f"<td>{_e(item.get('round_index'))}</td>"
            f"<td>{_e(item.get('round_status'))}</td>"
            f"<td>{_e(item.get('action_type'))}</td>"
            f"<td>{_e(item.get('layers'))}</td>"
            f"<td>{_e(item.get('solve_status'))}</td>"
            f"<td>{_e(item.get('score_status'))}</td>"
            f"<td>{_e(item.get('rl_worst_db'))}</td>"
            f"<td>{_e(item.get('tdr_peak_deviation_ohm'))}</td>"
            f"<td>{_e(item.get('objective_total_cost'))}</td>"
            f"<td>{_e(item.get('continue_recommendation'))}</td>"
            "</tr>"
        )
    if not rows:
        rows.append("<tr><td colspan=\"10\">暂无优化历史</td></tr>")
    return (
        "<table><tr><th>Round</th><th>Status</th><th>Action</th>"
        "<th>Layers</th><th>Solve</th><th>Score</th><th>Worst RL</th>"
        "<th>TDR Peak</th><th>Total Cost</th><th>Next</th></tr>"
        f"{''.join(rows)}</table>"
    )


def _rounds_table(rounds: Any) -> str:
    rows = []
    for item in rounds if isinstance(rounds, list) else []:
        rows.append(
            "<tr>"
            f"<td>{_e(item.get('round_index'))}</td>"
            f"<td>{_e(item.get('status'))}</td>"
            f"<td>{_e(item.get('return_loss_trace'))}</td>"
            f"<td>{_e(item.get('rl_worst_db'))}</td>"
            f"<td>{_e(item.get('insertion_worst_db_in_band'))}</td>"
            f"<td>{_e(item.get('tdr_peak_deviation_ohm'))}</td>"
            f"<td>{_e(item.get('objective_total_cost'))}</td>"
            f"<td><code>{_e(item.get('evidence_path'))}</code></td>"
            "</tr>"
        )
    return (
        "<table><tr><th>Round</th><th>Status</th><th>RL Trace</th>"
        "<th>Worst RL dB</th><th>Worst Insertion dB</th>"
        "<th>TDR Peak ohm</th><th>Total Cost</th><th>Evidence</th></tr>"
        f"{''.join(rows)}</table>"
    )


def _plot_section(plots: Any) -> str:
    if not isinstance(plots, Mapping) or not plots:
        return "<p>未找到曲线 artifact。</p>"
    cards = []
    for name in ("tdr", "sdd11", "sdd21", "s11", "s21"):
        path = plots.get(name)
        if path:
            cards.append(
                f'<div class="plot"><b>{_e(name.upper())}</b>'
                f'<img src="{_e(path)}" alt="{_e(name.upper())}"></div>'
            )
    return f"<div class=\"plots\">{''.join(cards)}</div>" if cards else "<p>未找到曲线 artifact。</p>"


def _load_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _load_solve_records(
    *,
    solve_result_paths: Sequence[str | Path],
    solve_manifest_paths: Sequence[str | Path],
) -> list[dict[str, Any]]:
    records = [_load_json(path) for path in solve_result_paths]
    for index, path in enumerate(solve_manifest_paths):
        payload = _load_json(path)
        if index < len(records):
            records[index]["solve_manifest_payload"] = payload
        else:
            records.append({"solve_manifest_payload": payload})
    return records


def _round_summary(
    round_index: int,
    *,
    score_payload: Mapping[str, Any],
    score_path: str | Path,
    solve_payload: Mapping[str, Any],
    solve_result_path: str | Path,
    solve_manifest_path: str | Path,
    edit_payload: Mapping[str, Any],
    edit_path: str | Path,
) -> dict[str, Any]:
    score = dict(score_payload.get("score") or {})
    evidence_summary = dict(score_payload.get("evidence_summary") or {})
    solve_summary = _solve_summary(solve_payload)
    edit_summary = dict(edit_payload.get("summary") or {})
    edit_digest = _edit_digest(edit_summary)
    objective = score.get("optimization_objective") or evidence_summary.get(
        "optimization_objective"
    ) or {}
    solve_status = solve_payload.get("status") or solve_summary.get("status")
    score_status = score.get("status") or evidence_summary.get("status")
    round_status = _round_status(
        solve_status=str(solve_status or ""),
        score_status=str(score_status or ""),
        solve_summary=solve_summary,
    )
    artifact_refs = _artifact_refs(
        score_payload,
        evidence_summary,
        solve_payload,
    )
    return {
        "round_index": round_index,
        "status": round_status,
        "solve_status": solve_status,
        "score_status": score_status,
        "touchstone_kind": score.get("touchstone_kind")
        or evidence_summary.get("touchstone_kind"),
        "return_loss_trace": score.get("return_loss_trace")
        or evidence_summary.get("return_loss_trace"),
        "insertion_loss_trace": score.get("insertion_loss_trace")
        or evidence_summary.get("insertion_loss_trace"),
        "rl_worst_db": score.get("rl_worst_db")
        or evidence_summary.get("rl_worst_db"),
        "rl_worst_frequency_ghz": score.get("rl_worst_frequency_ghz")
        or evidence_summary.get("rl_worst_frequency_ghz"),
        "insertion_worst_db_in_band": score.get("insertion_worst_db_in_band")
        or evidence_summary.get("insertion_worst_db_in_band"),
        "tdr_observation_port": score.get("tdr_observation_port")
        or evidence_summary.get("tdr_observation_port")
        or solve_summary.get("tdr_observation_port"),
        "tdr_peak_deviation_ohm": score.get("tdr_peak_deviation_ohm")
        or evidence_summary.get("tdr_peak_deviation_ohm"),
        "tdr_peak_time_ps": score.get("tdr_peak_time_ps")
        or evidence_summary.get("tdr_peak_time_ps"),
        "tdr_proximity_mse_ohm2": score.get("tdr_proximity_mse_ohm2")
        or evidence_summary.get("tdr_proximity_mse_ohm2"),
        "tdr_flatness_msd_ohm2": score.get("tdr_flatness_msd_ohm2")
        or evidence_summary.get("tdr_flatness_msd_ohm2"),
        "rl_violation_sum_db": score.get("rl_violation_sum_db")
        or evidence_summary.get("rl_violation_sum_db"),
        "objective_total_cost": objective.get("total_cost")
        if isinstance(objective, Mapping)
        else "",
        "pass_fail_reason": evidence_summary.get("pass_fail_reason")
        or "; ".join(score.get("diagnosis", [])),
        "plot_artifacts": score.get("plot_artifacts")
        or evidence_summary.get("plot_artifacts")
        or {},
        "artifact_refs": artifact_refs,
        "evidence_path": str(Path(score_path)) if score_path else "",
        "solve_result_path": str(Path(solve_result_path))
        if solve_result_path
        else "",
        "solve_manifest_path": str(Path(solve_manifest_path))
        if solve_manifest_path
        else _manifest_path_from_solve(solve_payload),
        "edit_manifest_path": str(Path(edit_path)) if edit_path else "",
        "edit_digest": edit_digest,
        "solve_summary": solve_summary,
        "continue_recommendation": _continue_recommendation(
            round_status,
            score_status=str(score_status or ""),
        ),
    }


def _solve_summary(solve_payload: Mapping[str, Any]) -> dict[str, Any]:
    summary = dict(solve_payload.get("summary") or {})
    summary.update(dict(solve_payload.get("solve_summary") or {}))
    manifest = solve_payload.get("solve_manifest_payload")
    if isinstance(manifest, Mapping):
        summary.update(dict((manifest.get("summary") or {})))
        outputs = manifest.get("outputs") or {}
        if isinstance(outputs, Mapping):
            if "touchstone_path" not in solve_payload and isinstance(
                outputs.get("touchstone"), Mapping
            ):
                summary["touchstone_path"] = outputs["touchstone"].get("path")
            if "tdr_path" not in solve_payload and isinstance(
                outputs.get("tdr"), Mapping
            ):
                summary["tdr_path"] = outputs["tdr"].get("path")
    for key in ("touchstone_path", "tdr_path", "solved_project"):
        if solve_payload.get(key):
            summary[key] = solve_payload.get(key)
    return summary


def _edit_digest(edit_summary: Mapping[str, Any]) -> dict[str, str]:
    changes = [
        change for change in edit_summary.get("changes") or []
        if isinstance(change, Mapping)
    ]
    return {
        "action_type": _join_unique(change.get("action_type") for change in changes),
        "layers": _join_unique(
            change.get("requested_layer") or change.get("layer")
            for change in changes
        ),
        "parameter_names": _join_unique(
            _parameter_names(change.get("parameters")) for change in changes
        ),
        "target_radius": _join_unique(_target_radius(change) for change in changes),
        "target_diameter": _join_unique(
            _target_diameter(change) for change in changes
        ),
        "parasitic_target": _join_unique(
            change.get("parasitic_target") for change in changes
        ),
        "center_source": _join_unique(change.get("center_source") for change in changes),
    }


def _history_row(round_item: Mapping[str, Any], zero_index: int) -> dict[str, Any]:
    edit = round_item.get("edit_digest") or {}
    solve = round_item.get("solve_summary") or {}
    if not isinstance(edit, Mapping):
        edit = {}
    if not isinstance(solve, Mapping):
        solve = {}
    return {
        "round_index": round_item.get("round_index") or zero_index + 1,
        "round_status": round_item.get("status"),
        "continue_recommendation": round_item.get("continue_recommendation"),
        "action_type": edit.get("action_type"),
        "layers": edit.get("layers"),
        "parameter_names": edit.get("parameter_names"),
        "target_radius": edit.get("target_radius"),
        "target_diameter": edit.get("target_diameter"),
        "parasitic_target": edit.get("parasitic_target"),
        "center_source": edit.get("center_source"),
        "solve_status": round_item.get("solve_status"),
        "touchstone_path": solve.get("touchstone_path"),
        "tdr_path": solve.get("tdr_path"),
        "tdr_exported": solve.get("tdr_exported"),
        "touchstone_sample_count": solve.get("touchstone_sample_count"),
        "tdr_sample_count": solve.get("tdr_sample_count"),
        "score_status": round_item.get("score_status"),
        "touchstone_kind": round_item.get("touchstone_kind"),
        "return_loss_trace": round_item.get("return_loss_trace"),
        "insertion_loss_trace": round_item.get("insertion_loss_trace"),
        "rl_worst_db": round_item.get("rl_worst_db"),
        "rl_worst_frequency_ghz": round_item.get("rl_worst_frequency_ghz"),
        "insertion_worst_db_in_band": round_item.get("insertion_worst_db_in_band"),
        "tdr_observation_port": round_item.get("tdr_observation_port"),
        "tdr_peak_deviation_ohm": round_item.get("tdr_peak_deviation_ohm"),
        "tdr_peak_time_ps": round_item.get("tdr_peak_time_ps"),
        "tdr_proximity_mse_ohm2": round_item.get("tdr_proximity_mse_ohm2"),
        "tdr_flatness_msd_ohm2": round_item.get("tdr_flatness_msd_ohm2"),
        "rl_violation_sum_db": round_item.get("rl_violation_sum_db"),
        "objective_total_cost": round_item.get("objective_total_cost"),
        "pass_fail_reason": round_item.get("pass_fail_reason"),
        "edit_manifest_path": round_item.get("edit_manifest_path"),
        "solve_result_path": round_item.get("solve_result_path"),
        "solve_manifest_path": round_item.get("solve_manifest_path"),
        "score_evidence_path": round_item.get("evidence_path"),
        "artifact_refs": round_item.get("artifact_refs"),
    }


def _round_status(
    *,
    solve_status: str,
    score_status: str,
    solve_summary: Mapping[str, Any],
) -> str:
    if score_status:
        return score_status
    if solve_status == "succeeded":
        if not solve_summary.get("tdr_path") or solve_summary.get("tdr_exported") is False:
            return "needs_tdr_export_before_score"
        return "needs_channel_score"
    if solve_status:
        return solve_status
    return "pending"


def _continue_recommendation(status: str, *, score_status: str) -> str:
    if status == "needs_tdr_export_before_score":
        return "export_tdr_from_solved_model_then_score"
    if status == "needs_channel_score":
        return "run_brd_channel_score"
    if score_status == "pass":
        return "candidate_complete_review_budget_for_more_optimization"
    if score_status == "fail":
        return "continue_with_tdr_driven_small_reversible_edit"
    if status in {"failed", "error"}:
        return "fix_failed_stage_before_next_round"
    return "continue_when_next_artifact_available"


def _artifact_refs(
    score_payload: Mapping[str, Any],
    evidence_summary: Mapping[str, Any],
    solve_payload: Mapping[str, Any],
) -> list[Any]:
    refs = (
        score_payload.get("artifact_refs")
        or evidence_summary.get("artifact_refs")
        or []
    )
    if not refs:
        refs = solve_payload.get("artifact_refs") or []
    if refs:
        return list(refs)
    return [
        solve_payload.get("touchstone_path", ""),
        solve_payload.get("tdr_path", ""),
        solve_payload.get("solve_manifest_path", ""),
    ]


def _manifest_path_from_solve(solve_payload: Mapping[str, Any]) -> str:
    return str(
        solve_payload.get("solve_manifest_path")
        or solve_payload.get("solve_manifest")
        or ""
    )


def _parameter_names(parameters: Any) -> str:
    if not isinstance(parameters, Mapping):
        return ""
    if parameters.get("name"):
        return str(parameters.get("name"))
    return _join_unique(
        value.get("name") if isinstance(value, Mapping) else ""
        for value in parameters.values()
    )


def _target_radius(change: Mapping[str, Any]) -> str:
    created = change.get("created_voids") or change.get("created_shapes") or []
    values = []
    for item in created if isinstance(created, list) else []:
        if isinstance(item, Mapping):
            values.append(item.get("radius_expression") or item.get("radius_m"))
    return _join_unique(values)


def _target_diameter(change: Mapping[str, Any]) -> str:
    created = change.get("created_voids") or change.get("created_shapes") or []
    values = []
    for item in created if isinstance(created, list) else []:
        if isinstance(item, Mapping):
            values.append(item.get("diameter_m"))
    return _join_unique(values)


def _join_unique(values: Any) -> str:
    seen = []
    for value in values:
        if value is None or value == "":
            continue
        text = _csv_value(value)
        if text not in seen:
            seen.append(text)
    return "; ".join(seen)


def _csv_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _e(value: Any) -> str:
    return html.escape(str(value or ""))
