from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from aedt_agent.agent.orchestrator.runtime import AgentRuntime
from aedt_agent.infrastructure.sqlite_mission_store import SQLiteMissionStore
from aedt_agent.interactive.workflows import AssistantWorkflowManager


class _Live:
    def __init__(self) -> None:
        self.authorized: list[tuple[str, str, str]] = []
        self.analysis_statuses: list[dict] = []
        self.export_root: Path | None = None
        self.export_spec: dict = {}

    def workflow_binding(self, session_id: str) -> dict:
        assert session_id == "live-1"
        return {
            "version": "2024.2",
            "pid": 123,
            "port": 50051,
            "active_project": "demo",
            "active_design": "layout",
        }

    def register_guarded_preview(self, session_id: str, *, action: str, result: dict) -> dict:
        return {
            **result,
            "approval_source": "external_host_only",
            "approval_request": {"action": action},
        }

    def authorize_guarded_preview(
        self,
        session_id: str,
        *,
        action: str,
        preview_id: str,
        approval_token: str,
    ) -> None:
        assert approval_token == "approved"
        self.authorized.append((session_id, action, preview_id))

    def layout_routing_inventory(self, session_id: str, **kwargs) -> dict:
        return {
            "path_count": 2,
            "nets": ["N1"],
            "layers": ["L1"],
            "design_unchanged": True,
        }

    def layout_object_inventory(self, session_id: str, **kwargs) -> dict:
        return {"categories": {}, "unavailable_categories": [], "design_unchanged": True}

    def layout_technology_inventory(self, session_id: str, **kwargs) -> dict:
        return {
            "stackup": [{"name": "TOP"}, {"name": "D1"}, {"name": "BOTTOM"}],
            "padstacks": [{"name": "VIA"}],
            "ports": ["P1", "P2"],
            "differential_pairs": [],
            "counts": {
                "stackup_layers": 3,
                "padstacks": 1,
                "ports": 2,
                "differential_pairs": 0,
            },
            "unavailable_sections": [],
            "design_unchanged": True,
        }

    def layout_connectivity_inventory(self, session_id: str, **kwargs) -> dict:
        assert kwargs["selector"] == {"nets": ["N1"]}
        return {
            "counts": {"nets": 1, "components": 2, "pins": 4, "vias": 3},
            "returned_counts": {"nets": 1, "components": 2, "pins": 4, "vias": 3},
            "truncated_sections": [],
            "unavailable_sections": [],
            "design_unchanged": True,
        }

    def layout_port_candidate_inventory(self, session_id: str, **kwargs) -> dict:
        return {
            "status": "needs_user_hint",
            "signal_nets": list(kwargs["signal_nets"]),
            "reference_nets": list(kwargs.get("reference_nets") or []),
            "candidates": [
                {
                    "kind": "component",
                    "name": "U1",
                    "signal_nets": ["n1", "n2"],
                    "score": 120.0,
                    "confidence": 0.92,
                }
            ],
            "recommended_endpoints": [],
            "design_unchanged": True,
        }

    def variable_inventory(self, session_id: str, **kwargs) -> dict:
        return {"count": 1, "variables": [], "design_unchanged": True}

    def preview_variable_batch_upsert(self, session_id: str, **kwargs) -> dict:
        return {
            "preview_id": "variable-batch-preview-1",
            "product": kwargs["product"],
            "changes": [
                {
                    "name": item["name"],
                    "action": "create",
                    "after_expression": item["expression"],
                }
                for item in kwargs["variables"]
            ],
            "approval_request": {"action": "aedt.variables.batch_upsert"},
            "project_saved": False,
        }

    def apply_variable_batch_upsert(
        self,
        session_id: str,
        *,
        preview_id: str,
        approval_token: str,
    ) -> dict:
        assert preview_id == "variable-batch-preview-1"
        assert approval_token == "variable-batch-approved"
        return {
            "status": "verified",
            "product": "layout",
            "requested_count": 3,
            "change_count": 3,
            "create_count": 3,
            "update_count": 0,
            "noop_count": 0,
            "changes": [
                {
                    "name": "W_main",
                    "action": "create",
                    "after_expression": "4.3mil",
                    "readback_expression": "4.3mil",
                },
                {
                    "name": "W_double",
                    "action": "create",
                    "after_expression": "2*W_main",
                    "readback_expression": "2*W_main",
                },
                {
                    "name": "$BoardScale",
                    "action": "create",
                    "after_expression": "1.0",
                    "readback_expression": "1",
                },
            ],
            "automatic_rollback_on_failure": True,
            "project_saved": False,
        }

    def setup_inventory(self, session_id: str, **kwargs) -> dict:
        return {
            "setup_count": 1,
            "setups": [{"name": "SetupL", "sweeps": ["Sweep1"]}],
            "ports": ["P1", "P2"],
            "port_order_source": "pyaedt.excitation_names",
            "design_unchanged": True,
        }

    def solution_inventory(self, session_id: str, **kwargs) -> dict:
        return {
            "product": "layout",
            "project_name": "demo",
            "design_name": "layout",
            "setup_name": kwargs.get("setup_name", ""),
            "setup_is_solved": True,
            "target_solution_available": True,
            "target_solution_names": [f"{kwargs.get('setup_name', '')} : Sweep1"],
            "snapshot_digest": "solution-snapshot-1",
            "design_unchanged": True,
        }

    def preview_hfss_geometry_create(self, session_id: str, **kwargs) -> dict:
        return {
            "preview_id": "geometry-preview-1",
            "primitives": list(kwargs["primitives"]),
            "requested_object_names": [item["name"] for item in kwargs["primitives"]],
            "expected_object_count": len(kwargs["primitives"]),
            "approval_request": {"action": "hfss.geometry.create"},
            "project_saved": False,
        }

    def apply_hfss_geometry_create(
        self,
        session_id: str,
        *,
        preview_id: str,
        approval_token: str,
    ) -> dict:
        assert preview_id == "geometry-preview-1"
        assert approval_token == "geometry-approved"
        return {
            "status": "verified",
            "expected_object_count": 2,
            "created_object_count": 2,
            "created_object_names": ["Substrate", "AirBox"],
            "objects": [
                {"name": "Substrate", "material_name": "FR4_epoxy"},
                {"name": "AirBox", "material_name": "vacuum"},
            ],
            "geometry_snapshot_digest": "geometry-after-1",
            "automatic_rollback_on_failure": True,
            "project_saved": False,
        }

    def preview_hfss_material_create(self, session_id: str, **kwargs) -> dict:
        return {
            "preview_id": "material-create-preview-1",
            "material_name": kwargs["material_name"],
            "approval_request": {"action": "hfss.material.create"},
            "project_saved": False,
        }

    def apply_hfss_material_create(
        self,
        session_id: str,
        *,
        preview_id: str,
        approval_token: str,
    ) -> dict:
        assert preview_id == "material-create-preview-1"
        assert approval_token == "material-create-approved"
        properties = {
            "permittivity": 4.2,
            "permeability": 1.01,
            "conductivity": 0.005,
            "dielectric_loss_tangent": 0.018,
            "magnetic_loss_tangent": 0.002,
        }
        return {
            "status": "verified",
            "created_material_name": "HarnessLaminate",
            "material_count": 3,
            "material": {
                "canonical_name": "HarnessLaminate",
                "is_dielectric": True,
                "electrical_properties": {
                    name: {"type": "simple", "value": value, "unit": None}
                    for name, value in properties.items()
                },
                "appearance": [10, 20, 30, 0.4],
                "definition_digest": "material-definition-1",
            },
            "automatic_rollback_on_failure": True,
            "project_saved": False,
        }

    def preview_layout_material_create_assign(self, session_id: str, **kwargs) -> dict:
        return {
            "preview_id": "layout-material-create-assign-preview-1",
            "material_name": kwargs["material_name"],
            "layer_name": kwargs["layer_name"],
            "assignment_field": kwargs["assignment_field"],
            "approval_request": {
                "action": "layout.material.create_and_assign"
            },
            "project_saved": False,
        }

    def apply_layout_material_create_assign(
        self,
        session_id: str,
        *,
        preview_id: str,
        approval_token: str,
    ) -> dict:
        assert preview_id == "layout-material-create-assign-preview-1"
        assert approval_token == "layout-material-create-assign-approved"
        properties = {
            "permittivity": 3.7,
            "permeability": 1.0,
            "conductivity": 0.001,
            "dielectric_loss_tangent": 0.012,
            "magnetic_loss_tangent": 0.0,
        }
        return {
            "status": "verified",
            "created_material_name": "HarnessLayoutLaminate",
            "expected_material_class": "dielectric",
            "material_count": 4,
            "stackup_layer_count": 2,
            "material": {
                "canonical_name": "HarnessLayoutLaminate",
                "is_dielectric": True,
                "electrical_properties": {
                    name: {"type": "simple", "value": value, "unit": None}
                    for name, value in properties.items()
                },
                "appearance": [20, 30, 40, 0.2],
                "definition_digest": "layout-material-definition-1",
            },
            "layer": {
                "name": "D1",
                "type": "dielectric",
                "id": 2,
                "material": "HarnessLayoutLaminate",
                "fill_material": "",
            },
            "before_assignment": "FR4_epoxy",
            "after_assignment": "HarnessLayoutLaminate",
            "material_catalog_digest": "layout-material-catalog-1",
            "stackup_digest": "layout-stackup-1",
            "automatic_rollback_on_failure": True,
            "project_saved": False,
        }

    def preview_hfss_material_assign(self, session_id: str, **kwargs) -> dict:
        return {
            "preview_id": "material-preview-1",
            "object_names": list(kwargs["object_names"]),
            "material_name": kwargs["material_name"],
            "approval_request": {"action": "hfss.material.assign"},
            "project_saved": False,
        }

    def apply_hfss_material_assign(
        self,
        session_id: str,
        *,
        preview_id: str,
        approval_token: str,
    ) -> dict:
        assert preview_id == "material-preview-1"
        assert approval_token == "material-approved"
        return {
            "status": "verified",
            "object_names": ["box1", "box2"],
            "material_name": "copper",
            "target_solve_inside": False,
            "target_count": 2,
            "verified_count": 2,
            "targets_after": [
                {
                    "name": "box1",
                    "material_name": "copper",
                    "solve_inside": False,
                },
                {
                    "name": "box2",
                    "material_name": "copper",
                    "solve_inside": False,
                },
            ],
            "automatic_rollback_on_failure": True,
            "project_saved": False,
        }

    def preview_hfss_length_mesh_create(self, session_id: str, **kwargs) -> dict:
        return {
            "preview_id": "length-mesh-preview-1",
            "mesh_name": kwargs["mesh_name"],
            "object_names": list(kwargs["object_names"]),
            "approval_request": {"action": "hfss.mesh.length.create"},
            "project_saved": False,
        }

    def apply_hfss_length_mesh_create(
        self,
        session_id: str,
        *,
        preview_id: str,
        approval_token: str,
    ) -> dict:
        assert preview_id == "length-mesh-preview-1"
        assert approval_token == "length-mesh-approved"
        return {
            "status": "verified",
            "mesh_name": "HarnessLength",
            "object_names": ["box1", "box2"],
            "inside_selection": True,
            "maximum_length": "0.4mm",
            "maximum_elements": 500,
            "target_count": 2,
            "created_mesh_operation_name": "HarnessLength",
            "mesh_operation": {
                "name": "HarnessLength",
                "type": "Length Based",
                "object_names": ["box1", "box2"],
                "inside_selection": True,
                "restrict_length": True,
                "maximum_length": "0.4mm",
                "restrict_elements": True,
                "maximum_elements": 500,
            },
            "automatic_rollback_on_failure": True,
            "project_saved": False,
        }

    def preview_hfss_infinite_sphere_create(self, session_id: str, **kwargs) -> dict:
        return {
            "preview_id": "infinite-sphere-preview-1",
            "sphere_name": kwargs["sphere_name"],
            "definition": kwargs["definition"],
            "approval_request": {"action": "hfss.far_field.infinite_sphere.create"},
            "project_saved": False,
        }

    def apply_hfss_infinite_sphere_create(
        self,
        session_id: str,
        *,
        preview_id: str,
        approval_token: str,
    ) -> dict:
        assert preview_id == "infinite-sphere-preview-1"
        assert approval_token == "infinite-sphere-approved"
        return {
            "status": "verified",
            "sphere_name": "HarnessSphere",
            "definition": "Theta-Phi",
            "angle1_axis": "Theta",
            "angle2_axis": "Phi",
            "angle1_start": -90.0,
            "angle1_stop": 90.0,
            "angle1_step": 5.0,
            "angle2_start": 0.0,
            "angle2_stop": 360.0,
            "angle2_step": 10.0,
            "units": "deg",
            "angle1_count": 37,
            "angle2_count": 37,
            "sample_count": 1369,
            "max_samples": 5000,
            "coordinate_system": "Global",
            "polarization": "Slant",
            "polarization_angle": 45.0,
            "created_field_setup_name": "HarnessSphere",
            "field_setup": {
                "name": "HarnessSphere",
                "type": "Infinite Sphere",
                "kind": "infinite_sphere",
                "definition": "Theta-Phi",
                "angle1_axis": "Theta",
                "angle2_axis": "Phi",
                "coordinate_system": "Global",
                "polarization": "Slant",
            },
            "automatic_rollback_on_failure": True,
            "project_saved": False,
        }

    def preview_hfss_surface_boundary_create(self, session_id: str, **kwargs) -> dict:
        return {
            "preview_id": "surface-boundary-preview-1",
            "boundary_kind": kwargs["boundary_kind"],
            "boundary_name": kwargs["boundary_name"],
            "approval_request": {"action": "hfss.surface_boundary.create"},
            "project_saved": False,
        }

    def apply_hfss_surface_boundary_create(
        self,
        session_id: str,
        *,
        preview_id: str,
        approval_token: str,
    ) -> dict:
        assert preview_id == "surface-boundary-preview-1"
        assert approval_token == "surface-boundary-approved"
        return {
            "status": "verified",
            "boundary_kind": "finite_conductivity",
            "boundary_name": "HarnessFinite",
            "object_names": [],
            "face_ids": [101],
            "options": {
                "material_name": "copper",
                "use_thickness": True,
                "thickness": "35um",
                "roughness": "0.5um",
                "is_infinite_ground": False,
                "is_two_sided": False,
                "is_internal": True,
                "is_shell_element": False,
            },
            "created_boundary_name": "HarnessFinite",
            "boundary": {
                "name": "HarnessFinite",
                "type": "Finite Conductivity",
                "kind": "finite_conductivity",
                "assignment_kind": "faces",
                "object_names": [],
                "face_ids": [101],
                "options": {
                    "material_name": "copper",
                    "use_thickness": True,
                    "thickness": "35um",
                    "roughness": "0.5um",
                    "is_infinite_ground": False,
                    "is_two_sided": False,
                    "is_internal": True,
                    "is_shell_element": False,
                },
            },
            "automatic_rollback_on_failure": True,
            "project_saved": False,
        }

    def preview_hfss_coordinate_system_create(self, session_id: str, **kwargs) -> dict:
        assert kwargs["origin"] == ["OX", "2mm", 3]
        assert kwargs["x_axis"] == [1, 1, 0]
        assert kwargs["y_axis"] == [0, 0, 2]
        return {
            "preview_id": "coordinate-system-preview-1",
            "coordinate_system_name": kwargs["coordinate_system_name"],
            "reference_coordinate_system": kwargs["reference_coordinate_system"],
            "approval_request": {"action": "hfss.coordinate_system.create"},
            "project_saved": False,
        }

    def apply_hfss_coordinate_system_create(
        self,
        session_id: str,
        *,
        preview_id: str,
        approval_token: str,
    ) -> dict:
        assert preview_id == "coordinate-system-preview-1"
        assert approval_token == "coordinate-system-approved"
        return {
            "status": "verified",
            "coordinate_system_name": "HarnessCS",
            "reference_coordinate_system": "ParentCS",
            "mode": "axis",
            "origin": ["OX", "2mm", 3],
            "x_axis": [1, 1, 0],
            "y_axis": [0, 0, 2],
            "created_coordinate_system_name": "HarnessCS",
            "coordinate_system": {
                "name": "HarnessCS",
                "type": "Relative",
                "kind": "relative",
                "reference_coordinate_system": "ParentCS",
                "mode": "Axis/Position",
                "origin": ["OX", "2mm", "3mm"],
                "x_axis": ["1", "1", "0"],
                "y_axis": ["0", "0", "2"],
                "property_digest": "coordinate-digest",
            },
            "active_coordinate_system_restored": True,
            "automatic_rollback_on_failure": True,
            "project_saved": False,
        }

    def preview_hfss_boundary(self, session_id: str, **kwargs) -> dict:
        assert kwargs["boundary_kind"] == "wave_port"
        assert kwargs["assignment_face_ids"] == [101]
        return {
            "preview_id": "typed-port-preview-1",
            "boundary_kind": kwargs["boundary_kind"],
            "boundary_name": kwargs["boundary_name"],
            "resolved_integration_line": {
                "start": ["0mm", "10mm", "5mm"],
                "end": ["0mm", "0mm", "5mm"],
            },
            "approval_request": {"action": "hfss.boundary.create"},
            "project_saved": False,
        }

    def apply_hfss_boundary(
        self,
        session_id: str,
        *,
        preview_id: str,
        approval_token: str,
    ) -> dict:
        assert preview_id == "typed-port-preview-1"
        assert approval_token == "typed-port-approved"
        return {
            "status": "verified",
            "boundary_kind": "wave_port",
            "boundary_name": "HarnessWave",
            "assignment_face_ids": [101],
            "assignment_object_name": "",
            "created_boundary_name": "HarnessWave",
            "boundary": {
                "name": "HarnessWave",
                "type": "Wave Port",
                "kind": "wave_port",
                "assignment_kind": "faces",
                "object_names": [],
                "face_ids": [101],
                "options": {
                    "renormalize": False,
                    "deembed_enabled": True,
                    "deembed_distance": "1.25mm",
                    "integration_line": {
                        "start": ["0mm", "10mm", "5mm"],
                        "end": ["0mm", "0mm", "5mm"],
                    },
                    "mode_count": 2,
                    "modes": [
                        {"name": "Mode1", "characteristic_impedance": "Zwave"},
                        {"name": "Mode2", "characteristic_impedance": "Zwave"},
                    ],
                },
            },
            "automatic_rollback_on_failure": True,
            "project_saved": False,
        }

    def preview_hfss_geometry_boundary_create(self, session_id: str, **kwargs) -> dict:
        return {
            "preview_id": "geometry-boundary-preview-1",
            "primitives": list(kwargs["primitives"]),
            "boundaries": list(kwargs["boundaries"]),
            "requested_object_names": [item["name"] for item in kwargs["primitives"]],
            "requested_boundary_names": [
                item["boundary_name"] for item in kwargs["boundaries"]
            ],
            "approval_request": {"action": "hfss.geometry_boundary.create"},
            "project_saved": False,
        }

    def apply_hfss_geometry_boundary_create(
        self,
        session_id: str,
        *,
        preview_id: str,
        approval_token: str,
    ) -> dict:
        assert preview_id == "geometry-boundary-preview-1"
        assert approval_token == "geometry-boundary-approved"
        return {
            "status": "verified",
            "created_object_count": 2,
            "created_object_names": ["PortBody", "AirRegion"],
            "created_boundary_count": 2,
            "created_boundary_names": ["P1", "Radiation1"],
            "objects": [
                {"name": "PortBody", "material_name": "vacuum"},
                {"name": "AirRegion", "material_name": "vacuum"},
            ],
            "resolved_boundaries": [
                {"boundary_name": "P1", "assignment_face_ids": [101]},
                {
                    "boundary_name": "Radiation1",
                    "assignment_face_ids": [201, 202, 203, 204, 205, 206],
                },
            ],
            "geometry_snapshot_digest": "geometry-boundary-after-1",
            "automatic_rollback_on_failure": True,
            "atomic_geometry_boundary_transaction": True,
            "project_saved": False,
        }

    def preview_hfss_setup_sweep_create(self, session_id: str, **kwargs) -> dict:
        return {
            "preview_id": "setup-sweep-preview-1",
            "setup": dict(kwargs["setup"]),
            "sweep": dict(kwargs["sweep"]),
            "approval_request": {"action": "hfss.setup_sweep.create"},
            "project_saved": False,
        }

    def apply_hfss_setup_sweep_create(
        self,
        session_id: str,
        *,
        preview_id: str,
        approval_token: str,
    ) -> dict:
        assert preview_id == "setup-sweep-preview-1"
        assert approval_token == "setup-sweep-approved"
        return {
            "status": "verified",
            "setup": {
                "name": "AtomicSetup",
                "type": "HFSSDriven",
                "properties": {"Frequency": "10GHz", "MaximumPasses": 3},
            },
            "sweep": {
                "name": "AtomicSweep",
                "range_type": "LinearCount",
                "count": 101,
            },
            "setup_inventory": {
                "name": "AtomicSetup",
                "type": "HFSSDriven",
                "properties": {"Frequency": "10GHz", "MaximumPasses": 3},
                "sweeps": ["AtomicSweep"],
            },
            "created_setup_name": "AtomicSetup",
            "created_sweep_name": "AtomicSweep",
            "atomic_setup_sweep_transaction": True,
            "automatic_rollback_on_failure": True,
            "project_saved": False,
        }

    def list_layout_paths(self, session_id: str, **kwargs) -> dict:
        return {
            "count": 2,
            "paths": [
                {"name": "line1", "width_expression": "4.3mil"},
                {"name": "line2", "width_expression": "4.3mil"},
            ],
        }

    def preview_layout_width(self, session_id: str, **kwargs) -> dict:
        return {
            "preview_id": "width-preview-1",
            "target_count": 2,
            "approval_request": {"action": "layout.path_width.parameterize"},
        }

    def apply_layout_width(self, session_id: str, *, preview_id: str, approval_token: str) -> dict:
        assert preview_id == "width-preview-1"
        assert approval_token == "operation-approved"
        return {
            "status": "verified",
            "target_count": 2,
            "verified_count": 2,
            "project_saved": False,
        }

    def preview_layout_component_ports_create(self, session_id: str, **kwargs) -> dict:
        return {
            "preview_id": "port-preview-1",
            "component_name": kwargs["component_name"],
            "signal_nets": list(kwargs["signal_nets"]),
            "expected_port_count": 2,
            "matching_pins": [
                {"name": "U1-1", "net_name": "N1"},
                {"name": "U1-2", "net_name": "N2"},
            ],
            "approval_request": {"action": "layout.component_ports.create"},
        }

    def apply_layout_component_ports_create(
        self,
        session_id: str,
        *,
        preview_id: str,
        approval_token: str,
    ) -> dict:
        assert preview_id == "port-preview-1"
        assert approval_token == "port-approved"
        return {
            "status": "verified",
            "component_name": "U1",
            "signal_nets": ["N1", "N2"],
            "expected_port_count": 2,
            "created_port_count": 2,
            "created_ports": ["Port_U1-1", "Port_U1-2"],
            "ports": ["P1", "P2", "Port_U1-1", "Port_U1-2"],
            "port_order_source": "pyaedt.excitation_names",
            "project_saved": False,
        }

    def layout_edge_port_candidate_inventory(self, session_id: str, **kwargs) -> dict:
        return {
            "status": "ready",
            "signal_nets": list(kwargs["signal_nets"]),
            "candidates": [
                {
                    "primitive": "line1",
                    "edge_number": 0,
                    "net": "N1",
                    "layer": "L1",
                    "distance_to_side": 0.2,
                },
                {
                    "primitive": "line2",
                    "edge_number": 0,
                    "net": "N2",
                    "layer": "L1",
                    "distance_to_side": 0.3,
                },
            ],
            "truncated": False,
            "snapshot_digest": "edge-candidates-1",
            "design_unchanged": True,
        }

    def preview_layout_edge_ports_create(self, session_id: str, **kwargs) -> dict:
        return {
            "preview_id": "edge-port-preview-1",
            "edge_targets": list(kwargs["edge_targets"]),
            "expected_port_count": 2,
            "approval_request": {"action": "layout.edge_ports.create"},
        }

    def apply_layout_edge_ports_create(
        self,
        session_id: str,
        *,
        preview_id: str,
        approval_token: str,
    ) -> dict:
        assert preview_id == "edge-port-preview-1"
        assert approval_token == "edge-port-approved"
        return {
            "status": "verified",
            "expected_port_count": 2,
            "created_port_count": 2,
            "created_ports": [
                {"port_name": "EdgePort_1", "target": {"request": {"primitive_name": "line1"}}},
                {"port_name": "EdgePort_2", "target": {"request": {"primitive_name": "line2"}}},
            ],
            "edge_targets": [
                {"request": {"primitive_name": "line1", "edge_number": 0, "port_type": "circuit"}},
                {"request": {"primitive_name": "line2", "edge_number": 0, "port_type": "circuit"}},
            ],
            "ports": ["P1", "P2", "EdgePort_1", "EdgePort_2"],
            "port_order_source": "pyaedt.excitation_names",
            "project_saved": False,
        }

    def preview_hfss_analysis_start(self, session_id: str, **kwargs) -> dict:
        assert kwargs["product"] == "layout"
        return {
            "preview_id": "solve-preview-1",
            "approval_request": {"action": "hfss.analysis.start"},
        }

    def apply_hfss_analysis_start(self, session_id: str, *, preview_id: str, approval_token: str) -> dict:
        assert preview_id == "solve-preview-1"
        assert approval_token == "solve-approved"
        return {
            "status": "submitted",
            "started": True,
            "blocking": False,
            "run_id": "run-1",
            "resources": {"cores": 4, "tasks": 1, "gpus": 0},
            "project_saved": False,
        }

    def hfss_analysis_status(self, session_id: str, **kwargs) -> dict:
        if self.analysis_statuses:
            return self.analysis_statuses.pop(0)
        return {"running": True, "latest_run": {"run_id": "run-1"}}

    def preview_hfss_export(self, session_id: str, **kwargs) -> dict:
        assert kwargs["product"] == "layout"
        self.export_spec = {
            **kwargs,
            "artifact_name": kwargs["artifact_name"] or kwargs["report_name"] or kwargs["setup_name"],
        }
        return {
            "preview_id": "export-preview-1",
            **self.export_spec,
            "ports": ["P1", "P2"],
            "port_order_source": "pyaedt.excitation_names",
            "approval_request": {"action": "hfss.results.export"},
            "approval_required": True,
            "project_unchanged": True,
        }

    def apply_hfss_export(self, session_id: str, *, preview_id: str, approval_token: str) -> dict:
        assert preview_id == "export-preview-1"
        assert approval_token == "export-approved"
        assert self.export_root is not None
        output = self.export_root / preview_id
        output.mkdir(parents=True)
        artifact_path = output / f"{self.export_spec['artifact_name']}.s2p"
        artifact_path.write_text(
            "# GHZ S RI R 50\n"
            "1 0.1 0 0.8 0 0.8 0 0.1 0\n"
            "2 0.12 0 0.7 0 0.7 0 0.12 0\n",
            encoding="ascii",
        )
        digest = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
        artifact = {"path": str(artifact_path), "sha256": digest, "bytes": artifact_path.stat().st_size}
        spec = {
            "product": "layout",
            "export_kind": self.export_spec["export_kind"],
            "setup_name": self.export_spec["setup_name"],
            "sweep_name": self.export_spec["sweep_name"],
            "report_name": self.export_spec["report_name"],
            "artifact_name": self.export_spec["artifact_name"],
        }
        manifest = {
            "project_name": "demo",
            "design_name": "layout",
            "spec": spec,
            "ports": ["P1", "P2"],
            "port_order_source": "pyaedt.excitation_names",
            "artifact": artifact,
        }
        manifest_path = output / f"{artifact_path.name}.evidence.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        return {
            "status": "verified",
            "product": "layout",
            "artifact": artifact,
            "manifest_path": str(manifest_path),
            "project_unchanged": True,
            "project_saved": False,
        }


