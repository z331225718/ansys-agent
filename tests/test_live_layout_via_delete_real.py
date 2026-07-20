from __future__ import annotations

import hashlib
import os
from pathlib import Path
import socket
import time

import pytest


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_REAL_LIVE_AEDT") != "1",
    reason="real live AEDT acceptance is opt-in",
)


def test_real_live_layout_via_delete_harness(tmp_path: Path, monkeypatch):
    from ansys.aedt.core import Hfss3dLayout
    from ansys.aedt.core.desktop import launch_aedt

    from aedt_agent.live.approval import HmacApprovalAuthority
    from aedt_agent.live.manager import LiveAedtSessionManager

    version = os.getenv("REAL_AEDT_VERSION", "2026.1")
    executable_override = os.getenv("REAL_AEDT_EXECUTABLE")
    if executable_override:
        executable = Path(executable_override)
    else:
        root_variable = "ANSYSEM_ROOT" + version.replace("20", "", 1).replace(".", "")
        executable = Path(os.environ[root_variable]) / "ansysedt.exe"
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        requested_port = probe.getsockname()[1]

    launched = False
    port = requested_port
    launched_pid = None
    layout_app = None
    session_id = ""
    direct_backend = None
    authority = HmacApprovalAuthority("real-layout-via-delete-secret-32-bytes")
    manager = LiveAedtSessionManager(approval_verifier=authority)
    project_path = tmp_path / "RealLayoutViaDeleteAcceptance.aedt"
    via_names = [
        "V_KEEP_N1",
        "V_KEEP_N2",
        "V_DELETE1",
        "V_DELETE_DROP",
        "V_DELETE_NO_NET",
        "V_STALE",
        "V_ROLLBACK1",
        "V_ROLLBACK_DROP",
        "V_ROLLBACK_NO_NET",
    ]
    try:
        launched, port = launch_aedt(
            executable,
            non_graphical=True,
            port=requested_port,
            student_version=False,
        )
        layout_app = Hfss3dLayout(
            project=str(project_path),
            design="Layout1",
            version=version,
            machine="localhost",
            port=port,
            new_desktop=False,
            close_on_exit=False,
        )
        assert layout_app.modeler.layers.add_layer(
            "BOT",
            layer_type="signal",
            thickness="0.035mm",
            elevation="0mm",
            material="copper",
        )
        assert layout_app.modeler.layers.add_layer(
            "D1",
            layer_type="dielectric",
            thickness="0.2mm",
            elevation="0.035mm",
            material="FR4_epoxy",
        )
        assert layout_app.modeler.layers.add_layer(
            "TOP",
            layer_type="signal",
            thickness="0.035mm",
            elevation="0.235mm",
            material="copper",
        )
        keep_n1 = _create_via(layout_app, "V_KEEP_N1", 0, 0, "N1")
        keep_n2 = _create_via(layout_app, "V_KEEP_N2", 0, 1, "N2")
        delete_one = _create_via(
            layout_app,
            "V_DELETE1",
            1,
            2,
            "N1",
            hole_diameter=0.25,
            angle=45,
            locked=True,
        )
        delete_drop = _create_via(
            layout_app,
            "V_DELETE_DROP",
            3,
            4,
            "N_DROP",
            angle=-30,
        )
        delete_no_net = _create_via(
            layout_app,
            "V_DELETE_NO_NET",
            5,
            6,
            None,
        )
        stale = _create_via(layout_app, "V_STALE", 7, 8, "N1")
        rollback_one = _create_via(
            layout_app,
            "V_ROLLBACK1",
            9,
            10,
            "N2",
            hole_diameter=0.3,
            angle=75,
            locked=True,
        )
        rollback_drop = _create_via(
            layout_app,
            "V_ROLLBACK_DROP",
            11,
            12,
            "N_ROLLBACK",
            angle=-60,
        )
        rollback_no_net = _create_via(
            layout_app,
            "V_ROLLBACK_NO_NET",
            13,
            14,
            None,
            angle=120,
            locked=True,
        )
        assert all(
            [
                keep_n1,
                keep_n2,
                delete_one,
                delete_drop,
                delete_no_net,
                stale,
                rollback_one,
                rollback_drop,
                rollback_no_net,
            ]
        )
        assert layout_app.save_project(str(project_path)) is True
        project_digest_before = _file_digest(project_path)
        assert project_digest_before is not None
        delete_names = ["V_DELETE1", "V_DELETE_DROP", "V_DELETE_NO_NET"]
        native_before = {
            name: _native_via_values(layout_app, name) for name in delete_names
        }

        opened = manager.attach(port=port, version=version)
        launched_pid = opened["probe"]["pid"]
        session_id = opened["live_session_id"]
        preview = manager.preview_layout_via_delete(
            session_id,
            project_name="RealLayoutViaDeleteAcceptance",
            design_name="Layout1",
            names=delete_names,
            max_vias=4,
        )
        assert preview["names"] == delete_names
        assert preview["via_count"] == 3
        assert preview["model_units"] == "mm"
        assert preview["project_dirty"] is False
        assert preview["project_saved"] is False
        assert {
            name: _native_via_values(layout_app, name) for name in delete_names
        } == native_before
        assert _file_digest(project_path) == project_digest_before

        applied = manager.apply_layout_via_delete(
            session_id,
            preview_id=preview["preview_id"],
            approval_token=authority.issue(**preview["approval_request"]),
        )
        assert applied["status"] == "verified"
        assert applied["deleted_names"] == delete_names
        assert applied["via_count"] == 3
        assert applied["absence_digest"]
        assert applied["automatic_rollback_on_failure"] is True
        assert applied["project_saved"] is False
        assert not set(delete_names).intersection(_native_via_names(layout_app))
        assert "N_DROP" not in dict(layout_app.modeler.nets)
        assert "N1" in dict(layout_app.modeler.nets)
        assert _file_digest(project_path) == project_digest_before

        stale_preview = manager.preview_layout_via_delete(
            session_id,
            project_name="RealLayoutViaDeleteAcceptance",
            design_name="Layout1",
            names=["V_STALE"],
        )
        stale.angle = "25deg"
        with pytest.raises(Exception, match="stale 3D Layout via delete preview"):
            manager.apply_layout_via_delete(
                session_id,
                preview_id=stale_preview["preview_id"],
                approval_token=authority.issue(**stale_preview["approval_request"]),
            )
        stale.angle = "0deg"
        assert "V_STALE" in _native_via_names(layout_app)
        assert _file_digest(project_path) == project_digest_before

        manager.release(session_id)
        session_id = ""

        from aedt_agent.live import backend as backend_module
        from aedt_agent.live.backend import LiveAedtBackend, LiveBackendError
        from aedt_agent.live.target import AedtTarget

        direct_backend = LiveAedtBackend(version=version)
        target = AedtTarget("port", port)
        rollback_names = [
            "V_ROLLBACK1",
            "V_ROLLBACK_DROP",
            "V_ROLLBACK_NO_NET",
        ]
        rollback_request = {
            "project_name": "RealLayoutViaDeleteAcceptance",
            "design_name": "Layout1",
            "names": rollback_names,
            "max_vias": 4,
        }
        rollback_preview = direct_backend.execute(
            target,
            "layout_via_delete_preview",
            rollback_request,
        )
        rollback_state_before = dict(
            direct_backend._previews[rollback_preview["preview_id"]]["state"]
        )
        with monkeypatch.context() as patch:
            patch.setattr(
                backend_module,
                "_verify_layout_via_delete_readback",
                lambda *args, **kwargs: (_ for _ in ()).throw(
                    LiveBackendError("injected real layout via delete readback failure")
                ),
            )
            with pytest.raises(
                LiveBackendError,
                match="injected real layout via delete readback failure",
            ):
                direct_backend.execute(
                    target,
                    "layout_via_delete_apply",
                    {"preview_id": rollback_preview["preview_id"]},
                )
        retry_preview = direct_backend.execute(
            target,
            "layout_via_delete_preview",
            rollback_request,
        )
        rollback_state_after = dict(
            direct_backend._previews[retry_preview["preview_id"]]["state"]
        )
        assert rollback_state_after == rollback_state_before
        assert "N_ROLLBACK" in rollback_state_after["net_names"]
        assert [item["name"] for item in rollback_state_after["vias"]] == rollback_names
        assert _file_digest(project_path) == project_digest_before
    finally:
        if session_id:
            try:
                manager.release(session_id)
            except Exception:
                pass
        if layout_app is not None:
            for name in via_names:
                try:
                    _delete_native_via(layout_app, name)
                except Exception:
                    pass
        if direct_backend is not None:
            try:
                direct_backend.release()
            except Exception:
                pass
        manager.close()
        if layout_app is not None:
            try:
                layout_app.release_desktop(
                    close_projects=False,
                    close_desktop=False,
                )
            except Exception:
                pass
        if launched:
            _close_test_owned_aedt(port, launched_pid, version)


