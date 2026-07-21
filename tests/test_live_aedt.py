from __future__ import annotations

import asyncio
from io import StringIO
import json
import math
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
        self.angle = "0.0deg"
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
    design_type = "HFSS 3D Layout Design"
    solution_type = "HFSS3DLayout"

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

        def get_layer_info(name):
            layer = next(item for item in stackup_layers if item.name == name)
            return [
                f"LayerId: {layer.id}",
                f"Type: {layer.type}",
                f"LayerThickness: {layer.thickness}{layer.thickness_units}",
                f"LowerElevation0: {layer.lower_elevation}{layer.thickness_units}",
                f"Material0: {layer.material}",
                f"FillMaterial0: {layer.fill_material}",
                f"IsLocked: False",
                f"TopBottomAssociation: {layer.top_bottom}",
                f"IsVisible: True",
                f"EtchFactor: {layer.etch}",
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
            layers=SimpleNamespace(
                stackup_layers=stackup_layers,
                oeditor=SimpleNamespace(GetLayerInfo=get_layer_info),
            ),
            padstacks=padstacks,
            model_units="mm",
        )
        self.variable_manager = SimpleNamespace(
            variables={"$pitch": SimpleNamespace(expression="1mm")},
            set_variable=lambda name, value, sweep=True: self._set_variable(name, value),
            delete_variable=lambda name: self.variable_manager.variables.pop(name, None) is not None,
        )
        self.materials = FakeMaterials()
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


class FakeViaCreateEditor:
    def __init__(self, app):
        self.app = app

    def FindObjects(self, field, value):
        if field != "Name":
            return []
        names = set(self.app.modeler.vias)
        names.update(self.app.modeler.components)
        names.update(self.app.modeler.line_names)
        return [value] if value in names else []

    def GetProperties(self, tab, name):
        assert tab == "BaseElementTab"
        assert name in self.app.modeler.vias
        return [
            "Type",
            "LockPosition",
            "Name",
            "Net",
            "Padstack Definition",
            "Start Layer",
            "Stop Layer",
            "OverrideHoleDiameter",
            "HoleDiameter",
            "Location",
            "Angle",
        ]

    def GetPropertyValue(self, tab, name, prop):
        assert tab == "BaseElementTab"
        via = self.app.modeler.vias[name]
        values = {
            "Type": "Via",
            "LockPosition": str(via.lock_position).lower(),
            "Name": via.name,
            "Net": via.net_name,
            "Padstack Definition": via.padstack,
            "Start Layer": via.start_layer,
            "Stop Layer": via.stop_layer,
            "OverrideHoleDiameter": str(via.override_hole_diameter).lower(),
            "HoleDiameter": via.holediam,
            "Location": f"{via.location[0]} ,{via.location[1]}",
            "Angle": via.angle,
        }
        return values[prop]

    def Delete(self, names):
        for name in names:
            self.app.modeler.vias.pop(name, None)


class FakeViaCreateLayout(FakeLayout):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.modeler.layers.stackup_layers.append(
            SimpleNamespace(
                name="BOT",
                type="signal",
                id=3,
                thickness=0.035,
                thickness_units="mm",
                lower_elevation=-0.035,
                material="copper",
                fill_material="FR4_epoxy",
                roughness="0mm",
                etch=0.0,
                is_negative=False,
                top_bottom="bottom",
            )
        )
        self.modeler.oeditor = FakeViaCreateEditor(self)
        self.modeler._vias = self.modeler.vias
        self.modeler.create_via = self.create_via
        existing = self.modeler.vias["V1"]
        existing.padstack = "VIA"
        existing.start_layer = "TOP"
        existing.stop_layer = "BOT"
        existing.override_hole_diameter = False

    def create_via(
        self,
        *,
        name,
        padstack,
        x,
        y,
        rotation,
        hole_diam,
        top_layer,
        bot_layer,
        net,
    ):
        if name in self.modeler.vias:
            return False
        via = FakeVia(name)
        via.padstack = padstack
        via.start_layer = top_layer
        via.stop_layer = bot_layer
        via.net_name = net if net is not None else "<NO-NET>"
        via.location = [float(x), float(y)]
        via.angle = "0.0deg"
        via.lock_position = False
        via.override_hole_diameter = hole_diam is not None
        via.holediam = f"{float(hole_diam)}mm" if hole_diam is not None else "0.2mm"
        self.modeler.vias[name] = via
        return via

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
        face_centers=None,
        volume=1.0,
        bounding_box=None,
        color=(120, 130, 140),
        transparency=0.1,
        is_planar=True,
        vertex_positions=None,
    ):
        self.id = object_id
        self.material_name = material_name
        self.solve_inside = solve_inside
        self.volume = volume
        self.bounding_box = bounding_box or [-1, -1, -1, 1, 1, 1]
        self.color = color
        self.transparency = transparency
        centers = face_centers or [[0, 0, 0]]
        self.faces = [
            SimpleNamespace(
                id=face_id + index,
                center=center,
                area=1.5,
                is_planar=is_planar,
            )
            for index, center in enumerate(centers)
        ]
        self.vertices = [
            SimpleNamespace(
                id=object_id * 1000 + index,
                position=list(position),
            )
            for index, position in enumerate(vertex_positions or [])
        ]


class FakeHfssModeler:
    def __init__(self):
        self.model_units = "mm"
        self.object_names = ["box1"]
        self._objects = {"box1": FakeObject()}

    def __getitem__(self, name):
        return self._objects[name]

    def get_mid_points_on_dir(self, assignment, direction):
        points = {
            0: ([1.0, 0.0, 0.0], [-1.0, 0.0, 0.0]),
            1: ([0.0, 1.0, 0.0], [0.0, -1.0, 0.0]),
            2: ([0.0, 0.0, 1.0], [0.0, 0.0, -1.0]),
            3: ([-1.0, 0.0, 0.0], [1.0, 0.0, 0.0]),
            4: ([0.0, -1.0, 0.0], [0.0, 1.0, 0.0]),
            5: ([0.0, 0.0, -1.0], [0.0, 0.0, 1.0]),
        }
        return points[int(direction)]


class FakeCoordinateChild:
    def __init__(self, properties):
        self.properties = properties

    def GetPropNames(self):
        return list(self.properties)

    def GetPropValue(self, name):
        return self.properties[name]


class FakeCoordinateSystem:
    def __init__(self, modeler, name):
        self.modeler = modeler
        self.name = name

    def delete(self):
        self.modeler._coordinate_systems.pop(self.name, None)
        if self.modeler._active_coordinate_system == self.name:
            self.modeler._active_coordinate_system = "Global"
        return True


class FakeCoordinateEditor:
    def __init__(self, modeler):
        self.modeler = modeler

    def GetCoordinateSystems(self):
        return ["Global", *self.modeler._coordinate_systems]

    def GetActiveCoordinateSystem(self):
        return self.modeler._active_coordinate_system

    def GetChildObject(self, name):
        return FakeCoordinateChild(self.modeler._coordinate_systems[name])

    def SetWCS(self, arguments):
        name = arguments[arguments.index("Working Coordinate System:=") + 1]
        if name not in self.GetCoordinateSystems():
            raise RuntimeError("unknown coordinate system")
        self.modeler._active_coordinate_system = name
        return True


class FakeCoordinateModeler(FakeHfssModeler):
    def __init__(self, *, mismatch_readback=False):
        super().__init__()
        self._coordinate_systems = {}
        self._active_coordinate_system = "Global"
        self.mismatch_readback = mismatch_readback
        self.oeditor = FakeCoordinateEditor(self)

    @property
    def coordinate_systems(self):
        return [FakeCoordinateSystem(self, name) for name in self._coordinate_systems]

    def create_coordinate_system(
        self,
        origin=None,
        reference_cs="Global",
        name=None,
        mode="axis",
        x_pointing=None,
        y_pointing=None,
        **kwargs,
    ):
        def origin_value(value):
            if isinstance(value, (int, float)):
                return f"{float(value):g}{self.model_units}"
            text = str(value)
            try:
                return f"{float(text):g}{self.model_units}"
            except ValueError:
                return text

        x_values = [f"{float(item):g}{self.model_units}" for item in x_pointing]
        if self.mismatch_readback:
            x_values[0] = f"{float(x_pointing[0]) + 1:g}{self.model_units}"
        self._coordinate_systems[name] = {
            "Type": "Relative",
            "Reference CS": reference_cs,
            "Mode": "Axis/Position",
            "Origin/X": origin_value(origin[0]),
            "Origin/Y": origin_value(origin[1]),
            "Origin/Z": origin_value(origin[2]),
            "X Axis/X": x_values[0],
            "X Axis/Y": x_values[1],
            "X Axis/Z": x_values[2],
            "Y Point/X": f"{float(y_pointing[0]):g}{self.model_units}",
            "Y Point/Y": f"{float(y_pointing[1]):g}{self.model_units}",
            "Y Point/Z": f"{float(y_pointing[2]):g}{self.model_units}",
        }
        self._active_coordinate_system = name
        return FakeCoordinateSystem(self, name)


