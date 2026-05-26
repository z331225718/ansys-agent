from aedt_agent.layout.import_cutout import normalize_net_patterns
from aedt_agent.layout.import_cutout import resolve_matching_nets


def test_normalize_net_patterns_accepts_string_and_list():
    assert normalize_net_patterns("SRDS_3_RX1_*") == ["SRDS_3_RX1_*"]
    assert normalize_net_patterns(["SRDS_3_RX1_P", "SRDS_3_RX1_N"]) == ["SRDS_3_RX1_P", "SRDS_3_RX1_N"]


def test_resolve_matching_nets_supports_wildcard_and_exact_names():
    available = ["GND", "SRDS_3_RX1_P", "SRDS_3_RX1_N", "SRDS_0_TX0_P"]

    assert resolve_matching_nets(["SRDS_3_RX1_*"], available) == ["SRDS_3_RX1_N", "SRDS_3_RX1_P"]
    assert resolve_matching_nets(["GND"], available) == ["GND"]
