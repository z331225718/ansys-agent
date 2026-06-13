"""Compatibility imports for the preserved v0 demo package."""

from aedt_agent._compat import install_package_aliases

_target = install_package_aliases(
    __name__,
    "aedt_agent.v0.demo",
    [
        "config",
        "import_cutout",
        "layout_ports",
        "planner",
        "planner_benchmark",
        "preflight",
        "service",
        "tuning",
        "web",
    ],
)

__all__ = getattr(_target, "__all__", [])


def __getattr__(name: str):
    return getattr(_target, name)
