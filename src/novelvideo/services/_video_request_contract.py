"""Shared server-side video request contracts and prompt compilation.

Implementation owner: services layer (contract-ownership migration 2026-10-05).

The model profile validates structured parameters, while this module validates
the one semantic condition that upstream video gateways commonly enforce:
prompt references must have a matching uploaded media item.  Explicit prompt
duration mentions are diagnostic only; the selected node duration is
authoritative.  It is intentionally pure and non-billing so it can run before
task reservation and queue insertion.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import math
import re
from typing import Any, Callable, Iterable, Mapping

from novelvideo.generators.video.capabilities import NativeAudio, VideoMode


_DURATION_PATTERNS = (
    re.compile(
        r"(?:视频|影片|镜头|片段|全片|总时长|时长|持续(?:时间)?|duration|video|shot|clip|lasts?)"
        r"[^\n。；;]{0,12}?"
        r"(?P<seconds>\d+(?:\.\d+)?)\s*(?:秒|s|sec(?:ond)?s?)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?P<seconds>\d+(?:\.\d+)?)\s*(?:秒|s|sec(?:ond)?s?)"
        r"\s*(?:视频|影片|镜头|片段|时长|duration|video|shot|clip)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?P<seconds>\d+(?:\.\d+)?)[- ](?:second|sec|s)\s*"
        r"(?:video|clip|shot)",
        re.IGNORECASE,
    ),
)
_REFERENCE_TOKEN_PATTERN = re.compile(
    r"@\s*(?:图片|图像|参考图|image|img|reference)\s*[-_#]?\s*\d+",
    re.IGNORECASE,
)
_TIMELINE_RANGE_PATTERN = re.compile(
    r"\[\s*\d+(?:\.\d+)?\s*[-~至–—]\s*\d+(?:\.\d+)?\s*(?:秒|s|sec(?:ond)?s?)?\s*\]",
    re.IGNORECASE,
)
_VIDEO_RESOLUTION_PATTERN = re.compile(
    r"^(?:[1-9]\d{2,5}p(?:横|竖|\([^)]*\))?|[1-9]\d{0,2}k(?:横|竖|\([^)]*\))?|[1-9]\d{2,5}x[1-9]\d{2,5})$",
    re.IGNORECASE,
)

#: 冒号式台词前面的说话动词。
#:
#: 只有这几个词被承认为「接下来是台词」。`男孩回应：我们走吧` 里的 `回应：` 以前不在
#: 名单里，于是那句台词既不被抽成台词、也不被从画面里剥离——它就留在散文里，正是
#: 「模型照着提示词念」的入口。
#:
#: 多字动词必须排在单字前面（`说道` 先于 `说`、`喊道` 先于 `喊`），否则
#: 「女孩突然喊道别过来。」会被切成动词「喊」+ 台词「道别过来。」。
_DIALOGUE_SPEECH_VERB_PATTERN = (
    r"(?:低声道|回答道|回应道|说道|喊道|答道|回答|回应|反问|追问|插话|说|Says)"
)
_UNAUTHORIZED_DIALOGUE_MARKER_PATTERN = re.compile(
    rf"{_DIALOGUE_SPEECH_VERB_PATTERN}\s*[:：]",
    re.IGNORECASE,
)
_SINGING_DIALOGUE_MARKER_PATTERN = re.compile(
    r"(?:唱歌|唱着|唱道|吟唱|哼唱|演唱|歌词|sing(?:s|ing)?)\s*[:：]",
    re.IGNORECASE,
)
_QUOTED_DIALOGUE_PATTERN = re.compile(
    r"“[^”]*”|「[^」]*」|『[^』]*』|\"[^\"]*\""
)
_QUOTED_DIALOGUE_CAPTURE_PATTERN = re.compile(
    r"“(?P<cn>[^”]*)”|「(?P<corner>[^」]*)」|『(?P<guillemet>[^』]*)』|\"(?P<ascii>[^\"]*)\""
)
#: 新的说话人子句（「男孩回应：」）：出现在一句台词中间，说明这是**另一个**轮次，
#: 当前台词到此为止，后面的由 finditer 接着收。
_NEW_SPEAKER_FRAGMENT = (
    r"[\u4e00-\u9fff]{1,8}(?:低声道|回答道|回应道|说道|喊道|答道|回答|回应|反问|追问|插话|说|问|答|喊)"
)
_UNQUOTED_DIALOGUE_CLAUSE_PATTERN = re.compile(
    rf"{_DIALOGUE_SPEECH_VERB_PATTERN}\s*[:：]\s*"
    # 不吞「已经插好的表演提示」：剥离时引号那趟会先把台词换成「说话表演」，若此处
    # 再匹配 `说：说话表演<后续画面散文>`，就会把台词之后的画面描述一并删掉
    # （2026-10-01 回归测试抓到：`苏岚说：...她抬起头望向铜钟说出这句话` 丢了动作）。
    rf"(?!\s*(?:说话表演|演唱表演))"
    # 台词吃到句末标点，但遇到「，+ 新说话人：」就停（一句里两个说话人必须拆成两轮，
    # 否则「女孩说：你终于来了，男孩回应：我们走吧」会被并成一句）。
    # 同理，台词自己的逗号（「你...你这个傻瓜，遇见你之后…」）没有说话动词，不停。
    rf"(?P<text>(?:(?![，,]\s*{_NEW_SPEAKER_FRAGMENT}\s*[:：])[^。！？!?；;\n]){{1,240}})",
    re.IGNORECASE,
)
#: 逗号引导式台词：「在说话表演，遇见你之后，我就一直没有过上好日子。」。
#:
#: 2026-10-01 真机事故（task 415d6973）：正文里这句没有引号也没有冒号的台词
#: 三种既有形态都抽不到，于是它留在画面描述里；同时结构化对白槽兜底进了另一句
#: （「你这个傻瓜」）。MiniMax H3 的原生音频是 REQUIRED（关不掉），同一份报文里
#: 有了两个说话指令——正文一句、槽位一句——模型必然乱说。别家平台同一模型不乱说，
#: 正是因为人家报文里只有一句。
#: 修法：台词在提示词里只允许出现一次（既有不变式），把这种形态也抽进槽位、
#: 从画面描述里剥成「说话表演」。逗号后不得以转场/镜头词开头（「说话表演，然后
#: 转身离开」「，镜头拉远」是动作续写，不是台词）；台词一路吃到句末标点（中间
#: 的逗号是台词自己的）。幂等：剥离后的提示是「说话表演」+句末标点，不再被命中。
_UNQUOTED_DIALOGUE_COMMA_PATTERN = re.compile(
    rf"(?:说话表演|说话|开口(?:说道|说)?|{_DIALOGUE_SPEECH_VERB_PATTERN})"
    r"\s*[，,]\s*"
    r"(?!(?:然后|接着|随后|同时|并且|而且|于是|所以|但是|不过|镜头|画面|背景|字幕|水印|人物|角色|转身|低头|抬头))"
    r"(?P<text>[^。！？!?；;\n]{2,240})",
    re.IGNORECASE,
)
#: 台词吃到句末后，句中若出现这两类接续，那后面不是当前这句台词：
#: ① 「，+ 镜头/转场/动作续写」；② 「，+ 新的说话人」（女孩说：…，男孩回应：…）。
#: 抽取与剥离两侧共用，保证「抽哪句、剥哪句」一致。台词自己的逗号
#: （「你...你这个傻瓜，遇见你之后…」）两类都不命中，因此保留。
_DIALOGUE_CONTINUATION_RE = re.compile(
    r"[，,]\s*(?:然后|接着|随后|同时|并且|而且|于是|所以|但是|不过|"
    r"镜头|画面|背景|前景|远景|特写|字幕|水印|标牌|人物|角色|"
    r"转身|低头|抬头|站起|坐下|走向|伸手|回头|闭眼|摇头|点头|"
    r"[\u4e00-\u9fff]{1,8}(?:低声道|回答道|回应道|说道|喊道|答道|回答|回应|反问|追问|插话|说|问|答|喊))"
)


def _trim_dialogue_continuation(text: str) -> tuple[str, str]:
    """Split captured speech from a trailing camera/action continuation.

    返回 ``(spoken, tail)``：``tail`` 保留分隔符，剥离器用它把镜头/动作续写留在
    画面描述里，而不是当成台词一起删掉。
    """

    match = _DIALOGUE_CONTINUATION_RE.search(text)
    if not match:
        return text, ""
    return text[: match.start()].strip(), text[match.start():]
#: 平铺叙述里的台词：「女孩对男孩说我们分手吧」。
#:
#: 没有冒号也没有引号，上面所有正则都看不见它，于是一句真要说的台词被当成静音
#: 镜头处理，用户看到的是「台词说不对」。
#:
#: 误判的代价不对称，所以这里卡两道：动词后必须直接接够长的中文、并且一路走到
#: 句末标点，同时排除「说明/说服/说出/问题/答案/说完」这类把动词当构词成分的词。
#: 「人物开口说话，镜头推近」因为「话」后面只有逗号而落选，正是想要的效果。
#:
#: 多字动词必须排在单字前面（`说道` 先于 `说`、`喊道` 先于 `喊`），否则
#: 「女孩突然喊道别过来。」会被切成动词「喊」+ 台词「道别过来。」。
_PLAIN_NARRATION_DIALOGUE_PATTERN = re.compile(
    r"[\u4e00-\u9fff]{1,8}(?:低声道|说道|喊道|说|问|答|喊)"
    # `话` 必须排除：`说话` 是「说话」这个动词本身，不是「说」+台词；
    # 而且剥离器自己产出的表演提示就是「说话表演」，漏掉 `话` 会让第二轮规范化
    # 把 `男孩说话表演。` 读回成台词 `话表演。`。
    r"(?![明法服合和完过儿出题案声做话])"
    r"(?P<text>[\u4e00-\u9fff]{3,40}[。！？!?])"
)
_SINGING_DIALOGUE_CLAUSE_PATTERN = re.compile(
    r"(?:唱歌|唱着|唱道|吟唱|哼唱|演唱|歌词|sing(?:s|ing)?)\s*[:：]\s*"
    r"(?P<text>[^。\n！？!?；;]{1,240}(?:[。！？!?]+)?)",
    re.IGNORECASE,
)
_LOGGER = logging.getLogger(__name__)

#: 引号前面的这几个词说明引号里是**画面文字**，不是台词。
#:
#: `雨落在屋檐上…路牌写着“欢迎回家”。` 里的引号是招牌内容。2026-09-15 把台词移进
#: `对白：` 槽位之后，这类引号差点被一起提升成台词——那会让角色开口念出招牌。
#: 画面文字本来就该留在画面描述里（它确实是画面的一部分），既不能进对白槽位，
#: 也不能被剥离成「说话表演」。
_SCENERY_TEXT_LEAD_PATTERN = re.compile(
    r"(?:写着|写着字|标注|标明|印着|刻着|显示)\s*[:：]?\s*$"
    r"|(?:字幕|标题|招牌|路牌|标牌|横幅|标语|画面文字)\s*[:：]\s*$"
)

#: 机器生成的对白槽位行。必须能被原样识别回来，否则同一条提示词被规范化两次
#: （REST 路由一次、job 一次）时会从槽位里再抽一遍台词、再挂一遍槽位。
#: 形状与 `_build_native_dialogue_provider_prompt` 一一对应。
_NATIVE_DIALOGUE_SLOT_LINE_PATTERN = re.compile(
    r"^对白：(?:@[^：:]{1,40}：)?「(?P<text>[^」]*)」口型同步$"
)


def _split_existing_dialogue_slots(value: object) -> tuple[str, tuple[str, ...]]:
    """Peel machine-generated dialogue slots off a prompt.

    Returns the remaining prose plus the spoken turns the slots carried, in order.
    槽位里的词必须先收回来再剥掉——第一次规范化已经把画面描述里的台词换成了
    「说话表演」，词只剩在槽位里；直接丢掉槽位就等于把台词弄丢。
    """

    spoken: list[str] = []
    kept: list[str] = []
    for line in str(value or "").splitlines():
        match = _NATIVE_DIALOGUE_SLOT_LINE_PATTERN.match(line.strip())
        if match:
            spoken.append(match.group("text").strip())
            continue
        kept.append(line)
    return "\n".join(kept).strip(), tuple(text for text in spoken if text)


def _is_scenery_text_quote(source: str, quote_start: int) -> bool:
    """Return True when the quoted run at ``quote_start`` is on-screen text."""

    prefix = source[max(0, quote_start - 14):quote_start]
    return bool(_SCENERY_TEXT_LEAD_PATTERN.search(prefix))

_VIDEO_ASPECT_PATTERN = re.compile(
    r"^[1-9]\d{0,5}(?:\.\d{1,4})?:[1-9]\d{0,5}(?:\.\d{1,4})?$"
)


def normalize_video_resolution_value(value: object) -> str | None:
    """Normalize a resolution label without imposing a local preset list."""
    normalized = str(value or "").strip().lower().replace("×", "x")
    return normalized if _VIDEO_RESOLUTION_PATTERN.fullmatch(normalized) else None


def explicit_audio_type_requests_silence(value: object) -> bool:
    """Return true only for an explicit visual-only audio contract."""
    return str(value or "").strip().casefold() in {"silence", "action"}


@dataclass(frozen=True, slots=True)
class VideoRequestIssue:
    code: str
    message: str
    details: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, **self.details}


class VideoRequestContractError(ValueError):
    """Raised when semantic video invariants fail before queue/billing work.

    The exception carries a credential-free provider-style metadata contract so
    both API callers and durable task runners can present the same actionable
    diagnosis without serializing the original prompt or media paths.
    """

    def __init__(self, issues: Iterable[VideoRequestIssue]):
        normalized = tuple(issue for issue in issues if isinstance(issue, VideoRequestIssue))
        if not normalized:
            raise ValueError("VideoRequestContractError requires at least one issue")
        self.issues = normalized
        super().__init__("；".join(issue.message for issue in normalized))
        self.provider_error_metadata = self._build_provider_error_metadata()

    def _build_provider_error_metadata(self) -> dict[str, object]:
        return {
            "error_code": "VIDEO_REQUEST_CONTRACT_INVALID",
            "endpoint_class": "video-request-contract",
            "stage": "preflight",
            "verification_stage": "contract",
            "retryable": False,
            "suggested_action": "按节点能力修正提示词时长或绑定对应参考素材后重试。",
            "request_contract": {
                "violations": [
                    {
                        "code": issue.code,
                        "details": dict(issue.details),
                    }
                    for issue in self.issues
                ]
            },
        }


@dataclass(frozen=True, slots=True)
class VideoPromptNormalization:
    """The lossless result of separating visual direction from speech.

    ``visual_prompt`` is used by silent and external-audio video requests.
    ``provider_prompt`` is the generic native-audio contract: the same visual
    direction plus one tagged ``对白：`` slot holding the spoken turns.
    MiniMax H3 has a provider-specific structured form built by
    :func:`build_minimax_h3_provider_prompt` from the same normalized fields.
    ``spoken_dialogue`` is retained for audio, lip-sync, subtitle, and task
    metadata consumers. The tuple deliberately preserves multiple quoted
    turns in source order.
    """

    visual_prompt: str
    # ``provider_prompt`` carries the user's words exactly once, inside the
    # ``对白：`` slot; the prose around it never repeats them.  Native-audio
    # providers read this field, so the words and the descriptions stay in
    # physically separate blocks.
    provider_prompt: str = ""
    spoken_dialogue: tuple[str, ...] = ()
    had_dialogue_marker: bool = False
    dialogue_is_sung: bool = False
    #: 台词来自哪里：``prompt``=提示词正文自带、``structured``=对白字段、``restored``=上一轮
    #: 产出的槽位。正文与字段都写了台词且不一致时取正文（既有权威顺序），并把
    #: ``dialogue_structured_overridden`` 置真，让调用方能在回执里告诉用户。
    dialogue_source: str = ""
    dialogue_structured_overridden: bool = False

    @property
    def dialogue_text(self) -> str:
        """Return the canonical single text payload used by current audio APIs."""

        return " ".join(self.spoken_dialogue)


def extract_prompt_duration_mentions(prompt: str) -> tuple[int, ...]:
    """Extract only explicit duration phrases, avoiding generic number prose."""

    values: list[int] = []
    # Shot timelines describe an interval inside the clip, not the clip's
    # authoritative total duration.  Remove those ranges before matching the
    # provider-style total-duration phrases.
    text = _TIMELINE_RANGE_PATTERN.sub(" ", str(prompt or ""))
    for pattern in _DURATION_PATTERNS:
        for match in pattern.finditer(text):
            try:
                value = int(round(float(match.group("seconds"))))
            except (TypeError, ValueError, OverflowError):
                continue
            if value > 0 and value not in values:
                values.append(value)
    return tuple(values)


def extract_prompt_reference_tokens(prompt: str) -> tuple[str, ...]:
    """Return explicit @reference tokens; plain words like '图片' are prose."""

    return tuple(match.group(0).strip() for match in _REFERENCE_TOKEN_PATTERN.finditer(str(prompt or "")))


_REFERENCE_LABEL_LEAD_PATTERN = re.compile(
    r"(?:参考图|参考图片|参考帧|角色图|场景图|资产图)\s*\d*\s*"
    r"(?:(?:提供|对应|代表|使用|标注为)\s*)?"
    r"(?:角色|场景|资产|主体)?\s*[：:]?\s*$"
)
_REFERENCE_LABEL_SUFFIX_PATTERN = re.compile(
    r"^\s*的(?:脸型|五官|发型|肤色|体型|外观|形象|造型|身份|服装|"
    r"材质|颜色|空间关系|空间|固定陈设|背景|场景|人物|构图|光线|"
    r"特征|参考)"
)


def _is_reference_asset_label_quote(source: str, start: int, end: int) -> bool:
    """Treat ``参考图1提供「角色名」的脸型`` as metadata, not spoken text."""

    before = source[max(0, start - 40) : start]
    after = source[end : end + 24]
    return bool(
        _REFERENCE_LABEL_LEAD_PATTERN.search(before)
        and _REFERENCE_LABEL_SUFFIX_PATTERN.match(after)
    )


def _has_non_reference_quote(value: object) -> bool:
    """Return whether a quote must block structured-dialogue fallback.

    Scenery and on-screen text still own the prompt's speech marker even though
    they are not dialogue. Reference asset labels are metadata and therefore do
    not block an explicit ``spoken_dialogue`` field.
    """

    source = str(value or "")
    for match in _QUOTED_DIALOGUE_PATTERN.finditer(source):
        if _is_reference_asset_label_quote(source, match.start(), match.end()):
            continue
        return True
    return False


def _quoted_dialogue_parts(value: object) -> tuple[str, ...]:
    """Extract quoted turns in source order without promoting plain prose."""

    parts: list[str] = []
    for match in _QUOTED_DIALOGUE_CAPTURE_PATTERN.finditer(str(value or "")):
        text = next(
            (
                group.strip()
                for group in match.groups()
                if isinstance(group, str) and group.strip()
            ),
            "",
        )
        if text:
            parts.append(text)
    return tuple(parts)


def _prompt_spoken_dialogue_parts(value: object) -> tuple[str, ...]:
    """Extract quoted, colon-marked, narrated and sung turns in source order.

    The first implementation collected these two forms in separate passes,
    which could reorder a mixed spoken/sung script.  Keep the source offset
    until deduplication so timeline order remains deterministic.

    Colon form（``说：我不回去了``）和叙述式（``女孩对男孩说我们分手吧。``）都必须
    收进来：`strip_dialogue_text_from_visual_prompt` 会把它们从画面描述里换成
    「说话表演」，如果这里不认，那句话就两个面都不存在，模型只能自己编一句。
    """

    source = str(value or "")
    indexed: list[tuple[int, str]] = []
    # 成对引号那趟收走的字符区间：第二趟与它重叠就跳过，避免同一句进两次。
    quoted_spans: list[tuple[int, int]] = []
    # 唱段整段由下面那趟按原样收走，引号那趟要跳过落在唱段里的引号，否则同一句
    # 歌词会被两趟各收一次（两趟的 offset 不同，按位置去重挡不住）。
    singing_spans = [
        match.span() for match in _SINGING_DIALOGUE_CLAUSE_PATTERN.finditer(source)
    ]
    for match in _QUOTED_DIALOGUE_CAPTURE_PATTERN.finditer(source):
        # 招牌、字幕、标题一类的画面文字不是台词，留在画面描述里。
        if _is_scenery_text_quote(source, match.start()):
            continue
        # 参考图说明会用「」标资产名（角色/场景/道具）。那是素材元数据，
        # 不是角色台词；否则 H3 会把「客人」「裁缝铺」逐字念出来。
        if _is_reference_asset_label_quote(source, match.start(), match.end()):
            continue
        if any(start <= match.start() < end for start, end in singing_spans):
            continue
        text = next(
            (
                group.strip()
                for group in match.groups()
                if isinstance(group, str) and group.strip()
            ),
            "",
        )
        if text:
            indexed.append((match.start(), text))
            quoted_spans.append(match.span())
    for pattern in (
        _UNQUOTED_DIALOGUE_CLAUSE_PATTERN,
        _UNQUOTED_DIALOGUE_COMMA_PATTERN,
        _PLAIN_NARRATION_DIALOGUE_PATTERN,
    ):
        for match in pattern.finditer(source):
            # 与成对引号那趟收走的区间重叠 => 引号写法已经拿到，跳过避免重复。
            # 用「区间重叠」而不是「文本里有没有引号」判断：冒号形式常常只截到
            # 半个引号（``说：“你终于来了``），那种未成对的引号本就该由本趟收。
            if any(start <= match.start("text") < end for start, end in quoted_spans):
                continue
            marked_text = str(match.group("text") or "").strip()
            # 作者写的引号常常是碎的（半角/全角混用、只写半个、夹省略号），例如
            # `在说：”你...你这个傻瓜…"`。这种写法引号那趟收不到（凑不成对），若这里
            # 再按「首字符是引号就跳过」，整句台词就两个面都不存在，H3（原生音频
            # 关不掉）只能自己编——2026-10-01 真机 task a149bd2c 就是这个形态。
            # 所以剥掉首尾游离引号后照收；成对的引号已由上一趟按区间重叠挡掉。
            marked_text = marked_text.strip("“”「」『』\"'").strip()
            marked_text, _tail = _trim_dialogue_continuation(marked_text)
            if marked_text:
                indexed.append((match.start("text"), marked_text))
    for match in _SINGING_DIALOGUE_CLAUSE_PATTERN.finditer(source):
        marked_text = str(match.group("text") or "").strip()
        if not marked_text:
            continue
        quoted = _quoted_dialogue_parts(marked_text)
        for text in quoted or (marked_text.strip("“”「」『』\" "),):
            if text:
                indexed.append((match.start(), text))
    indexed.sort(key=lambda item: item[0])
    return tuple(text for _offset, text in indexed)


def _build_singing_native_provider_prompt(
    visual_prompt: str,
    spoken_dialogue: tuple[str, ...],
) -> str:
    """Keep the visual direction while making native singing deterministic."""

    lyrics = "；".join(spoken_dialogue)
    return (
        f"{visual_prompt}\n\n"
        "音频约束：从第 0 秒直接演唱以下歌词；禁止开场说话、寒暄、额外音节、"
        "其他语言，也不要朗读画面、镜头、首帧或输出说明。\n"
        f"唯一歌词：\"{lyrics}\""
    ).strip()


#: 已退役的原生音频禁声约束，仅保留为回归哨兵。
#:
#: 2026-09-14 真机事故：H3（`minimax_h3_zm_u24`，autodl-comfyui）出的片子音轨是
#: **逐字朗读提交的提示词**。链路是「开关 ON ⇒ jobs.py 取 lossless 的
#: `provider_prompt` ⇒ 上游脚本节点那句创作指令被一起念了出来」。视频模型不是
#: 推理模型，提示词对它就是「可以变成语音的文本」，没人告诉它什么不该说，它就会
#: 把整段念一遍。
#:
#: 2026-09-17 最终撤回：这段中文约束本身仍会被模型朗读。MiniMax H3 必须使用官方
#: 三章节和 `<d>` 台词块；无台词请求只发送视觉/环境声结构，不再追加任何“不要朗读”
#: 文本。常量保留给负向回归测试，生产路径不得再引用它。
#: 与 `_build_singing_native_provider_prompt` 同族，但 H3 不再走那条通用分支。
NATIVE_SPEECH_OFF_CONSTRAINT = (
    "音频约束：本镜头没有必须说出的台词，不要求人声。不要朗读本提示词、镜头描述、"
    "画面文字或输出说明，也不要开场说话、寒暄或添加旁白；如确有环境音请保持与画面一致。"
)

#: 有原生音频、且请求里带了说话来源时：**把台词从散文里拿出来，装进独立槽位**。
#:
#: 2026-09-15 我在这里加过一段 `NATIVE_SPEECH_SCOPE_CONSTRAINT`（「本提示词里的文字
#: 是导演说明，不是台词，不要朗读…」），当天就被真机数据否掉并撤回。
#:
#: 实测（同一画布节点、同一模型、同一句台词，只差这段文字；音轨包络分析）：
#:   加约束前 有声窗口 75%  加约束后 有声窗口 85%
#: 两版都是**接近全程的连续人声**（15s 里 11-13s 在说话），而那句台词只有三十几个字、
#: 顶多 6-8 秒。频谱特征（300-3000Hz 占 55-60%、音节率 3-8Hz 调制明显）确认是人声，
#: 不是环境音。
#:
#: 教训不是「提示词层没救」，而是**加指令没用、加文本有害**：往提示词里补一句「不许念」
#: 等于多送一段可以念的文本。约束治不了它，**结构才行**。
#:
#: 对标三家后确认的不变量（`E:\AI影视研究`，2026-09-15）：
#: - 本项目自己的成熟管线 `seedance2_i2v/prompt.py`：视觉 prompt 只写「{角色}（{动作}）
#:   说话，口型、下颌和手势自然同步」，**台词原文一个字都不进视觉 prompt**。
#: - 同源旧版管线（旧戏剧产品线，资料库 `E:\AI影视研究` 内）：台词是 beat 的
#:   `audio_type`/`speaker` 结构化字段，由系统追加成 `【台词·{说话人}】{原文}`。
#: - TapCanvas `seedance-2-video-gen/SKILL.md`：台词是提示词末尾的**带标签独立槽位**
#:   `对白：@角色（情绪）：「台词」口型同步`，符号 2026-07-10 全项目统一。
#:
#: 所以这里的修法是：`provider_prompt` 用**已剥离台词的画面描述**，后面只挂一条
#: `对白：` 槽位。台词在整个提示词里**只出现一次**，且被「」围栏和标签锁死，
#: 与描述性文字物理隔离。这既不改写用户的字，也不新增可念文本。
NATIVE_SPEECH_SCOPE_CONSTRAINT_RETIRED = True

#: 原生音频下承载台词的唯一槽位标签，逐字对齐 TapCanvas 的全项目统一符号。
NATIVE_DIALOGUE_SLOT_LABEL = "对白："
#: 槽位尾标记：显式告诉模型人声与口型的对应关系。
NATIVE_DIALOGUE_SYNC_MARKER = "口型同步"


def _build_native_dialogue_provider_prompt(
    visual_prompt: str,
    spoken_dialogue: tuple[str, ...],
    *,
    speaker: str = "",
) -> str:
    """Attach the dialogue as one fenced slot instead of inline prose.

    TapCanvas 的交付格式是 ``对白：@角色（情绪）：「台词」口型同步``。画布没有情绪
    字段，因此保留标签、角色与围栏，省略括号段。多轮台词按源顺序各占一行，让模型
    看得见轮次而不是把几段话糊成一段。
    """

    speaker_label = f"@{speaker.strip()}：" if speaker.strip() else ""
    lines = [
        f"{NATIVE_DIALOGUE_SLOT_LABEL}{speaker_label}「{line}」{NATIVE_DIALOGUE_SYNC_MARKER}"
        for line in spoken_dialogue
        if line
    ]
    if not lines:
        return visual_prompt
    return "\n".join([visual_prompt, *lines]).strip()


_H3_LANGUAGE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Japanese", re.compile(r"[\u3040-\u30ff]")),
    ("Korean", re.compile(r"[\uac00-\ud7af]")),
    ("Chinese", re.compile(r"[\u3400-\u9fff]")),
)
# The placeholder usually carries sentence punctuation (``说话表演。``).  Consume
# that punctuation with the cue because the reconstructed dialogue already
# contains the user's own punctuation inside ``<d>``.
_H3_DIALOGUE_PLACEHOLDER_PATTERN = re.compile(
    r"(?:说话表演|演唱表演)[ \t]*[。.!！?？；;，,]*"
)
_H3_INTEGRATED_LABEL = "integrated_multimodal_description:"
_H3_SOUNDSCAPE_LABEL = "overall_soundscape:"
_H3_MUSIC_LABEL = "non_diegetic_music:"
_H3_SECTION_LABELS = (
    _H3_INTEGRATED_LABEL,
    _H3_SOUNDSCAPE_LABEL,
    _H3_MUSIC_LABEL,
)
_MINIMAX_H3_MODEL_PATTERN = re.compile(r"minimax[-_ ]?h3", re.IGNORECASE)
_H3_DROP_LINE_PATTERN = re.compile(
    r"^(?:"
    r"帮我(?:生成|写|做)|请(?:生成|写|做)|根据分镜|"
    r"生成(?:一段|一个|一条|这)|输出要求|创作要求|生成要求|"
    r"平台说明|系统说明|导演说明|首帧约束|尾帧约束|"
    r"音频约束|配音要求|对白要求|台词要求|"
    r"拒绝|负面约束|严格继承|综合(?:文本|参考)"
    r")"
)
_H3_META_BRACKET_PATTERN = re.compile(
    r"\["
    r"(?:"
    r"(?:对话)?(?:台词|对白|旁白|配音)(?:与(?:语气|情感))?"
    r"|音效(?:与氛围(?:描述)?)?|声音|音频|时长|duration"
    r"|口型(?:与表情)?(?:要求)?|lip\s*sync"
    r"|负面约束|输出要求|生成要求|首帧约束|尾帧约束|平台说明|系统说明"
    r")"
    r"\s*[:：][^\]]*\]",
    re.IGNORECASE,
)
_H3_META_HEADING_PATTERN = re.compile(
    r"【"
    r"(?:"
    r"台词(?:与表演)?|对白|旁白|配音|"
    r"口型(?:与表情)?(?:要求)?|声音|音频|"
    r"负面约束|输出要求|生成要求|首帧约束|尾帧约束|平台说明|系统说明"
    r")"
    r"】"
)
_H3_META_CLAUSE_PATTERN = re.compile(
    r"(?:"
    r"(?:只|仅)负责?"
    r"(?:(?:倾听|聆听)(?:(?:和|与|及)?(?:反应|回应))?|(?:反应|回应))|"
    r"(?:倾听|聆听)(?:和|与|及)(?:反应|回应)|"
    r"(?:说出|念出|朗读)(?:这|本|上述|以下)?(?:句|组|些)?"
    r"(?:话|台词|对白|词|内容)|"
    r"(?:只|仅)?(?:念|读|说)(?:这|本|上述|以下)?(?:句|段|些)?"
    r"(?:台词|对白|词|内容)|"
    r"(?:不要|不得|禁止|避免|无需|不用|不必|没有|未|不|别)"
    r"(?:再)?(?:开口|张嘴|说话|出声|发声|念白|念台词|说台词|朗读|读台词)|"
    r"(?:不|不要|禁止|避免|无需)(?:添加|加入|生成|使用)(?:旁白|人声|配音)|"
    r"(?:本镜头|本提示词|本段)(?:没有|不含|无需|不要|禁止|不得)|"
    r"^(?:对白|台词|旁白|配音)(?:内容|文本|语气|要求)?(?:\s*[:：].*)?$"
    r")"
)
_H3_META_PHRASE_PATTERN = re.compile(
    r"(?:口型同步|唇形同步|lip\s*sync|说话表演|演唱表演)",
    re.IGNORECASE,
)
_H3_SPEECH_ACTION_PATTERN = re.compile(
    r"(?:(?:张开嘴|张嘴|开口)?(?:说话|讲话|出声|发声)(?:表演)?|(?:开口|张嘴))"
)
_H3_LITERAL_DIALOGUE_PATTERN = re.compile(r"<d>.*?</d>", re.DOTALL)
_H3_CLAUSE_SPLIT_PATTERN = re.compile(r"([，,；;。！？!?]+)")
_H3_PUNCTUATION_ONLY_PATTERN = re.compile(r"^[，,；;。！？!?]+$")

#: H3 收尾禁令：只写一次、放在最末尾。
#: 用户 2026-10-04 拍板的规则是「不要字幕、不要 BGM」，本条保留这条规则本身。
#: 2026-10-04 真机复验证明中文写法 `不要字幕 不要BGM` 挡不住字幕（成片照样把台词
#: 烧进画面），改用外部语料里成片产线实测在用的英文写法（libtv
#: `case_m4_054`：「无字幕补充（视频必须追加）：no subtitles, no captions,
#: no text on screen, no dialogue text, no Chinese text overlay」）。正文已经改成
#: 英文，这条禁令跟着正文语言走，不再中英混写。
H3_AUDIO_TAIL = (
    "No subtitles, no captions, no text on screen, no dialogue text, no Chinese "
    "text overlay appear at any moment in this shot. Dialogue exists only "
    "through speech and lip movement. No background music."
)

# All video providers need the same hard visual rule.  The H3 compiler has a
# longer provider-specific tail; other providers receive this concise form so
# Chinese workflow prose cannot be copied into the frame as subtitles.
VIDEO_NO_TEXT_TAIL = (
    "Clean frame: no subtitles, no captions, no on-screen text, "
    "no dialogue text, no Chinese text overlay, no watermark, no logo. "
    "No background music."
)


def append_video_no_text_tail(prompt: object) -> str:
    """Append the frame-level no-text rule exactly once."""

    text = str(prompt or "").strip()
    if not text:
        return VIDEO_NO_TEXT_TAIL
    lowered = text.casefold()
    if "no subtitles" in lowered and (
        "no text on screen" in lowered or "no on-screen text" in lowered
    ) and "no background music" in lowered:
        return text
    return f"{text}\n{VIDEO_NO_TEXT_TAIL}"

#: 成片阶段才该出现的句子，被优化器写进了画面段。它们不是画面内容，
#: 却是 H3 会照抄成字幕的散文，所以送模型前统一删掉。
_H3_FRAMEWORK_VERSION = "village.h3-shot-document.v2"
_H3_PIPELINE_META_MARKERS = (
    "保持当前可见主体",
    "不新增角色或道具",
    "形成可直接剪辑的明确切点",
    "口型、手势与台词同步",
    "现场声只跟随画面",
    "PROJECT SCRIPT-DERIVED STYLE",
    # 连续接缝的落点锚说明句（见 runners/video.py 的 _append_seam_landing_reference）。
    # 它是对着参考图说的流程话；进了画面段就是一句可念的旁白。2026-10-04 真机
    # 400 的任务日志里，这条句子被框架检查报成 negative_instruction_in_body。
    "只用来确定结束构图",
    "是本镜的落点目标",
)
_H3_MOTION_META_PATTERN = re.compile(
    r"运动在约\s*[0-9.]+\s*秒内沿同一方向延续.*?最后落在(?P<end>[^，。；]+)"
)
#: 风格快照的锁头。它和它后面那段英文正文都是写给流程看的：H3 会把英文
#: 照念成 gibberish，也会把同一段文字画到画面上。风格应当由首帧承载。
_H3_STYLE_BLOCK_MARKERS = (
    "PROJECT SCRIPT-DERIVED STYLE",
    "PROJECT STYLE LOCK",
)
#: 纯拉丁文行：不是画面内容，是流程注入的英文说明。`<d>` 台词行在进入本函数前
#: 已经被占位符保护（整行变成 ``@@H3_DIALOGUE_n@@``），所以这里命中的都是该删的。
_H3_LATIN_PROSE_LINE_RE = re.compile(
    r"^(?![^<]*<d>)(?![^<]*</d>)[^<\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]*$"
)


def _looks_like_h3_latin_prose(line: str) -> bool:
    """A punctuation-shaped or short token is kept; a real English sentence is not."""

    text = str(line or "").strip()
    if not text or "<d>" in text:
        return False
    if not _H3_LATIN_PROSE_LINE_RE.match(text):
        return False
    return len(re.findall(r"[A-Za-z]", text)) >= 3
#: 静默镜头里绝不该出现的“要说话”词。它们会让模型自己编一段人声。
_H3_SPEECH_CUE_WORDS = (
    "说话",
    "开口",
    "口型",
    "台词",
    "对白",
    "旁白",
    "配音",
    "念白",
    "朗读",
    "低语",
    "呢喃",
)
_H3_SPEECH_CUE_WORDS_EN = (
    "speak",
    "speaks",
    "speaking",
    "says",
    "said",
    "voice",
    "voices",
    "lips",
    "lip",
    "mouth",
    "jaw",
    "dialogue",
    "narrat",
    "whisper",
    "murmur",
)
#: 画面里不该出现的中文文字要求：命中就等于点名让模型画字幕。
_H3_ON_SCREEN_TEXT_WORDS = (
    "文字卡",
    "文字显示",
    "显示文字",
    "显示字幕",
    "标题字幕",
    "画面提示文字",
    "画面说明文字",
    "字幕",
    "文字：",
    "文字:",
    "字样",
)
#: 英文正文里的同一族词。正文是英文之后，中文词表换成英文词表继续守这条线。
_H3_ON_SCREEN_TEXT_WORDS_EN = (
    "subtitle",
    "caption",
    "watermark",
    "text overlay",
    "on-screen text",
    "lettering",
    "logo",
)
#: 抽象情绪词：模型只能演身体，念不出“悲伤”。
_H3_EMOTION_LABEL_WORDS = (
    "悲伤",
    "绝望",
    "忧郁",
    "焦虑",
    "惆怅",
    "心碎",
    "欣喜",
    "恐惧地",
    "愤怒地",
    "悲伤地",
    "紧张地",
    "兴奋地",
)
_H3_EMOTION_LABEL_WORDS_EN = (
    "sad",
    "sadness",
    "angry",
    "anger",
    "hopeful",
    "hopeless",
    "desperate",
    "anxious",
    "anxiety",
    "melancholy",
    "fearful",
    "joyful",
    "depressed",
)
#: 否定句在白名单化的画面段里没有任何正向作用，只增加可念文本。
_H3_NEGATIVE_INSTRUCTION_RE = re.compile(
    r"(?:^|[。；;，,])\s*(?:不要|不得|禁止|避免|切勿|no\b|never\b|do not\b)"
)
#: 正文里的时间码：画面段按因果写，不写秒数与区间。数字是最容易被画成字幕的字形。
_H3_TIME_CODE_RE = re.compile(
    r"(?:\d+(?:\.\d+)?\s*[-–~至到]\s*\d+(?:\.\d+)?\s*(?:秒|s\b))"
    r"|(?:\d+(?:\.\d+)?\s*秒\s*[：:])",
    re.IGNORECASE,
)
#: 六段式运动稿的段名残留。编译层正常情况下会先拆段，这里是兜底检查：
#: 只要画面段里还带着这种段名，H3 就有机会把它念出来或画成字幕。
_H3_WORKFLOW_LABEL_RE = re.compile(
    r"\[(?:"
    r"明确的摄影机运镜轨迹与速度|摄影机运镜轨迹与速度|摄影机运镜|运镜轨迹与速度|运镜轨迹|"
    r"主体极其具体的物理动作细节或状态变化|主体极其具体的物理动作细节|主体物理动作细节|"
    r"主体物理动作|物理动作与状态变化|主体动作|物理动作|"
    r"环境物理动态|环境动态|音效与氛围描述|音效与氛围|音效|"
    r"对话台词与语气|对话台词|台词与语气|台词|对白|时长"
    r")\s*[:：]"
)


def _strip_h3_pipeline_meta(value: object) -> str:
    """Drop optimizer-only prose before it can become burned-in captions.

    优化器写给流程看的话（``运动在约4.0秒内沿同一方向延续``、``形成可直接剪辑的
    明确切点``、样式快照的哈希标头）不是画面内容，但 H3 会把它们当台词或字幕
    处理。真机证据：beat 9 烧出的「背向连续完成停步」逐字来自这段散文。
    """

    text = str(value or "")
    if not text:
        return ""
    text = re.sub(r"</?character_state>", "", text)
    text = _strip_h3_style_block(text)
    text = re.sub(
        r"PROJECT SCRIPT-DERIVED STYLE\s*\[[0-9a-fA-F]+\]\s*[:：]",
        "",
        text,
    )
    # I2V 的首帧锚点本身有用，但 ``从输入首帧的…开始`` 是机器说法；留下画面内容。
    text = re.sub(r"从输入首帧的(?P<anchor>[^，。；]+?)开始[，,]?", r"\1，", text)
    text = re.sub(r"(?:输入)?首帧(?:画面)?(?:中|里|内)[，,：:]?", "镜头开始时，", text)
    text = re.sub(r"说话者从首帧的面部状态开始保持连续表演[，,]?", "", text)
    text = re.sub(r"(?:随后|然后|接着)连续完成(?P<action>[^。，；]*)", r"随后\1", text)

    out_lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("PROJECT SCRIPT-DERIVED STYLE"):
            continue
        kept: list[str] = []
        for chunk in re.split(r"(?<=[。！？!?])", line):
            sentence = chunk.strip()
            if not sentence:
                continue
            motion = _H3_MOTION_META_PATTERN.search(sentence)
            if motion:
                end = motion.group("end").strip(" ，,；;")
                if end:
                    kept.append(f"末拍停在{end}。")
                continue
            if "首帧" in sentence:
                continue
            if any(marker in sentence for marker in _H3_PIPELINE_META_MARKERS):
                continue
            kept.append(sentence)
        joined = "".join(kept).strip()
        if joined:
            out_lines.append(joined)
    return "\n".join(out_lines)


def _strip_h3_style_block(value: str) -> str:
    """删掉风格快照的锁头、英文正文和 AVOID 行。"""

    lines = str(value or "").splitlines()
    kept: list[str] = []
    skipping = False
    for line in lines:
        stripped = line.strip()
        marker_at = min(
            (
                stripped.find(marker)
                for marker in _H3_STYLE_BLOCK_MARKERS
                if marker in stripped
            ),
            default=-1,
        )
        if marker_at >= 0:
            # 提示词持久化时会把换行压成空格，锁头常常和正文挤在同一行；
            # 只截断锁头之后的部分，前面的画面内容必须留住。
            head = stripped[:marker_at].strip()
            if head:
                kept.append(head)
            skipping = True
            continue
        if skipping:
            # 样式块一直延伸到下一个自然段结束：英文句子、AVOID 行都算在内。
            if not stripped:
                skipping = False
                continue
            if _looks_like_h3_latin_prose(stripped) or stripped.upper().startswith("AVOID"):
                continue
            skipping = False
        kept.append(line)
    return "\n".join(kept)


def is_minimax_h3_model_identifier(*values: object) -> bool:
    """Return whether any backend/model label identifies a MiniMax H3 family."""

    return any(
        _MINIMAX_H3_MODEL_PATTERN.search(str(value or ""))
        for value in values
    )


def _ensure_h3_audio_tail(prompt: object) -> str:
    """Append the H3 audio tail exactly once, at the very end."""

    text = str(prompt or "").rstrip()
    if not text:
        return H3_AUDIO_TAIL
    if H3_AUDIO_TAIL in text:
        return text
    return f"{text}\n\n{H3_AUDIO_TAIL}"


#: 台词原文在剧本里是带引号的（``"我就知道。"``）。H3 官方规范把双引号定义成
#: 「画面里真的看得见的文字」，引号跟着台词进了 ``<d>`` 槽，模型就把它烧成字幕。
#: 所以台词进槽前先剥掉最外层成对引号，槽里只留语言标签和要念的字。
_H3_DIALOGUE_QUOTE_PAIRS: tuple[tuple[str, str], ...] = (
    ('"', '"'),
    ("'", "'"),
    ("“", "”"),
    ("‘", "’"),
    ("「", "」"),
    ("『", "』"),
)


def _strip_h3_dialogue_quotes(value: object) -> str:
    """Strip the script's wrapping quotation marks from one spoken turn."""

    text = str(value or "").strip()
    stripped = True
    while stripped and len(text) >= 2:
        stripped = False
        for opener, closer in _H3_DIALOGUE_QUOTE_PAIRS:
            if text.startswith(opener) and text.endswith(closer):
                text = text[1:-1].strip()
                stripped = True
                break
    return text


