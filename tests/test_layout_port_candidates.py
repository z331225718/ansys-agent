from aedt_agent.demo.layout_ports import (
    ComponentConnection,
    apply_edb_layout_port_actions,
    apply_layout_port_actions,
    plan_layout_port_actions,
    score_layout_port_candidates,
)


def test_score_layout_port_candidates_prefers_bga_component_and_paired_caps():
    components = [
        ComponentConnection(
            name="U1",
            partname="KILLINGTON_7T700_BGA1677",
            component_type="ic",
            layer="TOP",
            bbox=[0.145, 0.033, 0.148, 0.038],
            pins=[
                {"pin": "N1", "net": "SRDS_0_TX0_N", "position": [0.1455, 0.0347], "padstack": "BALL20"},
                {"pin": "N2", "net": "SRDS_0_TX0_P", "position": [0.1465, 0.0347], "padstack": "BALL20"},
                {"pin": "P1", "net": "GND", "position": [0.1455, 0.0337], "padstack": "BALL20"},
            ],
        ),
        ComponentConnection(
            name="C458",
            partname="CAP_N_SC0201",
            component_type="capacitor",
            layer="TOP",
            bbox=[0.0819, 0.0651, 0.0822, 0.0655],
            pins=[{"pin": "2", "net": "SRDS_0_TX0_P", "position": [0.0821, 0.0653], "padstack": "RECT14X12M2"}],
        ),
        ComponentConnection(
            name="C461",
            partname="CAP_N_SC0201",
            component_type="capacitor",
            layer="TOP",
            bbox=[0.0819, 0.0645, 0.0822, 0.0649],
            pins=[{"pin": "2", "net": "SRDS_0_TX0_N", "position": [0.0821, 0.0647], "padstack": "RECT14X12M2"}],
        ),
        ComponentConnection(
            name="J32",
            partname="RF_CONNECTOR",
            component_type="io",
            layer="BOTTOM",
            bbox=[0.09, 0.03, 0.13, 0.037],
            pins=[{"pin": "16", "net": "GND", "position": [0.1, 0.03], "padstack": "PAD"}],
        ),
        ComponentConnection(
            name="C57",
            partname="CAP_TO_GND",
            component_type="capacitor",
            layer="TOP",
            bbox=[0.145, 0.032, 0.146, 0.033],
            pins=[{"pin": "2", "net": "GND", "position": [0.1455, 0.0325], "padstack": "RECT"}],
        ),
    ]

    report = score_layout_port_candidates(components, ["SRDS_0_TX0_N", "SRDS_0_TX0_P"], ["GND"])

    assert report["recommended_endpoints"][0]["name"] == "U1"
    assert report["recommended_endpoints"][0]["kind"] == "component"
    assert report["recommended_endpoints"][1]["name"] == "C458+C461"
    assert report["recommended_endpoints"][1]["kind"] == "component_group"
    assert report["recommended_endpoints"][0]["confidence"] > 0.85
    assert any("connects all signal nets" in reason for reason in report["candidates"][0]["reasons"])
    assert "C57+U1" not in {candidate["name"] for candidate in report["candidates"]}


def test_score_layout_port_candidates_requests_user_hint_when_only_one_endpoint_found():
    components = [
        ComponentConnection(
            name="U1",
            partname="BGA_DEVICE",
            component_type="ic",
            layer="TOP",
            bbox=[0, 0, 1, 1],
            pins=[
                {"pin": "A1", "net": "P", "position": [0.1, 0.1], "padstack": "BALL"},
                {"pin": "A2", "net": "N", "position": [0.2, 0.1], "padstack": "BALL"},
            ],
        )
    ]

    report = score_layout_port_candidates(components, ["P", "N"], ["GND"])

    assert report["status"] == "needs_user_hint"
    assert len(report["recommended_endpoints"]) == 1


