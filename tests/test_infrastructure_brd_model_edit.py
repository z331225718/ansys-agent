from __future__ import annotations

import json
import copy
from collections import OrderedDict
from pathlib import Path

import pytest

from aedt_agent.infrastructure.brd_model_edit import (
    BrdModelEditAdapter,
    BrdModelEditRequest,
)
from aedt_agent.infrastructure.brd_real_build import RealAedtEnvironment


def _request(tmp_path: Path, **overrides) -> BrdModelEditRequest:
    project = tmp_path / "case.aedt"
    project.write_text("project", encoding="utf-8")
    edb = tmp_path / "case.aedb"
    edb.mkdir()
    (edb / "edb.def").write_text("edb", encoding="utf-8")
    values = {
        "project_path": project,
        "artifact_dir": tmp_path / "artifacts",
        "actions": [
            {
                "action_type": "anti_pad.enlarge",
                "parasitic_target": "reviewed_test_parasitic",
                "layers": ["L6_GND"],
                "plane_shape_ids": [101],
                "via_centers": [
                    {"x": 1.0, "y": 2.0, "unit": "mm"},
                    {"x": 1.9, "y": 2.0, "unit": "mm"},
                ],
                "target_diameter": {"value": 0.6, "unit": "mm"},
                "bridge_between_vias": True,
                "constraints": {
                    "min_diameter": {"value": 0.3, "unit": "mm"},
                    "max_diameter": {"value": 0.8, "unit": "mm"},
                },
            }
        ],
        "environment": RealAedtEnvironment(
            version="2026.1",
            edb_backend="grpc",
        ),
    }
    values.update(overrides)
    return BrdModelEditRequest(**values)


class FakePad:
    def __init__(self, shape: str, diameter: str | None = None) -> None:
        self.shape = shape
        self.parameters = (
            OrderedDict({"Diameter": diameter})
            if diameter is not None
            else OrderedDict()
        )


class FakeDefinition:
    def __init__(self) -> None:
        self.pad_by_layer = {
            "L05": FakePad("circle", "0.4mm"),
            "L06_GND": FakePad("circle", "0.4mm"),
        }


class FakePadstacks:
    def __init__(self) -> None:
        self.definitions = {"BBVIA": FakeDefinition()}
        self.instances = {
            501: FakePadstackInstance(
                501,
                "via_dp_l2",
                "DP0",
                "BBVIA_L2_L3",
                [0.001, 0.002],
                "L2_GND",
                "L3",
            ),
            502: FakePadstackInstance(
                502,
                "via_dn_l2",
                "DN0",
                "BBVIA_L2_L3",
                [0.0019, 0.002],
                "L2_GND",
                "L3",
            ),
            503: FakePadstackInstance(
                503,
                "via_dp_l1",
                "DP0",
                "BBVIA_L1_L2",
                [0.001, 0.002],
                "TOP",
                "L2_GND",
            ),
            504: FakePadstackInstance(
                504,
                "via_dn_l1",
                "DN0",
                "BBVIA_L1_L2",
                [0.0028, 0.002],
                "TOP",
                "L2_GND",
            ),
        }


class FakePadstackInstance:
    def __init__(
        self,
        instance_id: int,
        name: str,
        net_name: str,
        padstack_definition: str,
        position: list[float],
        start_layer: str,
        stop_layer: str,
    ) -> None:
        self.id = instance_id
        self.name = name
        self.net_name = net_name
        self.padstack_definition = padstack_definition
        self.position = position
        self.start_layer = start_layer
        self.stop_layer = stop_layer


class FakePolygonData:
    def __init__(self, inside: bool = True) -> None:
        self.inside = inside

    def is_inside(self, point) -> bool:
        return self.inside


class FakePrimitive:
    def __init__(
        self,
        primitive_id: int,
        layer_name: str,
        net_name: str,
        *,
        is_void: bool = False,
        inside: bool = True,
        primitive_type: str = "Polygon",
    ) -> None:
        self.id = primitive_id
        self.layer_name = layer_name
        self.net_name = net_name
        self.is_void = is_void
        self.primitive_type = primitive_type
        self.polygon_data = FakePolygonData(inside)
        self.voids = []

    def add_void(self, void_shape):
        self.voids.append(void_shape)
        return True


