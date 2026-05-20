from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a Chinese Stage C workflow expansion presentation report.")
    parser.add_argument("--output-html", default="benchmarks/reports/stage_c_workflow_expansion_report.html")
    parser.add_argument("--output-json", default="benchmarks/reports/stage_c_workflow_expansion_report.json")
    args = parser.parse_args()

    report = build_report()
    output_html = Path(args.output_html)
    output_json = Path(args.output_json)
    output_html.parent.mkdir(parents=True, exist_ok=True)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    output_html.write_text(render_html(report), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


def build_report() -> dict[str, Any]:
    microstrip_nodes = [
        "create_substrate",
        "create_conductor_or_geometry_group",
        "assign_boundary",
        "create_airbox",
        "create_port",
        "create_setup",
        "create_sweep_or_export",
        "solve_setup",
        "create_sparameter_report",
    ]
    dipole_nodes = [
        "create_conductor_or_geometry_group",
        "create_airbox",
        "assign_boundary",
        "create_port",
        "create_setup",
        "create_sweep_or_export",
        "create_farfield_setup",
        "solve_setup",
        "create_sparameter_report",
    ]
    reused = sorted(set(microstrip_nodes) & set(dipole_nodes))
    return {
        "title": "Stage C 工作流扩展报告",
        "subtitle": "从一个微带线闭环 demo，扩展到可复用节点搭建偶极子天线 workflow",
        "summary": {
            "core_message": "Stage C 的重点不是让 LLM 直接写 PyAEDT 脚本，而是让 LLM 生成受控 workflow JSON，再由本地节点执行器驱动 AEDT。",
            "microstrip_status": "已形成可演示的端到端闭环：建模、端口、求解、S 参数读取和曲线展示。",
            "dipole_status": "新增偶极子模板已完成结构验证，并进入真实 AEDT smoke；主路径保留稳定的远场 setup，不把耗时 report export 放入默认链路。",
            "test_result": "218 passed, 2 skipped",
        },
        "architecture": [
            {"name": "聊天/模板输入", "description": "用户用自然语言或模板参数描述仿真目标。"},
            {"name": "LLM Planner", "description": "只生成 workflow JSON，不直接执行 PyAEDT 代码。"},
            {"name": "Workflow Validator", "description": "检查节点、输入、依赖和 sweep 类型等受控约束。"},
            {"name": "Controlled Node Executor", "description": "只执行白名单节点，例如几何、端口、边界、setup、sweep、solve、后处理。"},
            {"name": "AEDT/Fake Adapter", "description": "同一条 workflow 可先用 fake 验证结构，再接真实 AEDT。"},
            {"name": "Inspector + Validation", "description": "读取对象、材料、端口、边界、报告等事实，形成可展示证据。"},
        ],
        "workflow_comparison": [
            {
                "workflow": "微带线 S 参数",
                "purpose": "展示 LLM 控制 AEDT 完成几何、端口、求解和 S 参数闭环。",
                "nodes": microstrip_nodes,
                "outputs": ["S11/S21 曲线", "Touchstone 文件", "节点状态", "模型 validation"],
            },
            {
                "workflow": "偶极子天线",
                "purpose": "验证同一批通用节点能跨到天线场景，并新增远场 setup 能力。",
                "nodes": dipole_nodes,
                "outputs": ["S11 Touchstone", "远场 setup", "端口/边界/setup/sweep validation"],
            },
        ],
        "reuse": {
            "reused_nodes": reused,
            "new_nodes": ["create_farfield_setup", "create_antenna_report（保留为实验性后处理节点，默认真实 smoke 暂不挂载）"],
            "enhanced_nodes": ["create_conductor_or_geometry_group: 支持真实 cylinder，不再用 box 近似"],
            "principle": "新增 workflow 优先复用已有 primitive 节点；只有确实缺失的仿真能力才新增节点。",
        },
        "evidence": [
            {"name": "微带线 demo", "status": "可演示", "path": "http://127.0.0.1:8765"},
            {"name": "偶极子真实 AEDT smoke", "status": "succeeded", "path": "benchmarks/runs/stage_c_real_dipole_smoke/workflow_run.json"},
            {"name": "偶极子模板", "status": "完成", "path": "workflow_templates/dipole_antenna_s11_farfield.json"},
            {"name": "全量测试", "status": "218 passed, 2 skipped", "path": "pytest"},
        ],
        "next_steps": [
            "把 demo 页面增加 workflow 选择，允许 Microstrip / Dipole 两条展示路径。",
            "把 BRD/3D Layout 作为后续复杂 workflow：导入、选 net、cutout、叠层、端口、仿真、后处理。",
            "继续保持节点进化机制：从重复 workflow 中提 proposal，但不自动发布 stable 节点。",
        ],
    }


def render_html(report: dict[str, Any]) -> str:
    summary = report["summary"]
    architecture = "".join(_flow_step(item, index + 1) for index, item in enumerate(report["architecture"]))
    workflow_cards = "".join(_workflow_card(item) for item in report["workflow_comparison"])
    reuse_rows = "".join(
        [
            _kv_row("复用节点", "、".join(report["reuse"]["reused_nodes"])),
            _kv_row("新增节点", "、".join(report["reuse"]["new_nodes"])),
            _kv_row("增强节点", "；".join(report["reuse"]["enhanced_nodes"])),
            _kv_row("设计原则", report["reuse"]["principle"]),
        ]
    )
    evidence_rows = "".join(_evidence_row(item) for item in report["evidence"])
    next_steps = "".join(f"<li>{_escape(item)}</li>" for item in report["next_steps"])
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{_escape(report["title"])}</title>
  <style>
    :root{{--bg:#f5f7fb;--panel:#fff;--ink:#142033;--muted:#64748b;--line:#d7dde8;--blue:#2457d6;--teal:#0f766e;--amber:#a16207;--red:#b42318}}
    *{{box-sizing:border-box}}body{{margin:0;font-family:Arial,'Noto Sans SC',sans-serif;background:var(--bg);color:var(--ink);letter-spacing:0}}
    main{{max-width:1220px;margin:0 auto;padding:34px 24px 46px}}h1{{font-size:34px;line-height:1.12;margin:0 0 8px}}h2{{font-size:22px;margin:28px 0 12px}}p{{line-height:1.65}}.lead{{font-size:18px;color:var(--muted);max-width:940px}}
    .metrics{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin:22px 0}}.metric{{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:15px}}.metric strong{{display:block;font-size:24px;margin-bottom:5px}}
    .flow{{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:10px}}.step{{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:13px;min-height:138px}}.idx{{width:26px;height:26px;border-radius:6px;background:#e8eefc;color:var(--blue);display:grid;place-items:center;font-weight:800;margin-bottom:8px}}.step b{{display:block;margin-bottom:6px}}
    .cards{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}}.card{{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:16px}}.node-list{{display:flex;gap:6px;flex-wrap:wrap;margin-top:10px}}.chip{{font-size:12px;border:1px solid #cbd5e1;border-radius:999px;padding:5px 8px;background:#f8fafc;color:#334155}}
    table{{width:100%;border-collapse:collapse;background:var(--panel);border:1px solid var(--line);border-radius:8px;overflow:hidden}}th,td{{border-bottom:1px solid #e5e7eb;padding:11px;text-align:left;vertical-align:top}}th{{background:#eef2ff}}code{{background:#eef2ff;padding:2px 5px;border-radius:4px}}.ok{{color:var(--teal);font-weight:800}}ul{{line-height:1.75;background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:16px 22px}}
    @media(max-width:960px){{.metrics,.flow,.cards{{grid-template-columns:1fr}}main{{padding:24px 14px}}}}
  </style>
</head>
<body>
<main>
  <h1>{_escape(report["title"])}</h1>
  <p class="lead">{_escape(report["subtitle"])}</p>
  <div class="metrics">
    <div class="metric"><strong>受控 workflow</strong>{_escape(summary["core_message"])}</div>
    <div class="metric"><strong>微带线</strong>{_escape(summary["microstrip_status"])}</div>
    <div class="metric"><strong>偶极子</strong>{_escape(summary["dipole_status"])}</div>
    <div class="metric"><strong>{_escape(summary["test_result"])}</strong>当前回归测试</div>
  </div>

  <h2>Agent 架构</h2>
  <div class="flow">{architecture}</div>

  <h2>Workflow 扩展</h2>
  <div class="cards">{workflow_cards}</div>

  <h2>节点复用策略</h2>
  <table><tbody>{reuse_rows}</tbody></table>

  <h2>当前证据</h2>
  <table><thead><tr><th>项目</th><th>状态</th><th>路径</th></tr></thead><tbody>{evidence_rows}</tbody></table>

  <h2>下一步</h2>
  <ul>{next_steps}</ul>
</main>
</body>
</html>
"""


def _flow_step(item: dict[str, str], index: int) -> str:
    return (
        "<div class=\"step\">"
        f"<div class=\"idx\">{index}</div>"
        f"<b>{_escape(item['name'])}</b>"
        f"<span>{_escape(item['description'])}</span>"
        "</div>"
    )


def _workflow_card(item: dict[str, Any]) -> str:
    chips = "".join(f"<span class=\"chip\">{_escape(node)}</span>" for node in item["nodes"])
    outputs = "、".join(item["outputs"])
    return (
        "<div class=\"card\">"
        f"<h3>{_escape(item['workflow'])}</h3>"
        f"<p>{_escape(item['purpose'])}</p>"
        f"<p><b>输出：</b>{_escape(outputs)}</p>"
        f"<div class=\"node-list\">{chips}</div>"
        "</div>"
    )


def _kv_row(key: str, value: str) -> str:
    return f"<tr><th>{_escape(key)}</th><td>{_escape(value)}</td></tr>"


def _evidence_row(item: dict[str, str]) -> str:
    return (
        "<tr>"
        f"<td>{_escape(item['name'])}</td>"
        f"<td class=\"ok\">{_escape(item['status'])}</td>"
        f"<td><code>{_escape(item['path'])}</code></td>"
        "</tr>"
    )


def _escape(value: Any) -> str:
    text = str(value)
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


if __name__ == "__main__":
    main()