#: 已经编译好的正文里可能残留带引号的 ``<d>`` 槽，收口时一并剥掉。
_H3_QUOTED_DIALOGUE_RE = re.compile(
    r"<d>\s*(?P<language>\[[^\]]*\])\s*(?P<quote>[\"'“”‘’「」『』])"
    r"(?P<spoken>.*?)(?P=quote)\s*</d>",
    re.DOTALL,
)


def _strip_h3_literal_dialogue_quotes(prompt: str) -> str:
    """Strip wrapping quotes from every literal ``<d>`` block in a document."""

    def _replace(match: re.Match[str]) -> str:
        spoken = match.group("spoken").strip()
        if not spoken:
            return match.group(0)
        return f"<d>{match.group('language')} {spoken}</d>"

    return _H3_QUOTED_DIALOGUE_RE.sub(_replace, prompt)


def _finalize_h3_document(prompt: object) -> str:
    """Finish one H3 document: strip dialogue quotes, then append the tail once."""

    return _ensure_h3_audio_tail(
        _strip_h3_literal_dialogue_quotes(str(prompt or ""))
    )


#: 声音层只说“现场有什么声音”，不说“谁在说”。台词只走 ``<d>``。
#: 兜底句刻意做成名词短语：句子越像台词，越容易被念出来。
H3_SOUNDSCAPE_FALLBACK = "环境底噪与物理拟音。"
H3_ENGLISH_SOUNDSCAPE_FALLBACK = (
    "Natural environmental room tone with subtle physical action sounds."
)
_H3_BUILTIN_SOUND_TRANSLATIONS = {
    H3_SOUNDSCAPE_FALLBACK.rstrip("。"): H3_ENGLISH_SOUNDSCAPE_FALLBACK.rstrip("."),
    "雨点落在物体表面的连续雨声": "Continuous rain striking surfaces",
    "风穿过空旷地带的风声": "Wind passing through open terrain",
    "夜间的低频环境底噪": "Low ambient night-time room tone",
    "木结构房屋的轻微结构声": "Subtle creaks from the wooden structure",
    "明火与空气振动的低频声": "Low sounds of flames and vibrating air",
    "脚步与石面摩擦的轻响": "Soft footsteps and friction against stone",
    "树叶摩擦声": "Leaves rustling against one another",
    "衣料与身体的轻微动作声": "Subtle cloth and body movement",
}