class FakeModeler:
    def __init__(self) -> None:
        self.primitives = [
            FakePrimitive(101, "L06_GND", "GND"),
            FakePrimitive(102, "L05", "GND"),
            FakePrimitive(202, "L06_GND", "GND", inside=False),
        ]
        self.next_id = 1000

    def get_primitives(self, **filters):
        result = self.primitives
        if "layer_name" in filters:
            result = [
                primitive
                for primitive in result
                if primitive.layer_name == filters["layer_name"]
            ]
        if "is_void" in filters:
            result = [
                primitive
                for primitive in result
                if primitive.is_void is filters["is_void"]
            ]
        return result

    def create_circle(self, layer_name, x, y, radius, net_name=""):
        primitive = FakePrimitive(
            self.next_id,
            layer_name,
            net_name,
            is_void=not bool(net_name),
        )
        primitive.center = (x, y)
        primitive.radius = radius
        self.next_id += 1
        self.primitives.append(primitive)
        return primitive

    def create_polygon(self, points, layer_name, voids=None, net_name=""):
        primitive = FakePrimitive(
            self.next_id,
            layer_name,
            net_name,
            is_void=True,
        )
        primitive.points = points
        self.next_id += 1
        return primitive

    def create_rectangle(
        self,
        layer_name,
        net_name="",
        lower_left_point="",
        upper_right_point="",
        center_point="",
        width="",
        height="",
        representation_type="lower_left_upper_right",
        corner_radius="0mm",
        rotation="0deg",
    ):
        primitive = FakePrimitive(
            self.next_id,
            layer_name,
            net_name,
            is_void=True,
        )
        primitive.lower_left_point = lower_left_point
        primitive.upper_right_point = upper_right_point
        primitive.representation_type = representation_type
        self.next_id += 1
        return primitive

    def add_void(self, shape, void_shape):
        shape.voids.append(void_shape)
        return True


class FakeEdb:
    calls: list[tuple[str, dict]] = []
    last_instance: "FakeEdb | None" = None
    stores: dict[str, dict] = {}

    def __init__(self, *, edbpath: str, version: str, grpc: bool | None):
        self.edbpath = edbpath
        self.version = version
        self.grpc = grpc
        state = FakeEdb.stores.get(edbpath)
        if state is None:
            self.padstacks = FakePadstacks()
            self.modeler = FakeModeler()
            self.design_variables = {}
            self.project_variables = {}
        else:
            self.padstacks = copy.deepcopy(state["padstacks"])
            self.modeler = copy.deepcopy(state["modeler"])
            self.design_variables = dict(state["design_variables"])
            self.project_variables = dict(state["project_variables"])
        FakeEdb.last_instance = self
        FakeEdb.calls.append(
            (
                "init",
                {
                    "edbpath": edbpath,
                    "version": version,
                    "grpc": grpc,
                },
            )
        )

    def variable_exists(self, name: str) -> bool:
        return name in self.design_variables or name in self.project_variables

    def add_design_variable(self, name: str, value: str, description: str = ""):
        self.design_variables[name] = value

    def add_project_variable(self, name: str, value: str, description=None):
        variable_name = name if name.startswith("$") else f"${name}"
        self.project_variables[variable_name] = value

    def change_design_variable_value(self, name: str, value: str):
        if name.startswith("$"):
            self.project_variables[name] = value
        else:
            self.design_variables[name] = value

    def save(self) -> None:
        FakeEdb.stores[self.edbpath] = {
            "padstacks": copy.deepcopy(self.padstacks),
            "modeler": copy.deepcopy(self.modeler),
            "design_variables": dict(self.design_variables),
            "project_variables": dict(self.project_variables),
        }
        state_text = json.dumps(
            {
                "design_variables": self.design_variables,
                "project_variables": self.project_variables,
                "primitive_count": len(self.modeler.primitives),
                "void_counts": [
                    len(primitive.voids) for primitive in self.modeler.primitives
                ],
            },
            sort_keys=True,
        )
        (Path(self.edbpath) / "edb.def").write_text(
            f"edb\n{state_text}\n",
            encoding="utf-8",
        )
        FakeEdb.calls.append(("save", {}))

    def close(self) -> None:
        FakeEdb.calls.append(("close", {}))


