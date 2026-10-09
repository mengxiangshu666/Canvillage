"""AI chat service with project-scoped history and user-level agent sessions."""

from __future__ import annotations

import sys
import types

from ._service_parts import _service_backend as __service_backend
from ._service_parts import _service_context as __service_context
from ._service_parts import _service_dispatch as __service_dispatch
from ._service_parts import _service_growth as __service_growth
from ._service_parts import _service_village as __service_village
from ._service_parts import _service_render as __service_render
from ._service_parts import _service_shared as __service_shared
from ._service_parts import _service_state as __service_state

_parts = (
    __service_shared,
    __service_growth,
    __service_state,
    __service_dispatch,
    __service_render,
    __service_context,
    __service_backend,
    __service_village,
)

_namespace = {}
for _part in _parts:
    _namespace.update(
        (name, value)
        for name, value in vars(_part).items()
        if not (name.startswith("__") and name.endswith("__"))
    )
for _name, _value in _namespace.items():
    if not (_name.startswith("__") and _name.endswith("__")):
        globals()[_name] = _value
for _part in _parts:
    _part.__dict__.update(_namespace)

for _part in _parts[1:]:
    for _value in vars(_part).values():
        if (
            isinstance(_value, (type, types.FunctionType))
            and getattr(_value, "__module__", "") == _part.__name__
        ):
            _value.__module__ = __name__


class _ChatServiceFacadeModule(types.ModuleType):
    def __setattr__(self, name, value):
        super().__setattr__(name, value)
        if name.startswith("__") or name == "_parts":
            return
        for target in getattr(self, "_parts", ()):
            setattr(target, name, value)


sys.modules[__name__].__class__ = _ChatServiceFacadeModule

__all__ = sorted(name for name in _namespace if not name.startswith("_"))
