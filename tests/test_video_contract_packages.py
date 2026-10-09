"""T-226：视频出线合同的「数据包」层。

这一层只主张两件事，本文件就只验证这两件事：
1. 把合同从代码搬进数据以后，编译出来的上游报文逐字节不变；
2. 数据包写错（拼错字段、重复档案、抢同一个命中词）在导入期就报错，
   不靠排序静默裁决 —— 2026-09-12 的时长截断事故正是这一类。
"""

from __future__ import annotations

import json
import dataclasses
from pathlib import Path

import pytest

from novelvideo.generators.video.contract_packages import (
    SCHEMA_VERSION,
    ContractPackageError,
    find_duplicate_match_tokens,
    find_match_shadows,
    load_contract_packages,
    resolve_declared_profile,
)
from novelvideo.generators.video.upstream_profiles import (
    DEFAULT_PROFILE,
    PROFILES,
    VideoUpstreamProfile,
    _ACKNOWLEDGED_SHADOWS,
    _assert_acknowledged_shadows,
    _declared_profile,
    compile_payload,
    declared_profile_ids,
    describe_profile_match,
    parse_task_response,
    resolve_profile,
)

FIXTURE = Path(__file__).with_name("fixtures") / "video_contract_packages_snapshot_v1.json"
SURFACE_FIXTURE = Path(__file__).with_name("fixtures") / "video_contract_packages_surface_v1.json"
CONTRACTS_DIR = (
    Path(__file__).resolve().parents[1] / "src" / "novelvideo" / "generators" / "video" / "contracts"
)

MODEL_KEYS = (
    "sd-2.0-fast",
    "sd-2.0-fast-v1",
    "sd2",
    "sd_2",
    "sd2.0-480p",
    "sd2fast",
    "sd-2.5-M-720P-v1",
    "kling-3.0-omni",
    "veo-3.1",
    "seedance-2.0",
    "firefly-seedance2-480p",
    "s-videos-f-933-fast-480-2",
    "happyhorse-1.0",
    "totally-unknown-model",
)
DURATIONS = (5, 8, 30)
ASPECTS = ("16:9", "9:16")
RESPONSE_SAMPLES = (
    {"status": "succeeded", "id": "task-1", "video_url": "https://example.invalid/a.mp4"},
    {"data": {"state": "processing", "task_id": "task-2", "progress": 42}},
    {"data": {"status": "failed", "error": {"message": "boom"}}},
    {"task": {"status": "completed", "result": {"url": "https://example.invalid/b.mp4"}}},
)


def _current_snapshot() -> dict[str, object]:
    models: dict[str, object] = {}
    for model_key in MODEL_KEYS:
        profile = resolve_profile(model_key)
        payloads: dict[str, object] = {}
        for duration in DURATIONS:
            for aspect in ASPECTS:
                payloads[f"duration={duration}|aspect={aspect}"] = compile_payload(
                    model_key=model_key,
                    prompt="画面描述",
                    duration_seconds=duration,
                    aspect_ratio=aspect,
                    first_frame_uri="https://example.invalid/first.png",
                    reference_uris=["https://example.invalid/ref.png"],
                    auto_face=True,
                    generate_audio=True,
                    profile=profile,
                )
        models[model_key] = {
            "resolved_profile_id": profile.profile_id,
            "duration_choices": list(profile.duration_choices) if profile.duration_choices else None,
            "create_path": profile.create_path,
            "query_path": profile.query_path,
            "payloads": payloads,
        }

    responses: dict[str, object] = {}
    for index, sample in enumerate(RESPONSE_SAMPLES):
        parsed = parse_task_response(sample)
        responses[f"sample_{index}"] = {
            "task_id": parsed.task_id,
            "status": parsed.status,
            "result_url": parsed.result_url,
            "error": parsed.error,
            "progress": parsed.progress,
        }
    return {"models": models, "responses": responses}