class FakeHfss3dLayout:
    calls: list[tuple[str, dict]] = []
    events: list[str] = []

    def __init__(
        self,
        *,
        project: str,
        version: str,
        non_graphical: bool,
        new_desktop: bool,
        close_on_exit: bool,
        remove_lock: bool,
    ) -> None:
        self.project = project
        self.variables: dict[str, str] = {}
        self.variable_manager = self
        self.calls.append(
            (
                "init",
                {
                    "project": project,
                    "version": version,
                    "non_graphical": non_graphical,
                    "new_desktop": new_desktop,
                    "close_on_exit": close_on_exit,
                    "remove_lock": remove_lock,
                },
            )
        )
        self.events.append("aedt_init")

    def __setitem__(self, name: str, value: str) -> None:
        self.variables[name] = value
        self.calls.append(("set_variable", {"name": name, "value": value}))
        self.events.append(f"aedt_set:{name}")

    def __getitem__(self, name: str) -> str:
        return self.variables[name]

    def save_project(self) -> None:
        self.calls.append(("save_project", {"project": self.project}))
        self.events.append("aedt_save")

    def release_desktop(self, **kwargs) -> None:
        self.calls.append(("release_desktop", dict(kwargs)))
        self.events.append("aedt_release")


def test_model_edit_copies_project_bundle_and_adds_antipad_voids(tmp_path):
    FakeEdb.calls = []
    request = _request(tmp_path)

    result = BrdModelEditAdapter(edb_factory=FakeEdb).run(request)

    edited_project = Path(result.edited_project_path)
    edited_edb = Path(result.edited_edb_path)
    assert edited_project.is_file()
    assert edited_edb.is_dir()
    assert (edited_edb / "edb.def").read_text(encoding="utf-8").startswith(
        "edb\n"
    )
    assert request.project_path.read_text(encoding="utf-8") == "project"
    assert FakeEdb.calls[0][1]["grpc"] is True
    shape = FakeEdb.last_instance.modeler.primitives[0]
    assert shape.id == 101
    assert len(shape.voids) == 3
    manifest = json.loads(Path(result.manifest_path).read_text(encoding="utf-8"))
    assert manifest["outputs"]["edited_edb"]["file_count"] == 1
    assert result.summary["change_count"] == 1
    change = result.summary["changes"][0]
    assert change["requested_layer"] == "L6_GND"
    assert change["layer"] == "L06_GND"
    assert change["property"] == "plane_shape_void"
    assert change["shape_presence_check"] == "passed"
    assert change["selected_shapes"][0]["id"] == 101
    assert [void["type"] for void in change["created_voids"]] == [
        "circle",
        "circle",
        "rectangle_bridge",
    ]
    assert change["created_voids"][0]["diameter_m"] == pytest.approx(0.0006)


def test_model_edit_falls_back_to_primitive_add_void_when_modeler_returns_false(
    tmp_path,
):
    class ModelerAddVoidFalse(FakeModeler):
        def add_void(self, shape, void_shape):
            return False

    class EdbWithPrimitiveVoidFallback(FakeEdb):
        def __init__(self, *, edbpath: str, version: str, grpc: bool | None):
            super().__init__(edbpath=edbpath, version=version, grpc=grpc)
            self.modeler = ModelerAddVoidFalse()

    request = _request(tmp_path)

    result = BrdModelEditAdapter(
        edb_factory=EdbWithPrimitiveVoidFallback
    ).run(request)

    shape = FakeEdb.last_instance.modeler.primitives[0]
    assert len(shape.voids) == 3
    assert result.summary["changes"][0]["created_voids"][2][
        "added_to_shapes"
    ] == [101]


