from __future__ import annotations

from typing import Any

from aedt_agent.live.target import AedtTarget


class LayoutAntipadHarness:
    """Domain harness for 3D Layout anti-pad circle void operations."""

    @staticmethod
    def create_preview(backend: Any, target: AedtTarget, args: dict[str, Any]) -> dict[str, Any]:
        return backend._layout_antipad_circle_create_preview(target, args)

    @staticmethod
    def create_apply(backend: Any, target: AedtTarget, args: dict[str, Any]) -> dict[str, Any]:
        return backend._layout_antipad_circle_create_apply(target, args)
