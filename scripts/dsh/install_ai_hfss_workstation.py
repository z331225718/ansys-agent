#!/usr/bin/env python
"""Install (and roll back) the AI-HFSS Workstation DSH surface bundle.

The bundle registers ONE host loader row that mounts this package's browser
half. The runtime client-modules service scans that row, reads the package's
`dsh.client` declaration plus its `./client` export, and serves the browser
half through the /plugins route, where it registers:

  * a sidebar panel entry ("AI-HFSS 工作站") at parity with the built-in
    插件 (plugin-manager) and 自动化任务 (schedule) surfaces, and
  * a main-area page keyed by the same panel id.

The page embeds the read-only SI/AEDT Mission workbench dashboard.

Two properties make this safe to try and safe to undo:

1. The loader row lives in the bundle's OWN cordis patch
   (`dsh.bundle.patch`), which dsh applies as a layer from
   `dsh.profile.bundles`. The profile's `cordis.patch.yml` is therefore never
   edited.
2. `apply` snapshots `package.json` before touching it and records the
   snapshot in a manifest, so `rollback` restores the pre-install bytes rather
   than trying to invert an edit.

Commands
--------
check     read-only; report what apply would do and whether the source bundle
          still satisfies the client-module contract.
apply     install/update the bundle, junction and profile registration.
rollback  remove every artifact this tool created and restore package.json.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

BUNDLE_NAME = "@local/dsh-ai-hfss-workstation"
BUNDLE_DIR_NAME = "ai-hfss-workstation-bundle"
MANIFEST_NAME = "ai-hfss-workstation-install.json"
TEMPLATE_FILES = ("package.json", "cordis.patch.yml", "index.js", "client.js")
# Generated per machine (absolute paths for the launcher), never a repo template.
SERVICE_FILE = "service.json"
PANEL_ROW_ID = "ai-hfss-workstation"
WORKBENCH_URL_TOKEN = "__WORKBENCH_URL__"
DEFAULT_WORKBENCH_URL = "http://127.0.0.1:8766/workbench"
BACKUP_TAG = "ai-hfss"
DEFAULT_PROFILE_CONFIG = "config/execution_profiles/local_real_aedt.json"
DEFAULT_STARTUP_TIMEOUT_MS = 45000
# Machine-local runtime state for the dashboard the panel starts.
RUNTIME_DIR_NAME = "ai-hfss-workstation"
# A machine-absolute user path must never be baked into a rendered bundle: the
# bundle is copied into the profile and may be inspected or moved.
_USER_PATH = re.compile(r"[A-Za-z]:\\Users\\", re.IGNORECASE)


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _repo_bundle(repo_root: Path) -> Path:
    return repo_root / "integrations" / "dsh" / "bundles" / "ai-hfss-workstation"


def _bundle_dir(profile_dir: Path) -> Path:
    return profile_dir / BUNDLE_DIR_NAME


def _link_path(profile_dir: Path) -> Path:
    return profile_dir / "node_modules" / "@local" / "dsh-ai-hfss-workstation"


def _manifest_path(profile_dir: Path) -> Path:
    return profile_dir / MANIFEST_NAME


def normalize_workbench_url(value: str) -> str:
    """Validate and normalize the embedded workbench URL.

    The URL is rendered into a JavaScript string literal, so it must stay
    inside a conservative character set; anything else is rejected rather than
    escaped silently.
    """
    raw = value.strip()
    parts = urlsplit(raw)
    if parts.scheme not in ("http", "https"):
        raise ValueError("workbench URL must be an http or https URL")
    if not parts.netloc:
        raise ValueError("workbench URL must include a host")
    path = parts.path or "/"
    # Validate exactly the text that will be rendered into the string literal.
    # The fragment is dropped, so it never reaches the bundle.
    rendered = urlunsplit((parts.scheme, parts.netloc, path, parts.query, ""))
    if not re.fullmatch(r"[A-Za-z0-9._:\-\[\]/~%]{1,2048}", rendered):
        raise ValueError("workbench URL contains unsupported characters")
    return rendered


def default_python(repo_root: Path) -> str:
    """The interpreter the launcher should use, preferring the repo virtualenv."""
    for relative in (Path(".venv") / "Scripts" / "python.exe", Path(".venv") / "bin" / "python"):
        candidate = repo_root / relative
        if candidate.is_file():
            return candidate.as_posix()
    return "python"


def runtime_paths(runtime_dir: Path) -> dict[str, str]:
    """Files the panel writes while running the dashboard."""
    return {
        "missionsDb": (runtime_dir / "missions.db").as_posix(),
        "logFile": (runtime_dir / "dashboard.log").as_posix(),
        "pidFile": (runtime_dir / "dashboard.pid").as_posix(),
    }


def service_spec(
    repo_root: Path,
    workbench_url: str,
    *,
    python: Path | str | None = None,
    missions_db: Path | str | None = None,
    log_file: Path | str | None = None,
    runtime_dir: Path | None = None,
) -> dict[str, Any]:
    """Build the machine specification the host half reads at start time."""
    url = normalize_workbench_url(workbench_url)
    parts = urlsplit(url)
    runtime = runtime_dir or (Path.home() / ".dsh" / RUNTIME_DIR_NAME)
    defaults = runtime_paths(runtime)
    return {
        "repoRoot": repo_root.resolve().as_posix(),
        "python": Path(str(python)).as_posix() if python is not None else default_python(repo_root),
        "host": parts.hostname or "127.0.0.1",
        "port": parts.port or (443 if parts.scheme == "https" else 80),
        "workbenchPath": parts.path or "/workbench",
        "missionsDb": Path(str(missions_db)).as_posix() if missions_db is not None else defaults["missionsDb"],
        "logFile": Path(str(log_file)).as_posix() if log_file is not None else defaults["logFile"],
        "pidFile": defaults["pidFile"],
        "profileConfig": DEFAULT_PROFILE_CONFIG,
        "startupTimeoutMs": DEFAULT_STARTUP_TIMEOUT_MS,
    }


def rendered_bundle(
    repo_root: Path,
    workbench_url: str = DEFAULT_WORKBENCH_URL,
    *,
    python: Path | str | None = None,
    missions_db: Path | str | None = None,
    log_file: Path | str | None = None,
    runtime_dir: Path | None = None,
) -> dict[str, str]:
    """Render the bundle templates plus this machine's service specification."""
    source = _repo_bundle(repo_root)
    url = normalize_workbench_url(workbench_url)
    rendered: dict[str, str] = {}
    for name in TEMPLATE_FILES:
        text = (source / name).read_text(encoding="utf-8")
        rendered[name] = text.replace(WORKBENCH_URL_TOKEN, url)
    spec = service_spec(
        repo_root,
        url,
        python=python,
        missions_db=missions_db,
        log_file=log_file,
        runtime_dir=runtime_dir,
    )
    rendered[SERVICE_FILE] = json.dumps(spec, indent=2, sort_keys=True) + "\n"
    return rendered


