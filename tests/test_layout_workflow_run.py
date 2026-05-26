from aedt_agent.layout.workflow_run import import_cutout_summary_to_workflow_run


def test_import_cutout_summary_to_workflow_run_maps_steps_and_outputs():
    summary = {
        "status": "succeeded",
        "layout_file": "/tmp/case.brd",
        "signal_nets": ["SRDS_3_RX1_N", "SRDS_3_RX1_P"],
        "reference_nets": ["GND"],
        "edb_path": "/tmp/case_cutout.aedb",
        "aedt_project": "/tmp/case_cutout.aedt",
        "touchstone": "",
        "tdr": "",
        "steps": [
            {"id": "import_layout_file", "label": "Import", "status": "succeeded"},
            {"id": "select_layout_nets", "label": "Select nets", "status": "succeeded"},
            {"id": "create_layout_cutout", "label": "Cutout", "status": "succeeded"},
        ],
    }

    run = import_cutout_summary_to_workflow_run(summary)
    data = run.to_dict()

    assert data["workflow_id"] == "import_brd_cutout_sparam_tdr_v1"
    assert data["status"] == "succeeded"
    assert [step["step_id"] for step in data["steps"]] == [
        "import_layout_file",
        "select_layout_nets",
        "create_layout_cutout",
    ]
    assert data["outputs"]["aedt_project"] == "/tmp/case_cutout.aedt"
    assert data["outputs"]["edb_path"] == "/tmp/case_cutout.aedb"
    assert data["outputs"]["signal_nets"] == ["SRDS_3_RX1_N", "SRDS_3_RX1_P"]