class FakeGeometryModeler(FakeHfssModeler):
    def __init__(self, *, fail_on=""):
        super().__init__()
        self.fail_on = fail_on
        self.calls = []

    def _create(self, kind, name, material, call):
        self.calls.append((kind, call))
        if name == self.fail_on:
            raise RuntimeError(f"synthetic {kind} failure")
        face_centers = (
            [[-1, 0, 0], [1, 0, 0], [0, -1, 0], [0, 1, 0], [0, 0, -1], [0, 0, 1]]
            if kind in {"box", "region"}
            else [[0, 0, 0]]
        )
        obj = FakeObject(
            object_id=10 + len(self.object_names),
            material_name=material,
            face_id=200 + len(self.object_names) * 10,
            face_centers=face_centers,
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


class FakeMoveEditor:
    def __init__(self, modeler):
        self.modeler = modeler

    def GetActiveCoordinateSystem(self):
        return self.modeler.active_coordinate_system


class FakeMoveModeler(FakeHfssModeler):
    def __init__(self, *, fail_on=""):
        super().__init__()
        self.object_names = ["box1", "sheet1", "fixed1"]
        self._objects = {
            "box1": FakeObject(
                object_id=9,
                material_name="copper",
                solve_inside=False,
                face_id=101,
                face_centers=[[0, 0, 0], [2, 2, 2]],
                volume=8.0,
                bounding_box=[-1, -1, -1, 1, 1, 1],
            ),
            "sheet1": FakeObject(
                object_id=10,
                material_name="",
                solve_inside=False,
                face_id=201,
                face_centers=[[5, 1, 0]],
                volume=0.0,
                bounding_box=[4, 0, 0, 6, 2, 0],
            ),
            "fixed1": FakeObject(
                object_id=11,
                material_name="vacuum",
                solve_inside=True,
                face_id=301,
                face_centers=[[20, 0, 0]],
                volume=1.0,
                bounding_box=[19.5, -0.5, -0.5, 20.5, 0.5, 0.5],
            ),
        }
        self.fail_on = fail_on
        self.active_coordinate_system = "Global"
        self.oeditor = FakeMoveEditor(self)
        self.move_calls = []

    def move(self, assignment, vector):
        names = [str(item) for item in list(assignment)]
        self.move_calls.append((names, list(vector)))
        if any(name == self.fail_on for name in names):
            return False
        values = [float(item) for item in vector]
        for name in names:
            obj = self._objects[name]
            obj.bounding_box = [
                float(value) + values[index % 3]
                for index, value in enumerate(obj.bounding_box)
            ]
            for face in obj.faces:
                face.center = [
                    float(value) + values[index]
                    for index, value in enumerate(face.center)
                ]
        return True


def _fake_rotate_point(point, axis, angle_degrees):
    angle = math.radians(float(angle_degrees))
    cosine = math.cos(angle)
    sine = math.sin(angle)
    x, y, z = (float(item) for item in point)
    if axis == "X":
        values = [x, cosine * y - sine * z, sine * y + cosine * z]
    elif axis == "Y":
        values = [cosine * x + sine * z, y, -sine * x + cosine * z]
    else:
        values = [cosine * x - sine * y, sine * x + cosine * y, z]
    return [0.0 if round(item, 12) == 0 else round(item, 12) for item in values]


class FakeRotateModeler(FakeHfssModeler):
    def __init__(self, *, fail_on=""):
        super().__init__()
        self.object_names = ["box1", "sheet1", "fixed1"]
        self._objects = {
            "box1": FakeObject(
                object_id=19,
                material_name="copper",
                solve_inside=False,
                face_id=401,
                face_centers=[
                    [2, 1, 0],
                    [2, 1, 2],
                    [1, 1, 1],
                    [3, 1, 1],
                    [2, 0, 1],
                    [2, 2, 1],
                ],
                volume=8.0,
                bounding_box=[1, 0, 0, 3, 2, 2],
                vertex_positions=[
                    [1, 0, 0],
                    [1, 0, 2],
                    [1, 2, 0],
                    [1, 2, 2],
                    [3, 0, 0],
                    [3, 0, 2],
                    [3, 2, 0],
                    [3, 2, 2],
                ],
            ),
            "sheet1": FakeObject(
                object_id=20,
                material_name="",
                solve_inside=False,
                face_id=501,
                face_centers=[[5, 2, 1]],
                volume=0.0,
                bounding_box=[5, 1, 0, 5, 3, 2],
                vertex_positions=[
                    [5, 1, 0],
                    [5, 1, 2],
                    [5, 3, 0],
                    [5, 3, 2],
                ],
            ),
            "fixed1": FakeObject(
                object_id=21,
                material_name="vacuum",
                solve_inside=True,
                face_id=601,
                face_centers=[[20.5, 0.5, 0.5]],
                volume=1.0,
                bounding_box=[20, 0, 0, 21, 1, 1],
                vertex_positions=[[20, 0, 0], [21, 1, 1]],
            ),
        }
        self.fail_on = fail_on
        self.active_coordinate_system = "Global"
        self.oeditor = FakeMoveEditor(self)
        self.rotate_calls = []

    def rotate(self, assignment, axis, angle=90.0, units="deg"):
        names = [str(item) for item in list(assignment)]
        self.rotate_calls.append((names, str(axis), float(angle), str(units)))
        if any(name == self.fail_on for name in names):
            return False
        assert units == "deg"
        axis = str(axis).upper()
        for name in names:
            obj = self._objects[name]
            corners = [
                [x, y, z]
                for x in (obj.bounding_box[0], obj.bounding_box[3])
                for y in (obj.bounding_box[1], obj.bounding_box[4])
                for z in (obj.bounding_box[2], obj.bounding_box[5])
            ]
            rotated_corners = [
                _fake_rotate_point(point, axis, angle) for point in corners
            ]
            obj.bounding_box = [
                min(point[index] for point in rotated_corners)
                for index in range(3)
            ] + [
                max(point[index] for point in rotated_corners)
                for index in range(3)
            ]
            for face in obj.faces:
                face.center = _fake_rotate_point(face.center, axis, angle)
            for vertex in obj.vertices:
                vertex.position = _fake_rotate_point(vertex.position, axis, angle)
        return True


class FakeBoundary:
    def __init__(self, owner, name, boundary_type, *, port=False, props=None):
        self.owner = owner
        self.name = name
        self.type = boundary_type
        self.port = port
        self.props = dict(props or {})

    def delete(self):
        self.owner.boundaries = [item for item in self.owner.boundaries if item.name != self.name]
        if self.port:
            self.owner.ports = [item for item in self.owner.ports if item != self.name]
        return True


class FakeFieldSetup:
    def __init__(self, owner, name, definition, values, units, polarization, polarization_angle):
        self.owner = owner
        self.name = name
        self.type = "FarFieldSphere"
        axes = {
            "Theta-Phi": ("Theta", "Phi"),
            "El Over Az": ("Azimuth", "Elevation"),
            "Az Over El": ("Elevation", "Azimuth"),
        }[definition]
        self.properties = {
            "Name": name,
            "Type": "Infinite Sphere",
            "CS Definition": definition,
            f"Start {axes[0]}": _fake_angle(values[0], units),
            f"Stop {axes[0]}": _fake_angle(values[1], units),
            f"{axes[0]} Step": _fake_angle(values[2], units),
            f"Start {axes[1]}": _fake_angle(values[3], units),
            f"Stop {axes[1]}": _fake_angle(values[4], units),
            f"{axes[1]} Step": _fake_angle(values[5], units),
            "Coordinate System": "Global",
            "Polarization": polarization,
            "Slant Angle": _fake_angle(polarization_angle, units),
        }
        self.props = dict(self.properties)

    def delete(self):
        self.owner._field_setups.pop(self.name, None)
        return True


def _fake_angle(value, units):
    return f"{float(value):g}{units}"


class FakeHfss:
    are_there_simulations_running = False
    solution_type = "DrivenModal"
    design_type = "HFSS"
    axis_directions = SimpleNamespace(XNeg=0, YNeg=1, ZNeg=2, XPos=3, YPos=4, ZPos=5)

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.project_name = kwargs["project"]
        self.design_name = kwargs["design"]
        self._setups = {"Setup1": FakeSetup("Setup1")}
        self.post = FakePost()
        self.modeler = FakeHfssModeler()
        self.ports = ["P1", "P2"]
        self.boundaries = [FakeBoundary(self, "rad1", "Radiation", props={"Faces": [102]})]

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
        direction = kwargs.get("integration_line", 0)
        if isinstance(direction, list):
            start, end = direction
        else:
            start, end = self.modeler.get_mid_points_on_dir(assignment, direction)
        mode_count = int(kwargs.get("modes", 1))
        characteristic = str(kwargs.get("characteristic_impedance", "Zpi"))
        modes = {
            f"Mode{index}": {
                "ModeNum": index,
                "UseIntLine": index == 1,
                "IntLine": {
                    "Start": [str(item) if isinstance(item, str) else f"{item}mm" for item in start],
                    "End": [str(item) if isinstance(item, str) else f"{item}mm" for item in end],
                }
                if index == 1
                else {},
                "CharImp": characteristic,
            }
            for index in range(1, mode_count + 1)
        }
        deembed = float(kwargs.get("deembed", 0))
        boundary = FakeBoundary(
            self,
            name,
            "Wave Port",
            port=True,
            props={
                "Faces": [assignment],
                "NumModes": mode_count,
                "DoDeembed": deembed > 0,
                "DeembedDist": f"{deembed}mm" if deembed > 0 else "",
                "RenormalizeAllTerminals": kwargs.get("renormalize", True),
                "Modes": modes,
            },
        )
        self.boundaries.append(boundary)
        self.ports.append(name)
        return boundary

    def lumped_port(self, assignment, reference=None, name=None, **kwargs):
        direction = kwargs.get("integration_line", 0)
        if isinstance(direction, list):
            start, end = direction
        else:
            start, end = self.modeler.get_mid_points_on_dir(assignment, direction)
        boundary = FakeBoundary(
            self,
            name,
            "Lumped Port",
            port=True,
            props={
                "Objects": [assignment],
                "DoDeembed": kwargs.get("deembed", False),
                "RenormalizeAllTerminals": kwargs.get("renormalize", True),
                "Modes": {
                    "Mode1": {
                        "ModeNum": 1,
                        "UseIntLine": True,
                        "IntLine": {
                            "Start": [
                                str(item) if isinstance(item, str) else f"{item}mm"
                                for item in start
                            ],
                            "End": [
                                str(item) if isinstance(item, str) else f"{item}mm"
                                for item in end
                            ],
                        },
                        "CharImp": "Zpi",
                    }
                },
                "Impedance": f"{float(kwargs.get('impedance', 50))}ohm",
            },
        )
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


class FakeCoordinateHfss(FakeHfss):
    def __init__(self, *, mismatch_readback=False, **kwargs):
        super().__init__(**kwargs)
        self.modeler = FakeCoordinateModeler(mismatch_readback=mismatch_readback)
        self.variable_manager = SimpleNamespace(variables={"OX": "12.5mm"})


class FakeTypedPortHfss(FakeHfss):
    def __init__(self, *, mismatch_kind="", **kwargs):
        super().__init__(**kwargs)
        self.mismatch_kind = mismatch_kind
        self.modeler.object_names.append("PortSheet")
        self.modeler._objects["PortSheet"] = FakeObject(
            object_id=10,
            material_name="vacuum",
            face_id=201,
            face_centers=[[2.0, 0.0, 0.0]],
            volume=0.0,
            bounding_box=[1.0, -1.0, 0.0, 3.0, 1.0, 0.0],
        )

    def wave_port(self, assignment, reference=None, name=None, **kwargs):
        boundary = super().wave_port(
            assignment,
            reference=reference,
            name=name,
            **kwargs,
        )
        if self.mismatch_kind == "wave_port":
            boundary.props["NumModes"] = int(boundary.props["NumModes"]) + 1
        return boundary

    def lumped_port(self, assignment, reference=None, name=None, **kwargs):
        boundary = super().lumped_port(
            assignment,
            reference=reference,
            name=name,
            **kwargs,
        )
        if self.mismatch_kind == "lumped_port":
            boundary.props["Impedance"] = "999ohm"
        return boundary


class FakeFarFieldHfss(FakeHfss):
    def __init__(self, *, fail_create=False, mismatch_readback=False, **kwargs):
        super().__init__(**kwargs)
        self.are_there_simulations_running = False
        self._field_setups = {}
        self.fail_create = fail_create
        self.mismatch_readback = mismatch_readback

    @property
    def field_setup_names(self):
        return list(self._field_setups)

    @property
    def field_setups(self):
        return list(self._field_setups.values())

    def insert_infinite_sphere(
        self,
        *,
        definition,
        theta_start,
        theta_stop,
        theta_step,
        phi_start,
        phi_stop,
        phi_step,
        units,
        custom_coordinate_system,
        use_slant_polarization,
        polarization_angle,
        name,
    ):
        if self.fail_create:
            raise RuntimeError("synthetic far-field create failure")
        values = [
            theta_start,
            theta_stop,
            theta_step,
            phi_start,
            phi_stop,
            phi_step,
        ]
        if self.mismatch_readback:
            values[2] = float(theta_step) * 2
        setup = FakeFieldSetup(
            self,
            name,
            definition,
            values,
            units,
            "Slant" if use_slant_polarization else "Linear",
            polarization_angle,
        )
        self._field_setups[name] = setup
        return setup


class FakeGeometryHfss(FakeHfss):
    def __init__(self, *, geometry_fail_on="", boundary_fail_on="", **kwargs):
        super().__init__(**kwargs)
        self.are_there_simulations_running = False
        self.modeler = FakeGeometryModeler(fail_on=geometry_fail_on)
        self.boundary_fail_on = boundary_fail_on

    def wave_port(self, assignment, reference=None, name=None, **kwargs):
        if name == self.boundary_fail_on:
            raise RuntimeError("synthetic boundary failure")
        return super().wave_port(
            assignment,
            reference=reference,
            name=name,
            **kwargs,
        )


class FakeMoveHfss(FakeHfss):
    def __init__(self, *, move_fail_on="", **kwargs):
        super().__init__(**kwargs)
        self.are_there_simulations_running = False
        self.modeler = FakeMoveModeler(fail_on=move_fail_on)
        self.mesh = SimpleNamespace(meshoperation_names=[])
        self.boundaries = [
            FakeBoundary(self, "SheetPEC", "Perfect E", props={"Objects": ["sheet1"]})
        ]


class FakeRotateHfss(FakeHfss):
    def __init__(self, *, rotate_fail_on="", **kwargs):
        super().__init__(**kwargs)
        self.are_there_simulations_running = False
        self.modeler = FakeRotateModeler(fail_on=rotate_fail_on)
        self.mesh = SimpleNamespace(meshoperation_names=[])
        self.boundaries = [
            FakeBoundary(self, "SheetPEC", "Perfect E", props={"Objects": ["sheet1"]})
        ]


class FakeAntipadPoint:
    def __init__(self, x, y):
        self.position = [x, y]

    def IsArc(self):
        return 0

    def GetX(self):
        return self.position[0]

    def GetY(self):
        return self.position[1]


class FakeLayoutAntipadEditor:
    def __init__(self, app):
        self.app = app

    def FindObjects(self, field, value):
        if field == "Name":
            names = {"GND_PLANE", *self.app._voids}
            return [value] if value in names else []
        if field == "Type" and value == "circle void":
            return list(self.app._voids)
        return []

    def GetProperties(self, tab, name):
        assert tab == "BaseElementTab"
        if name == "GND_PLANE":
            return ["Type", "Name", "PlacementLayer", "Net"]
        return ["Type", "Name", "PlacementLayer", "Center", "Radius", "LockPosition"]

    def GetPropertyValue(self, tab, name, prop):
        if name == "GND_PLANE":
            return {
                "Type": "rect",
                "Name": name,
                "PlacementLayer": "TOP",
                "Net": "GND",
            }[prop]
        record = self.app._voids[name]
        return {
            "Type": "circle void",
            "Name": name,
            "PlacementLayer": "TOP",
            "Center": f"{record['center'][0]} ,{record['center'][1]}",
            "Radius": f"{record['radius']}mm",
            "LockPosition": "false",
        }[prop]

    def GetPolygonVoids(self, owner):
        return [name for name, record in self.app._voids.items() if record["owner"] == owner]

    def GetPolygon(self, name):
        return SimpleNamespace(GetPoints=lambda: self.app.modeler.geometries[name].points)

    def CreateCircleVoid(self, arguments):
        owner = arguments[arguments.index("owner:=") + 1]
        geometry = arguments[arguments.index("circle voidGeometry:=") + 1]
        name = geometry[geometry.index("Name:=") + 1]
        x = float(str(geometry[geometry.index("x:=") + 1]).removesuffix("mm"))
        y = float(str(geometry[geometry.index("y:=") + 1]).removesuffix("mm"))
        radius = float(str(geometry[geometry.index("r:=") + 1]).removesuffix("mm"))
        self.app._voids[name] = {"owner": owner, "center": [x, y], "radius": radius}
        self.app.modeler.circle_voids_names = list(self.app._voids)
        return name

    def Delete(self, name):
        names = [name] if isinstance(name, str) else list(name)
        for item in names:
            self.app._voids.pop(item, None)
        self.app.modeler.circle_voids_names = list(self.app._voids)


class FakeLayoutAntipad(FakeLayout):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._voids = {}
        self.modeler.geometries = {
            "GND_PLANE": SimpleNamespace(
                points=[
                    FakeAntipadPoint(-0.005, -0.005),
                    FakeAntipadPoint(0.005, -0.005),
                    FakeAntipadPoint(0.005, 0.005),
                    FakeAntipadPoint(-0.005, 0.005),
                ]
            )
        }
        self.modeler.oeditor = FakeLayoutAntipadEditor(self)


class FakeHfssAntipadModeler(FakeHfssModeler):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.object_names = ["L2_GND"]
        self._objects = {"L2_GND": self._plate(3.5, cut=False)}
        self.active_coordinate_system = "Global"
        self.oeditor = FakeMoveEditor(self)

    @staticmethod
    def _plate(volume, *, cut):
        centers = [
            [0, 0, 0.035],
            [0, 0, 0],
            [0, -5, 0.0175],
            [-5, 0, 0.0175],
            [0, 5, 0.0175],
            [5, 0, 0.0175],
        ]
        if cut:
            centers.append([1, -0.5, 0.0175])
        obj = FakeObject(
            object_id=6,
            material_name="copper",
            solve_inside=False,
            face_id=7,
            face_centers=centers,
            volume=volume,
            bounding_box=[-5, -5, 0, 5, 5, 0.035],
        )
        obj.name = "L2_GND"
        return obj

    def create_cylinder(self, orientation, origin, radius, height, num_sides=0, name=None, material=None):
        obj = FakeObject(
            object_id=34,
            material_name=material,
            solve_inside=True,
            face_id=35,
            face_centers=[[origin[0], origin[1], origin[2]]],
            volume=math.pi * float(radius) ** 2 * float(height),
            bounding_box=[
                origin[0] - radius,
                origin[1] - radius,
                origin[2],
                origin[0] + radius,
                origin[1] + radius,
                origin[2] + height,
            ],
        )
        obj.name = name
        self._objects[name] = obj
        self.object_names.append(name)
        return obj

    def subtract(self, blank, tool, keep_originals=False):
        radius = (self._objects[tool].bounding_box[3] - self._objects[tool].bounding_box[0]) / 2
        removed = math.pi * radius * radius * 0.035
        self.app._undo_plate = self._objects[blank]
        self._objects[blank] = self._plate(3.5 - removed, cut=True)
        self._objects.pop(tool)
        self.object_names.remove(tool)
        return True

    def delete(self, assignment=None):
        for name in list(assignment or []):
            self._objects.pop(name, None)
            if name in self.object_names:
                self.object_names.remove(name)
        return True

    def cleanup_objects(self):
        return True


class FakeHfssAntipadDesign:
    def __init__(self, app):
        self.app = app

    def Undo(self):
        self.app.modeler._objects["L2_GND"] = self.app._undo_plate
        if "__AP_TOOL" not in self.app.modeler.object_names:
            tool = self.app.modeler.create_cylinder(
                "Z", [1, -0.5, -0.0035], 0.8, 0.042, name="__AP_TOOL", material="vacuum"
            )
            tool.solve_inside = True


class FakeHfssAntipad(FakeHfss):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.materials = FakeMaterials()
        self._undo_plate = None
        self.modeler = FakeHfssAntipadModeler(self)
        self.odesign = FakeHfssAntipadDesign(self)
        self.mesh = SimpleNamespace(meshoperation_names=[])
        self.boundaries = [
            FakeBoundary(self, "PlatePEC", "Perfect E", props={"Objects": ["L2_GND"]})
        ]


class FakeAtomicSetupSweepHfss(FakeHfss):
    def __init__(self, *, sweep_fail_on="", **kwargs):
        super().__init__(**kwargs)
        self.are_there_simulations_running = False
        self.sweep_fail_on = sweep_fail_on

    def create_linear_count_sweep(self, *, setup, name, **kwargs):
        if name == self.sweep_fail_on:
            raise RuntimeError("synthetic sweep failure")
        return super().create_linear_count_sweep(setup=setup, name=name, **kwargs)

    def create_linear_step_sweep(self, *, setup, name, **kwargs):
        if name == self.sweep_fail_on:
            raise RuntimeError("synthetic sweep failure")
        return super().create_linear_step_sweep(setup=setup, name=name, **kwargs)


class FakeMaterialProperty:
    def __init__(self, value, unit=""):
        self.type = "simple"
        self.value = value
        self.unit = unit


class FakeMaterial:
    def __init__(self, name, *, dielectric, color, conductivity):
        self.name = name
        self._dielectric = dielectric
        self.material_appearance = color
        self._conductivity = FakeMaterialProperty(conductivity, "S_per_meter")
        self._permittivity = FakeMaterialProperty(1.0)
        self._permeability = FakeMaterialProperty(1.0)
        self._dielectric_loss_tangent = FakeMaterialProperty(0.0)
        self._magnetic_loss_tangent = FakeMaterialProperty(0.0)

    @property
    def conductivity(self):
        return self._conductivity

    @conductivity.setter
    def conductivity(self, value):
        if isinstance(value, FakeMaterialProperty):
            self._conductivity = value
        else:
            self._conductivity.value = value
            self._dielectric = float(value) < 100000.0

    @property
    def permittivity(self):
        return self._permittivity

    @permittivity.setter
    def permittivity(self, value):
        if isinstance(value, FakeMaterialProperty):
            self._permittivity = value
        else:
            self._permittivity.value = value

    @property
    def permeability(self):
        return self._permeability

    @permeability.setter
    def permeability(self, value):
        if isinstance(value, FakeMaterialProperty):
            self._permeability = value
        else:
            self._permeability.value = value

    @property
    def dielectric_loss_tangent(self):
        return self._dielectric_loss_tangent

    @dielectric_loss_tangent.setter
    def dielectric_loss_tangent(self, value):
        if isinstance(value, FakeMaterialProperty):
            self._dielectric_loss_tangent = value
        else:
            self._dielectric_loss_tangent.value = value

    @property
    def magnetic_loss_tangent(self):
        return self._magnetic_loss_tangent

    @magnetic_loss_tangent.setter
    def magnetic_loss_tangent(self, value):
        if isinstance(value, FakeMaterialProperty):
            self._magnetic_loss_tangent = value
        else:
            self._magnetic_loss_tangent.value = value

    def is_dielectric(self):
        return self._dielectric

    def update(self):
        return True


class FakeDefinitionManager:
    def __init__(self, materials):
        self.materials = materials

    def GetData(self, name):
        material = next(item for item in self.materials.values() if item.name == name)
        return [
            "NAME:" + name,
            "permittivity:=",
            material.permittivity.value,
            "permeability:=",
            material.permeability.value,
            "conductivity:=",
            material.conductivity.value,
            "dielectric_loss_tangent:=",
            material.dielectric_loss_tangent.value,
            "magnetic_loss_tangent:=",
            material.magnetic_loss_tangent.value,
            "appearance:=",
            list(material.material_appearance),
        ]

    def GetProjectMaterialNames(self):
        return [item.name for item in self.materials.values()]

    def EditMaterial(self, name, definition):
        material = next(item for item in self.materials.values() if item.name == name)
        values = {}
        for index, item in enumerate(definition[:-1]):
            if isinstance(item, str) and item.endswith(":="):
                values[item[:-2]] = definition[index + 1]
        for property_name in (
            "permittivity",
            "permeability",
            "conductivity",
            "dielectric_loss_tangent",
            "magnetic_loss_tangent",
        ):
            if property_name in values:
                setattr(material, property_name, float(values[property_name]))
        if "appearance" in values:
            material.material_appearance = list(values["appearance"])
        return True

    def AddMaterial(self, definition):
        name = str(definition[0]).removeprefix("NAME:")
        if name.casefold() in self.materials:
            raise RuntimeError("material already exists")
        material = FakeMaterial(
            name,
            dielectric=True,
            color=[128, 128, 128, 0.0],
            conductivity=0.0,
        )
        self.materials[name.casefold()] = material
        self.EditMaterial(name, definition)
        return True


class FakeMaterials:
    def __init__(self, *, mismatch_create_readback=False):
        self.material_keys = {
            "vacuum": FakeMaterial(
                "vacuum",
                dielectric=True,
                color=(220, 220, 230),
                conductivity=0.0,
            ),
            "copper": FakeMaterial(
                "copper",
                dielectric=False,
                color=(184, 115, 51),
                conductivity=58000000.0,
            ),
        }
        self.odefinition_manager = FakeDefinitionManager(self.material_keys)
        self.omaterial_manager = self.odefinition_manager
        self.mat_names_aedt = ["vacuum", "copper", "library_only"]
        self.mismatch_create_readback = mismatch_create_readback

    def _get_aedt_case_name(self, name):
        by_name = {item.casefold(): item for item in self.mat_names_aedt}
        return by_name.get(str(name).casefold(), False)

    def _aedmattolibrary(self, name):
        return self.material_keys.get(str(name).casefold(), False)

    def add_material(self, name, properties=None):
        if self._get_aedt_case_name(name):
            return self.material_keys.get(str(name).casefold(), False)
        properties = dict(properties or {})
        conductivity = float(properties.get("conductivity", 0.0))
        material = FakeMaterial(
            str(name),
            dielectric=conductivity < 100000.0,
            color=[128, 128, 128, 0.0],
            conductivity=conductivity,
        )
        for property_name in (
            "permittivity",
            "permeability",
            "conductivity",
            "dielectric_loss_tangent",
            "magnetic_loss_tangent",
        ):
            getattr(material, property_name).value = float(properties.get(property_name, 0.0))
        if self.mismatch_create_readback:
            material.permittivity.value += 1.0
        self.material_keys[str(name).casefold()] = material
        self.mat_names_aedt.append(str(name))
        return material

    def remove_material(self, name, library="Project"):
        removed = self.material_keys.pop(str(name).casefold(), None)
        if removed is None:
            return False
        self.mat_names_aedt = [
            item for item in self.mat_names_aedt if item.casefold() != str(name).casefold()
        ]
        return True


class FakeMaterialHfss(FakeHfss):
    def __init__(self, *, fail_material="", mismatch_create_readback=False, **kwargs):
        super().__init__(**kwargs)
        self.are_there_simulations_running = False
        self.materials = FakeMaterials(mismatch_create_readback=mismatch_create_readback)
        self.fail_material = fail_material
        self.modeler.object_names = ["box1", "box2", "sheet1"]
        self.modeler._objects = {
            "box1": FakeObject(
                object_id=11,
                material_name="vacuum",
                solve_inside=True,
                color=(1, 2, 3),
            ),
            "box2": FakeObject(
                object_id=12,
                material_name="vacuum",
                solve_inside=True,
                color=(4, 5, 6),
            ),
            "sheet1": FakeObject(
                object_id=13,
                material_name="vacuum",
                solve_inside=True,
                volume=0.0,
            ),
        }

    def assign_material(self, assignment, material):
        names = [assignment] if isinstance(assignment, str) else list(assignment)
        target = self.materials.material_keys[str(material).casefold()]
        for name in names:
            obj = self.modeler[name]
            obj.material_name = target.name
            obj.solve_inside = target.is_dielectric()
            obj.color = target.material_appearance
            if str(material).casefold() == self.fail_material.casefold():
                self.fail_material = ""
                raise RuntimeError("synthetic partial material assignment failure")
        return True


class FakeSurfaceBoundaryHfss(FakeMaterialHfss):
    def __init__(self, *, mismatch_readback=False, fail_create=False, **kwargs):
        super().__init__(**kwargs)
        self.modeler._objects["box2"].faces[0].id = 201
        self.modeler._objects["sheet1"].faces[0].id = 301
        for obj in self.modeler._objects.values():
            for face in obj.faces:
                face.is_planar = True
        self.axis_directions = SimpleNamespace(
            XNeg=0,
            YNeg=1,
            ZNeg=2,
            XPos=3,
            YPos=4,
            ZPos=5,
        )
        integration_lines = {
            0: ([-1.0, 0.0, 0.0], [1.0, 0.0, 0.0]),
            1: ([0.0, -1.0, 0.0], [0.0, 1.0, 0.0]),
            2: ([0.0, 0.0, -1.0], [0.0, 0.0, 1.0]),
            3: ([1.0, 0.0, 0.0], [-1.0, 0.0, 0.0]),
            4: ([0.0, 1.0, 0.0], [0.0, -1.0, 0.0]),
            5: ([0.0, 0.0, 1.0], [0.0, 0.0, -1.0]),
        }
        self.modeler.get_mid_points_on_dir = (
            lambda assignment, direction: integration_lines[direction]
        )
        self.mismatch_readback = mismatch_readback
        self.fail_create = fail_create

    def _surface_boundary(self, name, boundary_type, assignment, options):
        if self.fail_create:
            raise RuntimeError("synthetic surface boundary create failure")
        selections = list(assignment) if isinstance(assignment, list) else [assignment]
        props = {
            "Objects" if isinstance(selections[0], str) else "Faces": list(selections),
            **options,
        }
        properties = {
            "Name": name,
            "Type": boundary_type,
            "Assignment": ",".join(str(item) for item in selections),
        }
        if boundary_type == "Perfect E":
            properties["Inf Ground Plane"] = props.get("InfGroundPlane", False)
        elif boundary_type == "Finite Conductivity":
            properties.update(
                {
                    "Material/Material": props["Material"],
                    "Inf Ground Plane": props["InfGroundPlane"],
                    "Use Thickness": props["UseThickness"],
                    "Thickness": props["Thickness"],
                    "Roughness": props["Roughness"],
                }
            )
        elif boundary_type == "Impedance":
            properties.update(
                {
                    "Resistance": props["Resistance"],
                    "Reactance": props["Reactance"],
                    "Inf Ground Plane": props["InfGroundPlane"],
                }
            )
        elif boundary_type == "Lumped RLC":
            properties.update(
                {
                    "RLC Type": props["RLC Type"],
                    "Use Resist": props.get("UseResist", False),
                    "Resistance": props.get("Resistance", ""),
                    "Use Induct": props.get("UseInduct", False),
                    "Inductance": props.get("Inductance", ""),
                    "Use Cap": props.get("UseCap", False),
                    "Capacitance": props.get("Capacitance", ""),
                }
            )
        if self.mismatch_readback:
            self.mismatch_readback = False
            if boundary_type == "Perfect E":
                props["InfGroundPlane"] = not props.get("InfGroundPlane", False)
            else:
                boundary_type = "Perfect H"
                properties["Type"] = boundary_type
        boundary = FakeBoundary(self, name, boundary_type)
        boundary.props = props
        boundary.properties = properties
        self.boundaries.append(boundary)
        return boundary

    def assign_perfect_e(self, assignment, is_infinite_ground=False, name=None, **kwargs):
        return self._surface_boundary(
            name,
            "Perfect E",
            assignment,
            {"InfGroundPlane": is_infinite_ground},
        )

    def assign_perfect_h(self, assignment, name=None, **kwargs):
        return self._surface_boundary(name, "Perfect H", assignment, {})

    def assign_finite_conductivity(
        self,
        assignment,
        material=None,
        use_thickness=False,
        thickness="0.1mm",
        roughness="0um",
        is_infinite_ground=False,
        is_two_side=False,
        is_internal=True,
        is_shell_element=False,
        name=None,
        **kwargs,
    ):
        return self._surface_boundary(
            name,
            "Finite Conductivity",
            assignment,
            {
                "UseMaterial": True,
                "Material": material,
                "UseThickness": use_thickness,
                "Thickness": thickness,
                "Roughness": roughness,
                "InfGroundPlane": is_infinite_ground,
                "IsTwoSided": is_two_side,
                "IsInternal": is_internal,
                "IsShellElement": is_shell_element,
            },
        )

    def assign_impedance_to_sheet(
        self,
        assignment,
        name=None,
        resistance=50.0,
        reactance=0.0,
        is_infinite_ground=False,
        coordinate_system="Global",
    ):
        return self._surface_boundary(
            name,
            "Impedance",
            assignment,
            {
                "Resistance": str(resistance),
                "Reactance": str(reactance),
                "InfGroundPlane": is_infinite_ground,
            },
        )

    def assign_lumped_rlc_to_sheet(
        self,
        assignment,
        start_direction=0,
        name=None,
        rlc_type="Parallel",
        resistance=None,
        inductance=None,
        capacitance=None,
    ):
        start, end = self.modeler.get_mid_points_on_dir(assignment, start_direction)
        units = self.modeler.model_units
        options = {
            "CurrentLine": {
                "Start": [str(float(item)) + units for item in start],
                "End": [str(float(item)) + units for item in end],
            },
            "RLC Type": rlc_type,
        }
        for value, use_key, value_key, unit in (
            (resistance, "UseResist", "Resistance", "ohm"),
            (inductance, "UseInduct", "Inductance", "H"),
            (capacitance, "UseCap", "Capacitance", "F"),
        ):
            if value is not None:
                options[use_key] = True
                options[value_key] = str(value) + unit
        return self._surface_boundary(name, "Lumped RLC", assignment, options)


class FakeMeshOperation:
    def __init__(self, mesh, name, props):
        self._mesh = mesh
        self.name = name
        self.type = "Length Based"
        self.props = props

    def delete(self):
        self._mesh._records.pop(self.name, None)
        self._mesh._meshoperations = None
        return True


class FakeMeshModule:
    def __init__(self, mesh):
        self.mesh = mesh

    def DeleteOp(self, names):
        for name in names:
            self.mesh._records.pop(name, None)
        self.mesh._meshoperations = None


class FakeMesh:
    def __init__(self, *, fail_readback=False):
        self._records = {}
        self._meshoperations = None
        self.fail_readback = fail_readback
        self.omeshmodule = FakeMeshModule(self)

    @property
    def meshoperations(self):
        if self._meshoperations is None:
            self._meshoperations = list(self._records.values())
        return self._meshoperations

    @property
    def meshoperation_names(self):
        return list(self._records)

    def assign_length_mesh(
        self,
        assignment,
        inside_selection=True,
        maximum_length="1mm",
        maximum_elements=1000,
        name=None,
    ):
        props = {
            "Name": name,
            "Type": "Length Based",
            "Assignment": ",".join(assignment),
            "Objects": list(assignment),
            "Region": "Inside Selection" if inside_selection else "On Selection",
            "Enabled": True,
            "Restrict Length": maximum_length is not None,
            "Max Length": maximum_length or "1mm",
            "Restrict Max Elems": maximum_elements is not None,
            "Max Elems": str(maximum_elements or 1000),
        }
        if self.fail_readback:
            self.fail_readback = False
            props["Max Length"] = "999mm"
        operation = FakeMeshOperation(self, name, props)
        self._records[name] = operation
        self._meshoperations = None
        return operation


class FakeMeshHfss(FakeMaterialHfss):
    def __init__(self, *, fail_mesh_readback=False, **kwargs):
        super().__init__(**kwargs)
        self.mesh = FakeMesh(fail_readback=fail_mesh_readback)


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
        if command == "hfss_setup_sweep_create_preview":
            return {
                "preview_id": "setup-sweep-preview-1",
                "snapshot_digest": "setup-sweep-digest-1",
            }
        if command == "hfss_material_create_preview":
            return {
                "preview_id": "material-create-preview-1",
                "snapshot_digest": "material-create-digest-1",
            }
        if command == "hfss_material_update_preview":
            return {
                "preview_id": "material-update-preview-1",
                "snapshot_digest": "material-update-digest-1",
            }
        if command == "hfss_material_delete_preview":
            return {
                "preview_id": "material-delete-preview-1",
                "snapshot_digest": "material-delete-digest-1",
            }
        if command == "layout_material_create_assign_preview":
            return {
                "preview_id": "layout-material-create-assign-preview-1",
                "snapshot_digest": "layout-material-create-assign-digest-1",
            }
        if command == "layout_via_create_preview":
            return {
                "preview_id": "layout-via-create-preview-1",
                "snapshot_digest": "layout-via-create-digest-1",
            }
        if command == "layout_via_update_preview":
            return {
                "preview_id": "layout-via-update-preview-1",
                "snapshot_digest": "layout-via-update-digest-1",
            }
        if command == "layout_via_delete_preview":
            return {
                "preview_id": "layout-via-delete-preview-1",
                "snapshot_digest": "layout-via-delete-digest-1",
            }
        if command == "layout_antipad_circle_create_preview":
            return {
                "preview_id": "layout-antipad-preview-1",
                "snapshot_digest": "layout-antipad-digest-1",
            }
        if command == "hfss_material_assign_preview":
            return {
                "preview_id": "material-preview-1",
                "snapshot_digest": "material-digest-1",
            }
        if command == "hfss_length_mesh_create_preview":
            return {
                "preview_id": "length-mesh-preview-1",
                "snapshot_digest": "length-mesh-digest-1",
            }
        if command == "hfss_infinite_sphere_create_preview":
            return {
                "preview_id": "infinite-sphere-preview-1",
                "snapshot_digest": "infinite-sphere-digest-1",
            }
        if command == "hfss_surface_boundary_create_preview":
            return {
                "preview_id": "surface-boundary-preview-1",
                "snapshot_digest": "surface-boundary-digest-1",
            }
        if command == "hfss_coordinate_system_create_preview":
            return {
                "preview_id": "coordinate-system-preview-1",
                "snapshot_digest": "coordinate-system-digest-1",
            }
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
        if command == "hfss_geometry_move_preview":
            return {
                "preview_id": "geometry-move-preview-1",
                "snapshot_digest": "geometry-move-digest-1",
            }
        if command == "hfss_geometry_rotate_preview":
            return {
                "preview_id": "geometry-rotate-preview-1",
                "snapshot_digest": "geometry-rotate-digest-1",
            }
        if command == "hfss_antipad_subtract_preview":
            return {
                "preview_id": "hfss-antipad-preview-1",
                "snapshot_digest": "hfss-antipad-digest-1",
            }
        if command == "hfss_geometry_boundary_create_preview":
            return {
                "preview_id": "geometry-boundary-preview-1",
                "snapshot_digest": "geometry-boundary-digest-1",
            }
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
    assert [face["face_id"] for face in geometry["objects"][0]["faces"]] == sorted(
        face["face_id"] for face in geometry["objects"][0]["faces"]
    )
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
                "options": {
                    "modes": 1,
                    "integration_line_direction": "YPos",
                    "characteristic_impedance": "Zpi",
                },
            },
        )
    boundary_result = backend.execute(
        target,
        "hfss_boundary_apply",
        {"preview_id": boundary_preview["preview_id"]},
    )
    assert boundary_result["status"] == "verified"
    assert boundary_result["boundary_name"] == "P3"
    assert boundary_result["boundary"]["kind"] == "wave_port"
    assert boundary_result["boundary"]["options"]["mode_count"] == 1
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


def test_backend_atomically_creates_hfss_setup_and_sweep_with_readback():
    apps = []

    def factory(**kwargs):
        app = FakeAtomicSetupSweepHfss(**kwargs)
        apps.append(app)
        return app

    backend = LiveAedtBackend(desktop_factory=FakeDesktop, hfss_factory=factory)
    target = AedtTarget("pid", 42)
    preview = backend.execute(
        target,
        "hfss_setup_sweep_create_preview",
        {
            "project_name": "Board",
            "design_name": "HFSS1",
            "setup": {
                "name": "AtomicSetup",
                "type": "HFSSDriven",
                "properties": {
                    "Frequency": "10GHz",
                    "MaximumPasses": 5,
                    "MaxDeltaS": 0.02,
                },
            },
            "sweep": {
                "name": "AtomicSweep",
                "range_type": "LinearCount",
                "sweep_type": "Interpolating",
                "unit": "GHz",
                "start_frequency": 1,
                "stop_frequency": 20,
                "count": 201,
                "save_fields": True,
            },
        },
    )
    assert preview["project_dirty"] is False
    assert preview["project_saved"] is False
    assert preview["setup"]["name"] == "AtomicSetup"
    assert preview["sweep"]["name"] == "AtomicSweep"

    result = backend.execute(
        target,
        "hfss_setup_sweep_create_apply",
        {"preview_id": preview["preview_id"]},
    )
    assert result["status"] == "verified"
    assert result["created_setup_name"] == "AtomicSetup"
    assert result["created_sweep_name"] == "AtomicSweep"
    assert result["setup_inventory"] == {
        "name": "AtomicSetup",
        "type": "HFSSDriven",
        "properties": {
            "Frequency": "10GHz",
            "MaximumPasses": 5,
            "MaxDeltaS": 0.02,
        },
        "sweeps": ["AtomicSweep"],
    }
    assert result["atomic_setup_sweep_transaction"] is True
    assert result["automatic_rollback_on_failure"] is True
    assert result["project_saved"] is False
    assert _sweep_names_for_test(apps[0], "AtomicSetup") == ["AtomicSweep"]


def test_backend_atomic_hfss_setup_sweep_rolls_back_and_rejects_stale_preview():
    apps = []

    def factory(**kwargs):
        app = FakeAtomicSetupSweepHfss(sweep_fail_on="BadSweep", **kwargs)
        apps.append(app)
        return app

    backend = LiveAedtBackend(desktop_factory=FakeDesktop, hfss_factory=factory)
    target = AedtTarget("pid", 42)
    request = {
        "project_name": "Board",
        "design_name": "HFSS1",
        "setup": {
            "name": "MustRollback",
            "type": "HFSSDriven",
            "properties": {"Frequency": "10GHz"},
        },
        "sweep": {
            "name": "BadSweep",
            "range_type": "LinearCount",
            "start_frequency": 1,
            "stop_frequency": 10,
            "count": 11,
        },
    }
    preview = backend.execute(target, "hfss_setup_sweep_create_preview", request)
    with pytest.raises(LiveBackendError, match="synthetic sweep failure"):
        backend.execute(
            target,
            "hfss_setup_sweep_create_apply",
            {"preview_id": preview["preview_id"]},
        )
    assert apps[0].setup_names == ["Setup1"]

    apps[0].sweep_fail_on = ""
    stale = backend.execute(target, "hfss_setup_sweep_create_preview", request)
    apps[0].create_setup("ExternalSetup")
    with pytest.raises(LiveBackendError, match="stale HFSS setup and sweep"):
        backend.execute(
            target,
            "hfss_setup_sweep_create_apply",
            {"preview_id": stale["preview_id"]},
        )
    assert "MustRollback" not in apps[0].setup_names

    port_stale = backend.execute(target, "hfss_setup_sweep_create_preview", request)
    apps[0].ports = []
    with pytest.raises(LiveBackendError, match="stale HFSS setup and sweep"):
        backend.execute(
            target,
            "hfss_setup_sweep_create_apply",
            {"preview_id": port_stale["preview_id"]},
        )


def test_backend_atomic_hfss_setup_sweep_rejects_interpolating_without_ports():
    class NoPortHfss(FakeAtomicSetupSweepHfss):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.ports = []

    backend = LiveAedtBackend(desktop_factory=FakeDesktop, hfss_factory=NoPortHfss)
    request = {
        "project_name": "Board",
        "design_name": "HFSS1",
        "setup": {"name": "NoPortSetup", "properties": {"Frequency": "10GHz"}},
        "sweep": {
            "name": "NoPortSweep",
            "sweep_type": "Interpolating",
            "start_frequency": 1,
            "stop_frequency": 10,
            "count": 11,
        },
    }
    with pytest.raises(LiveBackendError, match="require at least one existing port"):
        backend.execute(
            AedtTarget("pid", 42),
            "hfss_setup_sweep_create_preview",
            request,
        )
    request["sweep"]["sweep_type"] = "Discrete"
    preview = backend.execute(
        AedtTarget("pid", 42),
        "hfss_setup_sweep_create_preview",
        request,
    )
    assert preview["existing_port_names"] == []


