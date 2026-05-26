import json

from aedt_agent.layout.progress import BrdWorkflowProgressWriter


def test_brd_progress_writer_writes_running_and_succeeded_artifacts(tmp_path):
    writer = BrdWorkflowProgressWriter(
        tmp_path / "workflow_run.json",
        layout_file="/tmp/case.brd",
        signal_nets=["SRDS_3_RX1_P", "SRDS_3_RX1_N"],
        reference_nets=["GND"],
    )

    writer.step_running("import_layout_file", "Open BRD/MCM with PyEDB")
    running = json.loads((tmp_path / "workflow_run.json").read_text(encoding="utf-8"))
    assert running["workflow_id"] == "import_brd_cutout_sparam_tdr_v1"
    assert running["status"] == "running"
    assert running["steps"][0]["step_id"] == "import_layout_file"
    assert running["steps"][0]["status"] == "running"

    writer.step_succeeded("import_layout_file", "Open BRD/MCM with PyEDB", {"source_edb_path": "/tmp/source.aedb"})
    writer.finish_succeeded({"edb_path": "/tmp/cutout.aedb", "aedt_project": "/tmp/cutout.aedt"})
    done = json.loads((tmp_path / "workflow_run.json").read_text(encoding="utf-8"))
    assert done["status"] == "succeeded"
    assert done["steps"][0]["status"] == "succeeded"
    assert done["outputs"]["edb_path"] == "/tmp/cutout.aedb"
    assert done["outputs"]["signal_nets"] == ["SRDS_3_RX1_P", "SRDS_3_RX1_N"]


def test_brd_progress_writer_records_failed_step(tmp_path):
    writer = BrdWorkflowProgressWriter(tmp_path / "workflow_run.json", layout_file="/tmp/case.brd")

    writer.step_running("select_layout_nets", "Select Nets")
    writer.step_failed("select_layout_nets", "Select Nets", "ValueError", "no signal nets matched")

    failed = json.loads((tmp_path / "workflow_run.json").read_text(encoding="utf-8"))
    assert failed["status"] == "failed"
    assert failed["steps"][0]["status"] == "failed"
    assert failed["steps"][0]["error_type"] == "ValueError"
    assert "no signal nets matched" in failed["steps"][0]["error_message"]
    assert failed["repair_context"]["failed_step_id"] == "select_layout_nets"
