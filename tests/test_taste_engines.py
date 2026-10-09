"""Taste engines must catch the specific failures that shipped in real films.

Each test below is a regression for something that actually went wrong in
production, not a hypothetical:

* 《一个音符》 and 《喂招》 both used one warm key light at high ratio, dust motes
  in a beam, and cold blue for pressure — the same look twice.
* 《喂招》 was 83% static shots and half close-ups.
* Every dialogue line in 《喂招》 was written without subtext, which is why the
  lines read as instruction rather than character.
"""

from __future__ import annotations

from novelvideo.production import taste_engines as engines
from novelvideo.production import taste_kb


def _shot(**overrides: object) -> dict[str, object]:
    shot: dict[str, object] = {
        "index": 1,
        "purpose": "让观众先看见门是关着的，这样下一镜他推门时观众会先紧张",
        "action": "他抬手停在门把上方，没有碰它，然后把手收回身侧",
        "beats": "0.0-1.2s 抬手；1.2-2.4s 停住；2.4-3.6s 收回",
        "composition": "中景，平视",
        "cameraMove": "机器从 2.4 米匀速推到 1.2 米，停在他的手上",
        "lighting": "",
        "performance": "肩膀先动，手后动",
    }
    shot.update(overrides)
    return shot


def _plan(*shots: dict[str, object], **overrides: object) -> dict[str, object]:
    plan: dict[str, object] = {
        "title": "测试",
        "lighting": "窗外的天光从画面左侧斜入，色温 5600K，光比 3:1",
        "color": "主色 60% 灰绿；辅色 30% 米白；点缀 10% 赭石",
        "style": "写实，无颗粒",
        "sceneLine": "一间朝北的工作间",
        "shots": list(shots) or [_shot()],
    }
    plan.update(overrides)
    return plan


# --------------------------------------------------------------------------- #
# the cliché registry
# --------------------------------------------------------------------------- #

def test_registry_entries_are_named_and_actionable() -> None:
    """Every banned cliché must carry a stable id, detectable patterns and a fix."""
    assert taste_kb.BANNED_CLICHES, "the registry must not be empty"
    for cliche in taste_kb.BANNED_CLICHES:
        assert cliche["cliche_id"].startswith("slop."), cliche
        assert cliche["patterns"], f"{cliche['cliche_id']} has no detection pattern"
        assert cliche["instead"], f"{cliche['cliche_id']} bans without a replacement"


def test_quota_values_are_bounded() -> None:
    """Ratios are ratios; a limit of 1.0 would be no limit at all."""
    for key in ("staticShotRatioMax", "singleCameraMoveRatioMax", "mustRewardShotRatioMax"):
        value = float(taste_kb.QUOTAS[key])
        assert 0.0 < value < 1.0, f"{key} is not a meaningful ratio: {value}"
    assert taste_kb.QUOTAS["adjacentSameFramingAllowed"] is False


def test_paired_samples_expose_the_contrast() -> None:
    """A pair is only useful if the strong half is materially more specific."""
    for sample in taste_kb.PAIRED_SAMPLES:
        assert sample["weak"] and sample["strong"] and sample["why"], sample
        assert len(sample["strong"]) > len(sample["weak"]), sample["slot"]


# --------------------------------------------------------------------------- #
# engine: clichés and rotation
# --------------------------------------------------------------------------- #

def test_reused_look_is_blocked_across_films() -> None:
    """The same lighting family must not be spent twice — the 《一个音符》/《喂招》 case."""
    plan = _plan(lighting="唯一主光源是头顶那盏灯，暖黄，光比 9:1，暗部沉入冷蓝")
    hits = engines.detect_cliches(plan)
    assert "slop.single-warm-key-high-ratio.v1" in hits

    findings = engines.audit_cliches(
        plan,
        previous_families={"上一部": ["单一暖光源高光比"]},
        profile_families={"cliche:slop.single-warm-key-high-ratio.v1": "单一暖光源高光比"},
    )
    assert any(item["code"] == "family_reused" for item in findings)