def _manager(tmp_path: Path) -> AssistantWorkflowManager:
    return AssistantWorkflowManager(
        live_manager=_Live(),
        db_path=tmp_path / "missions.db",
        template_ids=("brd_local_cut_build",),
        runtime_factory=lambda path: AgentRuntime(SQLiteMissionStore(path)),
    )


def _mapped_score_payload() -> dict:
    return {
        "setup_name": "SetupL",
        "sweep_name": "Sweep1",
        "artifact_name": "mapped-channel",
        "expected_port_order": ["P1", "P2"],
        "sparameter_mode": "single_ended",
        "source_ports": ["P1"],
        "destination_ports": ["P2"],
        "frequency_start_ghz": 1.0,
        "frequency_stop_ghz": 2.0,
        "rl_target_db": -15.0,
        "insertion_loss_min_db": -4.0,
        "reference_impedance_ohm": 50.0,
    }


def test_workflow_catalog_exposes_existing_graph_without_mutating_it(tmp_path: Path):
    manager = _manager(tmp_path)

    catalog = manager.list_workflows()
    inspected = manager.inspect_workflow("brd_local_cut_build")

    assert catalog["execution_model"] == "guarded_graph_step"
    assert catalog["workflows"][0]["workflow_id"] == "brd_local_cut_build"
    assert "brd.local_cut.build" in inspected["worker_capabilities"]
    assert inspected["graph"]["template_id"] == "brd_local_cut_build"


