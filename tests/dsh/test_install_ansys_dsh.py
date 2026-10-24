from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

from scripts.dsh.install_ansys_dsh import BEGIN, END, apply_installation, check_installation

REPO = Path(__file__).resolve().parents[2]


def _profile(tmp_path: Path, extra: str = "") -> Path:
    profile = tmp_path / "profile"
    profile.mkdir()
    (profile / "cordis.patch.yml").write_text(
        "- id: ui-theme\n  name: '@deepseek-ai/dsh-client-ui-theme'\n  config:\n    preference: dark\n" + extra,
        encoding="utf-8",
    )
    return profile


def _db(tmp_path: Path) -> Path:
    db = tmp_path / "missions.db"
    db.write_bytes(b"")
    return db


def test_check_is_read_only_and_then_apply_is_idempotent(tmp_path):
    profile = _profile(tmp_path)
    before = (profile / "cordis.patch.yml").read_bytes()
    checked = check_installation(REPO, profile, Path(sys.executable), _db(tmp_path))
    assert checked["ok"] is True
    assert (profile / "cordis.patch.yml").read_bytes() == before

    first = apply_installation(REPO, profile, Path(sys.executable), _db(tmp_path))
    assert first["applied"] is True
    text = (profile / "cordis.patch.yml").read_text(encoding="utf-8")
    assert text.count(BEGIN) == 1
    assert text.count(END) == 1
    assert text.count("id: preset-ansys-reviewed") == 1
    assert text.count("id: preset-ansys-explore") == 1
    assert "id: ui-theme" in text

    second = apply_installation(REPO, profile, Path(sys.executable), _db(tmp_path))
    assert second["applied"] is True
    text2 = (profile / "cordis.patch.yml").read_text(encoding="utf-8")
    assert text2.count(BEGIN) == 1
    assert text2.count("id: preset-ansys-reviewed") == 1
    assert text2.count("id: preset-ansys-explore") == 1


def test_reviewed_preset_has_no_official_mcp_or_write_tools(tmp_path):
    profile = _profile(tmp_path)
    assert apply_installation(REPO, profile, Path(sys.executable), _db(tmp_path))["applied"] is True
    text = (profile / "cordis.patch.yml").read_text(encoding="utf-8")
    block = text[text.index(BEGIN) : text.index(END)]
    reviewed = block[block.index("id: preset-ansys-reviewed") : block.index("id: preset-ansys-explore")]
    assert "serverName: aedt" not in block
    assert "mcp-aedt" not in block
    assert "mcp-ansys-assistant-reviewed" in block
    assert "aedt_agent.interactive.server" in block
    assert "--workflow-db" in block
    assert str(_db(tmp_path)) in block
    assert "tool-presentation" not in reviewed
    assert "tool-pwsh" not in reviewed
    assert "tool-bash" not in reviewed
    assert "tool-fs" not in reviewed


def test_unmarked_conflict_fails_before_any_write(tmp_path):
    profile = _profile(tmp_path, "- id: preset-ansys-engineering\n  name: '@deepseek-ai/dsh-agent-preset'\n")
    before = (profile / "cordis.patch.yml").read_bytes()
    checked = check_installation(REPO, profile, Path(sys.executable), _db(tmp_path))
    assert checked["ok"] is False
    assert any("legacy unmarked" in error for error in checked["errors"])
    result = apply_installation(REPO, profile, Path(sys.executable), _db(tmp_path))
    assert result["applied"] is False
    assert (profile / "cordis.patch.yml").read_bytes() == before
    assert not (profile / "ansys-reviewed-tools.mjs").exists()


def test_apply_preserves_unrelated_js_patch_content(tmp_path):
    profile = _profile(tmp_path, "- id: custom-shell\n  name: '@deepseek-ai/dsh-tool-pwsh'\n  disabled: !!js process.platform !== 'win32'\n")
    assert apply_installation(REPO, profile, Path(sys.executable), _db(tmp_path))["applied"] is True
    text = (profile / "cordis.patch.yml").read_text(encoding="utf-8")
    assert "disabled: !!js process.platform !== 'win32'" in text
    assert text.index("id: custom-shell") < text.index(BEGIN)


def test_templates_do_not_contain_machine_user_paths():
    for path in (REPO / "integrations" / "dsh" / "agent-presets").rglob("*"):
        if path.is_file():
            text = path.read_text(encoding="utf-8", errors="ignore")
            assert "C:\\Users\\z3312" not in text, path


def test_generated_block_is_valid_yaml_after_js_placeholders(tmp_path):
    yaml = pytest.importorskip("yaml")
    profile = _profile(tmp_path)
    assert apply_installation(REPO, profile, Path(sys.executable), _db(tmp_path))["applied"] is True
    text = (profile / "cordis.patch.yml").read_text(encoding="utf-8")
    block = text[text.index(BEGIN) : text.index(END)]
    normalized = re.sub(r"!!js [^\n]+", "false", block)
    entries = yaml.safe_load(normalized)
    ids = [row["id"] for insert in entries for row in insert["insert"]]
    assert ids == ["mcp-ansys-assistant-reviewed", "preset-ansys-reviewed", "preset-ansys-explore"]
