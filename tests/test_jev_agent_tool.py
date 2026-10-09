"""JEV agent 工具处理器测试：入参归一、答案形状、错误路径。"""

import json

from novelvideo.agent_tools.village_canvas.judgment_impl import _handle_jev_judge
from novelvideo.services import judgment as jev


def _patch_ask(monkeypatch, answers: dict):
    captured: dict = {}

    def fake_ask(state, questions, *, model=None, **kwargs):
        captured["state"] = state
        captured["questions"] = questions
        captured["model"] = model
        return {name: answers[name] for name in questions}

    monkeypatch.setattr(jev, "ask_questions", fake_ask)
    return captured


def test_handler_returns_typed_answers(monkeypatch):
    captured = _patch_ask(
        monkeypatch,
        {
            "need_reverse": jev.YesNoAnswer(probability=0.9),
            "best_ref": jev.ChoiceAnswer(
                choice="背面图", confidence=0.99, probabilities={"背面图": 1.0}
            ),
            "drama": jev.ScoreAnswer(score=3.0, confidence=0.6, legend={"0": "平淡"}),
        },
    )

    raw = _handle_jev_judge(
        {
            "state": ["场景：办公室，背面双开木门", "镜头1：众人回头看向门口"],
            "questions": {
                "need_reverse": {
                    "type": "noul",
                    "instructions": "需要背面图吗？",
                    "criteria": {"true": "有反打", "false": "只拍正面"},
                },
                "best_ref": {
                    "type": "choice",
                    "instructions": "用哪张图？",
                    "criteria": {"正面图": "只拍正面", "背面图": "拍到背面"},
                },
                "drama": {
                    "type": "score",
                    "instructions": "张力几分？",
                    "criteria": ["平淡", "极强"],
                },
            },
        }
    )
    payload = json.loads(raw)

    assert payload["ok"] is True
    assert payload["question_count"] == 3
    assert payload["answers"]["need_reverse"]["verdict"] == "yes"
    assert payload["answers"]["best_ref"]["choice"] == "背面图"
    assert payload["answers"]["drama"]["score"] == 3.0
    assert captured["questions"]["need_reverse"]["type"] == "noul"
    assert captured["questions"]["need_reverse"]["criteria"] == {
        "true": "有反打",
        "false": "只拍正面",
    }


def test_handler_rejects_bad_questions_without_touching_service(monkeypatch):
    def _boom(*args, **kwargs):
        raise AssertionError("不应触达判断服务")

    monkeypatch.setattr(jev, "ask_questions", _boom)

    for bad in (
        {"state": ["材料"], "questions": {"q": {"type": "essay", "instructions": "x"}}},
        {"state": ["材料"], "questions": {"q": {"type": "noul"}}},
        {"state": ["材料"], "questions": {}},
        {"state": [], "questions": {"q": {"type": "noul", "instructions": "x"}}},
    ):
        payload = json.loads(_handle_jev_judge(bad))
        assert payload["ok"] is False, bad
        assert "apikey_" not in json.dumps(payload, ensure_ascii=False)


def test_handler_reports_service_failure_readably(monkeypatch):
    def _fail(*args, **kwargs):
        raise jev.JudgmentError("JEV 判断失败：HTTP 401")

    monkeypatch.setattr(jev, "ask_questions", _fail)

    payload = json.loads(
        _handle_jev_judge(
            {"state": "材料", "questions": {"q": {"type": "noul", "instructions": "要不要？"}}}
        )
    )

    assert payload["ok"] is False
    assert "401" in payload["error"]