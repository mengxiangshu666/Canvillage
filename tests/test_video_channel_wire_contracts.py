"""出线合同跟着渠道条目走：随删随走、随填随有、两处对不上就报出来。

用户口径（2026-10-01）：「只要我们删掉，它就是默认没有了；如果我们把它接过来，就是有，
随删随走，随填随有。」这份测试把这句话拆成可复现的硬断言。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from novelvideo import config
from novelvideo.generators.video.channel_wire_contract import (
    WIRE_EVIDENCE_PROBE,
    WIRE_EVIDENCE_OPERATOR,
    WIRE_EVIDENCE_SUBMIT,
    WIRE_SOURCE_CHANNEL,
    WIRE_SOURCE_DEFAULT,
    WIRE_SOURCE_SEED,
    WireContractError,
    normalize_wire_contract_record,
    record_from_profile,
)
from novelvideo.generators.video.contract_packages import (
    AUTHORITY_SEED,
    ContractPackageError,
    load_contract_packages,
)
from novelvideo.generators.video.direct_models import (
    direct_video_model_option,
    resolve_direct_video_model,
)
from novelvideo.generators.video.direct_video_capability_cache import record_capability
from novelvideo.generators.video.upstream_profiles import (
    DEFAULT_PROFILE,
    VideoUpstreamProfile,
    channel_contract_from_probe,
    resolve_wire_contract,
)
from novelvideo.model_gateway_settings import (
    get_direct_video_models,
    persist_probed_wire_contract,
    save_direct_video_models,
)

_CONTRACT_DIR = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "novelvideo"
    / "generators"
    / "video"
    / "contracts"
)

#: 一条「渠道自己的合同」：用 seconds 而不是 duration，故意与同名种子（seedance2_native
#: 用 duration_seconds）不同，用来证明渠道那份说了算。
_CUSTOM_FIELDS = {
    "profile_id": "relay-openai-ish",
    "duration_field": "seconds",
    "duration_choices": [4, 8],
    "ratio_field": "aspect_ratio",
    "first_frame_field": "input_image",
    "audio_field": None,
    "auto_face_field": None,
    "ref_field": None,
    "create_path": "/v1/videos",
    "query_path": "/v1/videos/{task_id}",
}


def _isolate(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))


def _channel(
    *,
    record_id: str = "video-guard-1",
    model_id: str = "seedance-2.5",
    wire_contract: dict | None = None,
    protocol: str = "openai-video",
) -> dict:
    item = {
        "id": record_id,
        "label": model_id,
        "modelId": model_id,
        "baseUrl": "http://127.0.0.1:9800/v1",
        "apiKey": "channel-test-key",
        "requestedProtocol": protocol,
        "protocol": protocol,
        "enabled": True,
        "isDefault": True,
    }
    if wire_contract is not None:
        item["wireContract"] = wire_contract
    return item


def _profile(fields: dict) -> VideoUpstreamProfile:
    return VideoUpstreamProfile(**fields)


# --- 1. 三级顺序：渠道合同 → 种子 → 通用兜底 ---------------------------------


def test_channel_contract_wins_over_the_name_seed() -> None:
    """渠道自带合同在，种子就不参与 —— 哪怕模型名字正好命中某个种子。"""

    seed_only = resolve_wire_contract("seedance-2.5")
    assert seed_only.source == WIRE_SOURCE_SEED
    assert seed_only.profile_id == "seedance2_native"

    stored = record_from_profile(_profile(_CUSTOM_FIELDS), evidence=WIRE_EVIDENCE_SUBMIT)
    resolved = resolve_wire_contract("seedance-2.5", stored)

    assert resolved.source == WIRE_SOURCE_CHANNEL
    assert resolved.profile.duration_field == "seconds"
    assert resolved.profile.duration_choices == (4, 8)
    # 两处对不上要看得见，而不是被静默吞掉。
    assert "duration_field" in resolved.conflicts_with_seed
    assert "first_frame_field" in resolved.conflicts_with_seed
    assert resolved.seed_profile_id == "seedance2_native"


def test_unknown_model_without_channel_contract_falls_back_to_default() -> None:
    resolved = resolve_wire_contract("brand-new-gateway-model")
    assert resolved.source == WIRE_SOURCE_DEFAULT
    assert resolved.profile is DEFAULT_PROFILE
    assert resolved.verified is False


def test_probe_evidence_is_not_called_verified() -> None:
    """只读一次 /models 不算验证过 —— 只能真，不能骗人。"""

    probe = record_from_profile(DEFAULT_PROFILE, evidence=WIRE_EVIDENCE_PROBE)
    assert resolve_wire_contract("x", probe).verified is False

    submitted = record_from_profile(DEFAULT_PROFILE, evidence=WIRE_EVIDENCE_SUBMIT)
    assert resolve_wire_contract("x", submitted).verified is True


# --- 2. 随删随走、随填随有 -----------------------------------------------------


def test_deleting_a_channel_deletes_its_contract(monkeypatch, tmp_path: Path) -> None:
    _isolate(monkeypatch, tmp_path)
    contract = record_from_profile(_profile(_CUSTOM_FIELDS), evidence=WIRE_EVIDENCE_SUBMIT)
    saved = save_direct_video_models([_channel(wire_contract=contract)])
    assert saved[0]["wireContract"]["fields"]["duration_field"] == "seconds"

    # 删掉渠道 = 提交一份不含它的清单。
    save_direct_video_models([], confirm_clear=True)
    assert get_direct_video_models() == []

    # 合同跟着渠道一起没了：现在解析回到「按名字猜的种子」，而不是那条渠道的合同。
    resolved = resolve_wire_contract("seedance-2.5")
    assert resolved.source == WIRE_SOURCE_SEED
    assert resolved.profile.duration_field == "duration_seconds"


def test_adding_the_channel_back_brings_the_contract_back(
    monkeypatch, tmp_path: Path
) -> None:
    _isolate(monkeypatch, tmp_path)
    contract = record_from_profile(_profile(_CUSTOM_FIELDS), evidence=WIRE_EVIDENCE_SUBMIT)
    save_direct_video_models([_channel(wire_contract=contract)])
    save_direct_video_models([], confirm_clear=True)
    save_direct_video_models([_channel(wire_contract=contract)])

    stored = get_direct_video_models()[0].get("wireContract")
    assert stored["fields"]["duration_field"] == "seconds"
    resolved = resolve_wire_contract("seedance-2.5", stored)
    assert resolved.source == WIRE_SOURCE_CHANNEL


def test_unprobed_channel_gets_no_fabricated_contract(monkeypatch, tmp_path: Path) -> None:
    """没探测过的渠道不自动补合同；否则「协议字段的默认值」会盖掉按名字命中的档位。"""

    _isolate(monkeypatch, tmp_path)
    saved = save_direct_video_models([_channel(model_id="sd-2.0-fast-v1")])
    assert saved[0].get("wireContract") is None

    model = resolve_direct_video_model(f"direct_{saved[0]['id']}")
    option = direct_video_model_option(model)
    assert option["wireContractSource"] == WIRE_SOURCE_SEED
    assert option["wireContractProfileId"] == "huabu"
    assert option["wireContractVerified"] is False
    assert option["durationOptions"] == [5, 10, 15]


def test_probed_channel_does_not_get_an_invented_contract(
    monkeypatch, tmp_path: Path
) -> None:
    """探测过 ≠ 有合同。目录探测推不出字段名，就不许凭空写一份。"""

    _isolate(monkeypatch, tmp_path)
    record_capability(
        base_url="http://127.0.0.1:9800/v1",
        protocol="openai-video",
        upstream_model="MiniMax-H3",
        capability={"verificationStatus": "catalog-confirmed", "modelFound": True},
    )
    saved = save_direct_video_models([_channel(model_id="MiniMax-H3")])
    assert saved[0].get("wireContract") is None

    model = resolve_direct_video_model(f"direct_{saved[0]['id']}")
    option = direct_video_model_option(model)
    # MiniMax-H3 没有同名种子，所以三级解析落在通用兜底。
    assert option["wireContractSource"] == WIRE_SOURCE_DEFAULT
    assert option["wireContractVerified"] is False


def test_probe_write_back_does_not_downgrade_submit_evidence(
    monkeypatch, tmp_path: Path
) -> None:
    _isolate(monkeypatch, tmp_path)
    submitted = record_from_profile(DEFAULT_PROFILE, evidence=WIRE_EVIDENCE_SUBMIT)
    saved = save_direct_video_models([_channel(wire_contract=submitted)])
    record_id = saved[0]["id"]

    persist_probed_wire_contract(record_id, protocol="openai-video")

    kept = get_direct_video_models()[0].get("wireContract")
    assert kept["evidence"] == WIRE_EVIDENCE_SUBMIT


def test_probe_write_back_targets_only_the_named_channel(
    monkeypatch, tmp_path: Path
) -> None:
    """推导出**更具体**的合同时，写回也只落在被点名的那条渠道上。"""

    _isolate(monkeypatch, tmp_path)
    saved = save_direct_video_models(
        [
            _channel(record_id="video-a", model_id="model-a"),
            _channel(record_id="video-b", model_id="model-b"),
        ]
    )
    specific = record_from_profile(DEFAULT_PROFILE)
    specific["fields"]["duration_field"] = "duration_seconds"
    specific["fields"]["duration_choices"] = [4, 8]
    monkeypatch.setattr(
        "novelvideo.generators.video.upstream_profiles.channel_contract_from_probe",
        lambda **_kwargs: dict(specific),
    )
    persist_probed_wire_contract("video-a", protocol="openai-video")
    by_id = {item["id"]: item for item in get_direct_video_models()}
    assert by_id["video-a"]["wireContract"] is not None
    assert by_id["video-a"]["wireContract"]["fields"]["duration_field"] == "duration_seconds"
    assert by_id["video-b"].get("wireContract") is None
    assert len(saved) == 2


# --- 3. 写错要报出来，不许静默 ------------------------------------------------


def test_channel_contract_with_a_typo_is_rejected_loudly(
    monkeypatch, tmp_path: Path
) -> None:
    _isolate(monkeypatch, tmp_path)
    broken = {
        "profileId": "typo-profile",
        "source": WIRE_SOURCE_CHANNEL,
        "evidence": WIRE_EVIDENCE_OPERATOR,
        "fields": {"profile_id": "typo-profile", "duraton_choices": [5]},
    }
    with pytest.raises(WireContractError) as excinfo:
        save_direct_video_models([_channel(wire_contract=broken)])
    assert "未知字段" in str(excinfo.value)


def test_stored_contract_cannot_claim_seed_or_default_source() -> None:
    for source in (WIRE_SOURCE_SEED, WIRE_SOURCE_DEFAULT):
        with pytest.raises(WireContractError) as excinfo:
            normalize_wire_contract_record(
                {
                    "profileId": "x",
                    "source": source,
                    "evidence": WIRE_EVIDENCE_OPERATOR,
                    "fields": {"profile_id": "x"},
                }
            )
        assert "source" in str(excinfo.value)


def test_channel_contract_cannot_smuggle_match_rules() -> None:
    with pytest.raises(WireContractError) as excinfo:
        normalize_wire_contract_record(
            {
                "profileId": "x",
                "source": WIRE_SOURCE_CHANNEL,
                "evidence": WIRE_EVIDENCE_OPERATOR,
                "fields": {"profile_id": "x", "match_prefixes": ["sd-2"]},
            }
        )
    assert "命中规则" in str(excinfo.value)


def test_unreadable_stored_contract_is_surfaced_not_silently_dropped(
    monkeypatch, tmp_path: Path
) -> None:
    _isolate(monkeypatch, tmp_path)
    saved = save_direct_video_models([_channel(model_id="sd-2.0-fast-v1")])
    record_id = saved[0]["id"]

    # 模拟设置库里被写坏的一条合同（没人校验过就落盘的那种）。
    from novelvideo.model_gateway_settings import _settings_db_path

    import sqlite3

    path = _settings_db_path()
    connection = sqlite3.connect(str(path))
    connection.execute(
        "update runtime_settings set value=? where key='direct_video_models'",
        (
            json.dumps(
                [
                    {
                        **saved[0],
                        "wireContract": {
                            "profileId": "broken",
                            "source": "channel",
                            "evidence": "operator",
                            "fields": {"profile_id": "broken", "duraton_choices": [5]},
                        },
                    }
                ],
                ensure_ascii=False,
            ),
        ),
    )
    connection.commit()
    connection.close()

    item = get_direct_video_models()[0]
    assert item.get("wireContract") is None
    assert item["wireContractError"], "读不动的合同必须留下原因，不能当成没有"
    assert record_id == item["id"]


# --- 4. 相似名字不能互相抢占 --------------------------------------------------


def test_channel_contract_beats_a_more_specific_seed_name() -> None:
    """渠道合同与「谁的名字更长」无关：`sd-2.0-fast-v1` 走渠道那份。"""

    stored = record_from_profile(_profile(_CUSTOM_FIELDS), evidence=WIRE_EVIDENCE_SUBMIT)
    for model_key in ("sd-2.0-fast-v1", "sd-2.5-M-720p", "seedance-2.5"):
        resolved = resolve_wire_contract(model_key, stored)
        assert resolved.source == WIRE_SOURCE_CHANNEL
        assert resolved.profile.profile_id == "relay-openai-ish"


# --- 5. 源码里的包只是种子 ----------------------------------------------------


def test_every_shipped_package_declares_itself_a_seed() -> None:
    packages = load_contract_packages(_CONTRACT_DIR)
    assert len(packages) == 8
    for package in packages:
        assert package.authority == AUTHORITY_SEED, package.package_id
        raw = json.loads(package.path.read_text(encoding="utf-8"))
        assert raw["authority"] == AUTHORITY_SEED


def test_a_package_without_the_seed_marker_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "fake.json"
    path.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "packageId": "fake",
                "profiles": [{"profile_id": "fake", "match_prefixes": ["fake-"]}],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ContractPackageError) as excinfo:
        load_contract_packages(tmp_path)
    assert "authority" in str(excinfo.value)


def test_probe_derivation_is_honest_about_what_it_cannot_infer() -> None:
    """推不出合同就不编。非 OpenAI 兼容一律 None；OpenAI 兼容也只是通用默认，同样不编。"""

    assert channel_contract_from_probe(protocol="minimax-video-v2") is None
    assert channel_contract_from_probe(protocol="autodl-comfyui") is None
    assert channel_contract_from_probe(protocol="totally-unknown") is None
    # 目录探测看不到任何字段名，能给的只有通用默认档案；那就等于没有新信息，不写。
    assert channel_contract_from_probe(protocol="openai-video") is None
    assert channel_contract_from_probe(protocol="openai-video", runtime_verified=True) is None


def test_auto_derived_contract_only_counts_when_more_specific() -> None:
    """「推导出来了」的标准是比通用兜底更具体，不是「函数有返回值」。"""

    from novelvideo.generators.video.channel_wire_contract import record_from_profile
    from novelvideo.generators.video.upstream_profiles import (
        DEFAULT_PROFILE,
        derived_contract_adds_information,
    )

    assert derived_contract_adds_information(None) is False
    assert derived_contract_adds_information({"fields": {}}) is False
    assert derived_contract_adds_information(record_from_profile(DEFAULT_PROFILE)) is False

    # 真·更具体：换掉一个字段，就算有新信息。
    specific = record_from_profile(DEFAULT_PROFILE)
    specific["fields"]["duration_field"] = "duration_seconds"
    assert derived_contract_adds_information(specific) is True


def test_plain_save_of_an_untouched_probed_channel_does_not_invent_a_contract(
    monkeypatch, tmp_path: Path
) -> None:
    """2026-10-02 真机 8784 抓到：原样保存一次，渠道就被写进一份「猜的合同」。

    现场：`seedance-2.5` 渠道本来按名字命中 `seedance2_native` 种子（字段是
    `duration_seconds` / `/v1/video/generations`）。用户什么都没改，只点了一次保存，
    设置库里就多出一份 `openai_video_default` 的渠道合同 —— 机器猜的东西顶掉了更具体
    的种子，正是这一刀要消掉的「第二份真相」。
    """

    _isolate(monkeypatch, tmp_path)
    saved = save_direct_video_models([_channel(model_id="seedance-2.5")])
    record_id = saved[0]["id"]

    from novelvideo.generators.video.direct_video_capability_cache import (
        record_capability,
    )

    # 让这条渠道「被探测过」：目录探测命中，但没有任何提交证据。
    record_capability(
        base_url="http://127.0.0.1:9800/v1",
        protocol="openai-video",
        upstream_model="seedance-2.5",
        capability={
            "verificationStatus": "directory-only",
            "modelFound": True,
            "discoveredModelCount": 1,
            "detectedProtocol": "openai-video",
        },
    )

    # 用户原样把列表交回来（什么都没改）。
    save_direct_video_models(
        [
            {
                "id": record_id,
                "label": "本地极速视频",
                "modelId": "seedance-2.5",
                "baseUrl": "http://127.0.0.1:9800/v1",
                "apiKey": "",
                "protocol": "openai-video",
                "enabled": True,
                "isDefault": True,
            }
        ]
    )

    item = get_direct_video_models()[0]
    assert item.get("wireContract") is None, "原样保存不许凭空写出一份渠道合同"
    resolved = resolve_wire_contract("seedance-2.5", item.get("wireContract"))
    assert resolved.source == "seed", "按名字命中的那份种子必须还在"
    assert resolved.profile_id == "seedance2_native"


def test_probe_write_back_leaves_a_channel_alone_when_nothing_is_derived(
    monkeypatch, tmp_path: Path
) -> None:
    """点一次「检测连接」也不许凭空给渠道写合同。"""

    _isolate(monkeypatch, tmp_path)
    saved = save_direct_video_models([_channel(model_id="seedance-2.5")])
    record_id = saved[0]["id"]

    from novelvideo.model_gateway_settings import persist_probed_wire_contract

    assert persist_probed_wire_contract(record_id, protocol="openai-video") is None
    assert get_direct_video_models()[0].get("wireContract") is None


def test_saving_an_unchanged_list_rewrites_the_stored_row_byte_for_byte(
    monkeypatch, tmp_path: Path
) -> None:
    """原样保存＝设置库那一行一个字节都不许变。

    2026-10-02 真机 8784 验收就是靠这条把缺陷抓出来的：当时原样保存会写成一份
    「猜的合同」。断言必须落在**库里的原文**上，不能只看接口视图 —— 视图会把
    ``null`` 和「没有这个键」显示成一样。
    """

    import json
    import sqlite3

    from novelvideo.model_gateway_settings import (
        _settings_db_path,
        build_direct_video_models_status,
    )

    _isolate(monkeypatch, tmp_path)
    save_direct_video_models(
        [
            _channel(record_id="video-keep", model_id="seedance-2.5"),
            _channel(record_id="video-other", model_id="MiniMax-H3"),
        ]
    )

    def raw_row() -> str:
        connection = sqlite3.connect(str(_settings_db_path()))
        try:
            return str(
                connection.execute(
                    "select value from runtime_settings where key='direct_video_models'"
                ).fetchone()[0]
            )
        finally:
            connection.close()

    before = raw_row()
    status = build_direct_video_models_status()
    save_direct_video_models(
        [
            {
                "id": item["id"],
                "label": item["label"],
                "modelId": item["modelId"],
                "baseUrl": item["baseUrl"],
                "apiKey": "",
                "protocol": item.get("requestedProtocol") or item.get("protocol") or "auto",
                "enabled": bool(item.get("enabled")),
                "isDefault": bool(item.get("isDefault")),
            }
            for item in status
        ]
    )
    after = raw_row()

    assert json.loads(after) == json.loads(before)
    assert after == before, "原样保存必须逐字节一致，不能凭空补 wireContract 键"


def test_declared_aspect_ratio_field_is_not_silently_dropped() -> None:
    """数据包声明了 `ratio_field: "aspect_ratio"`，报文里就必须真的有这个字段。

    2026-10-01 发现：`convert_ratio` 只认 size / ratio / resolution，
    `aspect_ratio` 被静默丢掉 —— 元数据写着 9:16，报文里什么都没有。
    """

    from novelvideo.generators.video.upstream_profiles import convert_ratio

    assert convert_ratio("9:16", "aspect_ratio") == "9:16"
    assert convert_ratio("16:9", "size") == "16:9"
    assert convert_ratio("720p", "aspect_ratio") is None  # 非比例字面量只给 resolution 用
