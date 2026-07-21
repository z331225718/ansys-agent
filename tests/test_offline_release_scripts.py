from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import re

import pytest


ROOT = Path(__file__).resolve().parents[1]
OFFLINE_SCRIPTS = ROOT / "scripts" / "offline"
INSTALLER = OFFLINE_SCRIPTS / "Install-AnsysAgentOffline.ps1"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _ps_quote(value: object) -> str:
    return str(value).replace("'", "''")


def _refresh_bundle_checksums(root: Path) -> None:
    payloads = sorted(
        path for path in root.rglob("*") if path.is_file() and path.name != "SHA256SUMS"
    )
    lines = [f"{_sha256(path)} *{path.relative_to(root).as_posix()}" for path in payloads]
    (root / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_test_bundle(root: Path) -> None:
    files = {
        "requirements-desktop.txt": "PyYAML==6.0.3\n",
        "requirements-bootstrap.txt": "pip==25.3\n",
        "runtime/pyproject.toml": "[project]\nname='aedt-agent'\nversion='0.1.0'\n",
        "runtime/src/aedt_agent/__init__.py": "",
        "runtime/docs/中文说明.md": "离线部署说明\n",
        "runtime/knowledge/api_semantics/schema.sql": "select 1;\n",
        "runtime/nodes/catalog/example.yaml": "id: example\n",
        "runtime/workflow_templates/example.json": "{}\n",
        "runtime/workflows/scripts/example.js": "// workflow\n",
        "tools/codebase-memory-mcp/0.9.0/codebase-memory-mcp.exe": "native-test-binary",
        "wheelhouse/dummy-0-py3-none-any.whl": "not-a-real-wheel",
    }
    for relative, content in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    manifest = {
        "schema_version": 2,
        "project": {
            "name": "aedt-agent",
            "version": "0.1.0",
            "git_revision": None,
            "source_dirty": False,
        },
        "target": {
            "os": "windows",
            "architecture": "amd64",
            "python": "3.12",
            "aedt": "2024.2",
        },
        "native_tools": {
            "codebase_memory_mcp": {
                "version": "0.9.0",
                "platform": "windows-amd64",
                "path": "tools/codebase-memory-mcp/0.9.0/codebase-memory-mcp.exe",
                "sha256": _sha256(
                    root / "tools/codebase-memory-mcp/0.9.0/codebase-memory-mcp.exe"
                ),
            }
        },
        "payload_file_count": len(files) + 1,
    }
    (root / "bundle.json").write_text(json.dumps(manifest), encoding="utf-8")
    _refresh_bundle_checksums(root)


def _run_verify(bundle: Path) -> subprocess.CompletedProcess[str]:
    powershell = shutil.which("powershell.exe")
    if not powershell:
        pytest.skip("Windows PowerShell is unavailable")
    return subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(INSTALLER),
            "-BundleRoot",
            str(bundle),
            "-VerifyOnly",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def _run_failed_install(bundle: Path, install_root: Path) -> subprocess.CompletedProcess[str]:
    powershell = shutil.which("powershell.exe")
    if not powershell:
        pytest.skip("Windows PowerShell is unavailable")
    return subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(INSTALLER),
            "-BundleRoot",
            str(bundle),
            "-InstallRoot",
            str(install_root),
            "-PythonExe",
            str(install_root.parent / "missing-python.exe"),
            "-SkipKnowledgePrepare",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_offline_installer_verify_only_accepts_intact_bundle(tmp_path: Path) -> None:
    _write_test_bundle(tmp_path)

    result = _run_verify(tmp_path)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "verified"
    assert payload["target_aedt"] == "2024.2"
    assert payload["target_python"] == "3.12"


def test_offline_installer_verifies_utf8_payload_filename(tmp_path: Path) -> None:
    _write_test_bundle(tmp_path)
    checksum_text = (tmp_path / "SHA256SUMS").read_text(encoding="utf-8")
    assert "runtime/docs/中文说明.md" in checksum_text

    result = _run_verify(tmp_path)

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["status"] == "verified"


def test_offline_installer_rejects_tampered_payload(tmp_path: Path) -> None:
    _write_test_bundle(tmp_path)
    (tmp_path / "requirements-desktop.txt").write_text("tampered\n", encoding="utf-8")

    result = _run_verify(tmp_path)

    assert result.returncode != 0
    assert "SHA256 mismatch" in result.stderr


def test_offline_installer_rejects_checksum_path_traversal(tmp_path: Path) -> None:
    _write_test_bundle(tmp_path)
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    with (tmp_path / "SHA256SUMS").open("a", encoding="ascii") as stream:
        stream.write(f"{_sha256(outside)} *../outside.txt\n")

    result = _run_verify(tmp_path)

    assert result.returncode != 0
    assert "Unsafe checksum path" in result.stderr


def test_offline_installer_rejects_unlisted_payload(tmp_path: Path) -> None:
    _write_test_bundle(tmp_path)
    (tmp_path / "runtime" / "src" / "aedt_agent" / "injected.py").write_text(
        "raise RuntimeError('unexpected')\n",
        encoding="utf-8",
    )

    result = _run_verify(tmp_path)

    assert result.returncode != 0
    assert "unlisted or missing payload files" in result.stderr


def test_offline_installer_requires_native_tool_in_schema_v2(tmp_path: Path) -> None:
    _write_test_bundle(tmp_path)
    manifest_path = tmp_path / "bundle.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.pop("native_tools")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    _refresh_bundle_checksums(tmp_path)

    result = _run_verify(tmp_path)

    assert result.returncode != 0
    assert "missing the pinned codebase-memory-mcp native executable" in result.stderr


def test_offline_installer_rejects_unsafe_native_tool_path(tmp_path: Path) -> None:
    _write_test_bundle(tmp_path)
    manifest_path = tmp_path / "bundle.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["native_tools"]["codebase_memory_mcp"]["path"] = "../outside.exe"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    _refresh_bundle_checksums(tmp_path)

    result = _run_verify(tmp_path)

    assert result.returncode != 0
    assert "Unsafe native tool path" in result.stderr


def test_offline_installer_rolls_back_new_root_after_install_failure(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    _write_test_bundle(bundle)
    install_root = tmp_path.parent / f"{tmp_path.name}-failed-install"
    assert not install_root.exists()

    result = _run_failed_install(bundle, install_root)

    assert result.returncode != 0
    assert "safely rolled back" in result.stdout + result.stderr
    assert not install_root.exists()


def test_offline_installer_restores_existing_empty_root_after_failure(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    _write_test_bundle(bundle)
    install_root = tmp_path.parent / f"{tmp_path.name}-empty-install"
    install_root.mkdir()

    result = _run_failed_install(bundle, install_root)

    assert result.returncode != 0
    assert "safely rolled back" in result.stdout + result.stderr
    assert install_root.is_dir()
    assert list(install_root.iterdir()) == []
    install_root.rmdir()


def test_bundle_builder_is_locked_binary_only_and_excludes_local_config() -> None:
    script = (OFFLINE_SCRIPTS / "New-AnsysAgentOfflineBundle.ps1").read_text(encoding="utf-8")

    assert '[string]$TargetPython = "3.12"' in script
    assert '"codebase-memory-mcp-windows-amd64.zip"' in script
    assert "92f96896f952e539f0d6cb34d7892a25064b677ccbf808b8f8310ad897e86f2c" in script
    assert "9a205fa5ae759fbc866bfe1554f0c05a303be9ae6e0a00f94d875dc0c25e0680" in script
    assert "codebase_memory_mcp" in script
    assert '"third_party\\codebase-memory-mcp\\LICENSE"' in script
    for expected in (
        '"--frozen"',
        '"--require-hashes"',
        '"--only-binary=:all:"',
        '"*.local.*"',
        "Get-FileHash",
        "Assert-NoObviousSecrets",
        '"knowledge"',
        '"nodes"',
        '"workflow_templates"',
        '"workflows"',
        '"reference_scripts"',
        '"tasks"',
        '"validation_scripts"',
    ):
        assert expected in script


def test_offline_installer_replaces_network_shim_with_bundled_native_binary() -> None:
    installer = INSTALLER.read_text(encoding="utf-8")
    preflight = (OFFLINE_SCRIPTS / "Test-AnsysAgentOffline.ps1").read_text(encoding="utf-8")

    assert 'Scripts\\codebase-memory-mcp.exe' in installer
    assert 'source = "bundled-native-executable"' in installer
    assert 'Copy-Item -LiteralPath $nativeToolSource' in installer
    assert 'Arguments @("--version")' in preflight
    assert "network downloader shim" in preflight
    assert '"bundled-native-verified"' in preflight


def test_bundle_builder_writes_release_metadata_as_utf8_without_bom() -> None:
    script = (OFFLINE_SCRIPTS / "New-AnsysAgentOfflineBundle.ps1").read_text(encoding="utf-8")
    common = (OFFLINE_SCRIPTS / "OfflineRelease.Common.ps1").read_text(encoding="utf-8")

    assert "UTF8Encoding]::new($false, $true)" in common
    assert "Write-Utf8NoBomLines -Path $bootstrapRequirements" in script
    assert "Write-Utf8NoBomText -Path $manifestPath" in script
    assert "Write-Utf8NoBomLines -Path $checksumPath" in script
    assert "Write-Utf8NoBomLines -Path $zipHashPath" in script


def test_powershell5_utf8_writer_preserves_unicode_without_bom(tmp_path: Path) -> None:
    powershell = shutil.which("powershell.exe")
    if not powershell:
        pytest.skip("Windows PowerShell is unavailable")
    common = OFFLINE_SCRIPTS / "OfflineRelease.Common.ps1"
    output = tmp_path / "SHA256SUMS"
    command = (
        f". '{common}'; "
        f"Write-Utf8NoBomLines -Path '{output}' -Lines @('abc *docs/中文说明.md')"
    )

    result = subprocess.run(
        [powershell, "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    raw = output.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert raw.decode("utf-8") == "abc *docs/中文说明.md\r\n"


def test_aedt_2024r2_smoke_uses_read_only_cli_contract() -> None:
    script = (OFFLINE_SCRIPTS / "Invoke-Aedt2024R2Smoke.ps1").read_text(encoding="utf-8")
    common = (OFFLINE_SCRIPTS / "OfflineRelease.Common.ps1").read_text(encoding="utf-8")

    assert "[ValidateRange(0, 65535)]" in script
    assert "Port 0 is only valid with -StartAedt" in script
    assert '"live-sessions"' in script
    assert '"live-launch"' in script
    assert '"live-info"' in script
    assert '"--aedt-version", $AedtVersion' in script
    assert '"--install-dir", $aedtExecutable' in script
    assert '"--non-graphical"' in script
    assert "$Port = [int]$launch.session.port" in script
    assert "Invoke-CapturedNativeProcess" in script
    assert "2>&1" not in script
    assert "1> $stdoutPath 2> $stderrPath" in common
    assert "Start-Process" not in script
    assert '"-grpcsrv"' not in script
    assert "parameterize-width" not in script
    assert "apply_live" not in script
    assert "project_save" not in script


def test_powershell5_captured_native_process_keeps_success_stderr_out_of_json(
    tmp_path: Path,
) -> None:
    powershell = shutil.which("powershell.exe")
    if not powershell:
        pytest.skip("Windows PowerShell is unavailable")
    probe = tmp_path / "stream-probe.py"
    probe.write_text(
        "import sys\n"
        "sys.stderr.write('PyAEDT INFO on stderr\\n')\n"
        "sys.stdout.write('{\"status\":\"ok\"}')\n",
        encoding="utf-8",
    )
    common = OFFLINE_SCRIPTS / "OfflineRelease.Common.ps1"
    command = (
        "$ErrorActionPreference = 'Stop'; "
        f". '{_ps_quote(common)}'; "
        "$json = Invoke-CapturedNativeProcess "
        f"-FilePath '{_ps_quote(sys.executable)}' "
        f"-Arguments @('{_ps_quote(probe)}') -Label 'stream probe'; "
        "($json | ConvertFrom-Json).status"
    )

    result = subprocess.run(
        [powershell, "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"
    assert "PyAEDT INFO" not in result.stdout + result.stderr


def test_powershell5_captured_native_process_reports_stderr_on_failure(tmp_path: Path) -> None:
    powershell = shutil.which("powershell.exe")
    if not powershell:
        pytest.skip("Windows PowerShell is unavailable")
    probe = tmp_path / "failed-stream-probe.py"
    probe.write_text(
        "import sys\n"
        "sys.stdout.write('stdout detail\\n')\n"
        "sys.stderr.write('stderr detail\\n')\n"
        "raise SystemExit(7)\n",
        encoding="utf-8",
    )
    common = OFFLINE_SCRIPTS / "OfflineRelease.Common.ps1"
    command = (
        "$ErrorActionPreference = 'Stop'; "
        f". '{_ps_quote(common)}'; "
        "Invoke-CapturedNativeProcess "
        f"-FilePath '{_ps_quote(sys.executable)}' "
        f"-Arguments @('{_ps_quote(probe)}') -Label 'stream probe'"
    )

    result = subprocess.run(
        [powershell, "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode != 0
    assert "exit code 7" in result.stderr
    assert "stdout detail" in result.stderr
    assert "stderr detail" in result.stderr


def test_offline_preflight_checks_claude_help_security_flags() -> None:
    script = (OFFLINE_SCRIPTS / "Test-AnsysAgentOffline.ps1").read_text(encoding="utf-8")

    assert '@("--help")' in script
    for flag in (
        "--settings",
        "--setting-sources",
        "--mcp-config",
        "--strict-mcp-config",
        "--tools",
        "--allowedTools",
        "--disallowedTools",
        "--no-chrome",
        "--append-system-prompt-file",
        "--permission-mode",
    ):
        assert flag in script
    assert 'claudeStatus = "incompatible"' in script
    assert '"--version"' in script
    assert "Claude Code option parser preflight" in script
    assert '"--setting-sources="' in script
    assert "--bare" not in script
    assert "--disable-slash-commands" not in script


def test_offline_preflight_imports_desktop_dependencies_in_target_python() -> None:
    modules = ("ansys.aedt.core", "pyedb", "clr", "fastmcp", "codebase_memory_mcp")
    missing = [name for name in modules if importlib.util.find_spec(name) is None]
    if missing:
        pytest.skip(f"desktop extras are not installed in this test environment: {missing}")

    result = subprocess.run(
        [sys.executable, "-c", "import ansys.aedt.core,pyedb,clr,fastmcp,codebase_memory_mcp"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    script = (OFFLINE_SCRIPTS / "Test-AnsysAgentOffline.ps1").read_text(encoding="utf-8")
    for module in modules:
        assert module in script
    assert "dependency_import_status" in script
    assert "dependencyImportProbePath" in script
    assert "Write-Utf8NoBomText -Path $dependencyImportProbePath" in script
    assert "Remove-SafeTemporaryFile" in script


def test_powershell5_runs_full_offline_preflight_with_target_python() -> None:
    powershell = shutil.which("powershell.exe")
    claude = shutil.which("claude")
    if not powershell or not claude:
        pytest.skip("Windows PowerShell and Claude Code are required for the executable preflight")
    modules = ("ansys.aedt.core", "pyedb", "clr", "fastmcp", "codebase_memory_mcp")
    missing = [name for name in modules if importlib.util.find_spec(name) is None]
    if missing:
        pytest.skip(f"desktop extras are not installed in this test environment: {missing}")
    install_record = ROOT / ".aedt-agent" / "install.json"
    created_record = not install_record.exists()
    if created_record:
        install_record.parent.mkdir(parents=True, exist_ok=True)
        install_record.write_text('{"schema_version":1}\n', encoding="utf-8")
    temp_root = Path(tempfile.gettempdir())
    before_probes = set(temp_root.glob("ansys-agent-dependency-import-*.py"))
    try:
        result = subprocess.run(
            [
                powershell,
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(OFFLINE_SCRIPTS / "Test-AnsysAgentOffline.ps1"),
                "-InstallRoot",
                str(ROOT),
                "-SkipAedtCheck",
                "-SkipApiMemoryCheck",
                "-ClaudeExecutable",
                claude,
                "-RequireClaude",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    finally:
        if created_record:
            install_record.unlink(missing_ok=True)

    assert result.returncode == 0, result.stderr
    assert re.search(r'"dependency_import_status"\s*:\s*"passed"', result.stdout)
    assert re.search(r'"claude_status"\s*:\s*"compatible"', result.stdout)
    assert set(temp_root.glob("ansys-agent-dependency-import-*.py")) == before_probes


def test_find_aedt_executable_prefers_root_and_falls_back_to_win64(tmp_path: Path) -> None:
    powershell = shutil.which("powershell.exe")
    if not powershell:
        pytest.skip("Windows PowerShell is unavailable")
    common = OFFLINE_SCRIPTS / "OfflineRelease.Common.ps1"
    root_executable = tmp_path / "ansysedt.exe"
    win64_executable = tmp_path / "Win64" / "ansysedt.exe"
    win64_executable.parent.mkdir()
    root_executable.write_bytes(b"root")
    win64_executable.write_bytes(b"win64")
    command = (
        f". '{common}'; "
        f"Find-AedtExecutable -AedtRoot '{tmp_path}'"
    )

    preferred = subprocess.run(
        [powershell, "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert preferred.returncode == 0, preferred.stderr
    assert Path(preferred.stdout.strip()) == root_executable

    root_executable.unlink()
    fallback = subprocess.run(
        [powershell, "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert fallback.returncode == 0, fallback.stderr
    assert Path(fallback.stdout.strip()) == win64_executable