@pytest.mark.parametrize(
    "setup,sweep,error",
    [
        (
            {"name": "S", "type": "HFSSTransient"},
            {"name": "Sw", "start_frequency": 1, "stop_frequency": 2, "count": 3},
            "HFSSDriven or HFSSDrivenAuto",
        ),
        (
            {"name": "S", "properties": {"MaximumPasses": 0}},
            {"name": "Sw", "start_frequency": 1, "stop_frequency": 2, "count": 3},
            "MaximumPasses must be an integer between 1 and 1000",
        ),
        (
            {"name": "S", "properties": {"MinimumPasses": 5, "MaximumPasses": 2}},
            {"name": "Sw", "start_frequency": 1, "stop_frequency": 2, "count": 3},
            "MinimumPasses must not exceed MaximumPasses",
        ),
        (
            {"name": "S"},
            {
                "name": "Sw",
                "range_type": "LinearStep",
                "start_frequency": 1,
                "stop_frequency": 2,
                "step_size": 0.000001,
            },
            "exceed 100001 frequency points",
        ),
        (
            {"name": "S"},
            {
                "name": "Sw",
                "start_frequency": 1,
                "stop_frequency": 2,
                "count": 3,
                "save_fields": "yes",
            },
            "save_fields must be boolean",
        ),
    ],
)
def test_backend_atomic_hfss_setup_sweep_preview_rejects_unsafe_requests(
    setup,
    sweep,
    error,
):
    backend = LiveAedtBackend(
        desktop_factory=FakeDesktop,
        hfss_factory=FakeAtomicSetupSweepHfss,
    )
    with pytest.raises(LiveBackendError, match=error):
        backend.execute(
            AedtTarget("pid", 42),
            "hfss_setup_sweep_create_preview",
            {
                "project_name": "Board",
                "design_name": "HFSS1",
                "setup": setup,
                "sweep": sweep,
            },
        )


def _sweep_names_for_test(app, setup_name):
    return sorted(item.name for item in app.get_setup(setup_name).sweeps)


def test_backend_lists_bounded_hfss_project_material_inventory_without_changes():
    backend = LiveAedtBackend(
        desktop_factory=FakeDesktop,
        hfss_factory=FakeMaterialHfss,
    )
    result = backend.execute(
        AedtTarget("pid", 42),
        "hfss_material_inventory",
        {
            "project_name": "Board",
            "design_name": "HFSS1",
            "max_items": 1,
        },
    )
    assert result["material_count"] == 2
    assert result["returned_count"] == 1
    assert result["truncated"] is True
    assert result["materials"][0]["canonical_name"] == "copper"
    assert result["materials"][0]["definition_digest"]
    assert result["design_unchanged"] is True
    complete = backend.execute(
        AedtTarget("pid", 42),
        "hfss_material_inventory",
        {
            "project_name": "Board",
            "design_name": "HFSS1",
            "max_items": 2,
        },
    )
    assert complete["snapshot_digest"] == result["snapshot_digest"]


def test_backend_creates_typed_hfss_material_with_appearance_and_readback():
    apps = []

    def factory(**kwargs):
        app = FakeMaterialHfss(**kwargs)
        apps.append(app)
        return app

    backend = LiveAedtBackend(desktop_factory=FakeDesktop, hfss_factory=factory)
    target = AedtTarget("pid", 42)
    request = {
        "project_name": "Board",
        "design_name": "HFSS1",
        "material_name": "HarnessLaminate",
        "permittivity": 4.2,
        "permeability": 1.01,
        "conductivity": 0.005,
        "dielectric_loss_tangent": 0.018,
        "magnetic_loss_tangent": 0.002,
        "appearance": [10, 20, 30, 0.4],
    }
    preview = backend.execute(target, "hfss_material_create_preview", request)
    assert preview["project_dirty"] is False
    assert preview["project_saved"] is False
    assert preview["existing_material_count"] == 2
    assert "harnesslaminate" not in apps[0].materials.material_keys

    result = backend.execute(
        target,
        "hfss_material_create_apply",
        {"preview_id": preview["preview_id"]},
    )
    assert result["status"] == "verified"
    assert result["created_material_name"] == "HarnessLaminate"
    assert result["material_count"] == 3
    assert result["material"]["appearance"] == [10, 20, 30, 0.4]
    assert result["material"]["electrical_properties"]["permittivity"]["value"] == 4.2
    assert result["automatic_rollback_on_failure"] is True
    assert result["project_saved"] is False


def test_backend_hfss_material_create_rejects_stale_and_rolls_back_bad_readback():
    apps = []

    def factory(**kwargs):
        app = FakeMaterialHfss(mismatch_create_readback=True, **kwargs)
        apps.append(app)
        return app

    backend = LiveAedtBackend(desktop_factory=FakeDesktop, hfss_factory=factory)
    target = AedtTarget("pid", 42)
    request = {
        "project_name": "Board",
        "design_name": "HFSS1",
        "material_name": "HarnessBadReadback",
        "permittivity": 3.7,
    }
    preview = backend.execute(target, "hfss_material_create_preview", request)
    before_names = sorted(apps[0].materials.material_keys)
    with pytest.raises(LiveBackendError, match="permittivity readback mismatch"):
        backend.execute(
            target,
            "hfss_material_create_apply",
            {"preview_id": preview["preview_id"]},
        )
    assert sorted(apps[0].materials.material_keys) == before_names

    apps[0].materials.mismatch_create_readback = False
    stale = backend.execute(
        target,
        "hfss_material_create_preview",
        {**request, "material_name": "HarnessStale"},
    )
    apps[0].materials.add_material("ExternalMaterial", properties={"permittivity": 2.2})
    with pytest.raises(LiveBackendError, match="stale HFSS material create preview"):
        backend.execute(
            target,
            "hfss_material_create_apply",
            {"preview_id": stale["preview_id"]},
        )
    assert "harnessstale" not in apps[0].materials.material_keys


def test_backend_hfss_material_create_detects_uncached_external_project_material():
    apps = []

    def factory(**kwargs):
        app = FakeMaterialHfss(**kwargs)
        apps.append(app)
        return app

    backend = LiveAedtBackend(desktop_factory=FakeDesktop, hfss_factory=factory)
    target = AedtTarget("pid", 42)
    preview = backend.execute(
        target,
        "hfss_material_create_preview",
        {
            "project_name": "Board",
            "design_name": "HFSS1",
            "material_name": "MustNotCreate",
        },
    )
    original_names = apps[0].materials.odefinition_manager.GetProjectMaterialNames
    apps[0].materials.odefinition_manager.GetProjectMaterialNames = lambda: [
        *original_names(),
        "UncachedExternal",
    ]

    def load_external(name):
        material = FakeMaterial(
            name,
            dielectric=True,
            color=[100, 100, 100, 0.0],
            conductivity=0.0,
        )
        apps[0].materials.material_keys[name.casefold()] = material
        return material

    apps[0].materials._aedmattolibrary = load_external
    with pytest.raises(LiveBackendError, match="stale HFSS material create preview"):
        backend.execute(
            target,
            "hfss_material_create_apply",
            {"preview_id": preview["preview_id"]},
        )
    assert "mustnotcreate" not in apps[0].materials.material_keys


def test_backend_hfss_material_create_rejects_running_simulation_and_wrong_design_type():
    running_apps = []

    def running_factory(**kwargs):
        app = FakeMaterialHfss(**kwargs)
        app.are_there_simulations_running = True
        running_apps.append(app)
        return app

    running_backend = LiveAedtBackend(
        desktop_factory=FakeDesktop,
        hfss_factory=running_factory,
    )
    request = {
        "project_name": "Board",
        "design_name": "HFSS1",
        "material_name": "HarnessMaterial",
    }
    with pytest.raises(LiveBackendError, match="while a simulation is running"):
        running_backend.execute(
            AedtTarget("pid", 42),
            "hfss_material_create_preview",
            request,
        )

    def wrong_factory(**kwargs):
        app = FakeMaterialHfss(**kwargs)
        app.design_type = "HFSS 3D Layout Design"
        return app

    wrong_backend = LiveAedtBackend(
        desktop_factory=FakeDesktop,
        hfss_factory=wrong_factory,
    )
    with pytest.raises(LiveBackendError, match="requires an HFSS 3D design"):
        wrong_backend.execute(
            AedtTarget("pid", 42),
            "hfss_material_create_preview",
            request,
        )


@pytest.mark.parametrize(
    "request_payload,error",
    [
        ({"material_name": "copper"}, "already exists"),
        ({"material_name": "LIBRARY_ONLY"}, "material library entry"),
        ({"material_name": "bad/name"}, "safe AEDT material name"),
        ({"material_name": "M", "permittivity": 0}, "permittivity must be between"),
        ({"material_name": "M", "conductivity": -1}, "conductivity must be between"),
        ({"material_name": "M", "dielectric_loss_tangent": True}, "finite number"),
        ({"material_name": "M", "appearance": [0, 0, 0]}, "appearance must contain"),
        ({"material_name": "M", "appearance": [0, 256, 0, 0.5]}, r"appearance\[1\]"),
        ({"material_name": "M", "appearance": [0, 0, 0, 2]}, r"appearance\[3\]"),
    ],
)
def test_backend_hfss_material_create_preview_rejects_unsafe_specs(
    request_payload,
    error,
):
    backend = LiveAedtBackend(
        desktop_factory=FakeDesktop,
        hfss_factory=FakeMaterialHfss,
    )
    with pytest.raises(LiveBackendError, match=error):
        backend.execute(
            AedtTarget("pid", 42),
            "hfss_material_create_preview",
            {
                "project_name": "Board",
                "design_name": "HFSS1",
                **request_payload,
            },
        )


def test_backend_updates_exact_hfss_material_batch_and_preserves_references():
    apps = []

    def factory(**kwargs):
        app = FakeMaterialHfss(**kwargs)
        first = app.materials.add_material(
            "HarnessLaminateA",
            properties={
                "permittivity": 3.2,
                "permeability": 1.01,
                "conductivity": 0.02,
                "dielectric_loss_tangent": 0.011,
                "magnetic_loss_tangent": 0.003,
            },
        )
        first.material_appearance = [11, 22, 33, 0.35]
        second = app.materials.add_material(
            "HarnessLaminateB",
            properties={
                "permittivity": 4.1,
                "permeability": 1.02,
                "conductivity": 0.04,
                "dielectric_loss_tangent": 0.015,
                "magnetic_loss_tangent": 0.005,
            },
        )
        second.material_appearance = [41, 42, 43, 0.45]
        assert app.assign_material("box1", "HarnessLaminateA") is True
        assert app.assign_material("box2", "HarnessLaminateB") is True
        apps.append(app)
        return app

    backend = LiveAedtBackend(desktop_factory=FakeDesktop, hfss_factory=factory)
    target = AedtTarget("pid", 42)
    updates = [
        {
            "material_name": "HarnessLaminateA",
            "permittivity": 4.4,
            "appearance": [44, 55, 66, 0.6],
        },
        {
            "material_name": "HarnessLaminateB",
            "conductivity": 0.5,
            "dielectric_loss_tangent": 0.021,
        },
    ]
    preview = backend.execute(
        target,
        "hfss_material_update_preview",
        {
            "project_name": "Board",
            "design_name": "HFSS1",
            "updates": updates,
            "max_materials": 2,
        },
    )
    assert preview["target_count"] == 2
    assert preview["reference_count"] == 2
    assert preview["project_dirty"] is False
    assert apps[0].materials.material_keys["harnesslaminatea"].permittivity.value == 3.2

    result = backend.execute(
        target,
        "hfss_material_update_apply",
        {"preview_id": preview["preview_id"]},
    )
    assert result["status"] == "verified"
    assert result["updated_material_names"] == [
        "HarnessLaminateA",
        "HarnessLaminateB",
    ]
    assert result["updated_material_count"] == 2
    assert result["references_after"] == result["references_before"]
    after = {item["canonical_name"]: item for item in result["targets_after"]}
    assert after["HarnessLaminateA"]["electrical_properties"]["permittivity"][
        "value"
    ] == 4.4
    assert after["HarnessLaminateA"]["appearance"] == [44, 55, 66, 0.6]
    assert after["HarnessLaminateA"]["electrical_properties"]["conductivity"][
        "value"
    ] == 0.02
    assert after["HarnessLaminateB"]["electrical_properties"]["conductivity"][
        "value"
    ] == 0.5
    assert after["HarnessLaminateB"]["electrical_properties"][
        "dielectric_loss_tangent"
    ]["value"] == 0.021
    assert result["automatic_rollback_on_failure"] is True
    assert result["project_saved"] is False


def test_backend_hfss_material_update_rejects_stale_and_rolls_back_batch(
    monkeypatch,
):
    apps = []

    def factory(**kwargs):
        app = FakeMaterialHfss(**kwargs)
        app.materials.add_material(
            "HarnessUpdateA",
            properties={"permittivity": 3.0, "conductivity": 0.01},
        )
        app.materials.add_material(
            "HarnessUpdateB",
            properties={"permittivity": 4.0, "conductivity": 0.02},
        )
        apps.append(app)
        return app

    backend = LiveAedtBackend(desktop_factory=FakeDesktop, hfss_factory=factory)
    target = AedtTarget("pid", 42)
    request = {
        "project_name": "Board",
        "design_name": "HFSS1",
        "updates": [
            {"material_name": "HarnessUpdateA", "permittivity": 3.5},
            {"material_name": "HarnessUpdateB", "permittivity": 4.5},
        ],
    }
    stale = backend.execute(target, "hfss_material_update_preview", request)
    apps[0].materials.add_material("ExternalMaterial", properties={"permittivity": 2.2})
    with pytest.raises(LiveBackendError, match="stale HFSS material update preview"):
        backend.execute(
            target,
            "hfss_material_update_apply",
            {"preview_id": stale["preview_id"]},
        )
    assert apps[0].materials.material_keys["harnessupdatea"].permittivity.value == 3.0
    assert apps[0].materials.remove_material("ExternalMaterial") is True

    rollback_preview = backend.execute(target, "hfss_material_update_preview", request)
    before = backend.execute(
        target,
        "hfss_material_inventory",
        {"project_name": "Board", "design_name": "HFSS1", "max_items": 500},
    )
    from aedt_agent.live import backend as backend_module

    monkeypatch.setattr(
        backend_module,
        "_verify_hfss_material_update_catalog",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            LiveBackendError("injected material update readback failure")
        ),
    )
    with pytest.raises(LiveBackendError, match="injected material update readback failure"):
        backend.execute(
            target,
            "hfss_material_update_apply",
            {"preview_id": rollback_preview["preview_id"]},
        )
    after = backend.execute(
        target,
        "hfss_material_inventory",
        {"project_name": "Board", "design_name": "HFSS1", "max_items": 500},
    )
    assert after["snapshot_digest"] == before["snapshot_digest"]
    assert apps[0].materials.material_keys["harnessupdatea"].permittivity.value == 3.0
    assert apps[0].materials.material_keys["harnessupdateb"].permittivity.value == 4.0


@pytest.mark.parametrize(
    "updates,error",
    [
        ([], "at least one typed material update"),
        ([{"material_name": "HarnessUpdate", "unknown": 1}], "unsupported"),
        ([{"material_name": "HarnessUpdate"}], "change at least one"),
        (
            [
                {"material_name": "HarnessUpdate", "permittivity": 4.0},
                {"material_name": "harnessupdate", "permittivity": 5.0},
            ],
            "unique case-insensitively",
        ),
        ([{"material_name": "harnessupdate", "permittivity": 4.0}], "exact case"),
        ([{"material_name": "HarnessUpdate", "permittivity": 3.0}], "no-op"),
        (
            [{"material_name": "HarnessUpdate", "conductivity": 100000.0}],
            "cannot cross the dielectric/conductor threshold",
        ),
        (
            [{"material_name": "HarnessUpdate", "appearance": [0, 0, 0]}],
            "must contain",
        ),
    ],
)
def test_backend_hfss_material_update_rejects_unsafe_specs(updates, error):
    def factory(**kwargs):
        app = FakeMaterialHfss(**kwargs)
        material = app.materials.add_material(
            "HarnessUpdate",
            properties={"permittivity": 3.0, "conductivity": 0.01},
        )
        material.material_appearance = [10, 20, 30, 0.4]
        return app

    backend = LiveAedtBackend(desktop_factory=FakeDesktop, hfss_factory=factory)
    with pytest.raises(LiveBackendError, match=error):
        backend.execute(
            AedtTarget("pid", 42),
            "hfss_material_update_preview",
            {
                "project_name": "Board",
                "design_name": "HFSS1",
                "updates": updates,
            },
        )


def test_backend_hfss_material_update_rejects_complex_running_and_wrong_design():
    def complex_factory(**kwargs):
        app = FakeMaterialHfss(**kwargs)
        material = app.materials.add_material(
            "HarnessComplex",
            properties={"permittivity": 3.0, "conductivity": 0.01},
        )
        material.permittivity.type = "anisotropic"
        return app

    request = {
        "project_name": "Board",
        "design_name": "HFSS1",
        "updates": [{"material_name": "HarnessComplex", "conductivity": 0.02}],
    }
    complex_backend = LiveAedtBackend(
        desktop_factory=FakeDesktop,
        hfss_factory=complex_factory,
    )
    with pytest.raises(LiveBackendError, match="five electromagnetic properties"):
        complex_backend.execute(
            AedtTarget("pid", 42),
            "hfss_material_update_preview",
            request,
        )

    def running_factory(**kwargs):
        app = complex_factory(**kwargs)
        app.materials.material_keys["harnesscomplex"].permittivity.type = "simple"
        app.are_there_simulations_running = True
        return app

    running_backend = LiveAedtBackend(
        desktop_factory=FakeDesktop,
        hfss_factory=running_factory,
    )
    with pytest.raises(LiveBackendError, match="while a simulation is running"):
        running_backend.execute(
            AedtTarget("pid", 42),
            "hfss_material_update_preview",
            request,
        )

    def wrong_factory(**kwargs):
        app = complex_factory(**kwargs)
        app.materials.material_keys["harnesscomplex"].permittivity.type = "simple"
        app.design_type = "HFSS 3D Layout Design"
        return app

    wrong_backend = LiveAedtBackend(
        desktop_factory=FakeDesktop,
        hfss_factory=wrong_factory,
    )
    with pytest.raises(LiveBackendError, match="requires an HFSS 3D design"):
        wrong_backend.execute(
            AedtTarget("pid", 42),
            "hfss_material_update_preview",
            request,
        )


def test_backend_hfss_material_update_never_overwrites_racing_replacement(monkeypatch):
    apps = []

    def factory(**kwargs):
        app = FakeMaterialHfss(**kwargs)
        app.materials.add_material(
            "HarnessRace",
            properties={"permittivity": 3.0, "conductivity": 0.01},
        )
        apps.append(app)
        return app

    backend = LiveAedtBackend(desktop_factory=FakeDesktop, hfss_factory=factory)
    target = AedtTarget("pid", 42)
    preview = backend.execute(
        target,
        "hfss_material_update_preview",
        {
            "project_name": "Board",
            "design_name": "HFSS1",
            "updates": [{"material_name": "HarnessRace", "permittivity": 4.0}],
        },
    )
    replacement = FakeMaterial(
        "HarnessRace",
        dielectric=True,
        color=[99, 98, 97, 0.2],
        conductivity=0.01,
    )
    replacement.permittivity.value = 8.8
    from aedt_agent.live import backend as backend_module

    def replace_then_fail(*args, **kwargs):
        apps[0].materials.material_keys["harnessrace"] = replacement
        raise LiveBackendError("injected racing replacement")

    monkeypatch.setattr(
        backend_module,
        "_verify_hfss_material_update_catalog",
        replace_then_fail,
    )
    with pytest.raises(LiveBackendError, match="rollback is incomplete"):
        backend.execute(
            target,
            "hfss_material_update_apply",
            {"preview_id": preview["preview_id"]},
        )
    assert apps[0].materials.material_keys["harnessrace"] is replacement
    assert replacement.permittivity.value == 8.8


def test_hfss_material_update_raw_definition_mask_allows_only_requested_fields():
    from aedt_agent.live.backend import _verify_hfss_material_raw_definition_updates

    before = {
        "HarnessRaw": [
            "NAME:HarnessRaw",
            "permittivity:=",
            3.2,
            "thermal_conductivity:=",
            0.5,
            [
                "NAME:AttachedData",
                "Red:=",
                10,
                "Green:=",
                20,
                "Blue:=",
                30,
                "Transparency:=",
                0.4,
            ],
        ]
    }
    after = {
        "HarnessRaw": [
            "NAME:HarnessRaw",
            "permittivity:=",
            4.4,
            "thermal_conductivity:=",
            0.5,
            [
                "NAME:AttachedData",
                "Red:=",
                40,
                "Green:=",
                50,
                "Blue:=",
                60,
                "Transparency:=",
                0.6,
            ],
        ]
    }
    updates = [
        {
            "material_name": "HarnessRaw",
            "permittivity": 4.4,
            "appearance": [40, 50, 60, 0.6],
        }
    ]
    _verify_hfss_material_raw_definition_updates(before, after, updates)

    after["HarnessRaw"][4] = 0.7
    with pytest.raises(LiveBackendError, match="unrequested native"):
        _verify_hfss_material_raw_definition_updates(before, after, updates)


def test_backend_deletes_exact_unreferenced_hfss_material_batch():
    apps = []

    def factory(**kwargs):
        app = FakeMaterialHfss(**kwargs)
        first = app.materials.add_material(
            "HarnessDeleteA",
            properties={"permittivity": 3.2, "conductivity": 0.01},
        )
        first.material_appearance = [10, 20, 30, 0.4]
        second = app.materials.add_material(
            "HarnessDeleteB",
            properties={"permittivity": 4.1, "conductivity": 0.02},
        )
        second.material_appearance = [40, 50, 60, 0.5]
        apps.append(app)
        return app

    backend = LiveAedtBackend(desktop_factory=FakeDesktop, hfss_factory=factory)
    target = AedtTarget("pid", 42)
    preview = backend.execute(
        target,
        "hfss_material_delete_preview",
        {
            "project_name": "Board",
            "design_name": "HFSS1",
            "names": ["HarnessDeleteA", "HarnessDeleteB"],
            "max_materials": 2,
        },
    )
    assert preview["target_count"] == 2
    assert preview["solid_reference_count"] == 0
    assert preview["boundary_reference_count"] == 0
    assert preview["project_dirty"] is False
    assert "harnessdeletea" in apps[0].materials.material_keys

    result = backend.execute(
        target,
        "hfss_material_delete_apply",
        {"preview_id": preview["preview_id"]},
    )
    assert result["status"] == "verified"
    assert result["deleted_material_names"] == ["HarnessDeleteA", "HarnessDeleteB"]
    assert result["deleted_material_count"] == 2
    assert result["remaining_material_count"] == 2
    assert result["absence_digest"]
    assert "harnessdeletea" not in apps[0].materials.material_keys
    assert "harnessdeleteb" not in apps[0].materials.material_keys
    assert result["automatic_rollback_on_failure"] is True
    assert result["project_saved"] is False


def test_backend_hfss_material_delete_rejects_solid_and_boundary_references():
    def solid_factory(**kwargs):
        app = FakeMaterialHfss(**kwargs)
        app.materials.add_material(
            "HarnessUsed",
            properties={"permittivity": 3.0, "conductivity": 0.01},
        )
        assert app.assign_material("box1", "HarnessUsed") is True
        return app

    request = {
        "project_name": "Board",
        "design_name": "HFSS1",
        "names": ["HarnessUsed"],
    }
    solid_backend = LiveAedtBackend(
        desktop_factory=FakeDesktop,
        hfss_factory=solid_factory,
    )
    with pytest.raises(LiveBackendError, match="zero solid-object references"):
        solid_backend.execute(
            AedtTarget("pid", 42),
            "hfss_material_delete_preview",
            request,
        )

    def boundary_factory(**kwargs):
        app = FakeMaterialHfss(**kwargs)
        app.materials.add_material(
            "HarnessUsed",
            properties={"permittivity": 3.0, "conductivity": 0.01},
        )
        app.boundaries.append(
            FakeBoundary(
                app,
                "MaterialBoundary",
                "Finite Conductivity",
                props={"Faces": [101], "Material": "HarnessUsed"},
            )
        )
        return app

    boundary_backend = LiveAedtBackend(
        desktop_factory=FakeDesktop,
        hfss_factory=boundary_factory,
    )
    with pytest.raises(LiveBackendError, match="zero boundary references"):
        boundary_backend.execute(
            AedtTarget("pid", 42),
            "hfss_material_delete_preview",
            request,
        )


def test_backend_hfss_material_delete_rejects_stale_and_reconstructs_batch(
    monkeypatch,
):
    apps = []

    def factory(**kwargs):
        app = FakeMaterialHfss(**kwargs)
        app.materials.add_material(
            "HarnessDeleteA",
            properties={"permittivity": 3.0, "conductivity": 0.01},
        )
        app.materials.add_material(
            "HarnessDeleteB",
            properties={"permittivity": 4.0, "conductivity": 0.02},
        )
        apps.append(app)
        return app

    backend = LiveAedtBackend(desktop_factory=FakeDesktop, hfss_factory=factory)
    target = AedtTarget("pid", 42)
    request = {
        "project_name": "Board",
        "design_name": "HFSS1",
        "names": ["HarnessDeleteA", "HarnessDeleteB"],
    }
    stale = backend.execute(target, "hfss_material_delete_preview", request)
    apps[0].materials.add_material("ExternalMaterial", properties={"permittivity": 2.2})
    with pytest.raises(LiveBackendError, match="stale HFSS material delete preview"):
        backend.execute(
            target,
            "hfss_material_delete_apply",
            {"preview_id": stale["preview_id"]},
        )
    assert "harnessdeletea" in apps[0].materials.material_keys
    assert apps[0].materials.remove_material("ExternalMaterial") is True

    rollback_preview = backend.execute(target, "hfss_material_delete_preview", request)
    before = backend.execute(
        target,
        "hfss_material_inventory",
        {"project_name": "Board", "design_name": "HFSS1", "max_items": 500},
    )
    from aedt_agent.live import backend as backend_module

    monkeypatch.setattr(
        backend_module,
        "_verify_hfss_material_delete_catalog",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            LiveBackendError("injected material delete readback failure")
        ),
    )
    with pytest.raises(LiveBackendError, match="injected material delete readback failure"):
        backend.execute(
            target,
            "hfss_material_delete_apply",
            {"preview_id": rollback_preview["preview_id"]},
        )
    after = backend.execute(
        target,
        "hfss_material_inventory",
        {"project_name": "Board", "design_name": "HFSS1", "max_items": 500},
    )
    assert after["snapshot_digest"] == before["snapshot_digest"]
    assert "harnessdeletea" in apps[0].materials.material_keys
    assert "harnessdeleteb" in apps[0].materials.material_keys


