"""Capability contracts for operator-configured direct video models."""

from __future__ import annotations

from dataclasses import dataclass, replace
import logging
import re
from typing import Any

from .capabilities import NativeAudio, ReferenceLimits, VideoMode
from .model_name_capabilities import ModelNameCapabilities, parse_model_name_capabilities

logger = logging.getLogger(__name__)


_REFERENCE_LIMIT_KEYS = frozenset(
    {
        "inputImages",
        "referenceImages",
        "referenceVideos",
        "referenceAudios",
    }
)


@dataclass(frozen=True, slots=True)
class DirectVideoCapabilityProfile:
    """A model-family contract independent from the gateway that exposes it."""

    name: str
    pattern: re.Pattern[str]
    family: str
    modes: tuple[VideoMode, ...]
    duration: tuple[int, ...]
    resolution: tuple[str, ...]
    aspect: tuple[str, ...]
    reference_limits: ReferenceLimits
    native_audio: NativeAudio
    return_last_frame: bool
    default_resolution: str
    default_duration_seconds: int
    default_aspect_ratio: str
    default_generate_audio: bool = False
    # Explicit upstream metadata may declare arbitrary values instead of a
    # finite preset list.  Keep the finite list for picker suggestions, but
    # preserve validated custom values at the transport boundary.
    supports_custom_aspect_ratio: bool = False
    supports_custom_resolution: bool = False
    # 固定像素档位契约（如 Gemini/Veo 系列：992x432 等）。声明后提交时
    # 不再传 ratio，而是把比例就近映射到合法 size 档位。
    size_slots: tuple[str, ...] = ()
    # 提交时 size 参数名（默认 "size"）。
    size_field: str | None = None
    # 少数站点的「档位名」与它接受的入参写法不同：SparkCine 的任务记录回显
    # "768p@16:9"，但 POST /videos 只接受 OpenAI 视频接口的 "WxH" 像素写法
    # （2026-10-11 免费探测：size=1280x720 / 1080x1920 通过校验，size=720p@16:9 /
    # 720p / 16:9 / 768p 一律 422）。这里放提交层真正发出去的值；档位名仍按站点
    # 自己的回显口径发布给设置面板，两边都不撒谎。
    size_wire_values: tuple[tuple[str, str], ...] = ()
    # Auto-discovery may distinguish canvas modes that share one VideoMode.
    exact_canvas_modes: tuple[str, ...] = ()
    # 空集合表示内置合同四项都明确；非空集合只列出已明确的字段，
    # 便于区分“上游明确为 0”和“上游没有声明上限”。
    reference_limits_known: frozenset[str] = frozenset()
    # Semantic prompt invariants are separate from transport parameters.  They
    # are surfaced to the UI and rechecked by the server before submission.
    prompt_duration_consistency: bool = True
    prompt_reference_consistency: bool = True
    # Number of no-detail ``failed`` polls that may be rechecked for this
    # profile before the task is classified as terminal.
    failure_grace_polls: int | None = None
    # 少数站点的 OpenAI 兼容校验是严格模式：请求体多一个它没声明的包装字段就整包
    # 422（SparkCine 实测：真实被接受过的报文只有 model/prompt/seconds/size 四个键）。
    # 声明 False 后提交层不再携带 metadata 包装。
    supports_request_metadata: bool = True

    def matches(self, upstream_model: str) -> bool:
        return bool(self.pattern.search(upstream_model))

    def parameter_defaults(self) -> dict[str, Any]:
        """Publish the safe balanced preset; explicit user choices still win."""
        return {
            "resolution": self.default_resolution,
            "durationSeconds": self.default_duration_seconds,
            "aspectRatio": self.default_aspect_ratio,
            "generateAudio": self.default_generate_audio,
            "strategy": "balanced",
        }

    def resolve_size(self, aspect_ratio: object) -> str | None:
        """Map a requested aspect ratio to the nearest fixed pixel slot.

        Returns ``None`` when the profile declares no fixed size slots (the
        caller then keeps the legacy ratio/resolution contract).
        """
        if not self.size_slots:
            return None
        requested = str(aspect_ratio or "").strip().lower()
        if requested in self.size_slots:
            return requested
        target = _aspect_to_float(requested)
        if target is None:
            return self.size_slots[0]
        best = self.size_slots[0]
        best_delta = float("inf")
        for slot in self.size_slots:
            slot_ratio = _size_slot_ratio(slot)
            if slot_ratio is None:
                continue
            delta = abs(slot_ratio - target)
            if delta < best_delta:
                best_delta = delta
                best = slot
        return best

    def wire_size_value(self, slot: object) -> str:
        """Return the exact size value this profile puts on the wire.

        Slots stay in the channel's own vocabulary ("768p@16:9") because that is
        what the settings panel and the task records show; endpoints that take a
        different literal declare the translation here.
        """
        text = str(slot or "").strip()
        wanted = text.lower()
        for declared, wire in self.size_wire_values:
            if str(declared).strip().lower() == wanted:
                return str(wire).strip()
        return text

    def resolve_resolution(self, value: object) -> str:
        """Return a declared resolution, falling back to the balanced preset."""
        requested = str(value or "").strip().lower().replace("×", "x")
        if self.supports_custom_resolution and _valid_resolution_value(requested):
            return requested
        for supported in self.resolution:
            if supported.lower() == requested:
                return supported
        for supported in self.resolution:
            if supported == self.default_resolution:
                return supported
        return self.resolution[0]

    def resolve_aspect_ratio(self, value: object) -> str:
        requested = str(value or "").strip().replace("×", "x")
        if self.supports_custom_aspect_ratio and _aspect_to_float(requested) is not None:
            return requested
        if requested in self.aspect:
            return requested
        if self.default_aspect_ratio in self.aspect:
            return self.default_aspect_ratio
        return self.aspect[0]

    def resolve_duration(self, value: object) -> int:
        try:
            requested = int(round(float(value)))
        except (TypeError, ValueError):
            requested = self.default_duration_seconds
        return min(self.duration, key=lambda item: abs(item - requested))

    def resolve_generate_audio(self, value: object) -> bool:
        """Apply the audio contract instead of trusting stale node state."""
        if self.native_audio is NativeAudio.REQUIRED:
            return True
        if self.native_audio is NativeAudio.UNSUPPORTED:
            return False
        return bool(value)