def _english_builtin_h3_soundscape(value: str) -> str:
    """Translate only the finite generated vocabulary, retaining sound order.

    Unknown text still needs the translator; this never drops creative input.
    """
    translated = []
    for piece in value.rstrip("。").split("、"):
        english = _H3_BUILTIN_SOUND_TRANSLATIONS.get(piece)
        if english is None:
            return ""
        translated.append(english)
    return "; ".join(translated) + "."


_H3_SOUNDSCAPE_CONTEXT_WORDS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("雨", "雨夜", "暴雨", "屋檐"), "雨点落在物体表面的连续雨声"),
    (("风", "山道", "山巅", "旷野", "悬崖"), "风穿过空旷地带的风声"),
    (("夜", "深夜", "油灯", "烛"), "夜间的低频环境底噪"),
    (("祠堂", "堂屋", "木屋", "屋内", "室内"), "木结构房屋的轻微结构声"),
    (("火", "炉", "燃", "血", "雷", "渡劫"), "明火与空气振动的低频声"),
    (("街", "市", "集"), "远处街市的人声底噪"),
    (("山门", "台阶", "石"), "脚步与石面摩擦的轻响"),
    (("树林", "林", "竹"), "树叶摩擦声"),
)
_H3_SOUNDSCAPE_UNSAFE_WORDS = (
    "人声",
    "台词",
    "对白",
    "说话",
    "旁白",
    "配音",
    "低语",
    "呢喃",
    "吟唱",
    "朗读",
)


