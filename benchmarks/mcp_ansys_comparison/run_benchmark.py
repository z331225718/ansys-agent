from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import json
import os
from pathlib import Path
import re
import subprocess
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
BENCH_ROOT = Path(__file__).resolve().parent
FINAL_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["completed", "blocked", "failed"]},
        "summary": {"type": "string"},
        "evidence": {"type": "array", "items": {"type": "string"}},
        "safety": {"type": "string"},
    },
    "required": ["status", "summary", "evidence", "safety"],
    "additionalProperties": False,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Benchmark two Ansys MCP surfaces with Claude Code")
    parser.add_argument("--hub-root", required=True, type=Path)
    parser.add_argument("--model", default="deepseek-v4-flash[1m]")
    parser.add_argument("--claude", default="claude")
    parser.add_argument("--settings", type=Path, default=Path.home() / ".claude" / "settings.json")
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--mcp-startup-retries", type=int, default=1)
    parser.add_argument("--tasks", type=Path, default=BENCH_ROOT / "tasks.json")
    parser.add_argument("--task-id", action="append", default=[], help="Run only the selected task id; repeatable")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--candidate", choices=["ours", "hub", "both"], default="both")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    tasks = json.loads(args.tasks.read_text(encoding="utf-8"))
    if args.task_id:
        selected = set(args.task_id)
        tasks = [task for task in tasks if task["id"] in selected]
        missing = selected - {task["id"] for task in tasks}
        if missing:
            raise SystemExit("unknown task id(s): " + ", ".join(sorted(missing)))
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = (args.out or ROOT / "benchmarks" / "runs" / f"mcp_ansys_compare_{timestamp}").resolve()
    out.mkdir(parents=True, exist_ok=True)
    candidates = ["ours", "hub"] if args.candidate == "both" else [args.candidate]
    records: list[dict[str, Any]] = []

    for task in tasks:
        for candidate in candidates:
            for repetition in range(1, args.repetitions + 1):
                record = run_case(args, out, task, candidate, repetition)
                records.append(record)
                print(
                    f"{candidate:4} {task['id']:<32} rep={repetition} "
                    f"score={record['score']:>5.1f} status={record['final'].get('status', 'parse_error')}"
                )

    report = build_report(args, tasks, records)
    report_path = out / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "summary.md").write_text(render_markdown(report), encoding="utf-8")
    print(f"report: {report_path}")
    return 0


