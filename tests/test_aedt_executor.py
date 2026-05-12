from pathlib import Path

from aedt_agent.benchmark.aedt_executor import AEDTSubprocessExecutor


def test_aedt_executor_writes_wrapper_and_parses_success(tmp_path):
    calls = []

    def fake_runner(cmd, timeout, cwd, capture_output, text, env=None):
        calls.append((cmd, timeout, cwd, capture_output, text, env))

        class Result:
            returncode = 0
            stdout = '{"execution_ok": true, "validation_ok": true, "validation_result": {"passed": true}}'
            stderr = ""

        return Result()

    code_path = tmp_path / "code.py"
    validation_path = tmp_path / "validate.py"
    code_path.write_text("app.modeler.create_box([0,0,0], [1,1,1])", encoding="utf-8")
    validation_path.write_text("def validate(session_id, project_id, design_id): return {'passed': True}", encoding="utf-8")
    executor = AEDTSubprocessExecutor(python_executable="python-test", timeout=10, subprocess_runner=fake_runner)

    result = executor.execute(code_path, validation_path, tmp_path / "work")

    assert result["execution_ok"] is True
    assert result["validation_ok"] is True
    assert calls[0][0][0] == "python-test"
    assert (tmp_path / "work" / "aedt_attempt_wrapper.py").exists()
    assert "non_graphical=True" in (tmp_path / "work" / "aedt_attempt_wrapper.py").read_text(encoding="utf-8")


def test_aedt_executor_parses_runtime_error_log(tmp_path):
    def fake_runner(cmd, timeout, cwd, capture_output, text, env=None):
        class Result:
            returncode = 0
            stdout = '{"execution_ok": false, "validation_ok": false, "failure_type": "runtime_error", "log": "Traceback: NameError: broken_symbol"}'
            stderr = ""

        return Result()

    code_path = tmp_path / "code.py"
    validation_path = tmp_path / "validate.py"
    code_path.write_text("broken_symbol()", encoding="utf-8")
    validation_path.write_text("def validate(session_id, project_id, design_id): return {'passed': True}", encoding="utf-8")
    executor = AEDTSubprocessExecutor(python_executable="python-test", timeout=10, subprocess_runner=fake_runner)

    result = executor.execute(code_path, validation_path, tmp_path / "work")

    assert result["execution_ok"] is False
    assert result["validation_ok"] is False
    assert result["failure_type"] == "runtime_error"
    assert "NameError" in result["log"]


def test_aedt_executor_parses_json_before_release_logs(tmp_path):
    def fake_runner(cmd, timeout, cwd, capture_output, text, env=None):
        class Result:
            returncode = 0
            stdout = (
                "PyAEDT INFO: started\n"
                '{"execution_ok": true, "validation_ok": true, "validation_result": {"passed": true}}\n'
                "PyAEDT INFO: Desktop has been released and closed.\n"
            )
            stderr = ""

        return Result()

    code_path = tmp_path / "code.py"
    validation_path = tmp_path / "validate.py"
    code_path.write_text("", encoding="utf-8")
    validation_path.write_text("def validate(session_id, project_id, design_id): return {'passed': True}", encoding="utf-8")
    executor = AEDTSubprocessExecutor(python_executable="python-test", subprocess_runner=fake_runner)

    result = executor.execute(code_path, validation_path, tmp_path / "work")

    assert result["execution_ok"] is True
    assert result["validation_ok"] is True


def test_aedt_executor_uses_absolute_wrapper_path_for_relative_work_dir(tmp_path, monkeypatch):
    calls = []

    def fake_runner(cmd, timeout, cwd, capture_output, text, env=None):
        calls.append((cmd, cwd))

        class Result:
            returncode = 0
            stdout = '{"execution_ok": true, "validation_ok": true}'
            stderr = ""

        return Result()

    monkeypatch.chdir(tmp_path)
    code_path = Path("code.py")
    validation_path = Path("validate.py")
    code_path.write_text("", encoding="utf-8")
    validation_path.write_text("def validate(session_id, project_id, design_id): return {'passed': True}", encoding="utf-8")
    executor = AEDTSubprocessExecutor(python_executable="python-test", subprocess_runner=fake_runner)

    executor.execute(code_path, validation_path, Path("relative_work"))

    assert Path(calls[0][0][1]).is_absolute()
    assert Path(calls[0][1]).is_absolute()
