from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from aedt_agent.exploration.contracts import OperationPlan
from aedt_agent.exploration.validator import OperationValidator
from aedt_agent.interactive.kernel import InteractiveKernel
from aedt_agent.interactive.layout import LayoutSessionError
from aedt_agent.interactive.server import create_server


def _log(tool: str, arguments: dict[str, Any]) -> None:
    path = os.getenv("MCP_BENCH_LOG")
    if not path:
        return
    with Path(path).open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"tool": tool, "arguments": arguments}, ensure_ascii=False) + "\n")


class FakeLayoutManager:
    def __init__(self) -> None:
        self.scenario = os.getenv("MCP_BENCH_SCENARIO", "normal")
        self.sessions: dict[str, dict[str, Any]] = {}

    def open_session(
        self,
        project_path: str,
        *,
        writable: bool = False,
        workspace: str | None = None,
        version: str = "2026.1",
        edb_backend: str = "auto",
    ) -> dict[str, Any]:
        _log("open_layout_session", locals() | {"self": None})
        session_id = "layout-session-1"
        self.sessions[session_id] = {"writable": writable, "project_path": project_path}
        return {
            "session_id": session_id,
            "source_project_path": project_path,
            "working_project_path": (
                r"C:\bench-runs\board-working.aedb" if writable else r"C:\bench-runs\board-snapshot.aedb"
            ),
            "writable": writable,
            "source_unchanged": True,
            "version": version,
            "edb_backend": edb_backend,
        }

    def close_session(self, session_id: str) -> dict[str, Any]:
        _log("close_layout_session", {"session_id": session_id})
        self.sessions.pop(session_id, None)
        return {"session_id": session_id, "closed": True, "source_unchanged": True}

    def list_paths(self, session_id: str, selector: Any = None) -> dict[str, Any]:
        _log("list_layout_paths", {"session_id": session_id, "selector": selector.to_dict() if selector else {}})
        return {
            "session_id": session_id,
            "count": 1,
            "paths": [
                {
                    "primitive_id": "101",
                    "net": "N1",
                    "layer": "L1",
                    "width_m": 0.0001,
                    "width_expression": "0.1mm",
                    "is_parameterized": False,
                }
            ],
            "snapshot_digest": "inventory-digest",
        }

    def preview_parameterize_width(
        self,
        session_id: str,
        *,
        selector: Any,
        variable_name: str,
        variable_value: Any,
    ) -> dict[str, Any]:
        _log(
            "preview_parameterize_path_width",
            {
                "session_id": session_id,
                "selector": selector.to_dict(),
                "variable_name": variable_name,
                "variable_value": variable_value,
            },
        )
        return {
            "preview_id": "preview-1",
            "session_id": session_id,
            "target_count": 1,
            "targets": [{"primitive_id": "101", "net": "N1", "layer": "L1"}],
            "snapshot_digest": "preview-digest",
            "variable_name": variable_name,
            "variable_value": str(variable_value),
        }

    def apply_parameterize_width(self, session_id: str, preview_id: str) -> dict[str, Any]:
        _log("apply_parameterize_path_width", {"session_id": session_id, "preview_id": preview_id})
        if self.scenario == "backend_failure":
            raise LayoutSessionError("preview target digest is stale")
        return {
            "status": "verified",
            "session_id": session_id,
            "target_count": 1,
            "verified_count": 1,
            "working_project_path": r"C:\bench-runs\board-working.aedb",
            "after": [
                {
                    "primitive_id": "101",
                    "width_expression": "trace_w",
                    "is_parameterized": True,
                }
            ],
            "evidence": {
                "variable_is_parameter": True,
                "source_unchanged": True,
            },
        }


