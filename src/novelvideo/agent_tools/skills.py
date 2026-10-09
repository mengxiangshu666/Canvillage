"""Bounded runtime skill loading for the native Village Agent."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from novelvideo.agent_tools.tool_contract import tool_error, tool_result

SKILL_TOOL_NAME = "skill"
SKILL_TOOL_SCHEMA = "village_agent_skill.v1"
SKILL_ACTIVATION_SCHEMA = "village_agent_skill_activation.v1"
SKILL_SOURCE_BUNDLED = "bundled"
AGENT_SKILLS_DIR_ENV = "VILLAGE_CANVAS_AGENT_SKILLS_DIR"
MAX_SKILL_BYTES = 200_000
MAX_SKILL_NAME_CHARS = 80
MAX_SKILL_RESULT_CHARS = 240_000
MAX_ACTIVATION_AGENTS = 12
MAX_ACTIVATION_FLAGS = 12
MAX_ACTIVATION_FENCE = 12
MAX_ACTIVATION_FENCE_CHARS = 240

_SKILL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")
_WORKFLOW_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")
_FLAG_RE = re.compile(r"^[a-z0-9][a-z0-9_]{0,63}$")
# oiioii §OIIOII_SKILL_AGENT_PROTOCOL.md：skill 是编排配置包，不是一段 prompt。
# 四件套缺一不可，未知键一律显式失败，绝不静默降级成散文。
_ACTIVATION_KEYS = ("workflow", "agents", "flags", "fence")
_ACTIVATION_LIST_KEYS = ("agents", "flags", "fence")


class AgentSkillError(ValueError):
    """A skill request that failed an explicit runtime contract."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class SkillActivation:
    """Machine-readable activation contract declared in SKILL.md frontmatter.

    A loaded skill is not a text blob: it binds one registered workflow, one
    bounded set of peer skills, the flags that gate expensive side effects, and
    the fences the run may not cross.
    """

    workflow: str
    agents: tuple[str, ...]
    flags: tuple[str, ...]
    fence: tuple[str, ...]

    def public(self) -> dict[str, Any]:
        return {
            "schema": SKILL_ACTIVATION_SCHEMA,
            "binding": "mandatory",
            "workflow": self.workflow,
            "agents": list(self.agents),
            "flags": list(self.flags),
            "fence": list(self.fence),
        }

    def routing(self) -> dict[str, Any]:
        """Compact form for the skill catalog, where bytes are budgeted."""

        return {
            "workflow": self.workflow,
            "agents": list(self.agents),
            "flags": list(self.flags),
            "fence_count": len(self.fence),
        }


@dataclass(frozen=True, slots=True)
class AgentSkill:
    name: str
    description: str
    path: Path
    size_bytes: int
    sha256: str
    content: str
    version: str = ""
    activation: SkillActivation | None = None
    activation_error: str = ""
    activation_error_code: str = ""

    def public_item(self) -> dict[str, Any]:
        """Stable identity every consumer can pin against.

        The field set follows the shared shape the reference harnesses converged
        on (key, description, source, version, content hash, size). It is derived
        from the file on disk, never hand-maintained.
        """

        item: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "source": SKILL_SOURCE_BUNDLED,
            "version": self.version,
            "bytes": self.size_bytes,
            "sha256": self.sha256,
        }
        if self.activation is not None:
            item["activation"] = self.activation.routing()
        elif self.activation_error:
            item["activation_error_code"] = self.activation_error_code
            item["activation_error"] = self.activation_error
        return item

    def public_load(
        self,
        *,
        action: str = "load",
        switch_reason: str = "",
    ) -> dict[str, Any]:
        value: dict[str, Any] = {
            "schema": SKILL_TOOL_SCHEMA,
            "ok": True,
            "action": action,
            "name": self.name,
            "description": self.description,
            "source": SKILL_SOURCE_BUNDLED,
            "version": self.version,
            "bytes": self.size_bytes,
            "sha256": self.sha256,
            "content": self.content,
        }
        if self.activation is not None:
            value["activation"] = self.activation.public()
        else:
            value["activation_status"] = "unbound"
        if switch_reason:
            value["switch_reason"] = switch_reason
        return value


def agent_skills_root() -> Path:
    """Return the one configured skill root and fail if it is unavailable."""

    project_root = Path(__file__).resolve().parents[3]
    configured = str(os.environ.get(AGENT_SKILLS_DIR_ENV) or "").strip()
    if configured:
        root = Path(configured).expanduser()
    else:
        candidates = (
            project_root / "agent_skills",
            project_root.parent / "agent_skills",
        )
        root = next(
            (candidate for candidate in candidates if candidate.is_dir()),
            candidates[0],
        )
    if not root.is_absolute():
        root = project_root / root
    root = root.resolve()
    if not root.is_dir():
        raise AgentSkillError(
            "skill_root_unavailable",
            f"Agent skills directory is unavailable: {root}",
        )
    return root


