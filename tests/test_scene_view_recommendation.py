"""画布侧「镜头该用正面还是背面图」推荐：只读、不阻断、jev 缺席可退。"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from novelvideo.api.routes.scenes import _recommend_scene_view_for_beat
from novelvideo.services import judgment as jev


def _scene(tmp_path: Path, name: str = "办公室", with_reverse: bool = True):
    from novelvideo.utils.path_resolver import canonical_scene_reverse_master_path

    scene = SimpleNamespace(name=name, environment_prompt="正面：长桌书架\n背面：双开木门")
    if with_reverse:
        path = canonical_scene_reverse_master_path(tmp_path, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"png")
    return scene


def _beat(text: str = "小臣推门进来，众人回头看向门口。"):
    return {"beat_number": 1, "visual_description": text}


@pytest.mark.asyncio
async def test_no_reverse_image_recommends_front_without_calling_jev(tmp_path, monkeypatch):
    def _boom(*args, **kwargs):
        raise AssertionError("没有背面图时不该调用判断服务")

    monkeypatch.setattr(jev, "judge_choice", _boom)

    data = await _recommend_scene_view_for_beat(
        tmp_path, None, _scene(tmp_path, with_reverse=False), _beat()
    )

    assert data["ok"] is True
    assert data["view"] == "正面图"
    assert data["has_reverse"] is False


@pytest.mark.asyncio
async def test_jev_pick_is_passed_through_with_confidence(tmp_path, monkeypatch):
    captured: dict = {}

    def fake_judge(state, question, options, **kwargs):
        captured["state"] = state
        captured["options"] = options
        return jev.ChoiceAnswer(
            choice="背面图", confidence=0.93, probabilities={"背面图": 0.93, "正面图": 0.07}
        )

    monkeypatch.setattr(jev, "judge_choice", fake_judge)

    data = await _recommend_scene_view_for_beat(tmp_path, None, _scene(tmp_path), _beat())

    assert data["ok"] is True
    assert data["view"] == "背面图"
    assert data["confidence"] == 0.93
    # 材料里带上了场景四向合同与镜头描述，选项说明写清了正/背面含义
    assert any("双开木门" in item for item in captured["state"])
    assert any("回头看" in item for item in captured["state"])
    assert set(captured["options"]) == {"正面图", "背面图"}


@pytest.mark.asyncio
async def test_jev_failure_returns_ok_false_and_front_fallback(tmp_path, monkeypatch):
    def _fail(*args, **kwargs):
        raise jev.JudgmentError("JEV 判断失败：HTTP 401")

    monkeypatch.setattr(jev, "judge_choice", _fail)

    data = await _recommend_scene_view_for_beat(tmp_path, None, _scene(tmp_path), _beat())

    assert data["ok"] is False
    assert data["view"] == ""
    assert "401" in data["reason"]


@pytest.mark.asyncio
async def test_beat_without_visual_text_is_not_guessed(tmp_path, monkeypatch):
    def _boom(*args, **kwargs):
        raise AssertionError("没有描述就不该猜")

    monkeypatch.setattr(jev, "judge_choice", _boom)

    data = await _recommend_scene_view_for_beat(tmp_path, None, _scene(tmp_path), _beat(""))

    assert data["ok"] is False
    assert "没有视觉描述" in data["reason"]
