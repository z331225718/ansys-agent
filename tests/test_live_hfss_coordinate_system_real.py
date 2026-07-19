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


def test_real_live_hfss_coordinate_system_harness(tmp_path: Path, monkeypatch):
    from ansys.aedt.core import Hfss
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
    authority = HmacApprovalAuthority("real-coordinate-system-secret-32-bytes")
    manager = LiveAedtSessionManager(approval_verifier=authority)
    hfss_app = None
    launched_pid = None
    session_id = ""
    direct_backend = None
    project_path = tmp_path / "RealCoordinateSystemAcceptance.aedt"
    coordinate_names = {
        "HarnessCS",
        "ExternalCS",
        "ParentCS",
        "MustNotCreate",
        "RollbackCS",
    }
    try:
        launched, port = launch_aedt(
            executable,
            non_graphical=True,
            port=requested_port,
            student_version=False,
        )
        hfss_app = Hfss(
            project=str(project_path),
            design="HFSS1",
            solution_type="DrivenModal",
            version=version,
            machine="localhost",
            port=port,
            new_desktop=False,
            close_on_exit=False,
        )
        assert hfss_app.variable_manager.set_variable("OX", "12.5mm", sweep=True)
        parent = hfss_app.modeler.create_coordinate_system(
            origin=["1mm", "2mm", "3mm"],
            reference_cs="Global",
            name="ParentCS",
            mode="axis",
            x_pointing=[1, 0, 0],
            y_pointing=[0, 1, 0],
        )
        assert parent and parent.name == "ParentCS"
        _set_wcs(hfss_app, "ParentCS")
        assert hfss_app.save_project(str(project_path)) is True
        project_digest_before = _file_digest(project_path)
        assert project_digest_before is not None

        opened = manager.attach(port=port, version=version)
        launched_pid = opened["probe"]["pid"]
        session_id = opened["live_session_id"]
        inventory_before = manager.hfss_coordinate_system_inventory(
            session_id,
            project_name="RealCoordinateSystemAcceptance",
            design_name="HFSS1",
        )
        assert inventory_before["coordinate_system_count"] == 2
        assert inventory_before["relative_coordinate_system_count"] == 1
        assert inventory_before["active_coordinate_system"] == "ParentCS"
        assert inventory_before["design_unchanged"] is True

        preview = manager.preview_hfss_coordinate_system_create(
            session_id,
            project_name="RealCoordinateSystemAcceptance",
            design_name="HFSS1",
            coordinate_system_name="HarnessCS",
            reference_coordinate_system="ParentCS",
            origin=["OX", "2mm", 3],
            x_axis=[1, 1, 0],
            y_axis=[0, 0, 2],
        )
        inventory_after_preview = manager.hfss_coordinate_system_inventory(
            session_id,
            project_name="RealCoordinateSystemAcceptance",
            design_name="HFSS1",
        )
        assert preview["project_dirty"] is False
        assert preview["project_saved"] is False
        assert preview["active_coordinate_system_before"] == "ParentCS"
        assert inventory_after_preview["snapshot_digest"] == inventory_before["snapshot_digest"]
        assert _file_digest(project_path) == project_digest_before

        result = manager.apply_hfss_coordinate_system_create(
            session_id,
            preview_id=preview["preview_id"],
            approval_token=authority.issue(**preview["approval_request"]),
        )
        readback = result["coordinate_system"]
        assert result["status"] == "verified"
        assert result["created_coordinate_system_name"] == "HarnessCS"
        assert readback["kind"] == "relative"
        assert readback["reference_coordinate_system"] == "ParentCS"
        assert readback["mode"] == "Axis/Position"
        assert readback["origin"] == ["OX", "2mm", "3mm"]
        assert readback["x_axis"] == ["1mm", "1mm", "0mm"]
        assert readback["y_axis"] == ["0mm", "0mm", "2mm"]
        assert result["active_coordinate_system_restored"] is True
        assert result["automatic_rollback_on_failure"] is True
        assert result["project_saved"] is False
        assert hfss_app.modeler.oeditor.GetActiveCoordinateSystem() == "ParentCS"

        stale_preview = manager.preview_hfss_coordinate_system_create(
            session_id,
            project_name="RealCoordinateSystemAcceptance",
            design_name="HFSS1",
            coordinate_system_name="MustNotCreate",
            origin=[0, 0, 0],
            x_axis=[1, 0, 0],
            y_axis=[0, 1, 0],
        )
        external = hfss_app.modeler.create_coordinate_system(
            origin=[0, 0, 0],
            reference_cs="Global",
            name="ExternalCS",
            mode="axis",
            x_pointing=[1, 0, 0],
            y_pointing=[0, 1, 0],
        )
        assert external
        _set_wcs(hfss_app, "ParentCS")
        with pytest.raises(Exception, match="stale HFSS coordinate system create preview"):
            manager.apply_hfss_coordinate_system_create(
                session_id,
                preview_id=stale_preview["preview_id"],
                approval_token=authority.issue(**stale_preview["approval_request"]),
            )
        with pytest.raises(Exception, match="coordinate system already exists"):
            manager.preview_hfss_coordinate_system_create(
                session_id,
                project_name="RealCoordinateSystemAcceptance",
                design_name="HFSS1",
                coordinate_system_name="harnesscs",
                origin=[0, 0, 0],
                x_axis=[1, 0, 0],
                y_axis=[0, 1, 0],
            )
        with pytest.raises(Exception, match="must not be collinear"):
            manager.preview_hfss_coordinate_system_create(
                session_id,
                project_name="RealCoordinateSystemAcceptance",
                design_name="HFSS1",
                coordinate_system_name="InvalidAxes",
                origin=[0, 0, 0],
                x_axis=[1, 0, 0],
                y_axis=[2, 0, 0],
            )
        with pytest.raises(Exception, match="must be Global or an existing relative"):
            manager.preview_hfss_coordinate_system_create(
                session_id,
                project_name="RealCoordinateSystemAcceptance",
                design_name="HFSS1",
                coordinate_system_name="InvalidReference",
                reference_coordinate_system="MissingCS",
                origin=[0, 0, 0],
                x_axis=[1, 0, 0],
                y_axis=[0, 1, 0],
            )

        inventory_after = manager.hfss_coordinate_system_inventory(
            session_id,
            project_name="RealCoordinateSystemAcceptance",
            design_name="HFSS1",
        )
        assert inventory_after["coordinate_system_count"] == 4
        assert inventory_after["active_coordinate_system"] == "ParentCS"
        assert "MustNotCreate" not in {
            item["name"] for item in inventory_after["coordinate_systems"]
        }
        assert _file_digest(project_path) == project_digest_before

        manager.release(session_id)
        session_id = ""
        from aedt_agent.live import backend as backend_module
        from aedt_agent.live.backend import LiveAedtBackend, LiveBackendError
        from aedt_agent.live.target import AedtTarget

        direct_backend = LiveAedtBackend(version=version)
        target = AedtTarget("port", port)
        rollback_before = direct_backend.execute(
            target,
            "hfss_coordinate_system_inventory",
            {
                "project_name": "RealCoordinateSystemAcceptance",
                "design_name": "HFSS1",
            },
        )
        rollback_preview = direct_backend.execute(
            target,
            "hfss_coordinate_system_create_preview",
            {
                "project_name": "RealCoordinateSystemAcceptance",
                "design_name": "HFSS1",
                "coordinate_system_name": "RollbackCS",
                "reference_coordinate_system": "Global",
                "origin": [0, 0, 0],
                "x_axis": [1, 0, 0],
                "y_axis": [0, 1, 0],
            },
        )
        with monkeypatch.context() as patch:
            patch.setattr(
                backend_module,
                "_verify_hfss_coordinate_system_readback",
                lambda *args, **kwargs: (_ for _ in ()).throw(
                    LiveBackendError("injected real coordinate readback failure")
                ),
            )
            with pytest.raises(
                LiveBackendError,
                match="injected real coordinate readback failure",
            ):
                direct_backend.execute(
                    target,
                    "hfss_coordinate_system_create_apply",
                    {"preview_id": rollback_preview["preview_id"]},
                )
        rollback_after = direct_backend.execute(
            target,
            "hfss_coordinate_system_inventory",
            {
                "project_name": "RealCoordinateSystemAcceptance",
                "design_name": "HFSS1",
            },
        )
        assert rollback_after["snapshot_digest"] == rollback_before["snapshot_digest"]
        assert rollback_after["active_coordinate_system"] == "ParentCS"
        assert "RollbackCS" not in {
            item["name"] for item in rollback_after["coordinate_systems"]
        }
        assert _file_digest(project_path) == project_digest_before
    finally:
        if hfss_app is not None:
            try:
                _set_wcs(hfss_app, "Global")
            except Exception:
                pass
            try:
                for coordinate_system in reversed(
                    list(hfss_app.modeler.coordinate_systems or [])
                ):
                    if coordinate_system.name in coordinate_names:
                        coordinate_system.delete()
            except Exception:
                pass
            try:
                if "OX" in hfss_app.variable_manager.variables:
                    hfss_app.variable_manager.delete_variable("OX")
            except Exception:
                pass
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
        if hfss_app is not None:
            try:
                hfss_app.release_desktop(close_projects=False, close_desktop=False)
            except Exception:
                pass
        if launched:
            _close_test_owned_aedt(port, launched_pid, version)


def _set_wcs(app, name: str) -> None:
    app.modeler.oeditor.SetWCS(
        [
            "NAME:SetWCS Parameter",
            "Working Coordinate System:=",
            name,
            "RegionDepCSOk:=",
            False,
        ]
    )


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
