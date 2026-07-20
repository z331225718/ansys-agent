from __future__ import annotations

import os

import pytest


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_REAL_PYEDB") != "1",
    reason="real PyEDB smoke is opt-in",
)


def test_real_grpc_process_worker_parameterizes_path_width(tmp_path):
    from pyedb import Edb

    from aedt_agent.interactive.kernel import InteractiveKernel
    from aedt_agent.interactive.process_manager import ProcessLayoutSessionManager

    source = tmp_path / "source.aedb"
    edb = Edb(edbpath=str(source), version="2026.1", grpc=True)
    edb.stackup.add_layer("L1")
    edb.modeler.create_trace(
        [[0, 0], [0.01, 0]],
        "L1",
        width="0.1mm",
        net_name="N1",
    )
    edb.save()
    edb.close()

    manager = ProcessLayoutSessionManager(timeout_seconds=60)
    kernel = InteractiveKernel(session_manager=manager)
    try:
        opened = kernel.open_layout_session(
            str(source),
            writable=True,
            workspace=str(tmp_path / "work"),
            version="2026.1",
            edb_backend="grpc",
        )
        session_id = opened["session_id"]
        listed = kernel.execute_capability(
            "layout.paths.list",
            {
                "session_id": session_id,
                "selector": {"target_width": "0.1mm", "tolerance": "1nm"},
            },
        )
        preview = kernel.execute_capability(
            "layout.path_width.parameterize.preview",
            {
                "session_id": session_id,
                "selector": {
                    "target_width": "0.1mm",
                    "tolerance": "1nm",
                    "parameterized": False,
                },
                "variable_name": "trace_w",
                "variable_value": "0.1mm",
            },
        )
        applied = kernel.execute_capability(
            "layout.path_width.parameterize.apply",
            {"session_id": session_id, "preview_id": preview["preview_id"]},
        )
        closed = kernel.close_layout_session(session_id)
    finally:
        manager.shutdown()

    assert listed["count"] == 1
    assert applied["status"] == "verified"
    assert applied["verified_count"] == 1
    assert applied["after"][0]["width_expression"] == "trace_w"
    assert applied["evidence"]["variable_is_parameter"] is True
    assert closed["source_unchanged"] is True


def test_real_dotnet_backend_required_by_aedt_2024r2_is_installed(tmp_path):
    from pyedb import Edb

    source = tmp_path / "dotnet-source.aedb"
    edb = Edb(edbpath=str(source), version="2026.1", grpc=False)
    try:
        edb.stackup.add_layer("L1")
        edb.modeler.create_trace(
            [[0, 0], [0.01, 0]],
            "L1",
            width="0.1mm",
            net_name="N1",
        )
        assert edb.save() is True
    finally:
        edb.close()

    assert source.is_dir()