def test_declared_packages_compile_byte_identical_to_frozen_snapshot() -> None:
    """改造前冻结的真实报文，改造后必须逐字节一致（含命中顺序）。

    先比结构（不一致时给可读的 diff），再比同规范序列化出来的字符串，
    这样「逐字节」是字面成立的，而不是只看字典相等。
    """

    frozen = FIXTURE.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert _current_snapshot() == json.loads(frozen)

    current = json.dumps(_current_snapshot(), ensure_ascii=False, indent=2, sort_keys=True)
    assert current == frozen


def _jsonable(value: object) -> object:
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in sorted(value.items())}
    return value


def _current_surface() -> dict[str, object]:
    """每条合同的全部字段 + 对 7 个响应样本的解析结果。"""

    profiles: dict[str, object] = {}
    for profile in (*PROFILES, DEFAULT_PROFILE):
        record = {
            field.name: _jsonable(getattr(profile, field.name))
            for field in dataclasses.fields(profile)
        }
        responses = []
        for sample in SURFACE_RESPONSE_SAMPLES:
            parsed = parse_task_response(sample, profile)
            responses.append(
                {
                    "task_id": parsed.task_id,
                    "status": parsed.status,
                    "result_url": parsed.result_url,
                    "error": parsed.error,
                    "progress": parsed.progress,
                }
            )
        record["__responses"] = responses
        profiles[profile.profile_id] = record
    return {"profiles": profiles, "response_sample_count": len(SURFACE_RESPONSE_SAMPLES)}


SURFACE_RESPONSE_SAMPLES = (
    {"status": "succeeded", "id": "task-1", "video_url": "https://example.invalid/a.mp4"},
    {"data": {"state": "processing", "task_id": "task-2", "progress": 42}},
    {"data": {"status": "failed", "error": {"message": "boom"}}},
    {"task": {"status": "completed", "result": {"url": "https://example.invalid/b.mp4"}}},
    {"state": "queued"},
    {"percent": 7, "status": "running"},
    {"error": "plain string error"},
)


def test_contract_surface_is_frozen_field_by_field() -> None:
    """8 条合同（+兜底）的全部字段与响应解析结果必须逐字节不变。

    报文快照只覆盖「发出去的请求体」；这里覆盖剩下那半 —— status_keys / url_keys /
    task_id_keys / completed_values / failed_values / progress_keys 这些**不会出现在
    请求体里**的参数。用户要的「挪参数不会出错」两半都要钉住。
    """

    frozen = SURFACE_FIXTURE.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert _current_surface() == json.loads(frozen)
    current = json.dumps(_current_surface(), ensure_ascii=False, indent=2, sort_keys=True)
    assert current == frozen


def test_huabu_contract_is_the_loaded_package_object() -> None:
    """huabu 的合同必须真的来自数据包，而不是代码里残留的第二份副本。"""

    packages = load_contract_packages(CONTRACTS_DIR)
    huabu_specs = [
        spec for package in packages for spec in package.profiles if spec["profile_id"] == "huabu"
    ]
    assert len(huabu_specs) == 1

    from_package = VideoUpstreamProfile(**huabu_specs[0])
    in_profiles = [profile for profile in PROFILES if profile.profile_id == "huabu"]
    assert len(in_profiles) == 1
    assert in_profiles[0] == from_package
    assert "huabu" in declared_profile_ids()


def test_every_declared_family_comes_from_a_package() -> None:
    """数据包不是只为一家写的：至少两条不同形状的家族都从数据来。"""

    packages = load_contract_packages(CONTRACTS_DIR)
    from_packages = {
        spec["profile_id"]: spec for package in packages for spec in package.profiles
    }
    assert set(from_packages) <= set(declared_profile_ids())
    assert {"huabu", "seedance2_native"} <= set(from_packages)

    # seedance2 是第一条带路径模板的合同，证明 schema 够表达「路径」而不只是字段名。
    seedance = VideoUpstreamProfile(**from_packages["seedance2_native"])
    assert seedance.create_path == "/v1/video/generations"
    assert seedance.query_path == "/v1/video/generations/{task_id}"
    in_profiles = [p for p in PROFILES if p.profile_id == "seedance2_native"]
    assert in_profiles == [seedance]


