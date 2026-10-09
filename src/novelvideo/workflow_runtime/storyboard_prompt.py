"""Storyboard prompt assembly owned by the workflow runtime.

The storyboard compiler must carry the filmcraft rules that the image and video
node compilers inherited from ``filmcraft_kb``.  WorkflowRuntime may not import
``production`` implementations directly (``domain_boundaries.json`` rule
``workflow-to-production-implementation``), so rule selection goes through the
lazy ``services.production_contracts`` facade, exactly like the other stage
contracts.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from novelvideo.services import production_contracts

_FILMCRAFT_HEADER = (
    "\n全片手艺规则（与图像/视频节点编译器同源，不是风格建议），"
    "逐条落到 shots 字段里：\n"
)


def _action_context(request: str) -> bool:
    """Return whether the request states an explicit combat/action scene.

    The decision lives in ``production.filmcraft_kb`` behind the facade; this
    module keeps **no** cue list of its own.  The facade attribute is looked up
    at call time (not bound at import time), so an override installed on the
    facade is honored.
    """

    return bool(production_contracts.has_filmcraft_action_context(request))


def build_storyboard_filmcraft_contract(
    request: str,
    reuse_target_node_ids: Sequence[str] = (),
    director_vision: Mapping[str, Any] | None = None,
    project_dna: Mapping[str, Any] | None = None,
) -> str:
    """Render the filmcraft rules one storyboard compile context triggers.

    ``shot_count`` is only declared when the run reuses existing canvas nodes,
    so a plain compile never claims a multi-shot lock it has no evidence for.
    ``action_context`` is always declared, so the combat-only rule never leaks
    into a warm scene on a stray single-character verb.
    Returns ``""`` when nothing triggers, so callers never append an empty
    section to the prompt.
    """

    params: dict[str, Any] = {"action_context": _action_context(request)}
    if reuse_target_node_ids:
        params["shot_count"] = len(reuse_target_node_ids)
    # Both the decision above and the rule selection below go through facade
    # module attributes looked up at call time, so one monkeypatched facade
    # controls the whole contract.  A module-level ``from ... import`` here
    # would bind the function object at import and silently ignore overrides.
    rules = production_contracts.inject_filmcraft_rules(
        node_type="video",
        params=params,
        director_vision=director_vision,
        project_dna=project_dna,
        source_text=request,
        creation_stage="storyboard",
    )
    block = "\n".join(
        f"[{item['rule_id']} | stage={item['stage']} | trigger={item['trigger']}]\n"
        f"执行：{item['instruction']}\n禁忌：{item['avoid']}"
        for item in rules
    )
    if not block:
        return ""
    return _FILMCRAFT_HEADER + block


__all__ = ["build_storyboard_filmcraft_contract"]
