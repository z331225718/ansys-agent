from aedt_agent.layout.ports import apply_layout_port_actions, find_uniform_line_edge_candidates, plan_layout_port_actions


class Primitive:
    def __init__(self, name, net_name, layer, edges):
        self.name = name
        self.net_name = net_name
        self.layer = layer
        self.edges = edges


def test_find_uniform_line_edge_candidates_prefers_bbox_side_and_layer():
    primitives = [
        Primitive("sig_right", "SIG_P", "ART03", [[[9.8, 2.0], [9.8, 4.0]]]),
        Primitive("sig_left", "SIG_P", "ART03", [[[1.1, 2.0], [1.1, 4.0]]]),
        Primitive("other_layer", "SIG_P", "ART04", [[[9.9, 2.0], [9.9, 4.0]]]),
    ]

    report = find_uniform_line_edge_candidates(
        primitives,
        signal_nets=["SIG_P"],
        local_cut_region={"type": "bbox", "unit": "mil", "x_min": 1, "y_min": 1, "x_max": 10, "y_max": 5},
        hint={"side": "right", "layer": "ART03", "port_type": "edge"},
    )

    assert report["status"] == "ready"
    assert report["candidates"][0]["primitive"] == "sig_right"
    assert report["candidates"][0]["edge_number"] == 0
    assert report["candidates"][0]["distance_to_side"] == 0.2


def test_find_uniform_line_edge_candidates_reports_ambiguous_candidates():
    primitives = [
        Primitive("sig_a", "SIG_P", "ART03", [[[9.8, 2.0], [9.8, 4.0]]]),
        Primitive("sig_b", "SIG_P", "ART03", [[[9.81, 2.0], [9.81, 4.0]]]),
    ]

    report = find_uniform_line_edge_candidates(
        primitives,
        signal_nets=["SIG_P"],
        local_cut_region={"type": "bbox", "unit": "mil", "x_min": 1, "y_min": 1, "x_max": 10, "y_max": 5},
        hint={"side": "right", "layer": "ART03", "port_type": "edge"},
    )

    assert report["status"] == "ambiguous"
    assert len(report["candidates"]) == 2


def test_find_uniform_line_edge_candidates_allows_one_best_edge_per_signal_net():
    primitives = [
        Primitive("sig_p", "SIG_P", "ART03", [[[9.8, 2.0], [9.8, 4.0]]]),
        Primitive("sig_n", "SIG_N", "ART03", [[[9.8, 5.0], [9.8, 7.0]]]),
    ]

    report = find_uniform_line_edge_candidates(
        primitives,
        signal_nets=["SIG_P", "SIG_N"],
        local_cut_region={"type": "bbox", "unit": "mil", "x_min": 1, "y_min": 1, "x_max": 10, "y_max": 8},
        hint={"side": "right", "layer": "ART03", "port_type": "edge"},
    )

    assert report["status"] == "ready"
    assert [candidate["net"] for candidate in report["candidates"]] == ["SIG_P", "SIG_N"]


def test_plan_layout_port_actions_uses_ready_uniform_line_edge_candidates_as_endpoint():
    report = {
        "signal_nets": ["SIG_P", "SIG_N"],
        "reference_nets": ["GND"],
        "recommended_endpoints": [
            {
                "name": "U1",
                "components": ["U1"],
                "partname": "BGA_DEVICE",
                "component_type": "ic",
                "pins": [{"pin": "A1", "net": "SIG_P", "padstack": "BALL20"}],
            }
        ],
        "uniform_line_edge_candidates": {
            "status": "ready",
            "candidates": [
                {"primitive": "sig_p_trace", "edge_number": 2, "net": "SIG_P", "layer": "ART03"},
                {"primitive": "sig_n_trace", "edge_number": 4, "net": "SIG_N", "layer": "ART03"},
            ],
        },
    }

    plan = plan_layout_port_actions(report)

    assert plan["status"] == "ready"
    assert plan["endpoint_count"] == 2
    assert plan["port_actions"][1]["strategy"] == "uniform_line_edge_port"
    assert plan["port_actions"][1]["edges"] == [
        {"primitive": "sig_p_trace", "edge_number": 2, "net": "SIG_P", "layer": "ART03"},
        {"primitive": "sig_n_trace", "edge_number": 4, "net": "SIG_N", "layer": "ART03"},
    ]


def test_apply_layout_port_actions_creates_uniform_line_edge_ports():
    calls = []

    class FakeHfss3dLayout:
        def create_edge_port(self, assignment, edge_number, **kwargs):
            calls.append(("create_edge_port", assignment, edge_number, kwargs))
            return type("Port", (), {"name": f"{assignment}_{edge_number}"})()

    result = apply_layout_port_actions(
        FakeHfss3dLayout(),
        {
            "status": "ready",
            "port_actions": [
                {
                    "strategy": "uniform_line_edge_port",
                    "port_name": "P2_uniform",
                    "edges": [
                        {"primitive": "sig_p_trace", "edge_number": 2, "net": "SIG_P"},
                        {"primitive": "sig_n_trace", "edge_number": 4, "net": "SIG_N"},
                    ],
                }
            ],
        },
    )

    assert result["status"] == "succeeded"
    assert result["created_ports"] == ["sig_p_trace_2", "sig_n_trace_4"]
    assert calls == [
        ("create_edge_port", "sig_p_trace", 2, {"is_circuit_port": True, "is_wave_port": False}),
        ("create_edge_port", "sig_n_trace", 4, {"is_circuit_port": True, "is_wave_port": False}),
    ]
