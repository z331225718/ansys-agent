from pathlib import Path

import aedt_agent.demo.import_cutout as import_cutout
from aedt_agent.demo.import_cutout import (
    apply_aedt_environment,
    build_import_cutout_request,
    discover_layout_files,
    expand_net_patterns,
    parse_net_patterns,
    read_tdr_csv,
    run_fake_import_cutout,
    run_real_import_cutout,
    _net_suggestions,
)


def test_parse_net_patterns_accepts_comma_and_bracket_lists():
    assert parse_net_patterns("[DQS*, CLK*, VDD]") == ["DQS*", "CLK*", "VDD"]
    assert parse_net_patterns("DQS*,CLK*, VDD") == ["DQS*", "CLK*", "VDD"]
    assert parse_net_patterns(["DQS*", "CLK*,VDD"]) == ["DQS*", "CLK*", "VDD"]


def test_expand_net_patterns_is_case_insensitive_and_preserves_board_names():
    available = ["SOC_TX0", "soc_rx0", "GND", "VDD_1V0"]

    matched = expand_net_patterns(["*soc*tx*", "SOC_RX0", "vdd_1v0"], available)

    assert matched == ["SOC_TX0", "soc_rx0", "VDD_1V0"]


def test_expand_net_patterns_falls_back_to_closest_differential_pair_for_incomplete_user_net():
    available = [
        "GND",
        "P_SRDSH_ALT",
        "SRDS_0_TX0_N",
        "SRDS_0_TX0_P",
        "SRDS_0_RX1_N",
        "SRDS_0_RX1_P",
        "SRDS_0_RX0_N",
        "SRDS_0_RX0_P",
    ]

    matched = expand_net_patterns(["SRDS_3_RX1"], available, fuzzy=True)

    assert matched == ["SRDS_0_RX1_N", "SRDS_0_RX1_P"]