def test_plan_layout_port_actions_uses_cylinder_for_bga_and_toggle_via_pin_gap_port_for_connector():
    report = {
        "status": "ready",
        "signal_nets": ["SRDS_3_RX1_N", "SRDS_3_RX1_P"],
        "reference_nets": ["GND"],
        "recommended_endpoints": [
            {
                "kind": "component",
                "name": "U1",
                "components": ["U1"],
                "partname": "KILLINGTON_BGA1677",
                "component_type": "ic",
                "signal_nets": ["srds_3_rx1_n", "srds_3_rx1_p"],
                "reference_nets": ["gnd"],
                "pins": [
                    {"pin": "A10", "net": "SRDS_3_RX1_N", "position": [0.1545, 0.0467], "padstack": "BALL20"},
                    {"pin": "A12", "net": "SRDS_3_RX1_P", "position": [0.1565, 0.0467], "padstack": "BALL20"},
                    {"pin": "A11", "net": "GND", "position": [0.1555, 0.0467], "padstack": "BALL20"},
                ],
            },
            {
                "kind": "component",
                "name": "J33",
                "components": ["J33"],
                "partname": "PCIE_CABLE_X8",
                "component_type": "io",
                "signal_nets": ["srds_3_rx1_n", "srds_3_rx1_p"],
                "reference_nets": ["gnd"],
                "pins": [
                    {"pin": "16", "net": "GND", "position": [0.1000, 0.0300], "padstack": "RECT14X52M2", "start_layer": "TOP"},
                    {"pin": "25", "net": "SRDS_3_RX1_N", "position": [0.1010, 0.0300], "padstack": "RECT14X52M2", "start_layer": "TOP"},
                    {"pin": "27", "net": "SRDS_3_RX1_P", "position": [0.1020, 0.0300], "padstack": "RECT14X52M2", "start_layer": "TOP"},
                ],
            },
        ],
    }

    plan = plan_layout_port_actions(report, impedance=50)

    assert plan["status"] == "ready"
    assert plan["port_actions"][0]["strategy"] == "component_cylinder_port"
    assert plan["port_actions"][0]["requires_solder_ball_cylinders"] is True
    assert plan["port_actions"][0]["component"] == "U1"
    assert plan["port_actions"][0]["solderball_type"] == "Cyl"
    assert plan["port_actions"][0]["solderball_diameter"] == "20mil"
    assert plan["port_actions"][0]["solderball_height"] == "10mil"
    assert plan["port_actions"][1]["strategy"] == "toggle_via_pin_gap_port"
    assert plan["port_actions"][1]["component"] == "J33"
    assert plan["port_actions"][1]["api"] == "Hfss3dLayout.oeditor.ToggleViaPin"
    assert plan["port_actions"][1]["pin_pairs"][0]["signal_pin"] == "25"
    assert plan["port_actions"][1]["pin_pairs"][0]["reference_pin"] == "16"
    assert plan["port_actions"][1]["pin_pairs"][0]["reference_layer"] == "TOP"


def test_apply_edb_layout_port_actions_skips_hfss_toggle_via_pin_ports():
    calls = []

    class FakeExcitationManager:
        def create_port_between_pin_and_layer(self, **kwargs):
            calls.append(("create_port_between_pin_and_layer", kwargs))
            return type("Terminal", (), {"name": f"{kwargs['component_name']}_{kwargs['pins_name']}"})()

    class FakeEdb:
        excitation_manager = FakeExcitationManager()

    plan = {
        "status": "ready",
        "port_actions": [
            {
                "component": "J33",
                "port_name": "P2_J33",
                "strategy": "vertical_circuit_port_at_pin",
                "pin_pairs": [
                    {
                        "signal_pin": "25",
                        "signal_net": "SRDS_3_RX1_N",
                        "reference_net": "GND",
                        "reference_layer": "L2_GND",
                    },
                    {
                        "signal_pin": "27",
                        "signal_net": "SRDS_3_RX1_P",
                        "reference_net": "GND",
                        "reference_layer": "L2_GND",
                    },
                ],
                "impedance": 50,
            }
        ],
    }

    plan["port_actions"][0]["strategy"] = "toggle_via_pin_gap_port"

    result = apply_edb_layout_port_actions(FakeEdb(), plan)

    assert result["status"] == "skipped"
    assert result["created_ports"] == []
    assert calls == []


