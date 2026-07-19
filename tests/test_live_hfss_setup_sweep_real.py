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


def test_real_live_hfss_atomic_setup_sweep_harness(tmp_path: Path):
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
    launched, port = launch_aedt(
        executable,
        non_graphical=True,
        port=requested_port,
        student_version=False,
    )
    authority = HmacApprovalAuthority("real-atomic-setup-sweep-secret-32")
    manager = LiveAedtSessionManager(approval_verifier=authority)
    hfss_app = None
    launched_pid = None
    session_id = ""
    project_path = tmp_path / "RealAtomicSetupSweepAcceptance.aedt"
    try:
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
        fixture_body = hfss_app.modeler.create_box(
            ["0mm", "0mm", "0mm"],
            ["10mm", "5mm", "1mm"],
            name="SetupSweepFixtureBody",
            material="vacuum",
        )
        assert fixture_body
        fixture_face = min(fixture_body.faces, key=lambda item: float(item.center[0]))
        fixture_port = hfss_app.wave_port(
            assignment=fixture_face.id,
            name="SetupSweepFixturePort",
        )
        assert fixture_port
        assert hfss_app.save_project(str(project_path)) is True
        assert project_path.is_file()
        opened = manager.attach(port=port, version=version)
        launched_pid = opened["probe"]["pid"]
        session_id = opened["live_session_id"]
        project_digest_before = _file_digest(project_path)
        assert project_digest_before is not None
        setup = {
            "name": "AtomicSetup",
            "type": "HFSSDriven",
            "properties": {
                "Frequency": "10GHz",
                "MaximumPasses": 3,
                "MinimumPasses": 1,
                "MaxDeltaS": 0.05,
            },
        }
        sweep = {
            "name": "AtomicSweep",
            "range_type": "LinearCount",
            "sweep_type": "Interpolating",
            "unit": "GHz",
            "start_frequency": 1,
            "stop_frequency": 20,
            "count": 101,
            "save_fields": True,
        }
        preview = manager.preview_hfss_setup_sweep_create(
            session_id,
            project_name="RealAtomicSetupSweepAcceptance",
            design_name="HFSS1",
            setup=setup,
            sweep=sweep,
        )
        applied = manager.apply_hfss_setup_sweep_create(
            session_id,
            preview_id=preview["preview_id"],
            approval_token=authority.issue(**preview["approval_request"]),
        )
        inventory = manager.setup_inventory(
            session_id,
            product="hfss",
            project_name="RealAtomicSetupSweepAcceptance",
            design_name="HFSS1",
        )

        stale_preview = manager.preview_hfss_setup_sweep_create(
            session_id,
            project_name="RealAtomicSetupSweepAcceptance",
            design_name="HFSS1",
            setup={
                "name": "MustNotBeCreated",
                "type": "HFSSDriven",
                "properties": {"Frequency": "5GHz"},
            },
            sweep={
                "name": "MustNotBeCreatedSweep",
                "start_frequency": 1,
                "stop_frequency": 5,
                "count": 11,
            },
        )
        external_setup = hfss_app.create_setup(
            "ExternalSetup",
            setup_type="HFSSDriven",
        )
        assert external_setup
        with pytest.raises(Exception, match="stale HFSS setup and sweep create preview"):
            manager.apply_hfss_setup_sweep_create(
                session_id,
                preview_id=stale_preview["preview_id"],
                approval_token=authority.issue(**stale_preview["approval_request"]),
            )

        for boundary in list(hfss_app.boundaries or []):
            if str(getattr(boundary, "name", boundary)) == "SetupSweepFixturePort":
                assert boundary.delete() is True
        with pytest.raises(Exception, match="require at least one existing port"):
            manager.preview_hfss_setup_sweep_create(
                session_id,
                project_name="RealAtomicSetupSweepAcceptance",
                design_name="HFSS1",
                setup={
                    "name": "RollbackSetup",
                    "type": "HFSSDriven",
                    "properties": {"Frequency": "10GHz"},
                },
                sweep={
                    "name": "RollbackSweep",
                    "range_type": "LinearCount",
                    "sweep_type": "Interpolating",
                    "start_frequency": 1,
                    "stop_frequency": 10,
                    "count": 11,
                },
            )
        setup_names_after_guard = set(hfss_app.setup_names)

        assert launched is True
        assert preview["project_dirty"] is False
        assert preview["project_saved"] is False
        assert applied["status"] == "verified"
        assert applied["created_setup_name"] == "AtomicSetup"
        assert applied["created_sweep_name"] == "AtomicSweep"
        assert applied["setup_inventory"]["sweeps"] == ["AtomicSweep"]
        assert str(applied["setup_inventory"]["properties"]["Frequency"]) == "10GHz"
        assert str(applied["setup_inventory"]["properties"]["MaximumPasses"]) == "3"
        assert applied["atomic_setup_sweep_transaction"] is True
        assert applied["automatic_rollback_on_failure"] is True
        assert applied["project_saved"] is False
        assert _file_digest(project_path) == project_digest_before
        inventory_by_name = {item["name"]: item for item in inventory["setups"]}
        assert inventory_by_name["AtomicSetup"]["sweeps"] == ["AtomicSweep"]
        assert "MustNotBeCreated" not in inventory_by_name
        assert "RollbackSetup" not in setup_names_after_guard
        assert {"AtomicSetup", "ExternalSetup"}.issubset(setup_names_after_guard)
    finally:
        if hfss_app is not None:
            for setup_name in (
                "RollbackSetup",
                "MustNotBeCreated",
                "ExternalSetup",
                "AtomicSetup",
            ):
                try:
                    if setup_name in hfss_app.setup_names:
                        hfss_app.delete_setup(setup_name)
                except Exception:
                    pass
            try:
                fixture_boundaries = list(hfss_app.boundaries or [])
            except Exception:
                fixture_boundaries = []
            for boundary in fixture_boundaries:
                if str(getattr(boundary, "name", boundary)) == "SetupSweepFixturePort":
                    try:
                        boundary.delete()
                    except Exception:
                        pass
            try:
                hfss_app.modeler.delete(["SetupSweepFixtureBody"])
            except Exception:
                pass
        if session_id:
            try:
                manager.release(session_id)
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
