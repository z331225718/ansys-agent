from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from typing import Any


def _log(tool: str, arguments: dict[str, Any]) -> None:
    path = os.getenv("MCP_BENCH_LOG")
    if not path:
        return
    with Path(path).open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"tool": tool, "arguments": arguments}, ensure_ascii=False) + "\n")


class FakeWorkerClient:
    def __init__(self) -> None:
        self.scenario = os.getenv("MCP_BENCH_SCENARIO", "single_session")

    async def execute_async(
        self,
        target: Any,
        command: str,
        arguments: dict[str, Any],
        timeout: float | None = None,
    ) -> dict[str, Any]:
        _log(command, {"target": target.key, "arguments": arguments, "timeout": timeout})
        if self.scenario == "backend_failure" and command == "start_analysis":
            raise RuntimeError("AEDT setup failed to start")
        common = {"pid": 4242, "port": 50061, "target": {"kind": target.kind, "value": target.value}}
        if command == "ping":
            return common | {"connected": True}
        if command == "project_info":
            return common | {
                "project_names": ["BenchProject"],
                "active_project": "BenchProject",
                "active_design": "BenchHFSS",
            }
        if command == "create_hfss_design":
            return common | {"created": True, "project_name": arguments["project_name"], "design_name": arguments["design_name"]}
        if command == "save_project":
            return common | {"saved": True, "path": arguments.get("path") or r"C:\fixtures\BenchProject.aedt"}
        if command == "start_analysis":
            return common | {"started": True, "blocking": arguments.get("blocking", False)}
        if command == "analysis_status":
            return common | {"running": True, "setup_name": arguments.get("setup_name") or "Setup1"}
        if command == "close_projects":
            return common | {"closed": list(arguments.get("project_names") or [])}
        if command == "build_wr90_waveguide":
            return common | {"status": "completed", "validated": True}
        raise RuntimeError(f"unsupported fake command: {command}")

    async def release_async(self, target: Any, timeout: float | None = None) -> dict[str, Any]:
        _log("release_connection", {"target": target.key, "timeout": timeout})
        return {"released": True, "pid": 4242, "port": 50061}


class FakeSessionDiscovery:
    def __init__(self, scenario: str) -> None:
        self.scenario = scenario

    def list_sessions(self) -> list[dict[str, Any]]:
        _log("list_aedt_sessions", {})
        sessions = [
            {"pid": 4242, "ports": [50061], "version": "2026.1", "executable": "ansysedt.exe"}
        ]
        if self.scenario == "two_sessions":
            sessions.append(
                {"pid": 4343, "ports": [50062], "version": "2026.1", "executable": "ansysedt.exe"}
            )
        return sessions


class FakeLauncher:
    def launch(self, **kwargs: Any) -> dict[str, Any]:
        _log("launch_aedt", kwargs)
        return {"pid": 4242, "port": 50061, "launched": True, "version": kwargs.get("version", "2026.1")}


def main() -> None:
    root = os.environ["CAE_AGENT_HUB_AEDT_ROOT"]
    sys.path.insert(0, root)
    import mcp_server as hub

    scenario = os.getenv("MCP_BENCH_SCENARIO", "single_session")
    hub.worker_client = FakeWorkerClient()
    hub.session_discovery = FakeSessionDiscovery(scenario)
    hub.aedt_launcher = FakeLauncher()
    hub.mcp.run()


if __name__ == "__main__":
    main()
