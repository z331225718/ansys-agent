from aedt_agent.mcp.ast_guard import AstGuard


def test_ast_guard_accepts_simple_aedt_calls():
    result = AstGuard().validate("app.modeler.create_box([0,0,0], [1,1,1], name='box')")

    assert result.passed is True
    assert result.violations == []


def test_ast_guard_rejects_forbidden_import():
    result = AstGuard().validate("import subprocess\nsubprocess.run(['rm', '-rf', 'x'])")

    assert result.passed is False
    assert "forbidden import: subprocess" in result.violations
    assert "forbidden call: subprocess.run" in result.violations


def test_ast_guard_rejects_open_write():
    result = AstGuard().validate("open('x.txt', 'w')")

    assert result.passed is False
    assert "forbidden call: open" in result.violations
