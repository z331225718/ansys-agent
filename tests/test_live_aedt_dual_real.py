from __future__ import annotations

import os
import socket

import pytest


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_REAL_LIVE_AEDT") != "1",
    reason="real dual live AEDT acceptance is opt-in",
)


def test_two_owned_aedt_sessions_are_explicit_isolated_and_reused():
    from aedt_agent.live.manager import LiveAedtSessionManager

    manager = LiveAedtSessionManager()
    launched = []
    try:
        first = manager.launch(non_graphical=True, timeout=90)
        launched.append(first)
        second = manager.launch(non_graphical=True, timeout=90)
        launched.append(second)
        discovered = manager.list_sessions()["sessions"]
        discovered_by_pid = {item["pid"]: item for item in discovered}

        manager.create_hfss_design(
            first["live_session_id"],
            project_name="DualAcceptanceA",
            design_name="HFSSA",
        )
        manager.create_hfss_design(
            second["live_session_id"],
            project_name="DualAcceptanceB",
            design_name="HFSSB",
        )
        first_info = manager.project_info(first["live_session_id"])
        second_info = manager.project_info(second["live_session_id"])
        first_again = manager.attach(port=first["port"])

        assert first["port"] != second["port"]
        assert first["pid"] != second["pid"]
        assert discovered_by_pid[first["pid"]]["grpc_port"] == first["port"]
        assert discovered_by_pid[second["pid"]]["grpc_port"] == second["port"]
        assert discovered_by_pid[first["pid"]]["owned_by_assistant"] is True
        assert discovered_by_pid[second["pid"]]["owned_by_assistant"] is True
        assert manager.registry.broker_count == 2
        assert first_again["reused_broker"] is True
        assert "DualAcceptanceA" in first_info["project_names"]
        assert "DualAcceptanceB" not in first_info["project_names"]
        assert "DualAcceptanceB" in second_info["project_names"]
        assert "DualAcceptanceA" not in second_info["project_names"]

        manager.release(first["live_session_id"])
        manager.release(second["live_session_id"])
        assert manager.registry.broker_count == 0
        for item in launched:
            with socket.create_connection(("127.0.0.1", item["port"]), timeout=2):
                pass
    finally:
        manager.close()
        for item in launched:
            _close_test_owned_aedt(item["port"], item["pid"])


def _close_test_owned_aedt(port: int, pid: int) -> None:
    try:
        from ansys.aedt.core import Desktop

        desktop = Desktop(
            version="2026.1",
            machine="localhost",
            port=port,
            new_desktop=False,
            close_on_exit=False,
        )
        desktop.release_desktop(close_projects=True, close_on_exit=True)
        return
    except Exception:
        pass
    try:
        import psutil

        process = psutil.Process(pid)
        process.terminate()
        process.wait(timeout=10)
    except Exception:
        pass
