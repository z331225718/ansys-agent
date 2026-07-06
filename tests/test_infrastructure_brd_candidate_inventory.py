from __future__ import annotations

from pathlib import Path

from aedt_agent.infrastructure.brd_candidate_inventory import (
    BrdCandidateInventoryAdapter,
    BrdCandidateInventoryRequest,
)
from aedt_agent.infrastructure.brd_real_build import RealAedtEnvironment


def _request(tmp_path: Path, **overrides) -> BrdCandidateInventoryRequest:
    project = tmp_path / "case.aedt"
    project.write_text("project", encoding="utf-8")
    edb = tmp_path / "case.aedb"
    edb.mkdir()
    (edb / "edb.def").write_text("edb", encoding="utf-8")
    values = {
        "project_path": project,
        "artifact_dir": tmp_path / "artifacts",
        "seed_inventory": {
            "tdr_observation_port": "Diff1",
            "anti_pad_shape_layers": ["L2_GND"],
            "non_functional_pad_layers": ["L5"],
        },
        "geometry_constraints": {
            "anti_pad": {"max_radius_mil": 22},
            "non_functional_pad": {"min_radius_mil": 7.875},
        },
        "environment": RealAedtEnvironment(version="2026.1", edb_backend="grpc"),
    }
    values.update(overrides)
    return BrdCandidateInventoryRequest(**values)


class FakePadstackInstance:
    def __init__(
        self,
        instance_id: int,
        net_name: str,
        position: list[float],
        start_layer: str,
        stop_layer: str,
    ) -> None:
        self.id = instance_id
        self.name = f"via_{instance_id}"
        self.net_name = net_name
        self.padstack_definition = "BBVIA"
        self.position = position
        self.start_layer = start_layer
        self.stop_layer = stop_layer


class FakePadstacks:
    def __init__(self) -> None:
        self.instances = {
            501: FakePadstackInstance(501, "TX_P", [0.001, 0.002], "TOP", "L5"),
            502: FakePadstackInstance(502, "TX_N", [0.0019, 0.002], "TOP", "L5"),
            900: FakePadstackInstance(900, "GND", [0.004, 0.004], "TOP", "L5"),
        }


class FakePolygonData:
    def is_inside(self, point) -> bool:
        x, y = point
        return 0.0005 <= float(x) <= 0.0025 and 0.0015 <= float(y) <= 0.0025


class FakePrimitive:
    def __init__(
        self,
        primitive_id: int = 101,
        primitive_type: str = "Polygon",
    ) -> None:
        self.id = primitive_id
        self.layer_name = "L2_GND"
        self.net_name = "GND"
        self.is_void = False
        self.primitive_type = primitive_type
        self.polygon_data = FakePolygonData()


class FakeModeler:
    def __init__(self) -> None:
        self.primitives = [
            FakePrimitive(201, "Path"),
            FakePrimitive(101, "Polygon"),
        ]

    def get_primitives(self, **filters):
        return list(self.primitives)


class FakeStackup:
    def __init__(self) -> None:
        self.signal_layers = {
            "TOP": object(),
            "L2_GND": object(),
            "L3": object(),
            "L4_GND": object(),
            "L5": object(),
        }


class FakeEdb:
    calls = []

    def __init__(self, **kwargs) -> None:
        FakeEdb.calls.append(kwargs)
        self.padstacks = FakePadstacks()
        self.modeler = FakeModeler()
        self.stackup = FakeStackup()

    def close(self) -> None:
        pass


def test_candidate_inventory_discovers_shape_ids_and_via_centers(tmp_path):
    FakeEdb.calls = []

    result = BrdCandidateInventoryAdapter(edb_factory=FakeEdb).run(
        _request(tmp_path)
    )

    assert FakeEdb.calls[0]["grpc"] is True
    inventory = result.inventory
    anti_pad = inventory["anti_pad_shape_layers"][0]
    assert anti_pad["layer"] == "L2_GND"
    assert anti_pad["plane_shape_ids"] == ["101"]
    assert "201" not in anti_pad["plane_shape_ids"]
    assert anti_pad["center_padstack_instance_ids"] == ["501", "502"]
    assert anti_pad["bridge_center_padstack_instance_ids"] == ["501", "502"]
    assert anti_pad["target_radius"] == {"value": 22.0, "unit": "mil"}
    nfp = inventory["non_functional_pad_layers"][0]
    assert nfp["layer"] == "L5"
    assert nfp["center_padstack_instance_ids"] == ["501", "502"]
    assert nfp["signal_nets"] == ["TX_N", "TX_P"]
    assert Path(result.inventory_path).is_file()
    assert Path(result.manifest_path).is_file()


def test_candidate_inventory_can_discover_layers_without_seed(tmp_path):
    result = BrdCandidateInventoryAdapter(edb_factory=FakeEdb).run(
        _request(tmp_path, seed_inventory={})
    )

    assert result.summary["anti_pad_candidate_count"] == 1
    assert result.inventory["anti_pad_shape_layers"][0]["layer"] == "L2_GND"
    assert "L2_GND" in [
        item["layer"] for item in result.inventory["non_functional_pad_layers"]
    ]
