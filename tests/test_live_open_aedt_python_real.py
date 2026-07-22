from __future__ import annotations

import os
from pathlib import Path
import socket

import pytest


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_REAL_LIVE_AEDT") != "1",
    reason="real live AEDT acceptance is opt-in",
)


def test_real_live_open_aedt_python_saves_backs_up_and_executes_exact_code(tmp_path: Path):
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

    authority = HmacApprovalAuthority("real-open-aedt-python-secret-32-bytes")
    manager = LiveAedtSessionManager(approval_verifier=authority)
    app = None
    session_id = ""
    pid = None
    port = requested_port
    project_path = tmp_path / "RealOpenAedtPython.aedt"
    try:
        _, port = launch_aedt(executable, non_graphical=True, port=port, student_version=False)
        app = Hfss(
            project=str(project_path),
            design="HFSS1",
            version=version,
            machine="localhost",
            port=port,
            new_desktop=False,
            close_on_exit=False,
        )
        assert app.save_project(str(project_path)) is True

        opened = manager.attach(port=port, version=version)
        pid = opened["probe"]["pid"]
        session_id = opened["live_session_id"]
        code = (
            "created = app.modeler.create_box([0, 0, 0], [1, 2, 3], "
            "name='OPEN_EXEC_BOX', material='vacuum')\n"
            "emit({'created': created.name})"
        )
        preview = manager.preview_open_aedt_python(
            session_id,
            project_name="RealOpenAedtPython",
            design_name="HFSS1",
            product="hfss",
            code=code,
        )
        assert preview["execution_policy"] == "open_with_approval"
        assert "code_preview" not in preview
        assert len(preview["code_sha256"]) == 64
        result = manager.apply_open_aedt_python(
            session_id,
            preview_id=preview["preview_id"],
            approval_token=authority.issue(**preview["approval_request"]),
        )
        assert result["status"] == "completed"
        assert result["events"] == [{"created": "OPEN_EXEC_BOX"}]
        assert Path(result["backup"]["directory"]).joinpath(project_path.name).is_file()
        assert "OPEN_EXEC_BOX" in app.modeler.object_names
        manager.release(session_id)
        session_id = ""
        app.release_desktop(close_projects=False, close_desktop=False)
        app = None
        _close(port, pid, version)
        pid = None
        assert _hfss_backup_has_no_object(
            Path(result["backup"]["directory"]) / project_path.name,
            version=version,
            object_name="OPEN_EXEC_BOX",
        )
    finally:
        if session_id:
            try:
                manager.release(session_id)
            except Exception:
                pass
        manager.close()
        if app is not None:
            try:
                app.release_desktop(close_projects=False, close_desktop=False)
            except Exception:
                pass
        _close(port, pid, version)


def test_real_live_open_aedt_python_uses_aedb_backup_when_aedt_file_is_missing(tmp_path: Path):
    from ansys.aedt.core import Edb, Hfss3dLayout
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

    authority = HmacApprovalAuthority("real-open-aedb-fallback-secret-32-bytes")
    manager = LiveAedtSessionManager(approval_verifier=authority)
    edb = None
    app = None
    session_id = ""
    pid = None
    port = requested_port
    edb_path = tmp_path / "Board.aedb"
    try:
        edb = Edb(str(edb_path), version=version)
        edb.save()
        edb.close()
        edb = None
        _, port = launch_aedt(executable, non_graphical=True, port=port, student_version=False)
        app = Hfss3dLayout(
            project=str(edb_path),
            design="Layout1",
            version=version,
            machine="localhost",
            port=port,
            new_desktop=False,
            close_on_exit=False,
        )
        Path(app.project_file).unlink()

        opened = manager.attach(port=port, version=version)
        pid = opened["probe"]["pid"]
        session_id = opened["live_session_id"]
        preview = manager.preview_open_aedt_python(
            session_id,
            project_name="Board",
            design_name="Layout1",
            product="layout",
            code=(
                "app.modeler.layers.add_layer('TOP', layer_type='signal', thickness='0.035mm', material='copper')\n"
                "created = app.modeler.create_rectangle('TOP', [0, 0], [1, 1], "
                "name='OPEN_AEDB_RECT', net_name='SIG')\n"
                "emit({'created': created.name})"
            ),
        )
        assert preview["backup_plan"]["source_project"] == str(edb_path)
        result = manager.apply_open_aedt_python(
            session_id,
            preview_id=preview["preview_id"],
            approval_token=authority.issue(**preview["approval_request"]),
        )
        assert result["status"] == "completed"
        assert result["events"] == [{"created": "OPEN_AEDB_RECT"}]
        assert result["backup"]["source_kind"] == "aedb_directory"
        backup_edb = Path(result["backup"]["directory"], "Board.aedb")
        assert (backup_edb / "edb.def").is_file()
        assert "OPEN_AEDB_RECT" in app.modeler.geometries
        manager.release(session_id)
        session_id = ""
        app.release_desktop(close_projects=False, close_desktop=False)
        app = None
        _close(port, pid, version)
        pid = None
        assert _layout_aedb_backup_has_no_geometry(
            backup_edb,
            version=version,
            geometry_name="OPEN_AEDB_RECT",
        )
    finally:
        if edb is not None:
            try:
                edb.close()
            except Exception:
                pass
        if session_id:
            try:
                manager.release(session_id)
            except Exception:
                pass
        manager.close()
        if app is not None:
            try:
                app.release_desktop(close_projects=False, close_desktop=False)
            except Exception:
                pass
        _close(port, pid, version)


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


def _hfss_backup_has_no_object(project_path: Path, *, version: str, object_name: str) -> bool:
    from ansys.aedt.core import Hfss
    from ansys.aedt.core.desktop import launch_aedt

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    app = None
    pid = None
    try:
        _, port = launch_aedt(
            Path(os.environ["ANSYSEM_ROOT" + version.replace("20", "", 1).replace(".", "")]) / "ansysedt.exe",
            non_graphical=True,
            port=port,
            student_version=False,
        )
        app = Hfss(
            project=str(project_path),
            design="HFSS1",
            version=version,
            machine="localhost",
            port=port,
            new_desktop=False,
            close_on_exit=False,
        )
        return object_name not in app.modeler.object_names
    finally:
        if app is not None:
            try:
                app.release_desktop(close_projects=False, close_desktop=False)
            except Exception:
                pass
        _close(port, pid, version)


def _layout_aedb_backup_has_no_geometry(edb_path: Path, *, version: str, geometry_name: str) -> bool:
    from ansys.aedt.core import Hfss3dLayout
    from ansys.aedt.core.desktop import launch_aedt

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    app = None
    pid = None
    try:
        _, port = launch_aedt(
            Path(os.environ["ANSYSEM_ROOT" + version.replace("20", "", 1).replace(".", "")]) / "ansysedt.exe",
            non_graphical=True,
            port=port,
            student_version=False,
        )
        app = Hfss3dLayout(
            project=str(edb_path),
            design="Layout1",
            version=version,
            machine="localhost",
            port=port,
            new_desktop=False,
            close_on_exit=False,
        )
        return geometry_name not in app.modeler.geometries
    finally:
        if app is not None:
            try:
                app.release_desktop(close_projects=False, close_desktop=False)
            except Exception:
                pass
        _close(port, pid, version)
