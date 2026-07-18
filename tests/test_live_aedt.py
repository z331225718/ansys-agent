from __future__ import annotations

import asyncio
from io import StringIO
import json
from pathlib import Path
import sys
import threading
from types import SimpleNamespace

import pytest

from aedt_agent.live.backend import LiveAedtBackend, LiveBackendError
from aedt_agent.live.approval import HmacApprovalAuthority
from aedt_agent.live.discovery import list_aedt_sessions
from aedt_agent.live.launcher import (
    AedtLaunchError,
    AedtLauncher,
    _pyaedt_grpc_server_argument,
    _pyaedt_grpc_session_ready,
)
from aedt_agent.live.manager import LiveAedtSessionManager
from aedt_agent.live.protocol import ProtocolError, WorkerRequest, WorkerResponse
from aedt_agent.live.target import AedtTarget, TargetValidationError
from aedt_agent.live.worker import serve
from aedt_agent.desktop.approval_host import ApprovalHost, DesktopApprovalStore


class FakeProject:
    def GetName(self):
        return "Board"


class FakeDesign:
    def GetName(self):
        return "Layout1"

    def GetDesignType(self):
        return "HFSS 3D Layout Design"


class FakeDesktop:
    aedt_process_id = 42
    port = 50061
    aedt_version_id = "2026.1"

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.releases = []

    @property
    def project_list(self):
        return ["Board"]

    def active_project(self):
        return FakeProject()

    def active_design(self, project):
        assert isinstance(project, FakeProject)
        return FakeDesign()

    def design_list(self, project=None):
        return ["Layout1", "HFSS1"]

    def release_desktop(self, **kwargs):
        self.releases.append(kwargs)
        return True

    def save_project(self, project_name=None):
        return project_name == "Board"


class FakeLine:
    def __init__(self, name, net, layer, width, edges=None):
        self.name = name
        self.net_name = net
        self.placement_layer = layer
        self.width = width
        self.edges = list(edges or [[[0.0, 0.0], [1.0, 0.0]]])


class FakeVia:
    def __init__(self, name):
        self.name = name
        self.start_layer = "L1"
        self.stop_layer = "L2"
        self.holediam = "0.2mm"
        self.net_name = "GND"
        self.location = [1.0, 2.0]
        self.angle = "0deg"
        self.lock_position = False


class FakeLayoutPin:
    def __init__(self, name, component_name, net_name):
        self.name = name
        self.componentname = component_name
        self.net_name = net_name
        self.start_layer = "TOP"
        self.stop_layer = "TOP"
        self.location = [0.5, 0.75]
        self.holediam = "0mm"


class FakeLayoutNet:
    def __init__(self, name, geometry_names):
        self.name = name
        self.geometry_names = geometry_names


class FakeLayoutComponent:
    def __init__(
        self,
        name,
        pins=None,
        *,
        part="R0402",
        part_type="Resistor",
        location=None,
        bounding_box=None,
    ):
        self.name = name
        self.part = part
        self.part_type = part_type
        self.enabled = True
        self.placement_layer = "TOP"
        self.location = list(location or [3.0, 4.0])
        self.bounding_box = list(bounding_box or [2.5, 3.5, 3.5, 4.5])
        self.angle = "0deg"
        self.lock_position = False
        self.pins = dict(pins or {})


class FakeLayout:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.project_name = kwargs["project"]
        self.design_name = kwargs["design"]
        lines = {
            "line1": FakeLine("line1", "N1", "L1", "0.1mm"),
            "line2": FakeLine("line2", "N2", "L2", "0.2mm"),
        }
        stackup_layers = [
            SimpleNamespace(
                name="TOP",
                type="signal",
                id=1,
                thickness=0.035,
                thickness_units="mm",
                lower_elevation=0.2,
                material="copper",
                fill_material="FR4_epoxy",
                roughness="0mm",
                etch=0.0,
                is_negative=False,
                top_bottom="top",
            ),
            SimpleNamespace(
                name="D1",
                type="dielectric",
                id=2,
                thickness=0.2,
                thickness_units="mm",
                lower_elevation=0.0,
                material="FR4_epoxy",
                fill_material="FR4_epoxy",
                roughness="0mm",
                etch=0.0,
                is_negative=False,
                top_bottom="neither",
            ),
        ]
        hole = SimpleNamespace(shape="Cir", sizes=["0.2mm"], x="0mm", y="0mm", rot="0deg")
        pad_layer = SimpleNamespace(
            id=1,
            pad=SimpleNamespace(shape="Cir", sizes=["0.4mm"], x="0mm", y="0mm", rot="0deg"),
            antipad=SimpleNamespace(shape="Cir", sizes=["0.6mm"], x="0mm", y="0mm", rot="0deg"),
            thermal=None,
            connectiondir=0,
        )
        padstacks = {
            "VIA": SimpleNamespace(
                mat="copper",
                plating=100,
                holerange="UTL",
                hole=hole,
                layers={"TOP": pad_layer},
            )
        }
        pins = {"U1-1": FakeLayoutPin("U1-1", "U1", "N1")}
        components = {"U1": FakeLayoutComponent("U1", pins)}
        nets = {
            "GND": FakeLayoutNet("GND", ["plane1"]),
            "N1": FakeLayoutNet("N1", ["line1"]),
            "N2": FakeLayoutNet("N2", ["line2"]),
        }
        self.modeler = SimpleNamespace(
            line_names=list(lines),
            lines=lines,
            components=components,
            pins=pins,
            vias={"V1": FakeVia("V1")},
            nets=nets,
            power_nets={"GND": nets["GND"]},
            signal_nets={"N1": nets["N1"], "N2": nets["N2"]},
            no_nets={},
            polygon_names=["poly1"],
            rectangle_names=[],
            circle_names=[],
            polygon_voids_names=[],
            line_voids_names=[],
            rectangle_void_names=[],
            circle_voids_names=[],
            layers=SimpleNamespace(stackup_layers=stackup_layers),
            padstacks=padstacks,
            model_units="mm",
        )
        self.variable_manager = SimpleNamespace(
            variables={"$pitch": SimpleNamespace(expression="1mm")},
            set_variable=lambda name, value, sweep=True: self._set_variable(name, value),
            delete_variable=lambda name: self.variable_manager.variables.pop(name, None) is not None,
        )
        self._setups = {"SetupL": FakeSetup("SetupL", {"Frequency": "10GHz"})}
        self.are_there_simulations_running = False
        self.excitation_names = ["P1", "P2", "P3", "P4"]

    def release_desktop(self, **kwargs):
        return True

    @property
    def existing_analysis_setups(self):
        return list(self._setups)

    def get_setup(self, name):
        return self._setups[name]

    def analyze_setup(self, setup, **kwargs):
        if setup not in self._setups:
            return False
        self.are_there_simulations_running = True
        return True

    def stop_simulations(self, clean_stop=True):
        self.are_there_simulations_running = False
        return "stopped"

    def save_diff_pairs_to_file(self, output_file):
        Path(output_file).write_text(
            "P1,P2,1,0,Diff1,100,Comm1,25\n",
            encoding="ascii",
        )
        return True

    def _set_variable(self, name, value):
        self.variable_manager.variables[name] = value
        return True


class FakePortLayout(FakeLayout):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        u1_pins = {
            "U1-1": FakeLayoutPin("U1-1", "U1", "N1"),
            "U1-2": FakeLayoutPin("U1-2", "U1", "N2"),
            "U1-3": FakeLayoutPin("U1-3", "U1", "GND"),
        }
        j1_pins = {
            "J1-1": FakeLayoutPin("J1-1", "J1", "N1"),
            "J1-2": FakeLayoutPin("J1-2", "J1", "N2"),
            "J1-3": FakeLayoutPin("J1-3", "J1", "GND"),
        }
        for index, pin in enumerate(u1_pins.values()):
            pin.location = [1.0 + index * 0.2, 1.0]
        for index, pin in enumerate(j1_pins.values()):
            pin.location = [31.0 + index * 0.2, 1.0]
        self.modeler.components = {
            "J1": FakeLayoutComponent(
                "J1",
                j1_pins,
                part="RF_CONNECTOR",
                part_type="IO",
                location=[31.0, 1.0],
                bounding_box=[30.0, 0.0, 32.0, 2.0],
            ),
            "U1": FakeLayoutComponent(
                "U1",
                u1_pins,
                part="BGA_DEVICE",
                part_type="IC",
                location=[1.0, 1.0],
                bounding_box=[0.0, 0.0, 2.0, 2.0],
            ),
        }
        self.modeler.pins = {**j1_pins, **u1_pins}

    def create_ports_on_component_by_nets(self, component, nets):
        created = []
        for pin_name, pin in self.modeler.components[component].pins.items():
            if pin.net_name not in nets:
                continue
            name = f"Port_{pin_name}"
            if name not in self.excitation_names:
                self.excitation_names.append(name)
                created.append(SimpleNamespace(name=name))
        return created

    def delete_port(self, name, remove_geometry=True):
        if name not in self.excitation_names:
            return False
        self.excitation_names.remove(name)
        return True


class FakeShortPortLayout(FakePortLayout):
    def create_ports_on_component_by_nets(self, component, nets):
        return super().create_ports_on_component_by_nets(component, list(nets)[:1])


class FakeEdgePortLayout(FakePortLayout):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.modeler.lines["line1"].placement_layer = "L1"
        self.modeler.lines["line1"].edges = [[[9.8, 2.0], [9.8, 4.0]]]
        self.modeler.lines["line2"].placement_layer = "L1"
        self.modeler.lines["line2"].edges = [[[9.7, 5.0], [9.7, 7.0]]]
        self.modeler.layers.stackup_layers.append(SimpleNamespace(name="L1"))
        reference = FakeLine(
            "reference",
            "GND",
            "L1",
            "1mm",
            edges=[[[9.5, 0.0], [9.5, 8.0]]],
        )
        self.modeler.geometries = {**self.modeler.lines, "reference": reference}
        self.edge_port_calls = []

    def create_edge_port(self, assignment, edge_number, **kwargs):
        self.edge_port_calls.append((assignment, edge_number, kwargs))
        name = f"EdgePort_{len(self.edge_port_calls)}"
        self.excitation_names.append(name)
        return SimpleNamespace(name=name)


class FakeShortEdgePortLayout(FakeEdgePortLayout):
    def create_edge_port(self, assignment, edge_number, **kwargs):
        if self.edge_port_calls:
            self.edge_port_calls.append((assignment, edge_number, kwargs))
            return False
        return super().create_edge_port(assignment, edge_number, **kwargs)


class FakeSetup:
    def __init__(self, name, properties=None):
        self.name = name
        self.props = dict(properties or {})
        self.sweeps = []

    def update(self):
        return True

    def delete_sweep(self, name):
        self.sweeps = [item for item in self.sweeps if item.name != name]
        return True


class FakeSweep:
    def __init__(self, name):
        self.name = name


class FakePost:
    def __init__(self):
        self.all_report_names = ["S Parameters"]

    def create_report(self, *, plot_name, **kwargs):
        self.all_report_names.append(plot_name)
        return SimpleNamespace(plot_name=plot_name)

    def delete_report(self, plot_name):
        self.all_report_names.remove(plot_name)
        return True

    def export_report_to_file(self, output_dir, plot_name, extension):
        path = Path(output_dir) / f"{plot_name}.{extension}"
        path.write_text("Freq,S11\n1e9,-20\n", encoding="ascii")
        return str(path)


class FakeControlledExportLayout(FakeLayout):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.post = FakePost()
        self.excitation_names = ["P1", "P2", "P3", "P4"]

    def export_touchstone(self, setup=None, sweep=None, output_file=None):
        path = Path(output_file)
        path.write_text("# Hz S RI R 50\n", encoding="ascii")
        return str(path)


class FakeObject:
    def __init__(
        self,
        *,
        object_id=9,
        material_name="copper",
        solve_inside=False,
        face_id=101,
    ):
        self.id = object_id
        self.material_name = material_name
        self.solve_inside = solve_inside
        self.faces = [SimpleNamespace(id=face_id, center=[0, 0, 0], area=1.5)]


class FakeHfssModeler:
    def __init__(self):
        self.model_units = "mm"
        self.object_names = ["box1"]
        self._objects = {"box1": FakeObject()}

    def __getitem__(self, name):
        return self._objects[name]


