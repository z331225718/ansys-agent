from __future__ import annotations

import json
from typing import Any

from aedt_agent.agent.loop_runner import (
    load_loop_config,
    run_loop_from_config,
    validate_loop_config_for_run,
)
from aedt_agent.agent.mission import GraphRunStatus, MissionState
from aedt_agent.agent.policies import ExecutionProfile
from aedt_agent.pi_agent.case_config import PiAgentCase, PiAgentCaseError
from aedt_agent.pi_agent.initializer import initialize_local_case
from aedt_agent.pi_agent.status import (
    build_case_status,
    latest_graph_run_id,
    summarize_graph_report,
)


class PiAgentSupervisor:
    def __init__(self, case: PiAgentCase):
        self.case = case

    def preflight(self) -> dict[str, Any]:
        loop_config = load_loop_config(self.case.loop_config)
        profile = self._load_profile()
        loop_report = validate_loop_config_for_run(
            loop_config,
            check_paths=self.case.check_paths,
        )
        profile_checks = self._profile_checks(profile)
        status = (
            "passed"
            if loop_report["status"] == "passed"
            and all(item["status"] == "passed" for item in profile_checks)
            else "failed"
        )
        return {
            "status": status,
            "case": self.case.to_json_dict(),
            "loop_config": {
                "path": str(self.case.loop_config),
                "template_id": loop_config.get("template_id"),
                "goal": loop_config.get("goal"),
                "poll_interval_seconds": loop_config.get("poll_interval_seconds"),
            },
            "execution_profile": {
                "path": str(self.case.execution_profile),
                "profile_id": profile.profile_id,
                "simulation_runner": profile.simulation_runner,
                "allow_real_aedt": profile.allow_real_aedt,
            },
            "checks": [
                *loop_report["checks"],
                *profile_checks,
            ],
            "failed_checks": [
                *loop_report.get("failed_checks", []),
                *[
                    item["id"]
                    for item in profile_checks
                    if item["status"] == "failed"
                ],
            ],
        }

    def run(self) -> dict[str, Any]:
        preflight = self.preflight()
        if preflight["status"] != "passed":
            return {
                "status": "preflight_failed",
                "preflight": preflight,
            }
        loop_config = load_loop_config(self.case.loop_config)
        if self.case.graph_run_id and not loop_config.get("graph_run_id"):
            loop_config["graph_run_id"] = self.case.graph_run_id
        if self.case.mission_id and not loop_config.get("mission_id"):
            loop_config["mission_id"] = self.case.mission_id

        profile = self._load_profile()
        runtime = self._runtime(profile)
        report = run_loop_from_config(
            runtime,
            loop_config,
            worker_id=self.case.worker_id,
            max_workers=self.case.max_workers,
            poll_interval_seconds=self.case.poll_interval_seconds,
        )
        return {
            "status": report.get("status", "unknown"),
            "preflight": preflight,
            "run": report,
            "pi_status": summarize_graph_report(self.case, report),
        }

    def status(self) -> dict[str, Any]:
        if not self.case.db_path.is_file():
            return {
                "case_id": self.case.case_id,
                "status": "not_started",
                "reason": f"mission db does not exist: {self.case.db_path}",
                "case": self.case.to_json_dict(),
            }
        return build_case_status(self.case, runtime=self._runtime_without_workers())

    def init(self, *, target_case: str | None = None, force: bool = False) -> dict[str, Any]:
        return initialize_local_case(self.case, target_case=target_case, force=force)

    def resume(self, *, graph_run_id: str = "") -> dict[str, Any]:
        from aedt_agent.agent.graph_runner import resume_graph

        profile = self._load_profile()
        self._ensure_profile_allowed(profile)
        runtime = self._runtime(profile)
        selected_graph_id = self._select_graph_run_id(runtime, graph_run_id)
        report = resume_graph(
            runtime,
            selected_graph_id,
            worker_id=self.case.worker_id,
            max_workers=self.case.max_workers,
        )
        return {
            "status": report.get("status", "unknown"),
            "graph_run_id": selected_graph_id,
            "run": report,
            "pi_status": summarize_graph_report(self.case, report),
        }

    def approve(
        self,
        *,
        approval_id: str,
        option_id: str = "approve",
        comment: str | None = None,
    ) -> dict[str, Any]:
        from aedt_agent.agent.approvals import ApprovalService

        runtime = self._runtime_without_workers()
        approval = ApprovalService(runtime.store).approve(
            approval_id,
            selected_option_id=option_id,
            comment=comment,
        )
        return {
            "status": "approved",
            "approval": approval.to_json_dict(),
            "pi_status": build_case_status(self.case, runtime=runtime),
        }

    def reject(
        self,
        *,
        approval_id: str,
        comment: str | None = None,
    ) -> dict[str, Any]:
        from aedt_agent.agent.approvals import ApprovalService

        runtime = self._runtime_without_workers()
        approval = ApprovalService(runtime.store).reject(
            approval_id,
            comment=comment,
        )
        return {
            "status": "rejected",
            "approval": approval.to_json_dict(),
            "pi_status": build_case_status(self.case, runtime=runtime),
        }

    def stop(self, *, graph_run_id: str = "", reason: str = "pi agent stop") -> dict[str, Any]:
        runtime = self._runtime_without_workers()
        selected_graph_id = self._select_graph_run_id(runtime, graph_run_id)
        graph_run = runtime.store.get_graph_run(selected_graph_id)
        if graph_run is None:
            raise KeyError(f"graph run not found: {selected_graph_id}")
        already_terminal = graph_run.status in {
            GraphRunStatus.SUCCEEDED,
            GraphRunStatus.FAILED,
            GraphRunStatus.CANCELED,
        }
        if not already_terminal:
            runtime.store.update_graph_run_status(
                selected_graph_id,
                GraphRunStatus.CANCELED,
                current_node_id=graph_run.current_node_id,
                error={"code": "pi_agent_stop", "message": reason},
            )
        mission = runtime.get_mission(graph_run.mission_id)
        if mission.state not in {
            MissionState.COMPLETED,
            MissionState.FAILED,
            MissionState.CANCELED,
        }:
            runtime.store.update_mission_state(graph_run.mission_id, MissionState.CANCELED)
        return {
            "status": "already_terminal" if already_terminal else "canceled",
            "graph_run_id": selected_graph_id,
            "mission_id": graph_run.mission_id,
            "reason": reason,
            "pi_status": build_case_status(
                self.case,
                runtime=runtime,
                graph_run_id=selected_graph_id,
            ),
        }

    def web(self) -> None:
        from aedt_agent.agent.web import run_agent_window

        profile = self._load_profile()
        self._ensure_profile_allowed(profile)
        runtime = self._runtime(profile)
        run_agent_window(
            host=self.case.dashboard_host,
            port=self.case.dashboard_port,
            db_path=self.case.db_path,
            runtime=runtime,
        )

    def _load_profile(self) -> ExecutionProfile:
        payload = json.loads(self.case.execution_profile.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise PiAgentCaseError(f"{self.case.execution_profile} must contain a JSON object")
        return ExecutionProfile.from_json_dict(payload)

    def _profile_checks(self, profile: ExecutionProfile) -> list[dict[str, Any]]:
        return [
            _check(
                "profile_local_cli",
                profile.simulation_runner == "local_cli" or self.case.allow_ssh_remote,
                (
                    "Pi Agent defaults to local_cli; set allow_ssh_remote=true "
                    "only for explicit split-machine runs"
                ),
            ),
            _check(
                "profile_real_aedt_enabled",
                profile.allow_real_aedt,
                "reviewed BRD Pi Agent loop requires real AEDT execution enabled",
            ),
            _check(
                "case_max_workers_one",
                self.case.max_workers == 1,
                "Pi Agent MVP runs one AEDT worker at a time",
            ),
        ]

    def _ensure_profile_allowed(self, profile: ExecutionProfile) -> None:
        failed = [
            item
            for item in self._profile_checks(profile)
            if item["status"] == "failed"
        ]
        if failed:
            raise PiAgentCaseError(
                "unsafe Pi Agent execution profile: "
                + ", ".join(item["id"] for item in failed)
            )

    def _runtime_without_workers(self):
        from aedt_agent.agent.orchestrator import AgentRuntime
        from aedt_agent.infrastructure import SQLiteMissionStore

        return AgentRuntime(SQLiteMissionStore(self.case.db_path))

    def _runtime(self, profile: ExecutionProfile):
        from aedt_agent.agent.cli import _runtime_with_workers

        return _runtime_with_workers(self.case.db_path, profile)

    def _select_graph_run_id(self, runtime, explicit: str = "") -> str:
        selected = explicit or self.case.graph_run_id or latest_graph_run_id(runtime)
        if not selected:
            raise PiAgentCaseError("no graph_run_id found; run the case first")
        return selected


def _check(check_id: str, passed: bool, message: str) -> dict[str, str]:
    return {
        "id": check_id,
        "status": "passed" if passed else "failed",
        "severity": "error",
        "message": message,
    }