def test_cliche_budget_is_enforced() -> None:
    """One cliché per film is a choice; five is the model's defaults."""
    plan = _plan(
        lighting="唯一光源暖黄，光比 8:1，暗部沉入冷蓝",
        sceneLine="雨夜，积水的地面",
        style="写实，光柱里尘埃漂浮",
    )
    findings = engines.audit_cliches(plan)
    codes = {item["code"] for item in findings}
    assert "cliche_hit" in codes
    assert "cliche_budget_exceeded" in codes


# --------------------------------------------------------------------------- #
# engine: diversity quotas
# --------------------------------------------------------------------------- #

def test_mostly_static_film_is_blocked() -> None:
    """《喂招》 was 83% static; motion has to be designed, not omitted."""
    shots = [_shot(index=i, cameraMove="完全静止") for i in range(1, 11)]
    shots.append(_shot(index=11, cameraMove="机器缓慢前推"))
    findings = engines.audit_diversity(_plan(*shots))
    assert any(item["code"] == "static_ratio_exceeded" for item in findings)


def test_adjacent_same_framing_is_blocked() -> None:
    shots = [
        _shot(index=1, composition="中景，平视"),
        _shot(index=2, composition="中景，略仰视"),
    ]
    findings = engines.audit_diversity(_plan(*shots))
    assert any(item["code"] == "adjacent_same_framing" for item in findings)


# --------------------------------------------------------------------------- #
# engine: subtext and vagueness
# --------------------------------------------------------------------------- #

def test_dialogue_without_subtext_is_blocked() -> None:
    """Every line in 《喂招》 failed this; that is why they read as instructions."""
    findings = engines.audit_subtext(_plan(_shot(dialogue="手抬高。你以前总是忘了抬手。")))
    assert any(item["code"] == "dialogue_without_subtext" for item in findings)


def test_subtext_satisfies_the_engine() -> None:
    shot = _shot(dialogue="手抬高。", subtext="我没能护住你，至少把这个留给你。")
    findings = engines.audit_subtext(_plan(shot))
    assert not [item for item in findings if item["code"] == "dialogue_without_subtext"]


def test_stating_the_emotion_in_dialogue_is_blocked() -> None:
    shot = _shot(dialogue="我很难过。", subtext="不想让对方看见")
    findings = engines.audit_subtext(_plan(shot))
    assert any(item["code"] == "emotion_stated_in_dialogue" for item in findings)


def test_vague_adjectives_are_blocked() -> None:
    findings = engines.audit_adjectives(_plan(_shot(action="镜头缓慢推进，营造高级的电影感")))
    assert any(item["code"] == "vague_word_in_shot" for item in findings)


def test_informational_purpose_is_blocked() -> None:
    findings = engines.audit_single_focus(_plan(_shot(purpose="这一镜用来交代环境和气氛")))
    assert any(item["code"] == "purpose_is_informational" for item in findings)


# --------------------------------------------------------------------------- #
# scoring
# --------------------------------------------------------------------------- #

def test_clean_plan_scores_as_shippable() -> None:
    """A plan written to the standard must not be punished by the engines."""
    shots = [
        _shot(
            index=1,
            purpose="让观众先看见门是关着的，下一镜他推门时观众会先紧张",
            composition="中景，平视",
            cameraMove="机器从 2.4 米匀速推到 1.2 米，停在他的手上",
        ),
        _shot(
            index=2,
            purpose="他推门失败，观众确认第一镜的预示是真的",
            composition="近景，略俯视",
            cameraMove="手持轻微跟随他后退半步",
            action="他推进门把手，门只动了半寸，他松手后退半步",
            beats="0.0-1.0s 推；1.0-2.0s 门不动；2.0-3.0s 松手后退",
        ),
        _shot(
            index=3,
            purpose="观众看见走廊尽头有别人，于是知道他不是一个人来的",
            composition="全景，平视",
            cameraMove="机器沿着走廊横向移动，跟住他的背影",
            action="他沿走廊走向画面深处，脚步在台阶处慢下来",
            beats="0.0-1.5s 走；1.5-2.5s 放慢；2.5-3.5s 停住",
        ),
        _shot(
            index=4,
            purpose="他的手停在半空，观众知道他要放弃这次尝试",
            composition="极特写，微俯视",
            cameraMove="完全静止",
            action="他的手指张开又握紧，最后垂到身侧没有抬起来",
            beats="0.0-1.2s 张开；1.2-2.4s 握紧；2.4-3.6s 垂下",
        ),
    ]
    report = engines.audit_plan(_plan(*shots))
    assert report["verdict"] in {"ship", "revise"}, report["hardFindings"]