def test_apply_layout_port_actions_creates_toggle_via_pin_gap_ports():
    calls = []

    class FakeEditor:
        def __init__(self):
            self.ports = []

        def ToggleViaPin(self, args):
            calls.append(("ToggleViaPin", args))
            element = args[1]
            component, pin = element.split("-", 1)
            self.ports.append(f"{component}.{pin}.SRDS_3_RX1_N")

    class FakeModeler:
        oeditor = FakeEditor()

        def change_property(self, assignment, name, value, aedt_tab):
            calls.append(("change_property", assignment, name, value, aedt_tab))

    class FakeHfss3dLayout:
        modeler = FakeModeler()
        oeditor = modeler.oeditor

        @property
        def port_list(self):
            return list(self.oeditor.ports)

    plan = {
        "status": "ready",
        "port_actions": [
            {
                "component": "J33",
                "port_name": "P2_J33",
                "strategy": "toggle_via_pin_gap_port",
                "pin_pairs": [
                    {
                        "signal_pin": "25",
                        "signal_net": "SRDS_3_RX1_N",
                        "reference_net": "GND",
                    }
                ],
                "impedance": 50,
            }
        ],
    }

    result = apply_layout_port_actions(FakeHfss3dLayout(), plan)

    assert result["status"] == "succeeded"
    assert result["created_ports"] == ["J33.25.SRDS_3_RX1_N"]
    assert result["deferred_actions"] == []
    assert calls == [
        ("ToggleViaPin", ["NAME:elements", "J33-25"]),
        ("change_property", "Excitations:J33.25.SRDS_3_RX1_N", "HFSS Type", "Gap", "EM Design"),
    ]


def test_apply_edb_layout_port_actions_accepts_terminal_lists_and_forces_circuit_port_flag():
    terminals = [type("Terminal", (), {"name": "J33_25", "is_circuit_port": False})()]

    class FakeExcitationManager:
        def create_port_between_pin_and_layer(self, **kwargs):
            return terminals

    class FakeEdb:
        excitation_manager = FakeExcitationManager()

    plan = {
        "status": "ready",
        "port_actions": [
            {
                "component": "J33",
                "strategy": "vertical_circuit_port_at_pin",
                "pin_pairs": [
                    {
                        "signal_pin": "25",
                        "reference_net": "GND",
                        "reference_layer": "L2_GND",
                    }
                ],
            }
        ],
    }

    result = apply_edb_layout_port_actions(FakeEdb(), plan)

    assert result["status"] == "succeeded"
    assert result["created_ports"] == ["J33_25"]
    assert terminals[0].is_circuit_port is True


def test_plan_layout_port_actions_allows_user_solderball_dimensions():
    report = {
        "status": "ready",
        "signal_nets": ["P", "N"],
        "reference_nets": ["GND"],
        "recommended_endpoints": [
            {
                "kind": "component",
                "name": "U1",
                "components": ["U1"],
                "partname": "BGA_DEVICE",
                "component_type": "ic",
                "pins": [
                    {"pin": "A1", "net": "P", "position": [0, 0], "padstack": "BALL20"},
                    {"pin": "A2", "net": "N", "position": [1, 0], "padstack": "BALL20"},
                    {"pin": "A3", "net": "GND", "position": [0.5, 0], "padstack": "BALL20"},
                ],
            },
            {
                "kind": "component",
                "name": "U2",
                "components": ["U2"],
                "partname": "BGA_DEVICE",
                "component_type": "ic",
                "pins": [
                    {"pin": "B1", "net": "P", "position": [5, 0], "padstack": "BALL20"},
                    {"pin": "B2", "net": "N", "position": [6, 0], "padstack": "BALL20"},
                    {"pin": "B3", "net": "GND", "position": [5.5, 0], "padstack": "BALL20"},
                ],
            },
        ],
    }

    plan = plan_layout_port_actions(
        report,
        impedance=45,
        solderball={
            "type": "Cyl",
            "diameter": "18mil",
            "mid_diameter": "16mil",
            "height": "8mil",
            "material": "pec",
        },
    )

    action = plan["port_actions"][0]
    assert action["solderball_diameter"] == "18mil"
    assert action["solderball_mid_diameter"] == "16mil"
    assert action["solderball_height"] == "8mil"
    assert action["solderball_material"] == "pec"
    assert action["impedance"] == 45


def test_plan_layout_port_actions_requests_hint_when_pin_endpoint_has_no_reference():
    report = {
        "status": "ready",
        "signal_nets": ["P"],
        "reference_nets": ["GND"],
        "recommended_endpoints": [
            {
                "kind": "component",
                "name": "J1",
                "components": ["J1"],
                "partname": "EDGE_CONNECTOR",
                "component_type": "io",
                "signal_nets": ["p"],
                "reference_nets": [],
                "pins": [{"pin": "1", "net": "P", "position": [0.0, 0.0], "padstack": "RECT"}],
            }
        ],
    }

    plan = plan_layout_port_actions(report)

    assert plan["status"] == "needs_user_hint"
    assert plan["port_actions"][0]["strategy"] == "needs_reference_pin"