def _skill_name(value: object) -> str:
    name = str(value or "").strip()
    if (
        not name
        or len(name) > MAX_SKILL_NAME_CHARS
        or name in {".", ".."}
        or not _SKILL_NAME_RE.fullmatch(name)
    ):
        raise AgentSkillError(
            "skill_name_invalid",
            "Skill name must be a lowercase directory name using letters, digits, or hyphens.",
        )
    return name


def _skill_document(root: Path, name: str) -> Path:
    candidate = (root / name / "SKILL.md").resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise AgentSkillError("skill_path_invalid", "Skill path escaped its root.") from exc
    if not candidate.is_file():
        raise AgentSkillError("skill_not_found", f"Skill not found: {name}")
    return candidate


def _frontmatter_lines(content: str) -> list[str]:
    """Return the raw frontmatter body, excluding the --- fences."""

    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return []
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return lines[1:index]
    return []


def _scalar(value: str) -> str:
    """Strip YAML quoting from a single-line scalar without pulling in a parser."""

    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":
        try:
            return str(json.loads(text))
        except (TypeError, ValueError):
            return text[1:-1]
    return text


def _frontmatter_description(content: str) -> str:
    for line in _frontmatter_lines(content):
        if not line.strip() or line != line.lstrip():
            continue
        key, separator, value = line.partition(":")
        if separator and key.strip() == "description":
            return _scalar(value)[:320]
    return ""


def _frontmatter_version(content: str) -> str:
    """Read `version:` from the top level or from the `metadata:` block."""

    in_metadata = False
    for line in _frontmatter_lines(content):
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        key, separator, value = line.strip().partition(":")
        if not separator:
            continue
        key = key.strip()
        if indent == 0:
            in_metadata = key == "metadata"
            if key == "version":
                return _scalar(value)[:40]
            continue
        if in_metadata and key == "version":
            return _scalar(value)[:40]
    return ""


def _activation_entries(block: list[str], *, name: str) -> dict[str, Any]:
    """Parse the constrained indentation used by an `activation:` frontmatter block."""

    def fail(detail: str) -> AgentSkillError:
        return AgentSkillError(
            "skill_activation_invalid",
            f"Skill {name} activation block is invalid: {detail}",
        )

    entries: dict[str, Any] = {}
    open_list: str | None = None
    for raw in block:
        if not raw.strip():
            continue
        stripped = raw.strip()
        indent = len(raw) - len(raw.lstrip(" "))
        if indent == 2 and not stripped.startswith("- "):
            key, separator, value = stripped.partition(":")
            if not separator:
                raise fail(f"expected `key:` at column 3, got {stripped!r}")
            key = key.strip()
            if key not in _ACTIVATION_KEYS:
                raise fail(
                    f"unsupported key {key!r}; expected one of {', '.join(_ACTIVATION_KEYS)}"
                )
            if key in entries:
                raise fail(f"repeats key {key!r}")
            text = value.strip()
            if text:
                entries[key] = [_scalar(text)] if key in _ACTIVATION_LIST_KEYS else _scalar(text)
                open_list = None
            else:
                entries[key] = []
                open_list = key
            continue
        if indent >= 4 and stripped.startswith("- ") and open_list is not None:
            item = _scalar(stripped[2:])
            if not item:
                raise fail(f"list {open_list!r} has an empty item")
            entries[open_list].append(item)
            continue
        raise fail(f"unsupported line {stripped!r}; lists must be `- item` under their key")
    return entries


def _activation_items(entries: dict[str, Any], key: str, *, name: str) -> tuple[str, ...]:
    raw = entries.get(key)
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise AgentSkillError(
            "skill_activation_invalid",
            f"Skill {name} activation key {key!r} must be a list.",
        )
    items: list[str] = []
    for value in raw:
        text = str(value).strip()
        if text and text not in items:
            items.append(text)
    return tuple(items)


def _workflow_is_registered(workflow: str) -> bool:
    # Imported here so listing skills stays cheap and this module keeps a narrow
    # import graph; an unavailable registry must fail loudly, never degrade.
    try:
        from novelvideo.workflow_runtime.definitions import get_workflow_definition
    except Exception as exc:  # pragma: no cover - depends on the host install
        raise AgentSkillError(
            "skill_activation_workflow_registry_unavailable",
            f"Workflow registry is unavailable, so activation contracts cannot be verified: {exc}",
        ) from exc
    return get_workflow_definition(workflow) is not None