def _create_via(
    app,
    name: str,
    x: float,
    y: float,
    net: str | None,
    *,
    hole_diameter: float | None = None,
    angle: float = 0.0,
    locked: bool = False,
):
    via = app.modeler.create_via(
        name=name,
        x=x,
        y=y,
        hole_diam=hole_diameter,
        top_layer="TOP",
        bot_layer="BOT",
        net=net,
    )
    if via:
        via.angle = f"{angle}deg"
        via.lock_position = locked
    return via


def _native_via_values(app, name: str) -> dict[str, str]:
    editor = app.modeler.oeditor
    return {
        str(prop): str(editor.GetPropertyValue("BaseElementTab", name, prop))
        for prop in editor.GetProperties("BaseElementTab", name)
    }


def _native_via_names(app) -> list[str]:
    return sorted(str(item) for item in app.modeler.oeditor.FindObjects("Type", "via"))


def _delete_native_via(app, name: str) -> None:
    if name in _native_via_names(app):
        app.modeler.oeditor.Delete([name])
    cache = getattr(app.modeler, "_vias", None)
    if isinstance(cache, dict):
        cache.pop(name, None)
    assert name not in _native_via_names(app)


def _file_digest(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _close_test_owned_aedt(port: int, pid: int | None, version: str) -> None:
    try:
        from ansys.aedt.core import Desktop

        desktop = Desktop(
            version=version,
            machine="localhost",
            port=port,
            new_desktop=False,
            close_on_exit=False,
        )
        desktop.release_desktop(close_projects=True, close_on_exit=True)
    except Exception:
        pass
    if pid is None:
        return
    try:
        import psutil

        process = psutil.Process(pid)
        for _ in range(50):
            if not process.is_running():
                return
            time.sleep(0.2)
        process.terminate()
        process.wait(timeout=10)
    except psutil.NoSuchProcess:
        return
    except Exception:
        pass