_COMMON_VIDEO_ASPECTS = ("16:9", "9:16", "1:1", "4:3", "3:4", "21:9")


def _aspect_to_float(value: str) -> float | None:
    """Parse '16:9' / '992x432' style ratios into a float (w/h)."""
    text = str(value or "").strip().lower().replace("×", "x")
    if "x" in text:
        parts = text.split("x")
    elif ":" in text:
        parts = text.split(":")
    else:
        return None
    if len(parts) != 2:
        return None
    try:
        width = float(parts[0])
        height = float(parts[1])
    except ValueError:
        return None
    if width <= 0 or height <= 0:
        return None
    return width / height


def _size_slot_ratio(slot: str) -> float | None:
    """Return the aspect ratio a declared size slot stands for.

    Slots come in three shapes: a pixel pair (992x432), a bare ratio (16:9)
    and a resolution-qualified composite (768p@16:9).  The composite form
    carries the resolution before "@" and the ratio after it; handing the whole
    string to :func:`_aspect_to_float` splits it on ":" into parts that are not
    numbers, so no composite slot would ever match and
    :meth:`DirectVideoCapabilityProfile.resolve_size` would silently fall back
    to its first slot (a landscape size for every portrait request).
    """
    text = str(slot or "").strip()
    if "@" in text:
        text = text.rsplit("@", 1)[-1]
    return _aspect_to_float(text)


_CUSTOM_RESOLUTION = re.compile(
    r"^(?:[1-9][0-9]{2,5}p|[1-9][0-9]{0,2}k|[1-9][0-9]{2,5}x[1-9][0-9]{2,5})$",
    re.IGNORECASE,
)


def _valid_resolution_value(value: str) -> bool:
    normalized = str(value or "").strip().lower().replace("×", "x")
    return bool(_CUSTOM_RESOLUTION.fullmatch(normalized))


# SparkCine (hi.hi.run) 的 size 有两个口径，必须分开：
#   * 回显口径（GET {base}/videos 的任务记录）："768p@16:9" 这种「分辨率@比例」。
#   * 入参口径（POST {base}/videos）：OpenAI 视频接口的 "WxH" 像素，例如 "1280x720"。
# 2026-10-11 免费探测（未知模型 + 不同 size，看它是 404 还是 422）证明了这个区别：
# 1280x720 / 1080x1920 通过校验，720p@16:9 / 720p / 16:9 / 768p 全部 422；而完全
# 不带 size 会被受理并按 768p@16:9 建任务。像素值取自该站任务记录回显的 w/h 与
# 下载产物 ffprobe 读数，7 个档位一一对应。
#
# 槽位名按回显口径声明（设置面板和任务记录都用这个口径），真正发出去的值由
# _SPARKCINE_H3_VIDEO_SIZE_WIRE 翻译。清单仍是「实测出现过的集合」，不是上游公布的
# 完整枚举：站点目录只有模型 id，没有任何尺寸字段。
_SPARKCINE_H3_VIDEO_SIZE_SLOTS = (
    "720p@16:9",
    "720p@9:16",
    "768p@16:9",
    "768p@9:16",
    "1080p@16:9",
    "1080p@9:16",
    "1080p@1:1",
)

_SPARKCINE_H3_VIDEO_SIZE_WIRE = (
    ("720p@16:9", "1280x720"),
    ("720p@9:16", "720x1280"),
    ("768p@16:9", "1366x768"),
    ("768p@9:16", "768x1366"),
    ("1080p@16:9", "1920x1080"),
    ("1080p@9:16", "1080x1920"),
    ("1080p@1:1", "1080x1080"),
)

# Gemini / Veo 固定六档（上游 size 字段直接传像素值）。
_GEMINI_OMNI_SIZE_SLOTS = (
    "992x432",
    "864x496",
    "752x560",
    "640x640",
    "560x752",
    "496x864",
)