def _skill_activation(content: str, *, name: str, root: Path) -> SkillActivation | None:
    lines = _frontmatter_lines(content)
    start = next(
        (
            index
            for index, line in enumerate(lines)
            if line == line.lstrip() and line.strip() == "activation:"
        ),
        None,
    )
    if start is None:
        return None
    block: list[str] = []
    for line in lines[start + 1 :]:
        if line.strip() and line == line.lstrip():
            break
        block.append(line)

    entries = _activation_entries(block, name=name)
    workflow = str(entries.get("workflow") or "").strip()
    if not workflow:
        raise AgentSkillError(
            "skill_activation_invalid",
            f"Skill {name} activation must declare a workflow.",
        )
    if not _WORKFLOW_ID_RE.fullmatch(workflow):
        raise AgentSkillError(
            "skill_activation_invalid",
            f"Skill {name} activation workflow {workflow!r} is not a canonical workflow id.",
        )
    if not _workflow_is_registered(workflow):
        raise AgentSkillError(
            "skill_activation_workflow_unknown",
            f"Skill {name} binds workflow {workflow!r}, which is not a registered workflow definition.",
        )

    agents = _activation_items(entries, "agents", name=name)
    if len(agents) > MAX_ACTIVATION_AGENTS:
        raise AgentSkillError(
            "skill_activation_invalid",
            f"Skill {name} declares {len(agents)} agents; the bound is {MAX_ACTIVATION_AGENTS}.",
        )
    for agent in agents:
        try:
            _skill_name(agent)
        except AgentSkillError as exc:
            raise AgentSkillError(
                "skill_activation_invalid",
                f"Skill {name} activation lists an invalid agent name {agent!r}.",
            ) from exc
        if not (root / agent / "SKILL.md").is_file():
            raise AgentSkillError(
                "skill_activation_agent_unresolved",
                f"Skill {name} hands off to {agent!r}, which is not an installed skill.",
            )

    flags = _activation_items(entries, "flags", name=name)
    if len(flags) > MAX_ACTIVATION_FLAGS:
        raise AgentSkillError(
            "skill_activation_invalid",
            f"Skill {name} declares {len(flags)} flags; the bound is {MAX_ACTIVATION_FLAGS}.",
        )
    for flag in flags:
        if not _FLAG_RE.fullmatch(flag):
            raise AgentSkillError(
                "skill_activation_invalid",
                f"Skill {name} activation flag {flag!r} must be lowercase snake_case.",
            )

    fence = _activation_items(entries, "fence", name=name)
    if len(fence) > MAX_ACTIVATION_FENCE:
        raise AgentSkillError(
            "skill_activation_invalid",
            f"Skill {name} declares {len(fence)} fence rules; the bound is {MAX_ACTIVATION_FENCE}.",
        )
    for rule in fence:
        if len(rule) > MAX_ACTIVATION_FENCE_CHARS:
            raise AgentSkillError(
                "skill_activation_invalid",
                f"Skill {name} fence rule exceeds {MAX_ACTIVATION_FENCE_CHARS} characters.",
            )
    return SkillActivation(
        workflow=workflow,
        agents=agents,
        flags=flags,
        fence=fence,
    )


def _read_skill(path: Path, *, name: str, root: Path) -> AgentSkill:
    try:
        size_bytes = path.stat().st_size
    except OSError as exc:
        raise AgentSkillError("skill_read_failed", f"Could not stat skill: {name}") from exc
    if size_bytes > MAX_SKILL_BYTES:
        raise AgentSkillError(
            "skill_too_large",
            f"Skill {name} exceeds {MAX_SKILL_BYTES} bytes.",
        )
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise AgentSkillError("skill_read_failed", f"Could not read skill: {name}") from exc
    try:
        content = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise AgentSkillError(
            "skill_encoding_invalid",
            f"Skill {name} is not valid UTF-8.",
        ) from exc
    activation: SkillActivation | None = None
    activation_error = ""
    activation_error_code = ""
    try:
        activation = _skill_activation(content, name=name, root=root)
    except AgentSkillError as exc:
        # Listing must survive a broken contract so the catalog stays usable;
        # loading is where the failure becomes fatal.
        activation_error = exc.message
        activation_error_code = exc.code
    return AgentSkill(
        name=name,
        description=_frontmatter_description(content) or name,
        path=path,
        size_bytes=len(raw),
        sha256=hashlib.sha256(raw).hexdigest(),
        content=content,
        version=_frontmatter_version(content),
        activation=activation,
        activation_error=activation_error,
        activation_error_code=activation_error_code,
    )


