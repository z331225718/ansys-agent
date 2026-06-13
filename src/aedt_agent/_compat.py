from __future__ import annotations

import sys
from importlib import import_module
from types import ModuleType
from typing import Iterable


def install_package_aliases(
    legacy_package: str,
    target_package: str,
    module_names: Iterable[str],
) -> ModuleType:
    """Map legacy submodule imports to the same v0 module objects."""
    legacy_module = sys.modules[legacy_package]
    target_module = import_module(target_package)

    for module_name in module_names:
        target_name = f"{target_package}.{module_name}"
        legacy_name = f"{legacy_package}.{module_name}"
        module = import_module(target_name)
        sys.modules[legacy_name] = module
        setattr(legacy_module, module_name, module)

    return target_module
