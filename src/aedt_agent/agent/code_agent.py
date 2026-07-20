"""Code Agent — three-layer validation for LLM-generated PyAEDT code."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class CodeValidationResult:
    is_valid: bool
    errors: list[str]
    code: str = ""


def validate_python_syntax(code: str) -> CodeValidationResult:
    """Layer 1: validate that code is syntactically valid Python."""
    try:
        ast.parse(code)
        return CodeValidationResult(True, [], code)
    except SyntaxError as e:
        return CodeValidationResult(False, [f"SyntaxError at line {e.lineno}: {e.msg}"], code)


def validate_imports(code: str, allowed_imports: list[str]) -> CodeValidationResult:
    """Layer 2: validate that all imports are in the allowed whitelist."""
    if not allowed_imports:
        return CodeValidationResult(True, [], code)

    errors: list[str] = []
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return CodeValidationResult(False, ["could not parse code for import validation"], code)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if not _import_allowed(alias.name, allowed_imports):
                    errors.append(f"forbidden import: {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            if node.module and not _import_allowed(node.module, allowed_imports):
                errors.append(f"forbidden import from: {node.module}")

    return CodeValidationResult(len(errors) == 0, errors, code)


def _import_allowed(name: str, allowed: list[str]) -> bool:
    """Check if an import name matches the allowed list (prefix match)."""
    for prefix in allowed:
        if name == prefix or name.startswith(prefix + "."):
            return True
    return False


def validate_forbidden_patterns(code: str, patterns: list[str]) -> CodeValidationResult:
    """Validate that forbidden patterns do not appear in the code."""
    if not patterns:
        return CodeValidationResult(True, [], code)
    errors = []
    for pattern in patterns:
        if pattern in code:
            errors.append(f"forbidden pattern found: {pattern}")
    return CodeValidationResult(len(errors) == 0, errors, code)


def extract_code_block(text: str, language: str = "python") -> str:
    """Extract code from a markdown code fence ```language ... ```."""
    pattern = rf'```{language}\s*\n(.*?)\n```'
    match = re.search(pattern, text, re.DOTALL)
    if match:
        return match.group(1).strip()
    # Try any code fence
    match = re.search(r'```[a-z]*\s*\n(.*?)\n```', text, re.DOTALL)
    if match:
        return match.group(1).strip()
    # No fence found — return the raw text
    return text.strip()


def validate_code_agent_output(
    llm_output: str,
    *,
    allowed_imports: list[str] | None = None,
    forbidden_patterns: list[str] | None = None,
) -> tuple[str, list[str]]:
    """Full three-layer validation of LLM-generated code.

    Returns (code, errors). If errors is empty, the code passed all checks.
    """
    errors: list[str] = []
    code = extract_code_block(llm_output)
    if not code:
        return "", ["no code found in LLM output"]

    # Layer 1: syntax
    syntax_result = validate_python_syntax(code)
    errors.extend(syntax_result.errors)
    if not syntax_result.is_valid:
        return code, errors

    # Layer 2: imports
    if allowed_imports:
        import_result = validate_imports(code, allowed_imports)
        errors.extend(import_result.errors)

    # Layer 3: forbidden patterns
    if forbidden_patterns:
        pattern_result = validate_forbidden_patterns(code, forbidden_patterns)
        errors.extend(pattern_result.errors)

    return code, errors
