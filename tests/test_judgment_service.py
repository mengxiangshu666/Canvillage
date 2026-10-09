"""JEV 判断层测试：请求形状、三种题型解析、临时故障重试、缺密钥报错。"""

import json
from urllib.error import HTTPError

import pytest

from novelvideo.services import judgment


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _answers_payload(answers: dict) -> _FakeResponse:
    return _FakeResponse({"model": "jev-latest", "answers": answers})


def test_missing_key_fails_without_leaking_paths():
    import os

    original = os.environ.pop("JEV_API_KEY", None)
    try:
        with pytest.raises(judgment.JudgmentError) as exc_info:
            judgment.ask_questions(
                "材料",
                {"q": judgment.yes_no("是不是？")},
                keys_file="不存在的密钥文件.txt",
            )
        assert "密钥" in str(exc_info.value)
        assert "apikey_" not in str(exc_info.value)
    finally:
        if original is not None:
            os.environ["JEV_API_KEY"] = original


def test_request_carries_model_state_and_questions():
    captured: dict = {}

    def opener(request, timeout):
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return _answers_payload({"q": {"type": "noul", "noul": 0.9}})

    probability = judgment.judge_yes_no(
        "一段材料",
        "该场景需要背面图。",
        true_means="有反打镜头",
        false_means="只拍正面",
        opener=opener,
    )

    assert probability == 0.9
    assert captured["body"]["model"] == "jev-latest"
    assert captured["body"]["state"] == "一段材料"
    question = captured["body"]["questions"]["q"]
    assert question["type"] == "noul"
    assert question["criteria"] == {"true": "有反打镜头", "false": "只拍正面"}


def test_three_answer_types_parse_into_typed_objects():
    payload = _answers_payload(
        {
            "a": {"type": "noul", "noul": 0.76},
            "b": {
                "type": "choice",
                "choice": "背面图",
                "confidence": 0.99,
                "probabilities": {"正面图": 0.0, "背面图": 1.0},
            },
            "c": {
                "type": "score",
                "score": 2.98,
                "confidence": 0.58,
                "legend": {"0": "完全平淡", "9": "极强"},
            },
        }
    )

    answers = judgment.ask_questions(
        "材料",
        {
            "a": judgment.yes_no("要不要？"),
            "b": judgment.choice("用哪张图？", {"正面图": "只拍正面", "背面图": "拍到背面"}),
            "c": judgment.score("张力几分？", ["完全平淡", "极强"]),
        },
        opener=lambda request, timeout: payload,
    )

    assert isinstance(answers["a"], judgment.YesNoAnswer) and answers["a"].probability == 0.76
    assert isinstance(answers["b"], judgment.ChoiceAnswer)
    assert answers["b"].choice == "背面图" and answers["b"].probabilities["背面图"] == 1.0
    assert isinstance(answers["c"], judgment.ScoreAnswer) and answers["c"].score == 2.98


def test_transient_http_errors_retry_then_succeed():
    calls: list[int] = []
    sleeps: list[float] = []

    def opener(request, timeout):
        calls.append(1)
        if len(calls) < 3:
            raise HTTPError(request.full_url, 503, "unavailable", {}, None)
        return _answers_payload({"q": {"type": "noul", "noul": 0.1}})

    probability = judgment.judge_yes_no(
        "材料", "要不要？", opener=opener, sleep=sleeps.append
    )

    assert probability == 0.1
    assert len(calls) == 3
    assert sleeps == [1.0, 2.0]


def test_deterministic_http_error_fails_fast():
    attempts: list[int] = []

    def opener(request, timeout):
        attempts.append(1)
        raise HTTPError(request.full_url, 401, "unauthorized", {}, None)

    with pytest.raises(judgment.JudgmentError) as exc_info:
        judgment.judge_yes_no("材料", "要不要？", opener=opener)

    assert len(attempts) == 1
    assert "401" in str(exc_info.value)
    assert "apikey_" not in str(exc_info.value)


def test_unknown_answer_kind_raises_readable_error():
    payload = _answers_payload({"q": {"type": "essay", "text": "长篇大论"}})

    with pytest.raises(judgment.JudgmentError) as exc_info:
        judgment.ask_questions(
            "材料", {"q": judgment.yes_no("要不要？")}, opener=lambda request, timeout: payload
        )

    assert "essay" in str(exc_info.value)


def test_choice_and_score_convenience_helpers():
    captured: dict = {}

    def opener(request, timeout):
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return _answers_payload({"q": {"type": "choice", "choice": "B", "confidence": 0.8}})

    answer = judgment.judge_choice(
        "材料", "选一个？", {"A": "第一个", "B": "第二个"}, opener=opener
    )

    assert answer.choice == "B"
    criteria = captured["body"]["questions"]["q"]["criteria"]
    assert criteria == {"A": "第一个", "B": "第二个"}