def test_discover_layout_files_prefers_brd_and_mcm(tmp_path):
    (tmp_path / "board.brd").write_text("", encoding="utf-8")
    (tmp_path / "module.mcm").write_text("", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("", encoding="utf-8")

    files = discover_layout_files(tmp_path)

    assert files == [tmp_path / "board.brd", tmp_path / "module.mcm"]


def test_fake_import_cutout_writes_sparameter_and_tdr_artifacts(tmp_path):
    layout_file = tmp_path / "case.brd"
    layout_file.write_text("", encoding="utf-8")
    request = build_import_cutout_request(
        {
            "layout_file": str(layout_file),
            "signal_nets": "*tx0*",
            "reference_nets": "gnd",
            "artifact_dir": str(tmp_path / "run"),
        }
    )

    result = run_fake_import_cutout(request)
    tdr = read_tdr_csv(result["tdr"])

    assert result["status"] == "succeeded"
    assert result["signal_nets"] == ["56G_TX0_P", "56G_TX0_N"]
    assert result["reference_nets"] == ["GND"]
    assert Path(result["touchstone"]).exists()
    assert tdr["point_count"] == 6


def test_fake_import_cutout_emits_progress_events(tmp_path):
    layout_file = tmp_path / "case.brd"
    layout_file.write_text("", encoding="utf-8")
    request = build_import_cutout_request(
        {"layout_file": str(layout_file), "signal_nets": "*tx0*", "reference_nets": "gnd", "artifact_dir": str(tmp_path / "run")}
    )
    events = []

    run_fake_import_cutout(request, progress_callback=lambda event: events.append(event))

    assert [event["step_id"] for event in events if event["status"] == "running"] == [
        "import_layout_file",
        "select_layout_nets",
        "create_layout_cutout",
        "configure_layout_stackup",
        "locate_layout_port_candidates",
        "create_layout_ports",
        "create_layout_setup",
    ]
    assert events[-1]["status"] == "succeeded"


def test_build_import_cutout_request_discovers_stackup_xml_next_to_layout(tmp_path):
    layout_file = tmp_path / "case.brd"
    layout_file.write_text("", encoding="utf-8")
    stackup_xml = tmp_path / "stackup_board.xml"
    stackup_xml.write_text("<c:Control />", encoding="utf-8")

    request = build_import_cutout_request({"layout_file": str(layout_file)})

    assert request.stackup_xml == stackup_xml


def test_build_import_cutout_request_accepts_high_speed_port_and_sweep_settings(tmp_path):
    layout_file = tmp_path / "case.brd"
    layout_file.write_text("", encoding="utf-8")

    request = build_import_cutout_request(
        {
            "layout_file": str(layout_file),
            "sweep_start": "0GHz",
            "sweep_stop": "67GHz",
            "sweep_points": 501,
            "use_q3d_for_dc": True,
            "solderball_diameter": "18mil",
            "solderball_mid_diameter": "16mil",
            "solderball_height": "8mil",
            "solderball_material": "pec",
        }
    )

    assert request.sweep_start == "0GHz"
    assert request.sweep_stop == "67GHz"
    assert request.sweep_points == 501
    assert request.use_q3d_for_dc is True
    assert request.solderball == {
        "diameter": "18mil",
        "mid_diameter": "16mil",
        "height": "8mil",
        "material": "pec",
    }


def test_apply_aedt_environment_sets_versioned_roots(monkeypatch, tmp_path):
    ansysem_root = tmp_path / "v261" / "AnsysEM"
    awp_root = tmp_path / "v261"
    ansysem_root.mkdir(parents=True)

    apply_aedt_environment("2026.1", ansysem_root=str(ansysem_root), awp_root=str(awp_root))

    assert "ANSYSEM_ROOT261" in __import__("os").environ
    assert __import__("os").environ["ANSYSEM_ROOT261"] == str(ansysem_root)
    assert __import__("os").environ["AWP_ROOT261"] == str(awp_root)


def test_apply_aedt_environment_allows_platform_autodiscovery_when_roots_are_blank(monkeypatch):
    monkeypatch.delenv("ANSYSEM_ROOT261", raising=False)
    monkeypatch.delenv("AWP_ROOT261", raising=False)

    apply_aedt_environment("2026.1", ansysem_root="", awp_root="")

    assert "ANSYSEM_ROOT261" not in __import__("os").environ
    assert "AWP_ROOT261" not in __import__("os").environ


def test_apply_aedt_environment_reuses_existing_versioned_environment(monkeypatch, tmp_path):
    awp_root = tmp_path / "AWP" / "v261"
    ansysem_root = awp_root / "AnsysEM"
    ansysem_root.mkdir(parents=True)
    monkeypatch.setenv("AWP_ROOT261", str(awp_root))
    monkeypatch.setenv("ANSYSEM_ROOT261", str(ansysem_root))

    apply_aedt_environment("2026.1", ansysem_root="", awp_root="")

    assert __import__("os").environ["AWP_ROOT261"] == str(awp_root)
    assert __import__("os").environ["ANSYSEM_ROOT261"] == str(ansysem_root)


def test_cadence_launcher_requires_explicit_cdsroot(monkeypatch, tmp_path):
    launcher = tmp_path / "start_cadence.sh"
    launcher.write_text('TOOLS="/cadence/tools.lnx86"\n', encoding="utf-8")
    monkeypatch.delenv("CDSROOT", raising=False)

    try:
        import_cutout.apply_cadence_launcher_environment(launcher)
    except ValueError as exc:
        assert "CDSROOT" in str(exc)
    else:
        raise AssertionError("missing CDSROOT should fail instead of using a developer machine path")


def test_cadence_launcher_exports_aedt_roots_from_launcher(monkeypatch, tmp_path):
    cdsroot = tmp_path / "cadence"
    awp_root = tmp_path / "ansys_inc" / "v261"
    ansysem_root = awp_root / "AnsysEM"
    (cdsroot / "tools/bin").mkdir(parents=True)
    (cdsroot / "tools/pcb/bin").mkdir(parents=True)
    (cdsroot / "tools.lnx86/bin").mkdir(parents=True)
    (cdsroot / "tools/bin/extracta").write_text("", encoding="utf-8")
    ansysem_root.mkdir(parents=True)
    launcher = tmp_path / "start_aedt_cadence.sh"
    launcher.write_text(
        "\n".join(
            [
                f'CDSROOT="{cdsroot}"',
                'TOOLS="$CDSROOT/tools.lnx86"',
                f'AEDT_ROOT="${{AEDT_ROOT:-{awp_root}}}"',
                'export AWP_ROOT261="$AEDT_ROOT"',
                'export ANSYSEM_ROOT261="$AEDT_ROOT/AnsysEM"',
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv("AWP_ROOT261", raising=False)
    monkeypatch.delenv("ANSYSEM_ROOT261", raising=False)

    import_cutout.apply_cadence_launcher_environment(launcher)

    assert __import__("os").environ["AWP_ROOT261"] == str(awp_root)
    assert __import__("os").environ["ANSYSEM_ROOT261"] == str(ansysem_root)


def test_cadence_launcher_ignores_multiline_library_assignment(monkeypatch, tmp_path):
    cdsroot = tmp_path / "cadence"
    awp_root = tmp_path / "ansys_inc" / "v261"
    ansysem_root = awp_root / "AnsysEM"
    (cdsroot / "tools/bin").mkdir(parents=True)
    (cdsroot / "tools/pcb/bin").mkdir(parents=True)
    (cdsroot / "tools.lnx86/bin").mkdir(parents=True)
    (cdsroot / "tools/bin/extracta").write_text("", encoding="utf-8")
    ansysem_root.mkdir(parents=True)
    launcher = tmp_path / "start_aedt_cadence.sh"
    launcher.write_text(
        "\n".join(
            [
                f'CDSROOT="{cdsroot}"',
                'TOOLS="$CDSROOT/tools.lnx86"',
                f'AEDT_ROOT="${{AEDT_ROOT:-{awp_root}}}"',
                'export AWP_ROOT261="$AEDT_ROOT"',
                'export ANSYSEM_ROOT261="$AEDT_ROOT/AnsysEM"',
                'export LD_LIBRARY_PATH="\\',
                '$AEDT_ROOT/AnsysEM/common/mono/Linux64/lib64:\\',
                '${LD_LIBRARY_PATH:-}"',
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv("AWP_ROOT261", raising=False)
    monkeypatch.delenv("ANSYSEM_ROOT261", raising=False)

    import_cutout.apply_cadence_launcher_environment(launcher)

    assert __import__("os").environ["AWP_ROOT261"] == str(awp_root)
    assert __import__("os").environ["ANSYSEM_ROOT261"] == str(ansysem_root)


def test_net_suggestions_surface_matching_tokens_when_wildcard_misses():
    suggestions = _net_suggestions(["*56g*tx*"], ["GND", "GDDR6_VDD", "SRDS_0_TX0_N", "SRDS_0_TX0_P"])

    assert suggestions == ["SRDS_0_TX0_N", "SRDS_0_TX0_P"]


def test_real_import_cutout_uses_pyedb_cutout_before_hfss3dlayout(monkeypatch, tmp_path):
    layout_file = tmp_path / "case.brd"
    layout_file.write_text("brd", encoding="utf-8")
    stackup_xml = tmp_path / "stackup.xml"
    stackup_xml.write_text("<c:Control />", encoding="utf-8")
    ansysem_root = tmp_path / "ansys" / "v261" / "AnsysEM"
    ansysem_root.mkdir(parents=True)
    awp_root = ansysem_root.parent
    calls: list[tuple[str, object]] = []

    class FakeEdb:
        def __init__(self, edbpath, version=None, grpc=None):
            self.edbpath = str(Path(edbpath).with_suffix(".aedb"))
            self.nets = type("Nets", (), {"nets": {"GND": object(), "SRDS_0_TX0_N": object(), "SRDS_0_TX0_P": object()}})()
            self.stackup = type("Stackup", (), {"load_from_xml": lambda _, path: calls.append(("pyedb_stackup_xml", Path(path).name)) or True})()
            self.excitation_manager = type(
                "ExcitationManager",
                (),
                {
                    "create_port_between_pin_and_layer": lambda _, **kwargs: calls.append(
                        ("edb_create_vertical_circuit_port", kwargs)
                    )
                    or type("Terminal", (), {"name": f"{kwargs['component_name']}_{kwargs['pins_name']}"})()
                },
            )()
            calls.append(("edb_open", Path(edbpath).name))

        def cutout(self, **kwargs):
            Path(kwargs["output_aedb_path"]).mkdir(parents=True)
            calls.append(("cutout", kwargs["signal_nets"], kwargs["reference_nets"], kwargs["number_of_threads"]))
            return [1, 2, 3]

        def close(self):
            calls.append(("edb_close", None))

        def save(self):
            calls.append(("edb_save", None))
            return True

    class FakeHfss3dLayout:
        def __init__(self, project, version=None, non_graphical=None, new_desktop=None, close_on_exit=None):
            self.project_file = str(Path(project).with_suffix(".aedt"))
            self._ports = []
            outer = self
            fake_component = type(
                "Component",
                (),
                {
                    "set_die_type": lambda _, **kwargs: calls.append(("hfss_set_die_type", kwargs)) or True,
                    "set_solderball": lambda _, **kwargs: calls.append(("hfss_set_solderball", kwargs)) or True,
                },
            )()
            fake_signal_n = type(
                "Geometry",
                (),
                {
                    "name": "trace_n",
                    "net_name": "SRDS_0_TX0_N",
                    "edge_by_point": lambda _, point: calls.append(("hfss_edge_by_point", "trace_n", point)) or 1,
                },
            )()
            fake_signal_p = type(
                "Geometry",
                (),
                {
                    "name": "trace_p",
                    "net_name": "SRDS_0_TX0_P",
                    "edge_by_point": lambda _, point: calls.append(("hfss_edge_by_point", "trace_p", point)) or 2,
                },
            )()
            fake_ground = type(
                "Geometry",
                (),
                {
                    "name": "gnd_ref",
                    "net_name": "GND",
                    "edge_by_point": lambda _, point: calls.append(("hfss_edge_by_point", "gnd_ref", point)) or 0,
                },
            )()
            self.modeler = type(
                "Modeler",
                (),
                {
                    "components": {"U1": fake_component},
                    "geometries": {"trace_n": fake_signal_n, "trace_p": fake_signal_p, "gnd_ref": fake_ground},
                    "oeditor": type(
                        "Editor",
                        (),
                        {
                            "ImportStackupXML": lambda _, path: calls.append(("hfss_stackup_xml", Path(path).name)),
                            "ToggleViaPin": lambda _, args: calls.append(("hfss_toggle_via_pin", args))
                            or outer._ports.append(
                                f"{args[1].split('-', 1)[0]}.{args[1].split('-', 1)[1]}.SRDS_0_TX0_N"
                                if args[1].endswith("-1")
                                else f"{args[1].split('-', 1)[0]}.{args[1].split('-', 1)[1]}.SRDS_0_TX0_P"
                            ),
                        },
                    )(),
                    "change_property": lambda _, assignment, name, value, aedt_tab: calls.append(
                        ("hfss_change_property", assignment, name, value, aedt_tab)
                    ),
                },
            )()
            self.odesign = type(
                "Design",
                (),
                {
                    "EditHfssExtents": lambda _, args: calls.append(("hfss_edit_extents", args)),
                    "DesignOptions": lambda _, args, flag: calls.append(("hfss_design_options", args, flag)),
                },
            )()
            calls.append(("hfss_open", Path(project).name, non_graphical, close_on_exit))

        @property
        def port_list(self):
            return list(self._ports)

        def save_project(self):
            Path(self.project_file).write_text("aedt", encoding="utf-8")
            calls.append(("hfss_save", Path(self.project_file).name))

        def create_setup(self, name="Setup1", **kwargs):
            calls.append(("hfss_create_setup", name, kwargs))
            return name

        def create_linear_count_sweep(self, setup, unit, start_frequency, stop_frequency, num_of_freq_points, **kwargs):
            calls.append(("hfss_create_sweep", setup, unit, start_frequency, stop_frequency, num_of_freq_points, kwargs))
            return kwargs.get("name", "Sweep1")

        def create_edge_port(self, assignment, edge_number, **kwargs):
            calls.append(("hfss_create_edge_port", assignment, edge_number, kwargs))
            return type("Port", (), {"name": f"{assignment}_{edge_number}"})()

        def analyze_setup(self, name=None, **kwargs):
            raise AssertionError("BRD/MCM model-build demo must not run analyze_setup")

        def export_touchstone(self, setup=None, sweep=None, output_file=None, **kwargs):
            raise AssertionError("BRD/MCM model-build demo must not export solved Touchstone data")

        def create_ports_on_component_by_nets(self, component, nets):
            calls.append(("hfss_create_ports_on_component_by_nets", component, nets))
            return [type("Port", (), {"name": f"{component}_{net}"})() for net in nets]

        def release_desktop(self, *args, **kwargs):
            calls.append(("hfss_release", args, kwargs))

    monkeypatch.setattr(import_cutout, "_edb_class", lambda: FakeEdb, raising=False)
    monkeypatch.setattr(import_cutout, "_hfss3dlayout_class", lambda: FakeHfss3dLayout, raising=False)
    monkeypatch.setattr(
        import_cutout,
        "_locate_layout_port_candidates",
        lambda *args, **kwargs: {
            "status": "ready",
            "signal_nets": ["SRDS_0_TX0_N", "SRDS_0_TX0_P"],
            "reference_nets": ["GND"],
            "recommended_endpoints": [
                {
                    "name": "U1",
                    "components": ["U1"],
                    "partname": "BGA_DEVICE",
                    "component_type": "ic",
                    "pins": [
                        {"pin": "A1", "net": "SRDS_0_TX0_N", "position": [0, 0], "padstack": "BALL20"},
                        {"pin": "A2", "net": "SRDS_0_TX0_P", "position": [1, 0], "padstack": "BALL20"},
                        {"pin": "A3", "net": "GND", "position": [0.5, 0], "padstack": "BALL20"},
                    ],
                },
                {
                    "name": "J33",
                    "components": ["J33"],
                    "partname": "CONNECTOR",
                    "component_type": "io",
                    "pins": [
                        {"pin": "1", "net": "SRDS_0_TX0_N", "position": [5, 0], "padstack": "RECT", "start_layer": "L2_GND"},
                        {"pin": "2", "net": "SRDS_0_TX0_P", "position": [6, 0], "padstack": "RECT", "start_layer": "L2_GND"},
                        {"pin": "3", "net": "GND", "position": [5.5, 0], "padstack": "RECT", "start_layer": "L2_GND"},
                    ],
                },
            ],
            "candidates": [{"name": "U1"}, {"name": "J33"}],
        },
        raising=False,
    )
    request = build_import_cutout_request(
        {
            "layout_file": str(layout_file),
            "signal_nets": "srds_0_tx0_*",
            "reference_nets": "gnd",
            "stackup_xml": str(stackup_xml),
            "artifact_dir": str(tmp_path / "run"),
            "threads": 8,
            "sweep_start": "0GHz",
            "sweep_stop": "67GHz",
            "sweep_points": 501,
            "use_q3d_for_dc": True,
            "recorded_hfss_extents": {"OpenRegionType": "Radiation", "UseRadBound": True, "OperFreq": "5GHz"},
            "recorded_design_options": {"MeshingMethod": "PhiPlus", "PhiMesherDeltaZRatio": 100000},
            "recorded_setup_options": {"SliderType": "Balanced", "MeshSizeFactor": 1.5, "HfssMesh": True},
            "recorded_setup_advanced_settings": {"OrderBasis": -1, "MeshingMethod": "Auto", "PhiMesherDeltaZRatio": 100000},
            "recorded_setup_curve_approximation": {"ArcAngle": "10deg", "MaxPoints": 12, "UnionPolys": True},
            "recorded_sweep_options": {"UseQ3DForDC": False, "MaxSolutions": 2500, "InterpUseFullBasis": True},
            "interpolation_max_solutions": 2500,
            "solderball_diameter": "18mil",
            "solderball_mid_diameter": "16mil",
            "solderball_height": "8mil",
        }
    )

    result = run_real_import_cutout(
        request,
        aedt_version="2026.1",
        ansysem_root=str(ansysem_root),
        awp_root=str(awp_root),
        non_graphical=False,
    )

    assert result["status"] == "succeeded"
    assert result["adapter"] == "real_pyedb_cutout"
    assert result["signal_nets"] == ["SRDS_0_TX0_N", "SRDS_0_TX0_P"]
    assert result["reference_nets"] == ["GND"]
    assert result["cutout_extent_points"] == 3
    assert result["stackup_xml"] == str(stackup_xml)
    assert result["stackup_applied"] is True
    assert result["port_execution"]["status"] == "succeeded"
    assert result["port_execution"]["created_ports"] == [
        "U1_srds_0_tx0_n",
        "U1_srds_0_tx0_p",
        "J33.1.SRDS_0_TX0_N",
        "J33.2.SRDS_0_TX0_P",
    ]
    assert result["layout_setup"]["setup_name"] == "Setup1"
    assert result["layout_setup"]["sweep_name"] == "Sweep1"
    assert result["layout_setup"]["mode"] == "broadband"
    assert result["layout_setup"]["low_frequency"] == "5GHz"
    assert result["layout_setup"]["high_frequency"] == "67GHz"
    assert result["layout_setup"]["recorded_layout_settings"]["design_options"]["MeshingMethod"] == "PhiPlus"
    assert result["recorded_layout_settings"]["setup_options"]["MeshSizeFactor"] == 1.5
    assert result["recorded_layout_settings"]["setup_curve_approximation"]["MaxPoints"] == 12
    assert result["layout_solve"]["status"] == "skipped"
    assert result["layout_solve"]["reason"] == "model_build_only"
    assert result["touchstone"] == ""
    assert result["tdr"] == ""
    assert [step["step_id"] for step in result["steps"]] == [
        "import_layout_file",
        "select_layout_nets",
        "create_layout_cutout",
        "configure_layout_stackup",
        "locate_layout_port_candidates",
        "create_layout_ports",
        "create_layout_setup",
        "validate_layout_model",
    ]
    assert all(step["status"] == "succeeded" for step in result["steps"])
    assert result["port_candidates"]["port_action_plan"]["status"] == "ready"
    assert result["port_candidates"]["port_action_plan"]["port_actions"][0]["strategy"] == "component_cylinder_port"
    assert result["port_candidates"]["port_action_plan"]["port_actions"][1]["strategy"] == "toggle_via_pin_gap_port"
    assert result["edb_path"].endswith("_cutout.aedb")
    assert result["aedt_project"].endswith("_cutout_hfss.aedt")
    assert ("cutout", ["SRDS_0_TX0_N", "SRDS_0_TX0_P"], ["GND"], 8) in calls
    assert ("hfss_stackup_xml", "stackup.xml") in calls
    assert any(
        call[0] == "hfss_set_die_type"
        and call[1]["die_type"] == 1
        and call[1]["orientation"] == 1
        for call in calls
    )
    assert any(
        call[0] == "hfss_set_solderball"
        and call[1]["diameter"] == "18mil"
        and call[1]["mid_diameter"] == "16mil"
        and call[1]["height"] == "8mil"
        for call in calls
    )
    assert ("hfss_create_ports_on_component_by_nets", "U1", ["srds_0_tx0_n", "srds_0_tx0_p"]) in calls
    assert any(
        call[0] == "hfss_toggle_via_pin"
        and call[1] == ["NAME:elements", "J33-1"]
        for call in calls
    )
    assert not any(call[0] == "edb_create_vertical_circuit_port" for call in calls)
    assert not any(call[0] == "hfss_create_edge_port" for call in calls)
    assert ("pyedb_stackup_xml", "stackup.xml") not in calls
    assert any(call[0] == "hfss_open" and call[1].endswith("_cutout_hfss.aedb") and call[2] is False and call[3] is False for call in calls)
    assert any(
        call[0] == "hfss_design_options"
        and "MeshingMethod:=" in call[1]
        and "PhiPlus" in call[1]
        and "PhiMesherDeltaZRatio:=" in call[1]
        for call in calls
    )
    assert any(
        call[0] == "hfss_edit_extents"
        and "OpenRegionType:=" in call[1]
        and "Radiation" in call[1]
        for call in calls
    )
    assert any(
        call[0] == "hfss_create_setup"
        and call[1] == "Setup1"
        and call[2]["props"]["AdaptiveSettings"]["AdaptType"] == "kBroadband"
        and call[2]["props"]["SliderType"] == "Balanced"
        and call[2]["props"]["MeshSizeFactor"] == 1.5
        and call[2]["props"]["AdvancedSettings"]["OrderBasis"] == -1
        and call[2]["props"]["CurveApproximation"]["ArcAngle"] == "10deg"
        and call[2]["props"]["CurveApproximation"]["MaxPoints"] == 12
        and call[2]["props"]["AdaptiveSettings"]["BroadbandFrequencyDataList"]["AdaptiveFrequencyData"][0]["AdaptiveFrequency"] == "5GHz"
        and call[2]["props"]["AdaptiveSettings"]["BroadbandFrequencyDataList"]["AdaptiveFrequencyData"][1]["AdaptiveFrequency"] == "67GHz"
        for call in calls
    )
    assert any(
        call[0] == "hfss_create_sweep"
        and call[3] == 0.0
        and call[4] == 67.0
        and call[5] == 501
        and call[6]["sweep_type"] == "Interpolating"
        and call[6]["use_q3d_for_dc"] is False
        and call[6]["interpolation_max_solutions"] == 2500
        for call in calls
    )
    assert not any(call[0] == "hfss_analyze_setup" for call in calls)
    assert not any(call[0] == "hfss_export_touchstone" for call in calls)


def test_real_import_cutout_reports_failed_progress_when_open_layout_fails(monkeypatch, tmp_path):
    layout_file = tmp_path / "case.brd"
    layout_file.write_text("", encoding="utf-8")
    request = build_import_cutout_request({"layout_file": str(layout_file), "artifact_dir": str(tmp_path / "run")})
    events = []

    def fail_open(*args, **kwargs):
        raise RuntimeError("cannot open board")

    monkeypatch.setattr(import_cutout, "_open_layout_with_pyedb", fail_open)

    try:
        import_cutout.import_brd_with_pyedb_cutout(
            request,
            aedt_version="2026.1",
            non_graphical=False,
            progress_callback=lambda event: events.append(event),
        )
    except RuntimeError:
        pass

    assert events[0]["step_id"] == "import_layout_file"
    assert events[0]["status"] == "running"
    assert events[-1]["status"] == "failed"
    assert events[-1]["step_id"] == "import_layout_file"
    assert "cannot open board" in events[-1]["error_message"]
