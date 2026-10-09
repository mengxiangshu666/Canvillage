"""Village Infinite Canvas API toolset for 小树.

The implementation is split into normal Python modules. The package namespace
remains the public facade hosts and tests patch, while the three agent-visible
faces -- direct tool schemas, core capability cards, and the capability ->
handler map -- are assembled from one `ToolSpec` declaration each in the order
frozen by `tools/order.py`.
"""

from __future__ import annotations

from novelvideo.agent_tools.tool_contract import tool_error as tool_error
from novelvideo.agent_tools.tool_contract import tool_result as tool_result

from . import canvas_reads as _canvas_reads
from . import canvas_writes_impl as _canvas_writes_impl
from . import capability_broker as _capability_broker
from . import core as _core
from . import judgment_impl as _judgment_impl
from . import native_registry as _native_registry
from . import workflow_dispatch as _workflow_dispatch
from . import workflow_execution as _workflow_execution
from .runtime import is_runtime_proxy as _is_runtime_proxy
from .tools import SPECS as _DECLARED_SPECS
from .tools.order import CARD_ORDER as _CARD_ORDER
from .tools.order import TOOL_ORDER as _TOOL_ORDER
from .tools.spec import (
    assemble_capability_handler_names as _assemble_capability_handler_names,
)
from .tools.spec import (
    assemble_capability_handler_defaults as _assemble_capability_handler_defaults,
)
from .tools.spec import assemble_core_cards as _assemble_core_cards
from .tools.spec import assemble_tools as _assemble_tools

_IMPLEMENTATION_MODULES = (
    _core,
    _canvas_reads,
    _canvas_writes_impl,
    _workflow_dispatch,
    _workflow_execution,
    _capability_broker,
    _native_registry,
    _judgment_impl,
)

# Preserve the old shared-namespace facade without executing source into this
# module. Runtime proxies are deliberately skipped: the facade keeps the real
# function, while implementation modules resolve it through that facade.
for _module in _IMPLEMENTATION_MODULES:
    for _name, _value in vars(_module).items():
        if _name.startswith("__") or _is_runtime_proxy(_value):
            continue
        globals()[_name] = _value

_CREATIVE_CAPABILITY_INDEX = _capability_broker._CREATIVE_CAPABILITY_INDEX
_SKILL_CAPABILITY_INDEX = _capability_broker._SKILL_CAPABILITY_INDEX
_REGISTERED_CAPABILITY_HANDLER_NAMES = (
    _capability_broker._REGISTERED_CAPABILITY_HANDLER_NAMES
)
_REGISTERED_CAPABILITY_HANDLER_DEFAULTS = (
    _capability_broker._REGISTERED_CAPABILITY_HANDLER_DEFAULTS
)

_CORE_CAPABILITY_INDEX = _assemble_core_cards(
    order=_CARD_ORDER,
    legacy=(),
    specs=_DECLARED_SPECS,
)
_CAPABILITY_INDEX = (
    *_CORE_CAPABILITY_INDEX,
    *_CREATIVE_CAPABILITY_INDEX,
    *_SKILL_CAPABILITY_INDEX,
)
TOOLS = _assemble_tools(
    order=_TOOL_ORDER,
    legacy=(),
    specs=_DECLARED_SPECS,
    namespace=globals(),
)
_REGISTERED_CAPABILITY_HANDLER_NAMES.update(
    _assemble_capability_handler_names(_DECLARED_SPECS, globals())
)
_REGISTERED_CAPABILITY_HANDLER_DEFAULTS.update(
    _assemble_capability_handler_defaults(_DECLARED_SPECS)
)

del (
    _canvas_reads,
    _canvas_writes_impl,
    _capability_broker,
    _core,
    _module,
    _name,
    _native_registry,
    _value,
    _workflow_dispatch,
    _workflow_execution,
)