# 迁移完成后，所有「会被匹配到」的档案都必须来自数据包。留空表示一条都不许例外；
# 以后新加家族若只想写 Python，这条测试会红，逼出一次明确决定（要么写包，要么登记豁免）。
CODE_ONLY_MATCHABLE_PROFILES: frozenset[str] = frozenset()

# 三条近似合同：刻意保持成各自完整的条目（不合并成共享基类），
# 它们的关系由下面这张表钉死，任何单边改动都会红。
NEAR_IDENTICAL_TRIO = ("prompt_hubs_firefly", "kacang_933_fast", "prompt_hubs_flex")

# 这三条里必须逐字一致的字段。改上游常见动作就是改这些，单边改一处会漏掉另两处。
TRIO_SHARED_FIELDS = (
    "create_path",
    "query_path",
    "duration_field",
    "ratio_field",
    "first_frame_field",
    "ref_field",
    # 响应解析键：三条都吃默认值，必须保持一致（改一家不改另两家会读错任务状态）
    "status_keys",
    "completed_values",
    "failed_values",
    "url_keys",
    "progress_keys",
    "task_id_keys",
)

# 允许不同、但必须在这里逐条登记的差异；值就是「必须恰好等于」的期望值。
TRIO_ALLOWED_DIFFERENCES: dict[str, dict[str, object]] = {
    "profile_id": {
        "prompt_hubs_firefly": "prompt_hubs_firefly",
        "kacang_933_fast": "kacang_933_fast",
        "prompt_hubs_flex": "prompt_hubs_flex",
    },
    "duration_choices": {
        "prompt_hubs_firefly": None,
        "kacang_933_fast": None,
        "prompt_hubs_flex": None,
    },
    "match_prefixes": {
        "prompt_hubs_firefly": ("firefly-seedance2", "firefly-seedance"),
        "kacang_933_fast": (),
        "prompt_hubs_flex": ("veo-", "kling-", "runway-", "sora-", "gemini-omni"),
    },
    "match_exact": {
        "prompt_hubs_firefly": (),
        "kacang_933_fast": ("s-videos-f-933-fast-480-2",),
        "prompt_hubs_flex": (),
    },
    "audio_field": {
        "prompt_hubs_firefly": "generate_audio",
        "kacang_933_fast": "generate_audio",
        "prompt_hubs_flex": None,
    },
    "auto_face_field": {
        "prompt_hubs_firefly": "auto_face",
        "kacang_933_fast": "auto_face",
        "prompt_hubs_flex": None,
    },
    "extra_static": {
        "prompt_hubs_firefly": {"resolution": "480p"},
        "kacang_933_fast": {"resolution": "480p"},
        "prompt_hubs_flex": {},
    },
    "drop_fields": {
        "prompt_hubs_firefly": ("seconds",),
        "kacang_933_fast": ("seconds",),
        "prompt_hubs_flex": (),
    },
}


def test_every_matchable_profile_comes_from_a_package() -> None:
    """地基条款：能被匹配到的合同只有数据包一个来源，代码里不许再留副本。"""

    code_only = {
        profile.profile_id for profile in PROFILES if profile.profile_id not in declared_profile_ids()
    }
    assert code_only == set(CODE_ONLY_MATCHABLE_PROFILES)
    assert len(declared_profile_ids()) == len(PROFILES) == 8


def test_each_declared_package_rebuilds_its_shipped_profile_exactly() -> None:
    """每个数据包声明的档案，必须与真正在用的那份对象完全相等（没有第二份真值）。"""

    packages = load_contract_packages(CONTRACTS_DIR)
    rebuilt = {
        spec["profile_id"]: VideoUpstreamProfile(**spec)
        for package in packages
        for spec in package.profiles
    }
    in_use = {profile.profile_id: profile for profile in PROFILES}
    assert set(rebuilt) == set(in_use)
    for profile_id, profile in rebuilt.items():
        assert in_use[profile_id] == profile, f"{profile_id} 的数据包与在用对象不一致"