def test_default_workflow_catalog_includes_live_monitor_and_export(tmp_path: Path):
    manager = AssistantWorkflowManager(
        live_manager=_Live(),
        db_path=tmp_path / "catalog-missions.db",
        runtime_factory=lambda path: AgentRuntime(SQLiteMissionStore(path)),
    )

    descriptors = {item["workflow_id"]: item for item in manager.list_workflows()["workflows"]}

    assert descriptors["aedt_live_variable_batch_upsert"]["risk"] == "reversible_edit"
    assert descriptors["aedt_live_variable_batch_upsert"]["attached_live_session_reuse"] is True
    assert descriptors["aedt_live_variable_batch_upsert"]["recommended_initial_fields"] == [
        "product",
        "variables",
    ]
    assert descriptors["hfss_live_geometry_create"]["risk"] == "reversible_edit"
    assert descriptors["hfss_live_geometry_create"]["attached_live_session_reuse"] is True
    assert descriptors["hfss_live_geometry_create"]["recommended_initial_fields"] == [
        "primitives"
    ]
    assert descriptors["hfss_live_material_create"]["risk"] == "reversible_edit"
    assert descriptors["hfss_live_material_create"]["attached_live_session_reuse"] is True
    assert descriptors["hfss_live_material_create"]["recommended_initial_fields"] == [
        "material_name",
    ]
    assert descriptors["layout_live_material_create_assign"]["risk"] == (
        "reversible_edit"
    )
    assert descriptors["layout_live_material_create_assign"][
        "attached_live_session_reuse"
    ] is True
    assert descriptors["layout_live_material_create_assign"][
        "recommended_initial_fields"
    ] == ["assignment_field", "layer_name", "material_name"]
    assert descriptors["hfss_live_material_assign"]["risk"] == "reversible_edit"
    assert descriptors["hfss_live_material_assign"]["attached_live_session_reuse"] is True
    assert descriptors["hfss_live_material_assign"]["recommended_initial_fields"] == [
        "material_name",
        "object_names",
    ]
    assert descriptors["hfss_live_length_mesh_create"]["risk"] == "reversible_edit"
    assert descriptors["hfss_live_length_mesh_create"]["attached_live_session_reuse"] is True
    assert descriptors["hfss_live_length_mesh_create"]["recommended_initial_fields"] == [
        "mesh_name",
        "object_names",
    ]
    assert descriptors["hfss_live_infinite_sphere_create"]["risk"] == "reversible_edit"
    assert descriptors["hfss_live_infinite_sphere_create"]["attached_live_session_reuse"] is True
    assert descriptors["hfss_live_infinite_sphere_create"]["recommended_initial_fields"] == [
        "sphere_name"
    ]
    assert descriptors["hfss_live_surface_boundary_create"]["risk"] == "reversible_edit"
    assert descriptors["hfss_live_surface_boundary_create"]["attached_live_session_reuse"] is True
    assert descriptors["hfss_live_surface_boundary_create"]["recommended_initial_fields"] == [
        "boundary_kind",
        "boundary_name",
    ]
    assert descriptors["hfss_live_coordinate_system_create"]["risk"] == "reversible_edit"
    assert descriptors["hfss_live_coordinate_system_create"]["attached_live_session_reuse"] is True
    assert descriptors["hfss_live_coordinate_system_create"]["recommended_initial_fields"] == [
        "coordinate_system_name",
        "origin",
        "x_axis",
        "y_axis",
    ]
    assert descriptors["hfss_live_port_create"]["risk"] == "reversible_edit"
    assert descriptors["hfss_live_port_create"]["attached_live_session_reuse"] is True
    assert descriptors["hfss_live_port_create"]["recommended_initial_fields"] == [
        "boundary_kind",
        "boundary_name",
    ]
    assert descriptors["hfss_live_geometry_boundary_create"]["risk"] == "reversible_edit"
    assert descriptors["hfss_live_geometry_boundary_create"]["attached_live_session_reuse"] is True
    assert descriptors["hfss_live_geometry_boundary_create"]["recommended_initial_fields"] == [
        "boundaries",
        "primitives",
    ]
    assert descriptors["hfss_live_setup_sweep_create"]["risk"] == "reversible_edit"
    assert descriptors["hfss_live_setup_sweep_create"]["attached_live_session_reuse"] is True
    assert descriptors["hfss_live_setup_sweep_create"]["recommended_initial_fields"] == [
        "setup",
        "sweep",
    ]
    assert descriptors["layout_live_solve_monitor"]["risk"] == "read_only"
    assert descriptors["layout_live_solve_monitor"]["attached_live_session_reuse"] is True
    assert descriptors["layout_live_results_export"]["risk"] == "persistent_write"
    assert descriptors["layout_live_results_export"]["attached_live_session_reuse"] is True
    assert descriptors["layout_live_solve_export"]["risk"] == "expensive"
    assert descriptors["layout_live_solve_export"]["attached_live_session_reuse"] is True
    assert descriptors["layout_live_solve_export"]["recommended_initial_fields"] == ["export_kind", "setup_name"]
    assert descriptors["layout_live_touchstone_score"]["risk"] == "persistent_write"
    assert descriptors["layout_live_touchstone_score"]["attached_live_session_reuse"] is True
    assert descriptors["layout_live_touchstone_score"]["recommended_initial_fields"] == [
        "destination_ports",
        "expected_port_order",
        "frequency_start_ghz",
        "frequency_stop_ghz",
        "insertion_loss_min_db",
        "reference_impedance_ohm",
        "rl_target_db",
        "setup_name",
        "source_ports",
        "sparameter_mode",
    ]
    assert descriptors["layout_live_solve_touchstone_score"]["risk"] == "expensive"
    assert descriptors["layout_live_solve_touchstone_score"]["attached_live_session_reuse"] is True
    assert descriptors["layout_live_parameterize_solve_touchstone_score"]["risk"] == "expensive"
    assert descriptors["layout_live_parameterize_solve_touchstone_score"]["attached_live_session_reuse"] is True
    assert descriptors["layout_live_component_ports_create"]["risk"] == "reversible_edit"
    assert descriptors["layout_live_component_ports_create"]["attached_live_session_reuse"] is True
    assert descriptors["layout_live_component_ports_create"]["recommended_initial_fields"] == [
        "component_name",
        "signal_nets",
    ]
    assert descriptors["layout_live_uniform_edge_ports_create"]["risk"] == "reversible_edit"
    assert descriptors["layout_live_uniform_edge_ports_create"]["attached_live_session_reuse"] is True
    assert descriptors["layout_live_uniform_edge_ports_create"]["recommended_initial_fields"] == [
        "layer",
        "local_cut_region",
        "port_type",
        "side",
        "signal_nets",
    ]
    assert manager.inspect_workflow("layout_live_solve_monitor")["graph"]["edges"][1]["on"] == "running"


