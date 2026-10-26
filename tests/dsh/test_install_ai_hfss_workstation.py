from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.dsh.install_ai_hfss_workstation import (
    BUNDLE_DIR_NAME,
    BUNDLE_NAME,
    DEFAULT_WORKBENCH_URL,
    MANIFEST_NAME,
    PANEL_ROW_ID,
    SERVICE_FILE,
    TEMPLATE_FILES,
    _contract_problems,
    apply_installation,
    check_installation,
    normalize_workbench_url,
    rendered_bundle,
    rollback_installation,
)

REPO = Path(__file__).resolve().parents[2]


def _profile(tmp_path: Path, extra_patch: str = "") -> Path:
    profile = tmp_path / "profile"
    profile.mkdir()
    (profile / "package.json").write_text(
        json.dumps(
            {
                "name": "dsh-profile-test",
                "private": True,
                "dsh": {"profile": {"bundles": ["@deepseek-ai/dsh-base", "@deepseek-ai/dsh-web-app"]}},
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (profile / "cordis.patch.yml").write_text(
        "- id: ui-theme\n  name: '@deepseek-ai/dsh-client-ui-theme'\n  config:\n    preference: dark\n" + extra_patch,
        encoding="utf-8",
    )
    return profile


def test_check_is_read_only_and_reports_actions(tmp_path):
    profile = _profile(tmp_path)
    package_before = (profile / "package.json").read_bytes()
    patch_before = (profile / "cordis.patch.yml").read_bytes()

    report = check_installation(REPO, profile)

    assert report["ok"] is True
    assert report["errors"] == []
    assert report["installed"] is False
    assert any("bundle will be installed" in item for item in report["warnings"])
    assert any("bundle list will include" in item for item in report["warnings"])
    assert (profile / "package.json").read_bytes() == package_before
    assert (profile / "cordis.patch.yml").read_bytes() == patch_before
    assert not (profile / BUNDLE_DIR_NAME).exists()


def test_apply_installs_contract_then_is_idempotent(tmp_path):
    profile = _profile(tmp_path)
    first = apply_installation(REPO, profile)
    assert first["ok"] is True
    assert first["applied"] is True
    assert first["restart_required"] is True

    bundle = profile / BUNDLE_DIR_NAME
    assert {path.name for path in bundle.iterdir()} == set(TEMPLATE_FILES) | {SERVICE_FILE}

    # The browser half carries the rendered URL, never the template token.
    client = (bundle / "client.js").read_text(encoding="utf-8")
    assert "__WORKBENCH_URL__" not in client
    assert DEFAULT_WORKBENCH_URL in client

    # The bundle's own patch mounts the host loader row; the profile patch is untouched.
    patch = (bundle / "cordis.patch.yml").read_text(encoding="utf-8")
    assert f"id: {PANEL_ROW_ID}" in patch
    assert f'name: "{BUNDLE_NAME}"' in patch
    assert (profile / "cordis.patch.yml").read_text(encoding="utf-8").count(PANEL_ROW_ID) == 0

    package = json.loads((profile / "package.json").read_text(encoding="utf-8"))
    assert package["dependencies"][BUNDLE_NAME] == f"link:{BUNDLE_DIR_NAME}"
    assert package["dsh"]["profile"]["bundles"][-1] == BUNDLE_NAME

    link = profile / "node_modules" / "@local" / "dsh-ai-hfss-workstation"
    assert link.exists()
    assert link.resolve() == bundle.resolve()

    assert (profile / MANIFEST_NAME).is_file()

    second = apply_installation(REPO, profile)
    assert second["applied"] is False
    assert second["unchanged"] is True
    package_after = json.loads((profile / "package.json").read_text(encoding="utf-8"))
    assert package_after["dsh"]["profile"]["bundles"].count(BUNDLE_NAME) == 1


def test_rollback_restores_profile_bytes_exactly(tmp_path):
    profile = _profile(tmp_path)
    package_before = (profile / "package.json").read_bytes()
    patch_before = (profile / "cordis.patch.yml").read_bytes()

    assert apply_installation(REPO, profile)["applied"] is True
    assert (profile / "package.json").read_bytes() != package_before

    result = rollback_installation(REPO, profile)

    assert result["ok"] is True
    assert result["rolled_back"] is True
    assert (profile / "package.json").read_bytes() == package_before
    assert (profile / "cordis.patch.yml").read_bytes() == patch_before
    assert not (profile / BUNDLE_DIR_NAME).exists()
    assert not (profile / "node_modules" / "@local" / "dsh-ai-hfss-workstation").exists()
    assert not (profile / MANIFEST_NAME).exists()
    package = json.loads((profile / "package.json").read_text(encoding="utf-8"))
    assert BUNDLE_NAME not in package["dsh"]["profile"]["bundles"]
    assert BUNDLE_NAME not in package.get("dependencies", {})


def test_rollback_without_manifest_uses_latest_snapshot(tmp_path):
    profile = _profile(tmp_path)
    package_before = (profile / "package.json").read_bytes()
    assert apply_installation(REPO, profile)["applied"] is True
    (profile / MANIFEST_NAME).unlink()

    result = rollback_installation(REPO, profile)

    assert result["ok"] is True
    assert result["rolled_back"] is True
    assert (profile / "package.json").read_bytes() == package_before
    assert not (profile / BUNDLE_DIR_NAME).exists()


def test_rollback_is_safe_when_nothing_is_installed(tmp_path):
    profile = _profile(tmp_path)
    result = rollback_installation(REPO, profile)
    assert result["ok"] is True
    assert result["rolled_back"] is False
    assert any("nothing to roll back" in note for note in result["notes"])


def test_rollback_tolerates_partially_removed_bundle(tmp_path):
    profile = _profile(tmp_path)
    assert apply_installation(REPO, profile)["applied"] is True
    # Simulate a half-finished manual cleanup: files gone, registration left behind.
    import shutil

    shutil.rmtree(profile / BUNDLE_DIR_NAME)

    result = rollback_installation(REPO, profile)

    assert result["ok"] is True
    package = json.loads((profile / "package.json").read_text(encoding="utf-8"))
    assert BUNDLE_NAME not in package["dsh"]["profile"]["bundles"]


def test_apply_refuses_duplicate_hand_written_panel_row(tmp_path):
    profile = _profile(tmp_path, f"- insert:\n    - id: {PANEL_ROW_ID}\n      name: some.other.plugin\n")
    report = check_installation(REPO, profile)
    assert report["ok"] is False
    assert any("already declares a row" in item for item in report["errors"])
    assert apply_installation(REPO, profile)["applied"] is False


def test_custom_workbench_url_is_rendered(tmp_path):
    profile = _profile(tmp_path)
    url = "http://10.0.0.5:9001/workbench"
    assert apply_installation(REPO, profile, url)["applied"] is True
    client = (profile / BUNDLE_DIR_NAME / "client.js").read_text(encoding="utf-8")
    assert f'FALLBACK_WORKBENCH_URL = "{url}";' in client
    manifest = json.loads((profile / MANIFEST_NAME).read_text(encoding="utf-8"))
    assert manifest["workbench_url"] == url


@pytest.mark.parametrize(
    "value",
    ["ftp://host/x", "not-a-url", "http://", "http://host/x\");alert(1);//", "javascript:alert(1)"],
)
def test_workbench_url_rejects_unsafe_values(value):
    with pytest.raises(ValueError):
        normalize_workbench_url(value)


def test_workbench_url_normalizes_parts():
    assert normalize_workbench_url("http://127.0.0.1:8766/workbench#frag") == "http://127.0.0.1:8766/workbench"
    assert normalize_workbench_url("https://host:443") == "https://host:443/"


def test_contract_check_rejects_broken_bundles():
    rendered = rendered_bundle(REPO)
    assert _contract_problems(REPO, rendered) == []

    broken = dict(rendered)
    broken["package.json"] = json.loads(rendered["package.json"])
    del broken["package.json"]["dsh"]["client"]
    broken["package.json"] = json.dumps(broken["package.json"])
    assert any("dsh.client.platform" in item for item in _contract_problems(REPO, broken))

    broken_client = dict(rendered)
    broken_client["client.js"] = rendered["client.js"].replace("sidebar.panellist", "sidebar.other")
    assert any("sidebar.panellist" in item for item in _contract_problems(REPO, broken_client))


def test_rendered_bundle_has_no_machine_paths_and_keeps_ids():
    rendered = rendered_bundle(REPO)
    package = json.loads(rendered["package.json"])
    assert package["name"] == BUNDLE_NAME
    assert package["dsh"]["client"]["platform"] == "web"
    assert "./client" in package["exports"] and "." in package["exports"]
    marker = "C:" + "\\" + "Users"
    for name in TEMPLATE_FILES:
        assert marker not in rendered[name], name
    # service.json is the machine spec: absolute forward-slash paths are required.
    spec = json.loads(rendered[SERVICE_FILE])
    assert "\\" not in spec["repoRoot"]
    assert spec["repoRoot"].startswith("/") or ":/" in spec["repoRoot"]
    assert 'id: "@local/dsh-ai-hfss-workstation"' in rendered["client.js"]

def test_service_spec_defaults_match_the_repo(tmp_path):
    profile = _profile(tmp_path)
    assert apply_installation(REPO, profile)["applied"] is True
    spec = json.loads((profile / BUNDLE_DIR_NAME / SERVICE_FILE).read_text(encoding="utf-8"))
    assert spec["repoRoot"] == REPO.resolve().as_posix()
    assert spec["port"] == 8766
    assert spec["workbenchPath"] == "/workbench"
    assert spec["host"] == "127.0.0.1"
    assert spec["python"].endswith("python.exe") or spec["python"].endswith("python")
    assert "\\" not in spec["python"] and "\\" not in spec["logFile"]
    assert spec["missionsDb"].endswith("missions.db")
    assert spec["pidFile"].endswith("dashboard.pid")
    assert spec["startupTimeoutMs"] > 0


def test_service_port_follows_the_workbench_url(tmp_path):
    profile = _profile(tmp_path)
    url = "http://127.0.0.1:9010/workbench"
    assert apply_installation(REPO, profile, url)["applied"] is True
    spec = json.loads((profile / BUNDLE_DIR_NAME / SERVICE_FILE).read_text(encoding="utf-8"))
    assert spec["port"] == 9010, "the launcher must bind the port the panel embeds"


def test_service_paths_can_be_overridden(tmp_path):
    profile = _profile(tmp_path)
    db = tmp_path / "runs" / "missions.db"
    log = tmp_path / "runs" / "dash.log"
    python = tmp_path / "py" / "python.exe"
    assert apply_installation(REPO, profile, missions_db=db, log_file=log, python=python)["applied"] is True
    spec = json.loads((profile / BUNDLE_DIR_NAME / SERVICE_FILE).read_text(encoding="utf-8"))
    assert spec["missionsDb"] == db.as_posix()
    assert spec["logFile"] == log.as_posix()
    assert spec["python"] == python.as_posix()


def test_contract_check_requires_the_control_api():
    rendered = rendered_bundle(REPO)
    assert _contract_problems(REPO, rendered) == []

    broken = dict(rendered)
    broken["index.js"] = rendered["index.js"].replace('action === "/start"', 'action === "/nope"')
    assert any("control-API marker" in item for item in _contract_problems(REPO, broken))

    no_spec = dict(rendered)
    del no_spec[SERVICE_FILE]
    assert any("service.json is missing" in item for item in _contract_problems(REPO, no_spec))
