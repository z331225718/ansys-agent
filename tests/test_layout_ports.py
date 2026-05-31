from aedt_agent.layout.ports import find_uniform_line_edge_candidates


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