def test_model_edit_rejects_path_primitives_as_void_hosts(tmp_path):
    class ModelerWithPathTrace(FakeModeler):
        def __init__(self) -> None:
            super().__init__()
            self.primitives.insert(
                0,
                FakePrimitive(
                    303,
                    "L06_GND",
                    "TX_P",
                    primitive_type="Path",
                ),
            )

    class EdbWithPathTrace(FakeEdb):
        def __init__(self, *, edbpath: str, version: str, grpc: bool | None):
            super().__init__(edbpath=edbpath, version=version, grpc=grpc)
            self.modeler = ModelerWithPathTrace()

    request = _request(
        tmp_path,
        actions=[
            {
                "action_type": "anti_pad.enlarge",
                "parasitic_target": "reviewed_test_parasitic",
                "layers": ["L6_GND"],
                "plane_shape_ids": [303],
                "via_centers": [
                    {"x": 1.0, "y": 2.0, "unit": "mm"},
                    {"x": 1.9, "y": 2.0, "unit": "mm"},
                ],
                "target_diameter": {"value": 0.6, "unit": "mm"},
            }
        ],
    )

    with pytest.raises(ValueError, match="selected plane_shape_ids"):
        BrdModelEditAdapter(edb_factory=EdbWithPathTrace).run(request)


def test_model_edit_can_modify_existing_working_project(tmp_path):
    FakeEdb.calls = []
    request = _request(tmp_path, project_copy_mode="working_project")

    result = BrdModelEditAdapter(edb_factory=FakeEdb).run(request)
    manifest = json.loads(
        Path(result.manifest_path).read_text(encoding="utf-8")
    )

    assert Path(result.edited_project_path) == request.project_path
    assert Path(result.edited_edb_path) == request.project_path.with_suffix(
        ".aedb"
    )
    assert not (request.artifact_dir / "case.edited.aedt").exists()
    assert result.summary["project_copy_mode"] == "working_project"
    assert result.summary["persistence_check"]["status"] == "passed"
    assert (
        manifest["input"]["source_edb"]["sha256"]
        != manifest["outputs"]["edited_edb"]["sha256"]
    )


def test_model_edit_rejects_when_save_does_not_persist_bundle_changes(tmp_path):
    class NonPersistingFakeEdb(FakeEdb):
        def save(self) -> None:
            FakeEdb.calls.append(("save", {}))

    request = _request(tmp_path)

    with pytest.raises(RuntimeError, match="did not persist"):
        BrdModelEditAdapter(edb_factory=NonPersistingFakeEdb).run(request)


def test_model_edit_can_add_non_functional_pad_circle_shapes(tmp_path):
    FakeEdb.calls = []
    request = _request(
        tmp_path,
        actions=[
            {
                "action_type": "non_functional_pad.add_or_enlarge",
                "layers": ["L05"],
                "center_padstack_instance_ids": [501, 502],
                "target_radius": {"value": 0.25, "unit": "mm"},
                "parameter_name": "l05_nfp_r",
                "base_diameter": {"value": 0.4, "unit": "mm"},
                "constraints": {
                    "max_delta": {"value": 0.1, "unit": "mm"},
                    "max_diameter": {"value": 0.6, "unit": "mm"},
                },
            }
        ],
    )

    result = BrdModelEditAdapter(edb_factory=FakeEdb).run(request)

    edb = FakeEdb.last_instance
    assert edb.project_variables["$l05_nfp_r"] == "0.25mm"
    change = result.summary["changes"][0]
    assert change["property"] == "signal_circle_shape"
    assert change["implementation"] == "shape"
    assert change["center_source"] == "padstack_instances"
    assert change["parameters"] == {
        "name": "l05_nfp_r",
        "value": "0.25mm",
        "scope": "project",
        "expression": "$l05_nfp_r",
    }
    assert [shape["net"] for shape in change["created_shapes"]] == [
        "DP0",
        "DN0",
    ]
    assert change["created_shapes"][0]["diameter_m"] == pytest.approx(0.0005)
    assert change["created_shapes"][0]["radius_expression"] == "$l05_nfp_r"
    primitives = edb.modeler.primitives[-2:]
    assert [(primitive.net_name, primitive.is_void) for primitive in primitives] == [
        ("DP0", False),
        ("DN0", False),
    ]