def test_near_identical_trio_keeps_the_registered_relationship() -> None:
    """三条近似合同：共享字段必须一致，允许不同的必须与登记表逐字相符。

    不合并成共享基类的代价就是可能漂移；这条守卫把代价补回来 —— 改一处漏两处会红。
    """

    by_id = {profile.profile_id: profile for profile in PROFILES}
    for profile_id in NEAR_IDENTICAL_TRIO:
        assert profile_id in by_id, f"三条近似合同里的 {profile_id} 不见了"

    for field_name in TRIO_SHARED_FIELDS:
        values = {profile_id: getattr(by_id[profile_id], field_name) for profile_id in NEAR_IDENTICAL_TRIO}
        assert len(set(values.values())) == 1, f"{field_name} 在三条近似合同之间不一致：{values}"

    registered = set(TRIO_ALLOWED_DIFFERENCES)
    every_field = {f.name for f in dataclasses.fields(VideoUpstreamProfile)}
    unregistered = every_field - set(TRIO_SHARED_FIELDS) - registered
    assert unregistered == set(), f"有新字段没有归类到「必须一致」或「允许不同」：{sorted(unregistered)}"

    for field_name, expected in TRIO_ALLOWED_DIFFERENCES.items():
        actual = {profile_id: getattr(by_id[profile_id], field_name) for profile_id in NEAR_IDENTICAL_TRIO}
        assert actual == expected, f"{field_name} 与登记的差异不符：{actual}"


def test_retiring_a_family_cannot_go_unnoticed() -> None:
    """删掉一个家族的合同时必须被看见：受影响的 id 会掉进宽松兜底，那等于静默换合同。

    这里的清单是**故意写死**的：改动档案匹配或删除家族时它会红，逼出一次明确决定。
    """

    declared_families = {
        # huabu
        "sd-2.0-fast": "huabu",
        "sd-2.0-fast-v1": "huabu",
        "sd_2": "huabu",
        # sd2_5_m_full
        "sd-2.5-M-720P-v1": "sd2_5_m_full",
        # prompt_hubs_sd
        "sd2.0-480p": "prompt_hubs_sd",
        "sd2fast": "prompt_hubs_sd",
        # seedance2_native
        "seedance-2.0": "seedance2_native",
        "seedance-1.0-pro-fast": "seedance2_native",
        # prompt_hubs_firefly
        "firefly-seedance2-fast-480p": "prompt_hubs_firefly",
        "firefly-seedance2-fast-720p": "prompt_hubs_firefly",
        # prompt_hubs_flex
        "kling-3.0-omni": "prompt_hubs_flex",
        "veo-3.1-lite": "prompt_hubs_flex",
        "gemini-omni-flash": "prompt_hubs_flex",
        # kacang_933_fast
        "s-videos-f-933-fast-480-2": "kacang_933_fast",
        # happyhorse
        "happyhorse-1.0": "happyhorse",
    }
    drifted = {
        model_key: resolve_profile(model_key).profile_id
        for model_key, expected in declared_families.items()
        if resolve_profile(model_key).profile_id != expected
    }
    assert drifted == {}

    # 兜底只应留给真正没有合同的型号，不能成为删除档案后的静默去处。
    fallback_expected = {"brand-new-gateway-model", "totally-unknown-model"}
    fallback_actual = {
        model_key
        for model_key in (*declared_families, *fallback_expected)
        if resolve_profile(model_key).profile_id == DEFAULT_PROFILE.profile_id
    }
    assert fallback_actual == fallback_expected


def test_huabu_package_keeps_the_measured_wire_contract() -> None:
    """2026-09-12 实测事实：只认 5/10/15 秒，不收 size 参数，首帧字段是 image。"""

    profile = resolve_profile("sd-2.0-fast")
    assert profile.profile_id == "huabu"
    assert profile.duration_choices == (5, 10, 15)
    assert profile.ratio_field is None
    assert profile.first_frame_field == "image"

    payload = compile_payload(
        model_key="sd-2.0-fast",
        prompt="p",
        duration_seconds=30,
        aspect_ratio="9:16",
        profile=profile,
    )
    assert payload["duration"] == 15
    assert "size" not in payload and "ratio" not in payload


