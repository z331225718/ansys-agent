from __future__ import annotations

from typing import Any

from aedt_agent.live.target import AedtTarget


class HfssMaterialHarness:
    """Domain harness for HFSS material operations (create, update, delete, assign)."""

    @staticmethod
    def preview_create(backend: Any, target: AedtTarget, args: dict[str, Any]) -> dict[str, Any]:
        return backend._hfss_material_create_preview(target, args)

    @staticmethod
    def apply_create(backend: Any, target: AedtTarget, args: dict[str, Any]) -> dict[str, Any]:
        return backend._hfss_material_create_apply(target, args)

    @staticmethod
    def preview_update(backend: Any, target: AedtTarget, args: dict[str, Any]) -> dict[str, Any]:
        return backend._hfss_material_update_preview(target, args)

    @staticmethod
    def apply_update(backend: Any, target: AedtTarget, args: dict[str, Any]) -> dict[str, Any]:
        return backend._hfss_material_update_apply(target, args)

    @staticmethod
    def preview_delete(backend: Any, target: AedtTarget, args: dict[str, Any]) -> dict[str, Any]:
        return backend._hfss_material_delete_preview(target, args)

    @staticmethod
    def apply_delete(backend: Any, target: AedtTarget, args: dict[str, Any]) -> dict[str, Any]:
        return backend._hfss_material_delete_apply(target, args)

    @staticmethod
    def preview_assign(backend: Any, target: AedtTarget, args: dict[str, Any]) -> dict[str, Any]:
        return backend._hfss_material_assign_preview(target, args)

    @staticmethod
    def apply_assign(backend: Any, target: AedtTarget, args: dict[str, Any]) -> dict[str, Any]:
        return backend._hfss_material_assign_apply(target, args)