def test_model_edit_can_still_use_legacy_padstack_nfp_mode(tmp_path):
    request = _request(
        tmp_path,
        actions=[
            {
                "action_type": "non_functional_pad.add_or_enlarge",
                "implementation": "legacy_padstack",
                "padstack": "BBVIA",
                "layers": ["L05"],
                "delta": {"value": 0.05, "unit": "mm"},
                "constraints": {
                    "max_delta": {"value": 0.1, "unit": "mm"},
                    "max_diameter": {"value": 0.6, "unit": "mm"},
                },
            }
        ],
    )

    result = BrdModelEditAdapter(edb_factory=FakeEdb).run(request)

    change = result.summary["changes"][0]
    assert change["property"] == "regular_pad"
    assert change["implementation"] == "legacy_padstack"
    assert change["before"]["diameter_m"] == pytest.approx(0.0004)
    assert change["after"]["parameters"]["Diameter"].startswith("0.00045")


def test_model_edit_can_parameterize_antipad_void_radius(tmp_path):
    request = _request(
        tmp_path,
        actions=[
            {
                "action_type": "anti_pad.enlarge",
                "parasitic_target": "l2_laser_via_pad_parasitic",
                "center_padstack_instance_ids": [501, 502, 503],
                "bridge_center_padstack_instance_ids": [501, 502],
                "layers": ["L06_GND"],
                "plane_shape_ids": [101],
                "target_radius": {"value": 20, "unit": "mil"},
                "parameter_name": "l02_void_r",
                "bridge_between_vias": True,
            }
        ],
    )

    result = BrdModelEditAdapter(edb_factory=FakeEdb).run(request)

    edb = FakeEdb.last_instance
    assert edb.project_variables["$l02_void_r"] == "20mil"
    shape = edb.modeler.primitives[0]
    circle = shape.voids[0]
    bridge = shape.voids[2]
    assert circle.radius == "$l02_void_r"
    assert circle.center == (0.001, 0.002)
    assert bridge.lower_left_point == ["1.0mm", "2.0mm-1.0*$l02_void_r"]
    assert bridge.upper_right_point == ["1.9mm", "2.0mm+1.0*$l02_void_r"]
    assert "l02_void_r" in bridge.lower_left_point[1]
    change = result.summary["changes"][0]
    assert change["parameters"] == {
        "name": "l02_void_r",
        "value": "20mil",
        "scope": "project",
        "expression": "$l02_void_r",
    }
    assert change["parasitic_target"] == "l2_laser_via_pad_parasitic"
    assert change["center_source"] == "padstack_instances"
    assert [ref["id"] for ref in change["center_refs"]] == [501, 502, 503]
    assert change["center_refs"][2]["center_index"] == 0
    assert change["via_centers"] == [
        {"x": 0.001, "y": 0.002, "unit": "m"},
        {"x": 0.0019, "y": 0.002, "unit": "m"},
    ]
    assert change["created_voids"][0]["radius_expression"] == "$l02_void_r"
    assert change["created_voids"][2]["width_expression"] == "2.0*$l02_void_r"
    assert change["created_voids"][2]["length_m"] == pytest.approx(0.0009)
    assert change["created_voids"][2]["length_factor"] == 1.0
    assert change["created_voids"][2]["width_factor"] == 2.0
    assert change["created_voids"][2]["bridge_convention"] == "center_to_center_tangent_rectangle"
    assert change["created_voids"][2]["rectangle"]["parameterized"] is True
    assert change["created_voids"][2]["rectangle"]["engineering_start_point"] == [
        "1.0mm",
        "2.0mm+1.0*$l02_void_r",
    ]
    assert change["created_voids"][2]["rectangle"]["engineering_end_point"] == [
        "1.9mm",
        "2.0mm-1.0*$l02_void_r",
    ]
    assert (
        change["created_voids"][2]["rectangle"]["representation_type"]
        == "lower_left_upper_right"
    )