def test_ratio_quota_ignores_samples_too_small_to_measure() -> None:
    """In a two-shot plan any choice is 50%; that is arithmetic, not a defect."""
    shots = [
        _shot(index=1, cameraMove="机器缓慢前推"),
        _shot(index=2, cameraMove="手持轻微跟随"),
    ]
    findings = engines.audit_diversity(_plan(*shots))
    codes = {item["code"] for item in findings}
    assert "single_move_dominates" not in codes
    assert "static_ratio_exceeded" not in codes


def test_report_separates_hard_from_soft() -> None:
    report = engines.audit_plan(_plan(_shot(purpose="展示气氛")))
    assert report["schema"] == engines.TASTE_AUDIT_SCHEMA
    assert report["verdict"] == "blocked"
    assert report["hardCount"] >= 1
    assert "look" not in report


# --------------------------------------------------------------------------- #
# wardrobe continuity — the defect that shipped in 《左肩》
# --------------------------------------------------------------------------- #

def test_wardrobe_continuity_catches_the_shipped_jacket_error() -> None:
    """《左肩》 shipped with the customer's coat off in shot 3 and back on in 7.

    Every shot was fine on its own, which is why no per-shot rule caught it.
    The plan has to state each tracked garment's state in every shot, because
    the ambiguity *between* shots is what the video model resolves at random.
    """
    plan = _plan(
        _shot(index=1, cast=["裁缝", "客人"], wardrobe="客人穿浅灰衬衫外搭深灰薄外套"),
        _shot(index=2, cast=["裁缝", "客人"], wardrobe="客人已脱下深灰薄外套，身上只有浅灰衬衫"),
        _shot(index=3, cast=["客人"], wardrobe="镜子有轻微的氧化斑"),
    )
    findings = engines.audit_wardrobe_continuity(plan)
    assert [item["shot"] for item in findings] == [3]
    assert findings[0]["severity"] == "hard"
    assert findings[0]["code"] == "wardrobe.untracked-garment"


def test_wardrobe_continuity_accepts_a_fully_tracked_plan() -> None:
    plan = _plan(
        _shot(index=1, cast=["客人"], wardrobe="客人穿浅灰衬衫，深灰外套搭在椅背上"),
        _shot(index=2, cast=["客人"], wardrobe="客人只穿浅灰衬衫，上身没有外套"),
    )
    assert engines.audit_wardrobe_continuity(plan) == []


def test_wardrobe_continuity_ignores_shots_with_nobody_in_them() -> None:
    """An empty frame has no wardrobe; flagging it is a false positive."""
    plan = _plan(
        _shot(index=1, cast=["客人"], wardrobe="客人穿浅灰衬衫外搭深灰外套"),
        _shot(index=2, cast=[], wardrobe="台面上是牛皮纸样、裁缝剪与画粉盒"),
    )
    assert engines.audit_wardrobe_continuity(plan) == []


def test_wardrobe_continuity_does_not_invent_garments() -> None:
    """A plan that never mentions a tracked garment has nothing to track."""
    plan = _plan(
        _shot(index=1, cast=["客人"], wardrobe="客人穿浅灰衬衫"),
        _shot(index=2, cast=["客人"], wardrobe="客人穿浅灰衬衫，袖口卷起"),
    )
    assert engines.audit_wardrobe_continuity(plan) == []