class FakeGeometryModeler(FakeHfssModeler):
    def __init__(self, *, fail_on=""):
        super().__init__()
        self.fail_on = fail_on
        self.calls = []

    def _create(self, kind, name, material, call):
        self.calls.append((kind, call))
        if name == self.fail_on:
            raise RuntimeError(f"synthetic {kind} failure")
        obj = FakeObject(
            object_id=10 + len(self.object_names),
            material_name=material,
            face_id=102 + len(self.object_names),
        )
        self.object_names.append(name)
        self._objects[name] = obj
        return obj

    def create_box(self, origin, sizes, name=None, material=None):
        return self._create("box", name, material, (origin, sizes, name, material))

    def create_rectangle(self, orientation, origin, sizes, name=None, material=None):
        return self._create(
            "rectangle",
            name,
            material,
            (orientation, origin, sizes, name, material),
        )

    def create_cylinder(
        self,
        orientation,
        origin,
        radius,
        height,
        num_sides=0,
        name=None,
        material=None,
    ):
        return self._create(
            "cylinder",
            name,
            material,
            (orientation, origin, radius, height, num_sides, name, material),
        )

    def create_region(self, pad_value=300, pad_type="Percentage Offset", name="Region"):
        return self._create("region", name, "vacuum", (pad_value, pad_type, name))

    def delete(self, assignment=None):
        for name in list(assignment or []):
            if name in self.object_names:
                self.object_names.remove(name)
                self._objects.pop(name, None)
        return True


class FakeBoundary:
    def __init__(self, owner, name, boundary_type, *, port=False):
        self.owner = owner
        self.name = name
        self.type = boundary_type
        self.port = port

    def delete(self):
        self.owner.boundaries = [item for item in self.owner.boundaries if item.name != self.name]
        if self.port:
            self.owner.ports = [item for item in self.owner.ports if item != self.name]
        return True


class FakeHfss:
    are_there_simulations_running = True
    solution_type = "DrivenModal"
    design_type = "HFSS"

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.project_name = kwargs["project"]
        self.design_name = kwargs["design"]
        self._setups = {"Setup1": FakeSetup("Setup1")}
        self.post = FakePost()
        self.modeler = FakeHfssModeler()
        self.ports = ["P1", "P2"]
        self.boundaries = [FakeBoundary(self, "rad1", "Radiation")]

    @property
    def existing_analysis_setups(self):
        return list(self._setups)

    @property
    def setup_names(self):
        return list(self._setups)

    def create_setup(self, name, setup_type=None):
        setup = FakeSetup(name)
        self._setups[name] = setup
        return setup

    def get_setup(self, name):
        return self._setups[name]

    def delete_setup(self, name):
        self._setups.pop(name, None)
        return True

    def create_linear_count_sweep(self, *, setup, name, **kwargs):
        sweep = FakeSweep(name)
        self._setups[setup].sweeps.append(sweep)
        return sweep

    def create_linear_step_sweep(self, *, setup, name, **kwargs):
        sweep = FakeSweep(name)
        self._setups[setup].sweeps.append(sweep)
        return sweep

    def assign_radiation_boundary_to_faces(self, assignment, name=None):
        boundary = FakeBoundary(self, name, "Radiation")
        self.boundaries.append(boundary)
        return boundary

    def wave_port(self, assignment, reference=None, name=None, **kwargs):
        boundary = FakeBoundary(self, name, "Wave Port", port=True)
        self.boundaries.append(boundary)
        self.ports.append(name)
        return boundary

    def lumped_port(self, assignment, reference=None, name=None, **kwargs):
        boundary = FakeBoundary(self, name, "Lumped Port", port=True)
        self.boundaries.append(boundary)
        self.ports.append(name)
        return boundary

    def analyze_setup(self, setup, blocking=False):
        return setup == "Setup1" and blocking is False

    def release_desktop(self, **kwargs):
        return True


class FakeControlledSolveHfss(FakeHfss):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.are_there_simulations_running = False
        self.analysis_calls = []

    def analyze_setup(self, setup, **kwargs):
        self.analysis_calls.append((setup, kwargs))
        self.are_there_simulations_running = True
        return setup == "Setup1" and kwargs.get("blocking") is False

    def stop_simulations(self, clean_stop=True):
        self.are_there_simulations_running = False
        return "Simulation stop requested"

    def export_touchstone(self, setup=None, sweep=None, output_file=None):
        path = Path(output_file)
        path.write_text("# Hz S RI R 50\n", encoding="ascii")
        return str(path)


class FakeGeometryHfss(FakeHfss):
    def __init__(self, *, geometry_fail_on="", **kwargs):
        super().__init__(**kwargs)
        self.are_there_simulations_running = False
        self.modeler = FakeGeometryModeler(fail_on=geometry_fail_on)


class FakeSubmittedSolveHfss(FakeControlledSolveHfss):
    def analyze_setup(self, setup, **kwargs):
        self.analysis_calls.append((setup, kwargs))
        self.are_there_simulations_running = False
        return setup == "Setup1" and kwargs.get("blocking") is False


class FakeRegistry:
    def __init__(self):
        self.calls = []
        self.broker_count = 1
        self.targets = set()
        self.versions = []

    def execute(self, target, command, arguments, *, version="2026.1", **kwargs):
        self.calls.append((target, command, arguments))
        self.versions.append((command, version))
        if command == "ping":
            self.targets.add((target.key, version))
            return {
                "connected": True,
                "pid": 42,
                "port": 50061,
                "version": version,
                "requested_version": version,
                "version_verified": True,
            }
        if command == "project_save_preview":
            return {"preview_id": "save-preview-1", "snapshot_digest": "save-digest-1"}
        if command == "hfss_setup_preview":
            return {"preview_id": "setup-preview-1", "snapshot_digest": "setup-digest-1"}
        if command == "hfss_report_preview":
            return {"preview_id": "report-preview-1", "snapshot_digest": "report-digest-1"}
        if command == "hfss_boundary_preview":
            return {"preview_id": "boundary-preview-1", "snapshot_digest": "boundary-digest-1"}
        if command == "hfss_analysis_start_preview":
            return {"preview_id": "analysis-preview-1", "snapshot_digest": "analysis-digest-1"}
        if command == "hfss_analysis_cancel_preview":
            return {"preview_id": "cancel-preview-1", "snapshot_digest": "cancel-digest-1"}
        if command == "hfss_export_preview":
            return {"preview_id": "export-preview-1", "snapshot_digest": "export-digest-1"}
        if command == "hfss_geometry_create_preview":
            return {"preview_id": "geometry-preview-1", "snapshot_digest": "geometry-digest-1"}
        if command == "layout_component_ports_create_preview":
            return {"preview_id": "layout-port-preview-1", "snapshot_digest": "layout-port-digest-1"}
        if command == "layout_edge_ports_create_preview":
            return {"preview_id": "edge-port-preview-1", "snapshot_digest": "edge-port-digest-1"}
        return {"command": command, **arguments}

    def release(self, target, *, version="2026.1"):
        self.calls.append((target, "release", {}))
        self.versions.append(("release", version))
        self.targets.discard((target.key, version))
        return {"released": True}

    def has_target(self, target, *, version="2026.1"):
        return (target.key, version) in self.targets

    def close(self):
        pass


def test_target_requires_exact_pid_or_port():
    assert AedtTarget.from_values(pid=42).key == "pid:42"
    assert AedtTarget.from_values(port=50061).key == "port:50061"
    with pytest.raises(TargetValidationError):
        AedtTarget.from_values()
    with pytest.raises(TargetValidationError):
        AedtTarget.from_values(pid=1, port=2)


def test_protocol_round_trip_and_strict_fields():
    request = WorkerRequest.create("ping", AedtTarget("pid", 42), {}, 10)
    assert WorkerRequest.from_json(request.to_json()) == request
    response = WorkerResponse.success(request.request_id, {"pid": 42})
    assert WorkerResponse.from_json(response.to_json()) == response
    bad = json.loads(request.to_json())
    bad["extra"] = True
    with pytest.raises(ProtocolError):
        WorkerRequest.from_json(json.dumps(bad))