_DIRECT_VIDEO_PROFILES: tuple[DirectVideoCapabilityProfile, ...] = (
    DirectVideoCapabilityProfile(
        name="prompt-hubs-sd2.0-480p",
        pattern=re.compile(r"^sd2[._ -]?0[-_ ]?480p$", re.IGNORECASE),
        family="prompt-hubs-sd",
        modes=(
            VideoMode.TEXT_TO_VIDEO,
            VideoMode.IMAGE_TO_VIDEO,
            VideoMode.REFERENCE_TO_VIDEO,
        ),
        duration=tuple(range(4, 16)),
        resolution=("480p",),
        aspect=("16:9", "9:16"),
        reference_limits=ReferenceLimits(
            input_images=1,
            reference_images=9,
            reference_videos=3,
            reference_audios=3,
        ),
        native_audio=NativeAudio.UNSUPPORTED,
        return_last_frame=False,
        default_resolution="480p",
        default_duration_seconds=5,
        default_aspect_ratio="16:9",
    ),
    DirectVideoCapabilityProfile(
        name="huabu-sd2.0-fast",
        # 画布的 hyphenated SD route is a known video contract even when the
        # upstream /models entry only returns an ID without capability tags.
        pattern=re.compile(
            r"^sd(?:[-_ ]?2)[._ -]?0[-_ ]?fast(?:[-_ ]?v1)?$",
            re.IGNORECASE,
        ),
        family="prompt-hubs-sd",
        modes=(VideoMode.TEXT_TO_VIDEO, VideoMode.IMAGE_TO_VIDEO),
        duration=(5, 10, 15),
        resolution=("480p", "720p"),
        aspect=("16:9", "9:16", "1:1", "4:3", "3:4"),
        reference_limits=ReferenceLimits(input_images=1),
        native_audio=NativeAudio.UNSUPPORTED,
        return_last_frame=False,
        default_resolution="720p",
        default_duration_seconds=5,
        default_aspect_ratio="16:9",
        exact_canvas_modes=("textToVideo", "imageToVideo"),
        reference_limits_known=frozenset({"inputImages"}),
    ),
    DirectVideoCapabilityProfile(
        name="wokey-jimeng-seedance-2.0-fast",
        pattern=re.compile(r"^jimeng-seedance-2[._ -]?0-fast$", re.IGNORECASE),
        family="wokey-jimeng",
        modes=(
            VideoMode.TEXT_TO_VIDEO,
            VideoMode.IMAGE_TO_VIDEO,
            VideoMode.FIRST_LAST_FRAME,
            VideoMode.REFERENCE_TO_VIDEO,
        ),
        duration=tuple(range(4, 16)),
        resolution=("720p",),
        aspect=_COMMON_VIDEO_ASPECTS,
        reference_limits=ReferenceLimits(
            input_images=2,
            reference_images=9,
            reference_videos=3,
            reference_audios=3,
        ),
        native_audio=NativeAudio.OPTIONAL,
        return_last_frame=True,
        default_resolution="720p",
        default_duration_seconds=5,
        default_aspect_ratio="16:9",
    ),
    DirectVideoCapabilityProfile(
        name="wokey-jimeng-seedance-2.5",
        pattern=re.compile(r"^jimeng-seedance-2[._ -]?5$", re.IGNORECASE),
        family="wokey-jimeng",
        modes=(
            VideoMode.TEXT_TO_VIDEO,
            VideoMode.IMAGE_TO_VIDEO,
            VideoMode.FIRST_LAST_FRAME,
            VideoMode.REFERENCE_TO_VIDEO,
        ),
        duration=tuple(range(4, 31)),
        resolution=("480p", "720p"),
        aspect=_COMMON_VIDEO_ASPECTS,
        reference_limits=ReferenceLimits(
            input_images=2,
            reference_images=9,
            reference_videos=3,
            reference_audios=3,
        ),
        native_audio=NativeAudio.OPTIONAL,
        return_last_frame=True,
        default_resolution="720p",
        default_duration_seconds=5,
        default_aspect_ratio="16:9",
    ),
    DirectVideoCapabilityProfile(
        name="kacang-kling-v2v",
        pattern=re.compile(
            r"kling.*(?:v2v|video[-_ ]?edit|omni.*create)", re.IGNORECASE
        ),
        family="kacang-kling-v2v",
        modes=(VideoMode.REFERENCE_TO_VIDEO,),
        duration=tuple(range(3, 16)),
        resolution=("720p",),
        aspect=("16:9", "9:16"),
        reference_limits=ReferenceLimits(reference_images=9, reference_videos=1),
        native_audio=NativeAudio.UNSUPPORTED,
        return_last_frame=False,
        default_resolution="720p",
        default_duration_seconds=5,
        default_aspect_ratio="16:9",
    ),
    DirectVideoCapabilityProfile(
        name="minimax-h3-v2",
        pattern=re.compile(r"minimax[-_ ]?h3", re.IGNORECASE),
        family="minimax-h3",
        modes=(
            VideoMode.TEXT_TO_VIDEO,
            VideoMode.IMAGE_TO_VIDEO,
            VideoMode.FIRST_LAST_FRAME,
            VideoMode.REFERENCE_TO_VIDEO,
        ),
        duration=tuple(range(4, 16)),
        resolution=("768p", "2k"),
        aspect=_COMMON_VIDEO_ASPECTS,
        reference_limits=ReferenceLimits(
            input_images=2,
            reference_images=9,
            reference_videos=3,
            reference_audios=3,
        ),
        native_audio=NativeAudio.REQUIRED,
        return_last_frame=False,
        default_resolution="2k",
        default_duration_seconds=6,
        default_aspect_ratio="16:9",
        default_generate_audio=True,
        failure_grace_polls=3,
        exact_canvas_modes=(
            "textToVideo",
            "imageToVideo",
            "firstLastFrame",
            "imageReference",
            "allReference",
            "videoEdit",
        ),
    ),
    DirectVideoCapabilityProfile(
        name="kacang-mini-h3",
        pattern=re.compile(r"mini[-_ ]?h3", re.IGNORECASE),
        family="kacang-mini-h3",
        modes=(VideoMode.TEXT_TO_VIDEO,),
        duration=tuple(range(5, 16)),
        resolution=("2k",),
        aspect=_COMMON_VIDEO_ASPECTS,
        reference_limits=ReferenceLimits(),
        native_audio=NativeAudio.UNSUPPORTED,
        return_last_frame=False,
        default_resolution="2k",
        default_duration_seconds=5,
        default_aspect_ratio="16:9",
    ),
    DirectVideoCapabilityProfile(
        name="kacang-933",
        pattern=re.compile(
            r"s[-_ ]?videos[-_ ]?f[-_ ]?933[-_ ]?fast[-_ ]?480[-_ ]?2",
            re.IGNORECASE,
        ),
        family="kacang-933",
        modes=(
            VideoMode.TEXT_TO_VIDEO,
            VideoMode.IMAGE_TO_VIDEO,
            VideoMode.REFERENCE_TO_VIDEO,
        ),
        duration=tuple(range(4, 16)),
        resolution=("480p",),
        aspect=_COMMON_VIDEO_ASPECTS,
        reference_limits=ReferenceLimits(
            input_images=1,
            reference_images=9,
            reference_videos=3,
            reference_audios=3,
        ),
        native_audio=NativeAudio.UNSUPPORTED,
        return_last_frame=False,
        default_resolution="480p",
        default_duration_seconds=5,
        default_aspect_ratio="16:9",
    ),
    DirectVideoCapabilityProfile(
        name="gemini-omni-flash",
        pattern=re.compile(r"gemini[-_ ]?omni", re.IGNORECASE),
        family="prompt-hubs-flex",
        modes=(
            VideoMode.TEXT_TO_VIDEO,
            VideoMode.IMAGE_TO_VIDEO,
        ),
        duration=tuple(range(4, 16)),
        resolution=("720p",),
        aspect=("16:9", "9:16", "1:1", "4:3", "3:4"),
        reference_limits=ReferenceLimits(input_images=1),
        native_audio=NativeAudio.UNSUPPORTED,
        return_last_frame=False,
        default_resolution="720p",
        default_duration_seconds=5,
        default_aspect_ratio="16:9",
        size_slots=_GEMINI_OMNI_SIZE_SLOTS,
        size_field="size",
    ),
    DirectVideoCapabilityProfile(
        name="sparkcine-h3-video",
        # 覆盖 h3-video / h3-video-frames / h3-video-2..6：这一族在
        # GET {base}/models 里的 id 都带「h3 + video」，而目录条目本身不含任何
        # 时长/分辨率字段，能力只能由渠道实测合同补上。
        pattern=re.compile(r"h3[-_ ]?video", re.IGNORECASE),
        # 刻意用中性的 family：没有证据表明它就是 MiniMax H3，套上 H3 的六段式
        # 方言会把报文改成上游从没见过的形状。
        family="direct",
        # 视频能力来自站点自己的原生目录与开发者文档（GET /api/open/v1/models、
        # POST /api/open/v1/generations），不再只按 OpenAI 兼容层那一条文生视频
        # 路由推断：原生协议声明了 text_to_video / image_to_video /
        # multimodal_reference 三个 mode，参考素材最多 9 图 + 1 视频 + 3 音频。
        modes=(
            VideoMode.TEXT_TO_VIDEO,
            VideoMode.IMAGE_TO_VIDEO,
            VideoMode.REFERENCE_TO_VIDEO,
        ),
        # 站点任务记录里真实出现过的秒数是 4/5/6/10/15/20/30（自由整数）。这里按
        # 连续区间 4..30 声明，保证用户选的 30 秒不会被夹成 6 秒；「区间内每个整数
        # 都能用」这件事上游没有证实，已记在 T-256 规格的未验证清单里。
        duration=tuple(range(4, 31)),
        # 五档分辨率与六个比例都抄自站点原生目录；OpenAI 兼容层只实测过其中
        # 720p/768p/1080p，原生协议才是这套档位的权威来源。
        resolution=("480p", "720p", "768p", "960p", "1080p"),
        aspect=("16:9", "9:16", "21:9", "4:3", "1:1", "3:4"),
        reference_limits=ReferenceLimits(
            input_images=1,
            reference_images=9,
            reference_videos=1,
            reference_audios=3,
        ),
        # 原生协议目录里有 generate_audio（布尔，站点默认 true），产出的 mp4
        # 也确实带 AAC 音轨；所以音频是「可选」而不是不支持——画布上的音频
        # 开关会落成 parameters.generate_audio。
        native_audio=NativeAudio.OPTIONAL,
        return_last_frame=False,
        default_resolution="768p",
        default_duration_seconds=5,
        default_aspect_ratio="16:9",
        size_slots=_SPARKCINE_H3_VIDEO_SIZE_SLOTS,
        size_field="size",
        # 站点只认 "WxH" 入参，不认自己的回显写法（见上面的注释）。
        size_wire_values=_SPARKCINE_H3_VIDEO_SIZE_WIRE,
        # 站点严格校验请求体：metadata 包装会让提交整包 422。
        supports_request_metadata=False,
    ),
    DirectVideoCapabilityProfile(
        name="seedance-2.5",
        pattern=re.compile(r"seedance.*(?:2[._ -]?5|25)", re.IGNORECASE),
        family="direct",
        modes=(
            VideoMode.TEXT_TO_VIDEO,
            VideoMode.IMAGE_TO_VIDEO,
            VideoMode.FIRST_LAST_FRAME,
            VideoMode.REFERENCE_TO_VIDEO,
        ),
        duration=tuple(range(4, 31)),
        resolution=("480p", "720p"),
        aspect=_COMMON_VIDEO_ASPECTS,
        reference_limits=ReferenceLimits(
            input_images=2,
            reference_images=9,
            reference_videos=3,
            reference_audios=3,
        ),
        native_audio=NativeAudio.OPTIONAL,
        return_last_frame=True,
        default_resolution="720p",
        default_duration_seconds=5,
        default_aspect_ratio="16:9",
    ),
    DirectVideoCapabilityProfile(
        name="seedance",
        pattern=re.compile(r"seedance", re.IGNORECASE),
        family="direct",
        modes=(
            VideoMode.TEXT_TO_VIDEO,
            VideoMode.IMAGE_TO_VIDEO,
            VideoMode.FIRST_LAST_FRAME,
            VideoMode.REFERENCE_TO_VIDEO,
        ),
        duration=tuple(range(4, 16)),
        resolution=("480p", "720p"),
        aspect=_COMMON_VIDEO_ASPECTS,
        reference_limits=ReferenceLimits(
            input_images=2,
            reference_images=9,
            reference_videos=3,
            reference_audios=3,
        ),
        native_audio=NativeAudio.OPTIONAL,
        return_last_frame=True,
        default_resolution="720p",
        default_duration_seconds=5,
        default_aspect_ratio="16:9",
    ),
    DirectVideoCapabilityProfile(
        name="s-2.5db",
        # S-2.5db relay IDs are operator-facing names and may carry a route
        # suffix (for example ``-线路三``).  Keep the match scoped to the
        # model family so the route suffix does not fall through to the
        # generic 15-second contract.
        pattern=re.compile(r"s[-_. ]?2[._ -]?5db(?:$|[-_ ])", re.IGNORECASE),
        family="direct",
        modes=(VideoMode.TEXT_TO_VIDEO, VideoMode.IMAGE_TO_VIDEO),
        # The upstream catalog describes this route as a fixed 30-second
        # generator, so expose one legal duration instead of pretending that
        # every value from 4 to 30 is accepted.
        duration=(30,),
        resolution=("480p", "720p", "1080p"),
        aspect=("16:9", "9:16", "1:1", "4:3", "3:4"),
        reference_limits=ReferenceLimits(input_images=1),
        native_audio=NativeAudio.UNSUPPORTED,
        return_last_frame=False,
        default_resolution="720p",
        default_duration_seconds=30,
        default_aspect_ratio="16:9",
    ),
    DirectVideoCapabilityProfile(
        name="sd-2.5-720p-v1",
        # dubai3000 exposes ``sd-2.5-M-720P-v1`` as a bare ``/models`` ID and
        # publishes no capability tags there; the relay's public pricing card
        # is the only contract source (30 image / 10 audio / 10 video
        # references, native audio, selectable 5/10/15/30 seconds).  Declare
        # that contract locally — the same bridge as huabu
        # ``sd-2.0-fast-v1`` and ``S-2.5db`` — and keep the match scoped to
        # the ``-v1`` SKU so sibling variants such as ``sd-2.5-M-720p`` do
        # not inherit this richer contract.
        pattern=re.compile(
            r"^sd[-_. ]?2[._ -]?5[-_. ]?m[-_. ]?720p[-_. ]?v1(?:[-_ ].*)?$",
            re.IGNORECASE,
        ),
        family="direct",
        modes=(
            VideoMode.TEXT_TO_VIDEO,
            VideoMode.IMAGE_TO_VIDEO,
            VideoMode.REFERENCE_TO_VIDEO,
        ),
        duration=(5, 10, 15, 30),
        resolution=("720p",),
        aspect=("16:9", "9:16", "1:1", "4:3", "3:4"),
        reference_limits=ReferenceLimits(
            input_images=1,
            reference_images=30,
            reference_videos=10,
            reference_audios=10,
        ),
        native_audio=NativeAudio.OPTIONAL,
        return_last_frame=False,
        default_resolution="720p",
        default_duration_seconds=5,
        default_aspect_ratio="16:9",
        exact_canvas_modes=(
            "textToVideo",
            "imageToVideo",
            "imageReference",
            "allReference",
        ),
    ),
)
_GENERIC_DIRECT_VIDEO_PROFILE = DirectVideoCapabilityProfile(
    name="openai-video-generic",
    pattern=re.compile(r".*"),
    family="direct",
    modes=(VideoMode.TEXT_TO_VIDEO, VideoMode.IMAGE_TO_VIDEO),
    duration=tuple(range(4, 16)),
    resolution=("480p", "720p", "1080p"),
    aspect=("16:9", "9:16", "1:1", "4:3", "3:4"),
    reference_limits=ReferenceLimits(input_images=1),
    native_audio=NativeAudio.UNSUPPORTED,
    return_last_frame=False,
    default_resolution="720p",
    default_duration_seconds=5,
    default_aspect_ratio="16:9",
    reference_limits_known=frozenset({"inputImages"}),
)