@pytest.mark.parametrize(
    "names,error",
    [
        ([], "at least one exact"),
        (["Missing"], "already exist"),
        (["HarnessDelete", "harnessdelete"], "unique case-insensitively"),
        (["harnessdelete"], "exact case"),
        (["bad/name"], "safe exact"),
    ],
)
def test_backend_hfss_material_delete_rejects_unsafe_names(names, error):
    def factory(**kwargs):
        app = FakeMaterialHfss(**kwargs)
        app.materials.add_material(
            "HarnessDelete",
            properties={"permittivity": 3.0, "conductivity": 0.01},
        )
        return app

    backend = LiveAedtBackend(desktop_factory=FakeDesktop, hfss_factory=factory)
    with pytest.raises(LiveBackendError, match=error):
        backend.execute(
            AedtTarget("pid", 42),
            "hfss_material_delete_preview",
            {
                "project_name": "Board",
                "design_name": "HFSS1",
                "names": names,
            },
        )


def test_backend_hfss_material_delete_never_overwrites_racing_replacement(monkeypatch):
    apps = []

    def factory(**kwargs):
        app = FakeMaterialHfss(**kwargs)
        app.materials.add_material(
            "HarnessDeleteRace",
            properties={"permittivity": 3.0, "conductivity": 0.01},
        )
        apps.append(app)
        return app

    backend = LiveAedtBackend(desktop_factory=FakeDesktop, hfss_factory=factory)
    target = AedtTarget("pid", 42)
    preview = backend.execute(
        target,
        "hfss_material_delete_preview",
        {
            "project_name": "Board",
            "design_name": "HFSS1",
            "names": ["HarnessDeleteRace"],
        },
    )
    replacement = FakeMaterial(
        "HarnessDeleteRace",
        dielectric=True,
        color=[99, 98, 97, 0.2],
        conductivity=0.01,
    )
    replacement.permittivity.value = 8.8
    from aedt_agent.live import backend as backend_module

    def replace_then_fail(*args, **kwargs):
        apps[0].materials.material_keys["harnessdeleterace"] = replacement
        raise LiveBackendError("injected racing replacement")

    monkeypatch.setattr(
        backend_module,
        "_verify_hfss_material_delete_catalog",
        replace_then_fail,
    )
    with pytest.raises(LiveBackendError, match="rollback is incomplete"):
        backend.execute(
            target,
            "hfss_material_delete_apply",
            {"preview_id": preview["preview_id"]},
        )
    assert apps[0].materials.material_keys["harnessdeleterace"] is replacement
    assert replacement.permittivity.value == 8.8


def test_backend_layout_material_create_assign_supports_all_stackup_roles():
    apps = []

    def factory(**kwargs):
        app = FakeLayout(**kwargs)
        apps.append(app)
        return app

    backend = LiveAedtBackend(desktop_factory=FakeDesktop, layout_factory=factory)
    target = AedtTarget("pid", 42)
    cases = [
        {
            "material_name": "HarnessLaminate",
            "layer_name": "D1",
            "assignment_field": "material",
            "permittivity": 4.2,
            "conductivity": 0.005,
            "dielectric_loss_tangent": 0.018,
            "expected_class": "dielectric",
        },
        {
            "material_name": "HarnessFill",
            "layer_name": "TOP",
            "assignment_field": "fill_material",
            "permittivity": 3.6,
            "conductivity": 0.001,
            "expected_class": "dielectric",
        },
        {
            "material_name": "HarnessCopper",
            "layer_name": "TOP",
            "assignment_field": "material",
            "conductivity": 58_000_000.0,
            "expected_class": "conductor",
        },
    ]
    for case in cases:
        request = {
            "project_name": "Board",
            "design_name": "Layout1",
            **{key: value for key, value in case.items() if key != "expected_class"},
        }
        preview = backend.execute(
            target,
            "layout_material_create_assign_preview",
            request,
        )
        assert preview["project_dirty"] is False
        assert preview["project_saved"] is False
        assert preview["expected_material_class"] == case["expected_class"]
        assert preview["layer"]["name"] == case["layer_name"]
        assert case["material_name"].casefold() not in apps[0].materials.material_keys

        result = backend.execute(
            target,
            "layout_material_create_assign_apply",
            {"preview_id": preview["preview_id"]},
        )
        assert result["status"] == "verified"
        assert result["created_material_name"] == case["material_name"]
        assert result["layer"][case["assignment_field"]] == case["material_name"]
        assert result["material"]["is_dielectric"] is (
            case["expected_class"] == "dielectric"
        )
        assert result["automatic_rollback_on_failure"] is True
        assert result["project_saved"] is False


def test_backend_layout_material_create_assign_rejects_stale_and_rolls_back(
    monkeypatch,
):
    apps = []

    def factory(**kwargs):
        app = FakeLayout(**kwargs)
        apps.append(app)
        return app

    backend = LiveAedtBackend(desktop_factory=FakeDesktop, layout_factory=factory)
    target = AedtTarget("pid", 42)
    request = {
        "project_name": "Board",
        "design_name": "Layout1",
        "material_name": "RollbackLaminate",
        "layer_name": "D1",
        "assignment_field": "material",
        "permittivity": 3.7,
        "dielectric_loss_tangent": 0.012,
    }
    preview = backend.execute(
        target,
        "layout_material_create_assign_preview",
        request,
    )
    before_stackup = [
        vars(item).copy() for item in apps[0].modeler.layers.stackup_layers
    ]
    before_materials = sorted(apps[0].materials.material_keys)
    monkeypatch.setattr(
        "aedt_agent.live.backend._verify_layout_material_create_assign_readback",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            LiveBackendError("injected layout material readback failure")
        ),
    )
    with pytest.raises(
        LiveBackendError,
        match="injected layout material readback failure",
    ):
        backend.execute(
            target,
            "layout_material_create_assign_apply",
            {"preview_id": preview["preview_id"]},
        )
    assert [vars(item).copy() for item in apps[0].modeler.layers.stackup_layers] == before_stackup
    assert sorted(apps[0].materials.material_keys) == before_materials

    monkeypatch.undo()
    stale = backend.execute(
        target,
        "layout_material_create_assign_preview",
        {**request, "material_name": "StaleLaminate"},
    )
    apps[0].modeler.layers.stackup_layers[1].thickness = 0.25
    with pytest.raises(
        LiveBackendError,
        match="stale 3D Layout material create-and-assign preview",
    ):
        backend.execute(
            target,
            "layout_material_create_assign_apply",
            {"preview_id": stale["preview_id"]},
        )
    assert "stalelaminate" not in apps[0].materials.material_keys


@pytest.mark.parametrize(
    "request_payload,error",
    [
        (
            {"material_name": "M", "layer_name": "missing"},
            "stackup layer does not exist",
        ),
        (
            {
                "material_name": "M",
                "layer_name": "D1",
                "assignment_field": "fill_material",
            },
            "dielectric layers require",
        ),
        (
            {
                "material_name": "M",
                "layer_name": "D1",
                "conductivity": 58_000_000.0,
            },
            "requires a dielectric material",
        ),
        (
            {
                "material_name": "M",
                "layer_name": "TOP",
                "assignment_field": "material",
                "conductivity": 0.0,
            },
            "requires a conductor material",
        ),
        (
            {"material_name": "copper", "layer_name": "D1"},
            "already exists",
        ),
        (
            {"material_name": "library_only", "layer_name": "D1"},
            "material library entry",
        ),
        (
            {
                "material_name": "M",
                "layer_name": "D1",
                "assignment_field": "unsupported",
            },
            "assignment_field must be",
        ),
    ],
)
def test_backend_layout_material_create_assign_rejects_unsafe_requests(
    request_payload,
    error,
):
    backend = LiveAedtBackend(
        desktop_factory=FakeDesktop,
        layout_factory=FakeLayout,
    )
    with pytest.raises(LiveBackendError, match=error):
        backend.execute(
            AedtTarget("pid", 42),
            "layout_material_create_assign_preview",
            {
                "project_name": "Board",
                "design_name": "Layout1",
                **request_payload,
            },
        )


def _layout_via_create_request():
    return {
        "project_name": "Board",
        "design_name": "Layout1",
        "vias": [
            {
                "name": "V_NEW_1",
                "padstack": "VIA",
                "x": 3.0,
                "y": 4.0,
                "rotation_degrees": 45.0,
                "hole_diameter": 0.25,
                "top_layer": "TOP",
                "bottom_layer": "BOT",
                "net_name": "GND",
                "lock_position": True,
            },
            {
                "name": "V_NEW_2",
                "padstack": "VIA",
                "x": -1.0,
                "y": 2.5,
                "rotation_degrees": -30.0,
                "top_layer": "TOP",
                "bottom_layer": "BOT",
                "net_name": "GND",
            },
        ],
        "max_vias": 4,
    }


def test_backend_layout_via_create_batch_has_native_typed_readback():
    apps = []

    def factory(**kwargs):
        app = FakeViaCreateLayout(**kwargs)
        apps.append(app)
        return app

    backend = LiveAedtBackend(desktop_factory=FakeDesktop, layout_factory=factory)
    target = AedtTarget("pid", 42)
    preview = backend.execute(target, "layout_via_create_preview", _layout_via_create_request())
    assert preview["via_count"] == 2
    assert preview["model_units"] == "mm"
    assert preview["project_dirty"] is False
    assert preview["project_saved"] is False
    assert preview["dependency_summary"] == {
        "padstacks": ["VIA"],
        "signal_layers": ["BOT", "TOP"],
        "nets": ["GND"],
    }
    assert "V_NEW_1" not in apps[0].modeler.vias

    result = backend.execute(
        target,
        "layout_via_create_apply",
        {"preview_id": preview["preview_id"]},
    )
    assert result["status"] == "verified"
    assert result["via_count"] == 2
    assert [item["name"] for item in result["vias"]] == ["V_NEW_1", "V_NEW_2"]
    first, second = result["vias"]
    assert first["location"] == [3.0, 4.0]
    assert first["rotation_degrees"] == 45.0
    assert first["lock_position"] is True
    assert first["override_hole_diameter"] is True
    assert first["hole_diameter"] == "0.25mm"
    assert second["rotation_degrees"] == -30.0
    assert second["override_hole_diameter"] is False
    assert result["automatic_rollback_on_failure"] is True
    assert result["project_saved"] is False


def test_backend_layout_via_target_inventory_reads_fixed_native_properties():
    app = FakeViaCreateLayout(project="Board", design="Layout1")
    app.modeler.vias["V2"] = FakeVia("V2")
    app.modeler.vias["V2"].net_name = "SIG"
    backend = LiveAedtBackend(desktop_factory=FakeDesktop, layout_factory=lambda **kwargs: app)
    target = AedtTarget("pid", 42)

    result = backend.execute(
        target,
        "layout_object_property_inventory",
        {
            "project_name": "Board",
            "design_name": "Layout1",
            "object_kind": "via",
            "profile": "via_target/v1",
            "names": ["V1", "MISSING"],
            "max_items": 2,
        },
    )

    assert result["status"] == "partial"
    assert result["not_found_names"] == ["MISSING"]
    via = result["objects"][0]
    assert via["status"] == "ok"
    assert via["target_eligible"] is True
    assert via["values"] == {
        "net": {"status": "ok", "raw": "GND", "value": "GND"},
        "location": {"status": "ok", "raw": "1.0 ,2.0", "value": {"x": "1.0", "y": "2.0"}},
        "start_layer": {"status": "ok", "raw": "TOP", "value": "TOP"},
        "stop_layer": {"status": "ok", "raw": "BOT", "value": "BOT"},
    }
    assert via["via_target_digest"]
    assert result["objects"][1]["status"] == "not_found"

    with pytest.raises(LiveBackendError, match="exceeds max_items"):
        backend.execute(
            target,
            "layout_object_property_inventory",
            {"project_name": "Board", "design_name": "Layout1", "object_kind": "via", "profile": "via_target/v1", "names": [f"V{index}" for index in range(51)], "max_items": 50},
        )


def test_backend_layout_native_property_bridge_uses_canonical_schema_only():
    app = FakeViaCreateLayout(project="Board", design="Layout1")
    backend = LiveAedtBackend(desktop_factory=FakeDesktop, layout_factory=lambda **kwargs: app)
    target = AedtTarget("pid", 42)

    schema = backend.execute(
        target,
        "layout_property_schema",
        {"project_name": "Board", "design_name": "Layout1"},
    )
    assert schema["schema_version"] == "layout_native_property/v1"
    via_schema = schema["object_kinds"][0]
    assert via_schema["id"] == "via"
    assert via_schema["max_objects"] == 50
    assert via_schema["max_properties"] == 8
    assert {item["id"] for item in via_schema["properties"]} == {
        "net",
        "location",
        "start_layer",
        "stop_layer",
        "padstack_definition",
        "hole_diameter",
        "angle",
        "lock_position",
    }
    assert via_schema["profiles"] == [
        {"id": "via_target/v1", "property_ids": ["net", "location", "start_layer", "stop_layer"]}
    ]

    result = backend.execute(
        target,
        "layout_properties_read",
        {
            "project_name": "Board",
            "design_name": "Layout1",
            "object_kind": "via",
            "names": ["V1", "MISSING"],
            "property_ids": ["net", "padstack_definition", "lock_position"],
        },
    )
    assert result["status"] == "partial"
    assert result["records"][0] == {
        "name": "V1",
        "status": "ok",
        "properties": {
            "net": {"status": "ok", "raw": "GND", "value": "GND"},
            "padstack_definition": {"status": "ok", "raw": "VIA", "value": "VIA"},
            "lock_position": {"status": "ok", "raw": "false", "value": False},
        },
    }
    assert result["records"][1] == {"name": "MISSING", "status": "not_found", "properties": {}}
    assert result["response_digest"]

    unsupported = backend.execute(
        target,
        "layout_properties_read",
        {
            "project_name": "Board",
            "design_name": "Layout1",
            "object_kind": "via",
            "names": ["V1"],
            "property_ids": ["Net", "GetPropertyValue('BaseElementTab', 'V1', 'Net')"],
        },
    )
    assert unsupported["status"] == "property_not_supported"
    assert unsupported["unsupported_property_ids"] == [
        "GetPropertyValue('BaseElementTab', 'V1', 'Net')",
        "Net",
    ]
    assert unsupported["records"] == []


def test_backend_layout_via_create_rejects_stale_and_rolls_back(monkeypatch):
    apps = []

    def factory(**kwargs):
        app = FakeViaCreateLayout(**kwargs)
        apps.append(app)
        return app

    backend = LiveAedtBackend(desktop_factory=FakeDesktop, layout_factory=factory)
    target = AedtTarget("pid", 42)
    request = _layout_via_create_request()
    preview = backend.execute(target, "layout_via_create_preview", request)
    state_before = dict(backend._previews[preview["preview_id"]]["state"])
    monkeypatch.setattr(
        "aedt_agent.live.backend._verify_layout_via_create_readback",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            LiveBackendError("injected layout via readback failure")
        ),
    )
    with pytest.raises(LiveBackendError, match="injected layout via readback failure"):
        backend.execute(
            target,
            "layout_via_create_apply",
            {"preview_id": preview["preview_id"]},
        )
    assert "V_NEW_1" not in apps[0].modeler.vias
    assert "V_NEW_2" not in apps[0].modeler.vias

    monkeypatch.undo()
    retry = backend.execute(target, "layout_via_create_preview", request)
    assert backend._previews[retry["preview_id"]]["state"] == state_before
    apps[0].modeler.layers.stackup_layers[-1].thickness = 0.05
    with pytest.raises(LiveBackendError, match="stale 3D Layout via create preview"):
        backend.execute(
            target,
            "layout_via_create_apply",
            {"preview_id": retry["preview_id"]},
        )


def test_backend_layout_via_create_never_deletes_a_racing_external_name():
    apps = []

    def factory(**kwargs):
        app = FakeViaCreateLayout(**kwargs)
        apps.append(app)
        return app

    backend = LiveAedtBackend(desktop_factory=FakeDesktop, layout_factory=factory)
    target = AedtTarget("pid", 42)
    request = _layout_via_create_request()
    request["vias"] = request["vias"][:1]
    preview = backend.execute(target, "layout_via_create_preview", request)
    original_create = apps[0].modeler.create_via

    def raced_create(**kwargs):
        external = FakeVia(kwargs["name"])
        apps[0].modeler.vias[kwargs["name"]] = external
        return original_create(**{**kwargs, "name": kwargs["name"] + "_1"})

    apps[0].modeler.create_via = raced_create
    with pytest.raises(LiveBackendError, match="rollback is incomplete"):
        backend.execute(
            target,
            "layout_via_create_apply",
            {"preview_id": preview["preview_id"]},
        )
    assert "V_NEW_1" in apps[0].modeler.vias
    assert "V_NEW_1_1" not in apps[0].modeler.vias


@pytest.mark.parametrize(
    "mutate,error",
    [
        (lambda request: request.update(vias=[]), "at least one typed via"),
        (
            lambda request: request["vias"][0].update(name="V1"),
            "object name already exists",
        ),
        (
            lambda request: request["vias"][0].update(padstack="missing"),
            "padstack does not exist",
        ),
        (
            lambda request: request["vias"][0].update(top_layer="D1"),
            "top_layer must reference a signal layer",
        ),
        (
            lambda request: request["vias"][0].update(net_name="missing"),
            "net_name does not exist",
        ),
        (
            lambda request: request["vias"][0].update(hole_diameter=-1),
            "hole_diameter must be between",
        ),
        (
            lambda request: request["vias"][0].update(unsupported=True),
            "unsupported vias\\[0\\] field",
        ),
    ],
)
def test_backend_layout_via_create_rejects_unsafe_requests(mutate, error):
    request = _layout_via_create_request()
    mutate(request)
    backend = LiveAedtBackend(
        desktop_factory=FakeDesktop,
        layout_factory=FakeViaCreateLayout,
    )
    with pytest.raises(LiveBackendError, match=error):
        backend.execute(
            AedtTarget("pid", 42),
            "layout_via_create_preview",
            request,
        )


def _layout_via_update_request():
    return {
        "project_name": "Board",
        "design_name": "Layout1",
        "updates": [
            {
                "name": "V1",
                "net_name": "N1",
                "location": [5.0, 6.0],
                "rotation_degrees": 45.0,
                "lock_position": True,
            },
            {
                "name": "V2",
                "net_name": "N2",
                "location": [-2.0, 8.0],
                "rotation_degrees": -30.0,
            },
        ],
        "max_vias": 4,
    }


def _fake_via_update_app():
    app = FakeViaCreateLayout(project="Board", design="Layout1")
    second = app.create_via(
        name="V2",
        padstack="VIA",
        x=2.0,
        y=3.0,
        rotation=0.0,
        hole_diam=None,
        top_layer="TOP",
        bot_layer="BOT",
        net="N1",
    )
    second.angle = "15.0deg"
    second.lock_position = True
    return app


def _fake_net_removing_via_update_app():
    app = _fake_via_update_app()

    class NetAwareVia:
        def __init__(self):
            self.name = "V1"
            self.start_layer = "TOP"
            self.stop_layer = "BOT"
            self.holediam = "0.2mm"
            self.padstack = "VIA"
            self.override_hole_diameter = False
            self.location = [1.0, 2.0]
            self.angle = "0.0deg"
            self.lock_position = False
            self._net_name = "N_DROP"

        @property
        def net_name(self):
            return self._net_name

        @net_name.setter
        def net_name(self, value):
            old_name = self._net_name
            self._net_name = value
            if value not in app.modeler.nets:
                app.modeler.nets[value] = FakeLayoutNet(value, [self.name])
            if old_name and not any(
                item is not self and item.net_name == old_name
                for item in app.modeler.vias.values()
            ):
                app.modeler.nets.pop(old_name, None)

    app.modeler.nets["N_DROP"] = FakeLayoutNet("N_DROP", ["V1"])
    app.modeler.vias["V1"] = NetAwareVia()
    return app


def test_backend_layout_via_update_batch_has_exact_native_readback():
    app = _fake_via_update_app()
    backend = LiveAedtBackend(
        desktop_factory=FakeDesktop,
        layout_factory=lambda **kwargs: app,
    )
    target = AedtTarget("pid", 42)
    request = _layout_via_update_request()
    preview = backend.execute(target, "layout_via_update_preview", request)
    assert preview["via_count"] == 2
    assert preview["model_units"] == "mm"
    assert preview["project_dirty"] is False
    assert preview["project_saved"] is False
    assert [item["name"] for item in preview["before"]] == ["V1", "V2"]
    assert app.modeler.vias["V1"].location == [1.0, 2.0]
    assert app.modeler.vias["V2"].lock_position is True

    result = backend.execute(
        target,
        "layout_via_update_apply",
        {"preview_id": preview["preview_id"]},
    )
    assert result["status"] == "verified"
    assert result["via_count"] == 2
    first, second = result["vias"]
    assert first["name"] == "V1"
    assert first["net_name"] == "N1"
    assert first["location"] == [5.0, 6.0]
    assert first["rotation_degrees"] == 45.0
    assert first["lock_position"] is True
    assert second["name"] == "V2"
    assert second["net_name"] == "N2"
    assert second["location"] == [-2.0, 8.0]
    assert second["rotation_degrees"] == -30.0
    assert second["lock_position"] is True
    assert all(item["native_property_digest"] for item in result["vias"])
    assert result["automatic_rollback_on_failure"] is True
    assert result["project_saved"] is False


def test_backend_layout_via_update_rejects_stale_and_restores_full_snapshot(monkeypatch):
    app = _fake_via_update_app()
    backend = LiveAedtBackend(
        desktop_factory=FakeDesktop,
        layout_factory=lambda **kwargs: app,
    )
    target = AedtTarget("pid", 42)
    request = _layout_via_update_request()
    stale_preview = backend.execute(target, "layout_via_update_preview", request)
    app.modeler.vias["V1"].angle = "10deg"
    with pytest.raises(LiveBackendError, match="stale 3D Layout via update preview"):
        backend.execute(
            target,
            "layout_via_update_apply",
            {"preview_id": stale_preview["preview_id"]},
        )
    app.modeler.vias["V1"].angle = "0.0deg"

    rollback_preview = backend.execute(target, "layout_via_update_preview", request)
    before_state = json.loads(
        json.dumps(backend._previews[rollback_preview["preview_id"]]["state"])
    )
    monkeypatch.setattr(
        "aedt_agent.live.backend._verify_layout_via_update_readback",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            LiveBackendError("injected layout via update readback failure")
        ),
    )
    with pytest.raises(LiveBackendError, match="injected layout via update readback failure"):
        backend.execute(
            target,
            "layout_via_update_apply",
            {"preview_id": rollback_preview["preview_id"]},
        )
    monkeypatch.undo()
    retry = backend.execute(target, "layout_via_update_preview", request)
    assert backend._previews[retry["preview_id"]]["state"] == before_state


@pytest.mark.parametrize(
    "mutate,error",
    [
        (lambda request: request.update(updates=[]), "at least one typed via update"),
        (
            lambda request: request["updates"][0].update(name="missing"),
            "via is missing or ambiguous",
        ),
        (
            lambda request: request["updates"][0].update(net_name="missing"),
            "net_name does not exist",
        ),
        (
            lambda request: request["updates"][0].update(location=[1.0]),
            "location must contain two",
        ),
        (
            lambda request: request["updates"][0].update(lock_position="yes"),
            "lock_position must be boolean",
        ),
        (
            lambda request: request["updates"][0].update(unsupported=True),
            "unsupported updates\\[0\\] field",
        ),
        (
            lambda request: request["updates"].append(dict(request["updates"][0])),
            "unique case-insensitively",
        ),
    ],
)
def test_backend_layout_via_update_rejects_unsafe_requests(mutate, error):
    request = _layout_via_update_request()
    request["updates"] = request["updates"][:1]
    mutate(request)
    app = _fake_via_update_app()
    backend = LiveAedtBackend(
        desktop_factory=FakeDesktop,
        layout_factory=lambda **kwargs: app,
    )
    with pytest.raises(LiveBackendError, match=error):
        backend.execute(
            AedtTarget("pid", 42),
            "layout_via_update_preview",
            request,
        )


def test_backend_layout_via_update_rejects_noop():
    app = _fake_via_update_app()
    backend = LiveAedtBackend(
        desktop_factory=FakeDesktop,
        layout_factory=lambda **kwargs: app,
    )
    with pytest.raises(LiveBackendError, match="already equal"):
        backend.execute(
            AedtTarget("pid", 42),
            "layout_via_update_preview",
            {
                "project_name": "Board",
                "design_name": "Layout1",
                "updates": [{"name": "V1", "rotation_degrees": 360.0}],
            },
        )


def test_backend_layout_via_update_allows_only_changed_empty_source_net_removal():
    app = _fake_net_removing_via_update_app()
    backend = LiveAedtBackend(
        desktop_factory=FakeDesktop,
        layout_factory=lambda **kwargs: app,
    )
    preview = backend.execute(
        AedtTarget("pid", 42),
        "layout_via_update_preview",
        {
            "project_name": "Board",
            "design_name": "Layout1",
            "updates": [{"name": "V1", "net_name": "N1"}],
        },
    )
    result = backend.execute(
        AedtTarget("pid", 42),
        "layout_via_update_apply",
        {"preview_id": preview["preview_id"]},
    )
    assert result["status"] == "verified"
    assert result["vias"][0]["net_name"] == "N1"
    assert "N_DROP" not in app.modeler.nets


