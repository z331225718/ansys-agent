"""Compatibility imports for the preserved v0 evolution package."""

from aedt_agent._compat import install_package_aliases

_target = install_package_aliases(
    __name__,
    "aedt_agent.v0.evolution",
    ["evaluator", "miner", "models", "policy", "proposer"],
)

__all__ = getattr(_target, "__all__", [])


def __getattr__(name: str):
    return getattr(_target, name)
