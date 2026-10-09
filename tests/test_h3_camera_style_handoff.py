"""Check actual compiler instructions and provider requests, not film quality."""

import json

from pydantic_ai.messages import ModelResponse, SystemPromptPart, TextPart, UserPromptPart
from pydantic_ai.models.function import FunctionModel
import pytest

from novelvideo.freezone.film_prompt_contract import append_script_shot_visual_context
from novelvideo.freezone.video_request_contract import split_workflow_motion_prompt
from novelvideo.generators.video import h3_body_translation as dialect
from tests.test_freezone_native_dialogue_submission import capture_chain as capture_chain
from tests.test_script_camera_style_guidance import _script


REAL_TRANSLATE = dialect.translate_h3_body_to_english
COORDINATED_CAMERA = (
    "起始固定在桌前右侧略俯，先看清右手接近与停住；以右手开始撤回为触发，"
    "沿桌前同一观察侧缓慢向左横移，同时转向桌中央信封并小幅下俯，"
    "让信封与双手的距离逐渐显露，连续移动中保持三者同框；"
    "结束构图保留桌中央信封和桌沿双手，不越过人物与信封的互动轴"
)
ENGLISH_ACTION_STYLE = (
    "Her right hand approaches the seal, pauses just short of contact, then withdraws "
    "to the desk edge. Her left hand holds its position and the red envelope stays "
    "in the center of the desk. Light from the rear-wall window falls diagonally "
    "across the desk throughout. Matte fabric and readable paper fibers retain "
    "their material response; the red paper stands apart from the gray-green desk. "
    "Natural proportions and skin tones, clean highlights and readable shadows remain visible."
)
ENGLISH_CAMERAS = {
    "fixed": "The camera holds slightly above the desk front, keeping both hands, the envelope and desk edge visible throughout, including the ending.",
    "triggered": "The camera initially holds slightly above the desk front on the right. Only as her right hand begins to withdraw, it slowly trucks left along the same observation side, revealing the growing distance between the envelope and both hands. The ending retains the envelope in the desk center and both hands at the edge.",
    "coordinated": "The camera initially holds slightly above the desk front on the right. Only as her right hand begins to withdraw, it slowly trucks left along the same observation side while panning toward the envelope and tilting slightly down in one continuous movement. The increasing distance becomes visible while both hands and the envelope stay in frame. The ending retains the envelope in the desk center and both hands at the edge.",
}


def test_h3_translation_guidance_preserves_authored_camera_and_style():
    contract = dialect.H3_TRANSLATOR_SYSTEM_PROMPT
    assert "One camera move per phase" not in contract
    for rule in (
        "static observation", "coordinated camera movement", "reveal timing",
        "ending composition", "camera-relative route", "material response",
        "world-space light sources", "color relationships",
    ):
        assert rule in contract


@pytest.mark.asyncio
@pytest.mark.parametrize("audio", [False, True], ids=["picture-only", "native-audio"])
@pytest.mark.parametrize("camera", ["fixed", "triggered", "coordinated"])
async def test_authored_camera_style_reaches_real_h3_submission(
    capture_chain, monkeypatch, camera, audio,
):
    row = _script(moving=camera != "fixed").rows[0].model_dump()
    segments = split_workflow_motion_prompt(row["video_motion_prompt"])
    if camera == "coordinated":
        row["video_motion_prompt"] = row["video_motion_prompt"].replace(segments["camera"], COORDINATED_CAMERA)
    source = append_script_shot_visual_context(row["video_motion_prompt"], row)
    english = f"[Shot 1] {ENGLISH_CAMERAS[camera]} {ENGLISH_ACTION_STYLE}"
    requests = []

    def transport(messages, _info):
        requests.append(messages)
        return ModelResponse(parts=[TextPart(json.dumps({
            "description": english, "soundscape": "Sleeve fabric rubs softly over steady ventilation.",
        }))])

    monkeypatch.setattr(dialect, "translate_h3_body_to_english", REAL_TRANSLATE)
    monkeypatch.setattr(dialect, "_CACHE", {})
    monkeypatch.setattr(
        "novelvideo.generators.direct_models.get_direct_pydantic_model",
        lambda *_args, **_kwargs: FunctionModel(transport),
    )
    captured, _strip_audio = await capture_chain(prompt=source, audio=audio, entry="canvas")
    assert len(requests) == 1
    parts = [part for message in requests[0] for part in message.parts]
    system = "\n".join(part.content for part in parts if isinstance(part, SystemPromptPart))
    assert system == dialect.H3_TRANSLATOR_SYSTEM_PROMPT
    assert "One camera move per phase" not in system
    assert "coordinated camera movement" in system and "world-space light sources" in system
    payload = json.loads(next(part.content for part in parts if isinstance(part, UserPromptPart)))
    body = payload["shot_description"]
    for fact in (
        "尚未接触便停住", "随后撤回桌沿", "左手保持原位", "信封始终留在桌面中央",
        "后墙窗光斜照桌面", "哑光织物与清晰纸纤维", "灰绿环境衬托红纸",
        "肤色自然", "高光纯净", "暗部保留层次",
    ):
        assert fact in body
    assert split_workflow_motion_prompt(row["video_motion_prompt"])["camera"] in body
    assert "时长：" not in body and "对话台词" not in body
    assert payload["has_dialogue"] is False
    provider = captured["generate"]["prompt"]
    assert english in provider
    assert captured["autodl"]["prompt"] == provider
    assert captured["minimax_v2_payload"]["content"] == [{"type": "text", "text": provider}]
    assert ("overall_soundscape:" in provider) is audio
    assert captured["generate"]["generate_audio"] is audio
    assert "本镜已定美术" not in provider and "@图片" not in provider