def test_backend_layout_via_update_rollback_recreates_removed_source_net(monkeypatch):
    app = _fake_net_removing_via_update_app()
    backend = LiveAedtBackend(
        desktop_factory=FakeDesktop,
        layout_factory=lambda **kwargs: app,
    )
    target = AedtTarget("pid", 42)
    request = {
        "project_name": "Board",
        "design_name": "Layout1",
        "updates": [{"name": "V1", "net_name": "N1"}],
    }
    preview = backend.execute(target, "layout_via_update_preview", request)
    before_state = backend._previews[preview["preview_id"]]["state"]
    monkeypatch.setattr(
        "aedt_agent.live.backend._verify_layout_via_update_readback",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            LiveBackendError("injected source-net rollback failure")
        ),
    )
    with pytest.raises(LiveBackendError, match="injected source-net rollback failure"):
        backend.execute(
            target,
            "layout_via_update_apply",
            {"preview_id": preview["preview_id"]},
        )
    monkeypatch.undo()
    retry = backend.execute(target, "layout_via_update_preview", request)
    assert backend._previews[retry["preview_id"]]["state"] == before_state
    assert app.modeler.vias["V1"].net_name == "N_DROP"
    assert "N_DROP" in app.modeler.nets


def _layout_via_delete_request():
    return {
        "project_name": "Board",
        "design_name": "Layout1",
        "names": ["V1", "V2"],
        "max_vias": 4,
    }


def test_backend_layout_via_delete_batch_verifies_native_absence():
    app = _fake_via_update_app()
    backend = LiveAedtBackend(
        desktop_factory=FakeDesktop,
        layout_factory=lambda **kwargs: app,
    )
    target = AedtTarget("pid", 42)
    preview = backend.execute(target, "layout_via_delete_preview", _layout_via_delete_request())
    assert preview["names"] == ["V1", "V2"]
    assert [item["name"] for item in preview["before"]] == ["V1", "V2"]
    assert preview["via_count"] == 2
    assert preview["project_dirty"] is False
    assert preview["project_saved"] is False
    assert set(app.modeler.vias) == {"V1", "V2"}

    result = backend.execute(
        target,
        "layout_via_delete_apply",
        {"preview_id": preview["preview_id"]},
    )
    assert result["status"] == "verified"
    assert result["deleted_names"] == ["V1", "V2"]
    assert result["via_count"] == 2
    assert result["absence_digest"]
    assert result["automatic_rollback_on_failure"] is True
    assert result["project_saved"] is False
    assert app.modeler.vias == {}


def test_backend_layout_via_delete_rejects_stale_and_restores_full_native_batch(monkeypatch):
    app = _fake_via_update_app()
    backend = LiveAedtBackend(
        desktop_factory=FakeDesktop,
        layout_factory=lambda **kwargs: app,
    )
    target = AedtTarget("pid", 42)
    request = _layout_via_delete_request()
    stale_preview = backend.execute(target, "layout_via_delete_preview", request)
    app.modeler.vias["V1"].angle = "10deg"
    with pytest.raises(LiveBackendError, match="stale 3D Layout via delete preview"):
        backend.execute(
            target,
            "layout_via_delete_apply",
            {"preview_id": stale_preview["preview_id"]},
        )
    app.modeler.vias["V1"].angle = "0.0deg"

    rollback_preview = backend.execute(target, "layout_via_delete_preview", request)
    before_state = json.loads(
        json.dumps(backend._previews[rollback_preview["preview_id"]]["state"])
    )
    monkeypatch.setattr(
        "aedt_agent.live.backend._verify_layout_via_delete_readback",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            LiveBackendError("injected layout via delete readback failure")
        ),
    )
    with pytest.raises(LiveBackendError, match="injected layout via delete readback failure"):
        backend.execute(
            target,
            "layout_via_delete_apply",
            {"preview_id": rollback_preview["preview_id"]},
        )
    monkeypatch.undo()
    retry = backend.execute(target, "layout_via_delete_preview", request)
    assert backend._previews[retry["preview_id"]]["state"] == before_state


def test_backend_layout_via_delete_rolls_back_after_partial_native_failure():
    app = _fake_via_update_app()
    backend = LiveAedtBackend(
        desktop_factory=FakeDesktop,
        layout_factory=lambda **kwargs: app,
    )
    target = AedtTarget("pid", 42)
    preview = backend.execute(target, "layout_via_delete_preview", _layout_via_delete_request())
    before_state = backend._previews[preview["preview_id"]]["state"]
    original_delete = app.modeler.oeditor.Delete

    def fail_second(names):
        if names == ["V2"]:
            raise RuntimeError("injected second delete failure")
        return original_delete(names)

    app.modeler.oeditor.Delete = fail_second
    with pytest.raises(LiveBackendError, match="injected second delete failure"):
        backend.execute(
            target,
            "layout_via_delete_apply",
            {"preview_id": preview["preview_id"]},
        )
    app.modeler.oeditor.Delete = original_delete
    retry = backend.execute(target, "layout_via_delete_preview", _layout_via_delete_request())
    assert backend._previews[retry["preview_id"]]["state"] == before_state


def test_backend_layout_via_delete_never_overwrites_racing_external_name(monkeypatch):
    app = _fake_via_update_app()
    backend = LiveAedtBackend(
        desktop_factory=FakeDesktop,
        layout_factory=lambda **kwargs: app,
    )
    target = AedtTarget("pid", 42)
    request = _layout_via_delete_request()
    request["names"] = ["V1"]
    preview = backend.execute(target, "layout_via_delete_preview", request)

    def race_after_delete(*args, **kwargs):
        external = FakeVia("V1")
        external.padstack = "VIA"
        external.start_layer = "TOP"
        external.stop_layer = "BOT"
        external.override_hole_diameter = False
        external.location = [99.0, 99.0]
        app.modeler.vias["V1"] = external
        raise LiveBackendError("injected external via race")

    monkeypatch.setattr(
        "aedt_agent.live.backend._verify_layout_via_delete_readback",
        race_after_delete,
    )
    with pytest.raises(LiveBackendError, match="rollback is incomplete"):
        backend.execute(
            target,
            "layout_via_delete_apply",
            {"preview_id": preview["preview_id"]},
        )
    assert app.modeler.vias["V1"].location == [99.0, 99.0]


@pytest.mark.parametrize(
    "mutate,error",
    [
        (lambda request: request.update(names=[]), "at least one exact via name"),
        (lambda request: request.update(names=["missing"]), "via is missing or ambiguous"),
        (
            lambda request: request.update(names=["V1", "v1"]),
            "unique case-insensitively",
        ),
        (
            lambda request: request.update(names=["bad/name"]),
            "safe exact AEDT object name",
        ),
        (
            lambda request: request.update(max_vias=1),
            "exceeds the approved maximum",
        ),
    ],
)
def test_backend_layout_via_delete_rejects_unsafe_requests(mutate, error):
    request = _layout_via_delete_request()
    mutate(request)
    app = _fake_via_update_app()
    backend = LiveAedtBackend(
        desktop_factory=FakeDesktop,
        layout_factory=lambda **kwargs: app,
    )
    with pytest.raises(LiveBackendError, match=error):
        backend.execute(
            AedtTarget("pid", 42),
            "layout_via_delete_preview",
            request,
        )


def test_backend_layout_via_delete_rejects_custom_backdrill():
    app = _fake_via_update_app()
    original_properties = app.modeler.oeditor.GetProperties
    original_value = app.modeler.oeditor.GetPropertyValue

    def properties(tab, name):
        return [*original_properties(tab, name), "Backdrill Top"]

    def value(tab, name, prop):
        if prop == "Backdrill Top":
            return "TOP"
        return original_value(tab, name, prop)

    app.modeler.oeditor.GetProperties = properties
    app.modeler.oeditor.GetPropertyValue = value
    backend = LiveAedtBackend(
        desktop_factory=FakeDesktop,
        layout_factory=lambda **kwargs: app,
    )
    with pytest.raises(LiveBackendError, match="does not support custom Backdrill Top"):
        backend.execute(
            AedtTarget("pid", 42),
            "layout_via_delete_preview",
            {
                "project_name": "Board",
                "design_name": "Layout1",
                "names": ["V1"],
            },
        )


def test_backend_layout_antipad_circle_create_has_owner_readback_and_rollback(monkeypatch):
    target = AedtTarget("port", 50061)
    backend = LiveAedtBackend(
        desktop_factory=FakeDesktop,
        layout_factory=FakeLayoutAntipad,
    )
    request = {
        "project_name": "Board",
        "design_name": "Layout1",
        "voids": [
            {
                "name": "AP_GND_1",
                "owner_name": "GND_PLANE",
                "center": [1.0, -0.5],
                "radius": 0.8,
            }
        ],
    }
    preview = backend.execute(target, "layout_antipad_circle_create_preview", request)
    assert preview["voids"][0]["layer_name"] == "TOP"
    assert preview["owners"][0]["points"] == [
        [-5.0, -5.0],
        [5.0, -5.0],
        [5.0, 5.0],
        [-5.0, 5.0],
    ]
    applied = backend.execute(
        target,
        "layout_antipad_circle_create_apply",
        {"preview_id": preview["preview_id"]},
    )
    assert applied["status"] == "verified"
    assert applied["voids"][0]["owner_membership_verified"] is True
    assert applied["voids"][0]["layer_name"] == "TOP"

    rollback_preview = backend.execute(
        target,
        "layout_antipad_circle_create_preview",
        {
            **request,
            "voids": [{**request["voids"][0], "name": "AP_ROLLBACK"}],
        },
    )
    from aedt_agent.live import backend as backend_module

    with monkeypatch.context() as patch:
        patch.setattr(
            backend_module,
            "_verify_layout_antipad_circle_create_state",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                LiveBackendError("injected anti-pad readback failure")
            ),
        )
        with pytest.raises(LiveBackendError, match="injected anti-pad readback failure"):
            backend.execute(
                target,
                "layout_antipad_circle_create_apply",
                {"preview_id": rollback_preview["preview_id"]},
            )
    app = next(iter(backend._apps.values()))
    assert "AP_ROLLBACK" not in app._voids
    assert "AP_GND_1" in app._voids


def test_backend_layout_antipad_rejects_outside_or_crossing_owner():
    target = AedtTarget("port", 50061)
    backend = LiveAedtBackend(desktop_factory=FakeDesktop, layout_factory=FakeLayoutAntipad)
    with pytest.raises(LiveBackendError, match="crosses the owner boundary"):
        backend.execute(
            target,
            "layout_antipad_circle_create_preview",
            {
                "project_name": "Board",
                "design_name": "Layout1",
                "voids": [
                    {
                        "name": "AP_EDGE",
                        "owner_name": "GND_PLANE",
                        "center": [4.8, 0.0],
                        "radius": 0.5,
                    }
                ],
            },
        )


def test_backend_assigns_existing_hfss_material_batch_with_solve_inside_readback():
    apps = []

    def factory(**kwargs):
        app = FakeMaterialHfss(**kwargs)
        apps.append(app)
        return app

    backend = LiveAedtBackend(desktop_factory=FakeDesktop, hfss_factory=factory)
    target = AedtTarget("pid", 42)
    preview = backend.execute(
        target,
        "hfss_material_assign_preview",
        {
            "project_name": "Board",
            "design_name": "HFSS1",
            "object_names": ["box1", "box2"],
            "material_name": "copper",
            "max_objects": 4,
        },
    )
    assert preview["project_dirty"] is False
    assert preview["project_saved"] is False
    assert preview["target_count"] == 2
    assert preview["target_solve_inside"] is False
    assert preview["target_material"]["definition_digest"]
    assert [item["material_name"] for item in preview["targets_before"]] == [
        "vacuum",
        "vacuum",
    ]

    result = backend.execute(
        target,
        "hfss_material_assign_apply",
        {"preview_id": preview["preview_id"]},
    )
    assert result["status"] == "verified"
    assert result["target_count"] == result["verified_count"] == 2
    assert [item["name"] for item in result["targets_after"]] == ["box1", "box2"]
    assert all(item["material_name"] == "copper" for item in result["targets_after"])
    assert all(item["solve_inside"] is False for item in result["targets_after"])
    assert result["automatic_rollback_on_failure"] is True
    assert result["project_saved"] is False
    assert apps[0].modeler["box1"].material_name == "copper"


def test_backend_hfss_material_assignment_rolls_back_partial_failure_and_rejects_stale():
    apps = []

    def factory(**kwargs):
        app = FakeMaterialHfss(fail_material="copper", **kwargs)
        apps.append(app)
        return app

    backend = LiveAedtBackend(desktop_factory=FakeDesktop, hfss_factory=factory)
    target = AedtTarget("pid", 42)
    request = {
        "project_name": "Board",
        "design_name": "HFSS1",
        "object_names": ["box1", "box2"],
        "material_name": "copper",
        "max_objects": 4,
    }
    preview = backend.execute(target, "hfss_material_assign_preview", request)
    with pytest.raises(LiveBackendError, match="synthetic partial material assignment failure"):
        backend.execute(
            target,
            "hfss_material_assign_apply",
            {"preview_id": preview["preview_id"]},
        )
    for name, color in (("box1", (1, 2, 3)), ("box2", (4, 5, 6))):
        assert apps[0].modeler[name].material_name == "vacuum"
        assert apps[0].modeler[name].solve_inside is True
        assert apps[0].modeler[name].color == color

    stale = backend.execute(target, "hfss_material_assign_preview", request)
    apps[0].modeler["box1"].solve_inside = False
    with pytest.raises(LiveBackendError, match="stale HFSS material assignment preview"):
        backend.execute(
            target,
            "hfss_material_assign_apply",
            {"preview_id": stale["preview_id"]},
        )
    assert apps[0].modeler["box1"].material_name == "vacuum"


@pytest.mark.parametrize(
    "request_payload,error",
    [
        (
            {"object_names": ["box1"], "material_name": "gold"},
            "must already exist",
        ),
        (
            {"object_names": ["sheet1"], "material_name": "copper"},
            "only supports solid objects",
        ),
        (
            {"object_names": ["box1", "BOX1"], "material_name": "copper"},
            "must not contain duplicate names",
        ),
        (
            {"object_names": ["missing"], "material_name": "copper"},
            "unknown exact HFSS object name",
        ),
        (
            {"object_names": ["box1", "box2"], "material_name": "copper", "max_objects": 1},
            "exceeds the approved maximum",
        ),
    ],
)
def test_backend_hfss_material_assignment_preview_rejects_unsafe_targets(
    request_payload,
    error,
):
    backend = LiveAedtBackend(
        desktop_factory=FakeDesktop,
        hfss_factory=FakeMaterialHfss,
    )
    with pytest.raises(LiveBackendError, match=error):
        backend.execute(
            AedtTarget("pid", 42),
            "hfss_material_assign_preview",
            {
                "project_name": "Board",
                "design_name": "HFSS1",
                **request_payload,
            },
        )


def test_backend_lists_and_creates_hfss_length_mesh_with_verified_readback():
    apps = []

    def factory(**kwargs):
        app = FakeMeshHfss(**kwargs)
        apps.append(app)
        return app

    backend = LiveAedtBackend(desktop_factory=FakeDesktop, hfss_factory=factory)
    target = AedtTarget("pid", 42)
    inventory = backend.execute(
        target,
        "hfss_mesh_inventory",
        {"project_name": "Board", "design_name": "HFSS1", "max_items": 10},
    )
    assert inventory["mesh_operation_count"] == 0
    assert inventory["design_unchanged"] is True

    preview = backend.execute(
        target,
        "hfss_length_mesh_create_preview",
        {
            "project_name": "Board",
            "design_name": "HFSS1",
            "mesh_name": "HarnessLength",
            "object_names": ["box1", "box2"],
            "inside_selection": True,
            "maximum_length": "0.4mm",
            "maximum_elements": 500,
            "max_objects": 4,
        },
    )
    assert preview["target_count"] == 2
    assert preview["project_dirty"] is False
    assert preview["project_saved"] is False

    result = backend.execute(
        target,
        "hfss_length_mesh_create_apply",
        {"preview_id": preview["preview_id"]},
    )
    assert result["status"] == "verified"
    assert result["created_mesh_operation_name"] == "HarnessLength"
    assert result["mesh_operation"]["type"] == "Length Based"
    assert result["mesh_operation"]["object_names"] == ["box1", "box2"]
    assert result["mesh_operation"]["inside_selection"] is True
    assert result["mesh_operation"]["maximum_length"] == "0.4mm"
    assert result["mesh_operation"]["maximum_elements"] == 500
    assert result["automatic_rollback_on_failure"] is True
    assert result["project_saved"] is False
    assert list(apps[0].mesh._records) == ["HarnessLength"]


def test_backend_hfss_length_mesh_rolls_back_readback_failure_and_rejects_stale():
    apps = []

    def factory(**kwargs):
        app = FakeMeshHfss(fail_mesh_readback=True, **kwargs)
        apps.append(app)
        return app

    backend = LiveAedtBackend(desktop_factory=FakeDesktop, hfss_factory=factory)
    target = AedtTarget("pid", 42)
    request = {
        "project_name": "Board",
        "design_name": "HFSS1",
        "mesh_name": "MustRollback",
        "object_names": ["box1"],
        "inside_selection": False,
        "maximum_length": "0.2mm",
        "maximum_elements": 100,
    }
    preview = backend.execute(target, "hfss_length_mesh_create_preview", request)
    with pytest.raises(LiveBackendError, match="maximum length readback failed"):
        backend.execute(
            target,
            "hfss_length_mesh_create_apply",
            {"preview_id": preview["preview_id"]},
        )
    assert apps[0].mesh.meshoperation_names == []

    stale = backend.execute(target, "hfss_length_mesh_create_preview", request)
    apps[0].mesh.assign_length_mesh(
        ["box2"],
        maximum_length="1mm",
        maximum_elements=1000,
        name="ExternalLength",
    )
    with pytest.raises(LiveBackendError, match="stale HFSS length mesh create preview"):
        backend.execute(
            target,
            "hfss_length_mesh_create_apply",
            {"preview_id": stale["preview_id"]},
        )
    assert "MustRollback" not in apps[0].mesh.meshoperation_names
    assert "ExternalLength" in apps[0].mesh.meshoperation_names


@pytest.mark.parametrize(
    "payload,error",
    [
        (
            {"mesh_name": "M", "object_names": ["sheet1"]},
            "only supports solid objects",
        ),
        (
            {
                "mesh_name": "M",
                "object_names": ["box1"],
                "maximum_length": "0.5",
            },
            "must include explicit units",
        ),
        (
            {
                "mesh_name": "M",
                "object_names": ["box1"],
                "maximum_length": None,
                "maximum_elements": None,
            },
            "must not both be null",
        ),
        (
            {
                "mesh_name": "M",
                "object_names": ["box1"],
                "inside_selection": "yes",
            },
            "inside_selection must be boolean",
        ),
        (
            {
                "mesh_name": "M",
                "object_names": ["box1"],
                "maximum_elements": 10_000_001,
            },
            "maximum_elements must be an integer between 1 and 10000000",
        ),
    ],
)
def test_backend_hfss_length_mesh_preview_rejects_unsafe_requests(payload, error):
    backend = LiveAedtBackend(
        desktop_factory=FakeDesktop,
        hfss_factory=FakeMeshHfss,
    )
    with pytest.raises(LiveBackendError, match=error):
        backend.execute(
            AedtTarget("pid", 42),
            "hfss_length_mesh_create_preview",
            {"project_name": "Board", "design_name": "HFSS1", **payload},
        )


def test_backend_lists_and_creates_hfss_infinite_sphere_with_verified_readback():
    apps = []

    def factory(**kwargs):
        app = FakeFarFieldHfss(**kwargs)
        apps.append(app)
        return app

    backend = LiveAedtBackend(desktop_factory=FakeDesktop, hfss_factory=factory)
    target = AedtTarget("pid", 42)
    inventory = backend.execute(
        target,
        "hfss_far_field_inventory",
        {"project_name": "Board", "design_name": "HFSS1", "max_items": 10},
    )
    assert inventory["field_setup_count"] == 0
    assert inventory["creation_ready"] is True
    assert [
        (item["name"], item["type"])
        for item in inventory["radiated_field_sources"]
    ] == [("rad1", "Radiation")]
    assert inventory["design_unchanged"] is True

    preview = backend.execute(
        target,
        "hfss_infinite_sphere_create_preview",
        {
            "project_name": "Board",
            "design_name": "HFSS1",
            "sphere_name": "HarnessSphere",
            "definition": "El Over Az",
            "angle1_start": -180,
            "angle1_stop": 180,
            "angle1_step": 5,
            "angle2_start": -90,
            "angle2_stop": 90,
            "angle2_step": 5,
            "units": "deg",
            "polarization": "Slant",
            "polarization_angle": 45,
            "max_samples": 5000,
        },
    )
    assert preview["angle1_axis"] == "Azimuth"
    assert preview["angle2_axis"] == "Elevation"
    assert preview["sample_count"] == 2701
    assert preview["project_dirty"] is False
    assert preview["project_saved"] is False

    result = backend.execute(
        target,
        "hfss_infinite_sphere_create_apply",
        {"preview_id": preview["preview_id"]},
    )
    assert result["status"] == "verified"
    assert result["created_field_setup_name"] == "HarnessSphere"
    assert result["field_setup"]["kind"] == "infinite_sphere"
    assert result["field_setup"]["definition"] == "El Over Az"
    assert result["field_setup"]["angle1_axis"] == "Azimuth"
    assert result["field_setup"]["angle2_axis"] == "Elevation"
    assert result["field_setup"]["polarization"] == "Slant"
    assert result["automatic_rollback_on_failure"] is True
    assert result["project_saved"] is False
    assert list(apps[0]._field_setups) == ["HarnessSphere"]


def test_backend_hfss_infinite_sphere_rolls_back_readback_failure_and_rejects_stale():
    apps = []

    def factory(**kwargs):
        app = FakeFarFieldHfss(mismatch_readback=True, **kwargs)
        apps.append(app)
        return app

    backend = LiveAedtBackend(desktop_factory=FakeDesktop, hfss_factory=factory)
    target = AedtTarget("pid", 42)
    request = {
        "project_name": "Board",
        "design_name": "HFSS1",
        "sphere_name": "MustRollback",
        "definition": "Theta-Phi",
        "angle1_start": 0,
        "angle1_stop": 180,
        "angle1_step": 10,
        "angle2_start": 0,
        "angle2_stop": 360,
        "angle2_step": 10,
    }
    preview = backend.execute(target, "hfss_infinite_sphere_create_preview", request)
    with pytest.raises(LiveBackendError, match="angle1_step readback failed"):
        backend.execute(
            target,
            "hfss_infinite_sphere_create_apply",
            {"preview_id": preview["preview_id"]},
        )
    assert apps[0].field_setup_names == []

    apps[0].mismatch_readback = False
    stale = backend.execute(target, "hfss_infinite_sphere_create_preview", request)
    apps[0].insert_infinite_sphere(
        definition="Theta-Phi",
        theta_start=0,
        theta_stop=180,
        theta_step=30,
        phi_start=0,
        phi_stop=360,
        phi_step=30,
        units="deg",
        custom_coordinate_system=None,
        use_slant_polarization=False,
        polarization_angle=45,
        name="ExternalSphere",
    )
    with pytest.raises(LiveBackendError, match="stale HFSS infinite sphere create preview"):
        backend.execute(
            target,
            "hfss_infinite_sphere_create_apply",
            {"preview_id": stale["preview_id"]},
        )
    assert "MustRollback" not in apps[0].field_setup_names
    assert "ExternalSphere" in apps[0].field_setup_names


@pytest.mark.parametrize(
    "mutator,error",
    [
        (lambda app: app.boundaries.clear(), "requires an existing Radiation"),
        (lambda app: setattr(app, "solution_type", "EigenMode"), "does not support"),
    ],
)
def test_backend_hfss_infinite_sphere_requires_real_radiated_field_prerequisites(
    mutator, error
):
    app = FakeFarFieldHfss(project="Board", design="HFSS1")
    mutator(app)
    backend = LiveAedtBackend(
        desktop_factory=FakeDesktop,
        hfss_factory=lambda **kwargs: app,
    )
    with pytest.raises(LiveBackendError, match=error):
        backend.execute(
            AedtTarget("pid", 42),
            "hfss_infinite_sphere_create_preview",
            {
                "project_name": "Board",
                "design_name": "HFSS1",
                "sphere_name": "Sphere1",
            },
        )


@pytest.mark.parametrize(
    "payload,error",
    [
        ({"sphere_name": "bad/name"}, "safe AEDT name"),
        ({"sphere_name": "S", "definition": "bad"}, "definition must be"),
        ({"sphere_name": "S", "units": "grad"}, "units must be"),
        (
            {"sphere_name": "S", "angle1_start": 10, "angle1_stop": 0},
            "angle1_stop must be greater",
        ),
        (
            {"sphere_name": "S", "angle1_step": 0},
            "angle1_step must be positive",
        ),
        (
            {
                "sphere_name": "S",
                "angle1_step": 1,
                "angle2_step": 1,
                "max_samples": 100,
            },
            "sample count .* exceeds max_samples",
        ),
        ({"sphere_name": "S", "polarization": "circular"}, "polarization must be"),
    ],
)
def test_backend_hfss_infinite_sphere_preview_rejects_unsafe_requests(payload, error):
    backend = LiveAedtBackend(
        desktop_factory=FakeDesktop,
        hfss_factory=FakeFarFieldHfss,
    )
    with pytest.raises(LiveBackendError, match=error):
        backend.execute(
            AedtTarget("pid", 42),
            "hfss_infinite_sphere_create_preview",
            {"project_name": "Board", "design_name": "HFSS1", **payload},
        )


