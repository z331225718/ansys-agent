import json
from pathlib import Path

from aedt_agent.benchmark.config import BenchmarkConfig, load_benchmark_config
from aedt_agent.benchmark.generator import OpenAIGenerator
from aedt_agent.benchmark.harness_generator import HarnessGenerator
from aedt_agent.benchmark.official_retriever import GitNexusOfficialRetriever


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
                        "model": "model-under-test",
                        "max_retries": 3,
                        "retry_delay": 1.5,
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
    assert config.generator.openai.model == "model-under-test"
    assert config.generator.openai.max_retries == 3
    assert config.generator.openai.retry_delay == 1.5


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
                        "model": "model-under-test",
                        "timeout": 45,
                        "temperature": 0.1,
                        "max_retries": 2,
                        "retry_delay": 2.0,
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


def test_config_can_build_gitnexus_retriever(tmp_path):
    base = tmp_path / "benchmark_config.json"
    base.write_text(
        json.dumps(
            {
                "generator": {"backend": "file", "file": {"base_dir": "benchmarks/reference_scripts"}},
                "official_retrieval": {
                    "backend": "gitnexus_http",
                    "gitnexus_url": "http://127.0.0.1:4848",
                    "pyaedt_repo": "../pyaedt",
                    "pyaedt_examples": "../pyaedt-examples",
                    "top_k": 6,
                    "timeout": 20,
                },
                "paths": {
                    "tasks": "benchmarks/tasks",
                    "generated": "benchmarks/generated",
                    "nodes": "nodes/catalog",
                    "db": "knowledge/api_semantics/api_semantics.sqlite",
                    "report": "benchmarks/reports/report.json",
                },
            }
        ),
        encoding="utf-8",
    )

    config = load_benchmark_config(base)
    retriever = config.build_retriever()

    assert isinstance(retriever, GitNexusOfficialRetriever)
    assert retriever.backend == "gitnexus_http"
    assert retriever.top_k == 6


def test_config_exposes_aedt_environment(tmp_path):
    base = tmp_path / "benchmark_config.json"
    base.write_text(
        json.dumps(
            {
                "generator": {"backend": "file", "file": {"base_dir": "benchmarks/reference_scripts"}},
                "aedt": {
                    "version": "2026.1",
                    "non_graphical": True,
                    "ansysem_root": "~/ansys_inc/v261/AnsysEM",
                    "awp_root": "~/ansys_inc/v261",
                    "timeout": 123,
                },
                "paths": {
                    "tasks": "benchmarks/tasks",
                    "generated": "benchmarks/generated",
                    "nodes": "nodes/catalog",
                    "db": "knowledge/api_semantics/api_semantics.sqlite",
                    "report": "benchmarks/reports/report.json",
                },
            }
        ),
        encoding="utf-8",
    )

    config = load_benchmark_config(base)

    assert config.aedt.version == "2026.1"
    assert config.aedt.non_graphical is True
    assert config.aedt.ansysem_root.endswith("AnsysEM")
    assert config.aedt.timeout == 123


def test_config_can_build_harness_generator(tmp_path):
    group_a = tmp_path / "group_a.json"
    group_b = tmp_path / "group_b.json"
    group_a.write_text(json.dumps({"command": "fake", "args": ["--a"]}), encoding="utf-8")
    group_b.write_text(json.dumps({"command": "fake", "args": ["--b"]}), encoding="utf-8")
    base = tmp_path / "benchmark_config.json"
    base.write_text(
        json.dumps(
            {
                "generator": {"backend": "harness"},
                "harness": {
                    "backend": "claude",
                    "command": "fake",
                    "timeout": 33,
                    "group_a_config": str(group_a),
                    "group_b_config": str(group_b),
                    "work_dir": str(tmp_path / "work"),
                },
                "paths": {
                    "tasks": "benchmarks/tasks",
                    "generated": "benchmarks/generated",
                    "nodes": "nodes/catalog",
                    "db": "knowledge/api_semantics/api_semantics.sqlite",
                    "report": "benchmarks/reports/report.json",
                },
            }
        ),
        encoding="utf-8",
    )

    config = load_benchmark_config(base)
    generator = config.build_generator(repo_root=tmp_path)

    assert isinstance(generator, HarnessGenerator)
    assert generator.timeout == 33


def test_harness_timeout_defaults_to_formal_benchmark_value(tmp_path):
    base = tmp_path / "benchmark_config.json"
    base.write_text(
        json.dumps(
            {
                "generator": {"backend": "file", "file": {"base_dir": "benchmarks/reference_scripts"}},
                "paths": {
                    "tasks": "benchmarks/tasks",
                    "generated": "benchmarks/generated",
                    "nodes": "nodes/catalog",
                    "db": "knowledge/api_semantics/api_semantics.sqlite",
                    "report": "benchmarks/reports/report.json",
                },
            }
        ),
        encoding="utf-8",
    )

    config = load_benchmark_config(base)

    assert config.harness.timeout == 900
