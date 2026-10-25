from __future__ import annotations

from typing import Any

from aedt_agent.live.target import AedtTarget


class LayoutViaHarness:
    """Domain harness for 3D Layout via operations (create, update, delete)."""

    @staticmethod
    def create_preview(backend: Any, target: AedtTarget, args: dict[str, Any]) -> dict[str, Any]:
        return backend._layout_via_create_preview(target, args)

    @staticmethod
    def create_apply(backend: Any, target: AedtTarget, args: dict[str, Any]) -> dict[str, Any]:
        return backend._layout_via_create_apply(target, args)

    @staticmethod
    def update_preview(backend: Any, target: AedtTarget, args: dict[str, Any]) -> dict[str, Any]:
        return backend._layout_via_update_preview(target, args)

    @staticmethod
    def update_apply(backend: Any, target: AedtTarget, args: dict[str, Any]) -> dict[str, Any]:
        return backend._layout_via_update_apply(target, args)

    @staticmethod
    def delete_preview(backend: Any, target: AedtTarget, args: dict[str, Any]) -> dict[str, Any]:
        return backend._layout_via_delete_preview(target, args)

    @staticmethod
    def delete_apply(backend: Any, target: AedtTarget, args: dict[str, Any]) -> dict[str, Any]:
        return backend._layout_via_delete_apply(target, args)
