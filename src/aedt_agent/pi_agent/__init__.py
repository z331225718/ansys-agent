"""Backward-compatible imports for the former aedt_agent.pi_agent package."""

from aedt_agent.ansys_agent import (
    AnsysAgentCase as PiAgentCase,
    AnsysAgentCaseError as PiAgentCaseError,
    AnsysAgentSupervisor as PiAgentSupervisor,
    initialize_local_case,
    load_case_config,
)

__all__ = [
    "PiAgentCase",
    "PiAgentCaseError",
    "PiAgentSupervisor",
    "initialize_local_case",
    "load_case_config",
]