def test_backend_reuses_wrappers_and_lists_live_layout_paths():
    desktop = FakeDesktop()
    backend = LiveAedtBackend(
        desktop_factory=lambda **kwargs: desktop,
        hfss_factory=FakeHfss,
        layout_factory=FakeLayout,
    )
    target = AedtTarget("pid", 42)
    assert backend.execute(target, "ping", {})["project_names"] == ["Board"]
    info = backend.execute(target, "project_info", {})
    assert info["active_design"] == "Layout1"
    assert info["design_type"] == "HFSS 3D Layout Design"
    paths = backend.execute(
        target,
        "layout_paths_list",
        {"project_name": "Board", "design_name": "Layout1", "selector": {"nets": ["N1"]}},
    )
    assert paths["count"] == 1
    assert paths["paths"][0]["width_expression"] == "0.1mm"
    width_paths = backend.execute(
        target,
        "layout_paths_list",
        {"project_name": "Board", "design_name": "Layout1", "selector": {"target_width": "0.1 MM"}},
    )
    assert [item["name"] for item in width_paths["paths"]] == ["line1"]
    assert backend.execute(
        target,
        "hfss_analysis_start",
        {"project_name": "Board", "design_name": "HFSS1", "setup_name": "Setup1"},
    )["started"] is True
    save_preview = backend.execute(target, "project_save_preview", {"project_name": "Board"})
    saved = backend.execute(target, "project_save_apply", {"preview_id": save_preview["preview_id"]})
    assert saved["project_saved"] is True
    inventory = backend.execute(
        target,
        "hfss_design_inventory",
        {"project_name": "Board", "design_name": "HFSS1"},
    )
    assert inventory["setups"] == ["Setup1"]
    assert inventory["setup_details"] == [{"name": "Setup1", "properties": {}, "sweeps": []}]
    assert inventory["ports"] == ["P1", "P2"]
    assert inventory["boundaries"] == [{"name": "rad1", "type": "Radiation"}]
    assert inventory["reports"] == ["S Parameters"]
    geometry = backend.execute(
        target,
        "hfss_geometry_inventory",
        {"project_name": "Board", "design_name": "HFSS1", "object_names": ["box1"]},
    )
    assert geometry["objects"][0]["faces"][0]["face_id"] == 101
    setup_preview = backend.execute(
        target,
        "hfss_setup_preview",
        {
            "project_name": "Board",
            "design_name": "HFSS1",
            "setup_name": "Setup2",
            "properties": {"Frequency": "10GHz", "MaximumPasses": 5},
        },
    )
    setup_result = backend.execute(target, "hfss_setup_apply", {"preview_id": setup_preview["preview_id"]})
    assert setup_result["status"] == "verified"
    assert setup_result["properties"]["Frequency"] == "10GHz"
    setup_update_preview = backend.execute(
        target,
        "hfss_setup_update_preview",
        {
            "project_name": "Board",
            "design_name": "HFSS1",
            "setup_name": "Setup1",
            "properties": {"Frequency": "28GHz", "MaximumPasses": 8},
        },
    )
    assert setup_update_preview["before"]["Frequency"]["existed"] is False
    setup_update_result = backend.execute(
        target,
        "hfss_setup_update_apply",
        {"preview_id": setup_update_preview["preview_id"]},
    )
    assert setup_update_result["after"] == {"Frequency": "28GHz", "MaximumPasses": 8}
    assert setup_update_result["project_saved"] is False
    sweep_preview = backend.execute(
        target,
        "frequency_sweep_create_preview",
        {
            "product": "hfss",
            "project_name": "Board",
            "design_name": "HFSS1",
            "setup_name": "Setup1",
            "sweep_name": "Sweep28G",
            "range_type": "LinearCount",
            "sweep_type": "Interpolating",
            "unit": "GHz",
            "start_frequency": 1,
            "stop_frequency": 40,
            "count": 401,
        },
    )
    sweep_result = backend.execute(
        target,
        "frequency_sweep_create_apply",
        {"preview_id": sweep_preview["preview_id"]},
    )
    assert sweep_result["status"] == "verified"
    assert sweep_result["sweep_name"] == "Sweep28G"
    assert sweep_result["project_saved"] is False
    setup_inventory = backend.execute(
        target,
        "setup_inventory",
        {"product": "hfss", "project_name": "Board", "design_name": "HFSS1"},
    )
    assert setup_inventory["setup_count"] == 2
    assert setup_inventory["setups"][0] == {"name": "Setup1", "sweeps": ["Sweep28G"]}
    assert setup_inventory["ports"] == ["P1", "P2"]
    assert setup_inventory["port_order_source"] == "pyaedt.ports"
    assert setup_inventory["design_unchanged"] is True
    report_preview = backend.execute(
        target,
        "hfss_report_preview",
        {
            "project_name": "Board",
            "design_name": "HFSS1",
            "report_name": "S21 Plot",
            "setup_sweep_name": "Setup2 : LastAdaptive",
            "expressions": ["dB(S(2,1))"],
        },
    )
    report_result = backend.execute(target, "hfss_report_apply", {"preview_id": report_preview["preview_id"]})
    assert report_result["status"] == "verified"
    assert report_result["report_name"] == "S21 Plot"
    boundary_preview = backend.execute(
        target,
        "hfss_boundary_preview",
        {
            "project_name": "Board",
            "design_name": "HFSS1",
            "boundary_kind": "wave_port",
            "boundary_name": "P3",
            "assignment_face_ids": [101],
            "references": ["box1"],
            "options": {"modes": 1, "impedance": 50},
        },
    )
    boundary_result = backend.execute(
        target,
        "hfss_boundary_apply",
        {"preview_id": boundary_preview["preview_id"]},
    )
    assert boundary_result["status"] == "verified"
    assert boundary_result["boundary_name"] == "P3"
    preview = backend.execute(
        target,
        "layout_width_preview",
        {
            "project_name": "Board",
            "design_name": "Layout1",
            "selector": {"nets": ["N1"], "target_width": "0.1mm"},
            "variable_name": "trace_w",
            "variable_value": "0.1mm",
        },
    )
    assert preview["approval_required"] is True
    applied = backend.execute(target, "layout_width_apply", {"preview_id": preview["preview_id"]})
    assert applied["status"] == "verified"
    assert applied["project_saved"] is False
    routing = backend.execute(
        target,
        "layout_routing_inventory",
        {"project_name": "Board", "design_name": "Layout1", "selector": {}},
    )
    assert routing["nets"] == ["N1", "N2"]
    assert routing["layers"] == ["L1", "L2"]
    assert routing["variable_count"] == 2
    assert routing["variables"][0] == {"name": "$pitch", "expression": "1mm", "scope": "project"}
    assert routing["design_unchanged"] is True
    technology = backend.execute(
        target,
        "layout_technology_inventory",
        {
            "project_name": "Board",
            "design_name": "Layout1",
            "max_items": 100,
            "include_padstack_layers": True,
        },
    )
    assert technology["counts"] == {
        "stackup_layers": 2,
        "padstacks": 1,
        "ports": 4,
        "differential_pairs": 1,
    }
    assert technology["stackup"][0]["name"] == "TOP"
    assert technology["stackup"][0]["material"] == "copper"
    assert technology["padstacks"][0]["hole"]["sizes"] == ["0.2mm"]
    assert technology["padstacks"][0]["layers"][0]["antipad"]["sizes"] == ["0.6mm"]
    assert technology["ports"] == ["P1", "P2", "P3", "P4"]
    assert technology["differential_pairs"] == [
        {
            "positive_terminal": "P1",
            "negative_terminal": "P2",
            "active": True,
            "matched": False,
            "differential_mode": "Diff1",
            "differential_reference_ohm": 100.0,
            "common_mode": "Comm1",
            "common_reference_ohm": 25.0,
        }
    ]
    assert technology["unavailable_sections"] == []
    assert technology["design_unchanged"] is True
    bounded_technology = backend.execute(
        target,
        "layout_technology_inventory",
        {
            "project_name": "Board",
            "design_name": "Layout1",
            "max_items": 1,
            "include_padstack_layers": False,
        },
    )
    assert bounded_technology["counts"]["stackup_layers"] == 1
    assert bounded_technology["counts"]["ports"] == 1
    assert {item["section"] for item in bounded_technology["unavailable_sections"]} == {
        "stackup",
        "ports",
    }
    with pytest.raises(LiveBackendError, match="max_items"):
        backend.execute(
            target,
            "layout_technology_inventory",
            {
                "project_name": "Board",
                "design_name": "Layout1",
                "max_items": 0,
            },
        )
    connectivity = backend.execute(
        target,
        "layout_connectivity_inventory",
        {
            "project_name": "Board",
            "design_name": "Layout1",
            "selector": {"nets": ["N1"]},
            "max_items": 100,
            "include_geometry_names": True,
        },
    )
    assert connectivity["model_units"] == "mm"
    assert connectivity["counts"] == {"nets": 1, "components": 1, "pins": 1, "vias": 0}
    assert connectivity["nets"] == [
        {
            "name": "N1",
            "class": "signal",
            "component_count": 1,
            "pin_count": 1,
            "via_count": 0,
            "geometry_count": 1,
            "geometry_names": ["line1"],
            "geometry_status": "complete",
        }
    ]
    assert connectivity["components"][0]["name"] == "U1"
    assert connectivity["components"][0]["pin_count"] == 1
    assert connectivity["pins"][0]["component_name"] == "U1"
    assert connectivity["pins"][0]["net_name"] == "N1"
    assert connectivity["truncated_sections"] == []
    assert connectivity["unavailable_sections"] == []
    assert connectivity["design_unchanged"] is True
    component_connectivity = backend.execute(
        target,
        "layout_connectivity_inventory",
        {
            "project_name": "Board",
            "design_name": "Layout1",
            "selector": {"components": ["U1"]},
            "max_items": 100,
        },
    )
    assert component_connectivity["selector"] == {"nets": [], "components": ["U1"]}
    assert [item["name"] for item in component_connectivity["nets"]] == ["N1"]
    assert component_connectivity["nets"][0]["geometry_status"] == "not_requested"
    empty_intersection = backend.execute(
        target,
        "layout_connectivity_inventory",
        {
            "project_name": "Board",
            "design_name": "Layout1",
            "selector": {"nets": ["N2"], "components": ["U1"]},
        },
    )
    assert empty_intersection["counts"] == {"nets": 1, "components": 0, "pins": 0, "vias": 0}
    bounded_connectivity = backend.execute(
        target,
        "layout_connectivity_inventory",
        {
            "project_name": "Board",
            "design_name": "Layout1",
            "max_items": 1,
        },
    )
    assert bounded_connectivity["counts"]["nets"] == 3
    assert bounded_connectivity["returned_counts"]["nets"] == 1
    assert bounded_connectivity["truncated_sections"] == ["nets"]
    with pytest.raises(LiveBackendError, match="unknown layout net"):
        backend.execute(
            target,
            "layout_connectivity_inventory",
            {
                "project_name": "Board",
                "design_name": "Layout1",
                "selector": {"nets": ["DOES_NOT_EXIST"]},
            },
        )
    with pytest.raises(LiveBackendError, match="unsupported layout connectivity selector"):
        backend.execute(
            target,
            "layout_connectivity_inventory",
            {
                "project_name": "Board",
                "design_name": "Layout1",
                "selector": {"layers": ["TOP"]},
            },
        )
    with pytest.raises(LiveBackendError, match="must be a list"):
        backend.execute(
            target,
            "layout_connectivity_inventory",
            {
                "project_name": "Board",
                "design_name": "Layout1",
                "selector": {"nets": "N1"},
            },
        )
    objects = backend.execute(
        target,
        "layout_object_inventory",
        {"project_name": "Board", "design_name": "Layout1"},
    )
    assert objects["categories"]["components"] == {"count": 1, "names": ["U1"]}
    assert objects["categories"]["vias"] == {"count": 1, "names": ["V1"]}
    assert objects["categories"]["polygons"] == {"count": 1, "names": ["poly1"]}
    assert objects["unavailable_categories"] == []
    via_properties = backend.execute(
        target,
        "layout_object_property_inventory",
        {
            "project_name": "Board",
            "design_name": "Layout1",
            "object_kind": "via",
            "names": ["V1"],
        },
    )
    assert via_properties["objects"][0]["properties"]["holediam"] == "0.2mm"
    component_preview = backend.execute(
        target,
        "layout_object_property_update_preview",
        {
            "project_name": "Board",
            "design_name": "Layout1",
            "object_kind": "component",
            "names": ["U1"],
            "properties": {"location": [5.0, 6.0], "angle": "90deg", "lock_position": True},
        },
    )
    component_result = backend.execute(
        target,
        "layout_object_property_update_apply",
        {"preview_id": component_preview["preview_id"]},
    )
    assert component_result["status"] == "verified"
    assert component_result["after"][0]["properties"] == {
        "location": [5.0, 6.0],
        "angle": "90deg",
        "lock_position": True,
    }
    assert component_result["project_saved"] is False
    variable_preview = backend.execute(
        target,
        "variable_upsert_preview",
        {
            "product": "layout",
            "project_name": "Board",
            "design_name": "Layout1",
            "variable_name": "W_test",
            "expression": "4.3mil",
        },
    )
    assert variable_preview["existed"] is False
    variable_result = backend.execute(
        target,
        "variable_upsert_apply",
        {"preview_id": variable_preview["preview_id"]},
    )
    assert variable_result["after_expression"] == "4.3mil"
    assert variable_result["project_saved"] is False
    variable_inventory = backend.execute(
        target,
        "variable_inventory",
        {"product": "layout", "project_name": "Board", "design_name": "Layout1"},
    )
    assert variable_inventory["count"] == 3
    assert variable_inventory["variables"][-1]["name"] == "trace_w"
    layout_analysis_preview = backend.execute(
        target,
        "hfss_analysis_start_preview",
        {
            "product": "layout",
            "project_name": "Board",
            "design_name": "Layout1",
            "setup_name": "SetupL",
            "cores": 4,
            "tasks": 1,
            "gpus": 0,
        },
    )
    assert layout_analysis_preview["product"] == "layout"
    layout_analysis = backend.execute(
        target,
        "hfss_analysis_start_apply",
        {"preview_id": layout_analysis_preview["preview_id"]},
    )
    assert layout_analysis["started"] is True
    layout_status = backend.execute(
        target,
        "hfss_analysis_status",
        {"product": "layout", "project_name": "Board", "design_name": "Layout1", "setup_name": "SetupL"},
    )
    assert layout_status["product"] == "layout"
    assert layout_status["running"] is True
    layout_cancel_preview = backend.execute(
        target,
        "hfss_analysis_cancel_preview",
        {"product": "layout", "project_name": "Board", "design_name": "Layout1", "setup_name": "SetupL"},
    )
    backend.execute(target, "hfss_analysis_cancel_apply", {"preview_id": layout_cancel_preview["preview_id"]})
    backend.release()
    assert desktop.releases[-1] == {"close_projects": False, "close_on_exit": False}


def test_backend_creates_typed_hfss_geometry_batch_with_readback():
    apps = []

    def factory(**kwargs):
        app = FakeGeometryHfss(**kwargs)
        apps.append(app)
        return app

    backend = LiveAedtBackend(
        desktop_factory=FakeDesktop,
        hfss_factory=factory,
    )
    target = AedtTarget("pid", 42)
    primitives = [
        {
            "kind": "box",
            "name": "Substrate",
            "origin": ["-25mm", "-10mm", 0],
            "size": ["50mm", "20mm", "1.6mm"],
            "material": "FR4_epoxy",
            "solve_inside": True,
        },
        {
            "kind": "rectangle",
            "name": "Trace",
            "orientation": "XY",
            "origin": ["-20mm", "-1mm", "1.6mm"],
            "size": ["40mm", "2mm"],
            "material": "copper",
            "solve_inside": False,
        },
        {
            "kind": "cylinder",
            "name": "Via",
            "axis": "Z",
            "origin": [0, 0, 0],
            "radius": "0.2mm",
            "height": "1.6mm",
            "num_sides": 12,
            "material": "copper",
        },
        {
            "kind": "region",
            "name": "AirBox",
            "padding": ["10mm"] * 6,
            "padding_type": "Absolute Offset",
        },
    ]
    preview = backend.execute(
        target,
        "hfss_geometry_create_preview",
        {
            "project_name": "Board",
            "design_name": "HFSS1",
            "primitives": primitives,
            "max_new_objects": 4,
        },
    )
    assert preview["requested_object_names"] == ["Substrate", "Trace", "Via", "AirBox"]
    assert preview["expected_object_count"] == 4
    assert preview["model_units"] == "mm"
    assert preview["project_dirty"] is False
    result = backend.execute(
        target,
        "hfss_geometry_create_apply",
        {"preview_id": preview["preview_id"]},
    )
    assert result["status"] == "verified"
    assert result["created_object_names"] == ["Substrate", "Trace", "Via", "AirBox"]
    assert result["created_object_count"] == 4
    assert result["project_saved"] is False
    assert [item["name"] for item in result["objects"]] == [
        "Substrate",
        "Trace",
        "Via",
        "AirBox",
    ]
    app = apps[0]
    assert app.modeler._objects["Substrate"].solve_inside is True
    assert app.modeler._objects["Trace"].solve_inside is False
    assert [item[0] for item in app.modeler.calls] == ["box", "rectangle", "cylinder", "region"]


