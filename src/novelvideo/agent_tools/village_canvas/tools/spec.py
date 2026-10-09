"""One declaration per agent capability, and the assembly that uses it.

The Village canvas plugin builds three faces for the agent, in three different
source parts: the direct tool schemas, the core capability cards the broker
searches, and the capability -> handler map. The same capability therefore gets
written down more than once, and nothing keeps the copies in step -- a schema can
drift from the server constant it is supposed to describe, and a card can name a
handler that lives in another file.

`ToolSpec` is the single declaration: what the model calls, what the broker
advertises, and who answers. The assembly functions here turn a set of specs plus
whatever is still living in the legacy source parts into exactly the three shapes
the plugin exported before, in the order `tools/order.py` freezes. That lets the
migration move one capability at a time while `check_agent_tool_surface.py`
proves the agent-visible surface never moved.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Sequence


class SurfaceError(ValueError):
    """The declared specs and the legacy source parts disagree."""


@dataclass(frozen=True)
class ToolSpec:
    """One capability: broker card, optional direct tool, and its handler.

    ``handler_name`` is resolved in the plugin namespace at assembly time. That
    is a transitional seam: shared helpers still live in the exec-ed parts, so a
    migrated handler keeps working until its collaborators move too.
    """

    id: str
    handler_name: str
    card: Mapping[str, Any] | None = None
    tool_name: str | None = None
    description: str | None = None
    properties: Mapping[str, Any] | None = None
    required: tuple[str, ...] = ()
    handler_defaults: Mapping[str, Any] | None = None

    def build_schema(self) -> dict[str, Any]:
        """Return the direct-tool schema exactly the way the legacy helper did."""

        if not self.tool_name or self.description is None:
            raise SurfaceError(
                f"{self.id}: a direct tool needs tool_name and description"
            )
        return {
            "name": self.tool_name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": dict(self.properties or {}),
                "required": list(self.required),
            },
        }


def _duplicates(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    repeated: list[str] = []
    for value in values:
        if value in seen and value not in repeated:
            repeated.append(value)
        seen.add(value)
    return repeated


def _check_order_coverage(
    *,
    face: str,
    order: Sequence[str],
    legacy_ids: Iterable[str],
    spec_ids: Iterable[str],
) -> None:
    """Every entry must be claimed exactly once, by the order or by a spec."""

    order_ids = list(order)
    repeated = _duplicates(order_ids)
    if repeated:
        raise SurfaceError(f"{face}: order lists the same id twice: {repeated}")

    legacy = set(legacy_ids)
    declared = set(spec_ids)
    ordered = set(order_ids)

    duplicated = sorted(legacy & declared)
    if duplicated:
        raise SurfaceError(
            f"{face}: entries are both legacy and declared: {duplicated}"
        )
    undeclared = sorted(legacy - ordered)
    if undeclared:
        raise SurfaceError(
            f"{face}: legacy entries are missing from the frozen order: {undeclared}"
        )
    unclaimed = sorted(ordered - legacy - declared)
    if unclaimed:
        raise SurfaceError(
            f"{face}: order names ids that neither a spec nor the legacy parts "
            f"provide: {unclaimed}"
        )
    unlisted = sorted(declared - ordered)
    if unlisted:
        raise SurfaceError(
            f"{face}: specs are not listed in the frozen order: {unlisted}"
        )
    for value in (legacy_ids, declared):
        repeated_specs = _duplicates(value)
        if repeated_specs:
            raise SurfaceError(f"{face}: duplicate ids {repeated_specs}")


def assemble_core_cards(
    *,
    order: Sequence[str],
    legacy: Sequence[Mapping[str, Any]],
    specs: Iterable[ToolSpec],
) -> tuple[dict[str, Any], ...]:
    """Rebuild the core capability card tuple in the frozen order."""

    by_id = {spec.id: spec for spec in specs if spec.card is not None}
    legacy_by_id = {str(card["id"]): card for card in legacy}
    _check_order_coverage(
        face="core_capability_cards",
        order=order,
        legacy_ids=legacy_by_id,
        spec_ids=by_id,
    )

    assembled: list[dict[str, Any]] = []
    for card_id in order:
        spec = by_id.get(card_id)
        if spec is not None:
            card = dict(spec.card or {})
            if str(card.get("id")) != spec.id:
                raise SurfaceError(
                    f"{spec.id}: the card declares id {card.get('id')!r}"
                )
            assembled.append(card)
        else:
            assembled.append(legacy_by_id[card_id])
    return tuple(assembled)


def assemble_tools(
    *,
    order: Sequence[str],
    legacy: Sequence[tuple[str, dict[str, Any], Callable[..., Any]]],
    specs: Iterable[ToolSpec],
    namespace: Mapping[str, Any],
) -> tuple[tuple[str, dict[str, Any], Callable[..., Any]], ...]:
    """Rebuild the direct-tool tuple in the frozen order."""

    by_name = {spec.tool_name: spec for spec in specs if spec.tool_name}
    legacy_by_name = {
        str(name): (name, schema, handler) for name, schema, handler in legacy
    }
    _check_order_coverage(
        face="direct_tools",
        order=order,
        legacy_ids=legacy_by_name,
        spec_ids=by_name,
    )

    assembled: list[tuple[str, dict[str, Any], Callable[..., Any]]] = []
    for tool_name in order:
        spec = by_name.get(tool_name)
        if spec is None:
            assembled.append(legacy_by_name[tool_name])
            continue
        assembled.append(
            (tool_name, spec.build_schema(), resolve_handler(spec, namespace))
        )
    return tuple(assembled)


def resolve_handler(spec: ToolSpec, namespace: Mapping[str, Any]) -> Callable[..., Any]:
    """Return a late-bound callable for a declared handler.

    The legacy plugin tests and host adapters patch handler names on the package
    module after import. Resolving once here would silently pin the old function,
    so the returned wrapper reads the current value on every call.
    """

    resolve_handler_name(spec, namespace)

    def invoke(*args: Any, **kwargs: Any) -> Any:
        handler = resolve_handler_name(spec, namespace)
        return handler(*args, **kwargs)

    invoke.__name__ = spec.handler_name
    invoke.__qualname__ = spec.handler_name
    return invoke


def resolve_handler_name(
    spec: ToolSpec, namespace: Mapping[str, Any]
) -> Callable[..., Any]:
    """Validate that the declared handler exists and return its current value."""

    handler = namespace.get(spec.handler_name)
    if handler is None:
        raise SurfaceError(
            f"{spec.id}: handler {spec.handler_name!r} is not defined in the "
            "plugin namespace"
        )
    if not callable(handler):
        raise SurfaceError(f"{spec.id}: handler {spec.handler_name!r} is not callable")
    return handler


def assemble_capability_handler_names(
    specs: Iterable[ToolSpec],
    namespace: Mapping[str, Any],
) -> dict[str, str]:
    """Return capability id -> handler name after validating every declaration."""

    resolved: dict[str, str] = {}
    for spec in specs:
        if spec.id in resolved:
            raise SurfaceError(f"duplicate capability id {spec.id}")
        resolve_handler_name(spec, namespace)
        resolved[spec.id] = spec.handler_name
    return resolved


def assemble_capability_handler_defaults(
    specs: Iterable[ToolSpec],
) -> dict[str, dict[str, Any]]:
    """Return capability id -> fixed arguments merged into broker calls."""

    resolved: dict[str, dict[str, Any]] = {}
    for spec in specs:
        if spec.handler_defaults is None:
            continue
        if spec.id in resolved:
            raise SurfaceError(f"duplicate capability id {spec.id}")
        resolved[spec.id] = dict(spec.handler_defaults)
    return resolved
