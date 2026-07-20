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


def test_real_live_layout_via_update_harness(tmp_path: Path, monkeypatch):
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
    authority = HmacApprovalAuthority("real-layout-via-update-secret-32-bytes")
    manager = LiveAedtSessionManager(approval_verifier=authority)
    project_path = tmp_path / "RealLayoutViaUpdateAcceptance.aedt"
    via_names = [
        "V_UPDATE1",
        "V_UPDATE2",
        "V_ROLLBACK_ONLY",
        "V_N2_SEED",
        "V_N3_SEED",
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
        first = layout_app.modeler.create_via(
            name="V_UPDATE1",
            x="1mm",
            y="2mm",
            top_layer="TOP",
            bot_layer="BOT",
            net="N1",
        )
        second = layout_app.modeler.create_via(
            name="V_UPDATE2",
            x="3mm",
            y="4mm",
            top_layer="TOP",
            bot_layer="BOT",
            net="N1",
        )
        net_seed = layout_app.modeler.create_via(
            name="V_N2_SEED",
            x="0mm",
            y="0mm",
            top_layer="TOP",
            bot_layer="BOT",
            net="N2",
        )
        original_net_seed = layout_app.modeler.create_via(
            name="V_N3_SEED",
            x="-1mm",
            y="-1mm",
            top_layer="TOP",
            bot_layer="BOT",
            net="N3",
        )
        rollback_only = layout_app.modeler.create_via(
            name="V_ROLLBACK_ONLY",
            x="20mm",
            y="20mm",
            top_layer="TOP",
            bot_layer="BOT",
            net="N4",
        )
        assert first and second and net_seed and original_net_seed and rollback_only
        first.angle = "0deg"
        first.lock_position = True
        second.angle = "15deg"
        second.lock_position = False
        assert layout_app.save_project(str(project_path)) is True
        project_digest_before = _file_digest(project_path)
        assert project_digest_before is not None
        first_native_before = _native_via_values(layout_app, "V_UPDATE1")
        second_native_before = _native_via_values(layout_app, "V_UPDATE2")

        opened = manager.attach(port=port, version=version)
        launched_pid = opened["probe"]["pid"]
        session_id = opened["live_session_id"]
        updates = [
            {
                "name": "V_UPDATE1",
                "net_name": "N2",
                "location": [5.0, 6.0],
                "rotation_degrees": 45.0,
                "lock_position": True,
            },
            {
                "name": "V_UPDATE2",
                "net_name": "N2",
                "location": [-2.0, 8.0],
                "rotation_degrees": -30.0,
                "lock_position": False,
            },
        ]
        preview = manager.preview_layout_via_update(
            session_id,
            project_name="RealLayoutViaUpdateAcceptance",
            design_name="Layout1",
            updates=updates,
            max_vias=4,
        )
        assert preview["via_count"] == 2
        assert preview["model_units"] == "mm"
        assert preview["project_dirty"] is False
        assert preview["project_saved"] is False
        assert _native_via_values(layout_app, "V_UPDATE1") == first_native_before
        assert _native_via_values(layout_app, "V_UPDATE2") == second_native_before
        assert _file_digest(project_path) == project_digest_before

        applied = manager.apply_layout_via_update(
            session_id,
            preview_id=preview["preview_id"],
            approval_token=authority.issue(**preview["approval_request"]),
        )
        assert applied["status"] == "verified"
        assert applied["via_count"] == 2
        assert [item["name"] for item in applied["vias"]] == ["V_UPDATE1", "V_UPDATE2"]
        first_after, second_after = applied["vias"]
        assert first_after["net_name"] == "N2"
        assert first_after["location"] == [5.0, 6.0]
        assert first_after["rotation_degrees"] == 45.0
        assert first_after["lock_position"] is True
        assert second_after["net_name"] == "N2"
        assert second_after["location"] == [-2.0, 8.0]
        assert second_after["rotation_degrees"] == -30.0
        assert second_after["lock_position"] is False
        assert all(item["native_property_digest"] for item in applied["vias"])
        assert applied["automatic_rollback_on_failure"] is True
        assert applied["project_saved"] is False
        assert "N1" not in dict(layout_app.modeler.nets)
        assert _file_digest(project_path) == project_digest_before

        stale_preview = manager.preview_layout_via_update(
            session_id,
            project_name="RealLayoutViaUpdateAcceptance",
            design_name="Layout1",
            updates=[
                {
                    "name": "V_UPDATE1",
                    "net_name": "N3",
                    "location": [7.0, 8.0],
                    "rotation_degrees": 90.0,
                    "lock_position": False,
                }
            ],
        )
        first.lock_position = False
        first.angle = "60deg"
        first.lock_position = True
        with pytest.raises(Exception, match="stale 3D Layout via update preview"):
            manager.apply_layout_via_update(
                session_id,
                preview_id=stale_preview["preview_id"],
                approval_token=authority.issue(**stale_preview["approval_request"]),
            )
        first.lock_position = False
        first.angle = "45deg"
        first.lock_position = True
        assert _file_digest(project_path) == project_digest_before

        manager.release(session_id)
        session_id = ""

        from aedt_agent.live import backend as backend_module
        from aedt_agent.live.backend import LiveAedtBackend, LiveBackendError
        from aedt_agent.live.target import AedtTarget

        direct_backend = LiveAedtBackend(version=version)
        target = AedtTarget("port", port)
        rollback_request = {
            "project_name": "RealLayoutViaUpdateAcceptance",
            "design_name": "Layout1",
            "updates": [
                {
                    "name": "V_UPDATE1",
                    "net_name": "N3",
                    "location": [9.0, 10.0],
                    "rotation_degrees": 120.0,
                    "lock_position": False,
                },
                {
                    "name": "V_UPDATE2",
                    "net_name": "N3",
                    "location": [11.0, 12.0],
                    "rotation_degrees": 75.0,
                    "lock_position": True,
                },
                {
                    "name": "V_ROLLBACK_ONLY",
                    "net_name": "N3",
                    "location": [21.0, 22.0],
                    "rotation_degrees": -120.0,
                    "lock_position": True,
                },
            ],
        }
        rollback_preview = direct_backend.execute(
            target,
            "layout_via_update_preview",
            rollback_request,
        )
        rollback_state_before = dict(
            direct_backend._previews[rollback_preview["preview_id"]]["state"]
        )
        with monkeypatch.context() as patch:
            patch.setattr(
                backend_module,
                "_verify_layout_via_update_readback",
                lambda *args, **kwargs: (_ for _ in ()).throw(
                    LiveBackendError("injected real layout via update readback failure")
                ),
            )
            with pytest.raises(
                LiveBackendError,
                match="injected real layout via update readback failure",
            ):
                direct_backend.execute(
                    target,
                    "layout_via_update_apply",
                    {"preview_id": rollback_preview["preview_id"]},
                )
        retry_preview = direct_backend.execute(
            target,
            "layout_via_update_preview",
            rollback_request,
        )
        rollback_state_after = dict(
            direct_backend._previews[retry_preview["preview_id"]]["state"]
        )
        assert rollback_state_after == rollback_state_before
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
