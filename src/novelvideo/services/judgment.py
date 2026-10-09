"""JEV 判断层：画布、工作流与 Agent 共用的结构化判断服务。

JEV（TypeSafe SystemOne）是专职判断接口，不讲聊天协议：是非题给概率、
选择题给选项和置信度、打分题按刻度给加权分。一次请求可携带一批命名问题，
共用同一段材料（state）。

凭据只从受保护的项目本地文件或环境变量读取，绝不进入日志、结果或错误信息。
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_KEY_FILE = _PROJECT_ROOT / "项目资产" / "state" / "jev-api-key.txt"
_DEFAULT_BASE_URL = "https://api.typesafe.ai/v1/systemone"

_TRANSIENT_HTTP_CODES = {429, 500, 502, 503, 504}
_MAX_ATTEMPTS = 3


class JudgmentError(RuntimeError):
    """JEV 判断失败，统一归一到这个不含密钥的错误。"""


@dataclass
class YesNoAnswer:
    """是非题答案：probability 接近 1 为是，接近 0 为否，0.5 附近为拿不准。"""

    probability: float


@dataclass
class ChoiceAnswer:
    """选择题答案：选中的选项名、置信度与各选项概率。"""

    choice: str
    confidence: float
    probabilities: dict[str, float] = field(default_factory=dict)


@dataclass
class ScoreAnswer:
    """打分题答案：按刻度位置加权的分数、置信度与刻度说明。"""

    score: float
    confidence: float
    legend: dict[str, str] = field(default_factory=dict)


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _load_key(keys_file: str | os.PathLike[str] | None = None) -> str:
    key = str(os.environ.get("JEV_API_KEY", "") or "").strip()
    if key:
        return key
    path = Path(keys_file or _DEFAULT_KEY_FILE)
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _question(
    question_type: str,
    instructions: Any,
    criteria: Any,
) -> dict[str, Any]:
    question: dict[str, Any] = {"type": question_type, "instructions": instructions}
    if criteria is not None:
        question["criteria"] = criteria
    return question


def yes_no(statement: Any, true_means: str = "", false_means: str = "") -> dict[str, Any]:
    """是非题。criteria 说明什么算「是」/「否」，可选。"""

    criteria = None
    if true_means or false_means:
        criteria = {"true": true_means or "题目所述成立", "false": false_means or "题目所述不成立"}
    return _question("noul", statement, criteria)


def choice(question: Any, options: dict[str, str]) -> dict[str, Any]:
    """选择题。options 的键就是选项名，值是「何时选它」的说明。"""

    return _question("choice", question, dict(options))


def score(question: Any, levels: list[str]) -> dict[str, Any]:
    """打分题。levels 是从低到高的刻度说明，位置即分数，从 0 起。"""

    return _question("score", question, list(levels))


def ask_questions(
    state: Any,
    questions: dict[str, dict[str, Any]],
    *,
    model: str | None = None,
    timeout: float | None = None,
    keys_file: str | os.PathLike[str] | None = None,
    opener: Callable[..., Any] = urlopen,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, YesNoAnswer | ChoiceAnswer | ScoreAnswer]:
    """一次请求判断一批命名问题，返回与问题同名的答案对象。"""

    key = _load_key(keys_file)
    # A caller-supplied opener is the explicit dependency-injection seam used
    # by contract tests and local dry-runs.  It must be able to exercise
    # request parsing and retry behavior without a real credential.
    if not key and opener is urlopen:
        raise JudgmentError("JEV 判断不可用：未配置密钥（环境变量 JEV_API_KEY 或 state/jev-api-key.txt）")
    if not questions:
        raise JudgmentError("JEV 判断失败：问题列表为空")

    base_url = str(os.environ.get("JEV_BASE_URL", "") or _DEFAULT_BASE_URL).rstrip("/")
    payload = {
        "model": str(model or os.environ.get("JEV_MODEL", "") or "jev-latest"),
        "state": state,
        "questions": questions,
    }
    wait = _env_float("JEV_TIMEOUT_SECONDS", 30.0)

    last_error = ""
    for attempt in range(_MAX_ATTEMPTS):
        request = Request(
            f"{base_url}",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with opener(request, timeout=wait) as response:
                raw = json.loads(response.read().decode("utf-8"))
            return _parse_answers(raw.get("answers") or {})
        except HTTPError as exc:
            if exc.code in _TRANSIENT_HTTP_CODES and attempt < _MAX_ATTEMPTS - 1:
                last_error = f"HTTP {exc.code}"
                sleep(1.0 * (attempt + 1))
                continue
            raise JudgmentError(f"JEV 判断失败：HTTP {exc.code}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            if attempt < _MAX_ATTEMPTS - 1:
                last_error = str(exc)[:80]
                sleep(1.0 * (attempt + 1))
                continue
            raise JudgmentError(f"JEV 判断失败：连接不上判断服务（{last_error}）") from exc
        except json.JSONDecodeError as exc:
            raise JudgmentError("JEV 判断失败：响应不是有效 JSON") from exc
    raise JudgmentError(f"JEV 判断失败：{last_error or '未知错误'}")


def _parse_answers(raw: dict[str, Any]) -> dict[str, YesNoAnswer | ChoiceAnswer | ScoreAnswer]:
    parsed: dict[str, YesNoAnswer | ChoiceAnswer | ScoreAnswer] = {}
    for name, answer in (raw or {}).items():
        kind = str((answer or {}).get("type", "") or "")
        if kind == "noul":
            parsed[name] = YesNoAnswer(probability=float(answer.get("noul", 0.5)))
        elif kind == "choice":
            parsed[name] = ChoiceAnswer(
                choice=str(answer.get("choice", "") or ""),
                confidence=float(answer.get("confidence", 0.0)),
                probabilities={str(k): float(v) for k, v in (answer.get("probabilities") or {}).items()},
            )
        elif kind == "score":
            parsed[name] = ScoreAnswer(
                score=float(answer.get("score", 0.0)),
                confidence=float(answer.get("confidence", 0.0)),
                legend={str(k): str(v) for k, v in (answer.get("legend") or {}).items()},
            )
        else:
            raise JudgmentError(f"JEV 判断失败：问题 {name} 返回了未知题型 {kind!r}")
    return parsed


def judge_yes_no(
    state: Any,
    statement: Any,
    true_means: str = "",
    false_means: str = "",
    **kwargs: Any,
) -> float:
    """是非题便捷入口，返回「是」的概率 0-1。"""

    answer = ask_questions(state, {"q": yes_no(statement, true_means, false_means)}, **kwargs)
    result = answer["q"]
    assert isinstance(result, YesNoAnswer)
    return result.probability


def judge_choice(
    state: Any,
    question: Any,
    options: dict[str, str],
    **kwargs: Any,
) -> ChoiceAnswer:
    """选择题便捷入口，返回选中的选项与置信度。"""

    answer = ask_questions(state, {"q": choice(question, options)}, **kwargs)
    result = answer["q"]
    assert isinstance(result, ChoiceAnswer)
    return result


def judge_score(
    state: Any,
    question: Any,
    levels: list[str],
    **kwargs: Any,
) -> ScoreAnswer:
    """打分题便捷入口，返回加权分数与置信度。"""

    answer = ask_questions(state, {"q": score(question, levels)}, **kwargs)
    result = answer["q"]
    assert isinstance(result, ScoreAnswer)
    return result


__all__ = [
    "JudgmentError",
    "YesNoAnswer",
    "ChoiceAnswer",
    "ScoreAnswer",
    "ask_questions",
    "yes_no",
    "choice",
    "score",
    "judge_yes_no",
    "judge_choice",
    "judge_score",
]