def test_package_data_changes_behaviour_without_touching_code(tmp_path: Path) -> None:
    """同一个档案换成数据包以后，改档位只需要改 JSON。"""

    package = {
        "schemaVersion": SCHEMA_VERSION,
        "packageId": "tmp-huabu",
        "authority": "seed",
        "notes": ["临时包：验证数据真的驱动行为"],
        "profiles": [
            {
                "profile_id": "huabu",
                "match_prefixes": ["sd2", "sd-2", "sd_2"],
                "duration_field": "duration",
                "duration_choices": [4, 8, 12],
                "ratio_field": None,
                "first_frame_field": "image",
            }
        ],
    }
    (tmp_path / "huabu-sd2.json").write_text(json.dumps(package), encoding="utf-8")

    loaded = load_contract_packages(tmp_path)
    assert len(loaded) == 1
    profile = VideoUpstreamProfile(**loaded[0].profiles[0])
    payload = compile_payload(
        model_key="sd-2.0-fast",
        prompt="p",
        duration_seconds=30,
        aspect_ratio="16:9",
        profile=profile,
    )
    assert payload["duration"] == 12


def test_missing_declared_profile_fails_loudly() -> None:
    with pytest.raises(ContractPackageError) as excinfo:
        _declared_profile("not-a-real-profile")
    assert "not-a-real-profile" in str(excinfo.value)


def test_builtin_profiles_do_not_share_a_match_token() -> None:
    duplicates = find_duplicate_match_tokens(
        (profile.profile_id, profile.match_prefixes, profile.match_exact) for profile in PROFILES
    )
    assert duplicates == {}


