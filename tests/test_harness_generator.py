import json
import subprocess

import pytest

from aedt_agent.benchmark.harness_generator import HarnessGenerator, HarnessGroupConfig, extract_code


def test_extract_code_prefers_fenced_python():
    output = "notes\n```python\napp.modeler.create_box([0, 0, 0], [1, 1, 1], name='Box')\n```"

    assert extract_code(output).startswith("app.modeler.create_box")


def test_extract_code_reads_json_stream_result():
    output = json.dumps({"type": "result", "result": "```python\napp.save_project()\n```"})

    assert extract_code(output) == "app.save_project()"


def test_extract_code_strips_inline_backticks_from_json_result():
    output = json.dumps({"type": "result", "result": "`app.save_project()`"})

    assert extract_code(output) == "app.save_project()"


def test_extract_code_recovers_code_after_plain_text_preamble():
    output = """Now I have the fix.

1. Use the official signature.

A = 22.86
port_sheet = app.modeler.create_rectangle("XY", [-A / 2, 0, 0], [A, 10], name="port_face")
app.wave_port(assignment=port_sheet.name, integration_line=[[0, 0, 0], [0, 10, 0]], name="Port1")
"""

    code = extract_code(output)

    assert code.startswith("A = 22.86")
    assert "Now I have" not in code


def test_harness_generator_invokes_cli_and_writes_artifacts(tmp_path):
    calls = []

    def fake_run(command, input, cwd, env, timeout, capture_output, text):
        calls.append((command, input, cwd, timeout, capture_output, text))
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="```python\napp.modeler.create_box([0,0,0], [1,1,1], name='Box')\n```",
            stderr="",
        )

    generator = HarnessGenerator(
        command="fake-harness",
        timeout=12,
        work_dir=tmp_path / "work",
        group_configs={"A": HarnessGroupConfig(args=["--print"], cwd=str(tmp_path / "cwd"))},
        subprocess_runner=fake_run,
    )

    result = generator.generate_attempt(
        "make a box",
        task_id="T1",
        group="A",
        attempt=1,
        artifact_dir=tmp_path / "run",
    )

    assert calls[0][0] == ["fake-harness", "--print"]
    assert calls[0][1] == "make a box"
    assert result.code.startswith("app.modeler.create_box")
    assert (tmp_path / "run" / "attempt_1_harness_stdout.txt").exists()
    assert (tmp_path / "run" / "attempt_1_transcript.txt").exists()
    assert (tmp_path / "run" / "attempt_1_tool_usage.json").exists()


def test_harness_generator_raises_generation_error_for_non_code(tmp_path):
    def fake_run(**kwargs):
        return subprocess.CompletedProcess(kwargs["args"], 0, stdout="hello", stderr="")

    def fake_run_pos(command, input, cwd, env, timeout, capture_output, text):
        return subprocess.CompletedProcess(command, 0, stdout="hello", stderr="")

    generator = HarnessGenerator(
        command="fake",
        timeout=12,
        work_dir=tmp_path / "work",
        group_configs={"A": HarnessGroupConfig()},
        subprocess_runner=fake_run_pos,
    )

    with pytest.raises(ValueError):
        generator.generate_attempt("prompt", "T1", "A", 1, tmp_path / "run")
