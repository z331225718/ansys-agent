from __future__ import annotations

from typing import Any

from aedt_agent.live.target import AedtTarget


class HfssGeometryHarness:
    """Domain harness for HFSS 3D geometry operations (create, move, rotate, subtract, boundary)."""

    @staticmethod
    def preview_create(backend: Any, target: AedtTarget, args: dict[str, Any]) -> dict[str, Any]:
        return backend._hfss_geometry_create_preview(target, args)

    @staticmethod
    def apply_create(backend: Any, target: AedtTarget, args: dict[str, Any]) -> dict[str, Any]:
        return backend._hfss_geometry_create_apply(target, args)

    @staticmethod
    def preview_move(backend: Any, target: AedtTarget, args: dict[str, Any]) -> dict[str, Any]:
        return backend._hfss_geometry_move_preview(target, args)

    @staticmethod
    def apply_move(backend: Any, target: AedtTarget, args: dict[str, Any]) -> dict[str, Any]:
        return backend._hfss_geometry_move_apply(target, args)

    @staticmethod
    def preview_rotate(backend: Any, target: AedtTarget, args: dict[str, Any]) -> dict[str, Any]:
        return backend._hfss_geometry_rotate_preview(target, args)

    @staticmethod
    def apply_rotate(backend: Any, target: AedtTarget, args: dict[str, Any]) -> dict[str, Any]:
        return backend._hfss_geometry_rotate_apply(target, args)

    @staticmethod
    def preview_antipad_subtract(backend: Any, target: AedtTarget, args: dict[str, Any]) -> dict[str, Any]:
        return backend._hfss_antipad_subtract_preview(target, args)

    @staticmethod
    def apply_antipad_subtract(backend: Any, target: AedtTarget, args: dict[str, Any]) -> dict[str, Any]:
        return backend._hfss_antipad_subtract_apply(target, args)

    @staticmethod
    def preview_boundary_create(backend: Any, target: AedtTarget, args: dict[str, Any]) -> dict[str, Any]:
        return backend._hfss_geometry_boundary_create_preview(target, args)

    @staticmethod
    def apply_boundary_create(backend: Any, target: AedtTarget, args: dict[str, Any]) -> dict[str, Any]:
        return backend._hfss_geometry_boundary_create_apply(target, args)