def test_apply_layout_port_actions_creates_component_and_pin_ports():
    calls = []

    class FakeGeometry:
        def __init__(self, name, net_name, edge_number):
            self.name = name
            self.net_name = net_name
            self.edge_number = edge_number

        def edge_by_point(self, point):
            calls.append(("edge_by_point", self.name, point))
            return self.edge_number

    class FakeComponent:
        def set_die_type(self, **kwargs):
            calls.append(("set_die_type", kwargs))
            return True

        def set_solderball(self, **kwargs):
            calls.append(("set_solderball", kwargs))
            return True

    class FakeHfss3dLayout:
        modeler = type(
            "Modeler",
            (),
            {
                "components": {"U1": FakeComponent()},
                "geometries": {
                    "sig_25": FakeGeometry("sig_25", "SRDS_3_RX1_N", 2),
                    "gnd_16": FakeGeometry("gnd_16", "GND", 0),
                },
            },
        )()

        def create_ports_on_component_by_nets(self, component, nets):
            calls.append(("create_ports_on_component_by_nets", component, nets))
            return [type("Port", (), {"name": f"{component}_{net}"})() for net in nets]

        def create_edge_port(self, assignment, edge_number, **kwargs):
            calls.append(("create_edge_port", assignment, edge_number, kwargs))
            return type("Port", (), {"name": f"{assignment}_{edge_number}"})()

    plan = {
        "status": "ready",
        "port_actions": [
            {
                "component": "U1",
                "port_name": "P1_U1",
                "strategy": "component_cylinder_port",
                "signal_nets": ["n", "p"],
            },
            {
                "component": "J33",
                "port_name": "P2_J33",
                "strategy": "edge_port_at_pin",
                "pin_pairs": [
                    {
                        "signal_pin": "25",
                        "signal_net": "SRDS_3_RX1_N",
                        "signal_position": [0.101, 0.03],
                        "signal_start_layer": "TOP",
                        "signal_stop_layer": "TOP",
                        "reference_pin": "16",
                        "reference_net": "GND",
                        "reference_position": [0.1, 0.03],
                    }
                ],
            },
        ],
    }

    result = apply_layout_port_actions(FakeHfss3dLayout(), plan)

    assert result["status"] == "succeeded"
    assert result["created_ports"] == ["U1_n", "U1_p", "sig_25_2"]
    assert result["deferred_actions"] == []
    assert calls == [
        ("set_die_type", {"die_type": 1, "orientation": 1}),
        (
            "set_solderball",
            {
                "solderball_type": "Cyl",
                "diameter": "0.1mm",
                "mid_diameter": "0.1mm",
                "height": "0.2mm",
                "material": "solder",
            },
        ),
        ("create_ports_on_component_by_nets", "U1", ["n", "p"]),
        ("edge_by_point", "sig_25", [0.101, 0.03]),
        ("edge_by_point", "gnd_16", [0.1, 0.03]),
        (
            "create_edge_port",
            "sig_25",
            2,
            {
                "is_circuit_port": True,
                "is_wave_port": False,
            },
        )
    ]


def test_apply_layout_port_actions_defers_bga_endpoint_when_cylinder_creation_fails():
    class FakeComponent:
        def set_die_type(self, **kwargs):
            return True

        def set_solderball(self, **kwargs):
            return False

    class FakeHfss3dLayout:
        modeler = type("Modeler", (), {"components": {"U1": FakeComponent()}})()

        def create_ports_on_component_by_nets(self, component, nets):
            raise AssertionError("port must not be created without solderball cylinders")

    plan = {
        "status": "ready",
        "port_actions": [
            {
                "component": "U1",
                "port_name": "P1_U1",
                "strategy": "component_cylinder_port",
                "signal_nets": ["n", "p"],
                "solderball_type": "Cyl",
                "solderball_diameter": "20mil",
                "solderball_height": "10mil",
            }
        ],
    }

    result = apply_layout_port_actions(FakeHfss3dLayout(), plan)

    assert result["status"] == "deferred"
    assert result["created_ports"] == []
    assert result["deferred_actions"][0]["reason"] == "solderball cylinder creation failed"


