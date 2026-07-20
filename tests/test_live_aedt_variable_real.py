from __future__ import annotations

import hashlib
import os
import socket
from pathlib import Path

import pytest


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_REAL_LIVE_AEDT") != "1",
    reason="real live AEDT acceptance is opt-in",
)


def test_real_live_aedt_variable_batch_harness_for_hfss_and_layout(tmp_path: Path):
    from ansys.aedt.core import Hfss, Hfss3dLayout
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
    authority = HmacApprovalAuthority("real-variable-batch-acceptance-secret-32-bytes")
    manager = LiveAedtSessionManager(approval_verifier=authority)
    hfss_app = None
    layout_app = None
    launched_pid = None
    session_id = ""
    project_path = tmp_path / "RealVariableBatchAcceptance.aedt"
    hfss_names = {
        "W_existing",
        "W_main",
        "W_double",
        "RollbackA",
        "$GlobalScale",
        "$RollbackBad",
    }
    layout_names = {"LW_existing", "LW_double", "MustNotCreate", "ExternalChange"}
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
        assert hfss_app.variable_manager.set_variable(
            "W_existing",
            "1.0mm",
            sweep=False,
            description="preserve design metadata",
        )
        assert hfss_app.variable_manager.set_variable(
            "$GlobalScale",
            "2.0",
            sweep=False,
            description="preserve project metadata",
        )
        hfss_app.oproject.InsertDesign("HFSS 3D Layout Design", "Layout1", "", "")
        layout_app = Hfss3dLayout(
            project=hfss_app.project_name,
            design="Layout1",
            version=version,
            machine="localhost",
            port=port,
            new_desktop=False,
            close_on_exit=False,
        )
        assert layout_app.variable_manager.set_variable(
            "LW_existing",
            "4.0mil",
            sweep=False,
            description="preserve layout metadata",
            circuit_parameter=True,
        )
        hfss_app.set_active_design("HFSS1")
        assert hfss_app.save_project(str(project_path)) is True
        project_digest_before = _file_digest(project_path)
        assert project_digest_before is not None

        opened = manager.attach(port=port, version=version)
        launched_pid = opened["probe"]["pid"]
        session_id = opened["live_session_id"]

        single_preview = manager.preview_variable_upsert(
            session_id,
            product="hfss",
            project_name="RealVariableBatchAcceptance",
            design_name="HFSS1",
            variable_name="W_existing",
            expression="1.2500mm",
        )
        single = manager.apply_variable_upsert(
            session_id,
            preview_id=single_preview["preview_id"],
            approval_token=authority.issue(**single_preview["approval_request"]),
        )
        assert single["status"] == "verified"
        assert single["after_expression"] == "1.25mm"
        assert single["automatic_rollback_on_failure"] is True

        hfss_preview = manager.preview_variable_batch_upsert(
            session_id,
            product="hfss",
            project_name="RealVariableBatchAcceptance",
            design_name="HFSS1",
            variables=[
                {"name": "W_existing", "expression": "1.5000mm"},
                {"name": "W_main", "expression": "2.5mm"},
                {"name": "W_double", "expression": "2*W_main"},
                {"name": "$GlobalScale", "expression": "3.0"},
            ],
        )
        hfss_result = manager.apply_variable_batch_upsert(
            session_id,
            preview_id=hfss_preview["preview_id"],
            approval_token=authority.issue(**hfss_preview["approval_request"]),
        )
        assert hfss_result["status"] == "verified"
        assert hfss_result["requested_count"] == 4
        assert hfss_result["create_count"] == 2
        assert hfss_result["update_count"] == 2
        assert [item["name"] for item in hfss_result["changes"]] == [
            "W_existing",
            "W_main",
            "W_double",
            "$GlobalScale",
        ]

        layout_preview = manager.preview_variable_batch_upsert(
            session_id,
            product="layout",
            project_name="RealVariableBatchAcceptance",
            design_name="Layout1",
            variables=[
                {"name": "LW_existing", "expression": "4.3mil"},
                {"name": "LW_double", "expression": "2*LW_existing"},
                {"name": "$GlobalScale", "expression": "4.0"},
            ],
        )
        layout_result = manager.apply_variable_batch_upsert(
            session_id,
            preview_id=layout_preview["preview_id"],
            approval_token=authority.issue(**layout_preview["approval_request"]),
        )
        assert layout_result["status"] == "verified"
        assert layout_result["create_count"] == 1
        assert layout_result["update_count"] == 2
        assert layout_result["changes"][-1]["readback_expression"] == "4"

        stale_preview = manager.preview_variable_batch_upsert(
            session_id,
            product="layout",
            project_name="RealVariableBatchAcceptance",
            design_name="Layout1",
            variables=[{"name": "MustNotCreate", "expression": "1mm"}],
        )
        assert layout_app.variable_manager.set_variable("ExternalChange", "9mm")
        with pytest.raises(Exception, match="stale variable batch preview"):
            manager.apply_variable_batch_upsert(
                session_id,
                preview_id=stale_preview["preview_id"],
                approval_token=authority.issue(**stale_preview["approval_request"]),
            )
        assert layout_app.variable_manager.delete_variable("ExternalChange")

        rollback_before = manager.variable_inventory(
            session_id,
            product="hfss",
            project_name="RealVariableBatchAcceptance",
            design_name="HFSS1",
        )
        rollback_preview = manager.preview_variable_batch_upsert(
            session_id,
            product="hfss",
            project_name="RealVariableBatchAcceptance",
            design_name="HFSS1",
            variables=[
                {"name": "RollbackA", "expression": "1mm"},
                {"name": "$RollbackBad", "expression": "W_main"},
            ],
        )
        with pytest.raises(Exception, match="failed to create AEDT variable: \\$RollbackBad"):
            manager.apply_variable_batch_upsert(
                session_id,
                preview_id=rollback_preview["preview_id"],
                approval_token=authority.issue(**rollback_preview["approval_request"]),
            )
        rollback_after = manager.variable_inventory(
            session_id,
            product="hfss",
            project_name="RealVariableBatchAcceptance",
            design_name="HFSS1",
        )
        assert rollback_after["variables"] == rollback_before["variables"]
        assert "RollbackA" not in {item["name"] for item in rollback_after["variables"]}
        assert "$RollbackBad" not in {item["name"] for item in rollback_after["variables"]}

        hfss_inventory = manager.variable_inventory(
            session_id,
            product="hfss",
            project_name="RealVariableBatchAcceptance",
            design_name="HFSS1",
        )
        layout_inventory = manager.variable_inventory(
            session_id,
            product="layout",
            project_name="RealVariableBatchAcceptance",
            design_name="Layout1",
        )
        hfss_by_name = {item["name"]: item["expression"] for item in hfss_inventory["variables"]}
        layout_by_name = {item["name"]: item["expression"] for item in layout_inventory["variables"]}
        assert hfss_by_name["W_existing"] == "1.5mm"
        assert hfss_by_name["W_main"] == "2.5mm"
        assert hfss_by_name["W_double"] == "2*W_main"
        assert hfss_by_name["$GlobalScale"] == "4"
        assert layout_by_name["LW_existing"] == "4.3mil"
        assert layout_by_name["LW_double"] == "2*LW_existing"
        assert layout_by_name["$GlobalScale"] == "4"
        existing = hfss_app.variable_manager.variables["W_existing"]
        project_existing = hfss_app.variable_manager.variables["$GlobalScale"]
        assert existing.sweep is False
        assert existing.description == "preserve design metadata"
        assert project_existing.sweep is False
        assert project_existing.description == "preserve project metadata"
        assert hfss_result["automatic_rollback_on_failure"] is True
        assert layout_result["automatic_rollback_on_failure"] is True
        assert hfss_result["project_saved"] is False
        assert layout_result["project_saved"] is False
        assert _file_digest(project_path) == project_digest_before
    finally:
        if session_id:
            try:
                manager.release(session_id)
            except Exception:
                pass
        manager.close()
        if layout_app is not None:
            for name in layout_names:
                try:
                    layout_app.variable_manager.delete_variable(name)
                except Exception:
                    pass
        if hfss_app is not None:
            for name in hfss_names:
                try:
                    hfss_app.variable_manager.delete_variable(name)
                except Exception:
                    pass
        for app in (layout_app, hfss_app):
            if app is not None:
                try:
                    app.release_desktop(close_projects=False, close_desktop=False)
                except Exception:
                    pass
        if launched:
            _close_test_owned_aedt(port, launched_pid, version)


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
        return
    except Exception:
        pass
    if pid is None:
        return
    try:
        import psutil

        process = psutil.Process(pid)
        process.terminate()
        process.wait(timeout=10)
    except Exception:
        pass
