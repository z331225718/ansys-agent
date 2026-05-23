from pathlib import Path

from aedt_agent.demo.config import DemoConfig, load_demo_config


def test_load_demo_config_uses_blank_example_values(tmp_path):
    example = tmp_path / "demo_config.example.json"
    example.write_text(
        '{"planner":{"mode":"deterministic","provider":"","model":"","base_url":"","api_key":""},'
        '"server":{"host":"127.0.0.1","port":8765},'
        '"execution":{"default_adapter":"fake","run_dir":"benchmarks/runs/stage_c1_demo_latest"}}\n',
        encoding="utf-8",
    )

    config = load_demo_config(example_path=example, local_path=tmp_path / "missing.local.json")

    assert isinstance(config, DemoConfig)
    assert config.planner.mode == "deterministic"
    assert config.planner.api_key == ""
    assert config.execution.default_adapter == "fake"
    assert config.aedt.version == "2026.1"
    assert config.aedt.cadence_launcher == "~/code/start_aedt_cadence.sh"


def test_load_demo_config_local_overrides_without_requiring_secret(tmp_path):
    example = tmp_path / "demo_config.example.json"
    local = tmp_path / "demo_config.local.json"
    example.write_text(
        '{"planner":{"mode":"deterministic","provider":"","model":"","base_url":"","api_key":""},'
        '"server":{"host":"127.0.0.1","port":8765},'
        '"execution":{"default_adapter":"fake","run_dir":"benchmarks/runs/stage_c1_demo_latest"}}\n',
        encoding="utf-8",
    )
    local.write_text('{"server":{"port":9000},"planner":{"mode":"llm","model":"deepseek-v4-flash"}}\n', encoding="utf-8")

    config = load_demo_config(example_path=example, local_path=local)

    assert config.server.port == 9000
    assert config.planner.mode == "llm"
    assert config.planner.model == "deepseek-v4-flash"
    assert config.planner.api_key == ""


def test_load_demo_config_accepts_aedt_overrides(tmp_path):
    example = tmp_path / "demo_config.example.json"
    local = tmp_path / "demo_config.local.json"
    example.write_text(
        '{"planner":{"mode":"deterministic","provider":"","model":"","base_url":"","api_key":""},'
        '"server":{"host":"127.0.0.1","port":8765},'
        '"execution":{"default_adapter":"fake","run_dir":"benchmarks/runs/stage_c1_demo_latest"},'
        '"aedt":{"version":"2026.1","non_graphical":true,"ansysem_root":"~/ansys_inc/v261/AnsysEM","awp_root":"~/ansys_inc/v261","timeout":900,"cadence_launcher":"~/code/start_aedt_cadence.sh"}}\n',
        encoding="utf-8",
    )
    local.write_text('{"aedt":{"version":"2025.2","awp_root":"/opt/ansys/v252","timeout":1200}}\n', encoding="utf-8")

    config = load_demo_config(example_path=example, local_path=local)

    assert config.aedt.version == "2025.2"
    assert config.aedt.awp_root == "/opt/ansys/v252"
    assert config.aedt.timeout == 1200
    assert config.aedt.cadence_launcher == "~/code/start_aedt_cadence.sh"
