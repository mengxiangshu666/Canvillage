"""断点自愈：身份图任务在造型缺失时用默认文本模型补全后继续。

覆盖四条合同：
1. 「身份缺少造型描述或服装参考图」触发自动补全，补全结果持久化到身份并进入生成。
2. 造型已存在的身份不做任何补救（不花模型调用）。
3. 补全模型调用失败时，报错明确指向「手动补写造型描述后重试」。
4. 补救刷新期望快照，陈旧快照闸门不得把补救后的任务再次拒掉。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from novelvideo.agents.identity_planner import AppearanceDescription
from novelvideo.task_backend.runners import character_image as runner
from novelvideo.utils.path_resolver import canonical_identity_portrait_path

APPEARANCE_TEXT = "靛蓝粗布对襟短打，配宽腿裤与草鞋，腰间挂烟袋，短发利落。"


def _identity(**overrides: Any) -> SimpleNamespace:
    fields = dict(
        identity_id="id-1",
        identity_name="青年",
        face_prompt="",
        appearance_details="",
        body_type="",
        age_group="youth",
        portrait_image="",
        costume_image="",
    )
    fields.update(overrides)
    return SimpleNamespace(**fields)


def _character(identities: list[SimpleNamespace]) -> SimpleNamespace:
    return SimpleNamespace(
        name="村长",
        face_prompt="方脸浓眉",
        age_group="youth",
        gender="男",
        body_type="",
        identities=identities,
    )


class FakeStore:
    def __init__(self) -> None:
        self.updates: list[tuple[str, str, dict]] = []

    async def update_character_identity(
        self, character_name: str, identity_id: str, **updates: Any
    ) -> None:
        self.updates.append((character_name, identity_id, updates))


def _face_anchor(tmp_path: Path, identity_name: str = "青年") -> Path:
    anchor = canonical_identity_portrait_path(tmp_path, "村长", identity_name)
    anchor.parent.mkdir(parents=True, exist_ok=True)
    anchor.write_bytes(b"PNGDATA")
    return anchor


def _patched_model_calls(monkeypatch: pytest.MonkeyPatch):
    calls: dict[str, Any] = {}

    def install(error: Exception | None = None) -> None:
        async def fake_appearance(**kwargs: Any) -> AppearanceDescription:
            calls["appearance_kwargs"] = kwargs
            if error is not None:
                raise error
            return AppearanceDescription(appearance_details=APPEARANCE_TEXT)

        async def fake_generate(**kwargs: Any) -> dict[str, Any]:
            calls["generate_kwargs"] = kwargs
            Path(str(kwargs["output_path"])).write_bytes(b"PNGDATA")
            return {"success": True}

        monkeypatch.setattr(
            "novelvideo.agents.identity_planner.generate_identity_appearance_details",
            fake_appearance,
        )
        monkeypatch.setattr(
            "novelvideo.generators.generate_identity_image_unified",
            fake_generate,
        )

    return install, calls


async def _run(tmp_path: Path, character: SimpleNamespace, store: FakeStore, **kwargs: Any):
    progress: list[str] = []

    def update(progress_value: float, message: str) -> None:
        progress.append(message)

    audit: dict[str, Any] = {}
    output = await runner._generate_identity_image(
        store=store,
        character=character,
        ethnicity="Chinese",
        identity_id="id-1",
        identity_name="青年",
        output_dir=tmp_path,
        style="",
        model="test-image-model",
        task_type="identity_image",
        scope="",
        update=update,
        audit=audit,
        **kwargs,
    )
    return output, audit, progress


def test_missing_styling_triggers_remediation_then_generates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    install, calls = _patched_model_calls(monkeypatch)
    install()
    _face_anchor(tmp_path)
    store = FakeStore()
    character = _character([_identity()])

    output, audit, progress = asyncio.run(_run(tmp_path, character, store))

    assert Path(str(output)).exists()
    assert any("自动补全" in line for line in progress)
    appearance_updates = [u for _, _, u in store.updates if "appearance_details" in u]
    assert appearance_updates[0]["appearance_details"] == APPEARANCE_TEXT
    assert audit["auto_remediation"]["source"] == "default_text_model"
    assert APPEARANCE_TEXT in str(calls["generate_kwargs"]["identity_prompt"])


def test_existing_styling_skips_remediation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    install, calls = _patched_model_calls(monkeypatch)
    install()
    _face_anchor(tmp_path)
    store = FakeStore()
    character = _character([_identity(appearance_details="藏青长衫马褂")])

    output, audit, _progress = asyncio.run(_run(tmp_path, character, store))

    assert Path(str(output)).exists()
    assert not any("appearance_details" in u for _, _, u in store.updates)
    assert "auto_remediation" not in audit
    assert "appearance_details" not in calls


def test_remediation_failure_reports_a_manual_fix_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    install, _calls = _patched_model_calls(monkeypatch)
    install(error=RuntimeError("model unavailable"))
    _face_anchor(tmp_path)
    store = FakeStore()
    character = _character([_identity()])

    with pytest.raises(RuntimeError, match="造型描述自动补全失败"):
        asyncio.run(_run(tmp_path, character, store))
    assert store.updates == []


def test_remediation_refreshes_the_expected_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    install, _calls = _patched_model_calls(monkeypatch)
    install()
    _face_anchor(tmp_path)
    store = FakeStore()
    character = _character([_identity()])

    output, audit, _progress = asyncio.run(
        _run(
            tmp_path,
            character,
            store,
            expected_reference_snapshot={"revision": "stale-snapshot"},
        )
    )

    assert Path(str(output)).exists()
    assert "auto_remediation" in audit
    appearance_updates = [u for _, _, u in store.updates if "appearance_details" in u]
    assert appearance_updates[0]["appearance_details"] == APPEARANCE_TEXT


SAFETY_400 = (
    'direct image API HTTP 400: {"error":{"message":"您的请求无法用于生成图像。'
    '该请求可能因安全政策被拦截，或不适合进行图像生成。"}}'
)
REWRITTEN = "古风影视造型：青年男性，藏青色长衫与马褂，黑色长发整齐束起，佩玉。"


def test_safety_rejection_rewrites_prompt_then_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _face_anchor(tmp_path)
    store = FakeStore()
    character = _character([_identity(appearance_details="藏青长衫马褂")])
    calls: dict[str, Any] = {}

    async def fake_generate(**kwargs: Any) -> dict[str, Any]:
        attempts = calls.setdefault("generate_calls", [])
        attempts.append(kwargs)
        if len(attempts) == 1:
            raise RuntimeError(SAFETY_400)
        Path(str(kwargs["output_path"])).write_bytes(b"PNGDATA")
        return {"success": True}

    async def fake_rewrite(prompt_text: str, **_: Any) -> str:
        calls["rewrite_prompt"] = prompt_text
        return REWRITTEN

    monkeypatch.setattr(
        "novelvideo.generators.generate_identity_image_unified", fake_generate
    )
    monkeypatch.setattr(
        "novelvideo.agents.identity_planner.rewrite_image_prompt_for_policy",
        fake_rewrite,
    )

    output, audit, progress = asyncio.run(_run(tmp_path, character, store))

    assert Path(str(output)).exists()
    attempts = calls["generate_calls"]
    assert len(attempts) == 2
    assert calls["rewrite_prompt"] == attempts[0]["identity_prompt"]
    assert attempts[1]["identity_prompt"] == REWRITTEN
    assert audit["prompt_safety_rewrite"]["trigger"] == "image_api_safety_400"
    assert any("自动改写" in line for line in progress)


def test_non_safety_error_propagates_without_rewrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _face_anchor(tmp_path)
    store = FakeStore()
    character = _character([_identity(appearance_details="藏青长衫马褂")])
    calls: dict[str, Any] = {}

    async def fake_generate(**kwargs: Any) -> dict[str, Any]:
        calls.setdefault("generate_calls", []).append(kwargs)
        raise RuntimeError("direct image API HTTP 504: 图片生成超时")

    async def fake_rewrite(prompt_text: str, **_: Any) -> str:
        calls["rewrite_prompt"] = prompt_text
        return REWRITTEN

    monkeypatch.setattr(
        "novelvideo.generators.generate_identity_image_unified", fake_generate
    )
    monkeypatch.setattr(
        "novelvideo.agents.identity_planner.rewrite_image_prompt_for_policy",
        fake_rewrite,
    )

    with pytest.raises(RuntimeError, match="504"):
        asyncio.run(_run(tmp_path, character, store))

    assert len(calls["generate_calls"]) == 1
    assert "rewrite_prompt" not in calls


def test_rewrite_failure_reports_manual_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _face_anchor(tmp_path)
    store = FakeStore()
    character = _character([_identity(appearance_details="藏青长衫马褂")])

    async def fake_generate(**kwargs: Any) -> dict[str, Any]:
        raise RuntimeError(SAFETY_400)

    async def fake_rewrite(prompt_text: str, **_: Any) -> str:
        raise RuntimeError("model down")

    monkeypatch.setattr(
        "novelvideo.generators.generate_identity_image_unified", fake_generate
    )
    monkeypatch.setattr(
        "novelvideo.agents.identity_planner.rewrite_image_prompt_for_policy",
        fake_rewrite,
    )

    with pytest.raises(RuntimeError, match="自动改写失败"):
        asyncio.run(_run(tmp_path, character, store))


def test_remediate_identity_styling_skips_when_binding_resolves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from novelvideo.agents.identity_planner import remediate_identity_styling

    _face_anchor(tmp_path)
    store = FakeStore()
    identity = _identity(appearance_details="藏青长衫马褂")
    character = _character([identity])
    called = {"n": 0}

    async def fake_appearance(**_: Any) -> AppearanceDescription:
        called["n"] += 1
        return AppearanceDescription(appearance_details=APPEARANCE_TEXT)

    monkeypatch.setattr(
        "novelvideo.agents.identity_planner.generate_identity_appearance_details",
        fake_appearance,
    )

    result = asyncio.run(
        remediate_identity_styling(
            store=store,
            character=character,
            identity=identity,
            project_dir=tmp_path,
            reason="测试",
        )
    )

    assert result["remediated"] is False
    assert called["n"] == 0
    assert store.updates == []


def test_remediate_identity_styling_fills_and_persists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from novelvideo.agents.identity_planner import remediate_identity_styling

    _face_anchor(tmp_path)
    store = FakeStore()
    identity = _identity()
    character = _character([identity])

    async def fake_appearance(**_: Any) -> AppearanceDescription:
        return AppearanceDescription(appearance_details=APPEARANCE_TEXT)

    monkeypatch.setattr(
        "novelvideo.agents.identity_planner.generate_identity_appearance_details",
        fake_appearance,
    )

    result = asyncio.run(
        remediate_identity_styling(
            store=store,
            character=character,
            identity=identity,
            project_dir=tmp_path,
            reason="测试",
        )
    )

    assert result["remediated"] is True
    assert identity.appearance_details == APPEARANCE_TEXT
    (_, _, updates), = [u for u in store.updates if "appearance_details" in u[2]]

    from novelvideo.utils.identity_binding import resolve_identity_generation_binding

    binding = resolve_identity_generation_binding(
        project_dir=tmp_path, character=character, identity=identity
    )
    assert APPEARANCE_TEXT in binding.identity_prompt
    assert updates["appearance_details"] == APPEARANCE_TEXT


def test_remediate_identity_styling_reraises_other_gates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from novelvideo.agents.identity_planner import remediate_identity_styling

    store = FakeStore()
    identity = _identity()
    character = _character([identity])  # 无脸锚文件 → FileNotFoundError 闸门

    async def fake_appearance(**_: Any) -> AppearanceDescription:
        raise AssertionError("不应调用模型")

    monkeypatch.setattr(
        "novelvideo.agents.identity_planner.generate_identity_appearance_details",
        fake_appearance,
    )

    with pytest.raises(FileNotFoundError):
        asyncio.run(
            remediate_identity_styling(
                store=store,
                character=character,
                identity=identity,
                project_dir=tmp_path,
                reason="测试",
            )
        )


def test_remediate_identity_styling_wraps_model_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from novelvideo.agents.identity_planner import remediate_identity_styling

    _face_anchor(tmp_path)
    store = FakeStore()
    identity = _identity()
    character = _character([identity])

    async def fake_appearance(**_: Any) -> AppearanceDescription:
        raise RuntimeError("model down")

    monkeypatch.setattr(
        "novelvideo.agents.identity_planner.generate_identity_appearance_details",
        fake_appearance,
    )

    with pytest.raises(ValueError, match="造型描述自动补全失败"):
        asyncio.run(
            remediate_identity_styling(
                store=store,
                character=character,
                identity=identity,
                project_dir=tmp_path,
                reason="测试",
            )
        )
    assert store.updates == []