def test_apply_layout_port_actions_defers_component_endpoint_when_api_is_unavailable():
    class FakeHfss3dLayout:
        pass

    plan = {
        "status": "ready",
        "port_actions": [
            {
                "component": "U1",
                "port_name": "P1_U1",
                "strategy": "component_cylinder_port",
                "signal_nets": ["n", "p"],
            }
        ],
    }

    result = apply_layout_port_actions(FakeHfss3dLayout(), plan)

    assert result["status"] == "deferred"
    assert result["deferred_actions"][0]["component"] == "U1"


def test_apply_layout_port_actions_converts_meter_positions_to_layout_units_for_edge_lookup():
    calls = []

    class FakeGeometry:
        name = "trace"
        net_name = "SRDS_3_RX1_N"
        edges = [
            [[0.0, 0.0], [1.0, 0.0]],
            [[3976.0, 1181.0], [3977.0, 1181.0]],
        ]

    class FakeHfss3dLayout:
        modeler = type("Modeler", (), {"model_units": "mil", "geometries": {"trace": FakeGeometry()}})()

        def create_edge_port(self, assignment, edge_number, **kwargs):
            return type("Port", (), {"name": "P1"})()

    plan = {
        "status": "ready",
        "port_actions": [
            {
                "component": "J33",
                "port_name": "P1_J33",
                "strategy": "edge_port_at_pin",
                "pin_pairs": [
                    {
                        "signal_pin": "25",
                        "signal_net": "SRDS_3_RX1_N",
                        "signal_position": [0.101, 0.03],
                        "reference_pin": "16",
                        "reference_net": "GND",
                        "reference_position": [0.1, 0.03],
                    }
                ],
            }
        ],
    }

    result = apply_layout_port_actions(FakeHfss3dLayout(), plan)

    assert result["status"] == "succeeded"
    assert result["created_ports"] == ["P1"]


def test_apply_layout_port_actions_falls_back_to_component_ports_when_edge_port_fails():
    calls = []

    class FakeGeometry:
        name = "trace"
        net_name = "SRDS_3_RX1_N"
        edges = [
            [[0.0, 0.0], [1.0, 0.0]],
            [[3600.0, 3250.0], [3601.0, 3250.0]],
            [[3602.0, 3250.0], [3603.0, 3250.0]],
            [[3604.0, 3250.0], [3605.0, 3250.0]],
        ]

    class FakeHfss3dLayout:
        modeler = type("Modeler", (), {"model_units": "mil", "geometries": {"trace": FakeGeometry()}})()

        def create_edge_port(self, assignment, edge_number, **kwargs):
            calls.append(("create_edge_port", assignment, edge_number))
            raise RuntimeError("edge port creation failed")

        def create_ports_on_component_by_nets(self, component, nets):
            calls.append(("create_ports_on_component_by_nets", component, nets))
            return [type("Port", (), {"name": f"{component}_{net}"})() for net in nets]

    plan = {
        "status": "ready",
        "port_actions": [
            {
                "component": "J33",
                "port_name": "P1_J33",
                "strategy": "edge_port_at_pin",
                "signal_nets": ["srds_3_rx1_n", "srds_3_rx1_p"],
                "pin_pairs": [
                    {
                        "signal_pin": "A18",
                        "signal_net": "SRDS_3_RX1_N",
                        "signal_position": [0.091460066, 0.082600038],
                        "reference_pin": "A19",
                        "reference_net": "GND",
                        "reference_position": [0.091460066, 0.08200009],
                    }
                ],
            }
        ],
    }

    result = apply_layout_port_actions(FakeHfss3dLayout(), plan)

    assert result["status"] == "succeeded"
    assert result["created_ports"] == ["J33_srds_3_rx1_n", "J33_srds_3_rx1_p"]
    assert result["failed_actions"] == []
    assert calls[-2:] == [
        ("create_edge_port", "trace", 1),
        ("create_ports_on_component_by_nets", "J33", ["srds_3_rx1_n", "srds_3_rx1_p"]),
    ]