def _apply_model_name_capabilities(
    profile: DirectVideoCapabilityProfile,
    upstream_model: str,
    display_name: str = "",
) -> DirectVideoCapabilityProfile:
    """Fold capability segments written into a relay model name into a profile.

    Relay stations name models two ways and either one may carry the contract:
    the opaque upstream ID (``S-2.5-10图-内置过脸``) or a human-readable display
    name (``H3多图多音频生视频15秒``, whose ID is ``minimax_h3_zm_u24``).  Both
    are read and merged, because a station may put the capability in only one
    of them.

    Only facts a name states outright are applied.  Names that declare nothing
    return the profile untouched, so every vendor-named model keeps exactly the
    contract it had before.
    """

    declared = parse_model_name_capabilities(upstream_model)
    # The display name may restate the same facts; merge the two so a count
    # written in only one of them is still honoured.  Counts take the larger
    # value and never shrink an existing contract.
    if str(display_name or "").strip() and str(display_name).strip() != str(upstream_model or "").strip():
        from_display = parse_model_name_capabilities(display_name)
        declared = _merge_name_capabilities(declared, from_display)
    if declared.is_empty:
        return profile

    changes: dict[str, Any] = {}
    limits = profile.reference_limits

    # ``10图`` is the reference-image ceiling.  It is a floor for the declared
    # contract: a name that spells out a larger number than the matched family
    # profile must win, otherwise the picker would hide slots the relay accepts.
    reference_images = limits.reference_images
    if declared.reference_images is not None:
        reference_images = max(reference_images, declared.reference_images)
    reference_videos = limits.reference_videos
    if declared.reference_videos is not None:
        reference_videos = max(reference_videos, declared.reference_videos)
    reference_audios = limits.reference_audios
    if declared.reference_audios is not None:
        reference_audios = max(reference_audios, declared.reference_audios)

    # A model that accepts reference images must be able to receive one as the
    # first frame too; the transport needs an input slot for every mode the
    # canvas is allowed to offer.
    input_images = limits.input_images
    if declared.declares_reference_mode:
        input_images = max(input_images, 1)
    if declared.declares_first_frame or declared.declares_first_last_frame:
        input_images = max(input_images, 1)
    if declared.declares_first_last_frame:
        input_images = max(input_images, 2)

    resolved_limits = ReferenceLimits(
        input_images=input_images,
        reference_images=reference_images,
        reference_videos=reference_videos,
        reference_audios=reference_audios,
    )
    if resolved_limits != limits:
        changes["reference_limits"] = resolved_limits

    # ``全能参考`` (or an explicit reference count) is a declaration that the
    # model performs reference-conditioned generation.  Only add the mode when
    # the name actually says so — a bare vendor name must not gain a mode.
    modes = list(profile.modes)
    if declared.declares_reference_mode:
        if (
            resolved_limits.reference_images > 0
            or resolved_limits.reference_videos > 0
            or resolved_limits.reference_audios > 0
        ) and VideoMode.REFERENCE_TO_VIDEO not in modes:
            modes.append(VideoMode.REFERENCE_TO_VIDEO)
    if declared.declares_first_last_frame and VideoMode.FIRST_LAST_FRAME not in modes:
        modes.append(VideoMode.FIRST_LAST_FRAME)
    if VideoMode.IMAGE_TO_VIDEO not in modes:
        modes.append(VideoMode.IMAGE_TO_VIDEO)
    if VideoMode.TEXT_TO_VIDEO not in modes:
        modes.insert(0, VideoMode.TEXT_TO_VIDEO)
    if tuple(modes) != profile.modes:
        changes["modes"] = tuple(modes)

    # A name that pins one duration (``30秒``) describes a fixed-length
    # generator; expose exactly that value instead of a range it cannot honor.
    if declared.fixed_duration_seconds is not None:
        fixed = declared.fixed_duration_seconds
        if (
            len(profile.duration) != 1 or profile.duration[0] != fixed
        ) and 1 <= fixed <= 300:
            changes["duration"] = (fixed,)
            changes["default_duration_seconds"] = fixed

    if not changes:
        return profile
    return replace(profile, **changes)