def list_agent_skills() -> tuple[AgentSkill, ...]:
    root = agent_skills_root()
    skills: list[AgentSkill] = []
    for child in sorted(root.iterdir(), key=lambda item: item.name):
        if not child.is_dir():
            continue
        document = child / "SKILL.md"
        if not document.is_file():
            continue
        try:
            name = _skill_name(child.name)
        except AgentSkillError:
            continue
        skills.append(_read_skill(_skill_document(root, name), name=name, root=root))
    return tuple(skills)


def load_agent_skill(name: object) -> AgentSkill:
    root = agent_skills_root()
    safe_name = _skill_name(name)
    skill = _read_skill(_skill_document(root, safe_name), name=safe_name, root=root)
    if skill.activation_error:
        raise AgentSkillError(skill.activation_error_code, skill.activation_error)
    return skill


def build_skill_tool_description() -> str:
    try:
        skills = list_agent_skills()
    except AgentSkillError as exc:
        return (
            "Load one runtime skill by name. The skill catalog is unavailable: "
            f"{exc.message}"
        )
    lines = [
        "Load or switch one runtime skill by exact name before acting on a matching task.",
        (
            "When several skills are plausible, load only the single narrowest "
            "specialized skill that directly covers the task. The generic "
            "village-canvas skill is the fallback for broad project/status or "
            "pipeline requests only when no narrower specialized skill fits."
        ),
        "A loaded skill's activation contract is binding: enter its workflow, stay inside its",
        "agents and flags, and never cross its fence. Contracts are verified before load; a",
        "rejected contract means the skill must not be executed from memory or from prose.",
        "Available skills:",
    ]
    for skill in skills:
        line = f"- {skill.name}: {skill.description}"
        if skill.activation is not None:
            line += (
                f" [workflow={skill.activation.workflow}"
                f" agents={len(skill.activation.agents)}"
                f" flags={len(skill.activation.flags)}"
                f" fence={len(skill.activation.fence)}]"
            )
        elif skill.activation_error:
            line += f" [activation INVALID: {skill.activation_error}]"
        lines.append(line)
    return "\n".join(lines)[:12_000]


def skill_tool_schema() -> dict[str, Any]:
    return {
        "name": SKILL_TOOL_NAME,
        "description": build_skill_tool_description(),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["load", "switch", "list"],
                    "default": "load",
                    "description": (
                        "Load one skill, switch before any side effect, or list the "
                        "available runtime skill catalog."
                    ),
                },
                "name": {
                    "type": "string",
                    "description": (
                        "Exact skill directory name; required for action=load or switch."
                    ),
                },
                "reason": {
                    "type": "string",
                    "description": "Short reason for action=switch.",
                },
            },
            "additionalProperties": False,
        },
    }


def skill_tool_handler(arguments: Mapping[str, Any] | None = None) -> str:
    args = dict(arguments or {})
    action = str(args.get("action") or "load").strip().lower()
    if action == "list":
        try:
            skills = list_agent_skills()
        except AgentSkillError as exc:
            return tool_error(exc.message, error_code=exc.code)
        return tool_result(
            {
                "schema": SKILL_TOOL_SCHEMA,
                "ok": True,
                "action": "list",
                "count": len(skills),
                "skills": [skill.public_item() for skill in skills],
            }
        )
    if action not in {"load", "switch"}:
        return tool_error(
            "Skill action must be load, switch, or list.",
            error_code="skill_action_invalid",
        )
    try:
        skill = load_agent_skill(args.get("name"))
    except AgentSkillError as exc:
        return tool_error(exc.message, error_code=exc.code)
    return tool_result(
        skill.public_load(
            action=action,
            switch_reason=(
                str(args.get("reason") or "").strip()[:240]
                if action == "switch"
                else ""
            ),
        )
    )


__all__ = [
    "AGENT_SKILLS_DIR_ENV",
    "MAX_ACTIVATION_AGENTS",
    "MAX_ACTIVATION_FENCE",
    "MAX_ACTIVATION_FLAGS",
    "MAX_SKILL_BYTES",
    "MAX_SKILL_RESULT_CHARS",
    "SKILL_ACTIVATION_SCHEMA",
    "SKILL_SOURCE_BUNDLED",
    "SKILL_TOOL_NAME",
    "SKILL_TOOL_SCHEMA",
    "AgentSkill",
    "AgentSkillError",
    "SkillActivation",
    "agent_skills_root",
    "build_skill_tool_description",
    "list_agent_skills",
    "load_agent_skill",
    "skill_tool_handler",
    "skill_tool_schema",
]
