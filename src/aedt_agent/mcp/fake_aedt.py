from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class FakeFace:
    id: int
    center: list[float]
    area: float = 1.0


@dataclass
class FakeObject:
    name: str
    object_type: str
    material_name: str = ""
    origin: list[Any] = field(default_factory=list)
    sizes: list[Any] = field(default_factory=list)
    faces: list[FakeFace] = field(default_factory=list)

    @property
    def bottom_face_z(self) -> int:
        return self.faces[0].id if self.faces else 0


class FakeModeler:
    def __init__(self, app: "FakeAedtApp"):
        self._app = app

    def create_box(self, origin, sizes, name=None, material=None, **kwargs) -> FakeObject:
        object_name = name or f"Box{len(self._app.objects) + 1}"
        faces = [FakeFace(self._app.next_face_id(), [0.0, 0.0, 0.0], area=float(index + 1)) for index in range(6)]
        obj = FakeObject(object_name, "box", material or "", list(origin), list(sizes), faces)
        self._app.objects[object_name] = obj
        return obj

    def create_rectangle(self, orientation, origin, sizes, name=None, material=None, **kwargs) -> FakeObject:
        object_name = name or f"Rectangle{len(self._app.objects) + 1}"
        face = FakeFace(self._app.next_face_id(), [0.0, 0.0, 0.0], area=1.0)
        obj = FakeObject(object_name, "rectangle", material or "", list(origin), list(sizes), [face])
        self._app.objects[object_name] = obj
        return obj

    def create_region(self, padding=10, name="Region", **kwargs) -> FakeObject:
        return self.create_box([-padding, -padding, -padding], [2 * padding, 2 * padding, 2 * padding], name=name, material="air")

    def get_object_faces(self, object_name: str) -> list[int]:
        return [face.id for face in self._app.objects[object_name].faces]

    def get_face_center(self, face_id: int) -> list[float]:
        for obj in self._app.objects.values():
            for face in obj.faces:
                if face.id == face_id:
                    return list(face.center)
        raise KeyError(f"face not found: {face_id}")


class FakeAedtApp:
    def __init__(self, project_id: str, design_id: str):
        self.project_id = project_id
        self.design_id = design_id
        self.project_name = project_id
        self.design_name = design_id
        self.solution_type = ""
        self.objects: dict[str, FakeObject] = {}
        self.ports: dict[str, dict[str, Any]] = {}
        self.boundaries: dict[str, dict[str, Any]] = {}
        self.setups: dict[str, dict[str, Any]] = {}
        self.sweeps: dict[str, dict[str, Any]] = {}
        self._face_index = 0
        self.modeler = FakeModeler(self)

    def next_face_id(self) -> int:
        self._face_index += 1
        return self._face_index

    def assign_material(self, assignment, material) -> bool:
        names = assignment if isinstance(assignment, list) else [assignment]
        for name in names:
            self.objects[name].material_name = material
        return True

    def assign_radiation_boundary_to_objects(self, assignment, name=None, **kwargs) -> str:
        boundary_name = name or f"Radiation{len(self.boundaries) + 1}"
        self.boundaries[boundary_name] = {"type": "radiation", "assignment": assignment}
        return boundary_name

    def assign_perfecte_to_sheets(self, assignment, name=None, **kwargs) -> str:
        boundary_name = name or f"PerfectE{len(self.boundaries) + 1}"
        self.boundaries[boundary_name] = {"type": "perfect_e", "assignment": assignment}
        return boundary_name

    def create_open_region(self, frequency=None, **kwargs) -> str:
        boundary_name = kwargs.get("name", "OpenRegion")
        self.boundaries[boundary_name] = {"type": "open_region", "frequency": frequency}
        return boundary_name

    def lumped_port(self, assignment, name=None, **kwargs) -> str:
        port_name = name or f"Port{len(self.ports) + 1}"
        self.ports[port_name] = {"type": "lumped", "assignment": assignment, "kwargs": kwargs}
        return port_name

    def wave_port(self, assignment, name=None, **kwargs) -> str:
        port_name = name or f"Port{len(self.ports) + 1}"
        self.ports[port_name] = {"type": "wave", "assignment": assignment, "kwargs": kwargs}
        return port_name

    def create_setup(self, name="Setup1", **kwargs) -> str:
        self.setups[name] = dict(kwargs)
        return name

    def create_linear_count_sweep(self, setup, units="GHz", start_frequency=1, stop_frequency=10, num_of_freq_points=101, **kwargs) -> str:
        sweep_name = kwargs.get("name", f"{setup}_Sweep")
        self.sweeps[sweep_name] = {
            "setup": setup,
            "units": units,
            "start_frequency": start_frequency,
            "stop_frequency": stop_frequency,
            "num_of_freq_points": num_of_freq_points,
        }
        return sweep_name


class FakeAedtAdapter:
    def __init__(self, project_id: str, design_id: str):
        self.app = FakeAedtApp(project_id, design_id)

    def health_check(self) -> bool:
        return True

    def execute_node_callable(self, fn: Callable[[FakeAedtApp], dict[str, Any] | None]) -> dict[str, Any]:
        return fn(self.app) or {}

    def snapshot_state(self) -> dict[str, Any]:
        return {
            "project_id": self.app.project_id,
            "design_id": self.app.design_id,
            "objects": {
                name: {
                    "type": obj.object_type,
                    "material": obj.material_name,
                    "origin": obj.origin,
                    "sizes": obj.sizes,
                    "faces": [face.id for face in obj.faces],
                }
                for name, obj in self.app.objects.items()
            },
            "ports": dict(self.app.ports),
            "boundaries": dict(self.app.boundaries),
            "setups": dict(self.app.setups),
            "sweeps": dict(self.app.sweeps),
        }

    def release(self) -> None:
        return None
