"""工作流侧的台词配音：提示词台词 → TTS 音轨 → 视频请求的音频参考。

画布侧由前端 `VideoNode` 完成同一件事（`frontend/src/features/canvas/domain/
promptDialogue.ts` + `application/dialogueDubbing.ts`）：抽出台词、合成配音、作为
`type="audio"` 参考提交，并把模式切到全能参考。工作流是服务端无人值守派发，没有前端
可依赖，因此在后端补齐同一条链。

两条链的抽取口径必须一致，否则同一条提示词在画布和工作流里会得到不同结果：
    - 引号：``“…”`` / ``「…」`` / ``『…』`` / ``"…"``
    - 方括号槽位：`[对话台词与语气：…]` / `[台词：…]` / `[对白：…]`
    - 句式：`台词：…` / `对白：…` / `说：…`（到标点或换行为止）

配音结果按「台词 + 声线」缓存到 `freezone_audio_speech` 输出目录，重跑同一条提示词
不会重复扣费。任何一步失败都只降级为「本次不配音」，绝不阻断整个工作流批次。
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from novelvideo.generators.video.capabilities import VideoMode
from novelvideo.generators.video.direct_models import resolve_direct_video_model
from novelvideo.project_context import ProjectContext
# 架构边界：workflow 域不得直接依赖 freezone 实现，统一走 services 门面。

_LOGGER = logging.getLogger(__name__)

#: 与画布前端 `synthesizeDialogueAudio` 相同的声线槽位。
VOICE_SCOPE = "project_narrator"

#: 音频参考在能力合同里的角色分类必须是「普通参考」，不能命中首帧/尾帧。
DIALOGUE_AUDIO_ROLE = "声音/节奏参考"

#: 配音文件名的稳定前缀，便于从 `freezone_audio_speech` 输出里区分工作流配音。
JOB_ID_PREFIX = "wfdlg"

_QUOTED_PATTERN = re.compile(r"“([^”]*)”|「([^」]*)」|『([^』]*)』|\"([^\"]*)\"")
_BRACKET_SLOT_PATTERN = re.compile(
    r"\[(?:对话台词与语气|对话台词|台词|对白)\s*[:：]\s*([^\]\n]{1,240})\]"
)
_CLAUSE_PATTERN = re.compile(
    r"(?:台词|对白|说|说道|喊道|低声道|Says)\s*[:：]\s*"
    r"([^，,。！？!?；;\n\]\[“”「」『』\"|]{1,240})",
    re.IGNORECASE,
)
#: 「没有台词」的占位写法；与前端 `promptDialogue.ts`、`freezone/script_contract.py`
#: 同一口径，任何一侧补充写法都要同步，否则脚本表的「无」会被当成真台词。
NO_DIALOGUE_VALUES = frozenset(
    {
        "",
        "无",
        "没有",
        "无台词",
        "无对白",
        "没有台词",
        "没有对白",
        "无。",
        "none",
        "no dialogue",
        "n/a",
    }
)


def _normalize_dialogue(value: object) -> str:
    """去掉台词两端的引号与空白；槽位/句式命中时可能自带引号。"""

    text = str(value or "").strip()
    text = re.sub(r"^[\"'“”「」『』\s]+", "", text)
    text = re.sub(r"[\"'“”「」『』\s]+$", "", text)
    return re.sub(r"\s+", " ", text).strip()


def is_no_dialogue_value(value: object) -> bool:
    """这条值是不是「没有台词」占位（空值也算），而不是一句要说出来的台词。"""

    return _normalize_dialogue(value).casefold() in NO_DIALOGUE_VALUES


def extract_dialogue_lines(
    prompt: object,
    *,
    declared_dialogue: Sequence[object] = (),
) -> tuple[str, ...]:
    """按出现顺序抽出台词并去重；抽不到或全是「无」占位时返回空元组。

    ``declared_dialogue`` 是节点数据里已声明的台词（`dialogueText` /
    `spokenDialogue`），与提示词抽出的结果合并去重。
    """

    source = str(prompt or "")
    indexed: list[tuple[int, str]] = []
    for pattern in (_QUOTED_PATTERN, _BRACKET_SLOT_PATTERN, _CLAUSE_PATTERN):
        for match in pattern.finditer(source):
            text = next(
                (
                    group
                    for group in match.groups()
                    if isinstance(group, str) and group.strip()
                ),
                "",
            )
            if text:
                indexed.append((match.start(), text))
    indexed.sort(key=lambda item: item[0])

    values = [text for _offset, text in indexed]
    for declared in declared_dialogue:
        values.extend(str(declared or "").splitlines())

    lines: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _normalize_dialogue(value)
        if is_no_dialogue_value(text) or text in seen:
            continue
        seen.add(text)
        lines.append(text)
    return tuple(lines)


def dialogue_cache_key(lines: Sequence[str], voice_key: str = VOICE_SCOPE) -> str:
    """配音缓存比较键；格式与前端 `dialogueDubbingCacheKey` 完全一致。"""

    return f"{voice_key}\x00" + "\x01".join(lines)


def dialogue_job_id(lines: Sequence[str], voice_key: str = VOICE_SCOPE) -> str:
    """由台词与声线派生的稳定任务 id，同时充当落盘文件名。"""

    material = dialogue_cache_key(lines, voice_key).encode("utf-8")
    return f"{JOB_ID_PREFIX}-{hashlib.sha256(material).hexdigest()[:20]}"


@dataclass(frozen=True)
class DialogueDubbingResult:
    """一次台词配音决策的结果。

    ``applied`` 表示本地音轨已经就绪，可用于视频后混音；视频模型是否接受
    这条音轨作为参考由 ``reference_applied`` 单独表达。两条事实必须分开，
    否则不支持音频参考的模型会让工作流提前丢掉对白。
    """

    lines: tuple[str, ...] = ()
    applied: bool = False
    reason: str = ""
    reference_applied: bool = False
    reference_reason: str = ""
    audio_path: str = ""
    audio_url: str = ""
    job_id: str = ""
    cache_key: str = ""
    cache_hit: bool = False

    def as_receipt(self) -> dict[str, Any]:
        return {
            "applied": self.applied,
            "reason": self.reason,
            "reference_applied": self.reference_applied,
            "reference_reason": self.reference_reason,
            "lines": list(self.lines),
            "job_id": self.job_id,
            "cache_hit": self.cache_hit,
            "audio_path": self.audio_path,
            "audio_url": self.audio_url,
            "voice_scope": VOICE_SCOPE,
        }


def _skipped(
    lines: Sequence[str],
    reason: str,
    *,
    job_id: str = "",
    cache_key: str = "",
    reference_reason: str = "",
) -> DialogueDubbingResult:
    return DialogueDubbingResult(
        lines=tuple(lines),
        reason=reason,
        reference_reason=reference_reason,
        job_id=job_id,
        cache_key=cache_key,
    )


def _model_audio_reference_gate(backend: str) -> tuple[str, Any]:
    """返回 (拒绝原因, 能力)；原因非空表示当前模型不能收音频参考。"""

    direct_model = resolve_direct_video_model(backend)
    if direct_model is None:
        return "model_not_direct", None
    capability = getattr(direct_model, "capability", None)
    modes = tuple(getattr(capability, "modes", ()) or ())
    if VideoMode.REFERENCE_TO_VIDEO not in modes:
        return "reference_mode_unsupported", capability
    limits = getattr(capability, "reference_limits", None)
    try:
        audio_limit = int(getattr(limits, "reference_audios", 0) or 0)
    except (TypeError, ValueError):
        audio_limit = 0
    if audio_limit <= 0:
        return "audio_reference_unsupported", capability
    return "", capability


async def prepare_dialogue_dubbing(
    *,
    ctx: ProjectContext,
    prompt: object,
    backend: str,
    declared_dialogue: Sequence[object] = (),
    references: Sequence[Mapping[str, Any]] = (),
    last_frame_path: object = "",
    snapshot: Mapping[str, Any] | None = None,
    on_log: Callable[[str], None] | None = None,
) -> DialogueDubbingResult:
    """Keep declared speech for the video model; never synthesize external audio."""
    lines = extract_dialogue_lines(prompt, declared_dialogue=declared_dialogue)
    return _skipped(lines, "native_video_audio" if lines else "no_dialogue")


__all__ = [
    "DIALOGUE_AUDIO_ROLE",
    "DialogueDubbingResult",
    "VOICE_SCOPE",
    "dialogue_cache_key",
    "dialogue_job_id",
    "extract_dialogue_lines",
    "prepare_dialogue_dubbing",
]
