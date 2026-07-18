from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from aedt_agent.desktop.launcher import DesktopLaunchError
from aedt_agent.live.discovery import list_aedt_sessions


EXTENSION_NAME = "Ansys Agent"
EXTENSION_PRODUCT = "Project"


def install_extension(
    *,
    port: int | None = None,
    version: str = "2026.1",
    personal_lib: str | None = None,
    desktop_factory: Callable[..., Any] | None = None,
    add_script: Callable[..., bool] | None = None,
) -> dict[str, Any]:
    port = _select_port(port)
    if desktop_factory is None:
        from ansys.aedt.core import Desktop

        desktop_factory = Desktop
    if add_script is None:
        from ansys.aedt.core.extensions.customize_automation_tab import add_script_to_menu

        add_script = add_script_to_menu
    desktop = desktop_factory(
        version=version,
        machine="localhost",
        port=port,
        new_desktop=False,
        close_on_exit=False,
    )
    try:
        target_personal_lib = str(Path(personal_lib or desktop.personallib).resolve())
        entry = Path(__file__).with_name("aedt_extension_entry.py").resolve()
        installed = bool(
            add_script(
                name=EXTENSION_NAME,
                script_file=str(entry),
                product=EXTENSION_PRODUCT,
                copy_to_personal_lib=True,
                personal_lib=target_personal_lib,
                odesktop=desktop.odesktop,
            )
        )
        if not installed:
            raise DesktopLaunchError("PyAEDT rejected the Automation Tab extension installation")
        _refresh_toolkit_ui(desktop)
        return {
            "installed": True,
            "extension_name": EXTENSION_NAME,
            "product": EXTENSION_PRODUCT,
            "personal_lib": target_personal_lib,
            "port": port,
            "version": version,
            "restart_required": False,
        }
    finally:
        desktop.release_desktop(close_projects=False, close_on_exit=False)


def uninstall_extension(
    *,
    port: int | None = None,
    version: str = "2026.1",
    desktop_factory: Callable[..., Any] | None = None,
    remove_script: Callable[..., bool] | None = None,
) -> dict[str, Any]:
    port = _select_port(port)
    if desktop_factory is None:
        from ansys.aedt.core import Desktop

        desktop_factory = Desktop
    if remove_script is None:
        from ansys.aedt.core.extensions.customize_automation_tab import remove_script_from_menu

        remove_script = remove_script_from_menu
    desktop = desktop_factory(
        version=version,
        machine="localhost",
        port=port,
        new_desktop=False,
        close_on_exit=False,
    )
    try:
        removed = bool(remove_script(desktop_object=desktop, name=EXTENSION_NAME, product=EXTENSION_PRODUCT))
        if not removed:
            raise DesktopLaunchError("Ansys Agent extension is not installed or could not be removed")
        _refresh_toolkit_ui(desktop)
        return {
            "uninstalled": True,
            "extension_name": EXTENSION_NAME,
            "product": EXTENSION_PRODUCT,
            "port": port,
            "version": version,
        }
    finally:
        desktop.release_desktop(close_projects=False, close_on_exit=False)


def select_live_port(port: int | None = None) -> int:
    return _select_port(port)


def _select_port(port: int | None) -> int:
    if port is not None:
        if type(port) is not int or not 1 <= port <= 65535:
            raise DesktopLaunchError("AEDT gRPC port must be an integer from 1 to 65535")
        return port
    candidates = sorted(
        {
            int(item["grpc_port"])
            for item in list_aedt_sessions()
            if item.get("grpc_port") is not None
        }
    )
    if not candidates:
        raise DesktopLaunchError("no running AEDT gRPC session was discovered")
    if len(candidates) != 1:
        raise DesktopLaunchError("multiple AEDT sessions are running; specify --port explicitly")
    return candidates[0]


def _refresh_toolkit_ui(desktop: Any) -> None:
    refresh = getattr(desktop.odesktop, "RefreshToolkitUI", None)
    if callable(refresh):
        refresh()