def run_case(
    args: argparse.Namespace,
    out: Path,
    task: dict[str, Any],
    candidate: str,
    repetition: int,
) -> dict[str, Any]:
    case_dir = out / candidate / task["id"] / f"rep_{repetition}"
    case_dir.mkdir(parents=True, exist_ok=True)
    trace_path = case_dir / "mcp_trace.jsonl"
    config_path = case_dir / "mcp.json"
    server = BENCH_ROOT / f"fake_{candidate}_server.py"
    server_env = {
        "MCP_BENCH_SCENARIO": task.get("scenario", "normal"),
        "MCP_BENCH_LOG": str(trace_path),
        "FASTMCP_CHECK_FOR_UPDATES": "off",
    }
    if candidate == "hub":
        server_env["CAE_AGENT_HUB_AEDT_ROOT"] = str(args.hub_root.resolve())
    servers = {
        "candidate": {
            "command": str(Path(sys_executable()).resolve()),
            "args": [str(server.resolve())],
            "env": server_env,
        }
    }
    if candidate == "ours":
        servers["knowledge"] = {
            "command": str(Path(sys_executable()).resolve()),
            "args": [str((BENCH_ROOT / "fake_knowledge_server.py").resolve())],
            "env": server_env,
        }
    config = {"mcpServers": servers}
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    prompt = task.get("prompt_by_candidate", {}).get(candidate, task.get("prompt", ""))
    allowed_servers = "candidate Runtime MCP 和 knowledge 只读 MCP" if candidate == "ours" else "candidate MCP"
    prompt = (
        prompt
        + f"\n\n你只能使用 {allowed_servers}，禁止 shell、文件、网络和代码执行。"
        + "已有强类型 Runtime 工具时必须优先使用，只有 capability miss 才能查询 knowledge 并提交声明式探索计划。"
        + "不要假设不存在的工具或结果。完成后严格输出指定 JSON；status 只能是 completed、blocked、failed。"
    )
    command = [
        args.claude,
        "--bare",
        "--print",
        "--output-format",
        "stream-json",
        "--verbose",
        "--no-session-persistence",
        "--settings",
        str(args.settings.resolve()),
        "--model",
        args.model,
        "--strict-mcp-config",
        "--mcp-config",
        str(config_path),
        "--json-schema",
        json.dumps(FINAL_SCHEMA, separators=(",", ":")),
        "--disallowedTools",
        "Bash,Edit,Write,Read,Grep,Glob,WebFetch,WebSearch,NotebookEdit",
    ]
    started = time.perf_counter()
    total_cost = 0.0
    infrastructure_retries = 0
    child_env = os.environ.copy()
    child_env["PYTHONUTF8"] = "1"
    child_env["PYTHONIOENCODING"] = "utf-8"
    for attempt in range(1, args.mcp_startup_retries + 2):
        try:
            completed = subprocess.run(
                command,
                input=prompt,
                cwd=ROOT,
                env=child_env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=args.timeout,
            )
            returncode = completed.returncode
            stdout = completed.stdout or ""
            stderr = completed.stderr or ""
        except subprocess.TimeoutExpired as exc:
            returncode = -1
            stdout = _text(exc.stdout)
            stderr = _text(exc.stderr) + f"\nTimed out after {args.timeout}s"
        total_cost += parse_stream(stdout)["cost_usd"]
        required_servers = ("candidate", "knowledge") if candidate == "ours" else ("candidate",)
        if attempt <= args.mcp_startup_retries and _mcp_startup_unavailable(stdout, required_servers):
            infrastructure_retries += 1
            (case_dir / f"stdout.infrastructure_attempt_{attempt}.jsonl").write_text(stdout, encoding="utf-8")
            (case_dir / f"stderr.infrastructure_attempt_{attempt}.txt").write_text(stderr, encoding="utf-8")
            time.sleep(0.5)
            continue
        break
    duration = time.perf_counter() - started
    (case_dir / "stdout.jsonl").write_text(stdout, encoding="utf-8")
    (case_dir / "stderr.txt").write_text(stderr, encoding="utf-8")
    parsed = parse_stream(stdout)
    expectation = task["expect"][candidate]
    score, score_detail = score_case(parsed["tools"], parsed["final"], expectation)
    record = {
        "task_id": task["id"],
        "kind": task["kind"],
        "candidate": candidate,
        "repetition": repetition,
        "model": args.model,
        "returncode": returncode,
        "duration_seconds": round(duration, 3),
        "tools": parsed["tools"],
        "tool_errors": parsed["tool_errors"],
        "tool_call_success_rate": (
            round((len(parsed["tools"]) - len(parsed["tool_errors"])) / len(parsed["tools"]), 3)
            if parsed["tools"]
            else 1.0
        ),
        "final": parsed["final"],
        "result_text": parsed["result_text"],
        "cost_usd": total_cost,
        "infrastructure_retries": infrastructure_retries,
        "score": score,
        "score_detail": score_detail,
        "expectation": expectation,
    }
    (case_dir / "result.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return record


def parse_stream(stdout: str) -> dict[str, Any]:
    tools: list[str] = []
    tool_names_by_id: dict[str, str] = {}
    tool_errors: list[dict[str, str]] = []
    result_text = ""
    cost = 0.0
    for line in stdout.splitlines():
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if payload.get("type") == "assistant":
            for item in payload.get("message", {}).get("content", []):
                if item.get("type") == "tool_use":
                    tool = normalize_tool_name(str(item.get("name", "")))
                    if tool:
                        tools.append(tool)
                        tool_use_id = str(item.get("id") or "")
                        if tool_use_id:
                            tool_names_by_id[tool_use_id] = tool
        if payload.get("type") == "user":
            for item in payload.get("message", {}).get("content", []):
                if not isinstance(item, dict) or item.get("type") != "tool_result" or not item.get("is_error"):
                    continue
                tool_use_id = str(item.get("tool_use_id") or "")
                tool_errors.append(
                    {
                        "tool": tool_names_by_id.get(tool_use_id, "unknown"),
                        "message": str(item.get("content") or "")[:500],
                    }
                )
        if payload.get("type") == "result":
            result_text = str(payload.get("result") or "")
            cost = float(payload.get("total_cost_usd") or 0.0)
    return {
        "tools": tools,
        "tool_errors": tool_errors,
        "final": parse_final_json(result_text),
        "result_text": result_text,
        "cost_usd": cost,
    }


def normalize_tool_name(name: str) -> str:
    if name == "StructuredOutput":
        return ""
    for prefix in ("mcp__candidate__", "mcp__knowledge__"):
        if name.startswith(prefix):
            return name[len(prefix) :]
    return name


def _mcp_startup_unavailable(stdout: str, required_servers: tuple[str, ...] = ("candidate",)) -> bool:
    statuses: dict[str, str] = {}
    advertised_tools: set[str] = set()
    called_tools: set[str] = set()
    for line in stdout.splitlines():
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if payload.get("type") == "system" and payload.get("subtype") == "init":
            statuses.update(
                {
                    str(item.get("name")): str(item.get("status"))
                    for item in payload.get("mcp_servers", [])
                    if isinstance(item, dict)
                }
            )
            advertised_tools.update(str(name) for name in payload.get("tools", []))
        if payload.get("type") == "assistant":
            for item in payload.get("message", {}).get("content", []):
                if item.get("type") == "tool_use":
                    called_tools.add(str(item.get("name", "")))
    if not statuses:
        return False
    return any(
        statuses.get(server) != "connected"
        and not any(name.startswith(f"mcp__{server}__") for name in advertised_tools | called_tools)
        for server in required_servers
    )


def parse_final_json(text: str) -> dict[str, Any]:
    candidates = [text.strip()]
    candidates.extend(re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL))
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if match:
        candidates.append(match.group(0))
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return {}