def derive_h3_soundscape(beat: object = None) -> str:
    """把现场声写成名词清单，绝不写“谁在说话”。

    画面层已经承载了对白，声音层再出现人声词只会让模型多编一条音轨。
    这里只用 Beat 已有的场景/时间/动作线索推导，不凭空加声音事件。
    """

    if not isinstance(beat, Mapping):
        return H3_SOUNDSCAPE_FALLBACK
    scene_ref = beat.get("scene_ref")
    if not scene_ref and beat.get("scene_ref_json"):
        try:
            parsed = json.loads(str(beat.get("scene_ref_json")))
        except (TypeError, ValueError):
            parsed = None
        scene_ref = parsed if isinstance(parsed, Mapping) else None
    haystack = " ".join(
        str(value or "")
        for value in (
            beat.get("visual_description"),
            beat.get("time_of_day"),
            scene_ref.get("scene_id") if isinstance(scene_ref, Mapping) else "",
            beat.get("narration_segment"),
        )
    )
    picked: list[str] = []
    for keywords, description in _H3_SOUNDSCAPE_CONTEXT_WORDS:
        if any(keyword in haystack for keyword in keywords):
            if description not in picked:
                picked.append(description)
        if len(picked) >= 2:
            break
    picked.append("衣料与身体的轻微动作声")
    line = "、".join(picked) + "。"
    if any(word in line for word in _H3_SOUNDSCAPE_UNSAFE_WORDS):
        return H3_SOUNDSCAPE_FALLBACK
    return line


def _split_h3_audio_sections(value: str) -> tuple[str, str]:
    """Split the speakable body from the fixed audio sections.

    ``overall_soundscape`` / ``non_diegetic_music`` 是官方固定标签，不是画面
    内容。净化只对标签之前的部分生效，避免把标签本身或声音清单删掉。
    """

    text = str(value or "")
    cut: int | None = None
    for label in (_H3_SOUNDSCAPE_LABEL, _H3_MUSIC_LABEL):
        index = text.find(label)
        if index >= 0 and (cut is None or index < cut):
            cut = index
    if cut is None:
        return text, ""
    return text[:cut], text[cut:]


#: 历史提示词里写死的英文声音模板。它是流程说明而非现场声音，且是英文，
#: 送进 H3 只会变成可念文本。
_H3_SOUNDSCAPE_BOILERPLATE_RE = re.compile(
    r"Natural ambient sound and physical action\s+sounds consistent with the visible scene\.?",
    re.IGNORECASE,
)


def _replace_h3_boilerplate_soundscape(text: str, soundscape: str) -> str:
    """Substitute the legacy English soundscape line with a concrete Chinese one."""

    if not _H3_SOUNDSCAPE_BOILERPLATE_RE.search(text):
        return text
    return _H3_SOUNDSCAPE_BOILERPLATE_RE.sub(soundscape.strip(), text)


#: 脚本合同写进 `video_motion_prompt` 的六段式段名（见
#: ``freezone/script_contract.py`` 的 ``MOTION_SEGMENT_ORDER``）。这套六段结构是
#: 内部可审计合同，不是 H3 的正文：段名是给流程看的，H3 会把它们当剧本念出来或
#: 画成字幕（真机证据：beat_09 烧出的「背向连续完成停步」就是这么来的）。
#: 编译到官方三章节之前必须先拆段、去段名，并把声音/台词/时长分别归位。
_WORKFLOW_MOTION_LABELS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "camera",
        (
            "明确的摄影机运镜轨迹与速度",
            "摄影机运镜轨迹与速度",
            "摄影机运镜",
            "运镜轨迹与速度",
            "运镜轨迹",
            "运镜",
        ),
    ),
    (
        "subject_action",
        (
            "主体极其具体的物理动作细节或状态变化",
            "主体极其具体的物理动作细节",
            "主体物理动作细节",
            "主体物理动作",
            "物理动作与状态变化",
            "主体动作",
            "物理动作",
        ),
    ),
    ("environment_motion", ("环境物理动态", "环境动态")),
    ("sound", ("音效与氛围描述", "音效与氛围", "音效")),
    ("dialogue", ("对话台词与语气", "对话台词", "台词与语气", "台词", "对白")),
    ("duration", ("时长",)),
)

#: 画面上不该出现、声音里也不该出现的词；声音层复用它做二次过滤。
_WORKFLOW_SOUND_NEGATION_WORDS = (
    "不要",
    "禁止",
    "避免",
    "无音乐",
    "没有音乐",
    "no music",
    "no bgm",
)

_WORKFLOW_LABEL_SEPARATOR_RE = re.compile(r"[:：]")


def _workflow_bracket_spans(text: str) -> Iterable[tuple[int, int, str]]:
    """Read complete outer slots without clipping nested or detailed content."""
    depth = 0
    start = 0
    for index, character in enumerate(text):
        if character == "[":
            if depth == 0:
                start = index
            depth += 1
        elif character == "]" and depth:
            depth -= 1
            if depth == 0:
                yield start, index + 1, text[start + 1:index]


def _workflow_motion_role(label: object) -> str:
    """Return the segment role for one bracketed slot name, if it is a known one."""

    cleaned = re.sub(r"\s+", "", str(label or ""))
    if not cleaned:
        return ""
    best_role = ""
    best_length = 0
    for role, names in _WORKFLOW_MOTION_LABELS:
        for name in names:
            if cleaned.startswith(name) and len(name) > best_length:
                best_role = role
                best_length = len(name)
    return best_role


def split_workflow_motion_prompt(value: object) -> dict[str, str] | None:
    """Split the script contract's six-segment motion prompt into labelled parts.

    Returns ``None`` when the text is not that structure (an ordinary free-form
    prompt, an already-compiled official document, or a shot prompt), so the
    existing compile path is untouched for every other caller.
    """

    text = str(value or "")
    if not text:
        return None
    found: dict[str, str] = {}
    for _, _, body in _workflow_bracket_spans(text):
        parts = _WORKFLOW_LABEL_SEPARATOR_RE.split(body, maxsplit=1)
        if len(parts) != 2:
            continue
        role = _workflow_motion_role(parts[0])
        content = parts[1].strip()
        if not role or not content or role in found:
            continue
        found[role] = content
    if len(found) < 2:
        return None
    return found


def workflow_motion_soundscape(segments: Mapping[str, str]) -> str:
    """Turn the six-segment sound slot into a noun list for ``overall_soundscape``.

    声音层只说现场有什么声音。人工声词（说话/台词/旁白）、否定句和音乐项一律
    不进这一层：它们要么让模型多编一条人声，要么和收尾的 ``不要BGM`` 打架。
    """

    raw = str(segments.get("sound") or "").strip()
    if not raw:
        return ""
    kept: list[str] = []
    for token in re.split(r"[、，,；;。]", raw):
        piece = token.strip()
        if not piece:
            continue
        lowered = piece.casefold()
        if any(word.casefold() in lowered for word in _H3_SOUNDSCAPE_UNSAFE_WORDS):
            continue
        if any(word.casefold() in lowered for word in _WORKFLOW_SOUND_NEGATION_WORDS):
            continue
        if piece.casefold() in {"无", "none", "n/a", "无音乐", "无音效"}:
            continue
        kept.append(piece)
    if not kept:
        return ""
    return "、".join(kept) + "。"


def _rewrite_workflow_motion_brackets(value: str) -> str:
    """Strip the six-segment slot names from any shot text, keeping the content.

    The visual slots become plain prose. The sound, dialogue and duration slots
    leave the picture layer entirely — they are routed to ``overall_soundscape``,
    the ``<d>`` dialogue tags, and the API duration parameter respectively.
    """

    def _replace(body: str, original: str) -> str:
        parts = _WORKFLOW_LABEL_SEPARATOR_RE.split(body, maxsplit=1)
        if len(parts) != 2:
            return original
        role = _workflow_motion_role(parts[0])
        if not role:
            return original
        if role in {"sound", "dialogue", "duration"}:
            return " "
        content = parts[1].strip(" ，,；;。")
        return f"{content}。" if content else ""

    text = str(value or "")
    pieces: list[str] = []
    previous = 0
    for start, end, body in _workflow_bracket_spans(text):
        pieces.extend((text[previous:start], _replace(body, text[start:end])))
        previous = end
    pieces.append(text[previous:])
    return "".join(pieces)


def _h3_language_tag(text: str) -> str:
    """Return the language tag needed by the official H3 ``<d>`` syntax."""

    for label, pattern in _H3_LANGUAGE_PATTERNS:
        if pattern.search(text):
            return label
    return "English"


#: 内部身份 id（``小臣_少年时期``）不是给模型看的名字：下划线会被画进画面，
#: 双引号又会被 H3 当成画内可见文字。只有像展示名（无 ASCII 字母、数字、下划线）
#: 的说话人才写进正文，其余退回泛称。
_H3_INTERNAL_ID_CHARS_RE = re.compile(r"[A-Za-z0-9_]")


