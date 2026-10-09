"""把故事原文整理成可稳定生成分镜的工作文本。

原始故事必须完整保留；本模块只生成生产链使用的副本，避免标题、创作说明
和重复的项目信息被逐行拆成伪镜头。
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from typing import Mapping


_METADATA_FIELD_RE = re.compile(
    r"^\s*(作品标题|标题|片名|类型|题材|目标(?:时长|集数)?|画幅|风格|"
    r"核心主题|目标受众|受众|每集(?:目标)?(?:长度|时长)|叙事视角)\s*[:：]"
)
_TITLE_RE = re.compile(r"^\s*《[^》]{1,120}》\s*$")
_STORY_START_RE = re.compile(
    r"^\s*(?:第?[一二三四五六七八九十百0-9]+幕\b|"
    r"(?:序幕|尾声|结尾|开场)\s*[:：]?|"
    r"(?:第?[一二三四五六七八九十百0-9]+集|场次|镜头|INT\.|EXT\.)\b)",
    re.IGNORECASE,
)
_STORY_SECTION_RE = re.compile(
    r"^\s*(?P<section>第?[一二三四五六七八九十百0-9]+幕|序幕|尾声|结尾|开场)"
    r"\s*[:：]\s*(?P<body>.+)$",
    re.IGNORECASE,
)
_SETUP_LINE_RE = re.compile(
    r"^\s*(?:主角|角色设定|人物设定|关键场景|场景设定|关键道具|道具设定|"
    r"连续性要求|一致性要求|视觉风格)\s*[:：，,]?"
)
_SEPARATOR_RE = re.compile(r"^\s*(?:-{3,}|={3,}|\*{3,})\s*$")
_SENTENCE_RE = re.compile(r".+?(?:[。！？!?]+[”’』】\"]*|$)")
_SPACE_RE = re.compile(r"\s+")

#: 剧本里写给编剧看的文档块。命中即从这一行起整段截断，不再产出镜头。
#: 实例：`## 六、台词量核对（本版）`、`## 七、自检（对照 Dream导演 剧本闸门）`、
#: `## 八、下一步`。这些是剧本的附录，不是戏。
_SCRIPT_DOC_SECTION_RE = re.compile(
    r"^\s*#{1,4}\s*(?:[一二三四五六七八九十0-9]+\s*[、.．)]?\s*)?"
    r"(?:台词量核对|自检(?:对照)?|下一步|附录|剧本说明|制作说明|分镜说明|"
    r"质量自检|交付说明)\b"
)
#: Markdown 结构行：标题、分隔线、表格行、引用、勾选清单。它们从不入镜。
_SCRIPT_DOC_MARKUP_RE = re.compile(r"^\s*(?:#{1,6}\s|[-*_=]{3,}\s*$|\||>|☑|✅)")
#: 镜头/场次表头：`镜 2（4-8s）`、`**镜 1（12-17s）**`、`【镜 2（27-32s）】`。
_SCRIPT_SHOT_HEADER_RE = re.compile(
    r"^\s*(?:\*\*|【)?\s*镜\s*\d+\s*[（(][^）)]{0,24}[）)]\s*(?:\*\*|】)?\s*[，,。；;、:：]?\s*"
)
#: 场次标题行：`### 第二场｜真相夜话（12-22 秒 · 2 镜）`、`【场次转换】### 第三场…`。
_SCRIPT_SCENE_HEADER_RE = re.compile(
    r"^\s*(?:【[^】]{0,8}】\s*)?#{0,6}\s*"
    r"第\s*[一二三四五六七八九十百0-9]+\s*场"
    r"(?:\s*[｜|:：]|\s*(?:转场|过渡|切换))"
)
#: 黑场文字卡 / 转场卡：只为在画面上写中文字而存在，属于后期叠字，不进生成。
_SCRIPT_CARD_RE = re.compile(
    r"^\s*(?:(?:【黑屏】|黑屏|黑底|纯黑|全黑)|"
    r"(?:末拍|最后一帧).*(?:黑屏|黑底|纯黑|全黑))"
)
_SCRIPT_CARD_WORDS = (
    "文字", "字幕", "标题", "说明", "字样", "文档", "表格", "清单",
    "显示", "呈现", "提示", "转场", "过渡", "片头", "正文",
)
#: 编剧旁注：只按明确的行首标记排除，避免误删正常台词里的相同词语。
_SCRIPT_NOTE_LEAD_RE = re.compile(
    r"^\s*(?:\*\*|【)?\s*(?:画面说明|场次转换|编剧笔记|剧本说明|注\s*[:：])"
)
_SCRIPT_TRANSITION_NOTE_RE = re.compile(
    r"^\s*(?:画面切换至|镜头准备切入|镜头画面衔接|画面准备|"
    r"画面呈现文本|屏幕(?:中央|正中)|【空镜】第)"
)
_MARKDOWN_EMPHASIS_RE = re.compile(r"\*{1,2}")


def normalize_script_line(line: object) -> str:
    """Return the filmable text of one script line, or ``""`` for documentation.

    剧本里混着大量只给编剧看的内容：场次标题、镜头表头、表格行、勾选清单、
    编剧笔记、黑场文字卡。它们被逐行切成"镜头"以后，会变成真实付费生成任务，
    并让画面出现中文字幕。这里把这类行判掉；如果一行是"表头 + 正文"的混合体，
    则剥掉表头、保留可拍的正文。
    """

    raw = str(line or "").strip()
    if not raw:
        return ""
    if _SCRIPT_DOC_MARKUP_RE.match(raw):
        return ""
    if _SCRIPT_NOTE_LEAD_RE.match(raw):
        return ""
    if _SCRIPT_CARD_RE.match(raw) and any(word in raw for word in _SCRIPT_CARD_WORDS):
        return ""
    if _SCRIPT_SCENE_HEADER_RE.match(raw):
        return ""
    if _SCRIPT_TRANSITION_NOTE_RE.match(raw):
        return ""
    stripped = raw
    header = _SCRIPT_SHOT_HEADER_RE.match(stripped)
    if header:
        stripped = stripped[header.end() :].strip()
        if not stripped:
            return ""
    stripped = _MARKDOWN_EMPHASIS_RE.sub("", stripped).strip()
    return stripped


def is_script_documentation_line(line: object) -> bool:
    """Return whether a line is script documentation rather than a filmable beat."""

    raw = str(line or "").strip()
    if not raw:
        return False
    return not normalize_script_line(raw)


@dataclass(frozen=True, slots=True)
class StoryInputNormalization:
    """故事原文的不可变生产视图。"""

    beat_source_text: str
    metadata: Mapping[str, str]
    removed_lines: tuple[str, ...]
    normalized_content_hash: str

    @property
    def beat_lines(self) -> tuple[str, ...]:
        return tuple(line for line in self.beat_source_text.splitlines() if line.strip())


def _normalize_for_compare(value: str) -> str:
    return _SPACE_RE.sub(" ", str(value or "").strip()).replace("：", ":")


def _metadata_value(line: str) -> tuple[str, str] | None:
    match = _METADATA_FIELD_RE.match(line)
    if not match:
        return None
    key = match.group(1)
    value = line[match.end() :].strip()
    return key, value


def _is_story_start(line: str) -> bool:
    return bool(_STORY_START_RE.match(line))


def _is_metadata_block(lines: list[str]) -> bool:
    return sum(_metadata_value(line) is not None for line in lines) >= 2


def _split_story_line(line: str) -> list[str]:
    """Split long prose into production-sized semantic Beat candidates."""

    section = _STORY_SECTION_RE.match(line)
    body = section.group("body").strip() if section else line.strip()
    sentences = [match.group(0).strip() for match in _SENTENCE_RE.finditer(body)]
    sentences = [sentence for sentence in sentences if sentence]
    if not sentences:
        return [line.strip()] if line.strip() else []
    if section:
        sentences[0] = f"{section.group('section')}：{sentences[0]}"
    return sentences


def _beat_compare_key(line: str) -> str:
    section = _STORY_SECTION_RE.match(line)
    value = section.group("body") if section else line
    return _normalize_for_compare(value).strip("。！？!?；;，,")


def merge_story_fragments(fragments: list[str] | tuple[str, ...]) -> str:
    """Merge overlapping event excerpts without duplicating their source text."""

    merged: list[tuple[str, str]] = []
    for fragment in fragments:
        clean = str(fragment or "").strip()
        key = _normalize_for_compare(clean)
        if not key:
            continue
        if any(key == existing_key or key in existing_key for _, existing_key in merged):
            continue
        contained = [
            index
            for index, (_, existing_key) in enumerate(merged)
            if existing_key in key
        ]
        if contained:
            insert_at = contained[0]
            merged = [
                item for index, item in enumerate(merged) if index not in contained
            ]
            merged.insert(insert_at, (clean, key))
            continue
        merged.append((clean, key))
    return "\n\n---\n\n".join(fragment for fragment, _ in merged)


def normalize_story_input(text: str) -> StoryInputNormalization:
    """提取故事元信息并返回去污染后的分镜工作文本。

    过滤范围是确定性的：
    - 顶部标题和字段式项目说明进入 metadata；
    - 第一处幕/场次/镜头标记之前的创作设定不作为 Beat；
    - 后续重复的字段式元信息块被丢弃；
    - 普通剧情行原样保留，不按关键词粗暴删除。
    """

    raw_lines = [line.strip() for line in str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    lines = [line for line in raw_lines if line]
    if not lines:
        return StoryInputNormalization("", {}, (), hashlib.sha256(b"").hexdigest())

    metadata: dict[str, str] = {}
    removed: list[str] = []
    document_removed: list[str] = []

    # 剧本附录（台词量核对 / 自检 / 下一步）是文档，不是戏。命中即整段截断，
    # 否则 markdown 表格行和勾选清单会逐行变成"黑屏显示文字"的付费镜头。
    for cut_index, line in enumerate(lines):
        if _SCRIPT_DOC_SECTION_RE.match(line):
            document_removed.extend(lines[cut_index:])
            removed.extend(document_removed)
            lines = lines[:cut_index]
            break
    if not lines:
        return StoryInputNormalization("", metadata, tuple(removed), hashlib.sha256(b"").hexdigest())

    first_story_index = next(
        (index for index, line in enumerate(lines) if _is_story_start(line)),
        None,
    )

    title_consumed = False
    for index, line in enumerate(lines):
        parsed = _metadata_value(line)
        is_title = (
            index == 0
            and not title_consumed
            and bool(_TITLE_RE.match(line))
        )
        if parsed:
            key, value = parsed
            metadata.setdefault(key, value)
            title_consumed = True
        elif is_title:
            metadata.setdefault("作品标题", line.strip("《》"))
            title_consumed = True

    # 没有明确幕/场次标记时，保守保留普通正文，避免误删小说开头。
    preamble_end = first_story_index if first_story_index is not None else 0
    candidates: list[str] = []
    seen_metadata_lines: set[str] = set()
    for index, line in enumerate(lines):
        parsed = _metadata_value(line)
        normalized = _normalize_for_compare(line)
        in_preamble = first_story_index is not None and index < preamble_end
        title_line = bool(_TITLE_RE.match(line))

        if in_preamble or title_line or _SEPARATOR_RE.match(line):
            removed.append(line)
            continue
        if _SETUP_LINE_RE.match(line):
            metadata.setdefault("创作设定", line)
            removed.append(line)
            continue
        if parsed:
            # 元信息块可能被模型或导入器重复插入正文中；重复项不进入 Beat。
            if normalized in seen_metadata_lines or _is_metadata_block(
                lines[index : index + 3]
            ):
                removed.append(line)
                continue
            seen_metadata_lines.add(normalized)
            removed.append(line)
            continue
        filmable = normalize_script_line(line)
        if not filmable:
            removed.append(line)
            continue
        candidates.extend(_split_story_line(filmable))

    # 没有幕/场次标记时，仅删除明确的元信息字段；普通开头内容仍然保留。
    if first_story_index is None:
        candidates = []
        removed = list(document_removed)
        for line in lines:
            if (
                _metadata_value(line)
                or _TITLE_RE.match(line)
                or _SETUP_LINE_RE.match(line)
                or _SEPARATOR_RE.match(line)
            ):
                removed.append(line)
                continue
            filmable = normalize_script_line(line)
            if not filmable:
                removed.append(line)
                continue
            candidates.extend(_split_story_line(filmable))

    kept: list[str] = []
    seen_beats: set[str] = set()
    for candidate in candidates:
        key = _beat_compare_key(candidate)
        if not key or key in seen_beats:
            if candidate:
                removed.append(candidate)
            continue
        seen_beats.add(key)
        kept.append(candidate)

    # 对清洗后的工作文本做稳定哈希，用于重新导入幂等判断。
    beat_source_text = "\n".join(kept).strip()
    normalized_hash = hashlib.sha256(
        _normalize_for_compare(beat_source_text).encode("utf-8")
    ).hexdigest()
    return StoryInputNormalization(
        beat_source_text=beat_source_text,
        metadata=metadata,
        removed_lines=tuple(removed),
        normalized_content_hash=normalized_hash,
    )


__all__ = [
    "StoryInputNormalization",
    "merge_story_fragments",
    "normalize_story_input",
]
