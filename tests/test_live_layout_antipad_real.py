from __future__ import annotations

import hashlib
import os
from pathlib import Path
import socket

import pytest


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_REAL_LIVE_AEDT") != "1",
    reason="real live AEDT acceptance is opt-in",
)


def test_real_live_layout_antipad_circle_harness(tmp_path: Path, monkeypatch):
    from ansys.aedt.core import Hfss3dLayout
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

    authority = HmacApprovalAuthority("real-layout-antipad-secret-32-bytes")
    manager = LiveAedtSessionManager(approval_verifier=authority)
    app = None
    direct_backend = None
    session_id = ""
    pid = None
    port = requested_port
    project_path = tmp_path / "RealLayoutAntipadAcceptance.aedt"
    try:
        _, port = launch_aedt(executable, non_graphical=True, port=port, student_version=False)
        app = Hfss3dLayout(
            project=str(project_path),
            design="Layout1",
            version=version,
            machine="localhost",
            port=port,
            new_desktop=False,
            close_on_exit=False,
        )
        assert app.modeler.layers.add_layer(
            "GND", layer_type="signal", thickness="0.035mm", material="copper"
        )
        assert app.modeler.create_rectangle(
            "GND", [-5, -5], [10, 10], name="GND_PLANE", net_name="GND"
        )
        assert app.save_project(str(project_path)) is True
        disk_before = _digest(project_path)

        opened = manager.attach(port=port, version=version)
        pid = opened["probe"]["pid"]
        session_id = opened["live_session_id"]
        request = [
            {
                "name": "AP_REAL",
                "owner_name": "GND_PLANE",
                "center": [1.0, -0.5],
                "radius": 0.8,
            }
        ]
        preview = manager.preview_layout_antipad_circle_create(
            session_id,
            project_name="RealLayoutAntipadAcceptance",
            design_name="Layout1",
            voids=request,
        )
        assert preview["owners"][0]["points"] == [
            [-5.0, -5.0],
            [5.0, -5.0],
            [5.0, 5.0],
            [-5.0, 5.0],
        ]
        assert preview["voids"][0]["layer_name"] == "GND"
        assert _digest(project_path) == disk_before
        result = manager.apply_layout_antipad_circle_create(
            session_id,
            preview_id=preview["preview_id"],
            approval_token=authority.issue(**preview["approval_request"]),
        )
        assert result["status"] == "verified"
        assert result["voids"][0]["owner_membership_verified"] is True
        assert result["voids"][0]["center"] == [1.0, -0.5]
        assert result["voids"][0]["radius"] == pytest.approx(0.8)
        assert list(app.oeditor.GetPolygonVoids("GND_PLANE")) == ["AP_REAL"]
        assert _digest(project_path) == disk_before

        manager.release(session_id)
        session_id = ""
        from aedt_agent.live import backend as backend_module
        from aedt_agent.live.backend import LiveAedtBackend, LiveBackendError
        from aedt_agent.live.target import AedtTarget

        direct_backend = LiveAedtBackend(version=version)
        target = AedtTarget("port", port)
        rollback_args = {
            "project_name": "RealLayoutAntipadAcceptance",
            "design_name": "Layout1",
            "voids": [{**request[0], "name": "AP_ROLLBACK", "center": [-1.0, 0.5]}],
        }
        rollback_preview = direct_backend.execute(
            target, "layout_antipad_circle_create_preview", rollback_args
        )
        snapshot = rollback_preview["snapshot_digest"]
        with monkeypatch.context() as patch:
            patch.setattr(
                backend_module,
                "_verify_layout_antipad_circle_create_state",
                lambda *args, **kwargs: (_ for _ in ()).throw(
                    LiveBackendError("injected real layout anti-pad readback failure")
                ),
            )
            with pytest.raises(LiveBackendError, match="injected real layout anti-pad"):
                direct_backend.execute(
                    target,
                    "layout_antipad_circle_create_apply",
                    {"preview_id": rollback_preview["preview_id"]},
                )
        retry = direct_backend.execute(target, "layout_antipad_circle_create_preview", rollback_args)
        assert retry["snapshot_digest"] == snapshot
        retry_state = direct_backend._previews[retry["preview_id"]]["state"]
        assert "AP_ROLLBACK" not in retry_state["circle_void_names"]
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
