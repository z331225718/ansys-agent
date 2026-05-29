from __future__ import annotations

from typing import Any, Mapping

from aedt_agent.layout.void_fallback import build_void_fallback_payload


def build_recorded_optimization_action_plan(
    recorded_analysis: Mapping[str, Any],
    *,
    solve_enabled: bool = False,
) -> dict[str, Any]:
    void_payload = build_void_fallback_payload(recorded_analysis)
    actions = [
        {
            "type": "build_layout_model",
            "api": "pyedb_hfss3dlayout_build",
            "preferred_wrappers": [
                "PyEDB.Edb.cutout",
                "Hfss3dLayout.create_ports_on_component_by_nets",
                "Hfss3dLayout.create_edge_port",
                "Hfss3dLayout.create_setup",
                "Hfss3dLayout.create_linear_count_sweep",
            ],
            "inputs": {
                "signal_nets": list(((recorded_analysis.get("nets") or {}).get("signal") or [])),
                "reference_nets": list(((recorded_analysis.get("nets") or {}).get("reference") or [])),
                "setup": dict(recorded_analysis.get("setup") or {}),
                "sweep": dict(recorded_analysis.get("sweep") or {}),
            },
        },
        {
            "type": "apply_layout_void_adjustment",
            "api": "raw_aedt_void_fallback",
            "variable": void_payload.get("variable", ""),
            "fallback_payload": void_payload,
        },
        {
            "type": "save_project",
            "api": "Hfss3dLayout.save_project",
            "project": str((recorded_analysis.get("paths") or {}).get("aedt_project") or ""),
        },
    ]
    if solve_enabled:
        actions.append(
            {
                "type": "solve_layout_channel",
                "api": "Hfss3dLayout.analyze",
                "setup": str((recorded_analysis.get("setup") or {}).get("name") or "Setup1"),
            }
        )
    return {
        "status": "ready" if void_payload.get("status") == "ready" else "needs_user_hint",
        "mode": "build_only" if not solve_enabled else "solve_enabled",
        "source": "recorded_workflow_analysis",
        "actions": actions,
    }