def _h3_speaker_identity(speaker: object) -> str:
    """Name the speaking subject for H3 without leaking an internal identity id."""

    name = str(speaker or "").strip()
    if name and not _H3_INTERNAL_ID_CHARS_RE.search(name):
        return f"The on-screen character {name}"
    return "The on-screen speaker"


def _h3_dialogue_clause(
    text: str,
    *,
    speaker: str,
    sung: bool,
) -> str:
    """Build one official-H3 spoken/lyric clause without translating the words."""

    identity = _h3_speaker_identity(speaker)
    verb = "sings" if sung else "says"
    return f"{identity} (S1), with a natural voice, {verb}: <d>[{_h3_language_tag(text)}] {text}</d>"


def _h3_visual_unit_count(value: str) -> int:
    return len(re.findall(r"[\u3400-\u9fff]|[A-Za-z]+", str(value or "")))


def _h3_structured_prompt_needs_sanitization(value: object) -> bool:
    """Return whether a structured H3 prompt still carries speakable metadata."""

    text = _H3_LITERAL_DIALOGUE_PATTERN.sub(" ", str(value or ""))
    if _H3_META_BRACKET_PATTERN.search(text):
        return True
    if _H3_META_HEADING_PATTERN.search(text):
        return True
    if _H3_META_PHRASE_PATTERN.search(text) or _H3_SPEECH_ACTION_PATTERN.search(text):
        return True
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line and _H3_DROP_LINE_PATTERN.match(line):
            return True
    return any(
        _H3_META_CLAUSE_PATTERN.search(segment)
        for segment in _H3_CLAUSE_SPLIT_PATTERN.split(text)
        if segment
    )


def _clean_h3_visual_clause(value: str, *, speech_authorized: bool) -> str:
    """Keep visible staging while removing text that H3 reads as spoken copy."""

    clause = str(value or "").strip()
    if not clause:
        return ""
    if clause in {"说话表演", "演唱表演"}:
        return clause
    clause = _H3_META_HEADING_PATTERN.sub("", clause).strip(" ，,；;：:【】")
    clause = _H3_META_BRACKET_PATTERN.sub("", clause).strip(" +，,；;：:")
    if not clause:
        return ""
    if _H3_META_CLAUSE_PATTERN.search(clause):
        reduced = _H3_META_CLAUSE_PATTERN.sub("", clause).strip(" ，,；;：:")
        if _h3_visual_unit_count(reduced) < 7:
            return ""
        clause = reduced
    clause = _H3_META_PHRASE_PATTERN.sub("", clause).strip()
    if not clause:
        return ""
    if _H3_SPEECH_ACTION_PATTERN.search(clause):
        if speech_authorized:
            clause = _H3_SPEECH_ACTION_PATTERN.sub("唇部开合", clause)
        else:
            reduced = _H3_SPEECH_ACTION_PATTERN.sub("", clause)
            if _h3_visual_unit_count(reduced) < 4:
                return ""
            clause = reduced
    clause = _H3_META_PHRASE_PATTERN.sub("", clause).strip()
    clause = re.sub(r"(?:嘴唇|唇部)自然?唇部开合", "唇部自然开合", clause)
    clause = re.sub(r"(?:\s*\+\s*)+", " + ", clause)
    clause = re.sub(r"\s{2,}", " ", clause).strip(" ，,；;：:")
    return clause


def _sanitize_h3_visual_direction(value: object, *, speech_authorized: bool) -> str:
    """Remove director-only copy before it can become H3's unscripted audio.

    H3 treats every text block as performable screenplay, so a visual prompt
    that merely says "do not speak" or "say this line" can itself be read
    aloud.  This sanitizer operates only outside literal ``<d>`` dialogue and
    keeps visible staging.  Structured spoken turns are always inserted after
    this pass.
    """

    text = str(value or "").strip()
    if not text:
        return ""
    # 官方声音段是固定标签 + 声音清单，不参与画面净化，避免被当成散文删掉。
    head, sections = _split_h3_audio_sections(text)
    text = head.strip()
    if not text:
        return sections.strip()

    protected_dialogue: list[str] = []

    def _protect(match: re.Match[str]) -> str:
        protected_dialogue.append(match.group(0))
        return f"@@H3_DIALOGUE_{len(protected_dialogue) - 1}@@"

    text = _H3_LITERAL_DIALOGUE_PATTERN.sub(_protect, text)
    text = _strip_h3_pipeline_meta(text)
    # 六段式运动稿是内部合同：段名不进画面层，声音/台词/时长三段整段移出画面层。
    text = _rewrite_workflow_motion_brackets(text)
    cleaned_lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or _H3_DROP_LINE_PATTERN.match(line):
            continue
        segments = _H3_CLAUSE_SPLIT_PATTERN.split(line)
        kept: list[str] = []
        drop_next_punctuation = False
        for segment in segments:
            if not segment:
                continue
            if _H3_PUNCTUATION_ONLY_PATTERN.fullmatch(segment):
                if not drop_next_punctuation:
                    kept.append(segment)
                drop_next_punctuation = False
                continue
            cleaned = _clean_h3_visual_clause(
                segment,
                speech_authorized=speech_authorized,
            )
            if cleaned:
                kept.append(cleaned)
                drop_next_punctuation = False
            else:
                drop_next_punctuation = True
        rendered = "".join(kept).strip()
        rendered = re.sub(r"([，,；;。！？!?])[\s，,；;。！？!?]+", r"\1", rendered)
        rendered = rendered.strip(" ，,；;")
        if rendered:
            cleaned_lines.append(rendered)

    cleaned = "\n".join(cleaned_lines)
    for index, dialogue in enumerate(protected_dialogue):
        cleaned = cleaned.replace(f"@@H3_DIALOGUE_{index}@@", dialogue)
    if sections:
        return f"{cleaned.strip()}\n\n{sections.strip()}".strip()
    return cleaned.strip()


_H3_AVOID_LINE_RE = re.compile(r"^[ \t]*AVOID\b[^\n]*$", re.MULTILINE)
_H3_SHOT_ONE_PREFIX_RE = re.compile(r"^\[Shot\s+1\]", re.IGNORECASE)


def _sanitize_h3_english_body(value: object) -> str:
    """Clean an already-English H3 body without deleting it.

    中文净化器把整行英文当流程说明删掉，那是为中文正文设计的。编译成英文的
    正文（见 ``generators/video/h3_body_translation.py``）不能再走那条路，
    否则整段画面描述会被清空。这里只做三件事：保护 ``<d>`` 台词、去掉风格锁
    与 ``AVOID`` 行、补齐官方要求的 ``[Shot 1]`` 开头。
    """

    text = str(value or "").strip()
    if not text:
        return ""
    protected: list[str] = []

    def _protect(match: re.Match[str]) -> str:
        protected.append(match.group(0))
        return f"@@H3_DIALOGUE_{len(protected) - 1}@@"

    body = _H3_LITERAL_DIALOGUE_PATTERN.sub(_protect, text)
    body = _strip_h3_style_block(body)
    body = _H3_AVOID_LINE_RE.sub("", body)
    body = re.sub(r"[ \t]{2,}", " ", body)
    body = "\n".join(line.strip() for line in body.splitlines() if line.strip())
    for index, dialogue in enumerate(protected):
        body = body.replace(f"@@H3_DIALOGUE_{index}@@", dialogue)
    body = body.strip()
    if not body:
        return ""
    if not _H3_SHOT_ONE_PREFIX_RE.match(body):
        body = f"[Shot 1] {body}"
    return body


def _normalize_h3_turns(spoken_dialogue: Iterable[str] | str | None) -> tuple[str, ...]:
    """Normalize the spoken turns the same way for every H3 compile path."""

    if spoken_dialogue is None:
        return ()
    if isinstance(spoken_dialogue, str):
        spoken = _strip_h3_dialogue_quotes(spoken_dialogue)
        return (spoken,) if spoken else ()
    return tuple(
        spoken
        for spoken in (_strip_h3_dialogue_quotes(value) for value in spoken_dialogue)
        if spoken
    )


def resolve_h3_soundscape(
    visual_prompt: object,
    *,
    beat: Mapping[str, Any] | None = None,
    soundscape: object = "",
) -> str:
    """Choose the ``overall_soundscape`` line without touching the picture body.

    工作流那条链路把现场声写在六段式运动稿的第 4 段；那份稿子的段名不能进
    画面段，但声音本身必须保留。优先级：调用方显式传入 > 运动稿声音段 >
    从 Beat 场景推导的兜底清单。
    """

    explicit = str(soundscape or "").strip()
    if explicit:
        return explicit
    segments = split_workflow_motion_prompt(visual_prompt)
    if segments:
        from_segments = workflow_motion_soundscape(segments)
        if from_segments:
            return from_segments
    return derive_h3_soundscape(beat)


def build_minimax_h3_provider_prompt(
    visual_prompt: object,
    spoken_dialogue: Iterable[str] | str = (),
    *,
    speaker: str = "",
    sung: bool = False,
    beat: Mapping[str, Any] | None = None,
    soundscape: object = "",
    body_language: str = "zh",
) -> str:
    """Build the official structured prompt used by the MiniMax H3 v2 endpoint.

    The upstream prompt contract is intentionally narrow: the three section
    names and their order are fixed, while dialogue and lyrics must stay in
    their original language inside ``<d>...</d>``.  Platform instructions and
    "do not read this" warnings are deliberately not appended.  The only
    closing line is the single no-text/no-BGM tail ``H3_AUDIO_TAIL``.
    画面里不该出现的流程散文在送入前就被清掉，不靠尾部禁令兜底。

    ``body_language`` 决定画面段走哪套净化：``zh`` 是创作层的中文运动稿，
    先拆六段、去流程说明；``en`` 是已经按 H3 方言编译好的英文正文，只做
    去引号式的最小收口。官方规范要求正文英文、台词留原语言，所以英文正文
    才是送模型的常态，中文只是翻译不可用时的退路。
    """

    normalized_language = str(body_language or "zh").strip().casefold()
    english_body = normalized_language.startswith("en")

    turns = _normalize_h3_turns(spoken_dialogue)

    body = str(visual_prompt or "").strip()
    # 工作流那条链路交给编译层的是脚本合同的六段式运动稿。它是可审计的内部合同，
    # 不是 H3 正文：先拆段，把第 4 段（音效）抬成声音层，第 1/2/3 段去段名后
    # 成为画面段，第 5/6 段分别走 <d> 与 API 参数。没有可用的声音层时才回落到
    # 从 Beat 场景推导的中文名词清单。
    sound = resolve_h3_soundscape(body, beat=beat, soundscape=soundscape)
    if all(label in body for label in _H3_SECTION_LABELS):
        body = _replace_h3_boilerplate_soundscape(body, sound)
        if not english_body and _h3_structured_prompt_needs_sanitization(body):
            has_literal_dialogue = bool(_H3_LITERAL_DIALOGUE_PATTERN.search(body))
            return _finalize_h3_document(
                _sanitize_h3_visual_direction(
                    body,
                    speech_authorized=bool(turns) or has_literal_dialogue,
                )
            )
        return _finalize_h3_document(body)
    is_sung = bool(sung) or "演唱表演" in body
    if english_body:
        body = _sanitize_h3_english_body(body)
    else:
        body = _sanitize_h3_visual_direction(body, speech_authorized=bool(turns))

    if not body:
        body = "[Shot 1] A continuous cinematic audiovisual scene."
    elif not re.match(r"^\[Shot\s+1\]", body, re.IGNORECASE):
        body = f"[Shot 1] {body}"

    if turns:
        clauses = iter(
            _h3_dialogue_clause(item, speaker=speaker, sung=is_sung)
            for item in turns
        )

        def _replace_turn(match: re.Match[str]) -> str:
            try:
                clause = next(clauses)
            except StopIteration:
                return ""
            prefix = "" if match.start() == 0 or body[match.start() - 1].isspace() else " "
            return f"{prefix}{clause}"

        body = _H3_DIALOGUE_PLACEHOLDER_PATTERN.sub(_replace_turn, body)
        remaining = list(clauses)
        if remaining:
            body = f"{body.rstrip()} {' '.join(remaining)}"
    else:
        # A normalized visual-only request must not carry a spoken cue into
        # H3's multimodal body.  The model otherwise fills the gap with an
        # invented line even when the user supplied no dialogue.
        body = _H3_DIALOGUE_PLACEHOLDER_PATTERN.sub("", body)
        body = re.sub(r"\s{2,}", " ", body).strip(" ，,；;")

    return _finalize_h3_document(
        "\n\n".join(
            (
                f"{_H3_INTEGRATED_LABEL} {body}",
                f"{_H3_SOUNDSCAPE_LABEL} {sound}",
                f"{_H3_MUSIC_LABEL} N/A",
            )
        )
    )