def _merge_name_capabilities(
    primary: "ModelNameCapabilities", secondary: "ModelNameCapabilities"
) -> "ModelNameCapabilities":
    """Merge two parsed names, keeping the larger count and OR-ing the flags."""

    def larger(left: int | None, right: int | None) -> int | None:
        if left is None:
            return right
        if right is None:
            return left
        return max(left, right)

    return ModelNameCapabilities(
        reference_images=larger(primary.reference_images, secondary.reference_images),
        reference_videos=larger(primary.reference_videos, secondary.reference_videos),
        reference_audios=larger(primary.reference_audios, secondary.reference_audios),
        fixed_duration_seconds=(
            primary.fixed_duration_seconds
            if primary.fixed_duration_seconds is not None
            else secondary.fixed_duration_seconds
        ),
        supports_face_handling=(
            primary.supports_face_handling or secondary.supports_face_handling
        ),
        declares_reference_mode=(
            primary.declares_reference_mode or secondary.declares_reference_mode
        ),
        declares_first_last_frame=(
            primary.declares_first_last_frame or secondary.declares_first_last_frame
        ),
        declares_first_frame=(
            primary.declares_first_frame or secondary.declares_first_frame
        ),
        matched_segments=tuple(
            dict.fromkeys([*primary.matched_segments, *secondary.matched_segments])
        ),
    )