def test_backend_lists_and_creates_five_typed_hfss_surface_boundaries():
    app = FakeSurfaceBoundaryHfss(project="Board", design="HFSS1")
    app.solution_type = "Modal"
    backend = LiveAedtBackend(
        desktop_factory=FakeDesktop,
        hfss_factory=lambda **kwargs: app,
    )
    target = AedtTarget("pid", 42)
    inventory = backend.execute(
        target,
        "hfss_surface_boundary_inventory",
        {"project_name": "Board", "design_name": "HFSS1"},
    )
    assert inventory["boundary_count"] == 1
    assert inventory["supported_surface_boundary_count"] == 0
    assert inventory["design_unchanged"] is True

    requests = [
        {
            "boundary_kind": "perfect_e",
            "boundary_name": "HarnessPerfectE",
            "object_names": ["sheet1"],
            "options": {"is_infinite_ground": True},
        },
        {
            "boundary_kind": "perfect_h",
            "boundary_name": "HarnessPerfectH",
            "face_ids": [201],
        },
        {
            "boundary_kind": "finite_conductivity",
            "boundary_name": "HarnessFinite",
            "face_ids": [101],
            "options": {
                "material_name": "copper",
                "use_thickness": True,
                "thickness": "35um",
                "roughness": "0.5um",
                "is_two_sided": False,
                "is_internal": True,
            },
        },
        {
            "boundary_kind": "lumped_rlc",
            "boundary_name": "HarnessRLC",
            "object_names": ["sheet1"],
            "options": {
                "rlc_type": "Serial",
                "integration_line_direction": "XPos",
                "resistance": 50,
                "inductance": 1e-9,
                "capacitance": 2e-12,
            },
        },
        {
            "boundary_kind": "impedance",
            "boundary_name": "HarnessImpedance",
            "object_names": ["sheet1"],
            "options": {
                "resistance": 75,
                "reactance": -10,
                "is_infinite_ground": False,
            },
        },
    ]
    results = []
    for request in requests:
        preview = backend.execute(
            target,
            "hfss_surface_boundary_create_preview",
            {"project_name": "Board", "design_name": "HFSS1", **request},
        )
        assert preview["project_dirty"] is False
        assert preview["project_saved"] is False
        results.append(
            backend.execute(
                target,
                "hfss_surface_boundary_create_apply",
                {"preview_id": preview["preview_id"]},
            )
        )

    assert [item["status"] for item in results] == ["verified"] * 5
    assert [item["boundary"]["kind"] for item in results] == [
        "perfect_e",
        "perfect_h",
        "finite_conductivity",
        "lumped_rlc",
        "impedance",
    ]
    assert results[0]["boundary"]["object_names"] == ["sheet1"]
    assert results[1]["boundary"]["face_ids"] == [201]
    assert results[2]["boundary"]["options"]["material_name"] == "copper"
    assert results[2]["boundary"]["options"]["thickness"] == "35um"
    assert results[3]["boundary"]["options"]["rlc_type"] == "Serial"
    assert results[3]["boundary"]["options"]["integration_line"] == {
        "start": ["1.0mm", "0.0mm", "0.0mm"],
        "end": ["-1.0mm", "0.0mm", "0.0mm"],
    }
    assert results[3]["boundary"]["options"]["resistance"] == "50.0ohm"
    assert results[3]["boundary"]["options"]["inductance"] == "1e-09H"
    assert results[3]["boundary"]["options"]["capacitance"] == "2e-12F"
    assert float(results[4]["boundary"]["options"]["resistance"]) == 75.0
    assert all(item["automatic_rollback_on_failure"] is True for item in results)
    assert all(item["project_saved"] is False for item in results)


def test_backend_hfss_surface_boundary_rolls_back_readback_failure_and_rejects_stale():
    app = FakeSurfaceBoundaryHfss(
        project="Board",
        design="HFSS1",
        mismatch_readback=True,
    )
    backend = LiveAedtBackend(
        desktop_factory=FakeDesktop,
        hfss_factory=lambda **kwargs: app,
    )
    target = AedtTarget("pid", 42)
    request = {
        "project_name": "Board",
        "design_name": "HFSS1",
        "boundary_kind": "perfect_e",
        "boundary_name": "MustRollback",
        "object_names": ["sheet1"],
        "options": {"is_infinite_ground": True},
    }
    preview = backend.execute(target, "hfss_surface_boundary_create_preview", request)
    with pytest.raises(LiveBackendError, match="infinite-ground readback failed"):
        backend.execute(
            target,
            "hfss_surface_boundary_create_apply",
            {"preview_id": preview["preview_id"]},
        )
    assert "MustRollback" not in [item.name for item in app.boundaries]

    stale = backend.execute(target, "hfss_surface_boundary_create_preview", request)
    app.assign_perfect_h([201], name="ExternalPerfectH")
    with pytest.raises(LiveBackendError, match="stale HFSS surface boundary create preview"):
        backend.execute(
            target,
            "hfss_surface_boundary_create_apply",
            {"preview_id": stale["preview_id"]},
        )
    assert "MustRollback" not in [item.name for item in app.boundaries]
    assert "ExternalPerfectH" in [item.name for item in app.boundaries]


def test_backend_hfss_lumped_rlc_rolls_back_typed_readback_failure():
    app = FakeSurfaceBoundaryHfss(
        project="Board",
        design="HFSS1",
        mismatch_readback=True,
    )
    app.solution_type = "Modal"
    backend = LiveAedtBackend(
        desktop_factory=FakeDesktop,
        hfss_factory=lambda **kwargs: app,
    )
    target = AedtTarget("pid", 42)
    preview = backend.execute(
        target,
        "hfss_surface_boundary_create_preview",
        {
            "project_name": "Board",
            "design_name": "HFSS1",
            "boundary_kind": "lumped_rlc",
            "boundary_name": "MustRollbackRLC",
            "object_names": ["sheet1"],
            "options": {
                "rlc_type": "Parallel",
                "integration_line_direction": "YNeg",
                "resistance": 25,
            },
        },
    )
    with pytest.raises(LiveBackendError, match="type readback failed"):
        backend.execute(
            target,
            "hfss_surface_boundary_create_apply",
            {"preview_id": preview["preview_id"]},
        )
    assert "MustRollbackRLC" not in [item.name for item in app.boundaries]


def test_backend_hfss_lumped_rlc_rejects_unsupported_solution_type():
    app = FakeSurfaceBoundaryHfss(project="Board", design="HFSS1")
    app.solution_type = "Characteristic Mode"
    backend = LiveAedtBackend(
        desktop_factory=FakeDesktop,
        hfss_factory=lambda **kwargs: app,
    )
    with pytest.raises(LiveBackendError, match="does not support Lumped RLC"):
        backend.execute(
            AedtTarget("pid", 42),
            "hfss_surface_boundary_create_preview",
            {
                "project_name": "Board",
                "design_name": "HFSS1",
                "boundary_kind": "lumped_rlc",
                "boundary_name": "InvalidSolutionRLC",
                "object_names": ["sheet1"],
                "options": {"resistance": 50},
            },
        )


@pytest.mark.parametrize(
    "payload,error",
    [
        (
            {
                "boundary_kind": "perfect_e",
                "boundary_name": "B",
                "object_names": ["box1"],
                "face_ids": [101],
            },
            "exactly one",
        ),
        (
            {
                "boundary_kind": "impedance",
                "boundary_name": "B",
                "face_ids": [101],
            },
            "requires explicit sheet object_names",
        ),
        (
            {
                "boundary_kind": "impedance",
                "boundary_name": "B",
                "object_names": ["box1"],
            },
            "requires sheet objects",
        ),
        (
            {
                "boundary_kind": "lumped_rlc",
                "boundary_name": "B",
                "face_ids": [301],
                "options": {"resistance": 50},
            },
            "requires explicit sheet object_names",
        ),
        (
            {
                "boundary_kind": "lumped_rlc",
                "boundary_name": "B",
                "object_names": ["sheet1", "box1"],
                "options": {"resistance": 50},
            },
            "requires exactly one",
        ),
        (
            {
                "boundary_kind": "lumped_rlc",
                "boundary_name": "B",
                "object_names": ["box1"],
                "options": {"resistance": 50},
            },
            "requires sheet objects",
        ),
        (
            {
                "boundary_kind": "lumped_rlc",
                "boundary_name": "B",
                "object_names": ["sheet1"],
                "options": {},
            },
            "requires at least one positive",
        ),
        (
            {
                "boundary_kind": "lumped_rlc",
                "boundary_name": "B",
                "object_names": ["sheet1"],
                "options": {"resistance": -1},
            },
            "must be a positive finite number",
        ),
        (
            {
                "boundary_kind": "lumped_rlc",
                "boundary_name": "B",
                "object_names": ["sheet1"],
                "options": {"resistance": 50, "integration_line_direction": "Diagonal"},
            },
            "must be XNeg",
        ),
        (
            {
                "boundary_kind": "finite_conductivity",
                "boundary_name": "B",
                "object_names": ["box1"],
                "options": {"material_name": "missing"},
            },
            "must already exist",
        ),
        (
            {
                "boundary_kind": "finite_conductivity",
                "boundary_name": "B",
                "object_names": ["box1"],
                "options": {"material_name": "copper", "thickness": "0"},
            },
            "must include explicit units",
        ),
        (
            {
                "boundary_kind": "perfect_e",
                "boundary_name": "B",
                "object_names": ["box1"],
                "options": {"is_infinite_ground": True},
            },
            "requires planar sheet objects",
        ),
        (
            {
                "boundary_kind": "perfect_h",
                "boundary_name": "B",
                "object_names": ["box1"],
                "options": {"is_infinite_ground": True},
            },
            "unsupported perfect_h option",
        ),
    ],
)
def test_backend_hfss_surface_boundary_preview_rejects_unsafe_requests(payload, error):
    backend = LiveAedtBackend(
        desktop_factory=FakeDesktop,
        hfss_factory=FakeSurfaceBoundaryHfss,
    )
    with pytest.raises(LiveBackendError, match=error):
        backend.execute(
            AedtTarget("pid", 42),
            "hfss_surface_boundary_create_preview",
            {"project_name": "Board", "design_name": "HFSS1", **payload},
        )


def test_backend_lists_creates_and_restores_hfss_coordinate_system():
    app = FakeCoordinateHfss(project="Board", design="HFSS1")
    app.modeler.create_coordinate_system(
        origin=["1mm", "2mm", "3mm"],
        reference_cs="Global",
        name="ParentCS",
        mode="axis",
        x_pointing=[1, 0, 0],
        y_pointing=[0, 1, 0],
    )
    app.modeler.oeditor.SetWCS(
        [
            "NAME:SetWCS Parameter",
            "Working Coordinate System:=",
            "Global",
            "RegionDepCSOk:=",
            False,
        ]
    )
    backend = LiveAedtBackend(
        desktop_factory=FakeDesktop,
        hfss_factory=lambda **kwargs: app,
    )
    target = AedtTarget("pid", 42)
    inventory = backend.execute(
        target,
        "hfss_coordinate_system_inventory",
        {"project_name": "Board", "design_name": "HFSS1"},
    )
    assert inventory["coordinate_system_count"] == 2
    assert inventory["relative_coordinate_system_count"] == 1
    assert inventory["active_coordinate_system"] == "Global"
    assert inventory["design_unchanged"] is True

    preview = backend.execute(
        target,
        "hfss_coordinate_system_create_preview",
        {
            "project_name": "Board",
            "design_name": "HFSS1",
            "coordinate_system_name": "HarnessCS",
            "reference_coordinate_system": "ParentCS",
            "origin": ["OX", "2mm", 3],
            "x_axis": [1, 1, 0],
            "y_axis": [0, 0, 2],
        },
    )
    assert preview["active_coordinate_system_before"] == "Global"
    assert preview["reference_coordinate_system"] == "ParentCS"
    assert preview["project_dirty"] is False
    assert set(app.modeler._coordinate_systems) == {"ParentCS"}

    result = backend.execute(
        target,
        "hfss_coordinate_system_create_apply",
        {"preview_id": preview["preview_id"]},
    )
    assert result["status"] == "verified"
    assert result["created_coordinate_system_name"] == "HarnessCS"
    assert result["coordinate_system"]["reference_coordinate_system"] == "ParentCS"
    assert result["coordinate_system"]["origin"] == ["OX", "2mm", "3mm"]
    assert result["coordinate_system"]["x_axis"] == ["1mm", "1mm", "0mm"]
    assert result["coordinate_system"]["y_axis"] == ["0mm", "0mm", "2mm"]
    assert result["active_coordinate_system_restored"] is True
    assert app.modeler._active_coordinate_system == "Global"
    assert result["automatic_rollback_on_failure"] is True
    assert result["project_saved"] is False


def test_backend_hfss_coordinate_system_rejects_stale_and_rolls_back_bad_readback():
    app = FakeCoordinateHfss(project="Board", design="HFSS1")
    backend = LiveAedtBackend(
        desktop_factory=FakeDesktop,
        hfss_factory=lambda **kwargs: app,
    )
    target = AedtTarget("pid", 42)
    request = {
        "project_name": "Board",
        "design_name": "HFSS1",
        "coordinate_system_name": "MustNotCreate",
        "origin": [0, 0, 0],
        "x_axis": [1, 0, 0],
        "y_axis": [0, 1, 0],
    }
    stale = backend.execute(target, "hfss_coordinate_system_create_preview", request)
    app.variable_manager.variables["External"] = "1mm"
    with pytest.raises(LiveBackendError, match="stale HFSS coordinate system create preview"):
        backend.execute(
            target,
            "hfss_coordinate_system_create_apply",
            {"preview_id": stale["preview_id"]},
        )
    assert "MustNotCreate" not in app.modeler._coordinate_systems
    del app.variable_manager.variables["External"]

    app.modeler.mismatch_readback = True
    failed = backend.execute(target, "hfss_coordinate_system_create_preview", request)
    with pytest.raises(LiveBackendError, match=r"x_axis\[0\] readback failed"):
        backend.execute(
            target,
            "hfss_coordinate_system_create_apply",
            {"preview_id": failed["preview_id"]},
        )
    assert set(app.modeler._coordinate_systems) == set()
    assert app.modeler._active_coordinate_system == "Global"


@pytest.mark.parametrize(
    "payload,error",
    [
        ({"coordinate_system_name": "Global"}, "must not be Global"),
        ({"coordinate_system_name": "bad/name"}, "safe AEDT name"),
        ({"coordinate_system_name": "CS", "origin": ["x;bad", 0, 0]}, "unsupported"),
        ({"coordinate_system_name": "CS", "x_axis": [0, 0, 0]}, "x_axis must be nonzero"),
        ({"coordinate_system_name": "CS", "y_axis": [0, 0, 0]}, "y_axis must be nonzero"),
        (
            {"coordinate_system_name": "CS", "x_axis": [1, 0, 0], "y_axis": [2, 0, 0]},
            "must not be collinear",
        ),
        ({"coordinate_system_name": "CS", "x_axis": ["1", 0, 0]}, "finite number"),
        (
            {"coordinate_system_name": "CS", "reference_coordinate_system": "Missing"},
            "must be Global or an existing relative",
        ),
    ],
)
def test_backend_hfss_coordinate_system_preview_rejects_unsafe_requests(payload, error):
    app = FakeCoordinateHfss(project="Board", design="HFSS1")
    backend = LiveAedtBackend(
        desktop_factory=FakeDesktop,
        hfss_factory=lambda **kwargs: app,
    )
    request = {
        "project_name": "Board",
        "design_name": "HFSS1",
        "coordinate_system_name": "CS",
        "origin": [0, 0, 0],
        "x_axis": [1, 0, 0],
        "y_axis": [0, 1, 0],
        **payload,
    }
    with pytest.raises(LiveBackendError, match=error):
        backend.execute(
            AedtTarget("pid", 42),
            "hfss_coordinate_system_create_preview",
            request,
        )


def test_backend_hfss_coordinate_system_rejects_duplicate_and_non_relative_reference():
    app = FakeCoordinateHfss(project="Board", design="HFSS1")
    app.modeler.create_coordinate_system(
        origin=[0, 0, 0],
        reference_cs="Global",
        name="ExistingCS",
        x_pointing=[1, 0, 0],
        y_pointing=[0, 1, 0],
    )
    app.modeler._coordinate_systems["FaceCS"] = {
        "Type": "Face",
        "Reference CS": "Global",
        "Mode": "Axis/Position",
    }
    app.modeler._active_coordinate_system = "Global"
    backend = LiveAedtBackend(
        desktop_factory=FakeDesktop,
        hfss_factory=lambda **kwargs: app,
    )
    base = {
        "project_name": "Board",
        "design_name": "HFSS1",
        "origin": [0, 0, 0],
        "x_axis": [1, 0, 0],
        "y_axis": [0, 1, 0],
    }
    with pytest.raises(LiveBackendError, match="already exists: ExistingCS"):
        backend.execute(
            AedtTarget("pid", 42),
            "hfss_coordinate_system_create_preview",
            {**base, "coordinate_system_name": "existingcs"},
        )
    with pytest.raises(LiveBackendError, match="must be Global or an existing relative"):
        backend.execute(
            AedtTarget("pid", 42),
            "hfss_coordinate_system_create_preview",
            {
                **base,
                "coordinate_system_name": "ChildCS",
                "reference_coordinate_system": "FaceCS",
            },
        )


def test_backend_hfss_coordinate_system_rejects_wrong_design_and_running_solve():
    app = FakeCoordinateHfss(project="Board", design="HFSS1")
    backend = LiveAedtBackend(
        desktop_factory=FakeDesktop,
        hfss_factory=lambda **kwargs: app,
    )
    request = {
        "project_name": "Board",
        "design_name": "HFSS1",
        "coordinate_system_name": "CS",
        "origin": [0, 0, 0],
        "x_axis": [1, 0, 0],
        "y_axis": [0, 1, 0],
    }
    app.are_there_simulations_running = True
    with pytest.raises(LiveBackendError, match="while a simulation is running"):
        backend.execute(
            AedtTarget("pid", 42),
            "hfss_coordinate_system_create_preview",
            request,
        )
    app.are_there_simulations_running = False
    app.design_type = "HFSS 3D Layout Design"
    with pytest.raises(LiveBackendError, match="requires an HFSS 3D design"):
        backend.execute(
            AedtTarget("pid", 42),
            "hfss_coordinate_system_inventory",
            {"project_name": "Board", "design_name": "HFSS1"},
        )


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


def test_backend_moves_exact_hfss_geometry_batch_with_typed_readback():
    app = FakeMoveHfss(project="Board", design="HFSS1")
    backend = LiveAedtBackend(
        desktop_factory=FakeDesktop,
        hfss_factory=lambda **kwargs: app,
    )
    target = AedtTarget("pid", 42)
    fixed_before = list(app.modeler["fixed1"].bounding_box)
    preview = backend.execute(
        target,
        "hfss_geometry_move_preview",
        {
            "project_name": "Board",
            "design_name": "HFSS1",
            "moves": [
                {"name": "box1", "vector": [1.25, -2.5, 3.75]},
                {"name": "sheet1", "vector": [-4, 5, 0.25]},
            ],
        },
    )
    assert preview["names"] == ["box1", "sheet1"]
    assert preview["model_units"] == "mm"
    assert preview["coordinate_system"] == "Global"
    assert preview["target_count"] == 2
    assert preview["boundary_count"] == 1
    assert preview["project_dirty"] is False

    result = backend.execute(
        target,
        "hfss_geometry_move_apply",
        {"preview_id": preview["preview_id"]},
    )
    assert result["status"] == "verified"
    assert result["moved_object_names"] == ["box1", "sheet1"]
    assert result["moved_object_count"] == 2
    assert result["boundaries_preserved"] is True
    assert result["mesh_operations_preserved"] is True
    assert result["active_coordinate_system_preserved"] is True
    assert result["automatic_rollback_on_failure"] is True
    assert result["project_saved"] is False
    assert app.modeler["box1"].bounding_box == [0.25, -3.5, 2.75, 2.25, -1.5, 4.75]
    assert app.modeler["sheet1"].bounding_box == [0.0, 5.0, 0.25, 2.0, 7.0, 0.25]
    assert app.modeler["fixed1"].bounding_box == fixed_before
    assert [item[0] for item in app.modeler.move_calls] == [["box1"], ["sheet1"]]


def test_backend_hfss_geometry_move_rejects_unsafe_specs_and_stale_state():
    app = FakeMoveHfss(project="Board", design="HFSS1")
    backend = LiveAedtBackend(
        desktop_factory=FakeDesktop,
        hfss_factory=lambda **kwargs: app,
    )
    target = AedtTarget("pid", 42)
    base = {"project_name": "Board", "design_name": "HFSS1"}
    unsafe = [
        [],
        [{"name": "box1", "vector": [0, 0, 0]}],
        [{"name": "BOX1", "vector": [1, 0, 0]}],
        [
            {"name": "box1", "vector": [1, 0, 0]},
            {"name": "BOX1", "vector": [0, 1, 0]},
        ],
        [{"name": "missing", "vector": [1, 0, 0]}],
        [{"name": "box1", "vector": [float("inf"), 0, 0]}],
        [{"name": "box1", "vector": [1, 0, 0], "extra": True}],
    ]
    for moves in unsafe:
        with pytest.raises(LiveBackendError):
            backend.execute(target, "hfss_geometry_move_preview", {**base, "moves": moves})

    app.modeler.active_coordinate_system = "RelativeCS"
    with pytest.raises(LiveBackendError, match="requires Global"):
        backend.execute(
            target,
            "hfss_geometry_move_preview",
            {**base, "moves": [{"name": "box1", "vector": [1, 0, 0]}]},
        )
    app.modeler.active_coordinate_system = "Global"

    stale = backend.execute(
        target,
        "hfss_geometry_move_preview",
        {**base, "moves": [{"name": "box1", "vector": [1, 0, 0]}]},
    )
    app.modeler.move(["fixed1"], [0, 1, 0])
    with pytest.raises(LiveBackendError, match="stale HFSS geometry move preview"):
        backend.execute(
            target,
            "hfss_geometry_move_apply",
            {"preview_id": stale["preview_id"]},
        )


def test_backend_hfss_geometry_move_rolls_back_partial_and_readback_failures(monkeypatch):
    app = FakeMoveHfss(move_fail_on="sheet1", project="Board", design="HFSS1")
    backend = LiveAedtBackend(
        desktop_factory=FakeDesktop,
        hfss_factory=lambda **kwargs: app,
    )
    target = AedtTarget("pid", 42)
    request = {
        "project_name": "Board",
        "design_name": "HFSS1",
        "moves": [
            {"name": "box1", "vector": [1, 2, 3]},
            {"name": "sheet1", "vector": [-2, 4, 1]},
        ],
    }
    before = {
        name: list(app.modeler[name].bounding_box)
        for name in app.modeler.object_names
    }
    preview = backend.execute(target, "hfss_geometry_move_preview", request)
    with pytest.raises(LiveBackendError, match="returned false"):
        backend.execute(
            target,
            "hfss_geometry_move_apply",
            {"preview_id": preview["preview_id"]},
        )
    assert {
        name: app.modeler[name].bounding_box for name in app.modeler.object_names
    } == before

    app.modeler.fail_on = ""
    preview = backend.execute(target, "hfss_geometry_move_preview", request)
    import aedt_agent.live.backend as backend_module

    def fail_readback(*args, **kwargs):
        raise LiveBackendError("synthetic geometry move readback failure")

    monkeypatch.setattr(backend_module, "_verify_hfss_geometry_move_state", fail_readback)
    with pytest.raises(LiveBackendError, match="synthetic geometry move readback failure"):
        backend.execute(
            target,
            "hfss_geometry_move_apply",
            {"preview_id": preview["preview_id"]},
        )
    assert {
        name: app.modeler[name].bounding_box for name in app.modeler.object_names
    } == before


def _fake_rotation_snapshot(app):
    return {
        name: {
            "bbox": list(app.modeler[name].bounding_box),
            "faces": [
                (face.id, list(face.center), face.area, face.is_planar)
                for face in app.modeler[name].faces
            ],
            "vertices": [
                (vertex.id, list(vertex.position))
                for vertex in app.modeler[name].vertices
            ],
        }
        for name in app.modeler.object_names
    }


def test_backend_rotates_exact_hfss_geometry_batch_with_typed_readback():
    app = FakeRotateHfss(project="Board", design="HFSS1")
    backend = LiveAedtBackend(
        desktop_factory=FakeDesktop,
        hfss_factory=lambda **kwargs: app,
    )
    target = AedtTarget("pid", 42)
    before = _fake_rotation_snapshot(app)
    preview = backend.execute(
        target,
        "hfss_geometry_rotate_preview",
        {
            "project_name": "Board",
            "design_name": "HFSS1",
            "rotations": [
                {"name": "box1", "axis": "z", "angle_degrees": 90},
                {"name": "sheet1", "axis": "X", "angle_degrees": -90},
            ],
        },
    )
    assert preview["names"] == ["box1", "sheet1"]
    assert preview["coordinate_system"] == "Global"
    assert preview["rotation_origin"] == [0.0, 0.0, 0.0]
    assert preview["angle_units"] == "deg"
    assert preview["target_count"] == 2
    assert preview["boundary_count"] == 1
    assert preview["project_dirty"] is False

    result = backend.execute(
        target,
        "hfss_geometry_rotate_apply",
        {"preview_id": preview["preview_id"]},
    )
    assert result["status"] == "verified"
    assert result["rotated_object_names"] == ["box1", "sheet1"]
    assert result["rotated_object_count"] == 2
    assert result["boundaries_preserved"] is True
    assert result["mesh_operations_preserved"] is True
    assert result["active_coordinate_system_preserved"] is True
    assert result["automatic_rollback_on_failure"] is True
    assert result["project_saved"] is False
    after = _fake_rotation_snapshot(app)
    assert after["fixed1"] == before["fixed1"]
    assert after["box1"]["vertices"][0][1] == [0.0, 1.0, 0.0]
    assert after["sheet1"]["faces"][0][1] == [5.0, 1.0, -2.0]
    assert [item[0] for item in app.modeler.rotate_calls] == [["box1"], ["sheet1"]]


def test_backend_hfss_geometry_rotate_rejects_unsafe_specs_and_stale_state():
    app = FakeRotateHfss(project="Board", design="HFSS1")
    backend = LiveAedtBackend(
        desktop_factory=FakeDesktop,
        hfss_factory=lambda **kwargs: app,
    )
    target = AedtTarget("pid", 42)
    base = {"project_name": "Board", "design_name": "HFSS1"}
    unsafe = [
        [],
        [{"name": "box1", "axis": "Z", "angle_degrees": 0}],
        [{"name": "box1", "axis": "Z", "angle_degrees": 360}],
        [{"name": "box1", "axis": "Z", "angle_degrees": 361}],
        [{"name": "box1", "axis": "Z", "angle_degrees": True}],
        [{"name": "box1", "axis": "Q", "angle_degrees": 90}],
        [{"name": "BOX1", "axis": "Z", "angle_degrees": 90}],
        [
            {"name": "box1", "axis": "Z", "angle_degrees": 90},
            {"name": "BOX1", "axis": "X", "angle_degrees": 45},
        ],
        [{"name": "missing", "axis": "Z", "angle_degrees": 90}],
        [{"name": "box1", "axis": "Z", "angle_degrees": float("inf")}],
        [{"name": "box1", "axis": "Z", "angle_degrees": 90, "extra": True}],
    ]
    for rotations in unsafe:
        with pytest.raises(LiveBackendError):
            backend.execute(
                target,
                "hfss_geometry_rotate_preview",
                {**base, "rotations": rotations},
            )

    original_face_centers = [list(face.center) for face in app.modeler["box1"].faces]
    original_vertices = list(app.modeler["box1"].vertices)
    for index, face in enumerate(app.modeler["box1"].faces):
        face.center = [0, 0, index]
    app.modeler["box1"].vertices = []
    with pytest.raises(LiveBackendError, match="not observable"):
        backend.execute(
            target,
            "hfss_geometry_rotate_preview",
            {
                **base,
                "rotations": [
                    {"name": "box1", "axis": "Z", "angle_degrees": 90}
                ],
            },
        )
    for face, center in zip(app.modeler["box1"].faces, original_face_centers):
        face.center = center
    app.modeler["box1"].vertices = original_vertices

    app.modeler.active_coordinate_system = "RelativeCS"
    with pytest.raises(LiveBackendError, match="requires Global"):
        backend.execute(
            target,
            "hfss_geometry_rotate_preview",
            {
                **base,
                "rotations": [
                    {"name": "box1", "axis": "Z", "angle_degrees": 90}
                ],
            },
        )
    app.modeler.active_coordinate_system = "Global"

    stale = backend.execute(
        target,
        "hfss_geometry_rotate_preview",
        {
            **base,
            "rotations": [
                {"name": "box1", "axis": "Z", "angle_degrees": 90}
            ],
        },
    )
    app.modeler.rotate(["fixed1"], "Z", angle=90, units="deg")
    with pytest.raises(LiveBackendError, match="stale HFSS geometry rotation preview"):
        backend.execute(
            target,
            "hfss_geometry_rotate_apply",
            {"preview_id": stale["preview_id"]},
        )