async def compile_h3_provider_prompt(
    visual_prompt: object,
    spoken_dialogue: Iterable[str] | str = (),
    *,
    speaker: str = "",
    sung: bool = False,
    beat: Mapping[str, Any] | None = None,
    soundscape: object = "",
    on_log: Callable[[str], None] | None = None,
) -> str:
    """Compile one shot into the final document that goes to MiniMax H3.

    创作层写的是中文运动稿，H3 要的是英文正文 + ``<d>[Chinese] 逐字台词</d>``。
    这里把两件事串起来：先按中文规则把流程散文、风格锁、六段式段名清掉，再把
    干净的中文正文交给方言层译成英文，最后拼官方三章节。

    英文正文编译失败时停止新提交，避免违反用户的无字幕约束。
    已受理任务的恢复不经过本编译入口。
    """

    raw_body = str(visual_prompt or "").strip()
    turns = _normalize_h3_turns(spoken_dialogue)
    chinese_soundscape = resolve_h3_soundscape(
        raw_body,
        beat=beat,
        soundscape=soundscape,
    )
    cleaned_body = (
        _sanitize_h3_english_body(raw_body)
        if _h3_body_is_english(raw_body)
        else sanitize_h3_visual_prompt(raw_body, speech_authorized=bool(turns))
    ).strip()
    if not cleaned_body:
        cleaned_body = raw_body

    # An already-English body must stay a deterministic fast path.  The default
    # soundscape is intentionally Chinese because it is meant for the
    # translation call used by Chinese workflow prose.  Passing that default
    # into the translator alongside an English body used to turn a no-op into a
    # paid text-model dependency; when the text model was unavailable the whole
    # English body fell back to Chinese and reintroduced subtitle risk.  Keep
    # the sound layer safe in English without changing the visual description.
    english_body_input = not re.search(r"[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]", cleaned_body)
    explicit_soundscape = str(soundscape or "").strip()
    workflow_soundscape = workflow_motion_soundscape(
        split_workflow_motion_prompt(raw_body) or {}
    )
    translation_soundscape = (
        _english_builtin_h3_soundscape(chinese_soundscape) or chinese_soundscape
        if english_body_input and not explicit_soundscape and not workflow_soundscape
        else chinese_soundscape
    )

    compiled = None
    try:
        from novelvideo.generators.video.h3_body_translation import (
            translate_h3_body_to_english,
        )

        compiled = await translate_h3_body_to_english(
            cleaned_body,
            has_dialogue=bool(turns),
            soundscape=translation_soundscape,
        )
    except Exception as exc:  # noqa: BLE001 - report a bounded compiler failure
        if on_log:
            on_log(f"H3 英文正文编译异常: {type(exc).__name__}")

    if compiled is None:
        if on_log:
            on_log("H3 英文正文不可用，本次阻止提交，避免中文正文生成字幕或旁白")
        raise ValueError(
            "H3 英文正文编译失败，已阻止提交：请检查文字模型配置后重试"
        )

    provider_soundscape = (
        compiled.soundscape
        or _english_builtin_h3_soundscape(chinese_soundscape)
        or chinese_soundscape
    )
    if re.search(r"[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]", provider_soundscape):
        raise ValueError(
            "H3 声音段英文编译失败，已阻止提交：请检查文字模型配置后重试"
        )
    if on_log:
        on_log("H3 正文已按官方方言编译为英文，台词保留原语言")
    return build_minimax_h3_provider_prompt(
        compiled.description,
        turns,
        speaker=speaker,
        sung=sung,
        beat=beat,
        soundscape=provider_soundscape,
        body_language="en",
    )


async def compile_h3_picture_prompt(
    visual_prompt: object,
    *,
    speech_authorized: bool = False,
    on_log: Callable[[str], None] | None = None,
) -> str:
    """Compile the picture body alone, for shots whose native audio is off.

    关掉原生音频只是不要模型生成人声，画面上的中文散文照样会被烧成字幕。
    这条路径不拼官方三章节（没有台词槽，也就不该给模型一份带人声段落的
    文档），只做两件事：按中文规则净化，再按官方方言译成英文。
    """

    raw_body = str(visual_prompt or "").strip()
    cleaned = (
        _sanitize_h3_english_body(raw_body)
        if _h3_body_is_english(raw_body)
        else sanitize_h3_visual_prompt(raw_body, speech_authorized=bool(speech_authorized))
    ).strip()
    if not cleaned:
        cleaned = raw_body
    if not cleaned:
        return ""
    compiled = None
    try:
        from novelvideo.generators.video.h3_body_translation import (
            translate_h3_body_to_english,
        )

        compiled = await translate_h3_body_to_english(
            cleaned,
            has_dialogue=bool(speech_authorized),
        )
    except Exception as exc:  # noqa: BLE001 - report a bounded compiler failure
        if on_log:
            on_log(f"H3 画面段英文编译异常: {type(exc).__name__}")
    if compiled is None:
        if on_log:
            on_log("H3 画面段英文不可用，本次阻止提交，避免中文正文生成字幕")
        raise ValueError(
            "H3 画面段英文编译失败，已阻止提交：请检查文字模型配置后重试"
        )
    return compiled.description


def sanitize_h3_visual_prompt(value: object, *, speech_authorized: bool = True) -> str:
    """Clean a picture-only H3 prompt without wrapping the official three sections.

    关掉原生音频的镜头（外部配音、模型不带原生音频、用户手动关）不走官方三章节
    构建，但画面段仍然必须按框架净化：六段式运动稿的段名、优化器的流程说明、
    风格锁都在可念文本里，留在正文里一样会被烧成字幕。``speech_authorized``
    决定要不要保留「说话表演」这类的口型提示——有授权台词时保留（外部配音仍要
    对上口型），静默镜头则删掉。
    """

    return _sanitize_h3_visual_direction(
        value,
        speech_authorized=bool(speech_authorized),
    ).strip()


@dataclass(frozen=True, slots=True)
class H3FrameworkIssue:
    """One machine-checkable violation of the shot-document framework."""

    code: str
    message: str


# These findings mean the provider request is structurally unsafe: the model
# was explicitly asked to draw text, or a silent shot was asked to speak. They
# are different from advisory style findings, which can still be useful to a
# human reviewer without making a paid request impossible.
H3_BLOCKING_ISSUE_CODES = frozenset(
    {
        "empty_prompt",
        "section_missing",
        "section_order",
        "audio_tail_missing",
        "on_screen_text_requested",
        "speech_words_in_silent_shot",
        "dialogue_repeated_in_body",
        "pipeline_meta_in_body",
        "style_block_in_body",
        "workflow_label_in_body",
    }
)


def blocking_h3_prompt_issues(
    issues: tuple[H3FrameworkIssue, ...] | list[H3FrameworkIssue],
) -> tuple[H3FrameworkIssue, ...]:
    """Return only findings that must stop a provider request."""

    return tuple(issue for issue in issues if issue.code in H3_BLOCKING_ISSUE_CODES)


def _h3_body_of(prompt: str) -> str:
    """Return the speakable body, i.e. everything before the audio sections."""

    head, _ = _split_h3_audio_sections(str(prompt or ""))
    return head


#: 画面段里出现任何一个中日韩字符，就说明它还是中文创作稿而不是英文正文。
_H3_CJK_BODY_RE = re.compile(r"[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]")


def _h3_body_is_english(body: str) -> bool:
    """Return whether the picture body is the compiled English document."""

    stripped = str(body or "").strip()
    if not stripped:
        return False
    return not _H3_CJK_BODY_RE.search(stripped)


def lint_h3_provider_prompt(
    prompt: object,
    *,
    audio_type: object = "",
    dialogue_text: object = "",
) -> tuple[H3FrameworkIssue, ...]:
    """Check one final H3 prompt against the shot-document framework.

    这份检查只看送进模型的最终文档，和优化器的散文质检（``lint_motion_prompt``）
    分工不同：那边管“动作写得够不够”，这里管“文档里有没有会被念出来、
    画出来的文字”。两条都不通过也不阻止生成，先把问题讲清楚。
    """

    text = str(prompt or "")
    issues: list[H3FrameworkIssue] = []
    if not text.strip():
        return (H3FrameworkIssue("empty_prompt", "最终提示词为空。"),)

    positions = [
        text.find(label)
        for label in (_H3_INTEGRATED_LABEL, _H3_SOUNDSCAPE_LABEL, _H3_MUSIC_LABEL)
    ]
    if any(index < 0 for index in positions):
        issues.append(
            H3FrameworkIssue("section_missing", "官方三章节不完整，模型会猜结构。")
        )
    elif positions != sorted(positions):
        issues.append(
            H3FrameworkIssue("section_order", "三章节顺序不是官方约定的画面→环境声→配乐。")
        )
    if not text.rstrip().endswith(H3_AUDIO_TAIL):
        issues.append(
            H3FrameworkIssue("audio_tail_missing", f"收尾缺少「{H3_AUDIO_TAIL}」。")
        )

    body = _h3_body_of(text)
    body_without_dialogue = _H3_LITERAL_DIALOGUE_PATTERN.sub(" ", body)
    # 官方正文是英文，中文只是翻译不可用时的退路。两条线要守的规矩不一样：
    # 中文正文里出现整行英文是流程说明漏了；英文正文里整段都是英文才是对的。
    english_body = _h3_body_is_english(body_without_dialogue)

    if any(marker in body_without_dialogue for marker in _H3_PIPELINE_META_MARKERS):
        issues.append(
            H3FrameworkIssue(
                "pipeline_meta_in_body",
                "画面段里混进了流程说明，会被念出来或画成字幕。",
            )
        )
    if any(marker in body_without_dialogue for marker in _H3_STYLE_BLOCK_MARKERS):
        issues.append(
            H3FrameworkIssue("style_block_in_body", "画面段里混进了风格锁说明。")
        )
    if not english_body and any(
        _looks_like_h3_latin_prose(line)
        for line in body_without_dialogue.splitlines()
    ):
        issues.append(
            H3FrameworkIssue(
                "latin_prose_in_body",
                "画面段里有整行英文，H3 会照读成乱语。",
            )
        )
    if _H3_NEGATIVE_INSTRUCTION_RE.search(body_without_dialogue):
        issues.append(
            H3FrameworkIssue(
                "negative_instruction_in_body",
                "画面段里出现否定句，只会增加可念文本。",
            )
        )
    on_screen_words = (
        _H3_ON_SCREEN_TEXT_WORDS + _H3_ON_SCREEN_TEXT_WORDS_EN
        if english_body
        else _H3_ON_SCREEN_TEXT_WORDS
    )
    lowered_body = body_without_dialogue.casefold()
    if any(
        word.casefold() in lowered_body
        for word in on_screen_words
    ):
        issues.append(
            H3FrameworkIssue(
                "on_screen_text_requested",
                "画面段点名要画中文，成片会直接出现字幕。",
            )
        )
    emotion_words = (
        _H3_EMOTION_LABEL_WORDS + _H3_EMOTION_LABEL_WORDS_EN
        if english_body
        else _H3_EMOTION_LABEL_WORDS
    )
    if any(
        word.casefold() in lowered_body
        for word in emotion_words
    ):
        issues.append(
            H3FrameworkIssue(
                "emotion_label_in_body",
                "画面段用了抽象情绪词，模型只能靠身体演，看不到这个词。",
            )
        )
    if _H3_TIME_CODE_RE.search(body_without_dialogue):
        issues.append(
            H3FrameworkIssue(
                "time_code_in_body",
                "画面段出现时间码，数字是最容易被画成字幕的字形。",
            )
        )
    if _H3_WORKFLOW_LABEL_RE.search(body_without_dialogue):
        issues.append(
            H3FrameworkIssue(
                "workflow_label_in_body",
                "画面段还留着六段式运动稿的段名，H3 会把段名当正文念出来或画成字幕。",
            )
        )

    spoken = tuple(
        str(item or "").strip()
        for item in _as_turn_tuple(dialogue_text)
        if str(item or "").strip()
    )
    is_dialogue = bool(spoken) or str(audio_type or "").strip().casefold() == "dialogue"
    speech_words = (
        _H3_SPEECH_CUE_WORDS + _H3_SPEECH_CUE_WORDS_EN
        if english_body
        else _H3_SPEECH_CUE_WORDS
    )
    if not is_dialogue and any(
        word.casefold() in lowered_body for word in speech_words
    ):
        issues.append(
            H3FrameworkIssue(
                "speech_words_in_silent_shot",
                "静默镜头的画面段出现说话/口型/台词字样，模型会自己编人声。",
            )
        )
    for line in spoken:
        if line and line in body_without_dialogue:
            issues.append(
                H3FrameworkIssue(
                    "dialogue_repeated_in_body",
                    "台词原文同时出现在画面段和 <d> 槽位里。",
                )
            )
            break
    return tuple(issues)


def _normalize_spoken_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _as_turn_tuple(value: object) -> tuple[object, ...]:
    """Treat a scalar and a sequence of turns the same way."""

    if isinstance(value, (list, tuple)):
        return tuple(value)
    return (value,)


def _iter_structured_spoken(value: object) -> tuple[str, ...]:
    """Yield the spoken turns a structured field carries, in order."""

    if isinstance(value, (list, tuple)):
        items: tuple[object, ...] = tuple(value)
    else:
        items = (value,)
    turns: list[str] = []
    for item in items:
        structured = str(item or "").strip()
        if not structured:
            continue
        turns.extend(_quoted_dialogue_parts(structured) or (structured,))
    return tuple(turns)


def extract_spoken_dialogue(
    prompt: object = "",
    *,
    dialogue_text: object = "",
    restore: object = (),
    spoken_dialogue: object = (),
    allow_structured_fallback: bool = True,
) -> tuple[str, ...]:
    """Return structured speech while leaving ordinary visual prose untouched.

    Quoted text in a visual prompt is an explicit spoken turn.  A value supplied
    through the dedicated ``dialogue_text`` field is already structured and may
    therefore be unquoted; this is the only raw-text exception.

    台词**保留重复**：`女孩重复：“我们走吧。”` 是两次真实发声，去重等于删台词；
    TapCanvas 的台词合同明文禁止「删改台词、拆碎一行」。

    ``restore`` 是第一次规范化时被剥下来的 `对白：` 槽位内容，位置紧跟提示词自己的
    轮次，所以同一条提示词走两遍（REST 路由一次、job 一次）结果不变。

    **权威顺序**：提示词自己的台词 → 恢复回来的槽位 → 结构化字段兜底。结构化字段
    （`dialogue_text` / `spoken_dialogue`）只在提示词一个轮次都抽不到时才用——它们的
    契约是「独立对白文本，不进入视觉 prompt」（`api/schemas.py`），是外部配音与字幕
    的载荷；让一个可能过期的字段去覆盖提示词里写明的台词，就是台词说错的老路。

    ``allow_structured_fallback=False`` 用在提示词**带过**台词标记、但抽不出轮次的时候
    （典型是 `路牌写着“欢迎回家”`——引号属于画面文字，不是台词）。那种提示词没有原生
    对白，真去兜底只会把一份过期字段提升成台词，让角色念出招牌。
    """

    # 一旦提示词里已经有本函数产出的槽位，**槽位就是权威**：它是上一轮提取的结果，
    # 而这一轮的散文里台词早已被换成「说话表演」，再抽一遍只会把表演提示读回成台词。
    # 这条不变式让「路由一次 + job 一次」的二次规范化严格幂等。
    # 恢复回来的槽位原样使用、**不去重**：重复台词在那里是合法的（同一句说两遍）。
    restored_turns = tuple(
        text
        for text in (_normalize_spoken_text(item) for item in _as_turn_tuple(restore))
        if text
    )
    if restored_turns:
        return restored_turns

    prompt_turns = [
        text
        for text in (
            _normalize_spoken_text(item) for item in _prompt_spoken_dialogue_parts(prompt)
        )
        if text
    ]
    if prompt_turns:
        return tuple(prompt_turns)
    if not allow_structured_fallback:
        return ()

    # ``dialogue_text`` is the joined projection of ``spoken_dialogue``.
    # Prefer the turn-preserving field; using both would turn three turns into
    # three turns plus one joined duplicate on the route -> job pass.
    structured_values = _iter_structured_spoken(spoken_dialogue)
    if not structured_values:
        structured_values = _iter_structured_spoken(dialogue_text)
    fallback: list[str] = []
    for value in structured_values:
        normalized = _normalize_spoken_text(value)
        if normalized:
            # Repeated lines are real repeated utterances.  Deduplicating here
            # silently deletes the second delivery after the route -> job pass.
            fallback.append(normalized)
    return tuple(fallback)


