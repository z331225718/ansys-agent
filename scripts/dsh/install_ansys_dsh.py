#!/usr/bin/env python
"""Install the single Ansys engineering DSH preset bundle.

The mode is intentionally one: full official/domain MCPs plus PTC, with an
execution guard that keeps production Mission start/advance/approval in the
controlled local CLI/dashboard.
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

BUNDLE_NAME = "@local/dsh-ansys-presets"
BUNDLE_DIR_NAME = "ansys-agent-presets-bundle"
MANIFEST_NAME = "ansys-dsh-install.json"
BEGIN = "# >>> ansys-agent-dsh managed block v1 >>>"
END = "# <<< ansys-agent-dsh managed block v1 <<<"
TEMPLATE_FILES = ("package.json", "cordis.patch.yml", "production-guard.mjs")


def _quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _repo_bundle(repo_root: Path) -> Path:
    return repo_root / "integrations" / "dsh" / "bundles" / "ansys-engineering"


def rendered_bundle(repo_root: Path) -> dict[str, str]:
    source = _repo_bundle(repo_root)
    rendered: dict[str, str] = {}
    repo_posix = repo_root.resolve().as_posix()
    for name in TEMPLATE_FILES:
        text = (source / name).read_text(encoding="utf-8")
        rendered[name] = text.replace("__REPO_ROOT__", repo_posix)
    return rendered


def _contains_entry_id(text: str, entry_id: str) -> bool:
    pattern = rf"(?m)^\s*-\s*id:\s*[\'\"]?{re.escape(entry_id)}[\'\"]?\s*(?:#.*)?$"
    return re.search(pattern, text) is not None


def _managed_span(text: str) -> tuple[int, int] | None:
    begin = text.count(BEGIN)
    end = text.count(END)
    if begin == 0 and end == 0:
        return None
    if begin != 1 or end != 1:
        raise ValueError("managed block markers are partial or duplicated")
    start = text.index(BEGIN)
    stop = text.index(END, start) + len(END)
    return start, stop


def _package(profile_dir: Path) -> dict[str, Any]:
    path = profile_dir / "package.json"
    if not path.is_file():
        return {"name": "dsh-profile", "private": True, "dependencies": {}, "dsh": {"profile": {"bundles": []}}}
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else {}


def _bundle_dir(profile_dir: Path) -> Path:
    return profile_dir / BUNDLE_DIR_NAME


def _link_path(profile_dir: Path) -> Path:
    return profile_dir / "node_modules" / "@local" / "dsh-ansys-presets"


def _link_matches(profile_dir: Path) -> bool:
    link = _link_path(profile_dir)
    if not link.exists():
        return False
    try:
        return link.resolve() == _bundle_dir(profile_dir).resolve()
    except OSError:
        return False


def check_installation(repo_root: Path, profile_dir: Path, python: Path, workflow_db: Path | None = None) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    profile_dir = profile_dir.resolve()
    errors: list[str] = []
    warnings: list[str] = []
    cordis = profile_dir / "cordis.patch.yml"
    source = _repo_bundle(repo_root)
    for name in TEMPLATE_FILES:
        if not (source / name).is_file():
            errors.append(f"bundle template missing: {source / name}")
    if not profile_dir.is_dir():
        errors.append(f"dsh profile not found: {profile_dir}")
    if not cordis.is_file():
        errors.append(f"cordis.patch.yml not found: {cordis}")
    text = cordis.read_text(encoding="utf-8") if cordis.is_file() else ""
    try:
        span = _managed_span(text)
        outside = text if span is None else text[: span[0]] + text[span[1] :]
        if _contains_entry_id(outside, "preset-ansys-engineering"):
            errors.append(
                "unmarked preset-ansys-engineering exists; remove or migrate it manually before installing "
                "(the installer never overwrites unmarked entries)"
            )
    except ValueError as exc:
        errors.append(str(exc))
    for path in source.rglob("*"):
        if path.is_file() and "C:\\Users\\" in path.read_text(encoding="utf-8", errors="ignore"):
            errors.append(f"machine absolute user path found in template: {path}")
    if workflow_db is not None:
        warnings.append("--workflow-db is deprecated for the single-mode preset; the global ansys-assistant MCP owns its DB")
    try:
        rendered = rendered_bundle(repo_root)
    except OSError as exc:
        errors.append(f"cannot render bundle: {exc}")
        rendered = {}
    target = _bundle_dir(profile_dir)
    if target.is_dir():
        for name, content in rendered.items():
            path = target / name
            if not path.is_file() or path.read_text(encoding="utf-8") != content:
                warnings.append(f"bundle file will be updated: {name}")
    else:
        warnings.append(f"bundle will be installed: {target}")
    if not _link_matches(profile_dir):
        warnings.append("bundle link will be created in node_modules/@local")
    package = _package(profile_dir)
    bundles = list((package.get("dsh") or {}).get("profile", {}).get("bundles") or [])
    if BUNDLE_NAME not in bundles:
        warnings.append(f"profile bundle list will include: {BUNDLE_NAME}")
    dependencies = package.get("dependencies") or {}
    if dependencies.get(BUNDLE_NAME) != f"link:{BUNDLE_DIR_NAME}":
        warnings.append(f"profile dependency will be set: {BUNDLE_NAME}")
    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "unsupported_mode": "ansys-reviewed/ansys-explore were retired in favor of one mode",
        "bundle": str(target),
        "profile": str(profile_dir),
    }


def _write_bundle(repo_root: Path, profile_dir: Path) -> bool:
    target = _bundle_dir(profile_dir)
    rendered = rendered_bundle(repo_root)
    changed = False
    target.mkdir(parents=True, exist_ok=True)
    for name, content in rendered.items():
        path = target / name
        if path.is_file() and path.read_text(encoding="utf-8") == content:
            continue
        if path.is_file():
            shutil.copy2(path, path.with_suffix(path.suffix + f".bak-{_stamp()}"))
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(content, encoding="utf-8")
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
        if link.is_dir() and not link.is_symlink():
            shutil.rmtree(link)
        else:
            link.unlink()
    if os.name == "nt":
        result = subprocess.run(
            f'cmd /c mklink /J "{link}" "{target}"',
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise OSError(result.stderr.strip() or result.stdout.strip())
    else:
        link.symlink_to(target, target_is_directory=True)
    return True


def _register_bundle(profile_dir: Path) -> bool:
    path = profile_dir / "package.json"
    original = path.read_text(encoding="utf-8") if path.is_file() else "{}\n"
    package = _package(profile_dir)
    dependencies = dict(package.get("dependencies") or {})
    dependencies[BUNDLE_NAME] = f"link:{BUNDLE_DIR_NAME}"
    package["dependencies"] = dependencies
    dsh = dict(package.get("dsh") or {})
    profile = dict(dsh.get("profile") or {})
    bundles = list(profile.get("bundles") or [])
    if BUNDLE_NAME not in bundles:
        bundles.append(BUNDLE_NAME)
    profile["bundles"] = bundles
    dsh["profile"] = profile
    package["dsh"] = dsh
    updated = json.dumps(package, ensure_ascii=False, indent=2) + "\n"
    if updated == original:
        return False
    if path.is_file():
        shutil.copy2(path, path.with_name(path.name + f".bak-ansys-agent-{_stamp()}"))
    path.write_text(updated, encoding="utf-8")
    return True


def apply_installation(repo_root: Path, profile_dir: Path, python: Path, workflow_db: Path | None = None) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    profile_dir = profile_dir.resolve()
    report = check_installation(repo_root, profile_dir, python, workflow_db)
    if not report["ok"]:
        return {**report, "applied": False, "changed": False}
    cordis = profile_dir / "cordis.patch.yml"
    text = cordis.read_text(encoding="utf-8")
    span = _managed_span(text)
    changed = False
    if span is not None:
        shutil.copy2(cordis, cordis.with_name(cordis.name + f".bak-ansys-agent-{_stamp()}"))
        cordis.write_text((text[: span[0]] + text[span[1] :]).strip() + "\n", encoding="utf-8")
        changed = True
    changed = _write_bundle(repo_root, profile_dir) or changed
    changed = _ensure_link(profile_dir) or changed
    changed = _register_bundle(profile_dir) or changed
    manifest = {"workflow_db": None, "repo_root": str(repo_root), "python": str(python), "mode": "single"}
    manifest_path = profile_dir / MANIFEST_NAME
    manifest_text = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    if not manifest_path.is_file() or manifest_path.read_text(encoding="utf-8") != manifest_text:
        manifest_path.write_text(manifest_text, encoding="utf-8")
        changed = True
    return {**report, "applied": changed, "unchanged": not changed, "restart_required": changed}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check", "apply"))
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--dsh-profile", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--workflow-db", type=Path, default=None, help="deprecated; ignored for the single-mode preset")
    args = parser.parse_args(argv)
    result = (
        check_installation(args.repo_root, args.dsh_profile, args.python, args.workflow_db)
        if args.command == "check"
        else apply_installation(args.repo_root, args.dsh_profile, args.python, args.workflow_db)
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