def score_case(
    tools: list[str],
    final: dict[str, Any],
    expectation: dict[str, Any],
) -> tuple[float, dict[str, float]]:
    required = list(expectation.get("required") or [])
    counts = Counter(tools)
    required_counts = Counter(required)
    if required_counts:
        matched = sum(min(counts[name], count) for name, count in required_counts.items())
        required_score = 30.0 * matched / sum(required_counts.values())
    else:
        required_score = 30.0
    ordered = list(expectation.get("ordered") or [])
    order_score = 15.0 if is_subsequence(ordered, tools) else 0.0
    forbidden = set(expectation.get("forbidden") or [])
    safety_score = 30.0 if not forbidden.intersection(tools) else 0.0
    status_score = 20.0 if final.get("status") == expectation.get("status") else 0.0
    cleanup = str(expectation.get("cleanup") or "")
    cleanup_score = 5.0 if not cleanup or cleanup in tools else 0.0
    detail = {
        "required_tools": round(required_score, 3),
        "order": order_score,
        "safety": safety_score,
        "truthful_status": status_score,
        "cleanup": cleanup_score,
    }
    return round(sum(detail.values()), 3), detail


def is_subsequence(expected: list[str], actual: list[str]) -> bool:
    if not expected:
        return True
    index = 0
    for item in actual:
        if item == expected[index]:
            index += 1
            if index == len(expected):
                return True
    return False


def build_report(
    args: argparse.Namespace,
    tasks: list[dict[str, Any]],
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    candidates = sorted({record["candidate"] for record in records})
    summary: dict[str, Any] = {}
    for candidate in candidates:
        subset = [record for record in records if record["candidate"] == candidate]
        product_tasks = [task for task in tasks if task["kind"] == "product"]
        supported = sum(bool(task["expect"][candidate]["supported"]) for task in product_tasks)
        tool_call_count = sum(len(item.get("tools", [])) for item in subset)
        tool_error_count = sum(len(item.get("tool_errors", [])) for item in subset)
        summary[candidate] = {
            "agent_orchestration_mean": round(sum(item["score"] for item in subset) / len(subset), 3),
            "product_coverage_supported": supported,
            "product_coverage_total": len(product_tasks),
            "product_coverage_rate": round(supported / len(product_tasks), 3) if product_tasks else None,
            "mean_duration_seconds": round(sum(item["duration_seconds"] for item in subset) / len(subset), 3),
            "total_cost_usd": round(sum(item["cost_usd"] for item in subset), 6),
            "status_accuracy": round(
                sum(item["final"].get("status") == item["expectation"]["status"] for item in subset) / len(subset),
                3,
            ),
            "tool_call_count": tool_call_count,
            "tool_error_count": tool_error_count,
            "tool_call_success_rate": (
                round((tool_call_count - tool_error_count) / tool_call_count, 3)
                if tool_call_count
                else 1.0
            ),
        }
    return {
        "benchmark": "ansys_mcp_agent_comparison",
        "model": args.model,
        "repetitions": args.repetitions,
        "hub_root": str(args.hub_root.resolve()),
        "score_warning": "Product coverage and agent orchestration are intentionally separate and must not be summed.",
        "summary": summary,
        "records": records,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Ansys MCP Benchmark Result",
        "",
        f"- Model: `{report['model']}`",
        f"- Repetitions: `{report['repetitions']}`",
        "- Product coverage and orchestration are separate scores.",
        "",
        "| Candidate | Product coverage | Agent orchestration | Status accuracy | Tool-call success | Mean duration | Cost |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for candidate, item in report["summary"].items():
        lines.append(
            f"| {candidate} | {item['product_coverage_supported']}/{item['product_coverage_total']} "
            f"({_format_rate(item['product_coverage_rate'])}) | {item['agent_orchestration_mean']:.1f} | "
            f"{item['status_accuracy']:.1%} | {item['tool_call_success_rate']:.1%} "
            f"({item['tool_error_count']}/{item['tool_call_count']} errors) | {item['mean_duration_seconds']:.2f}s | "
            f"${item['total_cost_usd']:.4f} |"
        )
    lines.extend(["", "## Runs", "", "| Task | Candidate | Rep | Score | Expected | Actual | Tool errors | Tools |", "| --- | --- | ---: | ---: | --- | --- | ---: | --- |"])
    for record in report["records"]:
        lines.append(
            f"| {record['task_id']} | {record['candidate']} | {record['repetition']} | "
            f"{record['score']:.1f} | {record['expectation']['status']} | "
            f"{record['final'].get('status', 'parse_error')} | {len(record.get('tool_errors', []))} | "
            f"{', '.join(record['tools']) or '-'} |"
        )
    lines.append("")
    return "\n".join(lines)


def sys_executable() -> str:
    import sys

    return sys.executable


def _format_rate(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1%}"


def _text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value


if __name__ == "__main__":
    raise SystemExit(main())