def test_workflow_start_requires_preview_and_creates_graph_without_executing(tmp_path: Path):
    manager = _manager(tmp_path)
    preview = manager.preview_start(
        "live-1",
        workflow_id="brd_local_cut_build",
        goal="Build a reviewed local cut",
        initial_payload={
            "layout_file": "board.aedb",
            "signal_nets": ["D0"],
            "reference_nets": ["GND"],
            "local_cut_region": {"type": "bbox", "unit": "mil", "x_min": 0, "y_min": 0, "x_max": 10, "y_max": 10},
        },
    )

    started = manager.apply_start(
        "live-1",
        preview_id=preview["preview_id"],
        approval_token="approved",
    )
    status = manager.status(started["graph_run_id"])

    assert started["execution_started"] is False
    assert status["status"] == "running"
    assert status["graph_run"]["step_count"] == 0
    assert status["node_runs"] == []


def test_workflow_advance_is_target_bound_and_one_step_per_approval(tmp_path: Path):
    manager = _manager(tmp_path)
    start_preview = manager.preview_start(
        "live-1",
        workflow_id="brd_local_cut_build",
        goal="Build a reviewed local cut",
        initial_payload={
            "layout_file": "board.aedb",
            "signal_nets": ["D0"],
            "reference_nets": ["GND"],
            "local_cut_region": {"type": "bbox", "unit": "mil", "x_min": 0, "y_min": 0, "x_max": 10, "y_max": 10},
        },
    )
    started = manager.apply_start(
        "live-1",
        preview_id=start_preview["preview_id"],
        approval_token="approved",
    )
    advance_preview = manager.preview_advance(
        "live-1",
        graph_run_id=started["graph_run_id"],
    )

    status = manager.apply_advance(
        "live-1",
        preview_id=advance_preview["preview_id"],
        approval_token="approved",
    )

    assert status["graph_run"]["step_count"] == 1
    assert len(status["node_runs"]) == 1


def test_live_hfss_geometry_workflow_uses_nested_approval_and_scorecard(tmp_path: Path):
    manager = AssistantWorkflowManager(
        live_manager=_Live(),
        db_path=tmp_path / "geometry-missions.db",
        template_ids=("hfss_live_geometry_create",),
        runtime_factory=lambda path: AgentRuntime(SQLiteMissionStore(path)),
    )
    primitives = [
        {
            "kind": "box",
            "name": "Substrate",
            "origin": [0, 0, 0],
            "size": [10, 5, 1],
            "material": "FR4_epoxy",
        },
        {
            "kind": "region",
            "name": "AirBox",
            "padding": "10mm",
            "padding_type": "Absolute Offset",
        },
    ]
    start = manager.preview_start(
        "live-1",
        workflow_id="hfss_live_geometry_create",
        goal="Create a reviewed HFSS geometry batch",
        initial_payload={"primitives": primitives, "max_new_objects": 4},
    )
    started = manager.apply_start(
        "live-1",
        preview_id=start["preview_id"],
        approval_token="approved",
    )
    report = None
    for index in range(3):
        advance = manager.preview_advance("live-1", graph_run_id=started["graph_run_id"])
        if index == 1:
            assert advance["operation_approval_required"]["preview_id"] == "geometry-preview-1"
        report = manager.apply_advance(
            "live-1",
            preview_id=advance["preview_id"],
            approval_token="approved",
            operation_approval_token="geometry-approved" if index == 1 else "",
        )

    assert report is not None and report["status"] == "succeeded"
    assert "geometry-approved" not in str(report)
    scorecard = report["node_runs"][-1]["output_payload"]
    assert scorecard["status"] == "passed"
    assert scorecard["summary"]["created_object_names"] == ["Substrate", "AirBox"]
    assert scorecard["summary"]["project_saved"] is False


def test_live_hfss_material_workflow_assigns_and_scores_batch(tmp_path: Path):
    manager = AssistantWorkflowManager(
        live_manager=_Live(),
        db_path=tmp_path / "material-missions.db",
        template_ids=("hfss_live_material_assign",),
        runtime_factory=lambda path: AgentRuntime(SQLiteMissionStore(path)),
    )
    start = manager.preview_start(
        "live-1",
        workflow_id="hfss_live_material_assign",
        goal="Assign copper to two reviewed HFSS solids",
        initial_payload={
            "object_names": ["box1", "box2"],
            "material_name": "copper",
            "max_objects": 4,
        },
    )
    started = manager.apply_start(
        "live-1",
        preview_id=start["preview_id"],
        approval_token="approved",
    )
    report = None
    for index in range(3):
        advance = manager.preview_advance("live-1", graph_run_id=started["graph_run_id"])
        if index == 1:
            assert advance["operation_approval_required"]["preview_id"] == "material-preview-1"
        report = manager.apply_advance(
            "live-1",
            preview_id=advance["preview_id"],
            approval_token="approved",
            operation_approval_token="material-approved" if index == 1 else "",
        )

    assert report is not None and report["status"] == "succeeded"
    assert "material-approved" not in str(report)
    scorecard = report["node_runs"][-1]["output_payload"]
    assert scorecard["status"] == "passed"
    assert scorecard["summary"]["target_count"] == 2
    assert scorecard["summary"]["material_name"] == "copper"
    assert scorecard["summary"]["target_solve_inside"] is False
    assert scorecard["summary"]["project_saved"] is False


def test_live_hfss_material_create_workflow_creates_and_scores_definition(tmp_path: Path):
    manager = AssistantWorkflowManager(
        live_manager=_Live(),
        db_path=tmp_path / "material-create-missions.db",
        template_ids=("hfss_live_material_create",),
        runtime_factory=lambda path: AgentRuntime(SQLiteMissionStore(path)),
    )
    start = manager.preview_start(
        "live-1",
        workflow_id="hfss_live_material_create",
        goal="Create one reviewed isotropic HFSS material",
        initial_payload={
            "material_name": "HarnessLaminate",
            "permittivity": 4.2,
            "permeability": 1.01,
            "conductivity": 0.005,
            "dielectric_loss_tangent": 0.018,
            "magnetic_loss_tangent": 0.002,
            "appearance": [10, 20, 30, 0.4],
        },
    )
    started = manager.apply_start(
        "live-1",
        preview_id=start["preview_id"],
        approval_token="approved",
    )
    report = None
    for index in range(3):
        advance = manager.preview_advance("live-1", graph_run_id=started["graph_run_id"])
        if index == 1:
            assert (
                advance["operation_approval_required"]["preview_id"]
                == "material-create-preview-1"
            )
        report = manager.apply_advance(
            "live-1",
            preview_id=advance["preview_id"],
            approval_token="approved",
            operation_approval_token=(
                "material-create-approved" if index == 1 else ""
            ),
        )

    assert report is not None and report["status"] == "succeeded"
    assert "material-create-approved" not in str(report)
    scorecard = report["node_runs"][-1]["output_payload"]
    assert scorecard["status"] == "passed"
    assert scorecard["summary"]["created_material_name"] == "HarnessLaminate"
    assert scorecard["summary"]["definition_digest"] == "material-definition-1"
    assert scorecard["summary"]["project_saved"] is False


def test_live_layout_material_create_assign_workflow_scores_atomic_change(
    tmp_path: Path,
):
    manager = AssistantWorkflowManager(
        live_manager=_Live(),
        db_path=tmp_path / "layout-material-create-assign-missions.db",
        template_ids=("layout_live_material_create_assign",),
        runtime_factory=lambda path: AgentRuntime(SQLiteMissionStore(path)),
    )
    start = manager.preview_start(
        "live-1",
        workflow_id="layout_live_material_create_assign",
        goal="Create one laminate and assign it to D1",
        initial_payload={
            "material_name": "HarnessLayoutLaminate",
            "layer_name": "D1",
            "assignment_field": "material",
            "permittivity": 3.7,
            "permeability": 1.0,
            "conductivity": 0.001,
            "dielectric_loss_tangent": 0.012,
            "magnetic_loss_tangent": 0.0,
            "appearance": [20, 30, 40, 0.2],
        },
    )
    started = manager.apply_start(
        "live-1",
        preview_id=start["preview_id"],
        approval_token="approved",
    )
    report = None
    for index in range(3):
        advance = manager.preview_advance(
            "live-1",
            graph_run_id=started["graph_run_id"],
        )
        if index == 1:
            assert advance["operation_approval_required"]["preview_id"] == (
                "layout-material-create-assign-preview-1"
            )
        report = manager.apply_advance(
            "live-1",
            preview_id=advance["preview_id"],
            approval_token="approved",
            operation_approval_token=(
                "layout-material-create-assign-approved" if index == 1 else ""
            ),
        )

    assert report is not None and report["status"] == "succeeded"
    assert "layout-material-create-assign-approved" not in str(report)
    scorecard = report["node_runs"][-1]["output_payload"]
    assert scorecard["status"] == "passed"
    assert scorecard["summary"]["created_material_name"] == (
        "HarnessLayoutLaminate"
    )
    assert scorecard["summary"]["layer_name"] == "D1"
    assert scorecard["summary"]["assignment_field"] == "material"
    assert scorecard["summary"]["after_assignment"] == (
        "HarnessLayoutLaminate"
    )
    assert scorecard["summary"]["project_saved"] is False