def test_backend_hfss_geometry_rotate_rolls_back_partial_and_readback_failures(
    monkeypatch,
):
    app = FakeRotateHfss(rotate_fail_on="sheet1", project="Board", design="HFSS1")
    backend = LiveAedtBackend(
        desktop_factory=FakeDesktop,
        hfss_factory=lambda **kwargs: app,
    )
    target = AedtTarget("pid", 42)
    request = {
        "project_name": "Board",
        "design_name": "HFSS1",
        "rotations": [
            {"name": "box1", "axis": "Z", "angle_degrees": 90},
            {"name": "sheet1", "axis": "X", "angle_degrees": -90},
        ],
    }
    before = _fake_rotation_snapshot(app)
    preview = backend.execute(target, "hfss_geometry_rotate_preview", request)
    with pytest.raises(LiveBackendError, match="returned false"):
        backend.execute(
            target,
            "hfss_geometry_rotate_apply",
            {"preview_id": preview["preview_id"]},
        )
    assert _fake_rotation_snapshot(app) == before

    app.modeler.fail_on = ""
    preview = backend.execute(target, "hfss_geometry_rotate_preview", request)
    import aedt_agent.live.backend as backend_module

    def fail_readback(*args, **kwargs):
        raise LiveBackendError("synthetic geometry rotation readback failure")

    monkeypatch.setattr(
        backend_module,
        "_verify_hfss_geometry_rotation_state",
        fail_readback,
    )
    with pytest.raises(
        LiveBackendError,
        match="synthetic geometry rotation readback failure",
    ):
        backend.execute(
            target,
            "hfss_geometry_rotate_apply",
            {"preview_id": preview["preview_id"]},
        )
    assert _fake_rotation_snapshot(app) == before


def test_backend_hfss_antipad_subtract_verifies_volume_and_undo_rollback(monkeypatch):
    app = FakeHfssAntipad(project="Board", design="HFSS1")
    backend = LiveAedtBackend(
        desktop_factory=FakeDesktop,
        hfss_factory=lambda **kwargs: app,
    )
    target = AedtTarget("pid", 42)
    request = {
        "project_name": "Board",
        "design_name": "HFSS1",
        "blank_object_name": "L2_GND",
        "tool_name": "__AP_TOOL",
        "center": [1.0, -0.5],
        "radius": 0.8,
    }
    preview = backend.execute(target, "hfss_antipad_subtract_preview", request)
    assert preview["blank_z_range"] == [0.0, 0.035]
    assert preview["tool_origin"] == pytest.approx([1.0, -0.5, -0.0035])
    assert preview["tool_height"] == pytest.approx(0.042)
    applied = backend.execute(
        target,
        "hfss_antipad_subtract_apply",
        {"preview_id": preview["preview_id"]},
    )
    assert applied["status"] == "verified"
    assert applied["removed_volume"] == pytest.approx(math.pi * 0.8 * 0.8 * 0.035)
    assert applied["blank_after"]["object_id"] == 6
    assert applied["blank_after"]["material_name"] == "copper"
    assert applied["tool_deleted"] is True
    assert applied["boundaries_preserved"] is True

    rollback_app = FakeHfssAntipad(project="Board", design="HFSS1")
    rollback_backend = LiveAedtBackend(
        desktop_factory=FakeDesktop,
        hfss_factory=lambda **kwargs: rollback_app,
    )
    rollback_preview = rollback_backend.execute(
        target,
        "hfss_antipad_subtract_preview",
        request,
    )
    before = rollback_preview["snapshot_digest"]
    from aedt_agent.live import backend as backend_module

    with monkeypatch.context() as patch:
        patch.setattr(
            backend_module,
            "_verify_hfss_antipad_subtract_state",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                LiveBackendError("injected HFSS anti-pad readback failure")
            ),
        )
        with pytest.raises(LiveBackendError, match="injected HFSS anti-pad readback failure"):
            rollback_backend.execute(
                target,
                "hfss_antipad_subtract_apply",
                {"preview_id": rollback_preview["preview_id"]},
            )
    retry = rollback_backend.execute(target, "hfss_antipad_subtract_preview", request)
    assert retry["snapshot_digest"] == before
    assert rollback_app.modeler.object_names == ["L2_GND"]


def test_backend_hfss_antipad_rejects_non_fitting_circle_and_non_global_wcs():
    app = FakeHfssAntipad(project="Board", design="HFSS1")
    backend = LiveAedtBackend(desktop_factory=FakeDesktop, hfss_factory=lambda **kwargs: app)
    target = AedtTarget("pid", 42)
    base = {
        "project_name": "Board",
        "design_name": "HFSS1",
        "blank_object_name": "L2_GND",
        "tool_name": "__AP_TOOL",
        "center": [4.8, 0.0],
        "radius": 0.5,
    }
    with pytest.raises(LiveBackendError, match="fit inside"):
        backend.execute(target, "hfss_antipad_subtract_preview", base)
    derived = backend.execute(
        target,
        "hfss_antipad_subtract_preview",
        {key: value for key, value in {**base, "center": [0.0, 0.0]}.items() if key != "tool_name"},
    )
    assert derived["tool_name"].startswith("__AEDT_AGENT_AP_")
    app.modeler.active_coordinate_system = "RelativeCS"
    with pytest.raises(LiveBackendError, match="requires Global"):
        backend.execute(
            target,
            "hfss_antipad_subtract_preview",
            {**base, "center": [0.0, 0.0]},
        )


def test_backend_creates_and_reads_typed_hfss_wave_and_lumped_ports():
    apps = []

    def factory(**kwargs):
        app = FakeTypedPortHfss(**kwargs)
        apps.append(app)
        return app

    backend = LiveAedtBackend(desktop_factory=FakeDesktop, hfss_factory=factory)
    target = AedtTarget("pid", 42)
    initial = backend.execute(
        target,
        "hfss_port_inventory",
        {"project_name": "Board", "design_name": "HFSS1"},
    )
    assert initial["port_count"] == 0
    assert initial["design_unchanged"] is True

    wave_preview = backend.execute(
        target,
        "hfss_boundary_preview",
        {
            "project_name": "Board",
            "design_name": "HFSS1",
            "boundary_kind": "wave_port",
            "boundary_name": "TypedWave",
            "assignment_face_ids": [101],
            "options": {
                "modes": 2,
                "renormalize": False,
                "deembed": 1.25,
                "integration_line_direction": "YPos",
                "characteristic_impedance": "Zwave",
            },
        },
    )
    assert wave_preview["resolved_integration_line"] == {
        "start": ["0.0mm", "-1.0mm", "0.0mm"],
        "end": ["0.0mm", "1.0mm", "0.0mm"],
    }
    wave = backend.execute(
        target,
        "hfss_boundary_apply",
        {"preview_id": wave_preview["preview_id"]},
    )
    assert wave["status"] == "verified"
    assert wave["boundary"]["kind"] == "wave_port"
    assert wave["boundary"]["face_ids"] == [101]
    assert wave["boundary"]["options"]["mode_count"] == 2
    assert wave["boundary"]["options"]["deembed_distance"] == "1.25mm"
    assert {
        item["characteristic_impedance"] for item in wave["boundary"]["options"]["modes"]
    } == {"Zwave"}

    lumped_preview = backend.execute(
        target,
        "hfss_boundary_preview",
        {
            "project_name": "Board",
            "design_name": "HFSS1",
            "boundary_kind": "lumped_port",
            "boundary_name": "TypedLumped",
            "assignment_object_name": "PortSheet",
            "options": {
                "impedance": 60,
                "renormalize": False,
                "deembed": True,
                "integration_line_direction": "XPos",
            },
        },
    )
    lumped = backend.execute(
        target,
        "hfss_boundary_apply",
        {"preview_id": lumped_preview["preview_id"]},
    )
    assert lumped["status"] == "verified"
    assert lumped["boundary"]["kind"] == "lumped_port"
    assert lumped["boundary"]["object_names"] == ["PortSheet"]
    assert lumped["boundary"]["options"]["impedance"] == "60.0ohm"
    assert lumped["boundary"]["options"]["deembed_enabled"] is True

    inventory = backend.execute(
        target,
        "hfss_port_inventory",
        {"project_name": "Board", "design_name": "HFSS1"},
    )
    assert inventory["port_count"] == 2
    assert [item["name"] for item in inventory["ports"]] == ["TypedLumped", "TypedWave"]
    assert apps[0].ports[-2:] == ["TypedWave", "TypedLumped"]


def test_backend_typed_hfss_port_rolls_back_readback_failure():
    apps = []

    def factory(**kwargs):
        app = FakeTypedPortHfss(mismatch_kind="wave_port", **kwargs)
        apps.append(app)
        return app

    backend = LiveAedtBackend(desktop_factory=FakeDesktop, hfss_factory=factory)
    target = AedtTarget("pid", 42)
    preview = backend.execute(
        target,
        "hfss_boundary_preview",
        {
            "project_name": "Board",
            "design_name": "HFSS1",
            "boundary_kind": "wave_port",
            "boundary_name": "BadWave",
            "assignment_face_ids": [101],
            "options": {"modes": 2, "integration_line_direction": "YNeg"},
        },
    )
    with pytest.raises(LiveBackendError, match="mode count readback failed"):
        backend.execute(
            target,
            "hfss_boundary_apply",
            {"preview_id": preview["preview_id"]},
        )
    assert [item.name for item in apps[0].boundaries] == ["rad1"]
    assert apps[0].ports == ["P1", "P2"]


def test_backend_typed_hfss_port_rejects_unsafe_and_stale_requests():
    apps = []

    def factory(**kwargs):
        app = FakeTypedPortHfss(**kwargs)
        apps.append(app)
        return app

    backend = LiveAedtBackend(desktop_factory=FakeDesktop, hfss_factory=factory)
    target = AedtTarget("pid", 42)
    base = {
        "project_name": "Board",
        "design_name": "HFSS1",
        "boundary_name": "UnsafePort",
    }
    unsafe = [
        (
            {
                **base,
                "boundary_kind": "lumped_port",
                "assignment_face_ids": [101],
            },
            "requires one assignment_object_name",
        ),
        (
            {
                **base,
                "boundary_kind": "wave_port",
                "assignment_object_name": "PortSheet",
            },
            "requires assignment_face_ids",
        ),
        (
            {
                **base,
                "boundary_kind": "lumped_port",
                "assignment_object_name": "box1",
            },
            "planar sheet object",
        ),
        (
            {
                **base,
                "boundary_kind": "wave_port",
                "assignment_face_ids": [101],
                "references": ["box1"],
            },
            "does not accept references",
        ),
        (
            {
                **base,
                "boundary_kind": "wave_port",
                "assignment_face_ids": [101],
                "options": {"integration_line_direction": "Diagonal"},
            },
            "integration_line_direction",
        ),
    ]
    for request, error in unsafe:
        with pytest.raises(LiveBackendError, match=error):
            backend.execute(target, "hfss_boundary_preview", request)

    apps[0].solution_type = "DrivenTerminal"
    with pytest.raises(LiveBackendError, match="requires a DrivenModal solution"):
        backend.execute(
            target,
            "hfss_boundary_preview",
            {
                **base,
                "boundary_kind": "wave_port",
                "assignment_face_ids": [101],
            },
        )
    apps[0].solution_type = "DrivenModal"
    stale = backend.execute(
        target,
        "hfss_boundary_preview",
        {
            **base,
            "boundary_kind": "wave_port",
            "assignment_face_ids": [101],
        },
    )
    apps[0].boundaries.append(
        FakeBoundary(apps[0], "ExternalPort", "Wave Port", port=True, props={"Faces": [201]})
    )
    with pytest.raises(LiveBackendError, match="stale HFSS boundary preview"):
        backend.execute(
            target,
            "hfss_boundary_apply",
            {"preview_id": stale["preview_id"]},
        )


def test_backend_atomically_creates_hfss_geometry_and_boundaries_with_readback():
    apps = []

    def factory(**kwargs):
        app = FakeGeometryHfss(**kwargs)
        apps.append(app)
        return app

    backend = LiveAedtBackend(desktop_factory=FakeDesktop, hfss_factory=factory)
    target = AedtTarget("pid", 42)
    preview = backend.execute(
        target,
        "hfss_geometry_boundary_create_preview",
        {
            "project_name": "Board",
            "design_name": "HFSS1",
            "primitives": [
                {
                    "kind": "box",
                    "name": "AtomicBody",
                    "origin": [0, 0, 0],
                    "size": [10, 5, 1],
                    "material": "vacuum",
                },
                {
                    "kind": "region",
                    "name": "AtomicRegion",
                    "padding": [5] * 6,
                    "padding_type": "Absolute Offset",
                },
            ],
            "boundaries": [
                {
                    "boundary_kind": "wave_port",
                    "boundary_name": "AtomicPort",
                    "assignment_object": "AtomicBody",
                    "face_selector": "x_min",
                },
                {
                    "boundary_kind": "radiation",
                    "boundary_name": "AtomicRadiation",
                    "assignment_object": "AtomicRegion",
                    "face_selector": "all_faces",
                },
            ],
            "max_new_objects": 2,
            "max_new_boundaries": 2,
        },
    )
    assert preview["project_dirty"] is False
    assert preview["requested_object_names"] == ["AtomicBody", "AtomicRegion"]
    assert preview["requested_boundary_names"] == ["AtomicPort", "AtomicRadiation"]

    result = backend.execute(
        target,
        "hfss_geometry_boundary_create_apply",
        {"preview_id": preview["preview_id"]},
    )
    assert result["status"] == "verified"
    assert result["created_object_names"] == ["AtomicBody", "AtomicRegion"]
    assert result["created_boundary_names"] == ["AtomicPort", "AtomicRadiation"]
    assert result["created_object_count"] == 2
    assert result["created_boundary_count"] == 2
    assert result["resolved_boundaries"][0]["face_selector"] == "x_min"
    assert len(result["resolved_boundaries"][0]["assignment_face_ids"]) == 1
    assert len(result["resolved_boundaries"][1]["assignment_face_ids"]) == 6
    assert result["atomic_geometry_boundary_transaction"] is True
    assert result["automatic_rollback_on_failure"] is True
    assert result["project_saved"] is False
    assert {item.name for item in apps[0].boundaries} >= {
        "AtomicPort",
        "AtomicRadiation",
    }


def test_backend_atomic_hfss_geometry_boundary_rolls_back_and_rejects_stale_preview():
    apps = []

    def factory(**kwargs):
        app = FakeGeometryHfss(boundary_fail_on="BadPort", **kwargs)
        apps.append(app)
        return app

    backend = LiveAedtBackend(desktop_factory=FakeDesktop, hfss_factory=factory)
    target = AedtTarget("pid", 42)
    request = {
        "project_name": "Board",
        "design_name": "HFSS1",
        "primitives": [
            {
                "kind": "box",
                "name": "AtomicBody",
                "origin": [0, 0, 0],
                "size": [10, 5, 1],
            }
        ],
        "boundaries": [
            {
                "boundary_kind": "radiation",
                "boundary_name": "TemporaryRadiation",
                "assignment_object": "AtomicBody",
                "face_selector": "x_max",
            },
            {
                "boundary_kind": "wave_port",
                "boundary_name": "BadPort",
                "assignment_object": "AtomicBody",
                "face_selector": "x_min",
            },
        ],
    }
    preview = backend.execute(target, "hfss_geometry_boundary_create_preview", request)
    with pytest.raises(LiveBackendError, match="synthetic boundary failure"):
        backend.execute(
            target,
            "hfss_geometry_boundary_create_apply",
            {"preview_id": preview["preview_id"]},
        )
    assert apps[0].modeler.object_names == ["box1"]
    assert {item.name for item in apps[0].boundaries} == {"rad1"}
    assert apps[0].ports == ["P1", "P2"]

    apps[0].boundary_fail_on = ""
    stale = backend.execute(target, "hfss_geometry_boundary_create_preview", request)
    apps[0].modeler._create("box", "External", "vacuum", ())
    with pytest.raises(LiveBackendError, match="stale HFSS geometry and boundary"):
        backend.execute(
            target,
            "hfss_geometry_boundary_create_apply",
            {"preview_id": stale["preview_id"]},
        )