def test_backend_hfss_geometry_batch_rolls_back_and_rejects_stale_preview():
    apps = []

    def factory(**kwargs):
        app = FakeGeometryHfss(geometry_fail_on="Bad", **kwargs)
        apps.append(app)
        return app

    backend = LiveAedtBackend(desktop_factory=FakeDesktop, hfss_factory=factory)
    target = AedtTarget("pid", 42)
    primitives = [
        {
            "kind": "box",
            "name": "Good",
            "origin": [0, 0, 0],
            "size": [1, 1, 1],
            "material": "copper",
        },
        {
            "kind": "box",
            "name": "Bad",
            "origin": [1, 0, 0],
            "size": [1, 1, 1],
            "material": "copper",
        },
    ]
    preview = backend.execute(
        target,
        "hfss_geometry_create_preview",
        {
            "project_name": "Board",
            "design_name": "HFSS1",
            "primitives": primitives,
        },
    )
    with pytest.raises(LiveBackendError, match="synthetic box failure"):
        backend.execute(
            target,
            "hfss_geometry_create_apply",
            {"preview_id": preview["preview_id"]},
        )
    assert apps[0].modeler.object_names == ["box1"]

    apps[0].modeler.fail_on = ""
    stale = backend.execute(
        target,
        "hfss_geometry_create_preview",
        {
            "project_name": "Board",
            "design_name": "HFSS1",
            "primitives": [primitives[0]],
        },
    )
    apps[0].modeler._create("box", "External", "vacuum", ())
    with pytest.raises(LiveBackendError, match="stale HFSS geometry"):
        backend.execute(
            target,
            "hfss_geometry_create_apply",
            {"preview_id": stale["preview_id"]},
        )


@pytest.mark.parametrize(
    "primitives,error",
    [
        ([], "non-empty list"),
        ([{"kind": "sphere", "name": "S"}], "unsupported HFSS primitive"),
        (
            [
                {
                    "kind": "box",
                    "name": "B",
                    "origin": [0, 0, 0],
                    "size": [1, -1, 1],
                }
            ],
            "positive and finite",
        ),
        (
            [
                {
                    "kind": "rectangle",
                    "name": "R",
                    "orientation": "AB",
                    "origin": [0, 0, 0],
                    "size": [1, 1],
                }
            ],
            "orientation",
        ),
        (
            [
                {
                    "kind": "box",
                    "name": "B;Delete",
                    "origin": [0, 0, 0],
                    "size": [1, 1, 1],
                }
            ],
            "safe AEDT object name",
        ),
        (
            [
                {"kind": "region", "name": "AirBox", "padding": "10mm"},
                {
                    "kind": "box",
                    "name": "B",
                    "origin": [0, 0, 0],
                    "size": [1, 1, 1],
                },
            ],
            "region must be the last primitive",
        ),
    ],
)
def test_backend_hfss_geometry_preview_rejects_invalid_primitives(primitives, error):
    backend = LiveAedtBackend(desktop_factory=FakeDesktop, hfss_factory=FakeGeometryHfss)
    with pytest.raises(LiveBackendError, match=error):
        backend.execute(
            AedtTarget("pid", 42),
            "hfss_geometry_create_preview",
            {
                "project_name": "Board",
                "design_name": "HFSS1",
                "primitives": primitives,
            },
        )


def test_backend_hfss_geometry_preview_rejects_non_hfss_design_type():
    class WrongDesignType(FakeGeometryHfss):
        design_type = "HFSS 3D Layout Design"

    backend = LiveAedtBackend(desktop_factory=FakeDesktop, hfss_factory=WrongDesignType)
    with pytest.raises(LiveBackendError, match="requires an HFSS 3D design"):
        backend.execute(
            AedtTarget("pid", 42),
            "hfss_geometry_create_preview",
            {
                "project_name": "Board",
                "design_name": "HFSS1",
                "primitives": [
                    {
                        "kind": "box",
                        "name": "B",
                        "origin": [0, 0, 0],
                        "size": [1, 1, 1],
                    }
                ],
            },
        )


def test_backend_scores_live_port_candidates_and_creates_component_ports_with_readback():
    desktop = FakeDesktop()
    backend = LiveAedtBackend(
        desktop_factory=lambda **kwargs: desktop,
        hfss_factory=FakeHfss,
        layout_factory=FakePortLayout,
    )
    target = AedtTarget("pid", 42)
    candidates = backend.execute(
        target,
        "layout_port_candidate_inventory",
        {
            "project_name": "Board",
            "design_name": "Layout1",
            "signal_nets": ["N1", "N2"],
            "reference_nets": ["GND"],
            "max_candidates": 10,
        },
    )
    assert candidates["status"] == "ready"
    assert candidates["component_count"] == 2
    assert [item["name"] for item in candidates["recommended_endpoints"]] == ["U1", "J1"]
    assert candidates["recommended_endpoints"][0]["bbox"] == [0.0, 0.0, 0.002, 0.002]
    assert candidates["design_unchanged"] is True

    preview = backend.execute(
        target,
        "layout_component_ports_create_preview",
        {
            "project_name": "Board",
            "design_name": "Layout1",
            "component_name": "U1",
            "signal_nets": ["N1", "N2"],
            "max_new_ports": 4,
        },
    )
    assert preview["expected_port_count"] == 2
    assert [item["name"] for item in preview["matching_pins"]] == ["U1-1", "U1-2"]
    assert preview["approval_required"] is True
    assert preview["project_dirty"] is False
    result = backend.execute(
        target,
        "layout_component_ports_create_apply",
        {"preview_id": preview["preview_id"]},
    )
    assert result["status"] == "verified"
    assert result["created_ports"] == ["Port_U1-1", "Port_U1-2"]
    assert result["created_port_count"] == result["expected_port_count"] == 2
    assert result["project_dirty"] is True
    assert result["project_saved"] is False


def test_backend_component_port_create_rejects_stale_preview_and_rolls_back_partial_apply():
    desktop = FakeDesktop()
    instances = []

    def factory(**kwargs):
        instance = FakeShortPortLayout(**kwargs)
        instances.append(instance)
        return instance

    backend = LiveAedtBackend(
        desktop_factory=lambda **kwargs: desktop,
        hfss_factory=FakeHfss,
        layout_factory=factory,
    )
    target = AedtTarget("pid", 42)
    preview = backend.execute(
        target,
        "layout_component_ports_create_preview",
        {
            "project_name": "Board",
            "design_name": "Layout1",
            "component_name": "U1",
            "signal_nets": ["N1", "N2"],
        },
    )
    initial_ports = list(instances[0].excitation_names)
    with pytest.raises(LiveBackendError, match="readback count mismatch"):
        backend.execute(
            target,
            "layout_component_ports_create_apply",
            {"preview_id": preview["preview_id"]},
        )
    assert instances[0].excitation_names == initial_ports

    preview = backend.execute(
        target,
        "layout_component_ports_create_preview",
        {
            "project_name": "Board",
            "design_name": "Layout1",
            "component_name": "U1",
            "signal_nets": ["N1", "N2"],
        },
    )
    instances[0].modeler.components["U1"].pins["U1-1"].location = [9.0, 9.0]
    with pytest.raises(LiveBackendError, match="stale layout component port preview"):
        backend.execute(
            target,
            "layout_component_ports_create_apply",
            {"preview_id": preview["preview_id"]},
        )


def test_backend_component_port_preview_rejects_ambiguous_or_invalid_targets():
    desktop = FakeDesktop()
    instances = []

    def factory(**kwargs):
        instance = FakePortLayout(**kwargs)
        instances.append(instance)
        return instance

    backend = LiveAedtBackend(
        desktop_factory=lambda **kwargs: desktop,
        hfss_factory=FakeHfss,
        layout_factory=factory,
    )
    target = AedtTarget("pid", 42)
    with pytest.raises(LiveBackendError, match="unknown layout component"):
        backend.execute(
            target,
            "layout_component_ports_create_preview",
            {
                "project_name": "Board",
                "design_name": "Layout1",
                "component_name": "DOES_NOT_EXIST",
                "signal_nets": ["N1"],
            },
        )
    with pytest.raises(LiveBackendError, match="unknown layout net"):
        backend.execute(
            target,
            "layout_port_candidate_inventory",
            {
                "project_name": "Board",
                "design_name": "Layout1",
                "signal_nets": ["MISSING"],
            },
        )
    duplicate_pin = FakeLayoutPin("U1-4", "U1", "N1")
    instances[0].modeler.components["U1"].pins["U1-4"] = duplicate_pin
    with pytest.raises(LiveBackendError, match="multiple pins on net N1"):
        backend.execute(
            target,
            "layout_component_ports_create_preview",
            {
                "project_name": "Board",
                "design_name": "Layout1",
                "component_name": "U1",
                "signal_nets": ["N1", "N2"],
            },
        )


def test_backend_ranks_live_uniform_edges_and_creates_typed_edge_ports():
    desktop = FakeDesktop()
    instances = []

    def factory(**kwargs):
        instance = FakeEdgePortLayout(**kwargs)
        instances.append(instance)
        return instance

    backend = LiveAedtBackend(
        desktop_factory=lambda **kwargs: desktop,
        hfss_factory=FakeHfss,
        layout_factory=factory,
    )
    target = AedtTarget("pid", 42)
    candidates = backend.execute(
        target,
        "layout_edge_port_candidate_inventory",
        {
            "project_name": "Board",
            "design_name": "Layout1",
            "signal_nets": ["N1", "N2"],
            "local_cut_region": {
                "type": "bbox",
                "unit": "mm",
                "x_min": 0,
                "y_min": 0,
                "x_max": 10,
                "y_max": 8,
            },
            "side": "right",
            "layer": "L1",
        },
    )
    assert candidates["status"] == "ready"
    assert candidates["coordinate_unit"] == "mm"
    assert candidates["source_model_units"] == "mm"
    assert [item["primitive"] for item in candidates["candidates"]] == ["line1", "line2"]
    assert [item["distance_to_side"] for item in candidates["candidates"]] == [0.2, 0.3]
    assert candidates["design_unchanged"] is True
    incomplete = backend.execute(
        target,
        "layout_edge_port_candidate_inventory",
        {
            "project_name": "Board",
            "design_name": "Layout1",
            "signal_nets": ["N1", "N2"],
            "local_cut_region": {
                "type": "bbox",
                "unit": "mm",
                "x_min": 0,
                "y_min": 0,
                "x_max": 10,
                "y_max": 8,
            },
            "side": "right",
            "layer": "L1",
            "max_candidates": 1,
        },
    )
    assert incomplete["status"] == "incomplete"
    assert incomplete["truncated"] is True

    preview = backend.execute(
        target,
        "layout_edge_ports_create_preview",
        {
            "project_name": "Board",
            "design_name": "Layout1",
            "edge_targets": [
                {
                    "primitive_name": "line1",
                    "edge_number": 0,
                    "port_type": "circuit",
                },
                {
                    "primitive_name": "line2",
                    "edge_number": 0,
                    "port_type": "wave",
                    "reference_primitive": "reference",
                    "reference_edge_number": 0,
                    "wave_horizontal_extension": 6,
                    "wave_vertical_extension": 4,
                    "wave_launcher": "0.5mm",
                },
            ],
            "max_new_ports": 4,
        },
    )
    assert preview["expected_port_count"] == 2
    assert preview["edge_targets"][0]["primary_edge"]["midpoint"] == [9.8, 3.0]
    assert preview["edge_targets"][1]["reference_edge"]["primitive_name"] == "reference"
    result = backend.execute(
        target,
        "layout_edge_ports_create_apply",
        {"preview_id": preview["preview_id"]},
    )
    assert result["status"] == "verified"
    assert [item["port_name"] for item in result["created_ports"]] == [
        "EdgePort_1",
        "EdgePort_2",
    ]
    assert result["created_port_count"] == result["expected_port_count"] == 2
    assert instances[0].edge_port_calls == [
        ("line1", 0, {"is_circuit_port": True, "is_wave_port": False}),
        (
            "line2",
            0,
            {
                "is_circuit_port": False,
                "is_wave_port": True,
                "reference_primitive": "reference",
                "reference_edge_number": 0,
                "wave_horizontal_extension": 6.0,
                "wave_vertical_extension": 4.0,
                "wave_launcher": "0.5mm",
            },
        ),
    ]
    assert result["project_saved"] is False


