"""Implementation of the JEV judgment tool for the Village canvas agent.

Read-only: the call judges the supplied content and never touches the canvas,
tasks, or paid media generation. Credentials stay inside the judgment service.
"""

from __future__ import annotations

import time
from typing import Any

from novelvideo.agent_tools.tool_contract import tool_error, tool_result
from novelvideo.services import judgment as jev

_QUESTIONS_SCHEMA_NOTE = (
    "每个问题需要 type(noul/choice/score) 与 instructions；"
    "criteria：是非题为 {true, false}，选择题为 {选项名: 说明}，"
    "打分题为从低到高的刻度数组（位置即分数，从 0 起）。"
)


def _normalize_question(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError(_QUESTIONS_SCHEMA_NOTE)
    question_type = str(raw.get("type") or "").strip()
    if question_type not in {"noul", "choice", "score"}:
        raise ValueError(f"未知题型 {question_type!r}，只支持 noul/choice/score")
    instructions = raw.get("instructions")
    if instructions is None or not str(instructions).strip():
        raise ValueError("questions 缺少 instructions")
    criteria = raw.get("criteria")
    if question_type == "noul":
        return jev.yes_no(instructions, **_noul_criteria(criteria))
    if question_type == "choice":
        if not isinstance(criteria, dict) or not criteria:
            raise ValueError("选择题的 criteria 需要 {选项名: 何时选它} 形式")
        return jev.choice(instructions, {str(k): str(v) for k, v in criteria.items()})
    if not isinstance(criteria, (list, tuple)) or not criteria:
        raise ValueError("打分题的 criteria 需要从低到高的刻度数组")
    return jev.score(instructions, [str(level) for level in criteria])


def _noul_criteria(criteria: Any) -> dict[str, str]:
    if not isinstance(criteria, dict):
        return {}
    true_means = str(criteria.get("true") or "")
    false_means = str(criteria.get("false") or "")
    return {"true_means": true_means, "false_means": false_means}


def _handle_jev_judge(args: dict[str, Any], **_: Any) -> str:
    """Judge a batch of named questions with the JEV service."""

    t0 = time.perf_counter()
    try:
        raw_state = args.get("state")
        if isinstance(raw_state, str):
            state: Any = raw_state
        elif isinstance(raw_state, (list, tuple)):
            state = [str(item) for item in raw_state if str(item or "").strip()]
        else:
            raise ValueError("state 需要是一段文本或一组文本")
        if not state:
            raise ValueError("state 为空：判断必须提供材料")

        raw_questions = args.get("questions")
        if not isinstance(raw_questions, dict) or not raw_questions:
            raise ValueError(_QUESTIONS_SCHEMA_NOTE)
        questions = {
            str(name): _normalize_question(raw) for name, raw in raw_questions.items()
        }

        model = str(args.get("model") or "").strip() or None
        answers = jev.ask_questions(state, questions, model=model)
        payload = {
            "ok": True,
            "model": str(args.get("model") or "jev-latest"),
            "answers": {
                name: _answer_payload(answer) for name, answer in answers.items()
            },
        }
        return tool_result(
            _with_jev_trace(payload, t0=t0, ok=True, count=len(answers))
        )
    except (jev.JudgmentError, ValueError, TypeError) as exc:
        return tool_error(str(exc), tool="village_canvas_jev_judge", t0=t0)


def _answer_payload(answer: Any) -> dict[str, Any]:
    if isinstance(answer, jev.YesNoAnswer):
        band = "uncertain"
        if answer.probability >= 0.7:
            band = "yes"
        elif answer.probability <= 0.3:
            band = "no"
        return {"type": "noul", "probability": answer.probability, "verdict": band}
    if isinstance(answer, jev.ChoiceAnswer):
        return {
            "type": "choice",
            "choice": answer.choice,
            "confidence": answer.confidence,
            "probabilities": answer.probabilities,
        }
    if isinstance(answer, jev.ScoreAnswer):
        return {
            "type": "score",
            "score": answer.score,
            "confidence": answer.confidence,
            "legend": answer.legend,
        }
    return {"type": "unknown"}


def _with_jev_trace(payload: dict[str, Any], *, t0: float, ok: bool, count: int) -> dict[str, Any]:
    traced = dict(payload)
    traced["elapsed_ms"] = int((time.perf_counter() - t0) * 1000)
    traced["question_count"] = count
    return traced