def test_live_hfss_length_mesh_workflow_creates_and_scores_operation(tmp_path: Path):
    manager = AssistantWorkflowManager(
        live_manager=_Live(),
        db_path=tmp_path / "length-mesh-missions.db",
        template_ids=("hfss_live_length_mesh_create",),
        runtime_factory=lambda path: AgentRuntime(SQLiteMissionStore(path)),
    )
    start = manager.preview_start(
        "live-1",
        workflow_id="hfss_live_length_mesh_create",
        goal="Create a reviewed length mesh on two HFSS solids",
        initial_payload={
            "mesh_name": "HarnessLength",
            "object_names": ["box1", "box2"],
            "inside_selection": True,
            "maximum_length": "0.4mm",
            "maximum_elements": 500,
            "max_objects": 4,
        },
    )
    started = manager.apply_start(
        "live-1",
        preview_id=start["preview_id"],
        approval_token="approved",
    )
    report = None
    for index in range(3):
        advance = manager.preview_advance("live-1", graph_run_id=started["graph_run_id"])
        if index == 1:
            assert (
                advance["operation_approval_required"]["preview_id"]
                == "length-mesh-preview-1"
            )
        report = manager.apply_advance(
            "live-1",
            preview_id=advance["preview_id"],
            approval_token="approved",
            operation_approval_token="length-mesh-approved" if index == 1 else "",
        )

    assert report is not None and report["status"] == "succeeded"
    assert "length-mesh-approved" not in str(report)
    scorecard = report["node_runs"][-1]["output_payload"]
    assert scorecard["status"] == "passed"
    assert scorecard["summary"]["mesh_name"] == "HarnessLength"
    assert scorecard["summary"]["object_names"] == ["box1", "box2"]
    assert scorecard["summary"]["maximum_length"] == "0.4mm"
    assert scorecard["summary"]["maximum_elements"] == 500
    assert scorecard["summary"]["project_saved"] is False


def test_live_hfss_infinite_sphere_workflow_creates_and_scores_field_setup(tmp_path: Path):
    manager = AssistantWorkflowManager(
        live_manager=_Live(),
        db_path=tmp_path / "infinite-sphere-missions.db",
        template_ids=("hfss_live_infinite_sphere_create",),
        runtime_factory=lambda path: AgentRuntime(SQLiteMissionStore(path)),
    )
    start = manager.preview_start(
        "live-1",
        workflow_id="hfss_live_infinite_sphere_create",
        goal="Create a reviewed HFSS far-field sphere",
        initial_payload={
            "sphere_name": "HarnessSphere",
            "definition": "Theta-Phi",
            "angle1_start": -90,
            "angle1_stop": 90,
            "angle1_step": 5,
            "angle2_start": 0,
            "angle2_stop": 360,
            "angle2_step": 10,
            "units": "deg",
            "polarization": "Slant",
            "polarization_angle": 45,
            "max_samples": 5000,
        },
    )
    started = manager.apply_start(
        "live-1",
        preview_id=start["preview_id"],
        approval_token="approved",
    )
    report = None
    for index in range(3):
        advance = manager.preview_advance("live-1", graph_run_id=started["graph_run_id"])
        if index == 1:
            assert (
                advance["operation_approval_required"]["preview_id"]
                == "infinite-sphere-preview-1"
            )
        report = manager.apply_advance(
            "live-1",
            preview_id=advance["preview_id"],
            approval_token="approved",
            operation_approval_token=(
                "infinite-sphere-approved" if index == 1 else ""
            ),
        )

    assert report is not None and report["status"] == "succeeded"
    assert "infinite-sphere-approved" not in str(report)
    scorecard = report["node_runs"][-1]["output_payload"]
    assert scorecard["status"] == "passed"
    assert scorecard["summary"]["sphere_name"] == "HarnessSphere"
    assert scorecard["summary"]["definition"] == "Theta-Phi"
    assert scorecard["summary"]["sample_count"] == 1369
    assert scorecard["summary"]["polarization"] == "Slant"
    assert scorecard["summary"]["project_saved"] is False


def test_live_aedt_variable_batch_workflow_applies_and_scores_ordered_changes(
    tmp_path: Path,
):
    manager = AssistantWorkflowManager(
        live_manager=_Live(),
        db_path=tmp_path / "variable-batch-missions.db",
        template_ids=("aedt_live_variable_batch_upsert",),
        runtime_factory=lambda path: AgentRuntime(SQLiteMissionStore(path)),
    )
    variables = [
        {"name": "W_main", "expression": "4.3mil"},
        {"name": "W_double", "expression": "2*W_main"},
        {"name": "$BoardScale", "expression": "1.0"},
    ]
    start = manager.preview_start(
        "live-1",
        workflow_id="aedt_live_variable_batch_upsert",
        goal="Create reviewed layout variables in dependency order",
        initial_payload={"product": "layout", "variables": variables},
    )
    started = manager.apply_start(
        "live-1",
        preview_id=start["preview_id"],
        approval_token="approved",
    )
    report = None
    for index in range(3):
        advance = manager.preview_advance("live-1", graph_run_id=started["graph_run_id"])
        if index == 1:
            assert (
                advance["operation_approval_required"]["preview_id"]
                == "variable-batch-preview-1"
            )
        report = manager.apply_advance(
            "live-1",
            preview_id=advance["preview_id"],
            approval_token="approved",
            operation_approval_token=(
                "variable-batch-approved" if index == 1 else ""
            ),
        )

    assert report is not None and report["status"] == "succeeded"
    assert "variable-batch-approved" not in str(report)
    scorecard = report["node_runs"][-1]["output_payload"]
    assert scorecard["status"] == "passed"
    assert scorecard["summary"]["product"] == "layout"
    assert scorecard["summary"]["requested_count"] == 3
    assert scorecard["summary"]["variable_names"] == [
        "W_main",
        "W_double",
        "$BoardScale",
    ]
    assert scorecard["summary"]["project_saved"] is False


def test_live_hfss_surface_boundary_workflow_creates_and_scores_boundary(tmp_path: Path):
    manager = AssistantWorkflowManager(
        live_manager=_Live(),
        db_path=tmp_path / "surface-boundary-missions.db",
        template_ids=("hfss_live_surface_boundary_create",),
        runtime_factory=lambda path: AgentRuntime(SQLiteMissionStore(path)),
    )
    start = manager.preview_start(
        "live-1",
        workflow_id="hfss_live_surface_boundary_create",
        goal="Create a reviewed finite-conductivity coating",
        initial_payload={
            "boundary_kind": "finite_conductivity",
            "boundary_name": "HarnessFinite",
            "face_ids": [101],
            "options": {
                "material_name": "copper",
                "use_thickness": True,
                "thickness": "35um",
                "roughness": "0.5um",
            },
            "max_assignments": 4,
        },
    )
    started = manager.apply_start(
        "live-1",
        preview_id=start["preview_id"],
        approval_token="approved",
    )
    report = None
    for index in range(3):
        advance = manager.preview_advance("live-1", graph_run_id=started["graph_run_id"])
        if index == 1:
            assert (
                advance["operation_approval_required"]["preview_id"]
                == "surface-boundary-preview-1"
            )
        report = manager.apply_advance(
            "live-1",
            preview_id=advance["preview_id"],
            approval_token="approved",
            operation_approval_token=(
                "surface-boundary-approved" if index == 1 else ""
            ),
        )

    assert report is not None and report["status"] == "succeeded"
    assert "surface-boundary-approved" not in str(report)
    scorecard = report["node_runs"][-1]["output_payload"]
    assert scorecard["status"] == "passed"
    assert scorecard["summary"]["boundary_name"] == "HarnessFinite"
    assert scorecard["summary"]["boundary_kind"] == "finite_conductivity"
    assert scorecard["summary"]["face_ids"] == [101]
    assert scorecard["summary"]["options"]["material_name"] == "copper"
    assert scorecard["summary"]["project_saved"] is False


def test_live_hfss_coordinate_system_workflow_creates_restores_and_scores(tmp_path: Path):
    manager = AssistantWorkflowManager(
        live_manager=_Live(),
        db_path=tmp_path / "coordinate-system-missions.db",
        template_ids=("hfss_live_coordinate_system_create",),
        runtime_factory=lambda path: AgentRuntime(SQLiteMissionStore(path)),
    )
    start = manager.preview_start(
        "live-1",
        workflow_id="hfss_live_coordinate_system_create",
        goal="Create a reviewed relative coordinate system",
        initial_payload={
            "coordinate_system_name": "HarnessCS",
            "reference_coordinate_system": "ParentCS",
            "origin": ["OX", "2mm", 3],
            "x_axis": [1, 1, 0],
            "y_axis": [0, 0, 2],
        },
    )
    started = manager.apply_start(
        "live-1",
        preview_id=start["preview_id"],
        approval_token="approved",
    )
    report = None
    for index in range(3):
        advance = manager.preview_advance(
            "live-1",
            graph_run_id=started["graph_run_id"],
        )
        if index == 1:
            assert (
                advance["operation_approval_required"]["preview_id"]
                == "coordinate-system-preview-1"
            )
        report = manager.apply_advance(
            "live-1",
            preview_id=advance["preview_id"],
            approval_token="approved",
            operation_approval_token=(
                "coordinate-system-approved" if index == 1 else ""
            ),
        )

    assert report is not None and report["status"] == "succeeded"
    assert "coordinate-system-approved" not in str(report)
    scorecard = report["node_runs"][-1]["output_payload"]
    assert scorecard["status"] == "passed"
    assert scorecard["summary"]["coordinate_system_name"] == "HarnessCS"
    assert scorecard["summary"]["reference_coordinate_system"] == "ParentCS"
    assert scorecard["summary"]["active_coordinate_system_restored"] is True
    assert scorecard["summary"]["project_saved"] is False


