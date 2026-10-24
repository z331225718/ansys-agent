#!/usr/bin/env python
"""Idempotent installer for reviewed/explore Ansys DSH presets.

The installation owns exactly one marked block in cordis.patch.yml. It never
parses and rewrites the whole profile, because DSH patch files may contain
!!js expressions that a normal YAML parser cannot round-trip.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BEGIN = "# >>> ansys-agent-dsh managed block v1 >>>"
END = "# <<< ansys-agent-dsh managed block v1 <<<"
MANAGED_IDS = (
    "mcp-ansys-assistant-reviewed",
    "preset-ansys-reviewed",
    "preset-ansys-explore",
)
CONFLICT_IDS = (*MANAGED_IDS, "preset-ansys-engineering")


def _quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _preset_persona(reviewed: bool) -> str:
    if reviewed:
        return (
            "你是 Ansys Reviewed 只读观察 Agent。只能通过 ansys-assistant reviewed MCP 读取 "
            "workflow catalog、workflow 定义与 graph/status，以及通过 ansys-api-memory 查询 API。"
            "不得启动、推进或批准 Mission/Graph；不得 preview/apply 几何或材料；不得运行 shell、"
            "裸 Python、PTC、文件写或任何 AEDT 修改。生产启动、advance、恢复与 human_gate 审批"
            "只能由受控本地 CLI/dashboard 执行。缺少数据时明确说明不可用，不伪造审批证据。"
        )
    return (
        "你是 Ansys Explore 探索 Agent。可以查询 API、使用官方 PyAEDT-MCP 和受控领域工具进行"
        "非生产探索，也可以使用 PTC 编排。所有探索结果都不是生产审批证据，不得声称已通过"
        "human_gate 或可用于生产；生产修改必须回到 reviewed/受控 Harness。"
    )


def _reviewed_plugins(plugin_path: Path) -> str:
    return f"""          - id: persona
            name: '@deepseek-ai/dsh-persona'
            config:
              prefix: |-
                {_preset_persona(True)}
          - id: agent-instructions
            name: '@deepseek-ai/dsh-agent-instructions'
            config:
              maxBytes: 65536
          - id: reviewed-tools
            name: {_quote(str(plugin_path))}
          - id: tool-ask-user
            name: '@deepseek-ai/dsh-tool-ask-user'
"""


def _explore_plugins(repo_root: Path) -> str:
    skills = repo_root / "integrations" / "dsh" / "agent-presets" / "ansys-engineering" / "skills"
    return f"""          - id: persona
            name: '@deepseek-ai/dsh-persona'
            config:
              prefix: |-
                {_preset_persona(False)}
          - id: agent-instructions
            name: '@deepseek-ai/dsh-agent-instructions'
            config:
              maxBytes: 65536
          - id: tool-bash
            name: '@deepseek-ai/dsh-tool-bash'
            disabled: !!js process.platform === 'win32'
          - id: tool-pwsh
            name: '@deepseek-ai/dsh-tool-pwsh'
            disabled: !!js process.platform !== 'win32'
          - id: tool-fs
            name: '@deepseek-ai/dsh-tool-fs'
          - id: tool-fs-search
            name: '@deepseek-ai/dsh-tool-fs-search'
            config:
              sampleOverCapGlobResults: false
          - id: tool-jobs
            name: '@deepseek-ai/dsh-tool-jobs'
          - id: skill-filesystem
            name: '@deepseek-ai/dsh-skill-filesystem'
            config:
              customSkillDirs:
                - {_quote(str(skills))}
          - id: tool-skill
            name: '@deepseek-ai/dsh-tool-skill'
          - id: tool-todo
            name: '@deepseek-ai/dsh-tool-todo'
          - id: tool-web
            name: '@deepseek-ai/dsh-tool-web'
            config:
              fetch: true
              searchTimeoutMs: 60000
          - id: tool-presentation
            name: '@deepseek-ai/dsh-agent-tool-presentation'
            config:
              mode: ptc
          - id: present
            name: '@deepseek-ai/dsh-tool-present'
"""


def build_block(repo_root: Path, profile_dir: Path, python: Path, workflow_db: Path) -> str:
    repo = _quote(str(repo_root))
    py = _quote(str(python))
    db = _quote(str(workflow_db))
    plugin_path = profile_dir / "ansys-reviewed-tools.mjs"
    return f"""{BEGIN}
