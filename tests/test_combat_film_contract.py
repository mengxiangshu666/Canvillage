"""Contract tests for the combat-film gates.

The authoritative positive sample is ``tests/fixtures/combat_film_30s_1v1.json``
(6 shots x 5.0 s = 30.0 s).  Every negative case below is a one-field mutation of
that sample, so a passing run can only mean the predicate really fired.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from novelvideo.production.combat_film_contract import (
    CAUSALITY_CHAIN,
    CINEMATIC_FAMILIES,
    CINEMATIC_FAMILY_GATES,
    COMBAT_FILM_DECLARATIONS_GATE,
    COMBAT_FILM_GATES,
    COMBAT_FILM_STRUCTURE_GATE,
    DAMAGE_STATES,
    MAX_SHOTS,
    MAX_SHOT_SECONDS,
    MIN_SHOTS,
    MIN_SHOT_SECONDS,
    _AMBIGUOUS_STRONG_TERMS,
    _ATTRIBUTIVE_WINDOW,
    _EXPLICIT_FIGHT_NAMES,
    _EXPLICIT_FIGHT_TERMS,
    _NON_FIGHT_PROGRAM_HEADS,
    _STRONG_TERMS,
    _WEAK_ACTION_TERMS,
    audit_combat_cinematic_consistency,
    audit_combat_damage_continuity,
    audit_combat_declarations,
    audit_combat_dialogue_declarations,
    audit_combat_structure,
    combat_declaration,
    combat_intent_matches,
    combat_structure_capacity,
    compile_combat_film_contract,
    is_combat_film_request,
    requested_duration_seconds,
)
from novelvideo.production.director_evaluator import normalize_gate_name

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "combat_film_30s_1v1.json"


def _fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _shots() -> list[dict]:
    return copy.deepcopy(_fixture()["shots"])


def _request() -> str:
    return _fixture()["request"]


def _combat(shot: dict) -> dict:
    return shot["cinematic"]["combat"]


# --- the fixture is the positive sample -------------------------------------


def test_fixture_is_a_30_second_six_shot_package() -> None:
    shots = _shots()
    assert len(shots) == 6
    assert sum(shot["duration_seconds"] for shot in shots) == 30.0
    assert all(4.0 <= shot["duration_seconds"] <= 15.0 for shot in shots)


def test_fixture_passes_both_combat_gates() -> None:
    report = compile_combat_film_contract(shots=_shots(), request=_request())

    assert report["applies"] is True
    assert report["gate_observations"] == {
        COMBAT_FILM_STRUCTURE_GATE: True,
        COMBAT_FILM_DECLARATIONS_GATE: True,
    }
    assert report["required_gates"] == list(COMBAT_FILM_GATES)
    assert report["ready_for_media"] is True
    assert report["issues"] == []


def test_fixture_passes_through_the_executor_normalization_pipeline() -> None:
    """The sample must survive the real plan path, not just this module."""

    from novelvideo.workflow_runtime.executor import (
        StoryboardPlan,
        _normalize_plan_duration,
        _normalize_shot_contracts,
    )

    fixture = _fixture()
    plan = StoryboardPlan.model_validate(
        {
            "title": fixture["title"],
            "creative_direction": fixture["creative_direction"],
            "shots": fixture["shots"],
        }
    )
    plan = _normalize_plan_duration(plan, 30.0)
    plan = _normalize_shot_contracts(plan, None)
    shots = plan.model_dump(mode="json")["shots"]

    assert [shot["duration_seconds"] for shot in shots] == [5.0] * 6
    report = compile_combat_film_contract(shots=shots, request=_request())
    assert report["ready_for_media"] is True


def test_fixture_is_not_a_blanket_pass_for_any_package() -> None:
    """A package without declarations must fail the same fixture-shaped call."""

    bare = [
        {"shot_id": f"S{index:02d}", "duration_seconds": 5.0, "prompt": "镜头"}
        for index in range(1, 7)
    ]
    report = compile_combat_film_contract(shots=bare, request=_request())

    assert report["gate_observations"][COMBAT_FILM_DECLARATIONS_GATE] is False
    assert report["ready_for_media"] is False


# --- gate names -------------------------------------------------------------


def test_gate_names_are_not_folded_by_normalize_gate_name() -> None:
    for gate in COMBAT_FILM_GATES:
        assert normalize_gate_name(gate) == gate


def test_gate_names_are_not_registered_as_cross_shot_stage_gates() -> None:
    """Registering them there would erase them for the flagship 'idea' request.

    ``partition_stage_gates`` consults that set under a flag derived from
    ``minimum_shots``, which is 1 for the whole ``idea`` tier -- and
    ``infer_delivery_level`` puts "30 秒极限打斗" there.
    """

    from novelvideo.workflow_runtime.quality_stage import (
        CROSS_SHOT_GATES,
        partition_stage_gates,
    )

    assert not (set(COMBAT_FILM_GATES) & CROSS_SHOT_GATES)
    requested, canonical, skipped = partition_stage_gates(
        requested_gates=list(COMBAT_FILM_GATES),
        canonical_gates=[normalize_gate_name(gate) for gate in COMBAT_FILM_GATES],
        normalize=normalize_gate_name,
        media_expected=True,
        visual_continuity_applicable=False,
    )
    assert canonical == list(COMBAT_FILM_GATES)
    assert skipped == []
    assert len(requested) == len(COMBAT_FILM_GATES)


def test_combat_contract_is_reachable_through_the_neutral_facade() -> None:
    from novelvideo.services.production_contracts import (
        combat_film_quantity_gates,
        compile_combat_film_contract as compile_via_facade,
    )

    assert combat_film_quantity_gates() == COMBAT_FILM_GATES
    assert compile_via_facade(shots=_shots(), request=_request())["ready_for_media"] is True


# --- combat intent detection ------------------------------------------------


def test_combat_intent_rejects_the_measured_false_positives() -> None:
    """Bare substring matching misjudged both of these; a gate cannot."""

    assert is_combat_film_request(None, "一个关于反击命运的故事") is False
    assert is_combat_film_request(None, "把这段格挡式教育故事拍成短片") is False
    assert is_combat_film_request(None, "一个女孩在雨夜街头缓慢回头") is False


def test_explicit_and_ambiguous_tiers_partition_the_strong_terms() -> None:
    """Tier 2 is derived from tier 1, so no term can fall between them."""

    assert set(_EXPLICIT_FIGHT_TERMS) <= set(_STRONG_TERMS)
    assert set(_EXPLICIT_FIGHT_TERMS) | set(_AMBIGUOUS_STRONG_TERMS) == set(_STRONG_TERMS)
    assert not (set(_EXPLICIT_FIGHT_TERMS) & set(_AMBIGUOUS_STRONG_TERMS))
    assert "动作戏" in _EXPLICIT_FIGHT_NAMES
    assert "动作戏" not in _AMBIGUOUS_STRONG_TERMS


def test_combat_intent_accepts_explicit_fight_requests() -> None:
    assert is_combat_film_request(None, "做一个30秒的1v1打斗片") is True
    assert is_combat_film_request(None, "雨夜武戏：刺客突袭，刀客格挡后反击") is True
    assert is_combat_film_request(None, "30秒极限打斗，带高燃台词") is True
    assert is_combat_film_request(None, "拍一段拳击比赛纪录短片") is True
    assert is_combat_film_request({"project_goal": "两人对峙后贴身肉搏"}, "") is True
    assert is_combat_film_request({"genre": "combat"}, "") is True


def test_explicit_fight_names_gate_on_their_own() -> None:
    """Tier 1 names the film's violence, so one name is already evidence.

    ``动作戏`` / ``武打`` / ``拳击比赛`` carry no tier-1a *term*, which is why the
    phrase list exists; asserting ``matched["explicit"]`` proves the phrase scan
    -- not some other rule -- is what fired.
    """

    for text in (
        "拍个动作戏",
        "拍一段武打短片",
        "拍一段肉搏战的短片",
        "拍一段拳击比赛纪录短片",
        "格斗比赛纪录短片",
        "拍一段打斗场面的短片",
    ):
        matched = combat_intent_matches(text)
        assert matched["explicit"], text
        assert is_combat_film_request(None, text) is True, text


def test_attributive_head_list_is_the_measured_set() -> None:
    """The list is a ratchet, not a general "programme" vocabulary.

    Every entry was measured inside one of the five requests below, and each
    extra entry silently removes a real fight request from the gate
    ("打斗预告片" is still a fight), so growing it needs a measurement first.
    """

    assert _NON_FIGHT_PROGRAM_HEADS == (
        "花絮",
        "幕后",
        "纪录片",
        "采访",
        "访谈",
        "广告",
        "招生",
        "影评",
        "集锦",
        "探班",
    )
    assert _ATTRIBUTIVE_WINDOW == 9


def test_fight_names_used_attributively_do_not_gate() -> None:
    """Measured (2026-09-27): five ordinary requests were fail-closed as fights.

    Each one is a tier-1 name in front of a non-fight programme form, so the
    name says what the programme is *about* rather than naming the film's
    violence.  The assertions on ``matched`` prove the vocabulary did fire and
    the attributive rule -- not a vocabulary gap -- is what rejected them.

    The same five are replayed through ``build_cinematic_review`` in
    ``test_non_fight_requests_reach_the_review_without_combat_gates``, so the
    fail-closed path is covered, not just the predicate.
    """

    for text, name in (
        ("拍一段武打片的幕后花絮", "武打"),
        ("综合格斗选手的成长纪录片", "综合格斗"),
        ("散打比赛的赛前采访", "散打比赛"),
        ("自由搏击健身房的招生广告", "自由搏击"),
        ("拳击比赛的赛后采访", "拳击比赛"),
    ):
        matched = combat_intent_matches(text)
        assert name in matched["attributive"], text
        assert matched["explicit"] == [], text
        assert matched["sustained"] == [], text
        assert is_combat_film_request(None, text) is False, text


def test_attributive_filter_does_not_eat_a_real_fight_name() -> None:
    """The filter only fires on a programme head, and only inside the clause.

    ``打斗片`` / ``动作戏`` / ``拳击比赛纪录短片`` carry no programme head after
    the name, and a programme word in the *next* clause cannot reach back and
    silence a fight.
    """

    for text in (
        "做一个30秒的1v1打斗片",
        "拍个动作戏",
        "拍一段拳击比赛纪录短片",
        "雨夜武戏：刺客突袭，刀客格挡后反击",
        "两人不停交手，从檐廊打到院中",
        "拍一段打斗片，幕后花絮另说",
    ):
        matched = combat_intent_matches(text)
        assert matched["attributive"] == [], text
        assert is_combat_film_request(None, text) is True, text


def test_program_forms_and_training_demos_do_not_gate() -> None:
    for text in (
        "拍一段武戏的影评，只讲镜头语言",
        "拳击比赛赛事集锦，只剪高光",
        "武打片拍摄现场探班",
        "武打片导演的工作访谈",
        "拍一段武术教学，示范格挡动作",
        "两名学员近身练习擒拿",
    ):
        matched = combat_intent_matches(text)
        assert is_combat_film_request(None, text) is False, (text, matched)


def test_sustained_motion_recognises_place_to_place_fight() -> None:
    text = "镜头跟拍两人从屋顶打到庭院"
    matched = combat_intent_matches(text)
    assert "持续动作" in matched["sustained"]
    assert is_combat_film_request(None, text) is True


def test_ambiguous_combat_nouns_do_not_gate_non_fight_requests() -> None:
    """The six measured false positives, plus the venue promo that mirrors them.

    Every one of them names a match-up (tier 2) and nothing else, so the
    assertions on ``matched`` prove the sentence is rejected by the tier rule
    rather than by the vocabulary failing to see the noun.
    """

    for text in (
        "拳击手套产品广告，30秒",
        "记录一场村际篮球对决，30秒",
        "拍一个拳击馆的宣传片",
        "这次是两个团队的对决，30秒纪录片",
        "拍一段师徒交手切磋的纪录片",
        "拍一个格斗馆的宣传片",
    ):
        matched = combat_intent_matches(text)
        assert matched["strong"], text
        assert matched["explicit"] == [], text
        assert matched["sustained"] == [], text
        assert is_combat_film_request(None, text) is False, text

    # "一个人与命运搏斗" is the same shape with an abstract object, so it is
    # filtered as a metaphor before the tier-2 rule ever sees it.
    metaphor = "一个人与命运搏斗的励志短片"
    assert combat_intent_matches(metaphor)["strong"] == []
    assert is_combat_film_request(None, metaphor) is False


def test_matchup_nouns_need_a_corroborator_in_the_same_clause() -> None:
    """Measured regression from the first attempt at this rule.

    "两人不停交手" / "双方不断对打" are real fights -- the module's negation
    coverage (``_NON_NEGATING_PHRASES``) already existed to protect that reading
    -- and a rule that only accepted tier-1 names dropped both.  Sustained
    combat in the clause, or fight staging vocabulary in the clause, is the
    corroborator; a training demo or a sport match-up has neither.
    """

    for text in (
        "两人不停交手，从檐廊打到院中",
        "双方不断对打，招招见血",
        "拍一段武侠对打",
        "拍一段擂台比武",
    ):
        assert combat_intent_matches(text)["sustained"], text
        assert is_combat_film_request(None, text) is True, text

    for text in (
        "两人对决，点到为止",
        "拍一段师徒交手切磋的纪录片",
    ):
        matched = combat_intent_matches(text)
        assert matched["strong"], text
        assert matched["sustained"] == [], text
        assert is_combat_film_request(None, text) is False, text

    # Corroboration is clause-scoped: fight vocabulary in another clause cannot
    # promote a match-up noun.
    cross_clause = "拍一段武侠风格的片子，主题是村际篮球对决，30秒"
    assert "对决" in combat_intent_matches(cross_clause)["strong"]
    assert combat_intent_matches(cross_clause)["sustained"] == []
    assert is_combat_film_request(None, cross_clause) is False


def test_negation_covers_the_explicit_phrase_tier() -> None:
    """The first attempt matched phrases with a raw ``in text`` test.

    That let a *negated* phrase fire as soon as any other tier-1 term survived:
    "不要拍打斗场面，只拍师徒交手切磋的纪录片" came back True off the negated
    "打斗场面".  Phrases now go through the same negation/metaphor filter as
    terms.
    """

    for text in (
        "不拍打斗场面",
        "不做动作戏",
        "不要拍打斗场面，只拍师徒交手切磋的纪录片",
        "不拍武打片，拍一段安静的村口纪录片",
    ):
        assert is_combat_film_request(None, text) is False, text


def test_metaphor_object_before_the_verb_is_not_a_fight() -> None:
    """``与命运搏斗`` puts the abstract object *before* the verb.

    The tail check alone misses it, and a continuity modifier in between
    ("与命运不断搏斗") would otherwise promote it through the tier-2 rule.
    """

    for text in (
        "公司与命运不断搏斗的励志短片",
        "一个人与命运不停搏斗的励志故事",
        "一部与偏见不断搏斗的纪录片",
    ):
        assert combat_intent_matches(text)["strong"] == [], text
        assert is_combat_film_request(None, text) is False, text


def test_combat_intent_requires_more_than_one_isolated_action_verb() -> None:
    assert is_combat_film_request(None, "他抬手格挡屋檐的雨") is False
    assert is_combat_film_request(None, "她闪避了地上的水坑") is False


def test_combat_intent_rejects_one_daily_sense_verb_beside_an_opponent() -> None:
    """Measured false positives: these three ordinary sentences were read as fights.

    ``_WEAK_ACTION_TERMS`` is the named criterion: one combat verb with a
    productive non-violent reading (``闪避`` / ``反击``) beside an opponent
    marker is not evidence of a staged fight.  The assertions on ``matched``
    prove the vocabulary *did* fire -- the sentence is rejected by the
    weak-verb rule, not because the detector failed to see the verb.
    """

    assert _WEAK_ACTION_TERMS == ("闪避", "反击")
    for text in (
        "两人在咖啡馆重逢，互相闪避对方的目光，最后握手言和。",
        "父亲和女儿两人在厨房一起做饭，女儿闪避父亲递来的目光，画面温暖。",
        "两个同事在会议室里互相反击对方的质疑，靠一次坦白和解。",
    ):
        matched = combat_intent_matches(text)
        assert matched["action"], text
        assert matched["opponent"], text
        assert matched["strong"] == [], text
        assert is_combat_film_request(None, text) is False, text


def test_daily_sense_verbs_still_count_for_the_two_action_rule() -> None:
    """Only the opponent rule is guarded; two distinct action verbs still fire."""

    assert is_combat_film_request(None, "两人先闪避后反击，来回三个回合") is True


def test_a_daily_sense_verb_does_not_downgrade_a_strong_term() -> None:
    """The strong-term path is untouched: "打斗" still names a fight."""

    matched = combat_intent_matches("两人在雨夜古刹一对一打斗，要极限高燃。")
    assert matched["strong"] == ["打斗"]
    assert is_combat_film_request(None, "两人在雨夜古刹一对一打斗，要极限高燃。") is True
    assert is_combat_film_request(None, "两人在公园长椅上安静地看夕阳，一句台词都没有。") is False


def test_non_fight_requests_reach_the_review_without_combat_gates() -> None:
    """The production path, not just the predicate: no combat gate is requested."""

    from novelvideo.workflow_runtime.cinematic_review import build_cinematic_review

    shots = _shots()
    for shot in shots:
        shot["cinematic"].pop("combat")
    for text in (
        "两人在咖啡馆重逢，互相闪避对方的目光，最后握手言和。",
        "父亲和女儿两人在厨房一起做饭，女儿闪避父亲递来的目光，画面温暖。",
        "两个同事在会议室里互相反击对方的质疑，靠一次坦白和解。",
        "两人在公园长椅上安静地看夕阳，一句台词都没有。",
        # The six measured false positives, through the real decision point:
        # an ambiguous match-up noun must not request the fail-closed gates.
        "拳击手套产品广告，30秒",
        "记录一场村际篮球对决，30秒",
        "拍一个拳击馆的宣传片",
        "这次是两个团队的对决，30秒纪录片",
        "一个人与命运搏斗的励志短片",
        "拍一段师徒交手切磋的纪录片",
        # The five measured attributive false positives, through the real
        # decision point: a tier-1 name in front of a programme form must not
        # request the fail-closed gates either.
        "拍一段武打片的幕后花絮",
        "综合格斗选手的成长纪录片",
        "散打比赛的赛前采访",
        "自由搏击健身房的招生广告",
        "拳击比赛的赛后采访",
    ):
        review = build_cinematic_review(
            run={"inputs": {"request": text}, "artifacts": {}},
            shots=copy.deepcopy(shots),
            director_plan={},
        )
        assert review["required_gates"] == [], text
        assert not [
            key for key in review["gate_observations"] if key.startswith("combat")
        ], text

    fight = build_cinematic_review(
        run={
            "inputs": {"request": "两人在雨夜古刹一对一打斗，要极限高燃。"},
            "artifacts": {},
        },
        shots=copy.deepcopy(shots),
        director_plan={},
    )
    assert [gate for gate in fight["required_gates"] if gate.startswith("combat_film")] == [
        COMBAT_FILM_STRUCTURE_GATE,
        COMBAT_FILM_DECLARATIONS_GATE,
    ]


def test_combat_intent_rejects_a_negated_fight_at_clause_distance() -> None:
    """Measured: the old 3-character lookback missed these and read them as fights.

    In "不希望出现任何格斗镜头" the negation marker ends 7 characters before the
    term, so a fixed short window cannot see it.
    """

    assert is_combat_film_request(None, "这个片子完全没有任何打斗") is False
    assert is_combat_film_request(None, "不希望出现任何格斗镜头") is False
    assert is_combat_film_request(None, "别拍打斗") is False
    assert is_combat_film_request(None, "不拍武戏") is False
    assert is_combat_film_request(None, "一场没有搏斗的对峙，全靠眼神") is False


def test_combat_intent_accepts_fight_verbs_missing_from_the_shared_vocabulary() -> None:
    """Measured: these real fights were missed by the pre-fix detector."""

    for text in (
        "两人贴身缠斗",
        "两人扭打在一起",
        "两人挥拳互殴",
        "他不住地挥拳，两人扭打起来",
    ):
        assert is_combat_film_request(None, text) is True, text


def test_combat_intent_does_not_read_negation_inside_a_longer_phrase() -> None:
    """``不停`` / ``不断`` / ``不得不`` merely contain ``不``.

    Measured: "两人不停交手" and "双方不断对打" were dropped as negations, and
    the double negative in "不得不打斗" was lost when only the leading ``不``
    was exempted.
    """

    assert is_combat_film_request(None, "两人不停交手，从檐廊打到院中") is True
    assert is_combat_film_request(None, "双方不断对打，招招见血") is True
    assert is_combat_film_request(None, "两人不得不打斗") is True
    assert is_combat_film_request(None, "无不惊叹的打斗") is True
    assert is_combat_film_request(None, "无比精彩的肉搏战") is True
    assert is_combat_film_request(None, "无法避免的打斗") is True


def test_combat_intent_negation_is_clause_scoped() -> None:
    """A negation in an earlier clause must not silence a later fight clause."""

    assert is_combat_film_request(None, "不要拍抒情片，两人贴身缠斗") is True


def test_combat_intent_is_deterministic() -> None:
    text = "雨夜武戏：刺客突袭，刀客格挡后反击"
    assert combat_intent_matches(text) == combat_intent_matches(text)
    assert is_combat_film_request(None, text) is is_combat_film_request(None, text)


def test_non_combat_request_is_never_judged_by_this_contract() -> None:
    shots = _shots()
    for shot in shots:
        shot["cinematic"].pop("combat")
    report = compile_combat_film_contract(
        shots=shots, request="一个女孩在雨夜街头缓慢回头"
    )

    assert report["applies"] is False
    assert report["required_gates"] == []
    assert report["gate_observations"] == {}


def test_declared_combat_applies_the_contract_even_with_a_plain_request() -> None:
    """Declarations are evidence of intent; do not hide behind a vague prompt."""

    report = compile_combat_film_contract(shots=_shots(), request="拍个短片")

    assert report["applies"] is True
    assert report["gate_observations"][COMBAT_FILM_STRUCTURE_GATE] is True


# --- structure --------------------------------------------------------------


def test_structure_flags_a_three_second_shot() -> None:
    shots = _shots()
    shots[0]["duration_seconds"] = 3.0
    report = audit_combat_structure(shots)

    assert report["passed"] is False
    assert "combat.structure.shot_too_short" in {i["code"] for i in report["issues"]}


def test_structure_flags_a_twenty_second_shot() -> None:
    shots = _shots()
    shots[0]["duration_seconds"] = 20.0
    report = audit_combat_structure(shots)

    assert report["passed"] is False
    assert "combat.structure.shot_too_long" in {i["code"] for i in report["issues"]}


def test_structure_flags_a_missing_duration() -> None:
    shots = _shots()
    shots[0].pop("duration_seconds")
    report = audit_combat_structure(shots)

    assert "combat.structure.duration_missing" in {i["code"] for i in report["issues"]}


def test_structure_flags_a_single_shot_fight() -> None:
    report = audit_combat_structure(_shots()[:1])

    assert report["passed"] is False
    assert "combat.structure.shot_count_below_min" in {
        i["code"] for i in report["issues"]
    }
    assert MIN_SHOTS == 2


def test_structure_flags_too_many_shots() -> None:
    shots = _shots()
    for index in range(MAX_SHOTS + 1 - len(shots)):
        extra = copy.deepcopy(shots[0])
        extra["shot_id"] = f"X{index:02d}"
        shots.append(extra)
    report = audit_combat_structure(shots)

    assert len(shots) == MAX_SHOTS + 1
    assert "combat.structure.shot_count_above_max" in {
        i["code"] for i in report["issues"]
    }


def test_structure_capacity_keeps_target_and_floor_from_contradicting() -> None:
    """30 s at a 4 s floor allows at most 7 shots; 8 would force 3.75 s each."""

    window = combat_structure_capacity(30)
    assert window["feasible"] is True
    assert window["max_shots"] == 7
    assert window["min_shots"] == 2
    assert len(_shots()) <= window["max_shots"]

    eight = [
        {"shot_id": f"S{i:02d}", "duration_seconds": 3.75} for i in range(1, 9)
    ]
    assert audit_combat_structure(eight)["passed"] is False


def test_structure_capacity_never_exceeds_the_plan_shot_cap() -> None:
    """Measured: 180 s reported ``max_shots=45``, a window the gate itself rejects."""

    for target in (100, 180, 181, 240):
        window = combat_structure_capacity(target)
        assert window["max_shots"] <= MAX_SHOTS
        if window["max_shots"] > 0:
            # A package inside the reported window must still pass structure.
            count = window["max_shots"]
            seconds = target / count
            if MIN_SHOT_SECONDS <= seconds <= MAX_SHOT_SECONDS:
                package = [
                    {"shot_id": f"S{i:02d}", "duration_seconds": seconds}
                    for i in range(1, count + 1)
                ]
                assert audit_combat_structure(package)["passed"] is True

    assert combat_structure_capacity(240)["feasible"] is False
    assert combat_structure_capacity(240)["reason"] == "combat.structure.target_unreachable"


# --- requested total duration -----------------------------------------------


def test_requested_duration_seconds_matches_the_executor_parser() -> None:
    """The gate and ``_requested_duration_seconds`` must not disagree."""

    from novelvideo.workflow_runtime.executor import _requested_duration_seconds

    for request in (
        "30秒极限打斗，带高燃台词",
        "做一个30秒的1v1打斗片",
        "拍个短片",
        "300秒打斗",
        "2秒打斗",
        "12s short",
    ):
        assert requested_duration_seconds(request) == _requested_duration_seconds(
            request
        ), request


def test_requested_duration_is_none_when_no_duration_is_named() -> None:
    for request in ("", "拍个短片", "雨夜武戏：刺客突袭"):
        assert requested_duration_seconds(request) is None


def test_requested_duration_prefers_the_total_minute_title() -> None:
    from novelvideo.workflow_runtime.executor import _requested_duration_seconds

    request = "《落凡宗小臣》2 分钟版。原版 8 场 × 15 秒，本版拆成 19 镜。"

    assert requested_duration_seconds(request) == 120.0
    assert _requested_duration_seconds(request) == 120.0
    assert requested_duration_seconds("按 8 个分镜拆，控制在 30 秒") == 30.0


def test_structure_flags_a_total_that_contradicts_the_requested_duration() -> None:
    """The measured gap: a 30 s request shipped a 2.041 s cut with green gates."""

    report = audit_combat_structure(_shots()[:1], request="做一个30秒的打斗片")

    assert report["passed"] is False
    assert "combat.structure.total_duration_mismatch" in {
        i["code"] for i in report["issues"]
    }
    assert report["requested_seconds"] == 30.0
    assert report["total_seconds"] == 5.0


def test_structure_total_duration_is_not_judged_without_a_named_duration() -> None:
    """No duration named means *not judged*, not a silent pass and not a free fail."""

    report = audit_combat_structure(_shots()[:1], request="拍个短打片")

    assert "combat.structure.total_duration_mismatch" not in {
        i["code"] for i in report["issues"]
    }
    assert report["requested_seconds"] is None


def test_structure_accepts_a_total_inside_the_tolerance() -> None:
    """Tolerance matches the delivery-QC default of 1.0 s."""

    def codes(request: str) -> set[str]:
        return {i["code"] for i in audit_combat_structure(_shots(), request=request)["issues"]}

    assert "combat.structure.total_duration_mismatch" not in codes("30秒打斗")
    assert "combat.structure.total_duration_mismatch" not in codes("29秒打斗")
    assert "combat.structure.total_duration_mismatch" not in codes("31秒打斗")
    assert "combat.structure.total_duration_mismatch" in codes("28秒打斗")
    assert "combat.structure.total_duration_mismatch" in codes("32秒打斗")


def test_structure_reports_unreadable_shot_entries() -> None:
    """Measured: a non-object entry was silently dropped and the audit passed."""

    raw = [*_shots(), "junk", None]
    report = audit_combat_structure(raw)

    assert report["shot_count"] == 6
    assert report["unreadable_shot_count"] == 2
    assert "combat.structure.shot_unreadable" in {i["code"] for i in report["issues"]}
    assert report["passed"] is False


def test_compile_carries_the_requested_duration_into_the_fixture_gate() -> None:
    report = compile_combat_film_contract(shots=_shots(), request=_request())

    assert report["structure"]["total_seconds"] == 30.0
    assert report["structure"]["requested_seconds"] == 30.0
    assert report["ready_for_media"] is True

    truncated = _shots()[:1]
    blocked = compile_combat_film_contract(shots=truncated, request=_request())
    assert blocked["gate_observations"][COMBAT_FILM_STRUCTURE_GATE] is False
    assert blocked["ready_for_media"] is False


# --- empty input is never a pass --------------------------------------------


def test_empty_shot_lists_are_never_a_pass() -> None:
    """Measured before this guard: empty input returned ``passed=True``.

    That contradicts the module's own ``missing_evidence_is_not_passed`` policy
    and is exactly the silent green light it exists to remove.
    """

    assert audit_combat_damage_continuity([])["passed"] is False
    assert "combat.damage.evidence_missing" in {
        i["code"] for i in audit_combat_damage_continuity([])["issues"]
    }
    assert audit_combat_cinematic_consistency([])["passed"] is False
    assert "combat.cinematic.evidence_missing" in {
        i["code"] for i in audit_combat_cinematic_consistency([])["issues"]
    }


def test_single_shot_is_still_audited_for_damage_and_cinematic_families() -> None:
    """A one-shot fight must not slip past the sub-audits that feed the gate."""

    one = [{"shot_id": "S01", "duration_seconds": 5.0, "cinematic": {"combat": {"damage_state": "intact"}}}]

    assert audit_combat_damage_continuity(one)["passed"] is True
    assert audit_combat_cinematic_consistency(one)["passed"] is False


# --- declarations -----------------------------------------------------------


def test_declarations_pass_on_the_fixture() -> None:
    report = audit_combat_declarations(_shots())

    assert report["passed"] is True
    assert report["dialogue_line_count"] == 4


def test_declarations_fail_closed_when_the_block_is_absent() -> None:
    shots = _shots()
    assert combat_declaration(shots[0])["duel_scope"] == "1v1"

    for shot in shots:
        shot["cinematic"].pop("combat")
    report = audit_combat_declarations(shots)

    assert report["passed"] is False
    codes = {i["code"] for i in report["issues"]}
    assert "combat.declarations.missing" in codes
    assert report["dialogue_line_count"] == 0


def test_declarations_missing_evidence_is_false_never_none() -> None:
    """Fail-closed, matching film_production_contract._gate_ready."""

    bare = [{"shot_id": "S01", "duration_seconds": 5.0}]
    report = compile_combat_film_contract(shots=bare, request=_request())

    assert report["gate_observations"][COMBAT_FILM_STRUCTURE_GATE] is False
    assert report["gate_observations"][COMBAT_FILM_DECLARATIONS_GATE] is False
    assert all(value is not None for value in report["gate_observations"].values())


def test_declarations_flag_a_broken_causality_chain() -> None:
    shots = _shots()
    _combat(shots[2])["causality_chain"] = ["contact", "compression", "failure"]
    report = audit_combat_declarations(shots)

    assert report["passed"] is False
    assert "combat.declarations.causality_chain_invalid" in {
        i["code"] for i in report["issues"]
    }
    assert list(CAUSALITY_CHAIN) == ["contact", "compression", "failure", "no_recovery"]


def test_declarations_flag_a_reordered_causality_chain() -> None:
    shots = _shots()
    _combat(shots[1])["causality_chain"] = [
        "contact",
        "failure",
        "compression",
        "no_recovery",
    ]
    assert audit_combat_declarations(shots)["passed"] is False


def test_declarations_flag_a_two_versus_one_fight() -> None:
    shots = _shots()
    _combat(shots[0])["combatant_count"] = 3
    report = audit_combat_declarations(shots)

    assert report["passed"] is False
    assert "combat.declarations.combatant_count_invalid" in {
        i["code"] for i in report["issues"]
    }


def test_declarations_flag_identity_drift_between_shots() -> None:
    shots = _shots()
    _combat(shots[3])["combatant_ids"] = ["char-lin", "char-other"]
    report = audit_combat_declarations(shots)

    assert report["passed"] is False
    assert "combat.declarations.combatant_identity_drift" in {
        i["code"] for i in report["issues"]
    }


def test_declarations_flag_a_non_1v1_duel_scope() -> None:
    shots = _shots()
    _combat(shots[0])["duel_scope"] = "1v2"
    report = audit_combat_declarations(shots)

    assert "combat.declarations.duel_scope_invalid" in {
        i["code"] for i in report["issues"]
    }


# --- damage continuity ------------------------------------------------------


def test_damage_states_are_monotonic_in_the_fixture() -> None:
    report = audit_combat_damage_continuity(_shots())

    assert report["passed"] is True
    assert [item["damage_state"] for item in report["states"]] == [
        "intact",
        "intact",
        "intact",
        "minor",
        "severe",
        "down",
    ]
    assert list(DAMAGE_STATES) == ["intact", "minor", "severe", "down"]


def test_damage_continuity_flags_a_healed_wound() -> None:
    shots = _shots()
    _combat(shots[4])["damage_state"] = "intact"
    report = audit_combat_damage_continuity(shots)

    assert report["passed"] is False
    assert "combat.damage.regressed" in {i["code"] for i in report["issues"]}
    issues = compile_combat_film_contract(shots=shots, request=_request())
    assert issues["gate_observations"][COMBAT_FILM_DECLARATIONS_GATE] is False


def test_damage_continuity_flags_one_step_of_recovery() -> None:
    shots = _shots()
    _combat(shots[4])["damage_state"] = "down"
    _combat(shots[5])["damage_state"] = "severe"
    report = audit_combat_damage_continuity(shots)

    assert report["passed"] is False
    assert "combat.damage.regressed" in {i["code"] for i in report["issues"]}
    assert [item["damage_state"] for item in report["states"]][-2:] == ["down", "severe"]


def test_damage_continuity_accepts_holding_a_state() -> None:
    shots = _shots()
    _combat(shots[3])["damage_state"] = "severe"
    _combat(shots[4])["damage_state"] = "severe"
    assert audit_combat_damage_continuity(shots)["passed"] is True


def test_damage_continuity_rejects_an_unknown_state() -> None:
    shots = _shots()
    _combat(shots[2])["damage_state"] = "broken"
    report = audit_combat_damage_continuity(shots)

    assert "combat.damage.state_invalid" in {i["code"] for i in report["issues"]}


def test_damage_continuity_does_not_use_continuity_in_or_out() -> None:
    """``continuity_in/out`` is auto-backfilled, so comparing it is always true."""

    shots = _shots()
    _combat(shots[4])["damage_state"] = "intact"
    for index, shot in enumerate(shots):
        shot["continuity_in"] = {"damage_state": "intact"}
        shot["continuity_out"] = {"damage_state": "intact"}
    report = audit_combat_damage_continuity(shots)

    assert report["passed"] is False, "damage gate must read cinematic.combat"


# --- cinematic declaration families -----------------------------------------


def test_cinematic_consistency_requires_all_five_families() -> None:
    shots = _shots()
    del shots[0]["cinematic"]["sound"]
    report = audit_combat_cinematic_consistency(shots)

    assert report["passed"] is False
    assert "combat.cinematic.family_missing" in {i["code"] for i in report["issues"]}


def test_cinematic_consistency_does_not_silently_pass_without_declarations() -> None:
    """Without the presence check the shared audit returns applicable_gates=[]."""

    from novelvideo.production.cinematic_contract import audit_cinematic_contracts

    bare = [
        {"shot_id": "S01", "action": "出拳"},
        {"shot_id": "S02", "action": "格挡"},
    ]
    shared = audit_cinematic_contracts(bare)
    assert shared["passed"] is True and shared["applicable_gates"] == []

    report = audit_combat_cinematic_consistency(bare)
    assert report["passed"] is False
    assert "combat.cinematic.family_missing" in {i["code"] for i in report["issues"]}


def test_family_gate_map_matches_the_shared_cinematic_audit() -> None:
    """The family is "declared" only when the shared audit claims its gate."""

    from novelvideo.production.cinematic_contract import CINEMATIC_QUALITY_GATES

    assert [family for family, _ in CINEMATIC_FAMILY_GATES] == list(CINEMATIC_FAMILIES)
    gates = [gate for _, gate in CINEMATIC_FAMILY_GATES]
    assert len(set(gates)) == len(gates)
    assert set(gates) <= set(CINEMATIC_QUALITY_GATES)


def test_shared_audit_claims_no_family_for_placeholder_blocks() -> None:
    """The mechanism fix A relies on, pinned on the shared audit itself."""

    from novelvideo.production.cinematic_contract import audit_cinematic_contracts

    shot = {
        "shot_id": "S01",
        "cinematic": {family: {"placeholder": True} for family in CINEMATIC_FAMILIES},
    }
    shared = audit_cinematic_contracts([shot])

    assert shared["applicable_gates"] == []
    assert set(shared["gate_observations"].values()) == {None}


def test_placeholder_cinematic_families_are_not_declarations() -> None:
    """Measured gap: four placeholder families passed the gate with ``issues=0``.

    ``{"placeholder": True}`` / ``{"status": "todo"}`` / ``{"tbd": 1}`` /
    ``{"note": "占位"}`` are non-empty mappings, and non-emptiness used to be the
    whole test.  A family counts only when the shared audit claims it.
    """

    shots = _shots()
    for shot in shots:
        shot["cinematic"]["lighting"] = {"placeholder": True}
        shot["cinematic"]["color_look"] = {"status": "todo"}
        shot["cinematic"]["screen_direction"] = {"tbd": 1}
        shot["cinematic"]["edit"] = {"note": "占位"}
        shot["cinematic"]["sound"] = {"ambience": ["待定"]}

    report = audit_combat_declarations(shots)
    assert report["passed"] is False
    assert report["issues"]
    missing = [
        issue
        for issue in report["issues"]
        if issue["code"] == "combat.cinematic.family_missing"
    ]
    assert missing
    for family in ("lighting", "color_look", "screen_direction", "edit"):
        assert any(family in issue["message"] for issue in missing), family

    compiled = compile_combat_film_contract(shots=shots, request=_request())
    assert compiled["gate_observations"][COMBAT_FILM_DECLARATIONS_GATE] is False
    assert compiled["ready_for_media"] is False


def test_all_placeholder_families_also_fail() -> None:
    """Five placeholder families must not be a pass either."""

    shots = _shots()
    for shot in shots:
        for family in CINEMATIC_FAMILIES:
            shot["cinematic"][family] = {"placeholder": True}

    compiled = compile_combat_film_contract(shots=shots, request=_request())

    assert compiled["issues"]
    assert compiled["gate_observations"][COMBAT_FILM_DECLARATIONS_GATE] is False
    assert compiled["ready_for_media"] is False


def test_a_placeholder_family_is_missing_even_when_other_shots_declare_it() -> None:
    """Applicability is per shot: a good neighbour must not cover a placeholder."""

    shots = _shots()
    shots[2]["cinematic"]["lighting"] = {"x": 1}

    report = audit_combat_declarations(shots)
    hits = [
        issue
        for issue in report["issues"]
        if issue["code"] == "combat.cinematic.family_missing"
    ]

    assert report["passed"] is False
    assert [issue["shot_id"] for issue in hits] == ["S03"]
    assert "lighting" in hits[0]["message"]


def test_cinematic_consistency_flags_a_lighting_flip() -> None:
    shots = _shots()
    shots[3]["cinematic"]["lighting"]["source_direction"] = "画面右侧台灯"
    report = audit_combat_cinematic_consistency(shots)

    assert report["passed"] is False
    codes = {i["code"] for i in report["issues"]}
    assert "lighting.source_direction_inconsistent" in codes


def test_cinematic_consistency_flags_an_out_of_range_beat() -> None:
    shots = _shots()
    shots[0]["cinematic"]["edit"]["beat_seconds"] = 1.5
    report = audit_combat_cinematic_consistency(shots)

    assert report["passed"] is False
    assert "edit.beat_out_of_range" in {i["code"] for i in report["issues"]}


def test_cinematic_consistency_flags_a_broken_color_ratio_sum() -> None:
    shots = _shots()
    shots[2]["cinematic"]["color_look"]["ratios"] = {
        "dominant": 0.5,
        "secondary": 0.2,
        "accent": 0.1,
    }
    report = audit_combat_cinematic_consistency(shots)

    assert report["passed"] is False
    assert "color.ratios_invalid" in {i["code"] for i in report["issues"]}


def test_cinematic_consistency_flags_a_look_id_change() -> None:
    shots = _shots()
    shots[5]["cinematic"]["color_look"]["look_id"] = "another-look-v1"
    report = audit_combat_cinematic_consistency(shots)

    assert "color.look_changed" in {i["code"] for i in report["issues"]}


def test_cinematic_consistency_flags_an_axis_change() -> None:
    shots = _shots()
    shots[2]["cinematic"]["screen_direction"]["axis_id"] = "other-axis"
    report = audit_combat_cinematic_consistency(shots)

    assert "screen_direction.axis_changed" in {i["code"] for i in report["issues"]}


# --- declared dialogue ------------------------------------------------------


def test_declared_dialogue_is_judged_not_merely_counted() -> None:
    shots = _shots()
    _combat(shots[1])["dialogue_lines"] = ["刀客：台词：你必须说这一句"]
    report = audit_combat_dialogue_declarations(shots)

    assert report["passed"] is False
    assert "dialogue.meta_direction_leaked" in {i["code"] for i in report["issues"]}


def test_declared_dialogue_flags_an_empty_slot() -> None:
    shots = _shots()
    _combat(shots[1])["dialogue_lines"] = [""]

    assert "combat.dialogue.empty_line" in {
        i["code"] for i in audit_combat_declarations(shots)["issues"]
    }


def test_declared_dialogue_is_skipped_when_no_shot_declares_any() -> None:
    shots = _shots()
    for shot in shots:
        _combat(shot)["dialogue_lines"] = []
    report = audit_combat_dialogue_declarations(shots)

    assert report["applied"] is False
    assert report["reason"] == "no_declared_lines"
    assert report["passed"] is True


def test_dialogue_requested_but_nothing_declared_fails() -> None:
    shots = _shots()
    for shot in shots:
        _combat(shot)["dialogue_lines"] = []
    report = audit_combat_declarations(shots, require_dialogue=True)

    assert report["passed"] is False
    assert "combat.dialogue.missing" in {i["code"] for i in report["issues"]}


def test_dialogue_request_is_satisfied_by_the_fixture() -> None:
    report = compile_combat_film_contract(shots=_shots(), request=_request())

    assert report["declarations"]["require_dialogue"] is True
    assert report["ready_for_media"] is True


@pytest.mark.parametrize("mutation", ["causality", "damage", "identity", "dialogue"])
def test_every_mutation_fails_both_the_gate_and_the_ready_flag(mutation: str) -> None:
    shots = _shots()
    if mutation == "causality":
        _combat(shots[2])["causality_chain"] = ["contact"]
    elif mutation == "damage":
        _combat(shots[4])["damage_state"] = "intact"
    elif mutation == "identity":
        _combat(shots[3])["combatant_ids"] = ["char-lin", "char-other"]
    else:
        _combat(shots[1])["dialogue_lines"] = [""]
    report = compile_combat_film_contract(shots=shots, request=_request())

    assert report["gate_observations"][COMBAT_FILM_DECLARATIONS_GATE] is False
    assert report["ready_for_media"] is False


def test_compilation_is_deterministic() -> None:
    first = compile_combat_film_contract(shots=_shots(), request=_request())
    second = compile_combat_film_contract(shots=_shots(), request=_request())

    assert first["gate_observations"] == second["gate_observations"]
    assert first["issues"] == second["issues"]
