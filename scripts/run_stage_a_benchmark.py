from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from aedt_agent.benchmark.aedt_executor import AEDTSubprocessExecutor
from aedt_agent.benchmark.config import load_benchmark_config
from aedt_agent.benchmark.report_html_v2 import write_html_report_v2
from aedt_agent.benchmark.runner_v2 import run_aedt_benchmark_v2
from aedt_agent.knowledge.build_sqlite import build_api_semantics_db


def _print_progress(event: dict) -> None:
    if event.get("phase") == "attempt_start":
        print(
            f"[heartbeat] {event['task_id']} [{event['group']}] "
            f"attempt {event['attempt']}/{event['max_attempts']}",
            flush=True,
        )
        return
    if event.get("phase") == "attempt_end":
        status = "PASS" if event.get("final_pass") else "FAIL"
        print(
            f"[attempt] {event['task_id']} [{event['group']}] "
            f"attempt {event['attempt']}/{event['max_attempts']} {status} "
            f"execution={event.get('execution_ok')} validation={event.get('validation_ok')} "
            f"failure={event.get('failure_type') or '-'}",
            flush=True,
        )
        return
    print(f"[{event['current']}/{event['total']}] {event['task_id']} [{event['group']}]")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="run_stage_a_benchmark.py")
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="Delete existing generated candidates before running benchmark.",
    )
    parser.add_argument("--task", action="append", dest="tasks", help="Run a single task id. Can be repeated.")
    parser.add_argument("--groups", nargs="+", choices=["A", "B"], help="Groups to run, for example: --groups A B")
    parser.add_argument("--max-attempts", type=int, default=3, help="Maximum generation/repair attempts per task/group.")
    args, _unknown = parser.parse_known_args()
    return args


def build_executor(config=None) -> AEDTSubprocessExecutor:
    if config is None:
        return AEDTSubprocessExecutor()
    return AEDTSubprocessExecutor(
        version=config.aedt.version,
        non_graphical=config.aedt.non_graphical,
        ansysem_root=config.aedt.ansysem_root,
        awp_root=config.aedt.awp_root,
        timeout=config.aedt.timeout,
    )


def _model_label(config) -> str:
    if config.generator.backend.lower() == "harness":
        return f"{config.harness.backend}:{config.harness.command}"
    return config.generator.openai.model


def main() -> None:
    args = _parse_args()
    repo_root = REPO_ROOT
    config = load_benchmark_config(repo_root / "config/benchmark_config.json")
    run_dir = repo_root / config.paths.run_dir
    mode = "fresh" if args.fresh else "resume"

    print(f"Mode: {mode}")
    print(f"Run dir: {run_dir}")

    if args.fresh and run_dir.exists():
        shutil.rmtree(run_dir)
        print("Cleared existing benchmark run artifacts.")

    db_path = repo_root / config.paths.db
    build_api_semantics_db(
        repo_root / "knowledge/api_semantics/api_semantics.schema.sql",
        repo_root / "knowledge/api_semantics/api_semantics.seed.jsonl",
        db_path,
    )

    report = run_aedt_benchmark_v2(
        tasks_dir=repo_root / config.paths.tasks,
        run_dir=run_dir,
        generator=config.build_generator(repo_root),
        executor=build_executor(config),
        groups=args.groups or config.groups,
        task_ids=args.tasks,
        max_attempts=args.max_attempts,
        progress_callback=_print_progress,
    )
    report_path = repo_root / config.paths.report
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(__import__("json").dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    html_report_path = write_html_report_v2(
        report,
        repo_root / config.paths.html_report,
        model_name=_model_label(config),
    )

    print(f"Report written to: {report_path}")
    print(f"HTML report written to: {html_report_path}")
    print(f"Group metrics: {report['groups']}")


if __name__ == "__main__":
    main()