def test_model_edit_defines_aedt_variable_before_edb_geometry(tmp_path):
    events: list[str] = []

    class TrackingFakeEdb(FakeEdb):
        def __init__(self, *, edbpath: str, version: str, grpc: bool | None):
            events.append("edb_init")
            super().__init__(edbpath=edbpath, version=version, grpc=grpc)

    class TrackingFakeHfss3dLayout(FakeHfss3dLayout):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            events.append("aedt_init")

        def __setitem__(self, name: str, value: str) -> None:
            super().__setitem__(name, value)
            events.append(f"aedt_set:{name}:{value}")

        def save_project(self) -> None:
            super().save_project()
            events.append("aedt_save")

        def release_desktop(self, **kwargs) -> None:
            super().release_desktop(**kwargs)
            events.append("aedt_release")

    FakeEdb.calls = []
    FakeHfss3dLayout.calls = []
    request = _request(
        tmp_path,
        actions=[
            {
                "action_type": "anti_pad.enlarge",
                "parasitic_target": "l2_laser_via_pad_parasitic",
                "center_padstack_instance_ids": [501, 502],
                "layers": ["L06_GND"],
                "plane_shape_ids": [101],
                "target_radius": {"value": 20, "unit": "mil"},
                "parameter_name": "l02_void_r",
                "bridge_between_vias": True,
            }
        ],
    )

    BrdModelEditAdapter(
        edb_factory=TrackingFakeEdb,
        hfss3dlayout_factory=TrackingFakeHfss3dLayout,
    ).run(request)

    assert events[:4] == [
        "aedt_init",
        "aedt_set:$l02_void_r:20mil",
        "aedt_save",
        "aedt_release",
    ]
    assert events.index("aedt_release") < events.index("edb_init")
    init_call = dict(FakeHfss3dLayout.calls[0][1])
    assert init_call["project"].endswith("case.edited.aedt")
    assert init_call["remove_lock"] is True


def test_model_edit_rejects_multi_center_bridge_before_opening_aedt(tmp_path):
    FakeEdb.calls = []
    FakeHfss3dLayout.calls = []
    request = _request(
        tmp_path,
        edited_project_name="bad_bridge.aedt",
        actions=[
            {
                "action_type": "anti_pad.enlarge",
                "parasitic_target": "ambiguous_bridge",
                "center_padstack_instance_ids": [501, 502, 503],
                "layers": ["L06_GND"],
                "plane_shape_ids": [101],
                "target_radius": {"value": 20, "unit": "mil"},
                "parameter_name": "bad_bridge_r",
                "bridge_between_vias": True,
            }
        ],
    )

    with pytest.raises(ValueError, match="bridge_between_vias"):
        BrdModelEditAdapter(
            edb_factory=FakeEdb,
            hfss3dlayout_factory=FakeHfss3dLayout,
        ).run(request)

    assert FakeEdb.calls == []
    assert FakeHfss3dLayout.calls == []
    assert not (tmp_path / "artifacts" / "bad_bridge.aedt").exists()
    assert not (tmp_path / "artifacts" / "bad_bridge.aedb").exists()


def test_model_edit_uses_explicit_bridge_centers_for_multi_center_antipad(tmp_path):
    request = _request(
        tmp_path,
        actions=[
            {
                "action_type": "anti_pad.enlarge",
                "parasitic_target": "l2_laser_via_pad_parasitic",
                "center_padstack_instance_ids": [501, 502, 503, 504],
                "bridge_center_padstack_instance_ids": [501, 502],
                "layers": ["L06_GND"],
                "plane_shape_ids": [101],
                "target_radius": {"value": 20, "unit": "mil"},
                "parameter_name": "l02_void_r",
                "bridge_between_vias": True,
            }
        ],
    )

    result = BrdModelEditAdapter(edb_factory=FakeEdb).run(request)

    change = result.summary["changes"][0]
    bridge = change["created_voids"][3]
    assert [void["type"] for void in change["created_voids"]] == [
        "circle",
        "circle",
        "circle",
        "rectangle_bridge",
    ]
    assert change["via_centers"] == [
        {"x": 0.001, "y": 0.002, "unit": "m"},
        {"x": 0.0019, "y": 0.002, "unit": "m"},
        {"x": 0.0028, "y": 0.002, "unit": "m"},
    ]
    assert bridge["via_centers"] == [
        {"x": 0.001, "y": 0.002, "unit": "m"},
        {"x": 0.0019, "y": 0.002, "unit": "m"},
    ]
    assert [ref["id"] for ref in bridge["center_refs"]] == [501, 502]
    assert bridge["rectangle"]["engineering_start_point"] == [
        "1.0mm",
        "2.0mm+1.0*$l02_void_r",
    ]
    assert bridge["rectangle"]["engineering_end_point"] == [
        "1.9mm",
        "2.0mm-1.0*$l02_void_r",
    ]


