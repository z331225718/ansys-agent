"""Backward-compatible web panel helpers."""

from aedt_agent.ansys_agent.web import (
    dispatch_action,
    render_operator_panel,
    run_ansys_agent_panel as run_pi_agent_panel,
)

__all__ = ["dispatch_action", "render_operator_panel", "run_pi_agent_panel"]