@pytest.mark.parametrize(
    "boundaries,error",
    [
        ([], "non-empty list"),
        (
            [
                {
                    "boundary_kind": "wave_port",
                    "boundary_name": "P3",
                    "assignment_object": "ExistingObject",
                    "face_selector": "only_face",
                }
            ],
            "must name an object in this atomic batch",
        ),
        (
            [
                {
                    "boundary_kind": "wave_port",
                    "boundary_name": "P3",
                    "assignment_object": "AtomicSheet",
                    "face_selector": "all_faces",
                }
            ],
            "requires a selector that resolves to one face",
        ),
        (
            [
                {
                    "boundary_kind": "radiation",
                    "boundary_name": "rad1",
                    "assignment_object": "AtomicSheet",
                    "face_selector": "only_face",
                }
            ],
            "already exists",
        ),
        (
            [
                {
                    "boundary_kind": "wave_port",
                    "boundary_name": "P3",
                    "assignment_object": "AtomicSheet",
                    "face_selector": "only_face",
                    "options": {"modes": 0},
                }
            ],
            "modes must be an integer between 1 and 16",
        ),
        (
            [
                {
                    "boundary_kind": "lumped_port",
                    "boundary_name": "P3",
                    "assignment_object": "AtomicSheet",
                    "face_selector": "only_face",
                    "options": {"deembed": 1},
                }
            ],
            "deembed must be boolean for lumped_port",
        ),
    ],
)
def test_backend_atomic_hfss_geometry_boundary_preview_rejects_unsafe_requests(
    boundaries,
    error,
):
    backend = LiveAedtBackend(desktop_factory=FakeDesktop, hfss_factory=FakeGeometryHfss)
    with pytest.raises(LiveBackendError, match=error):
        backend.execute(
            AedtTarget("pid", 42),
            "hfss_geometry_boundary_create_preview",
            {
                "project_name": "Board",
                "design_name": "HFSS1",
                "primitives": [
                    {
                        "kind": "rectangle",
                        "name": "AtomicSheet",
                        "orientation": "YZ",
                        "origin": [0, 0, 0],
                        "size": [1, 1],
                    }
                ],
                "boundaries": boundaries,
            },
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
                    "kind": "rectangle",
                    "name": "R",
                    "orientation": "XY",
                    "origin": [0, 0, 0],
                    "size": [1, 1],
                    "solve_inside": True,
                }
            ],
            "unsupported rectangle field: solve_inside",
        ),
        (
            [
                {
                    "kind": "rectangle",
                    "name": "R",
                    "orientation": "XY",
                    "origin": [0, 0, 0],
                    "size": [1, 1],
                    "material": "copper",
                }
            ],
            "unsupported rectangle field: material",
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


def test_variable_batch_upsert_is_ordered_atomic_and_verifies_every_expression():
    layout = FakeLayout(project="Board", design="Layout1")
    backend = LiveAedtBackend(desktop_factory=FakeDesktop, layout_factory=lambda **kwargs: layout)
    target = AedtTarget("pid", 42)
    preview = backend.execute(
        target,
        "variable_batch_upsert_preview",
        {
            "product": "layout",
            "project_name": "Board",
            "design_name": "Layout1",
            "variables": [
                {"name": "$pitch", "expression": "1.5mm"},
                {"name": "W_main", "expression": "4.3mil"},
                {"name": "W_double", "expression": "2*W_main"},
            ],
        },
    )

    assert preview["requested_count"] == 3
    assert preview["create_count"] == 2
    assert preview["update_count"] == 1
    result = backend.execute(
        target,
        "variable_batch_upsert_apply",
        {"preview_id": preview["preview_id"]},
    )

    assert result["status"] == "verified"
    assert result["change_count"] == 3
    assert [item["name"] for item in result["changes"]] == [
        "$pitch",
        "W_main",
        "W_double",
    ]
    assert layout.variable_manager.variables == {
        "$pitch": "1.5mm",
        "W_main": "4.3mil",
        "W_double": "2*W_main",
    }
    assert result["automatic_rollback_on_failure"] is True
    assert result["project_saved"] is False


def test_variable_batch_rejects_noop_case_collision_controls_and_wrong_product():
    layout = FakeLayout(project="Board", design="Layout1")
    backend = LiveAedtBackend(
        desktop_factory=FakeDesktop,
        hfss_factory=lambda **kwargs: layout,
        layout_factory=lambda **kwargs: layout,
    )
    target = AedtTarget("pid", 42)
    base = {
        "product": "layout",
        "project_name": "Board",
        "design_name": "Layout1",
    }

    with pytest.raises(LiveBackendError, match="would make no changes"):
        backend.execute(
            target,
            "variable_batch_upsert_preview",
            base | {"variables": [{"name": "$pitch", "expression": "1.0mm"}]},
        )
    with pytest.raises(LiveBackendError, match="differs only by case"):
        backend.execute(
            target,
            "variable_batch_upsert_preview",
            base | {"variables": [{"name": "$PITCH", "expression": "2mm"}]},
        )
    with pytest.raises(LiveBackendError, match="control characters"):
        backend.execute(
            target,
            "variable_batch_upsert_preview",
            base | {"variables": [{"name": "W_bad", "expression": "1mm\n2mm"}]},
        )
    with pytest.raises(LiveBackendError, match="duplicate case-insensitive name"):
        backend.execute(
            target,
            "variable_batch_upsert_preview",
            base
            | {
                "variables": [
                    {"name": "W", "expression": "1mm"},
                    {"name": "w", "expression": "2mm"},
                ]
            },
        )
    with pytest.raises(LiveBackendError, match="does not match active design type"):
        backend.execute(
            target,
            "variable_batch_upsert_preview",
            base | {"product": "hfss", "variables": [{"name": "W", "expression": "1mm"}]},
        )


def test_variable_batch_rejects_unrelated_stale_change_and_rolls_back_partial_write():
    layout = FakeLayout(project="Board", design="Layout1")
    backend = LiveAedtBackend(desktop_factory=FakeDesktop, layout_factory=lambda **kwargs: layout)
    target = AedtTarget("pid", 42)
    request = {
        "product": "layout",
        "project_name": "Board",
        "design_name": "Layout1",
        "variables": [
            {"name": "$pitch", "expression": "1.5mm"},
            {"name": "W_bad", "expression": "4.3mil"},
        ],
    }
    stale = backend.execute(target, "variable_batch_upsert_preview", request)
    layout.variable_manager.variables["External"] = "9mm"
    with pytest.raises(LiveBackendError, match="stale variable batch preview"):
        backend.execute(
            target,
            "variable_batch_upsert_apply",
            {"preview_id": stale["preview_id"]},
        )
    del layout.variable_manager.variables["External"]

    failed = backend.execute(target, "variable_batch_upsert_preview", request)

    def faulty_set(name, value, sweep=True):
        layout.variable_manager.variables[name] = "wrong" if name == "W_bad" else value
        return True

    layout.variable_manager.set_variable = faulty_set
    with pytest.raises(LiveBackendError, match="readback verification failed: W_bad"):
        backend.execute(
            target,
            "variable_batch_upsert_apply",
            {"preview_id": failed["preview_id"]},
        )
    assert set(layout.variable_manager.variables) == {"$pitch"}
    restored = layout.variable_manager.variables["$pitch"]
    assert getattr(restored, "expression", restored) == "1mm"


def test_variable_batch_rejects_changes_while_simulation_is_running():
    layout = FakeLayout(project="Board", design="Layout1")
    layout.are_there_simulations_running = True
    backend = LiveAedtBackend(desktop_factory=FakeDesktop, layout_factory=lambda **kwargs: layout)
    with pytest.raises(LiveBackendError, match="simulation is running"):
        backend.execute(
            AedtTarget("pid", 42),
            "variable_batch_upsert_preview",
            {
                "product": "layout",
                "project_name": "Board",
                "design_name": "Layout1",
                "variables": [{"name": "W", "expression": "1mm"}],
            },
        )


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


def test_manager_requires_action_bound_one_use_approval_for_hfss_geometry_move():
    registry = FakeRegistry()
    authority = HmacApprovalAuthority("m" * 32)
    manager = LiveAedtSessionManager(registry=registry, approval_verifier=authority)
    session_id = manager.attach(pid=42)["live_session_id"]
    preview = manager.preview_hfss_geometry_move(
        session_id,
        project_name="Board",
        design_name="HFSS1",
        moves=[{"name": "Box1", "vector": [1, 2, 3]}],
    )
    token = authority.issue(**preview["approval_request"])
    result = manager.apply_hfss_geometry_move(
        session_id,
        preview_id=preview["preview_id"],
        approval_token=token,
    )
    assert result["command"] == "hfss_geometry_move_apply"
    with pytest.raises(Exception) as replay:
        manager.apply_hfss_geometry_move(
            session_id,
            preview_id=preview["preview_id"],
            approval_token=token,
        )
    assert getattr(replay.value, "code", None) == "approval_required"


def test_manager_requires_action_bound_one_use_approval_for_hfss_geometry_rotate():
    registry = FakeRegistry()
    authority = HmacApprovalAuthority("r" * 32)
    manager = LiveAedtSessionManager(registry=registry, approval_verifier=authority)
    session_id = manager.attach(pid=42)["live_session_id"]
    preview = manager.preview_hfss_geometry_rotate(
        session_id,
        project_name="Board",
        design_name="HFSS1",
        rotations=[{"name": "Box1", "axis": "Z", "angle_degrees": 90}],
    )
    token = authority.issue(**preview["approval_request"])
    result = manager.apply_hfss_geometry_rotate(
        session_id,
        preview_id=preview["preview_id"],
        approval_token=token,
    )
    assert result["command"] == "hfss_geometry_rotate_apply"
    with pytest.raises(Exception) as replay:
        manager.apply_hfss_geometry_rotate(
            session_id,
            preview_id=preview["preview_id"],
            approval_token=token,
        )
    assert getattr(replay.value, "code", None) == "approval_required"


def test_manager_requires_action_bound_one_use_approval_for_atomic_hfss_geometry_boundary():
    registry = FakeRegistry()
    authority = HmacApprovalAuthority("a" * 32)
    manager = LiveAedtSessionManager(registry=registry, approval_verifier=authority)
    session_id = manager.attach(pid=42)["live_session_id"]
    preview = manager.preview_hfss_geometry_boundary_create(
        session_id,
        project_name="Board",
        design_name="HFSS1",
        primitives=[
            {
                "kind": "rectangle",
                "name": "PortSheet",
                "orientation": "YZ",
                "origin": [0, 0, 0],
                "size": [1, 1],
            }
        ],
        boundaries=[
            {
                "boundary_kind": "wave_port",
                "boundary_name": "P3",
                "assignment_object": "PortSheet",
                "face_selector": "only_face",
            }
        ],
    )
    token = authority.issue(**preview["approval_request"])
    result = manager.apply_hfss_geometry_boundary_create(
        session_id,
        preview_id=preview["preview_id"],
        approval_token=token,
    )
    assert result["command"] == "hfss_geometry_boundary_create_apply"
    with pytest.raises(Exception) as replay:
        manager.apply_hfss_geometry_boundary_create(
            session_id,
            preview_id=preview["preview_id"],
            approval_token=token,
        )
    assert getattr(replay.value, "code", None) == "approval_required"


def test_manager_requires_action_bound_one_use_approval_for_atomic_hfss_setup_sweep():
    registry = FakeRegistry()
    authority = HmacApprovalAuthority("u" * 32)
    manager = LiveAedtSessionManager(registry=registry, approval_verifier=authority)
    session_id = manager.attach(pid=42)["live_session_id"]
    preview = manager.preview_hfss_setup_sweep_create(
        session_id,
        project_name="Board",
        design_name="HFSS1",
        setup={
            "name": "AtomicSetup",
            "type": "HFSSDriven",
            "properties": {"Frequency": "10GHz"},
        },
        sweep={
            "name": "AtomicSweep",
            "start_frequency": 1,
            "stop_frequency": 20,
            "count": 201,
        },
    )
    token = authority.issue(**preview["approval_request"])
    result = manager.apply_hfss_setup_sweep_create(
        session_id,
        preview_id=preview["preview_id"],
        approval_token=token,
    )
    assert result["command"] == "hfss_setup_sweep_create_apply"
    with pytest.raises(Exception) as replay:
        manager.apply_hfss_setup_sweep_create(
            session_id,
            preview_id=preview["preview_id"],
            approval_token=token,
        )
    assert getattr(replay.value, "code", None) == "approval_required"


def test_manager_requires_action_bound_one_use_approval_for_hfss_material_assignment():
    registry = FakeRegistry()
    authority = HmacApprovalAuthority("m" * 32)
    manager = LiveAedtSessionManager(registry=registry, approval_verifier=authority)
    session_id = manager.attach(pid=42)["live_session_id"]
    preview = manager.preview_hfss_material_assign(
        session_id,
        project_name="Board",
        design_name="HFSS1",
        object_names=["box1", "box2"],
        material_name="copper",
        max_objects=4,
    )
    assert preview["approval_request"]["action"] == "hfss.material.assign"
    token = authority.issue(**preview["approval_request"])
    result = manager.apply_hfss_material_assign(
        session_id,
        preview_id=preview["preview_id"],
        approval_token=token,
    )
    assert result["command"] == "hfss_material_assign_apply"
    with pytest.raises(Exception) as replay:
        manager.apply_hfss_material_assign(
            session_id,
            preview_id=preview["preview_id"],
            approval_token=token,
        )
    assert getattr(replay.value, "code", None) == "approval_required"


def test_manager_requires_action_bound_one_use_approval_for_hfss_material_create():
    registry = FakeRegistry()
    authority = HmacApprovalAuthority("c" * 32)
    manager = LiveAedtSessionManager(registry=registry, approval_verifier=authority)
    session_id = manager.attach(pid=42)["live_session_id"]
    preview = manager.preview_hfss_material_create(
        session_id,
        project_name="Board",
        design_name="HFSS1",
        material_name="HarnessLaminate",
        permittivity=4.2,
        dielectric_loss_tangent=0.018,
        appearance=[10, 20, 30, 0.4],
    )
    assert preview["approval_request"]["action"] == "hfss.material.create"
    token = authority.issue(**preview["approval_request"])
    result = manager.apply_hfss_material_create(
        session_id,
        preview_id=preview["preview_id"],
        approval_token=token,
    )
    assert result["command"] == "hfss_material_create_apply"
    with pytest.raises(Exception) as replay:
        manager.apply_hfss_material_create(
            session_id,
            preview_id=preview["preview_id"],
            approval_token=token,
        )
    assert getattr(replay.value, "code", None) == "approval_required"


def test_manager_requires_action_bound_one_use_approval_for_hfss_material_update():
    registry = FakeRegistry()
    authority = HmacApprovalAuthority("u" * 32)
    manager = LiveAedtSessionManager(registry=registry, approval_verifier=authority)
    session_id = manager.attach(pid=42)["live_session_id"]
    preview = manager.preview_hfss_material_update(
        session_id,
        project_name="Board",
        design_name="HFSS1",
        updates=[{"material_name": "HarnessLaminate", "permittivity": 4.4}],
        max_materials=4,
    )
    assert preview["approval_request"]["action"] == "hfss.material.update"
    token = authority.issue(**preview["approval_request"])
    result = manager.apply_hfss_material_update(
        session_id,
        preview_id=preview["preview_id"],
        approval_token=token,
    )
    assert result["command"] == "hfss_material_update_apply"
    with pytest.raises(Exception) as replay:
        manager.apply_hfss_material_update(
            session_id,
            preview_id=preview["preview_id"],
            approval_token=token,
        )
    assert getattr(replay.value, "code", None) == "approval_required"


def test_manager_requires_action_bound_one_use_approval_for_hfss_material_delete():
    registry = FakeRegistry()
    authority = HmacApprovalAuthority("d" * 32)
    manager = LiveAedtSessionManager(registry=registry, approval_verifier=authority)
    session_id = manager.attach(pid=42)["live_session_id"]
    preview = manager.preview_hfss_material_delete(
        session_id,
        project_name="Board",
        design_name="HFSS1",
        names=["HarnessUnusedA", "HarnessUnusedB"],
        max_materials=4,
    )
    assert preview["approval_request"]["action"] == "hfss.material.delete"
    token = authority.issue(**preview["approval_request"])
    result = manager.apply_hfss_material_delete(
        session_id,
        preview_id=preview["preview_id"],
        approval_token=token,
    )
    assert result["command"] == "hfss_material_delete_apply"
    with pytest.raises(Exception) as replay:
        manager.apply_hfss_material_delete(
            session_id,
            preview_id=preview["preview_id"],
            approval_token=token,
        )
    assert getattr(replay.value, "code", None) == "approval_required"


def test_manager_requires_action_bound_one_use_approval_for_layout_material_create_assign():
    registry = FakeRegistry()
    authority = HmacApprovalAuthority("l" * 32)
    manager = LiveAedtSessionManager(registry=registry, approval_verifier=authority)
    session_id = manager.attach(pid=42)["live_session_id"]
    preview = manager.preview_layout_material_create_assign(
        session_id,
        project_name="Board",
        design_name="Layout1",
        material_name="HarnessLaminate",
        layer_name="D1",
        assignment_field="material",
        permittivity=4.2,
        dielectric_loss_tangent=0.018,
    )
    assert preview["approval_request"]["action"] == (
        "layout.material.create_and_assign"
    )
    token = authority.issue(**preview["approval_request"])
    result = manager.apply_layout_material_create_assign(
        session_id,
        preview_id=preview["preview_id"],
        approval_token=token,
    )
    assert result["command"] == "layout_material_create_assign_apply"
    with pytest.raises(Exception) as replay:
        manager.apply_layout_material_create_assign(
            session_id,
            preview_id=preview["preview_id"],
            approval_token=token,
        )
    assert getattr(replay.value, "code", None) == "approval_required"


def test_manager_requires_action_bound_one_use_approval_for_layout_via_create():
    registry = FakeRegistry()
    authority = HmacApprovalAuthority("v" * 32)
    manager = LiveAedtSessionManager(registry=registry, approval_verifier=authority)
    session_id = manager.attach(pid=42)["live_session_id"]
    preview = manager.preview_layout_via_create(
        session_id,
        project_name="Board",
        design_name="Layout1",
        vias=_layout_via_create_request()["vias"],
        max_vias=4,
    )
    assert preview["approval_request"]["action"] == "layout.vias.create"
    token = authority.issue(**preview["approval_request"])
    result = manager.apply_layout_via_create(
        session_id,
        preview_id=preview["preview_id"],
        approval_token=token,
    )
    assert result["command"] == "layout_via_create_apply"
    with pytest.raises(Exception) as replay:
        manager.apply_layout_via_create(
            session_id,
            preview_id=preview["preview_id"],
            approval_token=token,
        )
    assert getattr(replay.value, "code", None) == "approval_required"


def test_manager_requires_action_bound_one_use_approval_for_layout_via_update():
    registry = FakeRegistry()
    authority = HmacApprovalAuthority("u" * 32)
    manager = LiveAedtSessionManager(registry=registry, approval_verifier=authority)
    session_id = manager.attach(pid=42)["live_session_id"]
    preview = manager.preview_layout_via_update(
        session_id,
        project_name="Board",
        design_name="Layout1",
        updates=_layout_via_update_request()["updates"],
        max_vias=4,
    )
    assert preview["approval_request"]["action"] == "layout.vias.update"
    token = authority.issue(**preview["approval_request"])
    result = manager.apply_layout_via_update(
        session_id,
        preview_id=preview["preview_id"],
        approval_token=token,
    )
    assert result["command"] == "layout_via_update_apply"
    with pytest.raises(Exception) as replay:
        manager.apply_layout_via_update(
            session_id,
            preview_id=preview["preview_id"],
            approval_token=token,
        )
    assert getattr(replay.value, "code", None) == "approval_required"


def test_manager_requires_action_bound_one_use_approval_for_layout_via_delete():
    registry = FakeRegistry()
    authority = HmacApprovalAuthority("d" * 32)
    manager = LiveAedtSessionManager(registry=registry, approval_verifier=authority)
    session_id = manager.attach(pid=42)["live_session_id"]
    preview = manager.preview_layout_via_delete(
        session_id,
        project_name="Board",
        design_name="Layout1",
        names=["V1", "V2"],
        max_vias=4,
    )
    assert preview["approval_request"]["action"] == "layout.vias.delete"
    token = authority.issue(**preview["approval_request"])
    result = manager.apply_layout_via_delete(
        session_id,
        preview_id=preview["preview_id"],
        approval_token=token,
    )
    assert result["command"] == "layout_via_delete_apply"
    with pytest.raises(Exception) as replay:
        manager.apply_layout_via_delete(
            session_id,
            preview_id=preview["preview_id"],
            approval_token=token,
        )
    assert getattr(replay.value, "code", None) == "approval_required"


def test_manager_requires_one_use_approvals_for_both_antipad_harnesses():
    registry = FakeRegistry()
    authority = HmacApprovalAuthority("a" * 32)
    manager = LiveAedtSessionManager(registry=registry, approval_verifier=authority)
    session_id = manager.attach(pid=42)["live_session_id"]

    layout = manager.preview_layout_antipad_circle_create(
        session_id,
        project_name="Board",
        design_name="Layout1",
        voids=[
            {
                "name": "AP1",
                "owner_name": "GND_PLANE",
                "center": [0.0, 0.0],
                "radius": 0.8,
            }
        ],
    )
    assert layout["approval_request"]["action"] == "layout.antipad.circle.create"
    layout_token = authority.issue(**layout["approval_request"])
    result = manager.apply_layout_antipad_circle_create(
        session_id,
        preview_id=layout["preview_id"],
        approval_token=layout_token,
    )
    assert result["command"] == "layout_antipad_circle_create_apply"
    with pytest.raises(Exception):
        manager.apply_layout_antipad_circle_create(
            session_id,
            preview_id=layout["preview_id"],
            approval_token=layout_token,
        )

    hfss = manager.preview_hfss_antipad_subtract(
        session_id,
        project_name="Board",
        design_name="HFSS1",
        blank_object_name="L2_GND",
        tool_name="__AP_TOOL",
        center=[0.0, 0.0],
        radius=0.8,
    )
    assert hfss["approval_request"]["action"] == "hfss.antipad.subtract"
    hfss_token = authority.issue(**hfss["approval_request"])
    result = manager.apply_hfss_antipad_subtract(
        session_id,
        preview_id=hfss["preview_id"],
        approval_token=hfss_token,
    )
    assert result["command"] == "hfss_antipad_subtract_apply"
    with pytest.raises(Exception):
        manager.apply_hfss_antipad_subtract(
            session_id,
            preview_id=hfss["preview_id"],
            approval_token=hfss_token,
        )


def test_manager_requires_action_bound_one_use_approval_for_hfss_length_mesh_create():
    registry = FakeRegistry()
    authority = HmacApprovalAuthority("h" * 32)
    manager = LiveAedtSessionManager(registry=registry, approval_verifier=authority)
    session_id = manager.attach(pid=42)["live_session_id"]
    preview = manager.preview_hfss_length_mesh_create(
        session_id,
        project_name="Board",
        design_name="HFSS1",
        mesh_name="HarnessLength",
        object_names=["box1", "box2"],
        inside_selection=True,
        maximum_length="0.4mm",
        maximum_elements=500,
        max_objects=4,
    )
    assert preview["approval_request"]["action"] == "hfss.mesh.length.create"
    token = authority.issue(**preview["approval_request"])
    result = manager.apply_hfss_length_mesh_create(
        session_id,
        preview_id=preview["preview_id"],
        approval_token=token,
    )
    assert result["command"] == "hfss_length_mesh_create_apply"
    with pytest.raises(Exception) as replay:
        manager.apply_hfss_length_mesh_create(
            session_id,
            preview_id=preview["preview_id"],
            approval_token=token,
        )
    assert getattr(replay.value, "code", None) == "approval_required"


def test_manager_requires_action_bound_one_use_approval_for_hfss_infinite_sphere():
    registry = FakeRegistry()
    authority = HmacApprovalAuthority("f" * 32)
    manager = LiveAedtSessionManager(registry=registry, approval_verifier=authority)
    session_id = manager.attach(pid=42)["live_session_id"]
    preview = manager.preview_hfss_infinite_sphere_create(
        session_id,
        project_name="Board",
        design_name="HFSS1",
        sphere_name="HarnessSphere",
        definition="Theta-Phi",
        angle1_start=-90,
        angle1_stop=90,
        angle1_step=5,
        angle2_start=0,
        angle2_stop=360,
        angle2_step=10,
        polarization="Slant",
        polarization_angle=45,
        max_samples=5000,
    )
    assert (
        preview["approval_request"]["action"]
        == "hfss.far_field.infinite_sphere.create"
    )
    token = authority.issue(**preview["approval_request"])
    result = manager.apply_hfss_infinite_sphere_create(
        session_id,
        preview_id=preview["preview_id"],
        approval_token=token,
    )
    assert result["command"] == "hfss_infinite_sphere_create_apply"
    with pytest.raises(Exception) as replay:
        manager.apply_hfss_infinite_sphere_create(
            session_id,
            preview_id=preview["preview_id"],
            approval_token=token,
        )
    assert getattr(replay.value, "code", None) == "approval_required"


def test_manager_requires_action_bound_one_use_approval_for_hfss_surface_boundary():
    registry = FakeRegistry()
    authority = HmacApprovalAuthority("s" * 32)
    manager = LiveAedtSessionManager(registry=registry, approval_verifier=authority)
    session_id = manager.attach(pid=42)["live_session_id"]
    preview = manager.preview_hfss_surface_boundary_create(
        session_id,
        project_name="Board",
        design_name="HFSS1",
        boundary_kind="finite_conductivity",
        boundary_name="HarnessFinite",
        face_ids=[101],
        options={
            "material_name": "copper",
            "use_thickness": True,
            "thickness": "35um",
            "roughness": "0.5um",
        },
    )
    assert preview["approval_request"]["action"] == "hfss.surface_boundary.create"
    token = authority.issue(**preview["approval_request"])
    result = manager.apply_hfss_surface_boundary_create(
        session_id,
        preview_id=preview["preview_id"],
        approval_token=token,
    )
    assert result["command"] == "hfss_surface_boundary_create_apply"
    with pytest.raises(Exception) as replay:
        manager.apply_hfss_surface_boundary_create(
            session_id,
            preview_id=preview["preview_id"],
            approval_token=token,
        )
    assert getattr(replay.value, "code", None) == "approval_required"


def test_manager_requires_action_bound_one_use_approval_for_hfss_coordinate_system():
    registry = FakeRegistry()
    authority = HmacApprovalAuthority("c" * 32)
    manager = LiveAedtSessionManager(registry=registry, approval_verifier=authority)
    session_id = manager.attach(pid=42)["live_session_id"]
    preview = manager.preview_hfss_coordinate_system_create(
        session_id,
        project_name="Board",
        design_name="HFSS1",
        coordinate_system_name="HarnessCS",
        reference_coordinate_system="Global",
        origin=[0, 0, "OX"],
        x_axis=[1, 0, 0],
        y_axis=[0, 1, 0],
    )
    assert preview["approval_request"]["action"] == "hfss.coordinate_system.create"
    token = authority.issue(**preview["approval_request"])
    result = manager.apply_hfss_coordinate_system_create(
        session_id,
        preview_id=preview["preview_id"],
        approval_token=token,
    )
    assert result["command"] == "hfss_coordinate_system_create_apply"
    with pytest.raises(Exception) as replay:
        manager.apply_hfss_coordinate_system_create(
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
    assert "preview_live_hfss_geometry_move" in server.tools
    assert "apply_live_hfss_geometry_move" in server.tools
    assert "preview_live_hfss_geometry_rotate" in server.tools
    assert "apply_live_hfss_geometry_rotate" in server.tools
    assert "preview_live_hfss_geometry_boundary_create" in server.tools
    assert "apply_live_hfss_geometry_boundary_create" in server.tools
    assert "get_live_hfss_far_field_inventory" in server.tools
    assert "preview_live_hfss_infinite_sphere_create" in server.tools
    assert "apply_live_hfss_infinite_sphere_create" in server.tools
    assert "get_live_hfss_surface_boundary_inventory" in server.tools
    assert "preview_live_hfss_surface_boundary_create" in server.tools
    assert "apply_live_hfss_surface_boundary_create" in server.tools
    assert "get_live_hfss_coordinate_system_inventory" in server.tools
    assert "preview_live_hfss_coordinate_system_create" in server.tools
    assert "apply_live_hfss_coordinate_system_create" in server.tools
    assert "preview_live_hfss_setup_create" in server.tools
    assert "apply_live_hfss_setup_create" in server.tools
    assert "preview_live_hfss_setup_sweep_create" in server.tools
    assert "apply_live_hfss_setup_sweep_create" in server.tools
    assert "preview_live_hfss_material_create" in server.tools
    assert "apply_live_hfss_material_create" in server.tools
    assert "preview_live_hfss_material_update" in server.tools
    assert "apply_live_hfss_material_update" in server.tools
    assert "preview_live_hfss_material_delete" in server.tools
    assert "apply_live_hfss_material_delete" in server.tools
    assert "preview_live_layout_material_create_assign" in server.tools
    assert "apply_live_layout_material_create_assign" in server.tools
    assert "preview_live_layout_via_create" in server.tools
    assert "apply_live_layout_via_create" in server.tools
    assert "preview_live_layout_via_update" in server.tools
    assert "apply_live_layout_via_update" in server.tools
    assert "preview_live_layout_via_delete" in server.tools
    assert "apply_live_layout_via_delete" in server.tools
    assert "preview_live_hfss_material_assign" in server.tools
    assert "apply_live_hfss_material_assign" in server.tools
    assert "get_live_hfss_material_inventory" in server.tools
    assert "get_live_hfss_mesh_inventory" in server.tools
    assert "preview_live_hfss_length_mesh_create" in server.tools
    assert "apply_live_hfss_length_mesh_create" in server.tools
    assert "preview_live_hfss_report_create" in server.tools
    assert "apply_live_hfss_report_create" in server.tools
    assert "get_live_hfss_port_inventory" in server.tools
    assert "preview_live_hfss_boundary_create" in server.tools
    assert "apply_live_hfss_boundary_create" in server.tools
    assert "preview_live_aedt_variable_batch_upsert" in server.tools
    assert "apply_live_aedt_variable_batch_upsert" in server.tools
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
        "preview_live_hfss_geometry_move",
        "apply_live_hfss_geometry_move",
        "preview_live_hfss_geometry_rotate",
        "apply_live_hfss_geometry_rotate",
        "get_live_hfss_far_field_inventory",
        "preview_live_hfss_infinite_sphere_create",
        "apply_live_hfss_infinite_sphere_create",
        "get_live_hfss_surface_boundary_inventory",
        "preview_live_hfss_surface_boundary_create",
        "apply_live_hfss_surface_boundary_create",
        "get_live_hfss_coordinate_system_inventory",
        "preview_live_hfss_coordinate_system_create",
        "apply_live_hfss_coordinate_system_create",
        "preview_live_hfss_setup_create",
        "apply_live_hfss_setup_create",
        "preview_live_hfss_material_create",
        "apply_live_hfss_material_create",
        "preview_live_hfss_material_update",
        "apply_live_hfss_material_update",
        "preview_live_hfss_material_delete",
        "apply_live_hfss_material_delete",
        "preview_live_layout_material_create_assign",
        "apply_live_layout_material_create_assign",
        "preview_live_layout_via_create",
        "apply_live_layout_via_create",
        "preview_live_layout_via_update",
        "apply_live_layout_via_update",
        "preview_live_layout_via_delete",
        "apply_live_layout_via_delete",
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
    assert by_name["layout.via.target_inventory"]["tools"] == [
        "get_live_layout_object_property_inventory"
    ]
    assert by_name["layout.object.properties.schema"]["tools"] == [
        "get_live_layout_property_schema"
    ]
    assert by_name["layout.object.properties.read"]["tools"] == [
        "read_live_layout_properties"
    ]
    assert by_name["layout.path_width.parameterize"]["tools"] == [
        "preview_live_parameterize_path_width",
        "apply_live_parameterize_path_width",
    ]
    assert by_name["hfss.material.assign"]["tools"] == [
        "preview_live_hfss_material_assign",
        "apply_live_hfss_material_assign",
    ]
    assert by_name["hfss.material.create"]["tools"] == [
        "preview_live_hfss_material_create",
        "apply_live_hfss_material_create",
    ]
    assert by_name["hfss.material.update"]["tools"] == [
        "preview_live_hfss_material_update",
        "apply_live_hfss_material_update",
    ]
    assert by_name["hfss.material.delete"]["tools"] == [
        "preview_live_hfss_material_delete",
        "apply_live_hfss_material_delete",
    ]
    assert by_name["hfss.geometry.move"]["tools"] == [
        "preview_live_hfss_geometry_move",
        "apply_live_hfss_geometry_move",
    ]
    assert "typed_bounding_box_and_face_center_translation_verified" in by_name[
        "hfss.geometry.move"
    ]["postconditions"]
    assert by_name["hfss.geometry.rotate"]["tools"] == [
        "preview_live_hfss_geometry_rotate",
        "apply_live_hfss_geometry_rotate",
    ]
    assert "typed_face_center_and_vertex_rotation_verified" in by_name[
        "hfss.geometry.rotate"
    ]["postconditions"]
    assert by_name["layout.material.create_and_assign"]["tools"] == [
        "preview_live_layout_material_create_assign",
        "apply_live_layout_material_create_assign",
    ]
    assert "layer_restored_before_new_material_removal_on_failure" in by_name[
        "layout.material.create_and_assign"
    ]["postconditions"]
    assert by_name["layout.vias.create"]["tools"] == [
        "preview_live_layout_via_create",
        "apply_live_layout_via_create",
    ]
    assert "native_via_property_readback_verified" in by_name[
        "layout.vias.create"
    ]["postconditions"]
    assert by_name["layout.vias.update"]["tools"] == [
        "preview_live_layout_via_update",
        "apply_live_layout_via_update",
    ]
    assert "only_requested_native_via_properties_changed" in by_name[
        "layout.vias.update"
    ]["postconditions"]
    assert by_name["layout.vias.delete"]["tools"] == [
        "preview_live_layout_via_delete",
        "apply_live_layout_via_delete",
    ]
    assert "full_native_batch_reconstruction_on_failure" in by_name[
        "layout.vias.delete"
    ]["postconditions"]
    assert "typed_property_and_optional_appearance_readback_verified" in by_name[
        "hfss.material.create"
    ]["postconditions"]
    assert "material_references_and_solve_inside_preserved" in by_name[
        "hfss.material.update"
    ]["postconditions"]
    assert "native_material_definitions_reconstructed_on_failure" in by_name[
        "hfss.material.delete"
    ]["postconditions"]
    assert by_name["hfss.material.inventory"]["tools"] == [
        "get_live_hfss_material_inventory"
    ]
    assert by_name["hfss.mesh.inventory"]["tools"] == [
        "get_live_hfss_mesh_inventory"
    ]
    assert by_name["hfss.mesh.length.create"]["tools"] == [
        "preview_live_hfss_length_mesh_create",
        "apply_live_hfss_length_mesh_create",
    ]
    assert by_name["hfss.coordinate_system.inventory"]["tools"] == [
        "get_live_hfss_coordinate_system_inventory"
    ]
    assert by_name["hfss.coordinate_system.create"]["tools"] == [
        "preview_live_hfss_coordinate_system_create",
        "apply_live_hfss_coordinate_system_create",
    ]
    assert "prior_active_working_coordinate_system_restored" in by_name[
        "hfss.coordinate_system.create"
    ]["postconditions"]
    assert "existing_project_material_only" in by_name["hfss.material.assign"][
        "postconditions"
    ]
    assert by_name["hfss.results.export"]["products"] == ["hfss", "layout"]
    unavailable = {item["name"] for item in v2["unavailable_capabilities"]}
    assert {"aedt.sessions.list", "aedt.sessions.launch", "hfss.design.create"}.issubset(unavailable)
    assert "preselected AEDT port, project, and design" in server.instructions
