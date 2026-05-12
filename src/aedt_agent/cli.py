from __future__ import annotations

import argparse
import os
from pathlib import Path

from aedt_agent.benchmark.config import load_benchmark_config
from aedt_agent.benchmark.generator import create_generator_from_env
from aedt_agent.benchmark.runner import run_offline_benchmark
from aedt_agent.knowledge.build_sqlite import build_api_semantics_db


def _require_run_benchmark_args(args: argparse.Namespace) -> None:
    missing = [
        name
        for name in ("tasks", "generated", "nodes", "report")
        if getattr(args, name) is None
    ]
    if missing:
        required = ", ".join(f"--{name}" for name in missing)
        raise SystemExit(f"run-benchmark requires {required} when --config is not provided")


def main() -> None:
    parser = argparse.ArgumentParser(prog="aedt-agent")
    subparsers = parser.add_subparsers(dest="command", required=True)

    build_db = subparsers.add_parser("build-db")
    build_db.add_argument("--schema", type=Path, required=True)
    build_db.add_argument("--seed", type=Path, required=True)
    build_db.add_argument("--db", type=Path, required=True)

    run_benchmark = subparsers.add_parser("run-benchmark")
    run_benchmark.add_argument("--tasks", type=Path)
    run_benchmark.add_argument("--generated", type=Path)
    run_benchmark.add_argument("--nodes", type=Path)
    run_benchmark.add_argument("--report", type=Path)
    run_benchmark.add_argument("--db", type=Path)
    run_benchmark.add_argument("--config", type=Path)
    run_benchmark.add_argument("--generate", action="store_true")
    run_benchmark.add_argument("--fresh", action="store_true")
    run_benchmark.add_argument("--groups", nargs="+", choices=["A", "B", "C"])

    args = parser.parse_args()
    if args.command == "build-db":
        build_api_semantics_db(args.schema, args.seed, args.db)
    elif args.command == "run-benchmark":
        if args.config:
            config = load_benchmark_config(args.config)
            tasks = Path(config.paths.tasks)
            generated = Path(config.paths.generated)
            nodes = Path(config.paths.nodes)
            report = Path(config.paths.report)
            db_path = Path(config.paths.db)
            selected_groups = args.groups or config.groups
            generator = config.build_generator() if args.generate else None
            model_name = config.generator.openai.model if args.generate else ""
        else:
            _require_run_benchmark_args(args)
            tasks = args.tasks
            generated = args.generated
            nodes = args.nodes
            report = args.report
            db_path = args.db
            selected_groups = args.groups
            generator = create_generator_from_env() if args.generate else None
            model_name = ""
            if args.generate:
                model_name = os.getenv("OPENAI_MODEL", "") or os.getenv("ANTHROPIC_MODEL", "")
        run_offline_benchmark(
            tasks,
            generated,
            nodes,
            report,
            generator=generator,
            db_path=db_path,
            groups=selected_groups,
            model_name=model_name,
            reuse_existing_candidates=not args.fresh,
        )


if __name__ == "__main__":
    main()
