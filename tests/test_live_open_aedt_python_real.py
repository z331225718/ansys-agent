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
        assert preview["code_preview"] == code
        result = manager.apply_open_aedt_python(
            session_id,
            preview_id=preview["preview_id"],
            approval_token=authority.issue(**preview["approval_request"]),
        )
        assert result["status"] == "completed"
        assert result["events"] == [{"created": "OPEN_EXEC_BOX"}]
        assert Path(result["backup"]["directory"]).joinpath(project_path.name).is_file()
        assert "OPEN_EXEC_BOX" in app.modeler.object_names
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
