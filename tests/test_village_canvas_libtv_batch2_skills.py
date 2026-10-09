"""Contract tests for the libtv gap-filling distillation batch (T-214).

The four skills here are the second batch distilled from ``libtv``'s skill
library.  Three of them fill a real gap in the 42 existing ``village-canvas-*``
skills (script doctor / lighting / single-image prompt craft), and one
(performance director) sits above ``performance-craft`` rather than beside it.

The guards follow the T-211 precedent: every skill must declare its libtv
provenance, carry an acceptance checklist, and never name a model the product
does not have — T-211 caught a stale ``Nebula Pro`` reference exactly this way.
"""

import re
from pathlib import Path

from novelvideo.agent_tools.skills import list_agent_skills, load_agent_skill


ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "agent_skills"

NEW_SKILLS = {
    "village-canvas-script-doctor": ("AI导演-剧本诊断改稿",),
    "village-canvas-lighting-director": ("AI 视频光影提示词",),
    "village-canvas-image-prompt-craft": ("大师级电影感图片提示词",),
    "village-canvas-performance-director": ("AI影视角色表演导演",),
}

#: Model names the product never had, or that a skill must not bind to.
#: Naming one inside a "this binding was removed" note is fine — that is how
#: T-214 documents the upstream's Seedance binding — so the guard rejects only
#: *instructional* use ("用 X 生成", "model: X"), which is what sends the Agent
#: after a model that does not exist.
FORBIDDEN_MODEL_NAMES = ("Nebula Pro",)
_MODEL_BINDING_RE = re.compile(
    r"(?:用|使用|调用|指定|选择)\s*[「\"']?(?:Nebula Pro|MJ V8|Seedance\s*2\.0)"
)


def test_batch2_skills_are_valid_and_declare_their_libtv_source():
    descriptions: list[str] = []
    for name, sources in NEW_SKILLS.items():
        path = SKILL_ROOT / name / "SKILL.md"
        assert path.is_file(), name
        text = path.read_text(encoding="utf-8")
        assert f"name: {name}" in text
        assert len(text) > 1_500, name
        # Upstream runtime vocabulary must never leak into a native skill.
        assert ".hermes" not in text, name
        assert "Hermes" not in text, name
        for source in sources:
            assert source in text, f"{name} 未声明来源 {source}"
        for field in ("description:", "验收清单", "来源与边界"):
            assert field in text, f"{name} 缺 {field}"
        for forbidden in FORBIDDEN_MODEL_NAMES:
            assert forbidden not in text, f"{name} 指向不存在/不该绑定的模型 {forbidden}"
        binding = _MODEL_BINDING_RE.search(text)
        assert binding is None, f"{name} 把模型写成了可调用对象：{binding!r}"
        description = text.split("description:", 1)[1].splitlines()[0].strip()
        assert description, name
        assert len(description) <= 320, f"{name} description 超长"
        descriptions.append(description)

    assert len(descriptions) == len(set(descriptions))


def test_batch2_skills_are_visible_in_the_real_native_catalog():
    discovered = {skill.name: skill for skill in list_agent_skills()}
    for name in NEW_SKILLS:
        assert name in discovered, f"{name} 未进运行时目录"
        loaded = load_agent_skill(name)
        assert loaded.name == name
        assert loaded.description
        assert loaded.size_bytes > 1_500, name


def test_batch2_skill_boundaries_stay_distinct_from_their_neighbours():
    """Each new skill must name what it is NOT, or routing gets mushy.

    ``performance-director`` (design the acting) and ``performance-craft``
    (execute an agreed direction) are the pair most at risk of collapsing.
    """

    expectations = {
        "village-canvas-script-doctor": "village-canvas-script-integrity",
        "village-canvas-performance-director": "village-canvas-performance-craft",
        "village-canvas-image-prompt-craft": "village-canvas-prompt-framework",
        "village-canvas-lighting-director": "village-canvas-cinematography",
    }
    for name, neighbour in expectations.items():
        text = (SKILL_ROOT / name / "SKILL.md").read_text(encoding="utf-8")
        assert neighbour in text, f"{name} 未声明与 {neighbour} 的分工"
