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


def test_real_live_layout_via_create_harness(tmp_path: Path, monkeypatch):
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
    authority = HmacApprovalAuthority("real-layout-via-secret-at-least-32-bytes")
    manager = LiveAedtSessionManager(approval_verifier=authority)
    project_path = tmp_path / "RealLayoutViaAcceptance.aedt"
    test_via_names = [
        "HarnessVia1",
        "HarnessVia2",
        "ExternalStaleVia",
        "RollbackVia",
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
        seed = layout_app.modeler.create_via(
            name="V_SEED",
            x="0mm",
            y="0mm",
            top_layer="TOP",
            bot_layer="BOT",
            net="N_EXISTING",
        )
        assert seed and seed.name == "V_SEED"
        assert layout_app.save_project(str(project_path)) is True
        project_digest_before = _file_digest(project_path)
        assert project_digest_before is not None
        baseline_names = _native_via_names(layout_app)
        assert baseline_names == ["V_SEED"]

        opened = manager.attach(port=port, version=version)
        launched_pid = opened["probe"]["pid"]
        session_id = opened["live_session_id"]
        via_specs = [
            {
                "name": "HarnessVia1",
                "padstack": "PlanarEMVia",
                "x": 1.0,
                "y": 2.0,
                "rotation_degrees": 45.0,
                "hole_diameter": 0.25,
                "top_layer": "TOP",
                "bottom_layer": "BOT",
                "net_name": "N_EXISTING",
                "lock_position": True,
            },
            {
                "name": "HarnessVia2",
                "padstack": "PlanarEMVia",
                "x": 3.0,
                "y": 4.0,
                "rotation_degrees": -30.0,
                "top_layer": "TOP",
                "bottom_layer": "BOT",
                "net_name": "N_EXISTING",
                "lock_position": False,
            },
        ]
        preview = manager.preview_layout_via_create(
            session_id,
            project_name="RealLayoutViaAcceptance",
            design_name="Layout1",
            vias=via_specs,
            max_vias=4,
        )
        assert preview["via_count"] == 2
        assert preview["model_units"] == "mm"
        assert preview["project_dirty"] is False
        assert preview["project_saved"] is False
        assert _native_via_names(layout_app) == baseline_names
        assert _file_digest(project_path) == project_digest_before

        applied = manager.apply_layout_via_create(
            session_id,
            preview_id=preview["preview_id"],
            approval_token=authority.issue(**preview["approval_request"]),
        )
        assert applied["status"] == "verified"
        assert applied["via_count"] == 2
        assert [item["name"] for item in applied["vias"]] == [
            "HarnessVia1",
            "HarnessVia2",
        ]
        first, second = applied["vias"]
        assert first["location"] == [1.0, 2.0]
        assert first["rotation_degrees"] == 45.0
        assert first["lock_position"] is True
        assert first["override_hole_diameter"] is True
        assert first["hole_diameter"] == "0.25mm"
        assert second["location"] == [3.0, 4.0]
        assert second["rotation_degrees"] == -30.0
        assert second["lock_position"] is False
        assert second["override_hole_diameter"] is False
        assert all(item["native_property_digest"] for item in applied["vias"])
        assert applied["automatic_rollback_on_failure"] is True
        assert applied["project_saved"] is False
        assert _native_via_names(layout_app) == [
            "HarnessVia1",
            "HarnessVia2",
            "V_SEED",
        ]
        assert _file_digest(project_path) == project_digest_before

        stale_preview = manager.preview_layout_via_create(
            session_id,
            project_name="RealLayoutViaAcceptance",
            design_name="Layout1",
            vias=[
                {
                    **via_specs[0],
                    "name": "ExternalStaleVia",
                    "x": 5.0,
                    "y": 6.0,
                }
            ],
        )
        external = layout_app.modeler.create_via(
            name="ExternalStaleVia",
            x="5mm",
            y="6mm",
            top_layer="TOP",
            bot_layer="BOT",
            net="N_EXISTING",
        )
        assert external and external.name == "ExternalStaleVia"
        with pytest.raises(Exception, match="stale 3D Layout via create preview"):
            manager.apply_layout_via_create(
                session_id,
                preview_id=stale_preview["preview_id"],
                approval_token=authority.issue(**stale_preview["approval_request"]),
            )
        assert "ExternalStaleVia" in _native_via_names(layout_app)
        assert _file_digest(project_path) == project_digest_before

        for name in ["HarnessVia1", "HarnessVia2", "ExternalStaleVia"]:
            _delete_native_via(layout_app, name)
        assert _native_via_names(layout_app) == baseline_names
        manager.release(session_id)
        session_id = ""

        from aedt_agent.live import backend as backend_module
        from aedt_agent.live.backend import LiveAedtBackend, LiveBackendError
        from aedt_agent.live.target import AedtTarget

        direct_backend = LiveAedtBackend(version=version)
        target = AedtTarget("port", port)
        rollback_request = {
            "project_name": "RealLayoutViaAcceptance",
            "design_name": "Layout1",
            "vias": [
                {
                    **via_specs[0],
                    "name": "RollbackVia",
                    "x": 7.0,
                    "y": 8.0,
                }
            ],
        }
        rollback_preview = direct_backend.execute(
            target,
            "layout_via_create_preview",
            rollback_request,
        )
        rollback_state_before = dict(
            direct_backend._previews[rollback_preview["preview_id"]]["state"]
        )
        direct_app = next(iter(direct_backend._apps.values()))
        with monkeypatch.context() as patch:
            patch.setattr(
                backend_module,
                "_verify_layout_via_create_readback",
                lambda *args, **kwargs: (_ for _ in ()).throw(
                    LiveBackendError("injected real layout via readback failure")
                ),
            )
            with pytest.raises(
                LiveBackendError,
                match="injected real layout via readback failure",
            ):
                direct_backend.execute(
                    target,
                    "layout_via_create_apply",
                    {"preview_id": rollback_preview["preview_id"]},
                )
        assert "RollbackVia" not in _native_via_names(direct_app)
        retry_preview = direct_backend.execute(
            target,
            "layout_via_create_preview",
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
            for name in test_via_names:
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
