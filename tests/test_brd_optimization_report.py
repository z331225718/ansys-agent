from __future__ import annotations

import json

from aedt_agent.reporting.brd_optimization_report import (
    build_brd_optimization_summary,
    render_brd_optimization_report_html,
    write_brd_optimization_history_csv,
)


def test_brd_optimization_report_lists_changes_and_plots(tmp_path):
    evidence = tmp_path / "brd_channel_score_evidence.json"
    evidence.write_text(
        json.dumps(
            {
                "score": {
                    "status": "pass",
                    "return_loss_trace": "SDD11",
                    "insertion_loss_trace": "SDD21",
                    "rl_worst_db": -18.5,
                    "rl_worst_frequency_ghz": 21.0,
                    "insertion_worst_db_in_band": -4.2,
                    "tdr_observation_port": "Diff1",
                    "tdr_peak_deviation_ohm": 6.0,
                    "tdr_peak_time_ps": 120.0,
                    "plot_artifacts": {
                        "tdr": "tdr.svg",
                        "sdd11": "sdd11.svg",
                        "sdd21": "sdd21.svg",
                    },
                },
                "artifact_refs": ["channel.s4p", "tdr.csv"],
            }
        ),
        encoding="utf-8",
    )
    manifest = tmp_path / "model_edit_manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "summary": {
                    "changes": [
                        {
                            "action_type": "anti_pad.enlarge",
                            "requested_layer": "L2_GND",
                            "layer": "L02_GND",
                            "property": "plane_shape_void",
                            "implementation": "shape",
                            "parasitic_target": "l1_ball",
                            "parameters": {
                                "name": "l02_void_r",
                                "value": "20mil",
                            },
                            "selected_shapes": [{"id": 173575}],
                            "via_centers": [
                                {"x": 0.299946568, "y": 0.122, "unit": "m"}
                            ],
                            "created_voids": [
                                {
                                    "type": "circle",
                                    "center": {
                                        "x": 0.299946568,
                                        "y": 0.122,
                                        "unit": "m",
                                    },
                                    "radius_m": 0.000508,
                                    "diameter_m": 0.001016,
                                    "radius_expression": "$l02_void_r",
                                    "added_to_shapes": [173575],
                                },
                                {
                                    "type": "rectangle_bridge",
                                    "length_m": 0.0009,
                                    "width_expression": "2.0*$l02_void_r",
                                    "rectangle": {
                                        "engineering_start_point": [
                                            0.299946568,
                                            0.122508,
                                        ],
                                        "engineering_end_point": [
                                            0.300846568,
                                            0.121492,
                                        ],
                                    },
                                    "added_to_shapes": [173575],
                                },
                            ],
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    summary = build_brd_optimization_summary(
        score_evidence_paths=[evidence],
        model_edit_manifest_paths=[manifest],
    )
    html = render_brd_optimization_report_html(summary)

    assert summary["status"] == "pass"
    assert summary["change_count"] == 1
    assert summary["layout_change_instructions"][0]["shape_ids"] == "173575"
    assert summary["final_score"]["return_loss_trace"] == "SDD11"
    assert "Layout 修改指引" in html
    assert "anti_pad.enlarge" in html
    assert "20mil" in html
    assert "299.947mm" in html
    assert "173575" in html
    assert "sdd11.svg" in html
    assert "sdd21.svg" in html


def test_brd_optimization_history_csv_records_partial_solve_round(tmp_path):
    solve_result = tmp_path / "result.json"
    solve_result.write_text(
        json.dumps(
            {
                "status": "succeeded",
                "solved_project": "working.aedt",
                "touchstone_path": "channel.s4p",
                "tdr_path": "",
                "solve_manifest_path": "solve_manifest.json",
                "summary": {
                    "status": "succeeded",
                    "tdr_exported": False,
                    "touchstone_sample_count": 1341,
                    "tdr_sample_count": 0,
                    "tdr_observation_port": "Diff1",
                },
                "artifact_refs": ["channel.s4p", "solve_manifest.json"],
            }
        ),
        encoding="utf-8",
    )
    manifest = tmp_path / "model_edit_manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "summary": {
                    "changes": [
                        {
                            "action_type": "anti_pad.enlarge",
                            "requested_layer": "L2_GND",
                            "parameters": {
                                "name": "l02_void_r",
                                "value": "20mil",
                            },
                            "parasitic_target": "l1_ball_and_laser_via",
                            "center_source": "padstack_instances",
                            "created_voids": [
                                {
                                    "radius_expression": "l02_void_r",
                                    "diameter_m": 0.001016,
                                }
                            ],
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    summary = build_brd_optimization_summary(
        solve_result_paths=[solve_result],
        model_edit_manifest_paths=[manifest],
    )
    csv_path = write_brd_optimization_history_csv(
        summary,
        tmp_path / "optimization_history.csv",
    )
    csv_text = csv_path.read_text(encoding="utf-8-sig")

    assert summary["status"] == "needs_tdr_export_before_score"
    assert summary["history_rows"][0]["continue_recommendation"] == (
        "export_tdr_from_solved_model_then_score"
    )
    assert summary["history_rows"][0]["parameter_names"] == "l02_void_r"
    assert "needs_tdr_export_before_score" in csv_text
    assert "l02_void_r" in csv_text
    assert "channel.s4p" in csv_text
    assert "export_tdr_from_solved_model_then_score" in csv_text