def test_live_hfss_port_workflow_creates_and_scores_typed_port(tmp_path: Path):
    manager = AssistantWorkflowManager(
        live_manager=_Live(),
        db_path=tmp_path / "typed-port-missions.db",
        template_ids=("hfss_live_port_create",),
        runtime_factory=lambda path: AgentRuntime(SQLiteMissionStore(path)),
    )
    start = manager.preview_start(
        "live-1",
        workflow_id="hfss_live_port_create",
        goal="Create a reviewed two-mode wave port",
        initial_payload={
            "boundary_kind": "wave_port",
            "boundary_name": "HarnessWave",
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
    started = manager.apply_start(
        "live-1",
        preview_id=start["preview_id"],
        approval_token="approved",
    )
    report = None
    for index in range(3):
        advance = manager.preview_advance("live-1", graph_run_id=started["graph_run_id"])
        if index == 1:
            assert advance["operation_approval_required"]["preview_id"] == "typed-port-preview-1"
        report = manager.apply_advance(
            "live-1",
            preview_id=advance["preview_id"],
            approval_token="approved",
            operation_approval_token=("typed-port-approved" if index == 1 else ""),
        )

    assert report is not None and report["status"] == "succeeded"
    assert "typed-port-approved" not in str(report)
    scorecard = report["node_runs"][-1]["output_payload"]
    assert scorecard["status"] == "passed"
    assert scorecard["summary"]["port_name"] == "HarnessWave"
    assert scorecard["summary"]["port_kind"] == "wave_port"
    assert scorecard["summary"]["face_ids"] == [101]
    assert scorecard["summary"]["options"]["mode_count"] == 2
    assert scorecard["summary"]["project_saved"] is False


def test_live_hfss_geometry_boundary_workflow_is_atomic_and_scored(tmp_path: Path):
    manager = AssistantWorkflowManager(
        live_manager=_Live(),
        db_path=tmp_path / "geometry-boundary-missions.db",
        template_ids=("hfss_live_geometry_boundary_create",),
        runtime_factory=lambda path: AgentRuntime(SQLiteMissionStore(path)),
    )
    primitives = [
        {
            "kind": "box",
            "name": "PortBody",
            "origin": [0, 0, 0],
            "size": [10, 5, 1],
        },
        {
            "kind": "region",
            "name": "AirRegion",
            "padding": "5mm",
            "padding_type": "Absolute Offset",
        },
    ]
    boundaries = [
        {
            "boundary_kind": "wave_port",
            "boundary_name": "P1",
            "assignment_object": "PortBody",
            "face_selector": "x_min",
        },
        {
            "boundary_kind": "radiation",
            "boundary_name": "Radiation1",
            "assignment_object": "AirRegion",
            "face_selector": "all_faces",
        },
    ]
    start = manager.preview_start(
        "live-1",
        workflow_id="hfss_live_geometry_boundary_create",
        goal="Atomically create reviewed HFSS geometry and boundaries",
        initial_payload={"primitives": primitives, "boundaries": boundaries},
    )
    started = manager.apply_start(
        "live-1",
        preview_id=start["preview_id"],
        approval_token="approved",
    )
    report = None
    for index in range(3):
        advance = manager.preview_advance("live-1", graph_run_id=started["graph_run_id"])
        if index == 1:
            assert (
                advance["operation_approval_required"]["preview_id"]
                == "geometry-boundary-preview-1"
            )
        report = manager.apply_advance(
            "live-1",
            preview_id=advance["preview_id"],
            approval_token="approved",
            operation_approval_token=(
                "geometry-boundary-approved" if index == 1 else ""
            ),
        )

    assert report is not None and report["status"] == "succeeded"
    assert "geometry-boundary-approved" not in str(report)
    scorecard = report["node_runs"][-1]["output_payload"]
    assert scorecard["status"] == "passed"
    assert scorecard["summary"]["created_object_names"] == ["PortBody", "AirRegion"]
    assert scorecard["summary"]["created_boundary_names"] == ["P1", "Radiation1"]
    assert scorecard["summary"]["project_saved"] is False


def test_live_hfss_setup_sweep_workflow_is_atomic_and_scored(tmp_path: Path):
    manager = AssistantWorkflowManager(
        live_manager=_Live(),
        db_path=tmp_path / "setup-sweep-missions.db",
        template_ids=("hfss_live_setup_sweep_create",),
        runtime_factory=lambda path: AgentRuntime(SQLiteMissionStore(path)),
    )
    setup = {
        "name": "AtomicSetup",
        "type": "HFSSDriven",
        "properties": {"Frequency": "10GHz", "MaximumPasses": 3},
    }
    sweep = {
        "name": "AtomicSweep",
        "range_type": "LinearCount",
        "sweep_type": "Interpolating",
        "unit": "GHz",
        "start_frequency": 1,
        "stop_frequency": 20,
        "count": 101,
    }
    start = manager.preview_start(
        "live-1",
        workflow_id="hfss_live_setup_sweep_create",
        goal="Atomically create a reviewed HFSS setup and sweep",
        initial_payload={"setup": setup, "sweep": sweep},
    )
    started = manager.apply_start(
        "live-1",
        preview_id=start["preview_id"],
        approval_token="approved",
    )
    report = None
    for index in range(3):
        advance = manager.preview_advance("live-1", graph_run_id=started["graph_run_id"])
        if index == 1:
            assert (
                advance["operation_approval_required"]["preview_id"]
                == "setup-sweep-preview-1"
            )
        report = manager.apply_advance(
            "live-1",
            preview_id=advance["preview_id"],
            approval_token="approved",
            operation_approval_token="setup-sweep-approved" if index == 1 else "",
        )

    assert report is not None and report["status"] == "succeeded"
    assert "setup-sweep-approved" not in str(report)
    scorecard = report["node_runs"][-1]["output_payload"]
    assert scorecard["status"] == "passed"
    assert scorecard["summary"]["created_setup_name"] == "AtomicSetup"
    assert scorecard["summary"]["created_sweep_name"] == "AtomicSweep"
    assert scorecard["summary"]["project_saved"] is False


def test_live_layout_audit_workflow_reuses_bound_session_for_graph_handlers(tmp_path: Path):
    manager = AssistantWorkflowManager(
        live_manager=_Live(),
        db_path=tmp_path / "live-missions.db",
        template_ids=("layout_live_audit",),
        runtime_factory=lambda path: AgentRuntime(SQLiteMissionStore(path)),
    )
    start_preview = manager.preview_start(
        "live-1",
        workflow_id="layout_live_audit",
        goal="Audit the active layout",
        initial_payload={"selector": {"nets": ["N1"]}},
    )
    started = manager.apply_start(
        "live-1",
        preview_id=start_preview["preview_id"],
        approval_token="approved",
    )

    for _ in range(2):
        advance_preview = manager.preview_advance(
            "live-1",
            graph_run_id=started["graph_run_id"],
        )
        report = manager.apply_advance(
            "live-1",
            preview_id=advance_preview["preview_id"],
            approval_token="approved",
        )

    assert report["status"] == "succeeded"
    assert "_assistant_live" not in report["graph_run"]["initial_payload"]
    assert "_assistant_live" not in report["node_runs"][0]["input_payload"]
    assert report["node_runs"][0]["output_payload"]["live_session_reused"] is True
    assert report["node_runs"][1]["output_payload"]["summary"]["path_count"] == 2
    assert report["node_runs"][1]["output_payload"]["summary"]["stackup_layer_count"] == 3
    assert report["node_runs"][1]["output_payload"]["summary"]["padstack_count"] == 1
    assert report["node_runs"][1]["output_payload"]["summary"]["connectivity_net_count"] == 1
    assert report["node_runs"][1]["output_payload"]["summary"]["component_count"] == 2
    assert report["node_runs"][1]["output_payload"]["summary"]["pin_count"] == 4
    assert report["node_runs"][1]["output_payload"]["summary"]["via_count"] == 3

    with pytest.raises(ValueError, match="reserved server-owned field"):
        manager.preview_start(
            "live-1",
            workflow_id="layout_live_audit",
            goal="Try to forge a binding",
            initial_payload={"_assistant_live": {"live_session_id": "forged"}},
        )


def test_live_component_port_workflow_reuses_candidates_and_keeps_token_out_of_graph_state(
    tmp_path: Path,
):
    manager = AssistantWorkflowManager(
        live_manager=_Live(),
        db_path=tmp_path / "port-missions.db",
        template_ids=("layout_live_component_ports_create",),
        runtime_factory=lambda path: AgentRuntime(SQLiteMissionStore(path)),
    )
    start = manager.preview_start(
        "live-1",
        workflow_id="layout_live_component_ports_create",
        goal="Create reviewed component ports",
        initial_payload={
            "component_name": "U1",
            "signal_nets": ["N1", "N2"],
            "reference_nets": ["GND"],
        },
    )
    started = manager.apply_start(
        "live-1",
        preview_id=start["preview_id"],
        approval_token="approved",
    )
    report = None
    for index in range(5):
        advance = manager.preview_advance("live-1", graph_run_id=started["graph_run_id"])
        if index == 3:
            assert advance["operation_approval_required"]["preview_id"] == "port-preview-1"
        report = manager.apply_advance(
            "live-1",
            preview_id=advance["preview_id"],
            approval_token="approved",
            operation_approval_token="port-approved" if index == 3 else "",
        )

    assert report is not None and report["status"] == "succeeded"
    assert "port-approved" not in str(report)
    scorecard = report["node_runs"][-1]["output_payload"]
    assert scorecard["status"] == "passed"
    assert scorecard["summary"]["component_name"] == "U1"
    assert scorecard["summary"]["created_ports"] == ["Port_U1-1", "Port_U1-2"]


def test_live_uniform_edge_port_workflow_reuses_old_selector_and_verifies_batch(
    tmp_path: Path,
):
    manager = AssistantWorkflowManager(
        live_manager=_Live(),
        db_path=tmp_path / "edge-port-missions.db",
        template_ids=("layout_live_uniform_edge_ports_create",),
        runtime_factory=lambda path: AgentRuntime(SQLiteMissionStore(path)),
    )
    start = manager.preview_start(
        "live-1",
        workflow_id="layout_live_uniform_edge_ports_create",
        goal="Create reviewed uniform line edge ports",
        initial_payload={
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
            "port_type": "circuit",
        },
    )
    started = manager.apply_start(
        "live-1",
        preview_id=start["preview_id"],
        approval_token="approved",
    )
    report = None
    for index in range(5):
        advance = manager.preview_advance("live-1", graph_run_id=started["graph_run_id"])
        if index == 3:
            assert advance["operation_approval_required"]["preview_id"] == "edge-port-preview-1"
        report = manager.apply_advance(
            "live-1",
            preview_id=advance["preview_id"],
            approval_token="approved",
            operation_approval_token="edge-port-approved" if index == 3 else "",
        )

    assert report is not None and report["status"] == "succeeded"
    assert "edge-port-approved" not in str(report)
    scorecard = report["node_runs"][-1]["output_payload"]
    assert scorecard["status"] == "passed"
    assert scorecard["summary"]["created_ports"] == ["EdgePort_1", "EdgePort_2"]
    assert [
        item["request"]["primitive_name"] for item in scorecard["summary"]["edge_targets"]
    ] == ["line1", "line2"]


def test_live_width_workflow_keeps_operation_token_out_of_graph_state(tmp_path: Path):
    manager = AssistantWorkflowManager(
        live_manager=_Live(),
        db_path=tmp_path / "width-missions.db",
        template_ids=("layout_live_parameterize_width",),
        runtime_factory=lambda path: AgentRuntime(SQLiteMissionStore(path)),
    )
    start = manager.preview_start(
        "live-1",
        workflow_id="layout_live_parameterize_width",
        goal="Parameterize matching path widths",
        initial_payload={
            "selector": {"target_width": "4.3mil"},
            "variable_name": "W_line",
            "variable_value": "4.3mil",
        },
    )
    started = manager.apply_start(
        "live-1",
        preview_id=start["preview_id"],
        approval_token="approved",
    )
    report = None
    for index in range(4):
        advance = manager.preview_advance("live-1", graph_run_id=started["graph_run_id"])
        if index == 2:
            assert advance["operation_approval_required"]["preview_id"] == "width-preview-1"
            with pytest.raises(Exception, match="nested live operation preview"):
                manager.apply_advance(
                    "live-1",
                    preview_id=advance["preview_id"],
                    approval_token="approved",
                )
        report = manager.apply_advance(
            "live-1",
            preview_id=advance["preview_id"],
            approval_token="approved",
            operation_approval_token="operation-approved" if index == 2 else "",
        )

    assert report is not None and report["status"] == "succeeded"
    serialized = str(report)
    assert "operation-approved" not in serialized
    assert report["node_runs"][-1]["output_payload"]["summary"]["verified_count"] == 2


def test_live_layout_solve_workflow_validates_setup_and_starts_non_blocking(tmp_path: Path):
    manager = AssistantWorkflowManager(
        live_manager=_Live(),
        db_path=tmp_path / "solve-missions.db",
        template_ids=("layout_live_solve_start",),
        runtime_factory=lambda path: AgentRuntime(SQLiteMissionStore(path)),
    )
    start = manager.preview_start(
        "live-1",
        workflow_id="layout_live_solve_start",
        goal="Start the approved live layout solve",
        initial_payload={
            "setup_name": "SetupL",
            "sweep_name": "Sweep1",
            "cores": 4,
            "tasks": 1,
            "gpus": 0,
        },
    )
    started = manager.apply_start(
        "live-1",
        preview_id=start["preview_id"],
        approval_token="approved",
    )
    report = None
    for index in range(4):
        advance = manager.preview_advance("live-1", graph_run_id=started["graph_run_id"])
        report = manager.apply_advance(
            "live-1",
            preview_id=advance["preview_id"],
            approval_token="approved",
            operation_approval_token="solve-approved" if index == 2 else "",
        )

    assert report is not None and report["status"] == "succeeded"
    scorecard = report["node_runs"][-1]["output_payload"]
    assert scorecard["status"] == "passed"
    assert scorecard["summary"]["run_id"] == "run-1"
    assert "solve-approved" not in str(report)


def test_live_layout_monitor_workflow_uses_bounded_graph_loop(tmp_path: Path):
    live = _Live()
    live.analysis_statuses = [
        {"product": "layout", "running": True, "setup_name": "SetupL", "latest_run": {"run_id": "run-1", "state": "running"}},
        {"product": "layout", "running": True, "setup_name": "SetupL", "latest_run": {"run_id": "run-1", "state": "running"}},
        {
            "product": "layout",
            "running": False,
            "setup_name": "SetupL",
            "latest_run": {
                "run_id": "run-1",
                "state": "not_running",
                "solution_evidence": {
                    "solve_success_verified": True,
                    "result_freshness_verified": True,
                    "verification_reasons": ["fresh_solution_artifacts_verified"],
                },
            },
        },
    ]
    manager = AssistantWorkflowManager(
        live_manager=live,
        db_path=tmp_path / "monitor-missions.db",
        template_ids=("layout_live_solve_monitor",),
        runtime_factory=lambda path: AgentRuntime(SQLiteMissionStore(path)),
    )
    start = manager.preview_start(
        "live-1",
        workflow_id="layout_live_solve_monitor",
        goal="Monitor the approved live layout solve",
        initial_payload={"setup_name": "SetupL"},
        max_steps=16,
    )
    started = manager.apply_start("live-1", preview_id=start["preview_id"], approval_token="approved")

    report = None
    for _ in range(5):
        advance = manager.preview_advance("live-1", graph_run_id=started["graph_run_id"])
        report = manager.apply_advance(
            "live-1",
            preview_id=advance["preview_id"],
            approval_token="approved",
        )

    assert report is not None and report["status"] == "succeeded"
    poll_runs = [item for item in report["node_runs"] if item["node_id"] == "poll_analysis"]
    assert len(poll_runs) == 3
    assert [item["edge_decision"] for item in poll_runs] == ["running", "running", "stopped"]
    assert all("_handoffs" not in item["output_payload"] for item in poll_runs)
    scorecard = report["node_runs"][-1]["output_payload"]
    assert scorecard["status"] == "passed"
    assert scorecard["summary"]["poll_count"] == 3
    assert scorecard["summary"]["solve_running_observed"] is True
    assert scorecard["summary"]["solve_success_verified"] is True
    assert scorecard["summary"]["result_freshness_verified"] is True
    assert scorecard["summary"]["solution_verification_reasons"] == [
        "fresh_solution_artifacts_verified"
    ]


def test_live_layout_results_export_workflow_writes_verified_artifacts(tmp_path: Path):
    live = _Live()
    live.export_root = tmp_path / "exports"
    live.analysis_statuses = [
        {"product": "layout", "running": False, "setup_name": "SetupL", "latest_run": {"run_id": "run-1", "state": "not_running"}}
    ]
    manager = AssistantWorkflowManager(
        live_manager=live,
        db_path=tmp_path / "export-missions.db",
        template_ids=("layout_live_results_export",),
        runtime_factory=lambda path: AgentRuntime(SQLiteMissionStore(path)),
    )
    start = manager.preview_start(
        "live-1",
        workflow_id="layout_live_results_export",
        goal="Export approved live layout Touchstone evidence",
        initial_payload={
            "export_kind": "touchstone",
            "setup_name": "SetupL",
            "sweep_name": "Sweep1",
        },
    )
    started = manager.apply_start("live-1", preview_id=start["preview_id"], approval_token="approved")

    report = None
    for index in range(4):
        advance = manager.preview_advance("live-1", graph_run_id=started["graph_run_id"])
        if index == 2:
            assert advance["operation_approval_required"]["preview_id"] == "export-preview-1"
        report = manager.apply_advance(
            "live-1",
            preview_id=advance["preview_id"],
            approval_token="approved",
            operation_approval_token="export-approved" if index == 2 else "",
        )

    assert report is not None and report["status"] == "succeeded"
    scorecard = report["node_runs"][-1]
    assert scorecard["output_payload"]["status"] == "passed"
    assert scorecard["output_payload"]["summary"]["artifact_path"].endswith("SetupL.s2p")
    assert len(scorecard["artifact_refs"]) == 2
    assert "export-approved" not in str(report)


def test_live_layout_touchstone_score_uses_verified_port_mapping(tmp_path: Path):
    live = _Live()
    live.export_root = tmp_path / "score-exports"
    live.analysis_statuses = [
        {"product": "layout", "running": False, "setup_name": "SetupL", "latest_run": {"run_id": "run-1", "state": "not_running"}}
    ]
    manager = AssistantWorkflowManager(
        live_manager=live,
        db_path=tmp_path / "score-missions.db",
        template_ids=("layout_live_touchstone_score",),
        runtime_factory=lambda path: AgentRuntime(SQLiteMissionStore(path)),
    )
    start = manager.preview_start(
        "live-1",
        workflow_id="layout_live_touchstone_score",
        goal="Export and score an explicitly mapped layout channel",
        initial_payload={
            **_mapped_score_payload(),
        },
    )
    started = manager.apply_start("live-1", preview_id=start["preview_id"], approval_token="approved")

    report = None
    for index in range(4):
        advance = manager.preview_advance("live-1", graph_run_id=started["graph_run_id"])
        if index == 2:
            assert advance["operation_approval_required"]["preview_id"] == "export-preview-1"
        report = manager.apply_advance(
            "live-1",
            preview_id=advance["preview_id"],
            approval_token="approved",
            operation_approval_token="export-approved" if index == 2 else "",
        )

    assert report is not None and report["status"] == "succeeded"
    scorecard = report["node_runs"][-1]
    assert scorecard["output_payload"]["status"] == "passed"
    assert scorecard["output_payload"]["summary"]["return_loss_trace"] == "S(P1,P1)"
    assert scorecard["output_payload"]["summary"]["insertion_loss_trace"] == "S(P2,P1)"
    assert scorecard["output_payload"]["summary"]["tdr_evaluated"] is False
    assert len(scorecard["artifact_refs"]) == 3
    assert Path(scorecard["output_payload"]["summary"]["score_evidence_path"]).is_file()
    assert "export-approved" not in str(report)


def test_touchstone_score_can_require_active_aedt_differential_pairs(tmp_path: Path):
    class _DifferentialLive(_Live):
        def setup_inventory(self, session_id: str, **kwargs) -> dict:
            inventory = super().setup_inventory(session_id, **kwargs)
            return {**inventory, "ports": ["TX_P", "TX_N", "RX_P", "RX_N"]}

        def layout_technology_inventory(self, session_id: str, **kwargs) -> dict:
            inventory = super().layout_technology_inventory(session_id, **kwargs)
            return {
                **inventory,
                "ports": ["TX_P", "TX_N", "RX_P", "RX_N"],
                "differential_pairs": [
                    {
                        "positive_terminal": "TX_P",
                        "negative_terminal": "TX_N",
                        "active": True,
                        "differential_mode": "DiffTX",
                    },
                    {
                        "positive_terminal": "RX_P",
                        "negative_terminal": "RX_N",
                        "active": True,
                        "differential_mode": "DiffRX",
                    },
                ],
            }

    live = _DifferentialLive()
    live.analysis_statuses = [
        {
            "product": "layout",
            "running": False,
            "setup_name": "SetupL",
            "latest_run": {"run_id": "run-1", "state": "not_running"},
        }
    ]
    manager = AssistantWorkflowManager(
        live_manager=live,
        db_path=tmp_path / "differential-validation.db",
        template_ids=("layout_live_touchstone_score",),
        runtime_factory=lambda path: AgentRuntime(SQLiteMissionStore(path)),
    )
    preview = manager.preview_start(
        "live-1",
        workflow_id="layout_live_touchstone_score",
        goal="Require the exact active AEDT differential pairs",
        initial_payload={
            "setup_name": "SetupL",
            "sweep_name": "Sweep1",
            "expected_port_order": ["TX_P", "TX_N", "RX_P", "RX_N"],
            "sparameter_mode": "differential",
            "source_ports": ["TX_P", "TX_N"],
            "destination_ports": ["RX_P", "RX_N"],
            "frequency_start_ghz": 1.0,
            "frequency_stop_ghz": 2.0,
            "rl_target_db": -15.0,
            "insertion_loss_min_db": -4.0,
            "reference_impedance_ohm": 100.0,
            "require_defined_differential_pairs": True,
        },
    )
    started = manager.apply_start(
        "live-1",
        preview_id=preview["preview_id"],
        approval_token="approved",
    )
    advance = manager.preview_advance("live-1", graph_run_id=started["graph_run_id"])
    report = manager.apply_advance(
        "live-1",
        preview_id=advance["preview_id"],
        approval_token="approved",
    )

    validation = report["node_runs"][0]["output_payload"]["score_spec"][
        "differential_pair_validation"
    ]
    assert validation == {
        "status": "verified",
        "source_pair": "defined_active",
        "source_differential_mode": "DiffTX",
        "destination_pair": "defined_active",
        "destination_differential_mode": "DiffRX",
        "all_pairs_defined_and_active": True,
    }


def test_live_layout_solve_touchstone_score_composes_two_operation_approvals(tmp_path: Path):
    live = _Live()
    live.export_root = tmp_path / "solve-score-exports"
    live.analysis_statuses = [
        {"product": "layout", "running": False, "setup_name": "SetupL", "latest_run": {"run_id": "run-1", "state": "submitted"}},
        {"product": "layout", "running": True, "setup_name": "SetupL", "latest_run": {"run_id": "run-1", "state": "running"}},
        {
            "product": "layout",
            "running": False,
            "setup_name": "SetupL",
            "latest_run": {
                "run_id": "run-1",
                "state": "not_running",
                "solution_evidence": {
                    "solve_success_verified": True,
                    "result_freshness_verified": True,
                    "verification_reasons": ["fresh_solution_artifacts_verified"],
                },
            },
        },
        {
            "product": "layout",
            "running": False,
            "setup_name": "SetupL",
            "latest_run": {
                "run_id": "run-1",
                "state": "not_running",
                "solution_evidence": {
                    "solve_success_verified": True,
                    "result_freshness_verified": True,
                    "verification_reasons": ["fresh_solution_artifacts_verified"],
                },
            },
        },
    ]
    manager = AssistantWorkflowManager(
        live_manager=live,
        db_path=tmp_path / "solve-score-missions.db",
        template_ids=("layout_live_solve_touchstone_score",),
        runtime_factory=lambda path: AgentRuntime(SQLiteMissionStore(path)),
    )
    start = manager.preview_start(
        "live-1",
        workflow_id="layout_live_solve_touchstone_score",
        goal="Solve and score one explicitly mapped live layout channel",
        initial_payload={**_mapped_score_payload(), "cores": 4, "tasks": 1, "gpus": 0},
        max_steps=20,
    )
    started = manager.apply_start("live-1", preview_id=start["preview_id"], approval_token="approved")

    report = None
    for index in range(10):
        advance = manager.preview_advance("live-1", graph_run_id=started["graph_run_id"])
        operation_token = ""
        if index == 2:
            assert advance["operation_approval_required"]["preview_id"] == "solve-preview-1"
            operation_token = "solve-approved"
        elif index == 8:
            assert advance["operation_approval_required"]["preview_id"] == "export-preview-1"
            operation_token = "export-approved"
        report = manager.apply_advance(
            "live-1",
            preview_id=advance["preview_id"],
            approval_token="approved",
            operation_approval_token=operation_token,
        )

    assert report is not None and report["status"] == "succeeded"
    assert [item["node_id"] for item in report["node_runs"]] == [
        "validate_setup",
        "preview_analysis",
        "apply_analysis",
        "poll_analysis",
        "poll_analysis",
        "poll_analysis",
        "validate_score_request",
        "preview_export",
        "apply_export",
        "score_touchstone",
    ]
    summary = report["node_runs"][-1]["output_payload"]["summary"]
    assert summary["score_status"] == "pass"
    assert summary["solve_run_id"] == "run-1"
    assert summary["solve_submission_verified"] is True
    assert summary["solve_success_verified"] is True
    assert summary["result_freshness_verified"] is True
    assert summary["solution_verification_reasons"] == ["fresh_solution_artifacts_verified"]
    assert summary["solve_running_observed"] is True
    assert summary["poll_count"] == 3
    assert summary["parameterization_verified"] is False
    assert "solve-approved" not in str(report)
    assert "export-approved" not in str(report)


def test_live_layout_parameterize_solve_score_composes_three_operation_approvals(tmp_path: Path):
    live = _Live()
    live.export_root = tmp_path / "parameterize-solve-score-exports"
    live.analysis_statuses = [
        {"product": "layout", "running": False, "setup_name": "SetupL", "latest_run": {"run_id": "run-1", "state": "submitted"}},
        {"product": "layout", "running": True, "setup_name": "SetupL", "latest_run": {"run_id": "run-1", "state": "running"}},
        {"product": "layout", "running": False, "setup_name": "SetupL", "latest_run": {"run_id": "run-1", "state": "not_running"}},
        {"product": "layout", "running": False, "setup_name": "SetupL", "latest_run": {"run_id": "run-1", "state": "not_running"}},
    ]
    manager = AssistantWorkflowManager(
        live_manager=live,
        db_path=tmp_path / "parameterize-solve-score-missions.db",
        template_ids=("layout_live_parameterize_solve_touchstone_score",),
        runtime_factory=lambda path: AgentRuntime(SQLiteMissionStore(path)),
    )
    start = manager.preview_start(
        "live-1",
        workflow_id="layout_live_parameterize_solve_touchstone_score",
        goal="Parameterize matching lines, solve, and score the mapped channel",
        initial_payload={
            **_mapped_score_payload(),
            "selector": {"target_width": "4.3mil"},
            "variable_name": "W_line",
            "variable_value": "4.3mil",
            "cores": 4,
            "tasks": 1,
            "gpus": 0,
        },
        max_steps=24,
    )
    started = manager.apply_start("live-1", preview_id=start["preview_id"], approval_token="approved")

    report = None
    for index in range(14):
        advance = manager.preview_advance("live-1", graph_run_id=started["graph_run_id"])
        operation_token = ""
        if index == 2:
            assert advance["operation_approval_required"]["preview_id"] == "width-preview-1"
            operation_token = "operation-approved"
        elif index == 6:
            assert advance["operation_approval_required"]["preview_id"] == "solve-preview-1"
            operation_token = "solve-approved"
        elif index == 12:
            assert advance["operation_approval_required"]["preview_id"] == "export-preview-1"
            operation_token = "export-approved"
        report = manager.apply_advance(
            "live-1",
            preview_id=advance["preview_id"],
            approval_token="approved",
            operation_approval_token=operation_token,
        )

    assert report is not None and report["status"] == "succeeded"
    summary = report["node_runs"][-1]["output_payload"]["summary"]
    assert summary["score_status"] == "pass"
    assert summary["solve_submission_verified"] is True
    assert summary["solve_success_verified"] is False
    assert summary["result_freshness_verified"] is False
    assert summary["parameterization_verified"] is True
    assert summary["parameterized_target_count"] == 2
    serialized = str(report)
    assert "operation-approved" not in serialized
    assert "solve-approved" not in serialized
    assert "export-approved" not in serialized


def test_parameterize_solve_score_stops_before_solve_when_readback_fails(tmp_path: Path):
    class _ReadbackMismatchLive(_Live):
        def apply_layout_width(self, session_id: str, *, preview_id: str, approval_token: str) -> dict:
            result = super().apply_layout_width(
                session_id,
                preview_id=preview_id,
                approval_token=approval_token,
            )
            return {**result, "verified_count": 1}

        def preview_hfss_analysis_start(self, session_id: str, **kwargs) -> dict:
            raise AssertionError("solve preview must not run after failed width readback")

    live = _ReadbackMismatchLive()
    manager = AssistantWorkflowManager(
        live_manager=live,
        db_path=tmp_path / "readback-stop-missions.db",
        template_ids=("layout_live_parameterize_solve_touchstone_score",),
        runtime_factory=lambda path: AgentRuntime(SQLiteMissionStore(path)),
    )
    start = manager.preview_start(
        "live-1",
        workflow_id="layout_live_parameterize_solve_touchstone_score",
        goal="Do not solve unless parameterization readback passes",
        initial_payload={
            **_mapped_score_payload(),
            "selector": {"target_width": "4.3mil"},
            "variable_name": "W_line",
            "variable_value": "4.3mil",
        },
    )
    started = manager.apply_start("live-1", preview_id=start["preview_id"], approval_token="approved")

    report = None
    for index in range(4):
        advance = manager.preview_advance("live-1", graph_run_id=started["graph_run_id"])
        report = manager.apply_advance(
            "live-1",
            preview_id=advance["preview_id"],
            approval_token="approved",
            operation_approval_token="operation-approved" if index == 2 else "",
        )

    assert report is not None and report["status"] == "succeeded"
    assert [item["node_id"] for item in report["node_runs"]] == [
        "select_paths",
        "preview_parameterization",
        "apply_parameterization",
        "verify_parameterization",
    ]
    assert report["node_runs"][-1]["edge_decision"] == "failed"
    assert report["node_runs"][-1]["output_payload"]["status"] == "failed"


def test_live_layout_solve_export_workflow_uses_two_independent_operation_approvals(tmp_path: Path):
    live = _Live()
    live.export_root = tmp_path / "combined-exports"
    live.analysis_statuses = [
        {"product": "layout", "running": False, "setup_name": "SetupL", "latest_run": {"run_id": "run-1", "state": "submitted"}},
        {"product": "layout", "running": True, "setup_name": "SetupL", "latest_run": {"run_id": "run-1", "state": "running"}},
        {"product": "layout", "running": False, "setup_name": "SetupL", "latest_run": {"run_id": "run-1", "state": "not_running"}},
        {"product": "layout", "running": False, "setup_name": "SetupL", "latest_run": {"run_id": "run-1", "state": "not_running"}},
    ]
    manager = AssistantWorkflowManager(
        live_manager=live,
        db_path=tmp_path / "combined-missions.db",
        template_ids=("layout_live_solve_export",),
        runtime_factory=lambda path: AgentRuntime(SQLiteMissionStore(path)),
    )
    start = manager.preview_start(
        "live-1",
        workflow_id="layout_live_solve_export",
        goal="Solve, monitor, and export one live layout setup",
        initial_payload={
            "setup_name": "SetupL",
            "sweep_name": "Sweep1",
            "export_kind": "touchstone",
            "artifact_name": "combined-network",
            "cores": 4,
            "tasks": 1,
            "gpus": 0,
        },
        max_steps=20,
    )
    started = manager.apply_start("live-1", preview_id=start["preview_id"], approval_token="approved")

    report = None
    for index in range(10):
        advance = manager.preview_advance("live-1", graph_run_id=started["graph_run_id"])
        operation_token = ""
        if index == 2:
            assert advance["operation_approval_required"]["preview_id"] == "solve-preview-1"
            operation_token = "solve-approved"
        elif index == 8:
            assert advance["operation_approval_required"]["preview_id"] == "export-preview-1"
            with pytest.raises(Exception, match="nested live operation preview"):
                manager.apply_advance(
                    "live-1",
                    preview_id=advance["preview_id"],
                    approval_token="approved",
                )
            operation_token = "export-approved"
        report = manager.apply_advance(
            "live-1",
            preview_id=advance["preview_id"],
            approval_token="approved",
            operation_approval_token=operation_token,
        )

    assert report is not None and report["status"] == "succeeded"
    assert [item["node_id"] for item in report["node_runs"]] == [
        "validate_setup",
        "preview_analysis",
        "apply_analysis",
        "poll_analysis",
        "poll_analysis",
        "poll_analysis",
        "validate_export",
        "preview_export",
        "apply_export",
        "verify_export",
    ]
    scorecard = report["node_runs"][-1]["output_payload"]
    assert scorecard["status"] == "passed"
    assert scorecard["summary"]["solve_run_id"] == "run-1"
    assert scorecard["summary"]["solve_submission_verified"] is True
    assert scorecard["summary"]["result_export_verified"] is True
    assert scorecard["summary"]["poll_count"] == 3
    assert scorecard["summary"]["solve_running_observed"] is True
    serialized = str(report)
    assert "solve-approved" not in serialized
    assert "export-approved" not in serialized