- insert:
    - id: mcp-ansys-assistant-reviewed
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        serverName: ansys_assistant_reviewed
        transport: stdio
        command: {py}
        args:
          - '-m'
          - 'aedt_agent.interactive.server'
          - '--profile'
          - 'reviewed'
          - '--workflow-db'
          - {db}
        cwd: {repo}
        toolCallTimeoutMs: 300000
        reconnect:
          enabled: true
          maxAttempts: 10
- insert:
    - id: preset-ansys-reviewed
      name: '@deepseek-ai/dsh-agent-preset'
      config:
        id: ansys-reviewed
        name: Ansys Reviewed 只读观察
        description: 只读观察真实 Mission/Graph/报告；无官方 PyAEDT-MCP、shell、PTC 或写入口。
        order: 11
        plugins:
{_reviewed_plugins(plugin_path)}    - id: preset-ansys-explore
      name: '@deepseek-ai/dsh-agent-preset'
      config:
        id: ansys-explore
        name: Ansys Explore 探索
        description: 非生产探索；结果不构成生产审批证据。
        order: 12
        plugins:
{_explore_plugins(repo_root)}{END}
"""


def _install_plugin(repo_root: Path, profile_dir: Path) -> None:
    source = repo_root / "integrations" / "dsh" / "agent-presets" / "ansys-reviewed" / "reviewed-tools.mjs"
    target = profile_dir / "ansys-reviewed-tools.mjs"
    text = source.read_text(encoding="utf-8")
    if target.exists() and target.read_text(encoding="utf-8") == text:
        return
    if target.exists():
        backup = target.with_suffix(target.suffix + f".bak-{_stamp()}")
        shutil.copy2(target, backup)
    target.write_text(text, encoding="utf-8")


MANIFEST_NAME = "ansys-dsh-install.json"


def _manifest(profile_dir: Path) -> dict[str, Any] | None:
    path = profile_dir / MANIFEST_NAME
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _contains_entry_id(text: str, entry_id: str) -> bool:
    pattern = rf"(?m)^\s*-\s*id:\s*[\'\"]?{re.escape(entry_id)}[\'\"]?\s*(?:#.*)?$"
    return re.search(pattern, text) is not None


def _block_span(text: str) -> tuple[int, int] | None:
    begin = text.count(BEGIN)
    end = text.count(END)
    if begin == 0 and end == 0:
        return None
    if begin != 1 or end != 1:
        raise ValueError("managed block markers are partial or duplicated")
    start = text.index(BEGIN)
    stop = text.index(END, start) + len(END)
    return start, stop


def check_installation(repo_root: Path, profile_dir: Path, python: Path, workflow_db: Path) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    profile_dir = profile_dir.resolve()
    python = python.resolve()
    workflow_db = workflow_db.resolve()
    errors: list[str] = []
    warnings: list[str] = []
    cordis = profile_dir / "cordis.patch.yml"
    for label, path in (("repo_root", repo_root), ("profile_dir", profile_dir), ("python", python)):
        if not path.exists():
            errors.append(f"{label} does not exist: {path}")
    for template in (
        repo_root / "integrations" / "dsh" / "agent-presets" / "ansys-reviewed" / "reviewed-tools.mjs",
        repo_root / "integrations" / "dsh" / "agent-presets" / "ansys-reviewed" / "preset.yml",
        repo_root / "integrations" / "dsh" / "agent-presets" / "ansys-explore" / "preset.yml",
    ):
        if not template.is_file():
            errors.append(f"preset asset missing: {template}")
    if not workflow_db.is_absolute():
        errors.append(f"workflow_db must be an absolute path: {workflow_db}")
    else:
        manifest = _manifest(profile_dir)
        installed_db = str(manifest.get("workflow_db") or "") if manifest else ""
        if installed_db and Path(installed_db).resolve() != workflow_db:
            errors.append(f"workflow_db differs from installed reviewed DB: installed={installed_db} requested={workflow_db}")
        if not workflow_db.exists():
            warnings.append(f"workflow_db does not exist yet: {workflow_db}")
        else:
            try:
                with sqlite3.connect(workflow_db) as db:
                    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
                required = {"missions", "graph_runs"}
                if tables and not required <= tables:
                    errors.append(f"workflow_db is not an ansys-agent mission database: missing {sorted(required - tables)}")
                elif not tables:
                    warnings.append("workflow_db is empty; initialize it with the local CLI/dashboard before use")
            except sqlite3.DatabaseError as exc:
                errors.append(f"workflow_db is not a valid SQLite database: {exc}")
    if not cordis.is_file():
        errors.append(f"cordis.patch.yml not found: {cordis}")
        text = ""
    else:
        text = cordis.read_text(encoding="utf-8")
    block_exists = False
    try:
        span = _block_span(text)
        block_exists = span is not None
        outside = text if span is None else text[: span[0]] + text[span[1] :]
        for managed_id in CONFLICT_IDS:
            if _contains_entry_id(outside, managed_id):
                if managed_id == "preset-ansys-engineering":
                    errors.append(
                        "legacy unmarked preset-ansys-engineering exists; migrate it to ansys-explore "
                        "manually before installing (unmarked entries are never overwritten)"
                    )
                else:
                    errors.append(f"conflicting unmarked entry exists: {managed_id}")
    except ValueError as exc:
        errors.append(str(exc))
    for template in (
        repo_root / "integrations" / "dsh" / "agent-presets",
        repo_root / "integrations" / "dsh" / "agent-presets" / "ansys-reviewed",
    ):
        if template.exists():
            for path in template.rglob("*"):
                if path.is_file() and "C:\\Users\\" in path.read_text(encoding="utf-8", errors="ignore"):
                    errors.append(f"machine absolute user path found in template: {path}")
    if python.exists():
        probe = subprocess.run(
            [str(python), "-c", "import fastmcp, aedt_agent.interactive.server"],
            cwd=repo_root,
            capture_output=True,
            text=True,
        )
        if probe.returncode != 0:
            errors.append("python runtime cannot import fastmcp/aedt_agent.interactive.server: " + (probe.stderr.strip() or probe.stdout.strip())[-500:])
    else:
        warnings.append("python executable missing; runtime import check skipped")
    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "block_exists": block_exists,
        "cordis": str(cordis),
        "would_manage_ids": list(MANAGED_IDS),
    }


def apply_installation(repo_root: Path, profile_dir: Path, python: Path, workflow_db: Path) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    profile_dir = profile_dir.resolve()
    python = python.resolve()
    workflow_db = workflow_db.resolve()
    report = check_installation(repo_root, profile_dir, python, workflow_db)
    if not report["ok"]:
        return {**report, "applied": False}
    cordis = profile_dir / "cordis.patch.yml"
    original = cordis.read_text(encoding="utf-8")
    block = build_block(repo_root, profile_dir, python, workflow_db)
    span = _block_span(original)
    plugin_target = profile_dir / "ansys-reviewed-tools.mjs"
    plugin_source = repo_root / "integrations" / "dsh" / "agent-presets" / "ansys-reviewed" / "reviewed-tools.mjs"
    manifest = _manifest(profile_dir) or {}
    unchanged = (
        span is not None
        and original[span[0] : span[1]].strip() == block.strip()
        and plugin_target.is_file()
        and plugin_target.read_text(encoding="utf-8") == plugin_source.read_text(encoding="utf-8")
        and str(manifest.get("workflow_db") or "") == str(workflow_db)
    )
    if unchanged:
        return {**report, "applied": False, "unchanged": True, "backup": None, "restart_required": False}
    if span is None:
        updated = original.rstrip() + "\n\n" + block + "\n"
    else:
        updated = original[: span[0]] + block + original[span[1] :]
    backup = cordis.with_name(cordis.name + f".bak-ansys-agent-{_stamp()}")
    shutil.copy2(cordis, backup)
    _install_plugin(repo_root, profile_dir)
    (profile_dir / MANIFEST_NAME).write_text(json.dumps({"workflow_db": str(workflow_db), "repo_root": str(repo_root), "python": str(python)}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp = cordis.with_suffix(cordis.suffix + ".ansys-agent.tmp")
    tmp.write_text(updated, encoding="utf-8")
    tmp.replace(cordis)
    return {**report, "applied": True, "backup": str(backup), "restart_required": True}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check", "apply"))
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--dsh-profile", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--workflow-db", type=Path, required=True)
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
