"""技能蒸馏管道 —— 录制事件 → 能力分类 → 技能蒸馏 → 安装为村长 Agent 技能。

流程（复用 Browser-BC 论文思想，自研实现）：
  trace(events) → classify（LLM 提取能力名/描述/触发词）
                → distill（LLM 生成 SKILL.md 正文）
                → install（写 agent_skills/<name>/SKILL.md）
"""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path

from novelvideo.config import STATE_DIR
from novelvideo.skills_distill import store as skill_store
from novelvideo.skills_distill.install import frontmatter, kebab
from novelvideo.skills_distill.llm import call_llm, parse_json_from_model

logger = logging.getLogger("novelvideo.skills_distill.pipeline")

TRACES_DIR = Path(STATE_DIR) / "skills_traces"


def _events_text(events: list[dict]) -> str:
    lines = []
    for i, ev in enumerate(events, 1):
        url = ev.get("url", "")
        kind = ev.get("kind", "action")
        action = ev.get("action", {})
        if isinstance(action, dict):
            atype = action.get("type", "")
            sel = action.get("selector", "") or action.get("text", "")
            lines.append(f"{i}. [{kind}] {atype} @ {url} {sel}".strip())
        else:
            lines.append(f"{i}. [{kind}] {url} {action}")
    return "\n".join(lines) or "(empty trace)"


# ── 1. 能力分类 ────────────────────────────────────────────────────────────
CLASSIFY_PROMPT = """你是浏览器操作技能蒸馏器。分析下面的录制操作序列，提炼出它代表的一个可复用能力。

操作序列：
{events}

要求输出 JSON（不要其他文字）：
{{"name": "英文 kebab-case 技能名（如 upload-character-assets）",
 "description": "一句话中文描述这个操作做什么",
 "triggers": ["2-4 个中文触发词/场景短语，用于村长 Agent 技能匹配"]}}
"""


def classify_capability(label: str, events: list[dict]) -> dict:
    prompt = CLASSIFY_PROMPT.format(events=_events_text(events))
    raw = call_llm(prompt, json_mode=True)
    cap = parse_json_from_model(raw)
    name = str(cap.get("name") or "distilled-skill").strip()
    description = str(cap.get("description") or label or name)
    triggers = [str(t).strip() for t in (cap.get("triggers") or []) if str(t).strip()]
    return {"name": name, "description": description, "triggers": triggers}


# ── 2. 技能蒸馏 ────────────────────────────────────────────────────────────
DISTILL_PROMPT = """你是浏览器操作技能编写器。根据下面的录制操作序列，编写一份可复用的 SKILL.md 正文（不含 frontmatter）。

能力：{name}
{description}

操作序列：
{events}

输出要求（Markdown，中文）：
1. ## Purpose —— 这个技能完成什么目标（一句话）
2. ## Preconditions —— 前置条件
3. ## Steps —— 编号步骤，写通用化的操作（不写死具体 URL/选择器，用"找到 XX 元素"这类表述）
4. ## Recovery —— 失败恢复分支（点击无效、页面没变化、404 怎么办）
5. ## Boundary Conditions —— 边界（不要做什么）
6. ## Exit Conditions —— 完成判定

只输出 Markdown 正文。
"""


def distill_skill_body(cap: dict, events: list[dict]) -> str:
    prompt = DISTILL_PROMPT.format(
        name=cap["name"], description=cap["description"], events=_events_text(events)
    )
    return call_llm(prompt, json_mode=False, max_tokens=8192)


# ── 3. trace 存取 ──────────────────────────────────────────────────────────
def save_trace(label: str, description: str, events: list[dict]) -> str:
    TRACES_DIR.mkdir(parents=True, exist_ok=True)
    trace_id = "tr_" + uuid.uuid4().hex[:12]
    path = TRACES_DIR / f"{trace_id}.json"
    path.write_text(
        json.dumps(
            {
                "trace_id": trace_id,
                "label": label,
                "description": description,
                "events": events,
                "created_at": __import__("datetime").datetime.now().isoformat(),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return trace_id


def load_trace(trace_id: str) -> dict:
    path = TRACES_DIR / f"{trace_id}.json"
    if not path.exists():
        raise FileNotFoundError(f"trace not found: {trace_id}")
    return json.loads(path.read_text(encoding="utf-8"))


def list_traces() -> list[dict]:
    if not TRACES_DIR.exists():
        return []
    out = []
    for p in sorted(TRACES_DIR.glob("*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            out.append(
                {
                    "trace_id": d.get("trace_id"),
                    "label": d.get("label"),
                    "events": len(d.get("events", [])),
                }
            )
        except Exception:  # noqa: BLE001
            continue
    return out


# ── 4. 全流程 ──────────────────────────────────────────────────────────────
def run_distill(trace_id: str) -> dict:
    trace = load_trace(trace_id)
    events = trace.get("events", [])
    if not events:
        raise ValueError("trace has no events")
    cap = classify_capability(trace.get("label", ""), events)
    body = distill_skill_body(cap, events)
    skill_name = kebab(cap["name"]) or "distilled-skill"
    markdown = frontmatter(skill_name, cap["description"], cap["triggers"]) + body.strip() + "\n"
    imported = skill_store.import_skill_file(
        f"{skill_name}.md",
        markdown.encode("utf-8"),
    )
    if not imported:
        raise ValueError("distilled skill admission produced no item")
    installed = skill_store.install_store_item(str(imported[0]["id"]))
    md_path = Path(str(installed["installed_path"]))
    result = {
        "trace_id": trace_id,
        "skill_name": md_path.parent.name,
        "installed_path": str(md_path),
        "capability": cap,
        "admission": installed.get("admission"),
    }
    logger.info("distilled %s -> %s", trace_id, md_path)
    return result