def _package(profile_dir: Path) -> dict[str, Any]:
    path = profile_dir / "package.json"
    if not path.is_file():
        return {"name": "dsh-profile", "private": True, "dependencies": {}, "dsh": {"profile": {"bundles": []}}}
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else {}


def _bundles(profile_dir: Path) -> list[str]:
    package = _package(profile_dir)
    bundles = ((package.get("dsh") or {}).get("profile") or {}).get("bundles") or []
    return [item for item in bundles if isinstance(item, str)]


def _has_bundle_entries(profile_dir: Path) -> bool:
    """Whether the profile still names this bundle in its dependency or layer list."""
    package = _package(profile_dir)
    dependencies = package.get("dependencies") or {}
    return BUNDLE_NAME in dependencies or BUNDLE_NAME in _bundles(profile_dir)


def _link_matches(profile_dir: Path) -> bool:
    link = _link_path(profile_dir)
    if not link.exists():
        return False
    try:
        return link.resolve() == _bundle_dir(profile_dir).resolve()
    except OSError:
        return False


def _contract_problems(repo_root: Path, rendered: dict[str, str]) -> list[str]:
    """Static check of the client-module contract the runtime relies on.

    These are exactly the failure modes that produce a silent no-op instead of
    a visible error: a missing `dsh.client`, a missing `./client` export, or a
    browser bundle whose registration id does not match the package name.
    """
    problems: list[str] = []
    raw = rendered.get("package.json")
    if raw is None:
        return ["rendered package.json is missing"]
    try:
        package = json.loads(raw)
    except json.JSONDecodeError as exc:
        return [f"rendered package.json is not valid JSON: {exc}"]
    if package.get("name") != BUNDLE_NAME:
        problems.append(f"package name must be {BUNDLE_NAME!r}, found {package.get('name')!r}")
    dsh = package.get("dsh") or {}
    client = dsh.get("client") or {}
    if client.get("platform") != "web":
        problems.append("dsh.client.platform must be 'web' so the runtime scans the browser half")
    exports = package.get("exports") or {}
    if "./client" not in exports:
        problems.append("exports['./client'] is required by the client-modules scan")
    if "." not in exports:
        problems.append("exports['.'] is required: the host loader row imports the package root")
    bundle_patch = (dsh.get("bundle") or {}).get("patch")
    if not bundle_patch:
        problems.append("dsh.bundle.patch is required so the loader row is mounted")
    host = rendered.get("index.js", "")
    for symbol in ("export function apply", "export const name"):
        if symbol not in host:
            problems.append(f"index.js does not export {symbol!r}; the loader row would fail to mount")
    # The panel starts/stops the dashboard through these host routes.
    for marker in ('"/api/ai-hfss"', "webServer.register", 'action === "/start"', 'action === "/stop"'):
        if marker not in host:
            problems.append(f"index.js is missing the control-API marker {marker!r}")
    client_js = rendered.get("client.js", "")
    if "window.__ModuleLoader__.load" not in client_js:
        problems.append("client.js must be a lazy-CJS bundle calling window.__ModuleLoader__.load")
    if f'id: "{BUNDLE_NAME}"' not in client_js:
        problems.append(f"client.js registration id must be {BUNDLE_NAME!r}")
    if "sidebar.panellist" not in client_js:
        problems.append("client.js must register the sidebar.panellist entry")
    if '"main"' not in client_js:
        problems.append("client.js must register the main slot page")
    if WORKBENCH_URL_TOKEN in client_js:
        problems.append("client.js still carries the unrendered workbench URL token")
    # The fallback URL must be a rendered http(s) URL, whatever the port.
    if 'FALLBACK_WORKBENCH_URL = "http' not in client_js:
        problems.append("client.js does not carry a rendered workbench URL")
    spec_raw = rendered.get(SERVICE_FILE)
    if spec_raw is None:
        problems.append("service.json is missing: the host half cannot start the dashboard")
    else:
        try:
            spec = json.loads(spec_raw)
        except json.JSONDecodeError as exc:
            problems.append(f"service.json is not valid JSON: {exc}")
        else:
            for key in ("repoRoot", "python", "port", "logFile", "missionsDb", "pidFile"):
                if key not in spec:
                    problems.append(f"service.json is missing {key!r}")
            port = spec.get("port")
            if not isinstance(port, int) or not 0 < port <= 65535:
                problems.append("service.json port must be a valid TCP port")
            for key in ("repoRoot", "python", "logFile", "missionsDb", "pidFile"):
                value = spec.get(key)
                if isinstance(value, str) and value != "" and "\\" in value:
                    problems.append(f"service.json {key!r} must use forward slashes")
    return problems