def required_native_audio_without_dialogue(
    *,
    prompt: object = "",
    dialogue_text: object = "",
    spoken_dialogue: object = (),
    audio_type: object = "",
    native_audio: object = "",
) -> bool:
    """真机护栏：模型必须原生出声、请求也声明要说话，却没有任何可用台词。

    此时不能让模型自己编——MiniMax H3 的原生音频关不掉，提示词里的散文对它就是
    可以念的文本，没有台词就会把画面描述当台词念出来（2026-10-01 真机）。
    返回 True 表示调用方应拦下并要求用户补一句台词或改为不说话。
    """

    if str(native_audio or "").strip().casefold() != "required":
        return False
    if str(audio_type or "").strip().casefold() not in {"dialogue", "narration"}:
        return False
    turns = extract_spoken_dialogue(
        prompt,
        dialogue_text=dialogue_text,
        spoken_dialogue=spoken_dialogue,
    )
    return not turns


def strip_dialogue_text_from_visual_prompt(
    prompt: object,
    *,
    spoken_text: object = "",
) -> str:
    """Remove spoken words while retaining a provider-safe performance cue."""

    text = str(prompt or "").strip()
    spoken = str(spoken_text or "").strip()
    if spoken:
        pattern = re.escape(spoken).replace(r"\ ", r"\s*")
        text = re.sub(pattern, "说话表演", text, flags=re.IGNORECASE)
    # Replace quoted content first, then remove only the speech marker and the
    # immediately following clause. Stopping at a comma for ordinary speech
    # preserves a later camera/action clause such as ``镜头缓慢推近``.
    # 招牌/字幕一类画面文字原样保留——它是画面的一部分，不是说话内容。
    def _quote_replacer(match: re.Match[str]) -> str:
        if _is_scenery_text_quote(text, match.start()):
            return match.group(0)
        if _is_reference_asset_label_quote(text, match.start(), match.end()):
            return match.group(0)
        return "说话表演"

    text = _QUOTED_DIALOGUE_PATTERN.sub(_quote_replacer, text)
    # 冒号式（含未成对引号）：剥台词、留续写。续写词（镜头推近…）在抽取侧同样被
    # `_trim_dialogue_continuation` 截掉，两侧口径一致。
    def _clause_replacer(match: re.Match[str]) -> str:
        _spoken, tail = _trim_dialogue_continuation(match.group("text"))
        return "说话表演" + tail

    text = _UNQUOTED_DIALOGUE_CLAUSE_PATTERN.sub(_clause_replacer, text)
    text = _UNQUOTED_DIALOGUE_COMMA_PATTERN.sub(_clause_replacer, text)
    # 动词与表演提示之间残留的冒号/引号收干净。
    text = re.sub(
        rf"{_DIALOGUE_SPEECH_VERB_PATTERN}\s*[:：]\s*(?:说话表演)",
        "说话表演",
        text,
        flags=re.IGNORECASE,
    )
    # Singing has the same semantic boundary as dialogue, but its performance
    # cue must survive as ``演唱表演`` so the visual model keeps the action
    # without receiving the literal lyric.
    text = _SINGING_DIALOGUE_CLAUSE_PATTERN.sub("演唱表演", text)
    # A marker left without a clause is still not a valid visual instruction;
    # remove only the marker, leaving the surrounding subject/action intact.
    text = _UNAUTHORIZED_DIALOGUE_MARKER_PATTERN.sub("", text)
    text = _SINGING_DIALOGUE_MARKER_PATTERN.sub("演唱表演", text)
    text = re.sub(r"说\s*说话表演", "说话表演", text)
    # 剥台词后作者写的游离引号收干净（成对引号那趟已消费，剩下的都是半拉引号）。
    text = re.sub(r"说话表演([！!？?。.，,]*)\s*[\"“”「」『』]+", r"说话表演\1", text)
    text = re.sub(r"[，,]\s*[，,]+", "，", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip(" ，,；;\"“”「」『』")


def normalize_video_prompt_for_submission_result(
    prompt: object,
    *,
    duration_seconds: int | float,
    dialogue_text: object = "",
    spoken_dialogue: object = (),
    audio_type: object = "",
    speaker: object = "",
    dialogue_authorized: bool = False,
    has_audio_reference: bool = False,
) -> VideoPromptNormalization:
    """Separate one video request into visual and spoken contracts.

    ``has_audio_reference`` marks a request that carries a reference audio track
    for the model to align to. Such a request has a real speech source and must
    not be mistaken for a visual-only request.
    """

    from novelvideo.chat.memory_hooks import preview_remove_redundant_duration

    # 上一次规范化挂上去的 `对白：` 槽位先收回来再剥掉：槽位里的词必须原样保留
    # （画面描述里的原句早已被换成「说话表演」，词只剩在槽位里），重新规范化时
    # 再按同样的形状挂回去，整条链路因此是幂等的。
    raw_prompt, restored_dialogue = _split_existing_dialogue_slots(prompt)
    structured_dialogue_values: list[object] = []
    if isinstance(spoken_dialogue, (list, tuple)):
        structured_dialogue_values.extend(spoken_dialogue)
    elif str(spoken_dialogue or "").strip():
        structured_dialogue_values.append(spoken_dialogue)
    if isinstance(dialogue_text, (list, tuple)):
        structured_dialogue_values.extend(dialogue_text)
    elif str(dialogue_text or "").strip():
        structured_dialogue_values.append(dialogue_text)
    # 提示词自带的说话标记。结构化字段只在这条为 False（提示词压根没提说话）时才兜底：
    # 否则 `路牌写着“欢迎回家”` 这种只有画面文字的提示词会把过期的 `dialogue_text`
    # 提升成台词，让角色开口念招牌。
    prompt_has_dialogue_marker = bool(
        _UNAUTHORIZED_DIALOGUE_MARKER_PATTERN.search(raw_prompt)
        or _SINGING_DIALOGUE_MARKER_PATTERN.search(raw_prompt)
        or _has_non_reference_quote(raw_prompt)
        or _PLAIN_NARRATION_DIALOGUE_PATTERN.search(raw_prompt)
    )
    spoken_dialogue_values = extract_spoken_dialogue(
        raw_prompt,
        dialogue_text=dialogue_text,
        spoken_dialogue=spoken_dialogue,
        restore=restored_dialogue,
        allow_structured_fallback=not prompt_has_dialogue_marker,
    )
    # 来源与冲突披露（T-221/JEV A4）：正文自带台词时它以正文为准（既有权威顺序），
    # 若结构化对白字段里还有**不同**的一句，标记出来供回执告知用户。
    prompt_turns = extract_spoken_dialogue(
        raw_prompt, allow_structured_fallback=False
    )
    structured_turns = tuple(
        text
        for text in (
            _normalize_spoken_text(item)
            for item in (
                *_iter_structured_spoken(spoken_dialogue),
                *_iter_structured_spoken(dialogue_text),
            )
        )
        if text
    )
    if restored_dialogue:
        dialogue_source = "restored"
    elif prompt_turns:
        dialogue_source = "prompt"
    elif structured_turns:
        dialogue_source = "structured"
    else:
        dialogue_source = ""
    dialogue_structured_overridden = bool(
        prompt_turns and structured_turns and tuple(prompt_turns) != structured_turns
    )
    raw_dialogue_marker = bool(
        prompt_has_dialogue_marker
        or bool(structured_dialogue_values)
        or bool(restored_dialogue)
    )
    has_singing_marker = bool(
        _SINGING_DIALOGUE_MARKER_PATTERN.search(raw_prompt)
        or "演唱表演" in raw_prompt
    )
    visual_source = raw_prompt
    if raw_dialogue_marker:
        _LOGGER.warning(
            "video prompt contains literal dialogue; separating it from the visual request "
            "(audio_type=%s, speaker_present=%s, dialogue_authorized=%s)",
            str(audio_type or "").strip(),
            bool(str(speaker or "").strip()),
            dialogue_authorized,
        )
        visual_source = strip_dialogue_text_from_visual_prompt(
            raw_prompt,
            spoken_text=" ".join(spoken_dialogue_values),
        )

    preview = preview_remove_redundant_duration(
        visual_source,
        node_type="videoNode",
        duration_sec=duration_seconds,
    )
    transformed = preview.get("transformed")
    visual_prompt = str(
        transformed if isinstance(transformed, str) else raw_prompt
    ).strip()
    # 音轨说什么在这里定死。**加指令没用**（2026-09-15 真机否掉了「不许念」的约束，
    # 原因见 `NATIVE_SPEECH_SCOPE_CONSTRAINT_RETIRED` 上方），要用**结构**。
    # 这里先生成通用 native prompt；最终后端确定后，H3 会改用官方三章节构建器，
    # 不再使用 `对白：…口型同步` 槽位。
    provider_prompt = visual_prompt
    if has_singing_marker and spoken_dialogue_values:
        provider_prompt = _build_singing_native_provider_prompt(
            visual_prompt,
            spoken_dialogue_values,
        )
    elif spoken_dialogue_values:
        provider_prompt = _build_native_dialogue_provider_prompt(
            visual_prompt,
            spoken_dialogue_values,
            speaker=str(speaker or ""),
        )
    return VideoPromptNormalization(
        visual_prompt=visual_prompt,
        provider_prompt=provider_prompt,
        spoken_dialogue=spoken_dialogue_values,
        had_dialogue_marker=raw_dialogue_marker,
        dialogue_is_sung=has_singing_marker,
        dialogue_source=dialogue_source,
        dialogue_structured_overridden=dialogue_structured_overridden,
    )


def normalize_video_prompt_for_submission(
    prompt: object,
    *,
    duration_seconds: int | float,
    dialogue_authorized: bool = False,
) -> str:
    """Apply deterministic speech-cue and duration normalization before submit.

    A node already carries the authoritative total duration.  When a prompt
    repeats that same value (for example, ``生成 4 秒视频``), keep the visual
    direction and remove only the redundant declaration before provider
    validation.  Mismatched values are deliberately left untouched so the
    contract can still surface a clear preflight error instead of guessing.
    Literal dialogue is never sent in the visual prompt. The optional
    ``dialogue_authorized`` flag remains accepted for stored API requests, but
    authorization selects the independent audio route rather than restoring
    words to the video prompt.
    """

    return normalize_video_prompt_for_submission_result(
        prompt,
        duration_seconds=duration_seconds,
        dialogue_authorized=dialogue_authorized,
    ).visual_prompt


def resolve_video_audio_preference(
    *,
    requested: object = True,
    requested_explicit: bool | None = None,
    audio_type: object = "",
    native_audio: object = "optional",
    native_audio_strategy: object = "",
    has_spoken_dialogue: bool = False,
    has_external_audio: bool = False,
) -> bool:
    """Resolve the provider-facing native-audio toggle from semantic intent.

    ``generate_audio_explicit`` marks the user-facing native-audio control on
    the canvas video node. When it is present, the selected switch value
    wins over persisted semantic fields such as ``native_audio_strategy`` or
    ``audio_type``. Those fields remain a compatibility fallback only for
    older callers that did not send the explicit switch marker. A model whose
    contract requires or forbids native audio remains authoritative.
    ``has_external_audio`` is accepted as evidence
    for callers and keeps the interface explicit, even though a typed speech
    request already implies the external route.
    """

    native = getattr(native_audio, "value", native_audio)
    native_value = str(native or "optional").strip().casefold()
    strategy = str(native_audio_strategy or "").strip().casefold()
    audio_kind = str(audio_type or "").strip().casefold()

    if native_value == "unsupported":
        return False

    # The simplified canvas exposes one audio switch. Do this check before
    # any persisted semantic field so an old ``native``/``external`` value
    # cannot silently override an explicitly toggled user choice. ``None`` is
    # reserved for callers that predate the explicit switch marker. A legacy
    # recovery record can contain the effective ``True`` value together with
    # a false marker; treat that inconsistent pair as an omitted marker.
    explicit_switch_is_consistent = not (
        requested_explicit is False and bool(requested) is True
    )
    if requested_explicit is not None and explicit_switch_is_consistent:
        # A provider that requires native audio still gets ``True`` when the
        # user explicitly enables the switch. Explicitly disabling it wins over
        # every persisted semantic field and is handled by the local artifact
        # gate for required providers.
        if bool(requested):
            return True
        # Required-audio providers cannot accept an audio-off wire request.
        # The final local artifact still follows the explicit canvas switch.
        return native_value == "required"

    if native_value == "required":
        return True
    if strategy in {"native", "model", "native_audio", "required"}:
        return True
    if strategy in {"external", "tts", "post", "silent", "off"}:
        return False
    if audio_kind in {"silence", "action"}:
        return False
    if bool(requested) and not has_external_audio:
        return True
    if audio_kind in {"dialogue", "narration"}:
        return False
    if has_spoken_dialogue:
        return False
    if has_external_audio:
        return False
    return bool(requested)


def should_strip_unrequested_native_audio(
    *,
    requested: object = True,
    requested_explicit: bool | None = None,
    audio_type: object = "",
    native_audio_strategy: object = "",
    has_spoken_dialogue: bool = False,
    has_external_audio: bool = False,
) -> bool:
    """Decide whether a returned provider audio stream is disposable.

    Some providers (notably H3-compatible endpoints) declare native audio as
    required and therefore return an audio stream even when the canvas asked
    for an external narration/dialogue chain.  The provider-facing request
    still follows that upstream contract; this predicate governs the final
    local artifact instead.  When the simplified canvas supplied an explicit
    audio switch (``requested_explicit`` is not ``None``), that switch is authoritative
    for the returned artifact as well.  Only an explicit native strategy from
    a legacy caller (one that omitted the switch marker) is allowed to keep
    the stream.
    """

    strategy = str(native_audio_strategy or "").strip().casefold()
    audio_kind = str(audio_type or "").strip().casefold()

    explicit_switch_is_consistent = not (
        requested_explicit is False and bool(requested) is True
    )
    if requested_explicit is not None and explicit_switch_is_consistent:
        return not bool(requested)

    if strategy in {"native", "model", "native_audio", "required"}:
        return False
    if strategy in {"external", "tts", "post", "silent", "off"}:
        return True
    if bool(requested) and not has_external_audio and audio_kind not in {"silence", "action"}:
        return False
    if audio_kind in {"silence", "action", "dialogue", "narration", "external"}:
        return True
    if has_spoken_dialogue or has_external_audio:
        return True
    return not bool(requested)


def _reference_counts(reference_items: Iterable[Mapping[str, Any] | object]) -> dict[str, int]:
    counts = {"image": 0, "video": 0, "audio": 0}
    for item in reference_items:
        if isinstance(item, Mapping):
            kind = str(item.get("type") or item.get("kind") or "image").strip().lower()
            path = str(item.get("path") or item.get("url") or "").strip()
        else:
            kind = str(getattr(item, "type", "") or "image").strip().lower()
            path = str(getattr(item, "path", "") or getattr(item, "url", "")).strip()
        if kind in counts and path:
            counts[kind] += 1
    return counts


#: 台词语速只用于服务端诊断日志，不再作为画布提交闸门。
#: `4.0` 是规划语速：低于它说明镜头偏紧，值得记一条日志，但由用户决定是否调整。
#: 2026-10-02 按用户要求取消原先 `6.0` 字/秒的硬拦截。
DIALOGUE_PLANNING_CHARS_PER_SECOND = 4.0
#: 与语速无关的计数：中文按字，西文按词，标点不计。
_DIALOGUE_COUNTABLE_PATTERN = re.compile(r"[\u4e00-\u9fff]|[A-Za-z]+")


def dialogue_speech_char_count(dialogue: Iterable[str] | str) -> int:
    """Count speakable units: one per CJK character, one per Latin word."""

    if isinstance(dialogue, str):
        values: Iterable[str] = (dialogue,)
    else:
        values = dialogue
    return len(_DIALOGUE_COUNTABLE_PATTERN.findall(" ".join(str(item or "") for item in values)))


def dialogue_duration_target(char_count: int) -> float:
    """Seconds the line wants at the planning rate; the comfortable value."""

    if char_count <= 0:
        return 0.0
    return char_count / DIALOGUE_PLANNING_CHARS_PER_SECOND


def validate_video_request_contract(
    *,
    prompt: str,
    duration_seconds: int | float,
    reference_items: Iterable[Mapping[str, Any] | object] = (),
    spoken_dialogue: Iterable[str] | str = (),
) -> tuple[VideoRequestIssue, ...]:
    """Validate semantic request invariants before any billing or submission."""

    issues: list[VideoRequestIssue] = []
    try:
        selected_duration = int(round(float(duration_seconds)))
    except (TypeError, ValueError, OverflowError):
        selected_duration = 0

    mentions = extract_prompt_duration_mentions(prompt)
    mismatched = tuple(value for value in mentions if value != selected_duration)
    if selected_duration > 0 and mismatched:
        # The selected duration is authoritative.  Competitor products validate
        # the request parameter against the model schema, not numbers quoted in
        # prose; keep this as a diagnostic so a shot description like
        # "第 3 秒切到特写" never blocks submission.
        _LOGGER.info(
            "prompt duration mentions do not match node duration: selected=%s mentions=%s",
            selected_duration,
            list(mentions),
        )

    # 台词时长只做诊断，不再拦截提交。字数仍然保留在这里，是为了让日志能指出
    # 偏紧的镜头；是否加长镜头或精简台词由用户决定。
    char_count = dialogue_speech_char_count(spoken_dialogue)
    if selected_duration > 0 and char_count > 0:
        target_seconds = dialogue_duration_target(char_count)
        if selected_duration < target_seconds:
            _LOGGER.info(
                "dialogue is tighter than the planning rate: %s chars in %s seconds "
                "(planning %.1f/s)",
                char_count,
                selected_duration,
                DIALOGUE_PLANNING_CHARS_PER_SECOND,
            )

    tokens = extract_prompt_reference_tokens(prompt)
    counts = _reference_counts(reference_items)
    if tokens and counts["image"] == 0:
        issues.append(
            VideoRequestIssue(
                code="prompt_reference_missing",
                message="提示词引用了参考图片，但当前请求没有可上传的图片素材",
                details={
                    "referenceTokens": list(tokens),
                    "referenceCounts": counts,
                },
            )
        )
    return tuple(issues)


_CANVAS_MODE_TO_VIDEO_MODE = {
    "textToVideo": VideoMode.TEXT_TO_VIDEO,
    "imageToVideo": VideoMode.IMAGE_TO_VIDEO,
    "firstLastFrame": VideoMode.FIRST_LAST_FRAME,
    "allReference": VideoMode.REFERENCE_TO_VIDEO,
    "imageReference": VideoMode.REFERENCE_TO_VIDEO,
    "videoEdit": VideoMode.REFERENCE_TO_VIDEO,
}


def _capability_reference_role(value: object) -> str:
    text = str(value or "").strip().casefold()
    if text in {"首帧", "first_frame", "first frame", "first"}:
        return "first"
    if text in {"尾帧", "last_frame", "last frame", "last"}:
        return "last"
    # A keyframe request with only a last frame is intentionally degraded to
    # single-frame i2v by the route; count that fallback as the input image.
    if text in {"尾帧参考", "last_frame_reference"}:
        return "first"
    return "reference"


def validate_structured_video_capability(
    *,
    backend: str | None,
    mode: str | None = None,
    duration_seconds: int | float | None = None,
    resolution: str | None = None,
    aspect_ratio: str | None = None,
    generate_audio: bool | None = None,
    reference_items: Iterable[Mapping[str, Any] | object] = (),
    last_frame_path: str | None = None,
) -> tuple[VideoRequestIssue, ...]:
    """Check a direct model's declared contract without queueing or billing.

    Legacy model IDs deliberately return no issues here; their existing
    provider-specific validators remain the compatibility boundary. Direct
    models, including generic adapters, share this one strict preflight.
    """

    from novelvideo.generators.video.direct_models import (
        direct_video_model_option,
        resolve_direct_video_model,
    )

    direct_model = resolve_direct_video_model(backend)
    if direct_model is None:
        return ()
    capability = direct_model.capability
    issues: list[VideoRequestIssue] = []
    try:
        option = direct_video_model_option(direct_model)
    except (AttributeError, TypeError, ValueError):
        option = {}
    model_id = str(
        option.get("id")
        or getattr(direct_model, "backend", "")
        or capability.model_id
    )
    capability_revision = str(
        option.get("capabilityRevision")
        or option.get("capability_revision")
        or getattr(capability, "catalog_revision", "")
        or "direct-video-contract.v1"
    )
    capability_source = str(
        option.get("capabilitySource")
        or option.get("capability_source")
        or "profile"
    )

    def issue(code: str, message: str, **details: object) -> None:
        issues.append(
            VideoRequestIssue(
                code=code,
                message=message,
                details={
                    "modelId": model_id,
                    "capabilityRevision": capability_revision,
                    "capabilitySource": capability_source,
                    **details,
                },
            )
        )

    selected_mode = _CANVAS_MODE_TO_VIDEO_MODE.get(str(mode or "").strip())
    if mode and selected_mode is None:
        issue("unsupported_video_mode", f"视频模式 {mode!r} 不在模型能力合同中", requestedMode=mode)
    elif selected_mode is not None and selected_mode not in capability.modes:
        issue(
            "unsupported_video_mode",
            f"模型 {capability.model_id!r} 不支持视频模式 {mode!r}",
            requestedMode=mode,
            supportedModes=[item.value for item in capability.modes],
        )

    if duration_seconds is not None:
        try:
            duration = int(round(float(duration_seconds)))
        except (TypeError, ValueError, OverflowError):
            duration = 0
        if duration not in capability.duration:
            issue(
                "unsupported_duration",
                f"模型 {capability.model_id!r} 不支持 {duration_seconds} 秒，支持值为 {list(capability.duration)}",
                requestedDuration=(
                    str(duration_seconds)
                    if isinstance(duration_seconds, float) and not math.isfinite(duration_seconds)
                    else duration_seconds
                ),
                supportedDurations=list(capability.duration),
            )

    if resolution is not None and str(resolution).strip():
        requested_resolution = str(resolution).strip().lower().replace("×", "x")
        # ModelCapability stores canonical heights, while provider profiles
        # may retain an exact transport label such as `768p竖`. Include both
        # sets in the preflight comparison so orientation is not rejected.
        profile = getattr(direct_model, "profile", None)
        profile_resolutions = getattr(profile, "resolution", ())
        resolution_supported = requested_resolution in {
            str(item).casefold() for item in (
                *capability.resolution,
                *(profile_resolutions if isinstance(profile_resolutions, (tuple, list)) else ()),
            )
        }
        custom_resolution = bool(
            getattr(capability, "supports_custom_resolution", False)
            and _VIDEO_RESOLUTION_PATTERN.fullmatch(requested_resolution)
        )
        if not resolution_supported and not custom_resolution:
            issue(
                "unsupported_resolution",
                f"模型 {capability.model_id!r} 不支持清晰度 {resolution!r}，支持值为 {list(capability.resolution)}",
                requestedResolution=resolution,
                supportedResolutions=list(capability.resolution),
            )

    if aspect_ratio is not None and str(aspect_ratio).strip():
        requested_aspect = str(aspect_ratio).strip()
        supported_aspects = {
            str(item).strip().casefold() for item in capability.aspect
        }
        custom_aspect = bool(
            getattr(capability, "supports_custom_aspect_ratio", False)
            and _VIDEO_ASPECT_PATTERN.fullmatch(requested_aspect)
        )
        if requested_aspect.casefold() not in supported_aspects and not custom_aspect:
            issue(
                "unsupported_aspect_ratio",
                f"模型 {capability.model_id!r} 不支持比例 {aspect_ratio!r}，支持值为 {list(capability.aspect)}",
                requestedAspectRatio=aspect_ratio,
                supportedAspectRatios=list(capability.aspect),
            )

    if generate_audio is True and capability.native_audio is NativeAudio.UNSUPPORTED:
        issue(
            "native_audio_unsupported",
            f"模型 {capability.model_id!r} 不支持原生音频",
            requestedNativeAudio=True,
            nativeAudio=capability.native_audio.value,
        )
    if generate_audio is False and capability.native_audio is NativeAudio.REQUIRED:
        issue(
            "native_audio_required",
            f"模型 {capability.model_id!r} 要求开启原生音频",
            requestedNativeAudio=False,
            nativeAudio=capability.native_audio.value,
        )

    counts = {"input_images": 0, "reference_images": 0, "reference_videos": 0, "reference_audios": 0}
    first_count = 0
    last_count = 0
    invalid_frame_media = False
    ordinary_items: list[tuple[str, str]] = []
    seen_inputs: set[tuple[str, str, str]] = set()
    for item in reference_items:
        if isinstance(item, Mapping):
            kind = str(item.get("type") or item.get("kind") or "image").strip().lower()
            path = str(item.get("path") or item.get("url") or "").strip()
            role_value = item.get("role")
        else:
            kind = str(getattr(item, "type", "") or "image").strip().lower()
            path = str(getattr(item, "path", "") or getattr(item, "url", "")).strip()
            role_value = getattr(item, "role", "")
        if not path or kind not in {"image", "video", "audio"}:
            continue
        role = _capability_reference_role(role_value)
        marker = (kind, path, role)
        if marker in seen_inputs:
            continue
        seen_inputs.add(marker)
        if role in {"first", "last"} and kind != "image":
            invalid_frame_media = True
            counts[f"reference_{kind}s"] += 1
            continue
        if role == "first":
            first_count += 1
            counts["input_images"] += int(kind == "image")
        elif role == "last":
            last_count += 1
            counts["input_images"] += int(kind == "image")
        else:
            ordinary_items.append((kind, path))
            counts[f"reference_{kind}s"] += 1
    if last_frame_path and ("image", str(last_frame_path).strip(), "last") not in seen_inputs:
        last_count += 1
        counts["input_images"] += 1

    if invalid_frame_media:
        issue("reference_role_mismatch", "首帧和尾帧只能绑定图片素材")
    elif selected_mode is VideoMode.IMAGE_TO_VIDEO and (
        first_count != 1 or last_count or ordinary_items
    ):
        issue("reference_role_mismatch", "图生视频必须恰好绑定一个首帧图片，不能混入尾帧或普通参考素材")
    elif selected_mode is VideoMode.FIRST_LAST_FRAME and (first_count != 1 or last_count != 1 or ordinary_items):
        issue("reference_role_mismatch", "首尾帧模式必须绑定一个首帧和一个尾帧图片")
    elif selected_mode is VideoMode.TEXT_TO_VIDEO and (first_count or last_count or ordinary_items):
        issue("reference_role_mismatch", "文生视频模式不能携带参考素材")
    elif selected_mode is VideoMode.REFERENCE_TO_VIDEO and not ordinary_items:
        issue("reference_role_mismatch", "参考视频模式至少需要一个普通参考素材")

    limits = capability.reference_limits
    for name, count in counts.items():
        maximum = getattr(limits, name)
        if count > maximum:
            issue(
                f"{name}_limit_exceeded",
                f"{capability.model_id!r} 的 {name} 上限为 {maximum}，当前为 {count}",
                referenceCounts=counts,
                referenceLimits=limits.to_dict(),
            )
    return tuple(issues)


__all__ = [
    "DIALOGUE_PLANNING_CHARS_PER_SECOND",
    "dialogue_duration_target",
    "dialogue_speech_char_count",
    "build_minimax_h3_provider_prompt",
    "blocking_h3_prompt_issues",
    "compile_h3_picture_prompt",
    "compile_h3_provider_prompt",
    "explicit_audio_type_requests_silence",
    "extract_spoken_dialogue",
    "is_minimax_h3_model_identifier",
    "VideoPromptNormalization",
    "VideoRequestContractError",
    "VideoRequestIssue",
    "extract_prompt_duration_mentions",
    "extract_prompt_reference_tokens",
    "normalize_video_prompt_for_submission",
    "normalize_video_prompt_for_submission_result",
    "resolve_video_audio_preference",
    "resolve_h3_soundscape",
    "sanitize_h3_visual_prompt",
    "should_strip_unrequested_native_audio",
    "split_workflow_motion_prompt",
    "strip_dialogue_text_from_visual_prompt",
    "validate_structured_video_capability",
    "validate_video_request_contract",
    "workflow_motion_soundscape",
]