def test_backend_edge_port_batch_rolls_back_partial_creation_and_rejects_stale_edges():
    desktop = FakeDesktop()
    instances = []

    def factory(**kwargs):
        instance = FakeShortEdgePortLayout(**kwargs)
        instances.append(instance)
        return instance

    backend = LiveAedtBackend(
        desktop_factory=lambda **kwargs: desktop,
        hfss_factory=FakeHfss,
        layout_factory=factory,
    )
    target = AedtTarget("pid", 42)
    request = {
        "project_name": "Board",
        "design_name": "Layout1",
        "edge_targets": [
            {"primitive_name": "line1", "edge_number": 0, "port_type": "circuit"},
            {"primitive_name": "line2", "edge_number": 0, "port_type": "circuit"},
        ],
    }
    preview = backend.execute(target, "layout_edge_ports_create_preview", request)
    before = list(instances[0].excitation_names)
    with pytest.raises(LiveBackendError, match="readback mismatch"):
        backend.execute(
            target,
            "layout_edge_ports_create_apply",
            {"preview_id": preview["preview_id"]},
        )
    assert instances[0].excitation_names == before

    instances[0].edge_port_calls.clear()
    preview = backend.execute(target, "layout_edge_ports_create_preview", request)
    instances[0].modeler.lines["line1"].edges = [[[8.0, 2.0], [8.0, 4.0]]]
    with pytest.raises(LiveBackendError, match="stale layout edge port preview"):
        backend.execute(
            target,
            "layout_edge_ports_create_apply",
            {"preview_id": preview["preview_id"]},
        )


def test_backend_edge_port_preview_rejects_duplicate_invalid_or_unbounded_targets():
    backend = LiveAedtBackend(
        desktop_factory=lambda **kwargs: FakeDesktop(),
        hfss_factory=FakeHfss,
        layout_factory=FakeEdgePortLayout,
    )
    target = AedtTarget("pid", 42)
    base = {"project_name": "Board", "design_name": "Layout1"}
    with pytest.raises(LiveBackendError, match="must not duplicate"):
        backend.execute(
            target,
            "layout_edge_ports_create_preview",
            {
                **base,
                "edge_targets": [
                    {"primitive_name": "line1", "edge_number": 0},
                    {"primitive_name": "line1", "edge_number": 0},
                ],
            },
        )
    with pytest.raises(LiveBackendError, match="wave port options require"):
        backend.execute(
            target,
            "layout_edge_ports_create_preview",
            {
                **base,
                "edge_targets": [
                    {
                        "primitive_name": "line1",
                        "edge_number": 0,
                        "port_type": "circuit",
                        "wave_launcher": "1mm",
                    }
                ],
            },
        )
    with pytest.raises(LiveBackendError, match="exceeds max_new_ports"):
        backend.execute(
            target,
            "layout_edge_ports_create_preview",
            {
                **base,
                "edge_targets": [
                    {"primitive_name": "line1", "edge_number": 0},
                    {"primitive_name": "line2", "edge_number": 0},
                ],
                "max_new_ports": 1,
            },
        )
    with pytest.raises(LiveBackendError, match="unknown layout layer"):
        backend.execute(
            target,
            "layout_edge_port_candidate_inventory",
            {
                **base,
                "signal_nets": ["N1"],
                "local_cut_region": {
                    "type": "bbox",
                    "unit": "mm",
                    "x_min": 0,
                    "y_min": 0,
                    "x_max": 10,
                    "y_max": 8,
                },
                "side": "right",
                "layer": "MISSING",
            },
        )


def test_backend_uses_display_design_name_and_refuses_internal_identifier_before_factory_call():
    class InternalNameDesign(FakeDesign):
        def GetName(self):
            return "0;Layout1"

        def GetDesignName(self):
            return "Layout1"

    class InternalNameDesktop(FakeDesktop):
        def active_design(self, project):
            assert isinstance(project, FakeProject)
            return InternalNameDesign()

    factory_calls = []
    desktop = InternalNameDesktop()
    backend = LiveAedtBackend(
        desktop_factory=lambda **kwargs: desktop,
        layout_factory=lambda **kwargs: factory_calls.append(kwargs) or FakeLayout(**kwargs),
    )
    target = AedtTarget("pid", 42)

    info = backend.execute(target, "project_info", {})

    assert info["active_design"] == "Layout1"
    with pytest.raises(LiveBackendError, match="internal identifier"):
        backend.execute(
            target,
            "layout_paths_list",
            {"project_name": "Board", "design_name": "0;Layout1", "selector": {}},
        )
    assert factory_calls == []


def test_variable_upsert_rejects_stale_preview_and_rolls_back_failed_readback():
    layout = FakeLayout(project="Board", design="Layout1")
    backend = LiveAedtBackend(desktop_factory=FakeDesktop, layout_factory=lambda **kwargs: layout)
    target = AedtTarget("pid", 42)
    stale = backend.execute(
        target,
        "variable_upsert_preview",
        {
            "product": "layout",
            "project_name": "Board",
            "design_name": "Layout1",
            "variable_name": "$pitch",
            "expression": "2mm",
        },
    )
    layout.variable_manager.variables["$pitch"] = "1.5mm"
    with pytest.raises(LiveBackendError, match="stale variable preview"):
        backend.execute(target, "variable_upsert_apply", {"preview_id": stale["preview_id"]})

    failed = backend.execute(
        target,
        "variable_upsert_preview",
        {
            "product": "layout",
            "project_name": "Board",
            "design_name": "Layout1",
            "variable_name": "W_bad",
            "expression": "4.3mil",
        },
    )
    layout.variable_manager.set_variable = (
        lambda name, value, sweep=True: layout.variable_manager.variables.__setitem__(name, "wrong") is None
    )
    with pytest.raises(LiveBackendError, match="readback verification failed"):
        backend.execute(target, "variable_upsert_apply", {"preview_id": failed["preview_id"]})
    assert "W_bad" not in layout.variable_manager.variables


def test_layout_component_batch_update_rolls_back_every_target_on_readback_failure():
    class BadComponent(FakeLayoutComponent):
        def __init__(self, name):
            self._angle = "0deg"
            super().__init__(name)

        @property
        def angle(self):
            return self._angle

        @angle.setter
        def angle(self, value):
            self._angle = "45deg" if value == "90deg" else value

    layout = FakeLayout(project="Board", design="Layout1")
    layout.modeler.components["U2"] = BadComponent("U2")
    backend = LiveAedtBackend(desktop_factory=FakeDesktop, layout_factory=lambda **kwargs: layout)
    target = AedtTarget("pid", 42)
    preview = backend.execute(
        target,
        "layout_object_property_update_preview",
        {
            "project_name": "Board",
            "design_name": "Layout1",
            "object_kind": "component",
            "names": ["U1", "U2"],
            "properties": {"angle": "90deg"},
        },
    )

    with pytest.raises(LiveBackendError, match="readback verification failed"):
        backend.execute(
            target,
            "layout_object_property_update_apply",
            {"preview_id": preview["preview_id"]},
        )

    assert layout.modeler.components["U1"].angle == "0deg"
    assert layout.modeler.components["U2"].angle == "0deg"


def test_backend_refuses_missing_design_instead_of_allowing_pyaedt_to_create_it():
    factory_calls = []
    backend = LiveAedtBackend(
        desktop_factory=FakeDesktop,
        layout_factory=lambda **kwargs: factory_calls.append(kwargs) or FakeLayout(**kwargs),
    )

    with pytest.raises(LiveBackendError, match="refusing implicit design creation"):
        backend.execute(
            AedtTarget("pid", 42),
            "layout_paths_list",
            {"project_name": "Board", "design_name": "TypoLayout", "selector": {}},
        )
    assert factory_calls == []


