from aedt_agent.benchmark.graders import (
    check_allowed_api_usage,
    check_restricted_python,
    check_syntax,
)


def test_check_syntax_detects_error():
    result = check_syntax("def broken(:\n    pass")
    assert result.passed is False


def test_check_restricted_python_blocks_os_system():
    result = check_restricted_python("import os\nos.system('rm -rf /')")
    assert result.passed is False
    assert any("os.system" in violation for violation in result.violations)


def test_check_allowed_api_usage_allows_whitelisted_calls():
    code = "app.modeler.create_box([0,0,0],[1,1,1])\napp.assign_material('box', 'copper')"
    result = check_allowed_api_usage(
        code,
        allowed_apis=["Hfss.modeler.create_box", "Hfss.assign_material"],
    )
    assert result.passed is True

