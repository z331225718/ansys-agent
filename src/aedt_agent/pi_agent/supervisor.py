"""Backward-compatible supervisor name."""

from aedt_agent.ansys_agent.supervisor import AnsysAgentSupervisor as PiAgentSupervisor

__all__ = ["PiAgentSupervisor"]