def _profile_patch_conflict(profile_dir: Path) -> str | None:
    """Detect a hand-written row that would duplicate this bundle's row id."""
    patch = profile_dir / "cordis.patch.yml"
    if not patch.is_file():
        return None
    text = patch.read_text(encoding="utf-8")
    pattern = rf"(?m)^\s*-\s*id:\s*['\"]?{re.escape(PANEL_ROW_ID)}['\"]?\s*(?:#.*)?$"
    if re.search(pattern, text):
        return (
            f"profile cordis.patch.yml already declares a row with id {PANEL_ROW_ID!r}; "
            "remove or rename it before installing (this bundle mounts that row id itself)"
        )
    return None


def check_installation(
    repo_root: Path,
    profile_dir: Path,
    workbench_url: str = DEFAULT_WORKBENCH_URL,
    *,
    python: Path | str | None = None,
    missions_db: Path | str | None = None,
    log_file: Path | str | None = None,
    runtime_dir: Path | None = None,
) -> dict[str, Any]:
    """Report what apply would do, without writing anything."""
    repo_root = repo_root.resolve()
    profile_dir = profile_dir.resolve()
    errors: list[str] = []
    warnings: list[str] = []
    source = _repo_bundle(repo_root)
    for name in TEMPLATE_FILES:
        if not (source / name).is_file():
            errors.append(f"bundle template missing: {source / name}")
    if not profile_dir.is_dir():
        errors.append(f"dsh profile not found: {profile_dir}")
    try:
        rendered = (
            rendered_bundle(
                repo_root,
                workbench_url,
                python=python,
                missions_db=missions_db,
                log_file=log_file,
                runtime_dir=runtime_dir,
            )
            if not errors
            else {}
        )
    except (OSError, ValueError) as exc:
        errors.append(f"cannot render bundle: {exc}")
        rendered = {}
    if rendered:
        errors.extend(_contract_problems(repo_root, rendered))
        for name in TEMPLATE_FILES:
            if name in rendered and _USER_PATH.search(rendered[name]):
                errors.append(f"machine absolute user path found in rendered template: {name}")
        if not (profile_dir / "package.json").is_file():
            warnings.append("profile package.json is missing; apply will create a minimal one")
        else:
            try:
                _package(profile_dir)
            except json.JSONDecodeError as exc:
                errors.append(f"profile package.json is not valid JSON: {exc}")
        conflict = _profile_patch_conflict(profile_dir) if profile_dir.is_dir() else None
        if conflict:
            errors.append(conflict)
        target = _bundle_dir(profile_dir)
        if target.is_dir():
            stale = [
                name
                for name, text in rendered.items()
                if not (target / name).is_file() or (target / name).read_text(encoding="utf-8") != text
            ]
            if stale:
                warnings.append("bundle files will be updated: " + ", ".join(sorted(stale)))
        else:
            warnings.append(f"bundle will be installed: {target}")
        if not _link_matches(profile_dir):
            warnings.append(f"junction will be created: {_link_path(profile_dir)}")
        if BUNDLE_NAME not in _bundles(profile_dir):
            warnings.append(f"profile bundle list will include: {BUNDLE_NAME}")
        dependencies = _package(profile_dir).get("dependencies") or {}
        if dependencies.get(BUNDLE_NAME) != f"link:{BUNDLE_DIR_NAME}":
            warnings.append(f"profile dependency will be set: {BUNDLE_NAME}")
    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "bundle": str(_bundle_dir(profile_dir)),
        "profile": str(profile_dir),
        "workbench_url": normalize_workbench_url(workbench_url) if not errors else workbench_url,
        "installed": (_manifest_path(profile_dir)).is_file(),
        "service": json.loads(rendered[SERVICE_FILE]) if rendered else None,
    }


