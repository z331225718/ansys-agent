import json
from pathlib import Path

from aedt_agent.benchmark.config import BenchmarkConfig, load_benchmark_config
from aedt_agent.benchmark.generator import OpenAIGenerator


def test_load_benchmark_config_merges_local_override(tmp_path):
    base = tmp_path / "benchmark_config.json"
    local = tmp_path / "benchmark_config.local.json"
    base.write_text(
        json.dumps(
            {
                "generator": {
                    "backend": "openai",
                    "openai": {
                        "base_url": "",
                        "api_key": "",
                        "model": "",
                        "timeout": 60,
                        "temperature": 0.0,
                    },
                },
                "paths": {
                    "tasks": "benchmarks/tasks",
                    "generated": "benchmarks/generated",
                    "nodes": "nodes/catalog",
                    "db": "knowledge/api_semantics/api_semantics.sqlite",
                    "report": "benchmarks/reports/report.json",
                },
                "groups": ["A", "B", "C"],
            }
        ),
        encoding="utf-8",
    )
    local.write_text(
        json.dumps(
            {
                "generator": {
                    "openai": {
                        "base_url": "https://example.test/v1",
                        "api_key": "secret",
                        "model": "deepseek-v4-flash",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    config = load_benchmark_config(base)

    assert config.generator.backend == "openai"
    assert config.generator.openai.base_url == "https://example.test/v1"
    assert config.generator.openai.api_key == "secret"
    assert config.generator.openai.model == "deepseek-v4-flash"


def test_config_can_build_openai_generator(tmp_path):
    base = tmp_path / "benchmark_config.json"
    base.write_text(
        json.dumps(
            {
                "generator": {
                    "backend": "openai",
                    "openai": {
                        "base_url": "https://example.test/v1",
                        "api_key": "secret",
                        "model": "deepseek-v4-flash",
                        "timeout": 45,
                        "temperature": 0.1,
                    },
                },
                "paths": {
                    "tasks": "benchmarks/tasks",
                    "generated": "benchmarks/generated",
                    "nodes": "nodes/catalog",
                    "db": "knowledge/api_semantics/api_semantics.sqlite",
                    "report": "benchmarks/reports/report.json",
                },
                "groups": ["A", "B", "C"],
            }
        ),
        encoding="utf-8",
    )

    config = load_benchmark_config(base)
    generator = config.build_generator()

    assert isinstance(generator, OpenAIGenerator)