def _write(tmp_path: Path, payload: object, name: str = "pkg.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _valid_package(**overrides: object) -> dict[str, object]:
    package: dict[str, object] = {
        "schemaVersion": SCHEMA_VERSION,
        "packageId": "pkg",
        "authority": "seed",
        "profiles": [{"profile_id": "a", "match_prefixes": ["a-"]}],
    }
    package.update(overrides)
    return package


@pytest.mark.parametrize(
    ("package", "expected"),
    [
        (_valid_package(schemaVersion=SCHEMA_VERSION + 1), "schemaVersion"),
        (_valid_package(oops=True), "未知字段"),
        # 随源码分发的包必须是显式声明的种子；缺这个字段就报错，防止有人把源码
        # 里那份当成权威合同又接回来。
        (_valid_package(authority="authoritative"), "authority"),
        (
            {key: value for key, value in _valid_package().items() if key != "authority"},
            "authority",
        ),
        (
            _valid_package(
                profiles=[{"profile_id": "a", "match_prefixes": ["a-"], "duraton_choices": [5]}]
            ),
            "未知字段",
        ),
        (_valid_package(profiles=[{"profile_id": "a"}]), "命中规则"),
        (
            _valid_package(
                profiles=[{"profile_id": "a", "match_prefixes": ["a-"], "duration_choices": [0]}]
            ),
            "正整数",
        ),
        (
            _valid_package(
                profiles=[
                    {"profile_id": "a", "match_prefixes": ["a-"], "duration_choices": [5, 5]}
                ]
            ),
            "重复档位",
        ),
        (
            _valid_package(
                profiles=[
                    {"profile_id": "a", "match_prefixes": ["a-"]},
                    {"profile_id": "a", "match_prefixes": ["b-"]},
                ]
            ),
            "profile_id",
        ),
    ],
)
def test_package_validation_errors(tmp_path: Path, package: dict[str, object], expected: str) -> None:
    _write(tmp_path, package)
    with pytest.raises(ContractPackageError) as excinfo:
        load_contract_packages(tmp_path)
    assert expected in str(excinfo.value)


def test_duplicate_match_token_is_reported_across_packages(tmp_path: Path) -> None:
    _write(
        tmp_path,
        _valid_package(packageId="one", profiles=[{"profile_id": "one", "match_prefixes": ["shared-"]}]),
        name="one.json",
    )
    _write(
        tmp_path,
        _valid_package(packageId="two", profiles=[{"profile_id": "two", "match_prefixes": ["shared-"]}]),
        name="two.json",
    )
    packages = load_contract_packages(tmp_path)
    duplicates = find_duplicate_match_tokens(
        (spec["profile_id"], spec.get("match_prefixes", ()), spec.get("match_exact", ()))
        for package in packages
        for spec in package.profiles
    )
    assert duplicates == {"shared-": ("one", "two")}


def test_empty_directory_yields_no_packages(tmp_path: Path) -> None:
    assert load_contract_packages(tmp_path / "missing") == ()
    assert load_contract_packages(tmp_path) == ()


# ---------------------------------------------------------------------------
# 命中裁决：从「靠顺序」改成「按具体度」
# ---------------------------------------------------------------------------

# 这些 id 都真实出现在本仓源码/文档/测试里（完整 296 条语料见
# workspace/artifacts/t226b_harvest_model_ids.py 的产出）。它们专门覆盖
# 「一个 id 同时命中多条档案」的边界，是这次改地基的靶心。
AMBIGUOUS_MODEL_IDS = {
    # huabu 的 sd-2 前缀 vs sd2_5_m_full
    "sd-2.5-M-720P-v1": "sd2_5_m_full",
    "sd-2.5-M-720p": "sd2_5_m_full",
    # huabu 的 sd2 前缀 vs prompt_hubs_sd
    "sd2.0-480p": "prompt_hubs_sd",
    "sd2.0-fast": "prompt_hubs_sd",
    "sd2.0-1080p-4k-pro": "prompt_hubs_sd",
    "sd2.0-15s": "prompt_hubs_sd",
    # huabu 自己
    "sd-2": "huabu",
    "sd-2.0-fast": "huabu",
    "sd-2.0-fast-v1": "huabu",
    "sd2": "huabu",
    "sd_2": "huabu",
    # 其它家族
    "kling-3.0-omni": "prompt_hubs_flex",
    "veo-3.1-lite": "prompt_hubs_flex",
    "seedance-2.0": "seedance2_native",
    "firefly-seedance2-fast-480p": "prompt_hubs_firefly",
    "s-videos-f-933-fast-480-2": "kacang_933_fast",
    "happyhorse-1.0": "happyhorse",
}


def _legacy_order_resolution(model_key: str) -> str:
    """改造前的算法：按 PROFILES 顺序，第一条 match 的胜出。"""

    key = str(model_key or "").strip()
    for profile in PROFILES:
        if profile.matches(key):
            return profile.profile_id
    return DEFAULT_PROFILE.profile_id


@pytest.mark.parametrize(("model_key", "expected"), sorted(AMBIGUOUS_MODEL_IDS.items()))
def test_ambiguous_ids_keep_their_established_contract(model_key: str, expected: str) -> None:
    """有歧义的 id 必须仍然走原来那份合同，不能因为改裁决规则而漂移。"""

    assert resolve_profile(model_key).profile_id == expected
    assert resolve_profile(model_key).profile_id == _legacy_order_resolution(model_key)


def test_resolution_ignores_profile_order() -> None:
    """重排档案不改变任何模型走哪份合同（旧算法会变，这正是被修掉的性质）。"""

    import random

    corpus = sorted(AMBIGUOUS_MODEL_IDS) + ["totally-unknown-model", ""]
    baseline = {key: resolve_profile(key).profile_id for key in corpus}

    rng = random.Random(20261001)
    for _ in range(50):
        shuffled = list(PROFILES)
        rng.shuffle(shuffled)
        declarations = tuple((p.profile_id, p.match_prefixes, p.match_exact) for p in shuffled)
        for key in corpus:
            resolved = resolve_declared_profile(declarations, key)
            profile_id = resolved[0] if resolved else DEFAULT_PROFILE.profile_id
            assert profile_id == baseline[key], f"{key} 在重排后改了合同"


def test_exact_match_beats_a_longer_prefix() -> None:
    """精确匹配胜过前缀匹配，即使前缀更长。"""

    declarations = (
        ("exact-one", (), ("sd-2.0-fast",)),
        ("prefix-one", ("sd-2.0-fast-extra",), ()),
    )
    assert resolve_declared_profile(declarations, "sd-2.0-fast")[0] == "exact-one"
    assert resolve_declared_profile(declarations, "sd-2.0-fast-extra-9")[0] == "prefix-one"


def test_every_shadow_is_acknowledged_with_a_reason() -> None:
    """覆盖关系必须逐条登记，并且写清为什么。"""

    shadows = find_match_shadows(
        (profile.profile_id, profile.match_prefixes, profile.match_exact) for profile in PROFILES
    )
    assert {shadow.key for shadow in shadows} == {item[:4] for item in _ACKNOWLEDGED_SHADOWS}
    for loser_token, loser_profile, winner_token, winner_profile, reason in _ACKNOWLEDGED_SHADOWS:
        assert len(reason) >= 10, f"{winner_profile} 压住 {loser_profile} 没写理由"
        assert winner_token.startswith(loser_token)


def test_unregistered_shadow_fails_at_import_time() -> None:
    """新增一条前缀盖住已有档案时必须报错，而不是静默抢走模型。"""

    greedy = VideoUpstreamProfile(
        profile_id="greedy-new-family",
        match_prefixes=("sd-2",),
    )
    with pytest.raises(ContractPackageError) as excinfo:
        _assert_acknowledged_shadows((*PROFILES, greedy))
    assert "未登记的命中覆盖" in str(excinfo.value)


def test_stale_shadow_acknowledgement_fails_at_import_time() -> None:
    """登记表里留下不再存在的覆盖关系也要报错，避免它长期虚高。"""

    without_huabu = tuple(p for p in PROFILES if p.profile_id != "huabu")
    with pytest.raises(ContractPackageError) as excinfo:
        _assert_acknowledged_shadows(without_huabu)
    assert "过期条目" in str(excinfo.value)


def test_describe_profile_match_explains_fallback_and_exactness() -> None:
    assert describe_profile_match("sd-2.0-fast-v1") == {
        "model_key": "sd-2.0-fast-v1",
        "profile_id": "huabu",
        "matched_token": "sd-2.0-fast-v1",
        "exact": True,
        "fallback": False,
    }
    assert describe_profile_match("brand-new-gateway-model") == {
        "model_key": "brand-new-gateway-model",
        "profile_id": DEFAULT_PROFILE.profile_id,
        "matched_token": None,
        "exact": False,
        "fallback": True,
    }


# 已知且**尚未验证**的档位缺口：出线合同按前缀盖住了这些型号，能力档案却不认它们，
# 于是界面按宽口径放行、出线时又按窄口径对齐 —— 用户选 12 秒会静默变成 10 秒。
#
# 这不是这次改出来的，是 `sd2_5_m_full` 的 `sd-2.5-m` 前缀宽于能力档案里那条只认
# `sd-2.5-M-720P-v1` 的规则。要动它必须先有该变体的真实档位证据（属付费路径，
# 本批不做），所以这里显式登记，让它在台账上可见而不是藏在绿灯后面。
KNOWN_UNVERIFIED_TIER_GAPS = frozenset(
    {
        "sd-2.5-M-720p",
    }
)


def _tier_gap_ids() -> set[str]:
    from novelvideo.generators.video.direct_video_profiles import resolve_direct_video_profile

    gaps: set[str] = set()
    for model_key in sorted(AMBIGUOUS_MODEL_IDS):
        wire = resolve_profile(model_key)
        if not wire.duration_choices:
            continue  # 自由档位家族（如 seedance native）本来就由调用方归一
        capability = resolve_direct_video_profile(
            model_key,
            base_url="https://example.invalid/v1",
            protocol="openai-video",
        )
        if not set(wire.duration_choices) <= set(capability.duration):
            gaps.add(model_key)
    return gaps


def test_wire_duration_tiers_agree_with_the_capability_profile() -> None:
    """档位在两处各写一遍（能力档案 vs 出线合同），对不上的只允许是已登记的缺口。

    这是「挪参数」最容易出事的地方：只改了出线合同、忘了改能力档案，节点就会把
    用户能选的档位和真正发出去的档位说成两回事。既有的单条用例只盯 sd-2.5-M-720P，
    这里扩成一张表，并且同时查「有没有新的对不上」和「登记的缺口是不是已经修好」。
    """

    assert _tier_gap_ids() == set(KNOWN_UNVERIFIED_TIER_GAPS)


def test_aligned_families_really_are_aligned() -> None:
    """对齐的那些家族必须真的对齐，不能靠「都在缺口表里」蒙混。"""

    from novelvideo.generators.video.direct_video_profiles import resolve_direct_video_profile

    checked = 0
    for model_key in ("sd-2.0-fast", "sd-2.0-fast-v1", "sd-2.5-M-720P-v1"):
        wire = resolve_profile(model_key)
        capability = resolve_direct_video_profile(
            model_key,
            base_url="https://example.invalid/v1",
            protocol="openai-video",
        )
        assert set(wire.duration_choices) == set(capability.duration), (
            f"{model_key}: 出线合同 {wire.duration_choices} vs 能力档案 {capability.duration}"
        )
        checked += 1
    assert checked == 3


# ---------------------------------------------------------------------------
# 语料出处守卫
# ---------------------------------------------------------------------------

# 本文件里每个型号 id 都必须能在**已提交内容**里找到，或显式登记为「故意编的探针」。
#
# 2026-10-01 事故（记在案）：`sd-2.5-M-1080P-v1` 曾经就在这份语料里。它是本会话写测试时
# 凭空想象的变体，随后采集脚本又从同一个工作树里把它读回来，当成「真实语料」喂给下面的
# 测试 —— 自己证明自己。它还把一条并不存在的「静默降档缺口」写进了验收档。已删除；
# 唯一真实的相关缺口是 `sd-2.5-M-720p`。
#
# 重新核对（需要 git）：`.venv\Scripts\python.exe workspace\artifacts\t226d_provenance_check.py`
COMMITTED_PROVENANCE_IDS = frozenset(
    {
        "sd-2",
        "sd-2.0-fast",
        "sd-2.0-fast-v1",
        "sd-2.5-M-720P-v1",
        "sd-2.5-M-720p",
        "sd2",
        "sd2.0-1080p-4k-pro",
        "sd2.0-15s",
        "sd2.0-480p",
        "sd2.0-fast",
        "sd2fast",
        "sd_2",
        "kling-3.0-omni",
        "veo-3.1",
        "veo-3.1-lite",
        "seedance-2.0",
        "firefly-seedance2-480p",
        "firefly-seedance2-fast-480p",
        "s-videos-f-933-fast-480-2",
        "happyhorse-1.0",
    }
)

# 故意编的：只用来验证「未知型号必须掉进兜底」这一条行为。
DELIBERATE_SYNTHETIC_IDS = frozenset({"totally-unknown-model", "brand-new-gateway-model"})


def test_corpus_ids_have_committed_provenance() -> None:
    """语料里的型号必须来自真实记录，不许是会话里现编的。

    这条守卫是为了防止 2026-10-01 那类循环证据再次发生：编一个型号 → 写进测试 →
    采集脚本读回来 → 当成事实。真实模型名只能来自已提交的源码、文档或测试。
    """

    corpus = set(MODEL_KEYS) | set(AMBIGUOUS_MODEL_IDS)
    unproven = corpus - COMMITTED_PROVENANCE_IDS - DELIBERATE_SYNTHETIC_IDS
    assert unproven == set(), (
        f"这些型号既不在已提交内容里，也不是登记的合成探针：{sorted(unproven)}。"
        "要么删掉，要么先证明它真实存在。"
    )
    assert COMMITTED_PROVENANCE_IDS.isdisjoint(DELIBERATE_SYNTHETIC_IDS)
    assert "sd-2.5-M-1080P-v1" not in corpus, "这条是 2026-10-01 编造出来的，不许回到语料里"
