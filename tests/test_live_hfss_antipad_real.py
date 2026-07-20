from __future__ import annotations

import hashlib
import math
import os
from pathlib import Path
import socket

import pytest


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_REAL_LIVE_AEDT") != "1",
    reason="real live AEDT acceptance is opt-in",
)


def test_real_live_hfss_antipad_subtract_harness(tmp_path: Path, monkeypatch):
    from ansys.aedt.core import Hfss
    from ansys.aedt.core.desktop import launch_aedt

    from aedt_agent.live.approval import HmacApprovalAuthority
    from aedt_agent.live.manager import LiveAedtSessionManager

    version = os.getenv("REAL_AEDT_VERSION", "2026.1")
    executable = Path(
        os.getenv("REAL_AEDT_EXECUTABLE")
        or Path(os.environ["ANSYSEM_ROOT" + version.replace("20", "", 1).replace(".", "")])
        / "ansysedt.exe"
    )
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        requested_port = probe.getsockname()[1]

    authority = HmacApprovalAuthority("real-hfss-antipad-secret-32-bytes")
    manager = LiveAedtSessionManager(approval_verifier=authority)
    app = None
    direct_backend = None
    session_id = ""
    pid = None
    port = requested_port
    project_path = tmp_path / "RealHfssAntipadAcceptance.aedt"
    try:
        _, port = launch_aedt(executable, non_graphical=True, port=port, student_version=False)
        app = Hfss(
            project=str(project_path),
            design="HFSS1",
            solution_type="DrivenModal",
            version=version,
            machine="localhost",
            port=port,
            new_desktop=False,
            close_on_exit=False,
        )
        plate = app.modeler.create_box(
            ["-5mm", "-5mm", "0mm"],
            ["10mm", "10mm", "0.035mm"],
            name="L2_GND",
            material="copper",
        )
        rollback_plate = app.modeler.create_box(
            ["15mm", "-5mm", "0mm"],
            ["10mm", "10mm", "0.035mm"],
            name="L3_GND_ROLLBACK",
            material="copper",
        )
        plate.solve_inside = False
        rollback_plate.solve_inside = False
        assert app.assign_perfect_e(["L2_GND"], name="PlatePEC")
        assert app.mesh.assign_length_mesh(
            ["L2_GND"],
            inside_selection=True,
            maximum_length="0.5mm",
            maximum_elements=500,
            name="PlateMesh",
        )
        assert app.save_project(str(project_path)) is True
        disk_before = _digest(project_path)

        opened = manager.attach(port=port, version=version)
        pid = opened["probe"]["pid"]
        session_id = opened["live_session_id"]
        preview = manager.preview_hfss_antipad_subtract(
            session_id,
            project_name="RealHfssAntipadAcceptance",
            design_name="HFSS1",
            blank_object_name="L2_GND",
            tool_name="__AP_TOOL_REAL",
            center=[1.0, -0.5],
            radius=0.8,
        )
        assert preview["blank_z_range"] == [0.0, 0.035]
        assert preview["tool_origin"][2] < 0
        assert preview["tool_origin"][2] + preview["tool_height"] > 0.035
        assert _digest(project_path) == disk_before
        result = manager.apply_hfss_antipad_subtract(
            session_id,
            preview_id=preview["preview_id"],
            approval_token=authority.issue(**preview["approval_request"]),
        )
        expected = math.pi * 0.8 * 0.8 * 0.035
        assert result["status"] == "verified"
        assert result["removed_volume"] == pytest.approx(expected, rel=1e-6)
        assert result["blank_before"]["object_id"] == result["blank_after"]["object_id"]
        assert result["blank_after"]["material_name"] == "copper"
        assert result["boundaries_preserved"] is True
        assert result["mesh_operations_preserved"] is True
        assert "__AP_TOOL_REAL" not in app.modeler.object_names
        assert _digest(project_path) == disk_before

        manager.release(session_id)
        session_id = ""
        from aedt_agent.live import backend as backend_module
        from aedt_agent.live.backend import LiveAedtBackend, LiveBackendError
        from aedt_agent.live.target import AedtTarget

        direct_backend = LiveAedtBackend(version=version)
        target = AedtTarget("port", port)
        rollback_args = {
            "project_name": "RealHfssAntipadAcceptance",
            "design_name": "HFSS1",
            "blank_object_name": "L3_GND_ROLLBACK",
            "tool_name": "__AP_TOOL_ROLLBACK",
            "center": [20.0, 0.5],
            "radius": 0.7,
        }
        rollback_preview = direct_backend.execute(
            target, "hfss_antipad_subtract_preview", rollback_args
        )
        snapshot = rollback_preview["snapshot_digest"]
        with monkeypatch.context() as patch:
            patch.setattr(
                backend_module,
                "_verify_hfss_antipad_subtract_state",
                lambda *args, **kwargs: (_ for _ in ()).throw(
                    LiveBackendError("injected real HFSS anti-pad readback failure")
                ),
            )
            with pytest.raises(LiveBackendError, match="injected real HFSS anti-pad"):
                direct_backend.execute(
                    target,
                    "hfss_antipad_subtract_apply",
                    {"preview_id": rollback_preview["preview_id"]},
                )
        retry = direct_backend.execute(target, "hfss_antipad_subtract_preview", rollback_args)
        assert retry["snapshot_digest"] == snapshot
        retry_state = direct_backend._previews[retry["preview_id"]]["state"]
        assert "__AP_TOOL_ROLLBACK" not in {
            item["name"] for item in retry_state["geometry"]
        }
        assert _digest(project_path) == disk_before
    finally:
        if session_id:
            try:
                manager.release(session_id)
            except Exception:
                pass
        if direct_backend is not None:
            try:
                direct_backend.release()
            except Exception:
                pass
        manager.close()
        if app is not None:
            try:
                app.release_desktop(close_projects=False, close_desktop=False)
            except Exception:
                pass
        _close(port, pid, version)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _close(port: int, pid: int | None, version: str) -> None:
    try:
        from ansys.aedt.core import Desktop

        Desktop(
            version=version,
            machine="localhost",
            port=port,
            new_desktop=False,
            close_on_exit=False,
        ).release_desktop(close_projects=True, close_on_exit=True)
    except Exception:
        pass
    if pid is None:
        return
    try:
        import psutil

        process = psutil.Process(pid)
        if process.is_running():
            process.terminate()
            process.wait(timeout=10)
    except Exception:
        pass
