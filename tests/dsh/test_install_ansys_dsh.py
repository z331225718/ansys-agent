from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.dsh.install_ansys_dsh import (
    BEGIN,
    BUNDLE_NAME,
    END,
    apply_installation,
    check_installation,
)

REPO = Path(__file__).resolve().parents[2]


def _profile(tmp_path: Path, extra: str = "") -> Path:
    profile = tmp_path / "profile"
    profile.mkdir()
    (profile / "package.json").write_text(
        json.dumps({"name": "dsh-profile-test", "private": True, "dsh": {"profile": {"bundles": ["@deepseek-ai/dsh-base"]}}}, indent=2) + "\n",
        encoding="utf-8",
    )
    (profile / "cordis.patch.yml").write_text(
        "- id: ui-theme\n  name: '@deepseek-ai/dsh-client-ui-theme'\n  config:\n    preference: dark\n" + extra,
        encoding="utf-8",
    )
    return profile


def test_check_is_read_only_then_apply_is_idempotent(tmp_path):
    profile = _profile(tmp_path)
    before = (profile / "cordis.patch.yml").read_bytes()
    checked = check_installation(REPO, profile, Path("python"))
    assert checked["ok"] is True
    assert (profile / "cordis.patch.yml").read_bytes() == before

    first = apply_installation(REPO, profile, Path("python"))
    assert first["applied"] is True
    bundle = profile / "ansys-agent-presets-bundle"
    assert {p.name for p in bundle.iterdir()} == {"package.json", "cordis.patch.yml", "production-guard.mjs"}
    assert (profile / "node_modules" / "@local" / "dsh-ansys-presets").exists()

    second = apply_installation(REPO, profile, Path("python"))
    assert second["applied"] is False
    assert second["unchanged"] is True


def test_bundle_has_one_ansys_mode_and_production_guard(tmp_path):
    profile = _profile(tmp_path)
    assert apply_installation(REPO, profile, Path("python"))["applied"] is True
    patch = (profile / "ansys-agent-presets-bundle" / "cordis.patch.yml").read_text(encoding="utf-8")
    assert patch.count("id: preset-ansys-engineering") == 1
    assert "preset-ansys-reviewed" not in patch
    assert "preset-ansys-explore" not in patch
    assert "production-guard" in patch
    assert "mcp__ansys_assistant__apply_ansys_workflow_start" in (
        profile / "ansys-agent-presets-bundle" / "production-guard.mjs"
    ).read_text(encoding="utf-8")
    assert "__REPO_ROOT__" not in patch
    assert str(REPO.resolve()).replace("\\", "/") in patch


def test_apply_registers_bundle_and_preserves_user_config(tmp_path):
    profile = _profile(tmp_path, "  disabled: !!js process.platform !== 'win32'\n")
    assert apply_installation(REPO, profile, Path("python"))["applied"] is True
    package = json.loads((profile / "package.json").read_text(encoding="utf-8"))
    assert package["dependencies"][BUNDLE_NAME] == "link:ansys-agent-presets-bundle"
    assert BUNDLE_NAME in package["dsh"]["profile"]["bundles"]
    assert "disabled: !!js process.platform !== 'win32'" in (profile / "cordis.patch.yml").read_text(encoding="utf-8")


def test_old_managed_block_is_removed(tmp_path):
    profile = _profile(tmp_path, f"{BEGIN}\n- insert:\n    - id: old\n{END}\n")
    assert apply_installation(REPO, profile, Path("python"))["applied"] is True
    text = (profile / "cordis.patch.yml").read_text(encoding="utf-8")
    assert BEGIN not in text and END not in text


@pytest.mark.parametrize("legacy_line", [
    "- id: preset-ansys-engineering\n",
    "- id:  preset-ansys-engineering\n",
    "- id: 'preset-ansys-engineering'\n",
])
def test_unmarked_legacy_conflict_fails_before_write(tmp_path, legacy_line):
    profile = _profile(tmp_path, legacy_line)
    before = (profile / "cordis.patch.yml").read_bytes()
    result = apply_installation(REPO, profile, Path("python"))
    assert result["applied"] is False
    assert any("unmarked preset-ansys-engineering" in error for error in result["errors"])
    assert (profile / "cordis.patch.yml").read_bytes() == before
    assert not (profile / "ansys-agent-presets-bundle").exists()


def test_templates_do_not_contain_machine_user_paths():
    for path in (REPO / "integrations" / "dsh").rglob("*"):
        if path.is_file():
            text = path.read_text(encoding="utf-8", errors="ignore")
            assert "C:\\Users\\z3312" not in text, path