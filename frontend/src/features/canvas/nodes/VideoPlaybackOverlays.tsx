// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ChangeEvent,
  type PointerEvent as ReactPointerEvent,
} from "react";
import {
  ArrowUp,
  Camera,
  Loader2,
  Pause,
  Play,
  RotateCcw,
  Sparkles,
  Square,
  Volume2,
  VolumeX,
  X as XIcon,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import { CreditCostPill } from "@/components/credits/credit-visual";
import {
  CANVAS_NODE_TOOLBAR_PILL_CLASS,
} from "@/features/canvas/ui/nodeFrameStyles";
import {
  VideoFrameCaptureMenu,
  type VideoFrameCaptureMode,
} from "@/features/canvas/ui/VideoFrameCaptureMenu";
import {
  NODE_CREDIT_PILL_FLAT_CLASS,
  NODE_GENERATE_BUTTON_BASE_CLASS,
  NODE_GENERATE_BUTTON_DISABLED_CLASS,
  NODE_GENERATE_BUTTON_ENABLED_CLASS,
} from "@/features/canvas/ui/nodeControlStyles";

//
// 替代 <video controls>：libtv 风格的浮层（底部一条）。订阅原生 <video>
// 的 play/pause/timeupdate/durationchange/volumechange，写回时直接操作元素，
// 由事件驱动 state 单向同步。隐藏时机：默认显示 0.85 透明度 + hover 加深，
// 不做自动隐藏，避免画布上看不到「这个视频还能控制」。

interface VideoPlayerControlsProps {
  videoEl: HTMLVideoElement | null;
  isCapturingFrame: boolean;
  onCapture: (mode: VideoFrameCaptureMode) => void;
  onPlaybackIntent: (playing: boolean) => void;
}

function formatTime(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "0:00";
  const total = Math.floor(seconds);
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${s.toString().padStart(2, "0")}`;
}

export function VideoPlayerControls({
  videoEl,
  isCapturingFrame,
  onCapture,
  onPlaybackIntent,
}: VideoPlayerControlsProps) {
  const { t } = useTranslation();
  const [isPlaying, setIsPlaying] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [isMuted, setIsMuted] = useState(false);
  const [isHoveringFrame, setIsHoveringFrame] = useState(false);

  useEffect(() => {
    if (!videoEl) return;
    const syncAll = () => {
      setIsPlaying(!videoEl.paused);
      setCurrentTime(videoEl.currentTime);
      setDuration(Number.isFinite(videoEl.duration) ? videoEl.duration : 0);
      setIsMuted(videoEl.muted);
    };
    syncAll();
    const onPlay = () => setIsPlaying(true);
    const onPause = () => setIsPlaying(false);
    const onTime = () => setCurrentTime(videoEl.currentTime);
    const onDur = () => {
      setDuration(Number.isFinite(videoEl.duration) ? videoEl.duration : 0);
    };
    const onVol = () => setIsMuted(videoEl.muted);
    videoEl.addEventListener("play", onPlay);
    videoEl.addEventListener("pause", onPause);
    videoEl.addEventListener("timeupdate", onTime);
    videoEl.addEventListener("durationchange", onDur);
    videoEl.addEventListener("loadedmetadata", onDur);
    videoEl.addEventListener("volumechange", onVol);
    return () => {
      videoEl.removeEventListener("play", onPlay);
      videoEl.removeEventListener("pause", onPause);
      videoEl.removeEventListener("timeupdate", onTime);
      videoEl.removeEventListener("durationchange", onDur);
      videoEl.removeEventListener("loadedmetadata", onDur);
      videoEl.removeEventListener("volumechange", onVol);
    };
  }, [videoEl]);

  const togglePlay = useCallback(() => {
    if (!videoEl) return;
    if (videoEl.paused) {
      void videoEl
        .play()
        .then(() => onPlaybackIntent(true))
        .catch(() => undefined);
    } else {
      videoEl.pause();
      onPlaybackIntent(false);
    }
  }, [onPlaybackIntent, videoEl]);

  const toggleMute = useCallback(() => {
    if (!videoEl) return;
    videoEl.muted = !videoEl.muted;
  }, [videoEl]);

  const onSeek = useCallback(
    (event: ChangeEvent<HTMLInputElement>) => {
      if (!videoEl) return;
      const next = Number(event.target.value);
      if (!Number.isFinite(next)) return;
      videoEl.currentTime = next;
      setCurrentTime(next);
    },
    [videoEl],
  );

  // 进度百分比（用作 range 背景的渐变锚点）。
  const progressPct =
    duration > 0 ? Math.min(100, (currentTime / duration) * 100) : 0;
  const sliderBg = `linear-gradient(to right, rgb(var(--accent-rgb)) 0%, rgb(var(--accent-rgb)) ${progressPct}%, rgba(255,255,255,0.18) ${progressPct}%, rgba(255,255,255,0.18) 100%)`;

  return (
    <div className="nodrag absolute inset-x-0 bottom-0 z-20 flex items-center gap-2.5 bg-gradient-to-t from-black/75 via-black/45 to-transparent px-3 pb-2 pt-6 text-text-dark">
      <button
        type="button"
        onClick={(event) => {
          // 唯一的播放/暂停入口:阻止冒泡,避免点它时把节点也选中。
          event.stopPropagation();
          togglePlay();
        }}
        className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-text-dark/90 transition-colors hover:bg-white/[0.12] hover:text-text-dark"
        title={
          isPlaying
            ? t("node.videoNode.player.pause", { defaultValue: "暂停" })
            : t("node.videoNode.player.play", { defaultValue: "播放" })
        }
      >
        {isPlaying ? (
          <Pause className="h-4 w-4" />
        ) : (
          <Play className="h-4 w-4" fill="currentColor" />
        )}
      </button>

      <span className="shrink-0 text-[11px] tabular-nums text-text-dark/85">
        {formatTime(currentTime)}
      </span>

      <input
        type="range"
        min={0}
        max={duration > 0 ? duration : 0}
        step={0.05}
        value={currentTime}
        onChange={onSeek}
        onMouseDown={(event) => event.stopPropagation()}
        className="video-player-scrubber h-1 min-w-0 flex-1 cursor-pointer appearance-none rounded-full"
        style={{ background: sliderBg }}
      />

      <span className="shrink-0 text-[11px] tabular-nums text-text-dark/85">
        {formatTime(duration)}
      </span>

      <button
        type="button"
        onClick={toggleMute}
        className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-text-dark/90 transition-colors hover:bg-white/[0.12] hover:text-text-dark"
        title={
          isMuted
            ? t("node.videoNode.player.unmute", { defaultValue: "取消静音" })
            : t("node.videoNode.player.mute", { defaultValue: "静音" })
        }
      >
        {isMuted ? (
          <VolumeX className="h-4 w-4" />
        ) : (
          <Volume2 className="h-4 w-4" />
        )}
      </button>

      <div
        className="relative shrink-0"
        onMouseEnter={() => setIsHoveringFrame(true)}
        onMouseLeave={() => setIsHoveringFrame(false)}
      >
        <button
          type="button"
          disabled={isCapturingFrame}
          onClick={() => onCapture("current")}
          title={t("node.videoNode.frame.captureCurrent")}
          className={`inline-flex h-7 w-7 items-center justify-center rounded-md transition-colors ${
            isCapturingFrame
              ? "cursor-not-allowed text-text-muted/60"
              : "text-text-dark/90 hover:bg-white/[0.12] hover:text-text-dark"
          }`}
        >
          {isCapturingFrame ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <Camera className="h-4 w-4" />
          )}
        </button>

        {isHoveringFrame && !isCapturingFrame && (
          <VideoFrameCaptureMenu onCapture={onCapture} />
        )}
      </div>
    </div>
  );
}

// --- subtitle erase: box overlay ------------------------------------------- //

interface DisplayedRect {
  left: number;
  top: number;
  width: number;
  height: number;
}

interface SubtitleEraseBoxOverlayProps {
  box: { x: number; y: number; width: number; height: number } | null;
  drag: { x0: number; y0: number; x1: number; y1: number } | null;
  disabled: boolean;
  getDisplayedRect: (containerW: number, containerH: number) => DisplayedRect;
  onDragStart: (start: {
    x0: number;
    y0: number;
    x1: number;
    y1: number;
  }) => void;
  onDragMove: (next: { x1: number; y1: number }) => void;
  onDragEnd: (
    final: { x: number; y: number; width: number; height: number } | null,
  ) => void;
}

export function SubtitleEraseBoxOverlay({
  box,
  drag,
  disabled,
  getDisplayedRect,
  onDragStart,
  onDragMove,
  onDragEnd,
}: SubtitleEraseBoxOverlayProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [containerSize, setContainerSize] = useState<{ w: number; h: number }>({
    w: 0,
    h: 0,
  });

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (!entry) return;
      setContainerSize({
        w: entry.contentRect.width,
        h: entry.contentRect.height,
      });
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const displayed = getDisplayedRect(containerSize.w, containerSize.h);

  const toNormalized = useCallback(
    (clientX: number, clientY: number) => {
      const el = containerRef.current;
      if (!el) return { nx: 0, ny: 0 };
      const rect = el.getBoundingClientRect();
      const localX = clientX - rect.left - displayed.left;
      const localY = clientY - rect.top - displayed.top;
      const nx = displayed.width > 0 ? localX / displayed.width : 0;
      const ny = displayed.height > 0 ? localY / displayed.height : 0;
      return {
        nx: Math.max(0, Math.min(1, nx)),
        ny: Math.max(0, Math.min(1, ny)),
      };
    },
    [displayed.height, displayed.left, displayed.top, displayed.width],
  );

  const handlePointerDown = useCallback(
    (event: ReactPointerEvent<HTMLDivElement>) => {
      if (disabled) return;
      event.preventDefault();
      event.stopPropagation();
      event.currentTarget.setPointerCapture(event.pointerId);
      const { nx, ny } = toNormalized(event.clientX, event.clientY);
      onDragStart({ x0: nx, y0: ny, x1: nx, y1: ny });
    },
    [disabled, onDragStart, toNormalized],
  );

  const handlePointerMove = useCallback(
    (event: ReactPointerEvent<HTMLDivElement>) => {
      if (disabled || !drag) return;
      const { nx, ny } = toNormalized(event.clientX, event.clientY);
      onDragMove({ x1: nx, y1: ny });
    },
    [disabled, drag, onDragMove, toNormalized],
  );

  const handlePointerUp = useCallback(
    (event: ReactPointerEvent<HTMLDivElement>) => {
      if (disabled || !drag) return;
      try {
        event.currentTarget.releasePointerCapture(event.pointerId);
      } catch {
        // pointer may not have been captured
      }
      const x = Math.min(drag.x0, drag.x1);
      const y = Math.min(drag.y0, drag.y1);
      const width = Math.abs(drag.x1 - drag.x0);
      const height = Math.abs(drag.y1 - drag.y0);
      if (width < 0.01 || height < 0.01) {
        onDragEnd(null);
        return;
      }
      onDragEnd({ x, y, width, height });
    },
    [disabled, drag, onDragEnd],
  );

  const effective = drag
    ? {
        x: Math.min(drag.x0, drag.x1),
        y: Math.min(drag.y0, drag.y1),
        width: Math.abs(drag.x1 - drag.x0),
        height: Math.abs(drag.y1 - drag.y0),
      }
    : box;

  return (
    <div
      ref={containerRef}
      className="nodrag absolute inset-0 z-30"
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={handlePointerUp}
      onClick={(event) => event.stopPropagation()}
      style={{ cursor: disabled ? "not-allowed" : "crosshair" }}
    >
      {effective && effective.width > 0 && effective.height > 0 && (
        <div
          className="pointer-events-none absolute border-2 border-[rgb(var(--accent-rgb))] bg-[rgb(var(--accent-rgb)/0.15)]"
          style={{
            left: displayed.left + effective.x * displayed.width,
            top: displayed.top + effective.y * displayed.height,
            width: effective.width * displayed.width,
            height: effective.height * displayed.height,
          }}
        />
      )}
    </div>
  );
}

// --- subtitle erase: ops panel --------------------------------------------- //

interface SubtitleEraseOpsPanelProps {
  mode: "smart" | "box";
  isErasing: boolean;
  hasBox: boolean;
  onExit: () => void;
  onResetBox: () => void;
  onSubmit: () => void;
}

export function SubtitleEraseOpsPanel({
  mode,
  isErasing,
  hasBox,
  onExit,
  onResetBox,
  onSubmit,
}: SubtitleEraseOpsPanelProps) {
  const { t } = useTranslation();
  const submitDisabled = isErasing || (mode === "box" && !hasBox);
  const labelKey =
    mode === "box"
      ? "nodeToolbar.video.subtitleRemovalBox"
      : "nodeToolbar.video.subtitleRemovalSmart";
  const icon =
    mode === "box" ? (
      <Square className="h-3.5 w-3.5 shrink-0 text-text-muted" />
    ) : (
      <Sparkles className="h-3.5 w-3.5 shrink-0 text-text-muted" />
    );

  return (
    <div className={`flex min-w-[420px] max-w-[calc(100vw-32px)] items-center gap-2 ${CANVAS_NODE_TOOLBAR_PILL_CLASS}`}>
      <button
        type="button"
        onClick={onExit}
        title={t("node.videoNode.subtitleErase.exit")}
        className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-bg-dark/70 text-text-muted transition-colors hover:bg-bg-dark hover:text-text-dark"
      >
        <XIcon className="h-4 w-4" />
      </button>

      <div className="flex min-w-0 flex-1 items-center gap-1.5 px-2 text-xs text-text-dark">
        {icon}
        <span className="truncate font-medium">{t(labelKey)}</span>
      </div>

      {mode === "box" && (
        <button
          type="button"
          onClick={onResetBox}
          title={t("node.videoNode.subtitleErase.tools.reset")}
          className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded px-1 text-text-dark/72 transition-colors hover:text-text-dark"
        >
          <RotateCcw className="h-4 w-4" />
        </button>
      )}

      <CreditCostPill
        display="0"
        disabled={submitDisabled}
        className={NODE_CREDIT_PILL_FLAT_CLASS}
      />

      <button
        type="button"
        disabled={submitDisabled}
        onClick={onSubmit}
        title={t("node.videoNode.subtitleErase.submit")}
        className={`${NODE_GENERATE_BUTTON_BASE_CLASS} shrink-0 ${
          submitDisabled
            ? NODE_GENERATE_BUTTON_DISABLED_CLASS
            : NODE_GENERATE_BUTTON_ENABLED_CLASS
        }`}
      >
        {isErasing ? (
          <Loader2 className="h-4 w-4 animate-spin" />
        ) : (
          <ArrowUp className="h-4 w-4" />
        )}
      </button>
    </div>
  );
}
