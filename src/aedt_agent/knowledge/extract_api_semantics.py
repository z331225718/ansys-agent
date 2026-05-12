from __future__ import annotations

import argparse
import ast
import json
from dataclasses import dataclass
from pathlib import Path


TOP_50_APIS = [
    "Hfss.modeler.create_box",
    "Hfss.assign_material",
    "Hfss.modeler.create_rectangle",
    "Hfss.wave_port",
    "Hfss.lumped_port",
    "Hfss.create_setup",
    "Hfss.modeler.create_region",
    "Hfss.create_open_region",
    "Hfss.assign_radiation_boundary_to_objects",
    "Hfss.assign_perfecte_to_sheets",
    "Hfss.modeler.get_object_faces",
    "Hfss.modeler.get_face_center",
    "Hfss.modeler.get_faceid_from_position",
    "Hfss.modeler.insert_3d_component",
    "Hfss.modeler.unite",
    "Hfss.modeler.subtract",
    "SetupHFSS.create_frequency_sweep",
    "SetupHFSS.create_linear_count_sweep",
    "Hfss.create_linear_count_sweep",
    "Hfss.export_touchstone",
    "Hfss.modeler.create_circle",
    "Hfss.modeler.create_cylinder",
    "Hfss.modeler.create_polyline",
    "Hfss.modeler.move",
    "Hfss.modeler.rotate",
    "Hfss.modeler.duplicate_along_line",
    "Hfss.modeler.create_sheet_to_ground",
    "Hfss.modeler.thicken_sheet",
    "Hfss.modeler.create_airbox",
    "Hfss.modeler.create_coaxial",
    "Hfss.modeler.create_waveguide",
    "Hfss.modeler.get_bounding_dimension",
    "Hfss.modeler.get_face_edges",
    "Hfss.modeler.get_edge_midpoint",
    "Hfss.modeler.get_vertices_of_line",
    "Hfss.modeler.get_object_from_name",
    "Hfss.modeler.change_region_padding",
    "Hfss.modeler.set_working_coordinate_system",
    "Hfss.modeler.create_coordinate_system",
    "Hfss.modeler.global_to_cs",
    "Hfss.modeler.value_in_object_units",
    "Hfss.modeler.create_object_from_face",
    "Hfss.modeler.create_group",
    "Hfss.modeler.create_face_coordinate_system",
    "Hfss.create_scattering",
    "Hfss.analyze",
    "Hfss.modeler.create_polyhedron",
    "Hfss.modeler.create_cone",
    "Hfss.modeler.create_ellipse",
    "Hfss.modeler.create_sphere",
    "Hfss.modeler.create_udp",
]


CATEGORY_BY_API = {
    "assign_material": "material",
    "wave_port": "excitation",
    "lumped_port": "excitation",
    "assign_radiation_boundary_to_objects": "boundary",
    "assign_perfecte_to_sheets": "boundary",
    "create_setup": "setup",
    "create_frequency_sweep": "setup",
    "create_linear_count_sweep": "setup",
    "export_touchstone": "postprocess",
    "create_scattering": "postprocess",
    "analyze": "postprocess",
}


@dataclass(frozen=True)
class ExtractedFunction:
    name: str
    signature: str
    docstring: str
    relative_source: str


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pyaedt-src", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--top-50", action="store_true")
    args = parser.parse_args()

    apis = TOP_50_APIS if args.top_50 else TOP_50_APIS[:5]
    extracted = _index_functions(args.pyaedt_src)
    records = [_build_record(api, extracted) for api in apis]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _index_functions(source_root: Path) -> dict[str, list[ExtractedFunction]]:
    items: dict[str, list[ExtractedFunction]] = {}
    for path in source_root.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                signature = _build_signature(node)
                docstring = (ast.get_docstring(node) or "").strip().splitlines()[0] if ast.get_docstring(node) else ""
                relative = _relative_source_path(path)
                items.setdefault(node.name, []).append(
                    ExtractedFunction(
                        name=node.name,
                        signature=signature,
                        docstring=docstring,
                        relative_source=relative,
                    )
                )
    return items


def _build_record(api: str, extracted: dict[str, list[ExtractedFunction]]) -> dict:
    method_name = api.split(".")[-1]
    category = CATEGORY_BY_API.get(method_name, "geometry")
    match = _choose_best_match(api, extracted.get(method_name, []))
    if match:
        signature = match.signature
        docstring = match.docstring or f"{method_name.replace('_', ' ')}."
        source_ref = f"https://github.com/ansys/pyaedt/blob/main/{match.relative_source}"
        confidence = "documented"
    else:
        signature = f"{method_name}(...)"
        docstring = f"{method_name.replace('_', ' ')}."
        source_ref = "https://github.com/ansys/pyaedt"
        confidence = "template"

    return {
        "fqname": api,
        "domain": "hfss",
        "category": category,
        "signature": signature,
        "params_json": "[]",
        "returns_json": "{}",
        "docstring": docstring,
        "constraints_json": "[]",
        "common_errors_json": "[]",
        "common_traps_json": "[]",
        "examples_ref_json": "[]",
        "source_refs_json": json.dumps([source_ref]),
        "confidence": confidence,
        "pyaedt_version": "0.27.0",
        "aedt_version": "2026.1",
        "last_verified_at": "2026-05-11",
    }


def _choose_best_match(api: str, candidates: list[ExtractedFunction]) -> ExtractedFunction | None:
    if not candidates:
        return None
    preferences = []
    if api.startswith("Hfss.modeler."):
        preferences = ["primitives_3d.py", "primitives.py", "object_3d.py", "primitives_2d.py"]
    elif api.startswith("Hfss."):
        preferences = ["hfss.py", "analysis_3d.py"]
    elif api.startswith("SetupHFSS."):
        preferences = ["solve_setup.py"]
    for preferred in preferences:
        for item in candidates:
            if item.relative_source.endswith(preferred):
                return item
    return candidates[0]


def _build_signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    args = []
    positional = node.args.args[1:] if node.args.args and node.args.args[0].arg == "self" else node.args.args
    defaults = [None] * (len(positional) - len(node.args.defaults)) + list(node.args.defaults)
    for arg, default in zip(positional, defaults):
        text = arg.arg
        if arg.annotation is not None:
            text += f": {ast.unparse(arg.annotation)}"
        if default is not None:
            text += f" = {ast.unparse(default)}"
        args.append(text)
    if node.args.vararg:
        args.append(f"*{node.args.vararg.arg}")
    for arg, default in zip(node.args.kwonlyargs, node.args.kw_defaults):
        text = arg.arg
        if arg.annotation is not None:
            text += f": {ast.unparse(arg.annotation)}"
        if default is not None:
            text += f" = {ast.unparse(default)}"
        args.append(text)
    if node.args.kwarg:
        args.append(f"**{node.args.kwarg.arg}")
    return f"{node.name}({', '.join(args)})"


def _relative_source_path(path: Path) -> str:
    parts = path.parts
    try:
        index = parts.index("ansys")
    except ValueError:
        return path.name
    return "src/" + "/".join(parts[index:])


if __name__ == "__main__":
    main()