def test_model_edit_flips_xy_for_vertical_antipad_bridge(tmp_path):
    request = _request(
        tmp_path,
        actions=[
            {
                "action_type": "anti_pad.enlarge",
                "parasitic_target": "reviewed_vertical_pair",
                "via_centers": [
                    {"x": 1.0, "y": 2.0, "unit": "mm"},
                    {"x": 1.0, "y": 2.9, "unit": "mm"},
                ],
                "layers": ["L06_GND"],
                "plane_shape_ids": [101],
                "target_radius": {"value": 20, "unit": "mil"},
                "parameter_name": "void_r",
                "bridge_between_vias": True,
            }
        ],
    )

    result = BrdModelEditAdapter(edb_factory=FakeEdb).run(request)

    change = result.summary["changes"][0]
    bridge = change["created_voids"][2]
    assert bridge["rectangle"]["orientation"] == "vertical"
    assert bridge["rectangle"]["lower_left_point"] == [
        "1.0mm-1.0*$void_r",
        "2.0mm",
    ]
    assert bridge["rectangle"]["upper_right_point"] == [
        "1.0mm+1.0*$void_r",
        "2.9mm",
    ]
    assert bridge["rectangle"]["engineering_start_point"] == [
        "1.0mm-1.0*$void_r",
        "2.0mm",
    ]
    assert bridge["rectangle"]["engineering_end_point"] == [
        "1.0mm+1.0*$void_r",
        "2.9mm",
    ]


def test_model_edit_handles_tuple_variable_api(tmp_path):
    class TupleVariableFakeEdb(FakeEdb):
        def variable_exists(self, name: str):
            return (
                name in self.design_variables
                or name in self.project_variables,
                object(),
            )

        def add_design_variable(
            self,
            name: str,
            value: str,
            is_parameter: bool = False,
            description: str = "",
        ):
            self.design_variables[name] = value
            return True, object()

        def add_project_variable(
            self,
            name: str,
            value: str,
            description: str = "",
        ):
            variable_name = name if name.startswith("$") else f"${name}"
            self.project_variables[variable_name] = value
            return True, object()

    request = _request(
        tmp_path,
        actions=[
            {
                "action_type": "anti_pad.enlarge",
                "parasitic_target": "l2_laser_via_pad_parasitic",
                "center_padstack_instance_ids": [501, 502],
                "layers": ["L06_GND"],
                "plane_shape_ids": [101],
                "target_radius": {"value": 20, "unit": "mil"},
                "parameter_name": "l02_void_r",
                "bridge_between_vias": True,
            }
        ],
    )

    BrdModelEditAdapter(edb_factory=TupleVariableFakeEdb).run(request)

    edb = FakeEdb.last_instance
    assert edb.project_variables["$l02_void_r"] == "20mil"
    assert edb.design_variables == {}


def test_model_edit_rejects_delta_only_for_antipad_void(tmp_path):
    request = _request(
        tmp_path,
        actions=[
            {
                "action_type": "anti_pad.enlarge",
                "parasitic_target": "reviewed_test_parasitic",
                "layers": ["L06_GND"],
                "plane_shape_ids": [101],
                "via_centers": [{"x": 1.0, "y": 2.0, "unit": "mm"}],
                "delta": {"value": 0.05, "unit": "mm"},
            }
        ],
    )

    with pytest.raises(ValueError, match="target_diameter or radius"):
        BrdModelEditAdapter(edb_factory=FakeEdb).run(request)


def test_model_edit_rejects_constraint_violation(tmp_path):
    request = _request(
        tmp_path,
        actions=[
            {
                "action_type": "anti_pad.enlarge",
                "parasitic_target": "reviewed_test_parasitic",
                "layers": ["L06_GND"],
                "plane_shape_ids": [101],
                "via_centers": [{"x": 1.0, "y": 2.0, "unit": "mm"}],
                "target_diameter": {"value": 0.9, "unit": "mm"},
                "constraints": {
                    "max_diameter": {"value": 0.8, "unit": "mm"}
                },
            }
        ],
    )

    with pytest.raises(ValueError, match="above max_diameter"):
        BrdModelEditAdapter(edb_factory=FakeEdb).run(request)


