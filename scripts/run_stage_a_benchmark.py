from __future__ import annotations

from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from aedt_agent.benchmark.config import load_benchmark_config
from aedt_agent.benchmark.runner import run_offline_benchmark
from aedt_agent.knowledge.build_sqlite import build_api_semantics_db


def main() -> None:
    repo_root = REPO_ROOT
    config = load_benchmark_config(repo_root / "config/benchmark_config.json")

    db_path = repo_root / config.paths.db
    build_api_semantics_db(
        repo_root / "knowledge/api_semantics/api_semantics.schema.sql",
        repo_root / "knowledge/api_semantics/api_semantics.seed.jsonl",
        db_path,
    )

    report = run_offline_benchmark(
        tasks_dir=repo_root / config.paths.tasks,
        generated_dir=repo_root / config.paths.generated,
        node_catalog_dir=repo_root / config.paths.nodes,
        report_path=repo_root / config.paths.report,
        generator=config.build_generator(),
        db_path=db_path,
        groups=config.groups,
        model_name=config.generator.openai.model,
    )

    print(f"Report written to: {repo_root / config.paths.report}")
    print(f"Go/No-Go: {report['go_nogo']['go']}")
    print(f"Metrics: {report['go_nogo']['metrics']}")


if __name__ == "__main__":
    main()
