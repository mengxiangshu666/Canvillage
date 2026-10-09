"""Runtime lookups for handlers exposed through the package facade.

The plugin package keeps the legacy public names that hosts and tests patch.
Normal implementation modules resolve a handler through this module at call
time, so replacing ``village_canvas._handle_x`` still affects the real call
path after the implementation has moved out of one shared globals dictionary.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from typing import Any


_PROXY_MARKER = "__village_canvas_runtime_proxy__"


def runtime_attr(name: str) -> Any:
    """Return one current attribute from the village_canvas package facade."""

    package = sys.modules.get(__package__ or "")
    if package is None:
        raise RuntimeError("village_canvas package is not loaded")
    try:
        return getattr(package, name)
    except AttributeError as exc:
        raise RuntimeError(f"village_canvas handler {name!r} is not defined") from exc


def runtime_handler(name: str) -> Callable[..., Any]:
    """Return one current callable handler from the package facade."""

    handler = runtime_attr(name)
    if not callable(handler):
        raise TypeError(f"village_canvas handler {name!r} is not callable")
    return handler


def runtime_proxy(name: str) -> Callable[..., Any]:
    """Return a late-bound callable that follows package monkeypatches."""

    def invoke(*args: Any, **kwargs: Any) -> Any:
        return runtime_handler(name)(*args, **kwargs)

    invoke.__name__ = name
    invoke.__qualname__ = name
    setattr(invoke, _PROXY_MARKER, True)
    return invoke


def is_runtime_proxy(value: object) -> bool:
    """Return whether a value is a late-bound facade proxy."""

    return bool(getattr(value, _PROXY_MARKER, False))
