from __future__ import annotations

import argparse
import os
from pathlib import Path

from aedt_agent.benchmark.generator import create_generator_from_env
from aedt_agent.benchmark.runner import run_offline_benchmark
from aedt_agent.knowledge.build_sqlite import build_api_semantics_db


def main() -> None:
    parser = argparse.ArgumentParser(prog="aedt-agent")
    subparsers = parser.add_subparsers(dest="command", required=True)

    build_db = subparsers.add_parser("build-db")
    build_db.add_argument("--schema", type=Path, required=True)
    build_db.add_argument("--seed", type=Path, required=True)
    build_db.add_argument("--db", type=Path, required=True)

    run_benchmark = subparsers.add_parser("run-benchmark")
    run_benchmark.add_argument("--tasks", type=Path, required=True)
    run_benchmark.add_argument("--generated", type=Path, required=True)
    run_benchmark.add_argument("--nodes", type=Path, required=True)
    run_benchmark.add_argument("--report", type=Path, required=True)
    run_benchmark.add_argument("--db", type=Path)
    run_benchmark.add_argument("--generate", action="store_true")
    run_benchmark.add_argument("--groups", nargs="+", choices=["A", "B", "C"])

    args = parser.parse_args()
    if args.command == "build-db":
        build_api_semantics_db(args.schema, args.seed, args.db)
    elif args.command == "run-benchmark":
        generator = create_generator_from_env() if args.generate else None
        model_name = ""
        if args.generate:
            model_name = os.getenv("OPENAI_MODEL", "") or os.getenv("ANTHROPIC_MODEL", "")
        run_offline_benchmark(
            args.tasks,
            args.generated,
            args.nodes,
            args.report,
            generator=generator,
            db_path=args.db,
            groups=args.groups,
            model_name=model_name,
        )


if __name__ == "__main__":
    main()