def resolve_direct_video_profile(
    upstream_model: str,
    *,
    base_url: str = "",
    protocol: str = "openai-video",
    display_name: str = "",
) -> DirectVideoCapabilityProfile:
    """Return the most specific declared profile for an upstream model ID.

    A persisted auto-discovered capability (size slots / modes) overrides the
    built-in profile so a newly connected relay station works on first use.

    Relay stations publish the capability contract inside the model *name*
    (``S-2.5-10图-内置过脸``) because ``GET /models`` carries no capability
    fields.  The contract may live in the opaque upstream ID or in the
    human-readable ``display_name`` (``H3多图多音频生视频15秒``), so both are
    read.  Name-declared facts are applied before persisted cache evidence, so
    a name that explicitly says ``10图`` is not served by the generic
    one-input-image fallback.
    """
    value = str(upstream_model or "").strip()
    matched = next((item for item in _DIRECT_VIDEO_PROFILES if item.matches(value)), None)
    if matched is None:
        # 未知模型落到通用档案时会套用「未实测」的时长/分辨率默认值；此前完全
        # 静默。保留宽容行为（转接站目录本就模型无关），但要留下可检索的告警，
        # 便于区分「真的通用模型」与「拼错/停用的模型」（T-217）。
        logger.warning(
            "direct video model %r has no declared profile; using generic fallback "
            "(unverified duration/resolution defaults)",
            value,
        )
    profile = matched or _GENERIC_DIRECT_VIDEO_PROFILE
    name_profile = _apply_model_name_capabilities(profile, value, display_name)
    cached: dict[str, Any] = {}
    if str(base_url or "").strip():
        from .direct_video_capability_cache import (
            get_cached_capability,
            get_cached_capability_for_model,
        )

        cached = get_cached_capability(
            base_url=base_url,
            protocol=protocol,
            upstream_model=value,
        ) or get_cached_capability_for_model(
            base_url=base_url,
            upstream_model=value,
            protocol=protocol,
        )
    if not cached:
        return name_profile
    profile = name_profile
    changes: dict[str, Any] = {}
    size_slots = cached.get("sizeSlots")
    # A model-name profile is the fallback contract for sparse /models
    # entries. Metadata dimensions merged into that fallback are not proof
    # that this model accepts a fixed-size transport field.
    profile_contract_only = cached.get("source") == "models-profile-contract"
    declared_capabilities = cached.get("declaredCapabilities")
    size_slots_declared = (
        "sizeSlots" in cached
        or (
            isinstance(declared_capabilities, list)
            and "sizeSlots" in declared_capabilities
        )
    )
    if size_slots_declared and isinstance(size_slots, list) and not profile_contract_only:
        changes["size_slots"] = tuple(
            str(slot).strip().lower().replace("×", "x")
            for slot in size_slots
            if str(slot).strip()
        )
        changes["size_field"] = cached.get("sizeField") or "size"
    resolutions = cached.get("resolutionOptions")
    if isinstance(resolutions, list) and resolutions:
        normalized_resolutions = tuple(
            dict.fromkeys(
                str(item).strip().lower() for item in resolutions if str(item).strip()
            )
        )
        if normalized_resolutions:
            changes["resolution"] = normalized_resolutions
            if profile.default_resolution not in normalized_resolutions:
                changes["default_resolution"] = normalized_resolutions[0]
    aspects = cached.get("aspectRatios")
    if isinstance(aspects, list) and aspects:
        normalized_aspects = tuple(
            dict.fromkeys(str(item).strip() for item in aspects if str(item).strip())
        )
        if normalized_aspects:
            changes["aspect"] = normalized_aspects
            if profile.default_aspect_ratio not in normalized_aspects:
                changes["default_aspect_ratio"] = normalized_aspects[0]
    modes = cached.get("modes")
    cached_mode_names: set[str] = set()
    if isinstance(modes, list) and modes:
        canvas_mode_map = {
            "textToVideo": VideoMode.TEXT_TO_VIDEO,
            "imageToVideo": VideoMode.IMAGE_TO_VIDEO,
            "firstLastFrame": VideoMode.FIRST_LAST_FRAME,
            "imageReference": VideoMode.REFERENCE_TO_VIDEO,
            "allReference": VideoMode.REFERENCE_TO_VIDEO,
            "videoEdit": VideoMode.REFERENCE_TO_VIDEO,
        }
        exact_canvas_modes = tuple(
            dict.fromkeys(str(mode) for mode in modes if str(mode) in canvas_mode_map)
        )
        mapped: list[VideoMode] = []
        for mode in exact_canvas_modes:
            cached_mode_names.add(mode)
            mapped_mode = canvas_mode_map[mode]
            if mapped_mode not in mapped:
                mapped.append(mapped_mode)
        if mapped:
            changes["modes"] = tuple(mapped)
            changes["exact_canvas_modes"] = exact_canvas_modes
    duration = cached.get("durationRange")
    if isinstance(duration, list) and len(duration) == 2:
        try:
            low, high = int(duration[0]), int(duration[1])
            if 1 <= low <= high <= 300:
                declared_durations = tuple(
                    item for item in profile.duration if low <= item <= high
                )
                # ``durationRange`` 只是档案已声明档位的信封 [min, max]，
                # 不等于"区间内每个整数都可用"。用它收窄声明档位；只有档案
                # 一个档位都没声明时才展开成连续区间。曾经的无条件展开把
                # 已验证的 5/10/15/30 合约膨胀成假的 5–30 连续值。
                changes["duration"] = (
                    declared_durations
                    if declared_durations
                    else tuple(range(low, high + 1))
                )
                if not low <= profile.default_duration_seconds <= high:
                    changes["default_duration_seconds"] = low
        except (TypeError, ValueError):
            pass
    duration_options = cached.get("durationOptions")
    if duration_options is None:
        duration_options = cached.get("duration_options")
    duration_declared = "durationOptions" in {
        str(item).strip()
        for item in declared_capabilities
    } if isinstance(declared_capabilities, list) else any(
        key in cached for key in ("durationOptions", "duration_options")
    )
    if duration_declared and isinstance(duration_options, list):
        normalized_duration_options: list[int] = []
        for value in duration_options:
            try:
                parsed = int(round(float(value)))
            except (TypeError, ValueError):
                continue
            if 1 <= parsed <= 300 and parsed not in normalized_duration_options:
                normalized_duration_options.append(parsed)
        normalized_duration_options.sort()
        if normalized_duration_options:
            changes["duration"] = tuple(normalized_duration_options)
            if profile.default_duration_seconds not in normalized_duration_options:
                changes["default_duration_seconds"] = normalized_duration_options[0]
    native_audio = str(cached.get("nativeAudio") or "").strip().lower()
    if native_audio in {item.value for item in NativeAudio}:
        resolved_native_audio = NativeAudio(native_audio)
        changes["native_audio"] = resolved_native_audio
        changes["default_generate_audio"] = (
            resolved_native_audio is NativeAudio.REQUIRED
        )
    for key, field in (
        ("supportsCustomAspectRatio", "supports_custom_aspect_ratio"),
        ("supportsCustomResolution", "supports_custom_resolution"),
    ):
        value = cached.get(key)
        if isinstance(value, bool):
            changes[field] = value
    limits = cached.get("referenceLimits")
    # An empty profile declaration means "the profile did not specify this
    # dimension", not "every reference limit is explicitly zero". Treating
    # it as fully known turns sparse catalog metadata into an invalid
    # reference_to_video contract and can take down the entire model center.
    profile_known_limits = set(profile.reference_limits_known)
    cached_known_raw = cached.get("referenceLimitsKnown")
    if isinstance(cached_known_raw, list):
        cached_known_limits = {
            str(item).strip()
            for item in cached_known_raw
            if str(item).strip() in _REFERENCE_LIMIT_KEYS
        }
    elif cached.get("source") == "models-metadata":
        # v2 缓存没有证据字段，旧探测器曾把未知数量写成 1，按未知迁移。
        cached_known_limits = set()
    elif isinstance(limits, dict):
        cached_known_limits = {
            key for key in limits if key in _REFERENCE_LIMIT_KEYS
        }
    else:
        cached_known_limits = set()
    effective_known_limits = profile_known_limits | cached_known_limits
    if isinstance(limits, dict):

        def limit(name: str, fallback: int) -> int:
            value = limits.get(name)
            if value is None:
                return fallback
            try:
                return max(0, int(value))
            except (TypeError, ValueError):
                return fallback

        changes["reference_limits"] = ReferenceLimits(
            input_images=limit(
                "inputImages", profile.reference_limits.input_images
            )
            if "inputImages" in cached_known_limits
            else profile.reference_limits.input_images,
            reference_images=limit(
                "referenceImages", profile.reference_limits.reference_images
            )
            if "referenceImages" in cached_known_limits
            else profile.reference_limits.reference_images,
            reference_videos=limit(
                "referenceVideos", profile.reference_limits.reference_videos
            )
            if "referenceVideos" in cached_known_limits
            else profile.reference_limits.reference_videos,
            reference_audios=limit(
                "referenceAudios", profile.reference_limits.reference_audios
            )
            if "referenceAudios" in cached_known_limits
            else profile.reference_limits.reference_audios,
        )
        if profile.reference_limits_known or cached_known_limits != _REFERENCE_LIMIT_KEYS:
            changes["reference_limits_known"] = frozenset(effective_known_limits)
    effective_modes = changes.get("modes", profile.modes)
    effective_limits = changes.get("reference_limits", profile.reference_limits)
    input_images = effective_limits.input_images
    reference_images = effective_limits.reference_images
    reference_videos = effective_limits.reference_videos
    reference_audios = effective_limits.reference_audios
    if VideoMode.IMAGE_TO_VIDEO in effective_modes:
        input_images = max(1, input_images)
    if VideoMode.FIRST_LAST_FRAME in effective_modes:
        input_images = max(2, input_images)
    if "imageReference" in cached_mode_names and reference_images == 0:
        if "referenceImages" not in effective_known_limits:
            reference_images = 9
    if "allReference" in cached_mode_names and reference_images == 0:
        if "referenceImages" not in effective_known_limits:
            reference_images = 9
    if "videoEdit" in cached_mode_names:
        if reference_videos == 0 and "referenceVideos" not in effective_known_limits:
            reference_videos = 1
    if VideoMode.REFERENCE_TO_VIDEO in effective_modes and not any(
        (reference_images, reference_videos, reference_audios)
    ):
        if "referenceImages" not in effective_known_limits:
            # 上游只声明“支持多参考”但未给数量时，使用画布协议的本地安全包络。
            reference_images = 9
        elif "referenceVideos" not in effective_known_limits:
            reference_videos = 1
    reconciled_limits = ReferenceLimits(
        input_images=input_images,
        reference_images=reference_images,
        reference_videos=reference_videos,
        reference_audios=reference_audios,
    )
    if reconciled_limits != effective_limits:
        changes["reference_limits"] = reconciled_limits
    if not changes:
        return profile
    return replace(profile, **changes)