def test_backend_approved_analysis_cancel_and_restricted_exports(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AEDT_AGENT_EXPORT_ROOT", str(tmp_path / "exports"))
    backend = LiveAedtBackend(desktop_factory=FakeDesktop, hfss_factory=FakeControlledSolveHfss)
    target = AedtTarget("pid", 42)
    preview = backend.execute(
        target,
        "hfss_analysis_start_preview",
        {
            "project_name": "Board",
            "design_name": "HFSS1",
            "setup_name": "Setup1",
            "cores": 4,
            "tasks": 1,
            "gpus": 0,
        },
    )
    assert preview["resources"] == {"cores": 4, "tasks": 1, "gpus": 0, "use_auto_settings": True}
    started = backend.execute(target, "hfss_analysis_start_apply", {"preview_id": preview["preview_id"]})
    assert started["started"] is True
    assert started["blocking"] is False
    status = backend.execute(
        target,
        "hfss_analysis_status",
        {"project_name": "Board", "design_name": "HFSS1", "setup_name": "Setup1"},
    )
    assert status["running"] is True
    assert status["latest_run"]["run_id"] == started["run_id"]

    cancel_preview = backend.execute(
        target,
        "hfss_analysis_cancel_preview",
        {"project_name": "Board", "design_name": "HFSS1", "setup_name": "Setup1"},
    )
    canceled = backend.execute(
        target,
        "hfss_analysis_cancel_apply",
        {"preview_id": cancel_preview["preview_id"]},
    )
    assert canceled["running"] is False

    touchstone_preview = backend.execute(
        target,
        "hfss_export_preview",
        {
            "project_name": "Board",
            "design_name": "HFSS1",
            "export_kind": "touchstone",
            "setup_name": "Setup1",
            "sweep_name": "LastAdaptive",
            "artifact_name": "network",
        },
    )
    touchstone = backend.execute(
        target,
        "hfss_export_apply",
        {"preview_id": touchstone_preview["preview_id"]},
    )
    artifact_path = Path(touchstone["artifact"]["path"])
    assert touchstone_preview["ports"] == ["P1", "P2"]
    assert touchstone_preview["port_order_source"] == "pyaedt.ports"
    assert artifact_path.suffix == ".s2p"
    assert artifact_path.is_relative_to(tmp_path / "exports")
    assert len(touchstone["artifact"]["sha256"]) == 64
    manifest = json.loads(Path(touchstone["manifest_path"]).read_text(encoding="utf-8"))
    assert manifest["artifact"]["sha256"] == touchstone["artifact"]["sha256"]
    assert manifest["ports"] == ["P1", "P2"]
    assert manifest["port_order_source"] == "pyaedt.ports"

    report_preview = backend.execute(
        target,
        "hfss_export_preview",
        {
            "project_name": "Board",
            "design_name": "HFSS1",
            "export_kind": "report_csv",
            "report_name": "S Parameters",
        },
    )
    report = backend.execute(target, "hfss_export_apply", {"preview_id": report_preview["preview_id"]})
    assert Path(report["artifact"]["path"]).suffix == ".csv"


def test_backend_exports_layout_results_with_product_bound_evidence(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AEDT_AGENT_EXPORT_ROOT", str(tmp_path / "exports"))
    backend = LiveAedtBackend(
        desktop_factory=FakeDesktop,
        layout_factory=FakeControlledExportLayout,
    )
    target = AedtTarget("pid", 42)
    preview = backend.execute(
        target,
        "hfss_export_preview",
        {
            "product": "layout",
            "project_name": "Board",
            "design_name": "Layout1",
            "export_kind": "touchstone",
            "setup_name": "SetupL",
            "artifact_name": "layout-network",
        },
    )

    assert preview["product"] == "layout"
    assert preview["ports"] == ["P1", "P2", "P3", "P4"]
    assert preview["port_order_source"] == "pyaedt.excitation_names"
    exported = backend.execute(target, "hfss_export_apply", {"preview_id": preview["preview_id"]})

    artifact_path = Path(exported["artifact"]["path"])
    manifest = json.loads(Path(exported["manifest_path"]).read_text(encoding="utf-8"))
    assert exported["product"] == "layout"
    assert artifact_path.suffix == ".s4p"
    assert manifest["spec"]["product"] == "layout"
    assert manifest["artifact"]["sha256"] == exported["artifact"]["sha256"]
    assert manifest["ports"] == ["P1", "P2", "P3", "P4"]


def test_backend_solution_inventory_and_run_freshness_use_result_snapshots(tmp_path: Path):
    results_directory = tmp_path / "Board.aedtresults"
    results_directory.mkdir()
    (results_directory / "old.sol").write_text("old", encoding="ascii")
    holder = {}

    def layout_factory(**kwargs):
        app = FakeLayout(**kwargs)
        app.results_directory = str(results_directory)
        app.existing_analysis_sweeps = ["SetupL : Sweep1"]
        app.get_setup("SetupL").is_solved = True
        holder["app"] = app
        return app

    backend = LiveAedtBackend(
        desktop_factory=FakeDesktop,
        layout_factory=layout_factory,
    )
    target = AedtTarget("pid", 42)
    inventory = backend.execute(
        target,
        "solution_inventory",
        {
            "product": "layout",
            "project_name": "Board",
            "design_name": "Layout1",
            "setup_name": "SetupL",
        },
    )
    assert inventory["target_solution_available"] is True
    assert inventory["target_solution_names"] == ["SetupL : Sweep1"]
    assert inventory["results"]["file_count"] == 1
    assert inventory["results"]["truncated"] is False
    assert inventory["design_unchanged"] is True

    preview = backend.execute(
        target,
        "hfss_analysis_start_preview",
        {
            "product": "layout",
            "project_name": "Board",
            "design_name": "Layout1",
            "setup_name": "SetupL",
        },
    )
    started = backend.execute(
        target,
        "hfss_analysis_start_apply",
        {"preview_id": preview["preview_id"]},
    )
    assert started["state"] == "running"
    (results_directory / "fresh.sol").write_text("fresh", encoding="ascii")
    holder["app"].are_there_simulations_running = False

    status = backend.execute(
        target,
        "hfss_analysis_status",
        {
            "product": "layout",
            "project_name": "Board",
            "design_name": "Layout1",
            "setup_name": "SetupL",
        },
    )
    evidence = status["latest_run"]["solution_evidence"]
    assert status["latest_run"]["state"] == "not_running"
    assert evidence["target_solution_available"] is True
    assert evidence["results_snapshot_changed"] is True
    assert evidence["result_written_after_submit"] is True
    assert evidence["solve_success_verified"] is True
    assert evidence["result_freshness_verified"] is True
    assert evidence["verification_reasons"] == ["fresh_solution_artifacts_verified"]


def test_backend_does_not_relabel_unchanged_old_solution_as_fresh(tmp_path: Path):
    results_directory = tmp_path / "Board.aedtresults"
    results_directory.mkdir()
    (results_directory / "old.sol").write_text("old", encoding="ascii")
    holder = {}

    def layout_factory(**kwargs):
        app = FakeLayout(**kwargs)
        app.results_directory = str(results_directory)
        app.existing_analysis_sweeps = ["SetupL : Sweep1"]
        app.get_setup("SetupL").is_solved = True
        holder["app"] = app
        return app

    backend = LiveAedtBackend(desktop_factory=FakeDesktop, layout_factory=layout_factory)
    target = AedtTarget("pid", 42)
    preview = backend.execute(
        target,
        "hfss_analysis_start_preview",
        {
            "product": "layout",
            "project_name": "Board",
            "design_name": "Layout1",
            "setup_name": "SetupL",
        },
    )
    backend.execute(target, "hfss_analysis_start_apply", {"preview_id": preview["preview_id"]})
    holder["app"].are_there_simulations_running = False

    status = backend.execute(
        target,
        "hfss_analysis_status",
        {
            "product": "layout",
            "project_name": "Board",
            "design_name": "Layout1",
            "setup_name": "SetupL",
        },
    )
    evidence = status["latest_run"]["solution_evidence"]
    assert evidence["target_solution_available"] is True
    assert evidence["results_snapshot_changed"] is False
    assert evidence["solve_success_verified"] is False
    assert evidence["result_freshness_verified"] is False
    assert "results_directory_snapshot_did_not_change" in evidence["verification_reasons"]
    assert evidence["verification_attempt"] == 1

    (results_directory / "late-flush.sol").write_text("fresh", encoding="ascii")
    refreshed_status = backend.execute(
        target,
        "hfss_analysis_status",
        {
            "product": "layout",
            "project_name": "Board",
            "design_name": "Layout1",
            "setup_name": "SetupL",
        },
    )
    refreshed = refreshed_status["latest_run"]["solution_evidence"]
    assert refreshed["verification_attempt"] == 2
    assert refreshed["results_snapshot_changed"] is True
    assert refreshed["solve_success_verified"] is True
    assert refreshed["result_freshness_verified"] is True


def test_backend_keeps_submitted_solve_pending_before_export_grace(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AEDT_AGENT_EXPORT_ROOT", str(tmp_path / "exports"))
    clock = [100.0]
    monkeypatch.setattr("aedt_agent.live.backend.time.monotonic", lambda: clock[0])
    backend = LiveAedtBackend(desktop_factory=FakeDesktop, hfss_factory=FakeSubmittedSolveHfss)
    target = AedtTarget("pid", 42)
    preview = backend.execute(
        target,
        "hfss_analysis_start_preview",
        {"project_name": "Board", "design_name": "HFSS1", "setup_name": "Setup1"},
    )
    started = backend.execute(target, "hfss_analysis_start_apply", {"preview_id": preview["preview_id"]})

    assert started["state"] == "submitted"
    assert not any(key.startswith("_") for key in started)
    status = backend.execute(
        target,
        "hfss_analysis_status",
        {"project_name": "Board", "design_name": "HFSS1", "setup_name": "Setup1"},
    )
    assert status["latest_run"]["state"] == "submitted"
    with pytest.raises(LiveBackendError, match="running or pending"):
        backend.execute(
            target,
            "hfss_export_preview",
            {
                "project_name": "Board",
                "design_name": "HFSS1",
                "export_kind": "touchstone",
                "setup_name": "Setup1",
            },
        )

    clock[0] = 106.0
    status = backend.execute(
        target,
        "hfss_analysis_status",
        {"project_name": "Board", "design_name": "HFSS1", "setup_name": "Setup1"},
    )
    assert status["latest_run"]["state"] == "not_running_unverified"
    export_preview = backend.execute(
        target,
        "hfss_export_preview",
        {
            "project_name": "Board",
            "design_name": "HFSS1",
            "export_kind": "touchstone",
            "setup_name": "Setup1",
        },
    )
    assert export_preview["approval_required"] is True


def test_analysis_preview_rejects_unbounded_resources_and_unsafe_artifact_names(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AEDT_AGENT_EXPORT_ROOT", str(tmp_path / "exports"))
    backend = LiveAedtBackend(desktop_factory=FakeDesktop, hfss_factory=FakeControlledSolveHfss)
    target = AedtTarget("pid", 42)
    base = {"project_name": "Board", "design_name": "HFSS1", "setup_name": "Setup1"}
    with pytest.raises(Exception, match="cores must be an integer"):
        backend.execute(target, "hfss_analysis_start_preview", base | {"cores": 1000})
    with pytest.raises(Exception, match="artifact_name"):
        backend.execute(
            target,
            "hfss_export_preview",
            base | {"export_kind": "touchstone", "artifact_name": "../escape"},
        )


def test_worker_stream_reuses_backend_and_returns_one_response_per_request():
    class Backend:
        def __init__(self, *, version):
            self.count = 0
            self.version = version

        def execute(self, target, command, arguments):
            self.count += 1
            return {"count": self.count}

        def release(self):
            return True

    target = AedtTarget("pid", 42)
    requests = [WorkerRequest.create("ping", target, {}, 10) for _ in range(2)]
    output = StringIO()
    assert serve(StringIO("\n".join(item.to_json() for item in requests)), output, backend_factory=Backend) == 0
    responses = [WorkerResponse.from_json(line) for line in output.getvalue().splitlines()]
    assert [item.result["count"] for item in responses] == [1, 2]


def test_manager_reuses_explicit_target_and_release_preserves_aedt():
    registry = FakeRegistry()
    manager = LiveAedtSessionManager(registry=registry)
    first = manager.attach(pid=42)
    second = manager.attach(pid=42)
    assert first["probe"]["connected"] is True
    assert second["reused_broker"] is True
    info = manager.project_info(first["live_session_id"])
    assert info["command"] == "project_info"
    released = manager.release(first["live_session_id"])
    assert released["aedt_closed"] is False
    assert released["projects_closed"] is False


def test_manager_accepts_matching_pid_port_pair_and_rejects_mismatch():
    registry = FakeRegistry()
    manager = LiveAedtSessionManager(registry=registry)

    attached = manager.attach(pid=42, port=50061)
    assert attached["target"] == {"kind": "port", "value": 50061}

    mismatch_registry = FakeRegistry()
    mismatch_manager = LiveAedtSessionManager(registry=mismatch_registry)
    with pytest.raises(Exception) as mismatch:
        mismatch_manager.attach(pid=99, port=50061)
    assert getattr(mismatch.value, "code", None) == "target_mismatch"
    assert mismatch_registry.calls[-1][1] == "release"


def test_manager_can_be_restricted_to_desktop_selected_port_and_project():
    registry = FakeRegistry()
    manager = LiveAedtSessionManager(
        registry=registry,
        required_port=50061,
        required_project="Board",
    )
    with pytest.raises(Exception) as wrong_port:
        manager.attach(port=50062)
    assert getattr(wrong_port.value, "code", None) == "target_forbidden"
    with pytest.raises(Exception) as launch:
        manager.launch()
    assert getattr(launch.value, "code", None) == "target_forbidden"

    session_id = manager.attach(port=50061)["live_session_id"]
    with pytest.raises(Exception) as wrong_project:
        manager.create_hfss_design(session_id, project_name="Other", design_name="HFSS1")
    assert getattr(wrong_project.value, "code", None) == "project_forbidden"
    allowed = manager.create_hfss_design(session_id, project_name="Board", design_name="HFSS1")
    assert allowed["command"] == "hfss_design_create"


def test_launcher_starts_explicit_grpc_target_and_manager_marks_ownership(tmp_path: Path):
    executable = tmp_path / "ansysedt.exe"
    executable.write_bytes(b"")

    class Process:
        pid = 77

        def poll(self):
            return None

        def terminate(self):
            raise AssertionError("healthy process must not be terminated")

    commands = []
    launcher = AedtLauncher(
        process_factory=lambda command, **kwargs: commands.append((command, kwargs)) or Process(),
        choose_port=lambda: 50123,
        port_is_free=lambda port: True,
        readiness_probe=lambda port: True,
        grpc_server_argument_factory=lambda port: f"127.0.0.1:{port}:SecureMode",
    )

    class LaunchRegistry(FakeRegistry):
        def execute(self, target, command, arguments, *, version="2026.1", **kwargs):
            result = super().execute(target, command, arguments, version=version, **kwargs)
            if command == "ping":
                return {**result, "pid": 77, "port": target.value}
            return result

    registry = LaunchRegistry()
    manager = LiveAedtSessionManager(registry=registry, launcher=launcher)
    opened = manager.launch(install_dir=str(executable), non_graphical=True)

    assert commands[0][0] == [
        str(executable.resolve()),
        "-grpcsrv",
        "127.0.0.1:50123:SecureMode",
        "-ng",
    ]
    assert opened["port"] == 50123
    assert opened["requested_port"] == 50123
    assert opened["grpc_argument_mode"] == "pyaedt"
    assert opened["owned_by_assistant"] is True
    assert opened["reused_broker"] is True
    released = manager.release(opened["live_session_id"])
    assert released["owned_by_assistant"] is True
    assert released["aedt_closed"] is False


def test_launcher_waits_for_pyaedt_readiness_before_probing(tmp_path: Path):
    executable = tmp_path / "ansysedt.exe"
    executable.write_bytes(b"")

    class Process:
        pid = 77

        def poll(self):
            return None

        def terminate(self):
            raise AssertionError("healthy assistant-owned process must not be terminated")

    commands = []
    probed = []
    readiness_calls = []

    def readiness(port):
        readiness_calls.append(port)
        return len(readiness_calls) >= 2

    launcher = AedtLauncher(
        process_factory=lambda command, **kwargs: commands.append(command) or Process(),
        choose_port=lambda: 50123,
        port_is_free=lambda port: True,
        readiness_probe=readiness,
        grpc_server_argument_factory=lambda port: f"127.0.0.1:{port}:SecureMode",
    )

    opened = launcher.launch(
        probe=lambda target, timeout: probed.append((target, timeout))
        or {"pid": 77, "port": 50222},
        install_dir=executable,
        non_graphical=True,
    )

    assert commands == [
        [str(executable.resolve()), "-grpcsrv", "127.0.0.1:50123:SecureMode", "-ng"]
    ]
    assert readiness_calls == [50123, 50123]
    assert probed and probed[0][0] == AedtTarget("port", 50123)
    assert opened["requested_port"] == 50123
    assert opened["port"] == 50222


def test_launcher_legacy_override_uses_pre_transport_port_argument(
    tmp_path: Path,
    monkeypatch,
):
    executable = tmp_path / "ansysedt.exe"
    executable.write_bytes(b"")
    monkeypatch.setenv("PYAEDT_USE_PRE_GRPC_ARGS", "True")

    class Process:
        pid = 77

        def poll(self):
            return None

        def terminate(self):
            raise AssertionError("healthy assistant-owned process must not be terminated")

    commands = []
    launcher = AedtLauncher(
        process_factory=lambda command, **kwargs: commands.append(command) or Process(),
        choose_port=lambda: 50123,
        port_is_free=lambda port: True,
        readiness_probe=lambda port: True,
    )

    opened = launcher.launch(
        probe=lambda target, timeout: {"pid": 77, "port": target.value},
        install_dir=executable,
    )

    assert commands == [[str(executable.resolve()), "-grpcsrv", "50123"]]
    assert opened["grpc_argument_mode"] == "legacy"


def test_pyaedt_grpc_server_argument_uses_pinned_transport_helper(monkeypatch):
    monkeypatch.delenv("PYAEDT_USE_PRE_GRPC_ARGS", raising=False)
    calls = []

    class ServerArgs:
        def __str__(self):
            return "127.0.0.1:50061:SecureMode"

    argument = _pyaedt_grpc_server_argument(
        50061,
        server_args_factory=lambda host, port: calls.append((host, port)) or ServerArgs(),
    )

    assert argument == "127.0.0.1:50061:SecureMode"
    assert calls == [("127.0.0.1", 50061)]


def test_pyaedt_grpc_session_ready_uses_pinned_readiness_helper():
    calls = []

    assert _pyaedt_grpc_session_ready(
        50061,
        session_active=lambda port: calls.append(port) or True,
    )
    assert calls == [50061]


def test_launcher_timeout_terminates_only_the_process_it_started(tmp_path: Path):
    executable = tmp_path / "ansysedt.exe"
    executable.write_bytes(b"")

    class Process:
        pid = 77
        returncode = None
        terminated = False
        killed = False

        def poll(self):
            return self.returncode

        def terminate(self):
            self.terminated = True
            self.returncode = 0

        def wait(self, timeout=None):
            return self.returncode

        def kill(self):
            self.killed = True
            self.returncode = -9

    class Clock:
        value = 0.0

        def __call__(self):
            current = self.value
            self.value += 0.6
            return current

    process = Process()
    probe_calls = []
    launcher = AedtLauncher(
        process_factory=lambda command, **kwargs: process,
        monotonic=Clock(),
        sleep=lambda seconds: None,
        choose_port=lambda: 50123,
        port_is_free=lambda port: True,
        readiness_probe=lambda port: False,
        grpc_server_argument_factory=lambda port: f"127.0.0.1:{port}:SecureMode",
    )

    with pytest.raises(AedtLaunchError, match="timed out"):
        launcher.launch(
            probe=lambda target, timeout: probe_calls.append((target, timeout)),
            install_dir=executable,
            timeout=1,
        )

    assert probe_calls == []
    assert process.terminated is True
    assert process.killed is False


def test_discovery_reports_command_line_grpc_port_and_version():
    connection = SimpleNamespace(status="LISTEN", laddr=SimpleNamespace(port=50061))
    process = SimpleNamespace(
        info={
            "pid": 42,
            "name": "ansysedt.exe",
            "exe": r"C:\Program Files\ANSYS Inc\v261\AnsysEM\ansysedt.exe",
            "create_time": 1.0,
            "cmdline": ["ansysedt.exe", "-grpcsrv", "50061"],
        },
        net_connections=lambda kind: [connection],
    )
    sessions = list_aedt_sessions(process_iter=lambda attrs: [process])
    assert sessions[0]["grpc_port"] == 50061
    assert sessions[0]["version"] == "2026.1"


def test_live_apply_is_disabled_without_host_approval_verifier():
    registry = FakeRegistry()
    manager = LiveAedtSessionManager(registry=registry)
    opened = manager.attach(pid=42)
    with pytest.raises(Exception) as error:
        manager.apply_layout_width(
            opened["live_session_id"],
            preview_id="preview-1",
            approval_token="agent-invented",
        )
    assert getattr(error.value, "code", None) == "approval_required"
    assert not any(call[1] == "layout_width_apply" for call in registry.calls)


def test_hmac_approval_is_bound_short_lived_and_one_use():
    now = [1000.0]
    authority = HmacApprovalAuthority("a" * 32, clock=lambda: now[0])
    token = authority.issue(
        action="project.save",
        resource_id="save-preview-1",
        digest="digest-1",
        ttl_seconds=10,
    )
    assert authority.verify("project.save", "save-preview-1", "digest-1", token) is True
    assert authority.verify("project.save", "save-preview-1", "digest-1", token) is False
    expired = authority.issue(
        action="project.save",
        resource_id="save-preview-2",
        digest="digest-2",
        ttl_seconds=10,
    )
    now[0] = 1011.0
    assert authority.verify("project.save", "save-preview-2", "digest-2", expired) is False


def test_manager_requires_digest_bound_approval_for_project_save():
    registry = FakeRegistry()
    authority = HmacApprovalAuthority("b" * 32)
    manager = LiveAedtSessionManager(registry=registry, approval_verifier=authority)
    session_id = manager.attach(pid=42)["live_session_id"]
    preview = manager.preview_project_save(session_id, project_name="Board")
    request = preview["approval_request"]
    token = authority.issue(**request)
    other_session_id = manager.attach(pid=42)["live_session_id"]
    other_preview = manager.preview_project_save(other_session_id, project_name="Board")
    with pytest.raises(Exception) as wrong_session:
        manager.apply_project_save(
            other_session_id,
            preview_id=other_preview["preview_id"],
            approval_token=token,
        )
    assert getattr(wrong_session.value, "code", None) == "approval_required"
    saved = manager.apply_project_save(session_id, preview_id=preview["preview_id"], approval_token=token)
    assert saved["command"] == "project_save_apply"
    with pytest.raises(Exception) as replay:
        manager.apply_project_save(session_id, preview_id=preview["preview_id"], approval_token=token)
    assert getattr(replay.value, "code", None) == "approval_required"


def test_manager_requires_independent_approvals_for_hfss_setup_and_report():
    registry = FakeRegistry()
    authority = HmacApprovalAuthority("c" * 32)
    manager = LiveAedtSessionManager(registry=registry, approval_verifier=authority)
    session_id = manager.attach(pid=42)["live_session_id"]
    setup_preview = manager.preview_hfss_setup(
        session_id,
        project_name="Board",
        design_name="HFSS1",
        setup_name="Setup2",
        properties={"Frequency": "10GHz"},
    )
    setup_token = authority.issue(**setup_preview["approval_request"])
    setup_result = manager.apply_hfss_setup(
        session_id,
        preview_id=setup_preview["preview_id"],
        approval_token=setup_token,
    )
    assert setup_result["command"] == "hfss_setup_apply"

    report_preview = manager.preview_hfss_report(
        session_id,
        project_name="Board",
        design_name="HFSS1",
        report_name="S21 Plot",
        setup_sweep_name="Setup2 : LastAdaptive",
        expressions=["dB(S(2,1))"],
    )
    with pytest.raises(Exception) as wrong_action:
        manager.apply_hfss_report(
            session_id,
            preview_id=report_preview["preview_id"],
            approval_token=setup_token,
        )
    assert getattr(wrong_action.value, "code", None) == "approval_required"
    report_token = authority.issue(**report_preview["approval_request"])
    report_result = manager.apply_hfss_report(
        session_id,
        preview_id=report_preview["preview_id"],
        approval_token=report_token,
    )
    assert report_result["command"] == "hfss_report_apply"


def test_manager_requires_action_bound_approval_for_layout_component_ports():
    registry = FakeRegistry()
    authority = HmacApprovalAuthority("p" * 32)
    manager = LiveAedtSessionManager(registry=registry, approval_verifier=authority)
    session_id = manager.attach(pid=42)["live_session_id"]
    preview = manager.preview_layout_component_ports_create(
        session_id,
        project_name="Board",
        design_name="Layout1",
        component_name="U1",
        signal_nets=["N1", "N2"],
        max_new_ports=4,
    )
    token = authority.issue(**preview["approval_request"])
    result = manager.apply_layout_component_ports_create(
        session_id,
        preview_id=preview["preview_id"],
        approval_token=token,
    )
    assert result["command"] == "layout_component_ports_create_apply"
    with pytest.raises(Exception) as replay:
        manager.apply_layout_component_ports_create(
            session_id,
            preview_id=preview["preview_id"],
            approval_token=token,
        )
    assert getattr(replay.value, "code", None) == "approval_required"


def test_manager_requires_action_bound_one_use_approval_for_hfss_geometry():
    registry = FakeRegistry()
    authority = HmacApprovalAuthority("g" * 32)
    manager = LiveAedtSessionManager(registry=registry, approval_verifier=authority)
    session_id = manager.attach(pid=42)["live_session_id"]
    preview = manager.preview_hfss_geometry_create(
        session_id,
        project_name="Board",
        design_name="HFSS1",
        primitives=[
            {
                "kind": "box",
                "name": "Box2",
                "origin": [0, 0, 0],
                "size": [1, 1, 1],
                "material": "copper",
            }
        ],
    )
    token = authority.issue(**preview["approval_request"])
    result = manager.apply_hfss_geometry_create(
        session_id,
        preview_id=preview["preview_id"],
        approval_token=token,
    )
    assert result["command"] == "hfss_geometry_create_apply"
    with pytest.raises(Exception) as replay:
        manager.apply_hfss_geometry_create(
            session_id,
            preview_id=preview["preview_id"],
            approval_token=token,
        )
    assert getattr(replay.value, "code", None) == "approval_required"


def test_manager_requires_action_bound_approval_for_layout_edge_ports():
    registry = FakeRegistry()
    authority = HmacApprovalAuthority("e" * 32)
    manager = LiveAedtSessionManager(registry=registry, approval_verifier=authority)
    session_id = manager.attach(pid=42)["live_session_id"]
    preview = manager.preview_layout_edge_ports_create(
        session_id,
        project_name="Board",
        design_name="Layout1",
        edge_targets=[
            {"primitive_name": "line1", "edge_number": 0, "port_type": "circuit"},
        ],
    )
    token = authority.issue(**preview["approval_request"])
    result = manager.apply_layout_edge_ports_create(
        session_id,
        preview_id=preview["preview_id"],
        approval_token=token,
    )
    assert result["command"] == "layout_edge_ports_create_apply"
    with pytest.raises(Exception) as replay:
        manager.apply_layout_edge_ports_create(
            session_id,
            preview_id=preview["preview_id"],
            approval_token=token,
        )
    assert getattr(replay.value, "code", None) == "approval_required"

    boundary_preview = manager.preview_hfss_boundary(
        session_id,
        project_name="Board",
        design_name="HFSS1",
        boundary_kind="radiation",
        boundary_name="rad2",
        assignment_face_ids=[101],
    )
    boundary_token = authority.issue(**boundary_preview["approval_request"])
    boundary_result = manager.apply_hfss_boundary(
        session_id,
        preview_id=boundary_preview["preview_id"],
        approval_token=boundary_token,
    )
    assert boundary_result["command"] == "hfss_boundary_apply"


def test_manager_requires_independent_approval_for_solve_cancel_and_export():
    registry = FakeRegistry()
    authority = HmacApprovalAuthority("d" * 32)
    manager = LiveAedtSessionManager(registry=registry, approval_verifier=authority)
    session_id = manager.attach(pid=42)["live_session_id"]

    solve_preview = manager.preview_hfss_analysis_start(
        session_id,
        project_name="Board",
        design_name="HFSS1",
        setup_name="Setup1",
        cores=4,
    )
    solve_token = authority.issue(**solve_preview["approval_request"])
    solve = manager.apply_hfss_analysis_start(
        session_id,
        preview_id=solve_preview["preview_id"],
        approval_token=solve_token,
    )
    assert solve["command"] == "hfss_analysis_start_apply"

    cancel_preview = manager.preview_hfss_analysis_cancel(
        session_id,
        project_name="Board",
        design_name="HFSS1",
        setup_name="Setup1",
    )
    with pytest.raises(Exception) as wrong_action:
        manager.apply_hfss_analysis_cancel(
            session_id,
            preview_id=cancel_preview["preview_id"],
            approval_token=solve_token,
        )
    assert getattr(wrong_action.value, "code", None) == "approval_required"
    cancel_token = authority.issue(**cancel_preview["approval_request"])
    canceled = manager.apply_hfss_analysis_cancel(
        session_id,
        preview_id=cancel_preview["preview_id"],
        approval_token=cancel_token,
    )
    assert canceled["command"] == "hfss_analysis_cancel_apply"

    export_preview = manager.preview_hfss_export(
        session_id,
        project_name="Board",
        design_name="HFSS1",
        export_kind="touchstone",
        setup_name="Setup1",
    )
    with pytest.raises(Exception) as reused_solve_approval:
        manager.apply_hfss_export(
            session_id,
            preview_id=export_preview["preview_id"],
            approval_token=solve_token,
        )
    assert getattr(reused_solve_approval.value, "code", None) == "approval_required"
    export_token = authority.issue(**export_preview["approval_request"])
    exported = manager.apply_hfss_export(
        session_id,
        preview_id=export_preview["preview_id"],
        approval_token=export_token,
    )
    assert exported["command"] == "hfss_export_apply"


def test_manager_registers_and_waits_for_desktop_host_approval():
    class Bridge:
        def __init__(self):
            self.registered = None
            self.token = "desktop-approved-token"
            self.used = False

        def register(self, action, resource_id, digest, preview):
            self.registered = (action, resource_id, digest, preview)
            return {"status": "pending"}

        def poll(self, resource_id, timeout_seconds=0):
            assert resource_id == self.registered[1]
            return {"status": "approved", "approval_token": self.token}

        def verify(self, action, resource_id, digest, token):
            valid = not self.used and (action, resource_id, digest) == self.registered[:3] and token == self.token
            self.used = self.used or valid
            return valid

    registry = FakeRegistry()
    bridge = Bridge()
    manager = LiveAedtSessionManager(registry=registry, approval_verifier=bridge)
    session_id = manager.attach(pid=42)["live_session_id"]
    preview = manager.preview_project_save(session_id, project_name="Board")
    assert preview["approval_status"] == "pending"
    assert preview["approval_poll"]["tool"] == "wait_for_live_approval"
    decision = manager.wait_for_approval(session_id, preview_id=preview["preview_id"], timeout_seconds=10)
    saved = manager.apply_project_save(
        session_id,
        preview_id=preview["preview_id"],
        approval_token=decision["approval_token"],
    )
    assert saved["command"] == "project_save_apply"


def test_desktop_bound_manager_enforces_design_and_disables_direct_writes():
    registry = FakeRegistry()
    manager = LiveAedtSessionManager(
        registry=registry,
        required_design="Layout1",
        required_version="2026.1",
        strict_desktop=True,
    )
    session_id = manager.attach(pid=42)["live_session_id"]
    with pytest.raises(Exception) as wrong_design:
        manager.list_layout_paths(
            session_id,
            project_name="Board",
            design_name="OtherLayout",
        )
    assert getattr(wrong_design.value, "code", None) == "design_forbidden"
    with pytest.raises(Exception) as direct_create:
        manager.create_hfss_design(session_id, project_name="Board", design_name="Layout1")
    assert getattr(direct_create.value, "code", None) == "preview_required"
    with pytest.raises(Exception) as direct_solve:
        manager.start_hfss_analysis(
            session_id,
            project_name="Board",
            design_name="Layout1",
            setup_name="Setup1",
        )
    assert getattr(direct_solve.value, "code", None) == "preview_required"


def test_manager_uses_desktop_approval_host_from_process_environment(monkeypatch):
    key = "desktop-environment-key-at-least-24"
    host = ApprovalHost(
        "127.0.0.1",
        0,
        key,
        DesktopApprovalStore(prompt=lambda record: record.action == "project.save"),
    )
    thread = threading.Thread(target=host.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("AEDT_AGENT_APPROVAL_URL", f"http://127.0.0.1:{host.port}")
    monkeypatch.setenv("AEDT_AGENT_APPROVAL_KEY", key)
    manager = LiveAedtSessionManager(registry=FakeRegistry())
    try:
        session_id = manager.attach(pid=42)["live_session_id"]
        preview = manager.preview_project_save(session_id, project_name="Board")
        decision = manager.wait_for_approval(
            session_id,
            preview_id=preview["preview_id"],
            timeout_seconds=2,
        )
        assert decision["status"] == "approved"
        result = manager.apply_project_save(
            session_id,
            preview_id=preview["preview_id"],
            approval_token=decision["approval_token"],
        )
        assert result["command"] == "project_save_apply"
    finally:
        manager.close()
        from aedt_agent.desktop.approval_client import DesktopApprovalClient

        DesktopApprovalClient(f"http://127.0.0.1:{host.port}", key)._post("/shutdown", {})
        thread.join(timeout=3)


def test_mcp_registers_live_tools_without_changing_artifact_tools(monkeypatch):
    class FakeFastMCP:
        def __init__(self, name, **kwargs):
            self.tools = {}
            self.instructions = kwargs.get("instructions")

        def tool(self):
            def register(fn):
                self.tools[fn.__name__] = fn
                return fn

            return register

    class Live:
        def list_sessions(self):
            return {"sessions": []}

        def capture_capability_trace(self, candidate_id):
            return {"candidate_id": candidate_id, "server_owned": True}

        def promote_capability_candidate(self, trace_id, *, target_kind="auto"):
            return {"trace_id": trace_id, "target_kind": target_kind, "status": "candidate"}

    monkeypatch.setitem(sys.modules, "fastmcp", SimpleNamespace(FastMCP=FakeFastMCP))
    from aedt_agent.interactive.kernel import InteractiveKernel
    from aedt_agent.interactive.server import create_server

    kernel = InteractiveKernel(session_manager=SimpleNamespace())
    server = create_server(kernel=kernel, live_manager=Live())
    assert "open_layout_session" in server.tools
    assert "list_live_aedt_sessions" in server.tools
    assert "preview_live_project_save" in server.tools
    assert "apply_live_project_save" in server.tools
    assert "launch_live_aedt_session" in server.tools
    assert "get_live_hfss_design_inventory" in server.tools
    assert "get_live_hfss_geometry_inventory" in server.tools
    assert "preview_live_hfss_geometry_create" in server.tools
    assert "apply_live_hfss_geometry_create" in server.tools
    assert "preview_live_hfss_setup_create" in server.tools
    assert "apply_live_hfss_setup_create" in server.tools
    assert "preview_live_hfss_report_create" in server.tools
    assert "apply_live_hfss_report_create" in server.tools
    assert "preview_live_hfss_boundary_create" in server.tools
    assert "apply_live_hfss_boundary_create" in server.tools
    assert "preview_live_hfss_analysis_start" in server.tools
    assert "apply_live_hfss_analysis_start" in server.tools
    assert "preview_live_hfss_analysis_cancel" in server.tools
    assert "apply_live_hfss_analysis_cancel" in server.tools
    assert "preview_live_hfss_results_export" in server.tools
    assert "apply_live_hfss_results_export" in server.tools
    assert "wait_for_live_approval" in server.tools
    assert "propose_ansys_operation" in server.tools
    assert "get_ansys_operation_plan_schema" in server.tools
    assert "validate_ansys_operation" in server.tools
    assert "preview_exploratory_operation" in server.tools
    assert "apply_exploratory_operation" in server.tools
    assert "capture_capability_trace" in server.tools
    assert "promote_ansys_capability" in server.tools
    assert "list_ansys_capabilities_v2" in server.tools
    assert asyncio.run(server.tools["list_live_aedt_sessions"]()) == {"sessions": []}
    assert asyncio.run(server.tools["capture_capability_trace"]("candidate-1")) == {
        "candidate_id": "candidate-1",
        "server_owned": True,
    }
    assert asyncio.run(server.tools["promote_ansys_capability"]("trace-1", "skill")) == {
        "trace_id": "trace-1",
        "target_kind": "skill",
        "status": "candidate",
    }
    assert "report blocked before calling" in server.instructions


def test_desktop_bound_mcp_hides_out_of_scope_tools_and_filters_catalogs(monkeypatch):
    class FakeFastMCP:
        def __init__(self, name, **kwargs):
            self.tools = {}
            self.instructions = kwargs.get("instructions")

        def tool(self):
            def register(fn):
                self.tools[fn.__name__] = fn
                return fn

            return register

    monkeypatch.setitem(sys.modules, "fastmcp", SimpleNamespace(FastMCP=FakeFastMCP))
    monkeypatch.setenv("AEDT_AGENT_DESKTOP_STRICT", "1")
    from aedt_agent.interactive.kernel import InteractiveKernel
    from aedt_agent.interactive.server import create_server

    server = create_server(
        kernel=InteractiveKernel(session_manager=SimpleNamespace()),
        live_manager=SimpleNamespace(),
    )

    from aedt_agent.desktop.launcher import _DESKTOP_ASSISTANT_MCP_TOOLS

    hidden = {
        "open_layout_session",
        "close_layout_session",
        "list_layout_paths",
        "preview_parameterize_path_width",
        "apply_parameterize_path_width",
        "list_live_aedt_sessions",
        "launch_live_aedt_session",
        "create_live_hfss_design",
        "start_live_hfss_analysis",
    }
    assert hidden.isdisjoint(server.tools)
    assert set(server.tools) == set(_DESKTOP_ASSISTANT_MCP_TOOLS)
    assert {
        "attach_live_aedt_session",
        "release_live_aedt_session",
        "get_live_aedt_project_info",
        "get_live_hfss_design_inventory",
        "get_live_hfss_geometry_inventory",
        "preview_live_hfss_setup_create",
        "apply_live_hfss_setup_create",
        "list_live_layout_paths",
        "preview_live_parameterize_path_width",
        "apply_live_parameterize_path_width",
        "wait_for_live_approval",
        "propose_ansys_operation",
        "validate_ansys_operation",
        "preview_exploratory_operation",
        "apply_exploratory_operation",
        "capture_capability_trace",
        "promote_ansys_capability",
    }.issubset(server.tools)

    v1 = asyncio.run(server.tools["list_ansys_capabilities"]())
    assert v1["scope"] == "desktop_bound"
    assert v1["capabilities"] == []
    assert set(v1["unavailable_capabilities"]) == {
        "layout.paths.list",
        "layout.path_width.parameterize.preview",
        "layout.path_width.parameterize.apply",
    }

    v2 = asyncio.run(server.tools["list_ansys_capabilities_v2"]())
    by_name = {item["name"]: item for item in v2["capabilities"]}
    assert v2["scope"] == "desktop_bound"
    assert "aedt.sessions.list" not in by_name
    assert "aedt.sessions.launch" not in by_name
    assert "hfss.design.create" not in by_name
    assert by_name["layout.paths.list"]["tools"] == ["list_live_layout_paths"]
    assert by_name["layout.paths.list"]["modes"] == ["live"]
    assert by_name["layout.path_width.parameterize"]["tools"] == [
        "preview_live_parameterize_path_width",
        "apply_live_parameterize_path_width",
    ]
    assert by_name["hfss.results.export"]["products"] == ["hfss", "layout"]
    unavailable = {item["name"] for item in v2["unavailable_capabilities"]}
    assert {"aedt.sessions.list", "aedt.sessions.launch", "hfss.design.create"}.issubset(unavailable)
    assert "preselected AEDT port, project, and design" in server.instructions