def _write_bundle(profile_dir: Path, rendered: dict[str, str]) -> bool:
    target = _bundle_dir(profile_dir)
    target.mkdir(parents=True, exist_ok=True)
    changed = False
    for name, text in rendered.items():
        path = target / name
        if path.is_file() and path.read_text(encoding="utf-8") == text:
            continue
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)
        changed = True
    return changed


def _ensure_link(profile_dir: Path) -> bool:
    link = _link_path(profile_dir)
    target = _bundle_dir(profile_dir)
    if _link_matches(profile_dir):
        return False
    link.parent.mkdir(parents=True, exist_ok=True)
    if link.exists() or link.is_symlink():
        _remove_link(link)
    if os.name == "nt":
        result = subprocess.run(f'cmd /c mklink /J "{link}" "{target}"', capture_output=True, text=True)
        if result.returncode != 0:
            raise OSError(result.stderr.strip() or result.stdout.strip())
    else:
        link.symlink_to(target, target_is_directory=True)
    return True


def _remove_link(link: Path) -> bool:
    """Remove the junction/symlink without following it into the bundle."""
    if not (link.exists() or link.is_symlink()):
        return False
    try:
        if link.is_symlink():
            link.unlink()
        elif os.name == "nt":
            os.rmdir(link)
        else:
            link.unlink()
        return True
    except OSError:
        result = subprocess.run(f'cmd /c rmdir "{link}"', capture_output=True, text=True)
        if result.returncode != 0:
            raise OSError(result.stderr.strip() or result.stdout.strip())
        return True