class FakeLiveManager:
    def __init__(self) -> None:
        self.scenario = os.getenv("MCP_BENCH_SCENARIO", "single_session")
        self.sessions: dict[str, dict[str, Any]] = {}
        self.analysis_running = False
        self.exploration_candidates: dict[str, dict[str, Any]] = {}
        self.exploration_validations: dict[str, dict[str, Any]] = {}
        self.exploration_previews: dict[str, dict[str, Any]] = {}
        self.issued_approvals: set[tuple[str, str]] = set()
        self.exploration_validator = OperationValidator(
            package_versions={"pyaedt": "1.0.1", "pyedb": "0.77.0"},
            evidence_verifier=_BenchmarkEvidenceVerifier(),
        )

    def list_sessions(self) -> dict[str, Any]:
        _log("list_live_aedt_sessions", {})
        sessions = [
            {"pid": 4201, "ports": [50061], "version": "2026.1", "executable": "ansysedt.exe"}
        ]
        if self.scenario == "two_sessions":
            sessions.append(
                {"pid": 4202, "ports": [50062], "version": "2026.1", "executable": "ansysedt.exe"}
            )
        return {"sessions": sessions, "selection_required": True}

    def launch(
        self,
        *,
        version: str = "2026.1",
        port: int = 0,
        install_dir: str | None = None,
        non_graphical: bool = False,
        timeout: float = 120.0,
    ) -> dict[str, Any]:
        _log(
            "launch_live_aedt_session",
            {
                "version": version,
                "port": port,
                "install_dir": install_dir,
                "non_graphical": non_graphical,
                "timeout": timeout,
            },
        )
        session_id = "live-session-launched"
        self.sessions[session_id] = {"pid": 4301, "port": port or 50071}
        return {
            "launched": True,
            "pid": 4301,
            "port": port or 50071,
            "version": version,
            "live_session_id": session_id,
            "owned_by_assistant": True,
            "release_required": True,
        }

    def attach(
        self,
        *,
        pid: int | None = None,
        port: int | None = None,
        version: str = "2026.1",
    ) -> dict[str, Any]:
        _log("attach_live_aedt_session", {"pid": pid, "port": port, "version": version})
        if pid is None and port is None:
            raise ValueError("pid or port is required")
        if pid is not None and port is not None and (pid, port) != (4201, 50061):
            raise ValueError("pid and port do not identify the same AEDT session")
        session_id = "live-session-1"
        self.sessions[session_id] = {"pid": pid or 4201, "port": port or 50061}
        return {
            "live_session_id": session_id,
            "target": {"kind": "port" if port is not None else "pid", "value": port or pid},
            "probe": {"connected": True, "pid": pid or 4201, "port": port or 50061},
            "reused_broker": True,
            "release_required": True,
            "release_tool": "release_live_aedt_session",
        }

    def release(self, session_id: str) -> dict[str, Any]:
        _log("release_live_aedt_session", {"live_session_id": session_id})
        self.sessions.pop(session_id, None)
        self.issued_approvals = {item for item in self.issued_approvals if item[0] != session_id}
        return {
            "live_session_id": session_id,
            "released": True,
            "aedt_closed": False,
            "projects_closed": False,
        }

    def project_info(self, session_id: str) -> dict[str, Any]:
        _log("get_live_aedt_project_info", {"live_session_id": session_id})
        self._require_session(session_id)
        return {
            "live_session_id": session_id,
            "project_names": ["BenchProject"],
            "active_project": "BenchProject",
            "active_design": "Layout1",
            "design_type": "HFSS 3D Layout Design",
        }

    def preview_project_save(self, session_id: str, *, project_name: str) -> dict[str, Any]:
        _log(
            "preview_live_project_save",
            {"live_session_id": session_id, "project_name": project_name},
        )
        self._require_session(session_id)
        return {
            "preview_id": "save-preview-1",
            "project_name": project_name,
            "snapshot_digest": "save-preview-digest",
            "approval_required": True,
            "approval_source": "external_host_only",
        }

    def apply_project_save(self, session_id: str, *, preview_id: str, approval_token: str) -> dict[str, Any]:
        _log(
            "apply_live_project_save",
            {"live_session_id": session_id, "preview_id": preview_id, "approval_token": approval_token},
        )
        self._consume_approval(session_id, preview_id, approval_token)
        return {"status": "verified", "preview_id": preview_id, "project_saved": True}

    def create_hfss_design(
        self,
        session_id: str,
        *,
        project_name: str,
        design_name: str,
        solution_type: str = "DrivenModal",
    ) -> dict[str, Any]:
        _log(
            "create_live_hfss_design",
            {
                "live_session_id": session_id,
                "project_name": project_name,
                "design_name": design_name,
                "solution_type": solution_type,
            },
        )
        self._require_session(session_id)
        return {"created_or_activated": True, "project_name": project_name, "design_name": design_name}

    def hfss_design_inventory(
        self,
        session_id: str,
        *,
        project_name: str,
        design_name: str,
    ) -> dict[str, Any]:
        _log(
            "get_live_hfss_design_inventory",
            {
                "live_session_id": session_id,
                "project_name": project_name,
                "design_name": design_name,
            },
        )
        self._require_session(session_id)
        return {
            "project_name": project_name,
            "design_name": design_name,
            "solution_type": "DrivenModal",
            "setups": ["Setup1"],
            "ports": ["P1", "P2"],
            "boundaries": [{"name": "rad1", "type": "Radiation"}],
            "reports": ["S Parameters"],
        }

    def hfss_geometry_inventory(
        self,
        session_id: str,
        *,
        project_name: str,
        design_name: str,
        object_names: list[str] | None = None,
    ) -> dict[str, Any]:
        _log(
            "get_live_hfss_geometry_inventory",
            {
                "live_session_id": session_id,
                "project_name": project_name,
                "design_name": design_name,
                "object_names": object_names or [],
            },
        )
        self._require_session(session_id)
        return {
            "object_count": 1,
            "objects": [
                {
                    "name": "airbox",
                    "faces": [
                        {"face_id": 101, "center": [0, 5, 5], "area": 100},
                        {"face_id": 102, "center": [10, 5, 5], "area": 100},
                    ],
                }
            ],
            "snapshot_digest": "geometry-digest",
        }

    def preview_hfss_setup(self, session_id: str, **kwargs: Any) -> dict[str, Any]:
        _log("preview_live_hfss_setup_create", {"live_session_id": session_id, **kwargs})
        self._require_session(session_id)
        return self._approval_preview("setup-preview-1", "setup-digest")

    def apply_hfss_setup(self, session_id: str, *, preview_id: str, approval_token: str) -> dict[str, Any]:
        _log(
            "apply_live_hfss_setup_create",
            {"live_session_id": session_id, "preview_id": preview_id, "approval_token": approval_token},
        )
        self._approved(session_id, preview_id, approval_token)
        return {"status": "verified", "setup_name": "Setup2", "project_saved": False}

    def preview_hfss_report(self, session_id: str, **kwargs: Any) -> dict[str, Any]:
        _log("preview_live_hfss_report_create", {"live_session_id": session_id, **kwargs})
        self._require_session(session_id)
        return self._approval_preview("report-preview-1", "report-digest")

    def apply_hfss_report(self, session_id: str, *, preview_id: str, approval_token: str) -> dict[str, Any]:
        _log(
            "apply_live_hfss_report_create",
            {"live_session_id": session_id, "preview_id": preview_id, "approval_token": approval_token},
        )
        self._approved(session_id, preview_id, approval_token)
        return {"status": "verified", "report_name": "S Parameter Plot", "project_saved": False}

    def preview_hfss_boundary(self, session_id: str, **kwargs: Any) -> dict[str, Any]:
        _log("preview_live_hfss_boundary_create", {"live_session_id": session_id, **kwargs})
        self._require_session(session_id)
        return self._approval_preview("boundary-preview-1", "boundary-digest")

    def apply_hfss_boundary(self, session_id: str, *, preview_id: str, approval_token: str) -> dict[str, Any]:
        _log(
            "apply_live_hfss_boundary_create",
            {"live_session_id": session_id, "preview_id": preview_id, "approval_token": approval_token},
        )
        self._approved(session_id, preview_id, approval_token)
        return {"status": "verified", "boundary_name": "P1", "project_saved": False}

    def start_hfss_analysis(
        self,
        session_id: str,
        *,
        project_name: str,
        design_name: str,
        setup_name: str,
        blocking: bool = False,
    ) -> dict[str, Any]:
        _log(
            "start_live_hfss_analysis",
            {
                "live_session_id": session_id,
                "project_name": project_name,
                "design_name": design_name,
                "setup_name": setup_name,
                "blocking": blocking,
            },
        )
        self._require_session(session_id)
        if self.scenario == "backend_failure":
            raise RuntimeError("injected live analysis failure")
        return {"started": True, "setup_name": setup_name, "blocking": blocking}

    def preview_hfss_analysis_start(self, session_id: str, **kwargs: Any) -> dict[str, Any]:
        _log("preview_live_hfss_analysis_start", {"live_session_id": session_id, **kwargs})
        self._require_session(session_id)
        return self._approval_preview("analysis-preview-1", "analysis-digest") | {
            "setup_name": kwargs["setup_name"],
            "resources": {
                "cores": kwargs.get("cores"),
                "tasks": kwargs.get("tasks"),
                "gpus": kwargs.get("gpus"),
                "use_auto_settings": kwargs.get("use_auto_settings", True),
            },
            "blocking": False,
        }

    def apply_hfss_analysis_start(
        self, session_id: str, *, preview_id: str, approval_token: str
    ) -> dict[str, Any]:
        _log(
            "apply_live_hfss_analysis_start",
            {"live_session_id": session_id, "preview_id": preview_id, "approval_token": approval_token},
        )
        self._approved(session_id, preview_id, approval_token)
        if self.scenario == "backend_failure":
            raise RuntimeError("injected live analysis failure")
        self.analysis_running = True
        return {"status": "submitted", "started": True, "run_id": "aedt-run-1", "blocking": False}

    def hfss_analysis_status(
        self,
        session_id: str,
        *,
        project_name: str,
        design_name: str,
        setup_name: str = "",
    ) -> dict[str, Any]:
        _log(
            "get_live_hfss_analysis_status",
            {
                "live_session_id": session_id,
                "project_name": project_name,
                "design_name": design_name,
                "setup_name": setup_name,
            },
        )
        self._require_session(session_id)
        return {
            "running": self.analysis_running,
            "setups": ["Setup1"],
            "setup_name": setup_name,
            "latest_run": {"run_id": "aedt-run-1", "state": "running"} if self.analysis_running else None,
        }

    def preview_hfss_analysis_cancel(self, session_id: str, **kwargs: Any) -> dict[str, Any]:
        _log("preview_live_hfss_analysis_cancel", {"live_session_id": session_id, **kwargs})
        self._require_session(session_id)
        return self._approval_preview("cancel-preview-1", "cancel-digest") | {"clean_stop": True}

    def apply_hfss_analysis_cancel(
        self, session_id: str, *, preview_id: str, approval_token: str
    ) -> dict[str, Any]:
        _log(
            "apply_live_hfss_analysis_cancel",
            {"live_session_id": session_id, "preview_id": preview_id, "approval_token": approval_token},
        )
        self._approved(session_id, preview_id, approval_token)
        self.analysis_running = False
        return {"status": "cancel_requested", "running": False}

    def preview_hfss_export(self, session_id: str, **kwargs: Any) -> dict[str, Any]:
        _log("preview_live_hfss_results_export", {"live_session_id": session_id, **kwargs})
        self._require_session(session_id)
        return self._approval_preview("export-preview-1", "export-digest") | {
            "export_kind": kwargs["export_kind"],
            "path_policy": "server_managed_directory_only",
        }

    def apply_hfss_export(self, session_id: str, *, preview_id: str, approval_token: str) -> dict[str, Any]:
        _log(
            "apply_live_hfss_results_export",
            {"live_session_id": session_id, "preview_id": preview_id, "approval_token": approval_token},
        )
        self._approved(session_id, preview_id, approval_token)
        return {
            "status": "verified",
            "artifact": {"path": r"C:\bench-runs\exports\network.s2p", "sha256": "a" * 64, "bytes": 128},
            "manifest_path": r"C:\bench-runs\exports\network.s2p.evidence.json",
            "project_unchanged": True,
        }

    def list_layout_paths(
        self,
        session_id: str,
        *,
        project_name: str,
        design_name: str,
        selector: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        _log(
            "list_live_layout_paths",
            {
                "live_session_id": session_id,
                "project_name": project_name,
                "design_name": design_name,
                "selector": selector or {},
            },
        )
        self._require_session(session_id)
        return {
            "project_name": project_name,
            "design_name": design_name,
            "count": 1,
            "paths": [
                {"name": "trace1", "primitive_id": "101", "net": "N1", "layer": "L1", "width_expression": "0.1mm"}
            ],
        }

    def preview_layout_width(
        self,
        session_id: str,
        *,
        project_name: str,
        design_name: str,
        selector: dict[str, Any],
        variable_name: str,
        variable_value: str,
    ) -> dict[str, Any]:
        _log(
            "preview_live_parameterize_path_width",
            {
                "live_session_id": session_id,
                "project_name": project_name,
                "design_name": design_name,
                "selector": selector,
                "variable_name": variable_name,
                "variable_value": variable_value,
            },
        )
        self._require_session(session_id)
        return {
            "preview_id": "live-preview-1",
            "target_count": 1,
            "snapshot_digest": "live-preview-digest",
            "approval_required": True,
            "approval_source": "external_host_only",
            "project_dirty": False,
        }

    def apply_layout_width(self, session_id: str, *, preview_id: str, approval_token: str) -> dict[str, Any]:
        _log(
            "apply_live_parameterize_path_width",
            {"live_session_id": session_id, "preview_id": preview_id, "approval_token": approval_token},
        )
        self._consume_approval(session_id, preview_id, approval_token)
        return {
            "status": "verified",
            "preview_id": preview_id,
            "target_count": 1,
            "verified_count": 1,
            "after": [{"name": "trace1", "width_expression": "trace_w"}],
            "project_dirty": True,
            "project_saved": False,
        }

    def wait_for_approval(
        self,
        session_id: str,
        *,
        preview_id: str,
        timeout_seconds: float = 0,
    ) -> dict[str, Any]:
        _log(
            "wait_for_live_approval",
            {
                "live_session_id": session_id,
                "preview_id": preview_id,
                "timeout_seconds": timeout_seconds,
            },
        )
        self._require_session(session_id)
        self.issued_approvals.add((session_id, preview_id))
        return {
            "live_session_id": session_id,
            "preview_id": preview_id,
            "status": "approved",
            "approval_token": "bench-host-approved",
        }

    def propose_exploratory_operation(self, plan: dict[str, Any]) -> dict[str, Any]:
        _log("propose_ansys_operation", {"plan": plan})
        normalized = OperationPlan.from_dict(plan).to_dict()
        candidate_id = f"candidate-{len(self.exploration_candidates) + 1}"
        self.exploration_candidates[candidate_id] = normalized
        return {
            "status": "proposed",
            "candidate_id": candidate_id,
            "trace_id": "trace-benchmark-verified",
            "risk": normalized["risk"],
        }

    def validate_exploratory_operation(self, candidate_id: str) -> dict[str, Any]:
        _log("validate_ansys_operation", {"candidate_id": candidate_id})
        plan = self.exploration_candidates[candidate_id]
        validation = self.exploration_validator.validate(plan)
        self.exploration_validations[candidate_id] = validation
        return {
            "status": "validated",
            "candidate_id": candidate_id,
            "risk": plan["risk"],
            "policy_version": validation["policy_version"],
            "rollback_strategy": validation["rollback_strategy"],
        }

    def preview_exploratory_operation(self, session_id: str, *, candidate_id: str) -> dict[str, Any]:
        _log(
            "preview_exploratory_operation",
            {"live_session_id": session_id, "candidate_id": candidate_id},
        )
        self._require_session(session_id)
        if candidate_id not in self.exploration_validations:
            raise ValueError("candidate must be successfully validated before preview")
        plan = self.exploration_candidates[candidate_id]
        preview_id = "explore-preview-1"
        self.exploration_previews[preview_id] = {"candidate_id": candidate_id, "plan": plan}
        approval_required = plan["risk"] == "reversible_edit"
        return {
            "status": "previewed",
            "candidate_id": candidate_id,
            "preview_id": preview_id,
            "snapshot_digest": "exploration-preview-digest",
            "risk": plan["risk"],
            "approval_required": approval_required,
            "approval_source": "external_host_only" if approval_required else "none",
            "mutation_count": 1 if approval_required else 0,
        }

    def apply_exploratory_operation(
        self,
        session_id: str,
        *,
        preview_id: str,
        approval_token: str = "",
    ) -> dict[str, Any]:
        _log(
            "apply_exploratory_operation",
            {
                "live_session_id": session_id,
                "preview_id": preview_id,
                "approval_token": approval_token,
            },
        )
        self._require_session(session_id)
        preview = self.exploration_previews[preview_id]
        risk = preview["plan"]["risk"]
        if risk == "reversible_edit":
            self._consume_approval(session_id, preview_id, approval_token)
        return {
            "status": "verified",
            "candidate_id": preview["candidate_id"],
            "preview_id": preview_id,
            "trace_id": "trace-benchmark-verified",
            "risk": risk,
            "readback_verified": True,
            "project_saved": False,
        }

    def capture_capability_trace(self, candidate_id: str) -> dict[str, Any]:
        _log("capture_capability_trace", {"candidate_id": candidate_id})
        if candidate_id not in self.exploration_candidates:
            raise ValueError(f"unknown exploratory candidate: {candidate_id}")
        return {
            "schema_version": 2,
            "trace_id": "trace-benchmark-verified",
            "candidate_id": candidate_id,
            "state": "verified",
            "sealed": True,
            "seal_digest": "c" * 64,
            "authentication": {"scheme": "hmac-sha256", "key_id": "benchmark-key-id"},
            "seal_hmac": "d" * 64,
            "events": [],
        }

    def promote_capability_candidate(self, trace_id: str, *, target_kind: str = "auto") -> dict[str, Any]:
        _log("promote_ansys_capability", {"trace_id": trace_id, "target_kind": target_kind})
        return {
            "status": "candidate",
            "trace_id": trace_id,
            "target_kind": "harness" if target_kind == "auto" else target_kind,
            "candidate_id": "promoted-benchmark-capability",
            "candidate_directory": r"C:\bench-runs\capability-candidates\promoted-benchmark-capability",
            "source_trace_verified": True,
            "capture_required": False,
            "next_action": "human_review_only",
            "applied": False,
            "committed": False,
            "hot_loaded": False,
        }

    def _require_session(self, session_id: str) -> None:
        if session_id not in self.sessions:
            raise ValueError(f"unknown live session: {session_id}")

    @staticmethod
    def _approval_preview(preview_id: str, digest: str) -> dict[str, Any]:
        return {
            "preview_id": preview_id,
            "snapshot_digest": digest,
            "approval_required": True,
            "approval_source": "external_host_only",
        }

    def _approved(self, session_id: str, preview_id: str, token: str) -> None:
        self._consume_approval(session_id, preview_id, token)

    def _consume_approval(self, session_id: str, preview_id: str, token: str) -> None:
        self._require_session(session_id)
        key = (session_id, preview_id)
        if token != "bench-host-approved" or key not in self.issued_approvals:
            raise RuntimeError("apply requires a token issued by wait_for_live_approval for this preview")
        self.issued_approvals.remove(key)


class _BenchmarkEvidenceVerifier:
    @staticmethod
    def verify(evidence: list[dict[str, Any]]) -> dict[str, Any]:
        return {"status": "verified", "verified_count": len(evidence)}


def main() -> None:
    kernel = InteractiveKernel(session_manager=FakeLayoutManager())
    create_server(kernel=kernel, live_manager=FakeLiveManager()).run(show_banner=False)


if __name__ == "__main__":
    main()
