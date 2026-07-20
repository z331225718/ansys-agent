from __future__ import annotations

import hashlib
import json
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from aedt_agent.interactive.workflows import AssistantWorkflowManager
from aedt_agent.live.approval import HmacApprovalAuthority
from aedt_agent.live.manager import LiveAedtSessionManager


def run_live_layout_audit_smoke(
    *,
    port: int,
    version: str,
    output_dir: str | Path,
    expected_project: str = "",
    expected_design: str = "",
    confirmed_read_only: bool = False,
    live_factory: Callable[..., Any] = LiveAedtSessionManager,
    workflow_factory: Callable[..., Any] = AssistantWorkflowManager,
) -> dict[str, Any]:
    if not confirmed_read_only:
        raise ValueError("--confirm-read-only is required before attaching to AEDT")
    if not 1 <= int(port) <= 65535:
        raise ValueError("port must be between 1 and 65535")
    root = Path(output_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    authority = HmacApprovalAuthority(secrets.token_urlsafe(32))
    live = live_factory(approval_verifier=authority)
    started_at = datetime.now(UTC).isoformat()
    opened = None
    try:
        opened = live.attach(port=int(port), version=version)
        session_id = opened["live_session_id"]
        info = live.project_info(session_id)
        _verify_target(info, expected_project=expected_project, expected_design=expected_design)
        workflows = workflow_factory(
            live_manager=live,
            db_path=root / "missions.db",
            template_ids=("layout_live_audit",),
        )
        preview = workflows.preview_start(
            session_id,
            workflow_id="layout_live_audit",
            goal="Read-only smoke audit of the attached HFSS 3D Layout design",
            initial_payload={},
            max_steps=8,
        )
        started = workflows.apply_start(
            session_id,
            preview_id=preview["preview_id"],
            approval_token=_issue(authority, preview),
        )
        report = workflows.status(started["graph_run_id"])
        for _ in range(8):
            if report["status"] in {"succeeded", "failed", "canceled"}:
                break
            step_preview = workflows.preview_advance(session_id, graph_run_id=started["graph_run_id"])
            report = workflows.apply_advance(
                session_id,
                preview_id=step_preview["preview_id"],
                approval_token=_issue(authority, step_preview),
            )
        scorecard = next(
            (
                item.get("output_payload")
                for item in reversed(report.get("node_runs", []))
                if item.get("node_id") == "audit_scorecard"
            ),
            {},
        )
        status = "passed" if report["status"] == "succeeded" and scorecard.get("status") == "passed" else "failed"
        release = live.release(session_id)
        opened = None
        evidence = {
            "schema": "ansys-assistant-live-smoke/v1",
            "status": status,
            "started_at": started_at,
            "finished_at": datetime.now(UTC).isoformat(),
            "target": {
                "port": int(port),
                "version": version,
                "active_project": info.get("active_project"),
                "active_design": info.get("active_design"),
                "design_type": info.get("design_type"),
            },
            "workflow_id": "layout_live_audit",
            "graph_run_id": started["graph_run_id"],
            "graph_status": report["status"],
            "scorecard": scorecard,
            "read_only": True,
            "project_saved": False,
            "release": {
                "released": bool(release.get("released", True)),
                "aedt_closed": bool(release.get("aedt_closed", False)),
                "projects_closed": bool(release.get("projects_closed", False)),
            },
        }
        _write_evidence(root, evidence)
        return {**evidence, "evidence_path": str(root / "live_layout_audit_smoke.json")}
    finally:
        if opened is not None:
            live.release(opened["live_session_id"])
        close = getattr(live, "close", None)
        if callable(close):
            close()


def run_live_width_preview_smoke(
    *,
    port: int,
    version: str,
    output_dir: str | Path,
    target_width: str,
    variable_name: str,
    variable_value: str,
    expected_project: str = "",
    expected_design: str = "",
    nets: list[str] | None = None,
    layers: list[str] | None = None,
    confirmed_preview_only: bool = False,
    live_factory: Callable[..., Any] = LiveAedtSessionManager,
    workflow_factory: Callable[..., Any] = AssistantWorkflowManager,
) -> dict[str, Any]:
    if not confirmed_preview_only:
        raise ValueError("--confirm-preview-only is required before attaching to AEDT")
    if not 1 <= int(port) <= 65535:
        raise ValueError("port must be between 1 and 65535")
    if not target_width or not variable_name or not variable_value:
        raise ValueError("target_width, variable_name, and variable_value are required")
    root = Path(output_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    authority = HmacApprovalAuthority(secrets.token_urlsafe(32))
    live = live_factory(approval_verifier=authority)
    opened = None
    started_at = datetime.now(UTC).isoformat()
    try:
        opened = live.attach(port=int(port), version=version)
        session_id = opened["live_session_id"]
        info = live.project_info(session_id)
        _verify_target(info, expected_project=expected_project, expected_design=expected_design)
        workflows = workflow_factory(
            live_manager=live,
            db_path=root / "missions.db",
            template_ids=("layout_live_parameterize_width",),
        )
        selector = {
            "target_width": target_width,
            "nets": list(nets or []),
            "layers": list(layers or []),
        }
        start_preview = workflows.preview_start(
            session_id,
            workflow_id="layout_live_parameterize_width",
            goal="Preview-only validation of live path width parameterization",
            initial_payload={
                "selector": selector,
                "variable_name": variable_name,
                "variable_value": variable_value,
            },
            max_steps=8,
        )
        started = workflows.apply_start(
            session_id,
            preview_id=start_preview["preview_id"],
            approval_token=_issue(authority, start_preview),
        )
        report = workflows.status(started["graph_run_id"])
        for _ in range(2):
            step = workflows.preview_advance(session_id, graph_run_id=started["graph_run_id"])
            report = workflows.apply_advance(
                session_id,
                preview_id=step["preview_id"],
                approval_token=_issue(authority, step),
            )
        node = report["node_runs"][-1]
        if node["node_id"] != "preview_parameterization" or node["status"] != "succeeded":
            raise RuntimeError("width preview workflow did not stop after preview_parameterization")
        output = dict(node["output_payload"])
        operation = dict(output.get("operation_preview") or {})
        if operation.get("project_dirty") is not False or not operation.get("approval_required"):
            raise RuntimeError("width operation preview did not prove a clean, approval-gated state")
        release = live.release(session_id)
        opened = None
        evidence = {
            "schema": "ansys-assistant-live-width-preview-smoke/v1",
            "status": "passed",
            "started_at": started_at,
            "finished_at": datetime.now(UTC).isoformat(),
            "target": {
                "port": int(port),
                "version": version,
                "active_project": info.get("active_project"),
                "active_design": info.get("active_design"),
                "design_type": info.get("design_type"),
            },
            "workflow_id": "layout_live_parameterize_width",
            "graph_run_id": started["graph_run_id"],
            "stopped_after_node": node["node_id"],
            "selector": selector,
            "variable_name": variable_name,
            "variable_value": variable_value,
            "target_count": int(operation.get("target_count") or 0),
            "operation_preview_id": output.get("operation_preview_id"),
            "approval_required": True,
            "apply_executed": False,
            "project_dirty": False,
            "project_saved": False,
            "release": {
                "released": bool(release.get("released", True)),
                "aedt_closed": bool(release.get("aedt_closed", False)),
                "projects_closed": bool(release.get("projects_closed", False)),
            },
        }
        _write_named_evidence(root, "live_width_preview_smoke.json", evidence)
        return {**evidence, "evidence_path": str(root / "live_width_preview_smoke.json")}
    finally:
        if opened is not None:
            live.release(opened["live_session_id"])
        close = getattr(live, "close", None)
        if callable(close):
            close()


def _verify_target(info: dict[str, Any], *, expected_project: str, expected_design: str) -> None:
    if info.get("design_type") != "HFSS 3D Layout Design":
        raise ValueError(f"active design is not HFSS 3D Layout: {info.get('design_type')}")
    if expected_project and info.get("active_project") != expected_project:
        raise ValueError("active project does not match --expected-project")
    if expected_design and info.get("active_design") != expected_design:
        raise ValueError("active design does not match --expected-design")


def _issue(authority: HmacApprovalAuthority, preview: dict[str, Any]) -> str:
    request = dict(preview["approval_request"])
    return authority.issue(
        action=request["action"],
        resource_id=request["resource_id"],
        digest=request["digest"],
    )


def _write_evidence(root: Path, evidence: dict[str, Any]) -> None:
    _write_named_evidence(root, "live_layout_audit_smoke.json", evidence)


def _write_named_evidence(root: Path, name: str, evidence: dict[str, Any]) -> None:
    path = root / name
    encoded = json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
    path.write_bytes(encoded)
    (root / f"{name}.sha256").write_text(
        hashlib.sha256(encoded).hexdigest() + "  " + path.name + "\n",
        encoding="ascii",
    )