def _write_manifest(profile_dir: Path, payload: dict[str, Any]) -> None:
    path = _manifest_path(profile_dir)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _read_manifest(profile_dir: Path) -> dict[str, Any] | None:
    path = _manifest_path(profile_dir)
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _register(profile_dir: Path, snapshot: bool) -> tuple[bool, Path | None]:
    """Add this bundle to the profile dependency list and bundle layer list."""
    path = profile_dir / "package.json"
    original_text = path.read_text(encoding="utf-8") if path.is_file() else "{}\n"
    package = _package(profile_dir)
    dependencies = dict(package.get("dependencies") or {})
    dependencies[BUNDLE_NAME] = f"link:{BUNDLE_DIR_NAME}"
    package["dependencies"] = dependencies
    dsh = dict(package.get("dsh") or {})
    profile = dict(dsh.get("profile") or {})
    bundles = [item for item in (profile.get("bundles") or []) if isinstance(item, str)]
    if BUNDLE_NAME not in bundles:
        bundles.append(BUNDLE_NAME)
    profile["bundles"] = bundles
    dsh["profile"] = profile
    package["dsh"] = dsh
    updated = json.dumps(package, ensure_ascii=False, indent=2) + "\n"
    if updated == original_text:
        return False, None
    backup: Path | None = None
    if path.is_file() and snapshot:
        backup = path.with_name(f"{path.name}.bak-{BACKUP_TAG}-{_stamp()}")
        shutil.copy2(path, backup)
    path.write_text(updated, encoding="utf-8")
    return True, backup


def apply_installation(
    repo_root: Path,
    profile_dir: Path,
    workbench_url: str = DEFAULT_WORKBENCH_URL,
    *,
    python: Path | str | None = None,
    missions_db: Path | str | None = None,
    log_file: Path | str | None = None,
    runtime_dir: Path | None = None,
) -> dict[str, Any]:
    """Install or update the bundle, its junction and the profile registration."""
    repo_root = repo_root.resolve()
    profile_dir = profile_dir.resolve()
    options = {
        "python": python,
        "missions_db": missions_db,
        "log_file": log_file,
        "runtime_dir": runtime_dir,
    }
    report = check_installation(repo_root, profile_dir, workbench_url, **options)
    if not report["ok"]:
        return {**report, "applied": False, "changed": False}
    rendered = rendered_bundle(repo_root, workbench_url, **options)
    existing = _read_manifest(profile_dir)
    changed = _write_bundle(profile_dir, rendered)
    changed = _ensure_link(profile_dir) or changed
    registered, backup = _register(profile_dir, snapshot=True)
    changed = registered or changed
    previous = existing if isinstance(existing, dict) else {}
    payload: dict[str, Any] = {
        "bundle_name": BUNDLE_NAME,
        "bundle_dir": BUNDLE_DIR_NAME,
        "link": str(_link_path(profile_dir)),
        "repo_root": str(repo_root),
        "workbench_url": normalize_workbench_url(workbench_url),
        "files": sorted(rendered),
        # Keep the first install time so a re-run stays byte-identical.
        "installed_at": previous.get("installed_at") or _stamp(),
    }
    backup_name = previous.get("package_backup") if isinstance(previous.get("package_backup"), str) else None
    if backup is not None:
        backup_name = backup.name
    if backup_name is not None:
        payload["package_backup"] = backup_name
    if previous != payload:
        _write_manifest(profile_dir, payload)
        changed = True
    return {
        **report,
        "applied": changed,
        "changed": changed,
        "unchanged": not changed,
        "restart_required": changed,
        "package_backup": payload.get("package_backup"),
    }


def _latest_package_backup(profile_dir: Path) -> Path | None:
    candidates = sorted(profile_dir.glob(f"package.json.bak-{BACKUP_TAG}-*"))
    return candidates[-1] if candidates else None


