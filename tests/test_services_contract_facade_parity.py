"""`novelvideo.services.video_request_contract` 门面不得静默吞掉参数。

2026-09-16 真机事故：`workflow_runtime/media_dispatch.py` 调用这个门面而不是实现模块，
我在 `validate_video_request_contract` 上新增了 `spoken_dialogue` 时只改了实现，门面保持
旧签名把它吃掉，工作流立刻 `TypeError: got an unexpected keyword argument`。

这一层是手写的转发函数（每加一个参数就要在门面里再抄一遍），所以「抄漏」是结构性风险，
不是一次性失误。本文件用**签名比对**把它钉死：门面每个函数的参数名与默认值必须与实现
一致（`Any` 收口的 `**kwargs` 转发天然一致，直接跳过）。
"""

from __future__ import annotations

import inspect

import pytest

from novelvideo.freezone import video_request_contract as implementation
from novelvideo.services import video_request_contract as facade
from novelvideo.services import _video_request_contract as shared_implementation


def test_legacy_contract_module_alias_preserves_patch_identity(monkeypatch):
    assert implementation is shared_implementation
    assert facade.VideoRequestContractError is implementation.VideoRequestContractError
    monkeypatch.setattr(implementation, "append_video_no_text_tail", lambda prompt: "patched")
    assert facade.append_video_no_text_tail("scene") == "patched"

#: 门面模块的公开转发函数。核心签名用 `Any` 收口（`**kwargs`）的那些天然对齐，不列。
FORWARDED_FUNCTIONS = (
    "append_video_no_text_tail",
    "build_minimax_h3_provider_prompt",
    "compile_h3_picture_prompt",
    "compile_h3_provider_prompt",
    "explicit_audio_type_requests_silence",
    "extract_spoken_dialogue",
    "is_minimax_h3_model_identifier",
    "normalize_video_prompt_for_submission",
    "normalize_video_prompt_for_submission_result",
    "normalize_video_resolution_value",
    "required_native_audio_without_dialogue",
    "sanitize_h3_visual_prompt",
    "strip_dialogue_text_from_visual_prompt",
    "validate_structured_video_capability",
    "validate_video_request_contract",
)


def _parameters(func: object) -> list[tuple[str, object, object]]:
    signature = inspect.signature(func)  # type: ignore[arg-type]
    return [
        (name, parameter.kind, parameter.default)
        for name, parameter in signature.parameters.items()
    ]


@pytest.mark.parametrize("name", FORWARDED_FUNCTIONS)
def test_facade_wrapper_mirrors_the_implementation_signature(name: str) -> None:
    facade_function = getattr(facade, name)
    real = getattr(implementation, name)

    facade_params = _parameters(facade_function)
    real_params = _parameters(real)

    assert [item[0] for item in facade_params] == [item[0] for item in real_params], (
        f"{name} 的参数名与实现不一致——门面会把新增参数静默吃掉。"
        f"门面={[i[0] for i in facade_params]} 实现={[i[0] for i in real_params]}"
    )
    assert facade_params == real_params, f"{name} 的参数种类或默认值与实现不一致"


def test_facade_forwards_spoken_dialogue_without_restoring_any_prose_gate() -> None:
    """门面仍接受 `spoken_dialogue`，但不把台词字数或文案秒数变成提交拦截。"""

    issues = facade.validate_video_request_contract(
        prompt="镜头持续 5 秒，男人站在雨里。",
        duration_seconds=3,
        spoken_dialogue=["这是一段很长的独白" * 5],
    )

    assert issues == ()