def test_model_edit_allows_antipad_on_any_layer_with_selected_shape(tmp_path):
    request = _request(
        tmp_path,
        actions=[
            {
                "action_type": "anti_pad.enlarge",
                "parasitic_target": "reviewed_test_parasitic",
                "layers": ["L05"],
                "plane_shape_ids": [102],
                "via_centers": [{"x": 1.0, "y": 2.0, "unit": "mm"}],
                "target_diameter": {"value": 0.6, "unit": "mm"},
            }
        ],
    )

    result = BrdModelEditAdapter(edb_factory=FakeEdb).run(request)

    assert result.summary["changes"][0]["layer"] == "L05"


def test_model_edit_keeps_legacy_antipad_layer_override_compatible(tmp_path):
    request = _request(
        tmp_path,
        actions=[
            {
                "action_type": "anti_pad.enlarge",
                "parasitic_target": "reviewed_test_parasitic",
                "layers": ["L05"],
                "plane_shape_ids": [102],
                "via_centers": [{"x": 1.0, "y": 2.0, "unit": "mm"}],
                "target_diameter": {"value": 0.6, "unit": "mm"},
                "allow_non_plane_antipad": True,
            }
        ],
    )

    result = BrdModelEditAdapter(edb_factory=FakeEdb).run(request)

    assert result.summary["changes"][0]["layer"] == "L05"


def test_model_edit_requires_shape_selection_for_antipad(tmp_path):
    request = _request(
        tmp_path,
        actions=[
            {
                "action_type": "anti_pad.enlarge",
                "parasitic_target": "reviewed_test_parasitic",
                "layers": ["L06_GND"],
                "via_centers": [{"x": 1.0, "y": 2.0, "unit": "mm"}],
                "target_diameter": {"value": 0.6, "unit": "mm"},
            }
        ],
    )

    with pytest.raises(ValueError, match="plane_shape_ids are required"):
        BrdModelEditAdapter(edb_factory=FakeEdb).run(request)


def test_model_edit_rejects_antipad_when_via_center_is_not_inside_shape(tmp_path):
    request = _request(
        tmp_path,
        actions=[
            {
                "action_type": "anti_pad.enlarge",
                "parasitic_target": "reviewed_test_parasitic",
                "layers": ["L06_GND"],
                "plane_shape_ids": [202],
                "via_centers": [{"x": 1.0, "y": 2.0, "unit": "mm"}],
                "target_diameter": {"value": 0.6, "unit": "mm"},
            }
        ],
    )

    with pytest.raises(ValueError, match="not inside any selected plane shape"):
        BrdModelEditAdapter(edb_factory=FakeEdb).run(request)


def test_model_edit_rejects_antipad_when_extra_selected_shape_is_unrelated(tmp_path):
    request = _request(
        tmp_path,
        actions=[
            {
                "action_type": "anti_pad.enlarge",
                "parasitic_target": "reviewed_test_parasitic",
                "layers": ["L06_GND"],
                "plane_shape_ids": [101, 202],
                "via_centers": [{"x": 1.0, "y": 2.0, "unit": "mm"}],
                "target_diameter": {"value": 0.6, "unit": "mm"},
            }
        ],
    )

    with pytest.raises(ValueError, match="does not contain any via center"):
        BrdModelEditAdapter(edb_factory=FakeEdb).run(request)


def test_model_edit_requires_parasitic_target_for_antipad(tmp_path):
    request = _request(
        tmp_path,
        actions=[
            {
                "action_type": "anti_pad.enlarge",
                "layers": ["L06_GND"],
                "plane_shape_ids": [101],
                "via_centers": [{"x": 1.0, "y": 2.0, "unit": "mm"}],
                "target_diameter": {"value": 0.6, "unit": "mm"},
            }
        ],
    )

    with pytest.raises(ValueError, match="parasitic_target is required"):
        BrdModelEditAdapter(edb_factory=FakeEdb).run(request)


def test_model_edit_requires_sidecar_aedb(tmp_path):
    project = tmp_path / "missing_sidecar.aedt"
    project.write_text("project", encoding="utf-8")

    with pytest.raises(FileNotFoundError, match="sidecar AEDB"):
        BrdModelEditAdapter(edb_factory=FakeEdb).run(
            _request(tmp_path, project_path=project)
        )