def rollback_installation(repo_root: Path, profile_dir: Path) -> dict[str, Any]:
    """Remove every artifact this tool created and restore package.json."""
    repo_root = repo_root.resolve()
    profile_dir = profile_dir.resolve()
    manifest = _read_manifest(profile_dir)
    removed: list[str] = []
    restored: list[str] = []
    notes: list[str] = []

    package_path = profile_dir / "package.json"
    backup: Path | None = None
    if isinstance(manifest, dict) and isinstance(manifest.get("package_backup"), str):
        candidate = profile_dir / str(manifest["package_backup"])
        if candidate.is_file():
            backup = candidate
    if backup is None:
        backup = _latest_package_backup(profile_dir)

    if backup is not None and backup.is_file():
        shutil.copy2(backup, package_path)
        restored.append(f"package.json <- {backup.name}")
    elif package_path.is_file() and _has_bundle_entries(profile_dir):
        # No snapshot available: remove just our entries and keep everything else.
        package = _package(profile_dir)
        dependencies = dict(package.get("dependencies") or {})
        dependencies.pop(BUNDLE_NAME, None)
        package["dependencies"] = dependencies
        dsh = dict(package.get("dsh") or {})
        profile = dict(dsh.get("profile") or {})
        bundles = [item for item in (profile.get("bundles") or []) if item != BUNDLE_NAME]
        profile["bundles"] = bundles
        dsh["profile"] = profile
        package["dsh"] = dsh
        package_path.write_text(json.dumps(package, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        restored.append("package.json (surgical: bundle entry removed)")
        notes.append("no package.json snapshot was recorded; removed only this bundle's entries")

    link = _link_path(profile_dir)
    if _remove_link(link):
        removed.append(str(link))

    bundle_dir = _bundle_dir(profile_dir)
    if bundle_dir.is_dir():
        shutil.rmtree(bundle_dir)
        removed.append(str(bundle_dir))

    manifest_path = _manifest_path(profile_dir)
    if manifest_path.is_file():
        manifest_path.unlink()
        removed.append(str(manifest_path))

    if not removed and not restored:
        notes.append("nothing to roll back: no bundle artifacts found")
    still_listed = BUNDLE_NAME in _bundles(profile_dir) if package_path.is_file() else False
    return {
        "ok": not still_listed,
        "rolled_back": bool(removed or restored),
        "removed": removed,
        "restored": restored,
        "notes": notes,
        "restart_required": bool(removed or restored),
        "errors": [] if not still_listed else [f"{BUNDLE_NAME} is still listed in package.json"],
        "profile": str(profile_dir),
        "repo": str(repo_root),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("check", "apply", "rollback"))
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--dsh-profile", type=Path, default=None, help="defaults to ~/.dsh/profiles/desktop")
    parser.add_argument("--workbench-url", default=DEFAULT_WORKBENCH_URL)
    parser.add_argument(
        "--python",
        type=Path,
        default=None,
        help="interpreter that runs the dashboard; defaults to <repo>/.venv",
    )
    parser.add_argument(
        "--missions-db",
        type=Path,
        default=None,
        help="Mission SQLite database the workbench reads; defaults under ~/.dsh",
    )
    parser.add_argument("--log-file", type=Path, default=None, help="dashboard stdout/stderr log")
    parser.add_argument(
        "--runtime-dir",
        type=Path,
        default=None,
        help="directory for missions.db/dashboard.log/dashboard.pid (defaults to ~/.dsh/ai-hfss-workstation)",
    )
    args = parser.parse_args(argv)
    profile = args.dsh_profile or Path.home() / ".dsh" / "profiles" / "desktop"
    options = {
        "python": args.python,
        "missions_db": args.missions_db,
        "log_file": args.log_file,
        "runtime_dir": args.runtime_dir,
    }
    if args.command == "check":
        result = check_installation(args.repo_root, profile, args.workbench_url, **options)
    elif args.command == "apply":
        result = apply_installation(args.repo_root, profile, args.workbench_url, **options)
    else:
        result = rollback_installation(args.repo_root, profile)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