def test_apply_layout_port_actions_skips_degenerate_edges_when_locating_edge_port():
    class FakeGeometry:
        name = "trace"
        net_name = "SRDS_3_RX1_N"
        edges = [
            [[165.196765038168, 44.2180289545057], [165.196765038168, 44.2180289545057]],
            [[3976.0, 1181.0], [3978.0, 1181.0]],
        ]

        def edge_by_point(self, point):
            raise ZeroDivisionError("Float division by zero")

    class FakeHfss3dLayout:
        modeler = type("Modeler", (), {"model_units": "mil", "geometries": {"trace": FakeGeometry()}})()

        def create_edge_port(self, assignment, edge_number, **kwargs):
            return type("Port", (), {"name": f"{assignment}_{edge_number}"})()

    plan = {
        "status": "ready",
        "port_actions": [
            {
                "component": "J33",
                "port_name": "P1_J33",
                "strategy": "edge_port_at_pin",
                "pin_pairs": [
                    {
                        "signal_pin": "25",
                        "signal_net": "SRDS_3_RX1_N",
                        "signal_position": [0.101, 0.03],
                        "reference_pin": "16",
                        "reference_net": "GND",
                        "reference_position": [0.1, 0.03],
                    }
                ],
            }
        ],
    }

    result = apply_layout_port_actions(FakeHfss3dLayout(), plan)

    assert result["status"] == "succeeded"
    assert result["created_ports"] == ["trace_1"]


def test_apply_layout_port_actions_filters_geometries_by_editor_net_before_reading_properties():
    class BadGeometry:
        name = "component_like"

        @property
        def net_name(self):
            raise RuntimeError("Net property does not exist")

    class TraceGeometry:
        name = "trace"
        edges = [[[3976.0, 1181.0], [3978.0, 1181.0]]]

        @property
        def net_name(self):
            raise RuntimeError("must not read Net property when editor filtered by net")

    class FakeEditor:
        def FindObjects(self, key, value):
            assert (key, value) == ("Net", "SRDS_3_RX1_N")
            return ["trace"]

    class FakeHfss3dLayout:
        modeler = type(
            "Modeler",
            (),
            {
                "model_units": "mil",
                "geometries": {"component_like": BadGeometry(), "trace": TraceGeometry()},
                "oeditor": FakeEditor(),
            },
        )()

        def create_edge_port(self, assignment, edge_number, **kwargs):
            return type("Port", (), {"name": f"{assignment}_{edge_number}"})()

    plan = {
        "status": "ready",
        "port_actions": [
            {
                "component": "J33",
                "port_name": "P1_J33",
                "strategy": "edge_port_at_pin",
                "pin_pairs": [
                    {
                        "signal_pin": "25",
                        "signal_net": "SRDS_3_RX1_N",
                        "signal_position": [0.101, 0.03],
                        "reference_pin": "16",
                        "reference_net": "GND",
                        "reference_position": [0.1, 0.03],
                    }
                ],
            }
        ],
    }

    result = apply_layout_port_actions(FakeHfss3dLayout(), plan)

    assert result["status"] == "succeeded"
    assert result["created_ports"] == ["trace_0"]


def test_apply_layout_port_actions_defers_edge_port_when_no_signal_edge_is_found():
    calls = []

    class FakeHfss3dLayout:
        modeler = type("Modeler", (), {"model_units": "mil", "geometries": {}})()

        def create_edge_port(self, assignment, edge_number, **kwargs):
            calls.append(("create_edge_port", assignment, edge_number))
            return True

    plan = {
        "status": "ready",
        "port_actions": [
            {
                "component": "J33",
                "port_name": "P1_J33",
                "strategy": "edge_port_at_pin",
                "signal_nets": ["srds_3_rx1_n"],
                "pin_pairs": [
                    {
                        "signal_pin": "A18",
                        "signal_net": "SRDS_3_RX1_N",
                        "signal_position": [0.091460066, 0.082600038],
                        "signal_start_layer": "TOP",
                        "signal_stop_layer": "TOP",
                        "reference_pin": "A19",
                        "reference_net": "GND",
                        "reference_position": [0.091460066, 0.08200009],
                    }
                ],
            }
        ],
    }

    result = apply_layout_port_actions(FakeHfss3dLayout(), plan)

    assert result["status"] == "deferred"
    assert result["created_ports"] == []
    assert result["deferred_actions"][0]["reason"] == "no signal primitive edge found near pin A18"
    assert calls == []
