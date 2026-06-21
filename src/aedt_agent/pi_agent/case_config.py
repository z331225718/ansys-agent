"""Backward-compatible case config names."""

from aedt_agent.ansys_agent.case_config import (
    AnsysAgentCase as PiAgentCase,
    AnsysAgentCaseError as PiAgentCaseError,
    load_case_config,
)

__all__ = ["PiAgentCase", "PiAgentCaseError", "load_case_config"]
