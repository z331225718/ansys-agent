from __future__ import annotations

from aedt_agent.agent.code_agent import (
    extract_code_block,
    validate_code_agent_output,
    validate_forbidden_patterns,
    validate_imports,
    validate_python_syntax,
)


def test_validate_python_syntax_passes_valid_code():
    result = validate_python_syntax("x = 1\ny = 2\nprint(x + y)")
    assert result.is_valid
    assert not result.errors


def test_validate_python_syntax_catches_syntax_error():
    result = validate_python_syntax("x = 1\nif x == 1\n    print(x)")
    assert not result.is_valid
    assert any("SyntaxError" in e for e in result.errors)


def test_validate_imports_allows_whitelisted():
    result = validate_imports("import pyedb\nfrom ansys.aedt.core import Hfss3dLayout", ["pyedb", "ansys.aedt.core"])
    assert result.is_valid
    assert not result.errors


def test_validate_imports_rejects_forbidden():
    result = validate_imports("import os\nimport pyedb", ["pyedb"])
    assert not result.is_valid
    assert any("os" in e for e in result.errors)


def test_validate_imports_allows_subpackage():
    result = validate_imports("from ansys.aedt.core.hfss import Hfss", ["ansys.aedt.core"])
    assert result.is_valid


def test_validate_forbidden_patterns_detects_os_system():
    result = validate_forbidden_patterns("os.system('rm -rf /')", ["os.system", "subprocess"])
    assert not result.is_valid
    assert any("os.system" in e for e in result.errors)


def test_validate_forbidden_patterns_passes_clean_code():
    result = validate_forbidden_patterns("x = aedt.analyze_setup('setup1')", ["os.system"])
    assert result.is_valid


def test_extract_code_block_finds_fenced_code():
    text = "Here is code:\n```python\nprint('hello')\n```\nDone."
    code = extract_code_block(text)
    assert code == "print('hello')"


def test_extract_code_block_returns_raw_when_no_fence():
    text = "just some text without code fence"
    code = extract_code_block(text)
    assert code == text


def test_validate_code_agent_output_full_pass():
    llm_output = "```python\nfrom pyedb import Edb\nedb = Edb()\n```"
    code, errors = validate_code_agent_output(
        llm_output,
        allowed_imports=["pyedb"],
        forbidden_patterns=["os.system"],
    )
    assert code
    assert not errors


def test_validate_code_agent_output_catches_bad_import():
    llm_output = "```python\nimport os\nos.system('ls')\n```"
    code, errors = validate_code_agent_output(
        llm_output,
        allowed_imports=["pyedb"],
        forbidden_patterns=["os.system"],
    )
    assert code
    assert errors
