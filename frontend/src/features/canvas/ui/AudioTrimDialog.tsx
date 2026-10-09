// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Loader2, Pause, Play } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import {
  getCachedAudioPeaks,
  loadAudioPeaks,
  PEAK_BUCKETS_PER_SEC,
} from '@/features/canvas/compose/audioPeaks';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';

/** 最短可保留片段：比它再短就没有裁剪意义，拖拽时也用它做下限。 */
export const MIN_TRIM_MS = 200;
/** 峰值低于该比例视为静音；峰值数组已按最大值归一化。 */
const SILENCE_RATIO = 0.08;
/** 自动裁剪时首尾各留一点余量，避免把起音的包络切掉。 */
const SILENCE_PAD_MS = 150;
const ACCENT = 'rgb(56, 189, 248)';
const TRACK = 'rgba(255, 255, 255, 0.18)';

export type TrimRangeMs = { startMs: number; endMs: number };

/**
 * 从峰值里找出有声区间（跳过首尾静音）。全静音 / 没有峰值 / 时长未知时返回整段，
 * 让「一键裁剪」退化成不做任何裁剪，而不是把素材裁没。
 */
export function audibleRangeMs(
  peaks: Float32Array | null,
  durationMs: number,
  options: { silenceRatio?: number; padMs?: number } = {},
): TrimRangeMs {
  const full: TrimRangeMs = { startMs: 0, endMs: Math.max(0, Math.round(durationMs)) };
  if (!peaks || peaks.length === 0 || !(durationMs > 0)) return full;
  const silenceRatio = options.silenceRatio ?? SILENCE_RATIO;
  const padMs = options.padMs ?? SILENCE_PAD_MS;
  let first = -1;
  let last = -1;
  for (let i = 0; i < peaks.length; i += 1) {
    if (peaks[i] > silenceRatio) {
      if (first < 0) first = i;
      last = i;
    }
  }
  if (first < 0) return full;
  const msPerBucket = durationMs / peaks.length;
  const startMs = Math.max(0, first * msPerBucket - padMs);
  const endMs = Math.min(durationMs, (last + 1) * msPerBucket + padMs);
  if (endMs - startMs < MIN_TRIM_MS) return full;
  return { startMs: Math.round(startMs), endMs: Math.round(endMs) };
}

/** 把选区收进 [0, durationMs]，并保证不短于 minMs（先向右借，右边到底再向左）。 */
export function clampTrimRangeMs(
  range: TrimRangeMs,
  durationMs: number,
  minMs: number = MIN_TRIM_MS,
): TrimRangeMs {
  const duration = Math.max(0, Math.round(durationMs));
  let startMs = Math.min(Math.max(0, Math.round(range.startMs)), duration);
  let endMs = Math.min(Math.max(0, Math.round(range.endMs)), duration);
  if (endMs - startMs < minMs) {
    endMs = Math.min(duration, startMs + minMs);
    startMs = Math.max(0, endMs - minMs);
  }
  return { startMs, endMs };
}

export function formatTrimClock(ms: number): string {
  const safe = Math.max(0, ms) / 1000;
  const mm = Math.floor(safe / 60)
    .toString()
    .padStart(2, '0');
  const ss = Math.floor(safe % 60)
    .toString()
    .padStart(2, '0');
  const tenth = Math.floor((safe % 1) * 10);
  return `${mm}:${ss}.${tenth}`;
}

type AudioTrimDialogProps = {
  open: boolean;
  src: string;
  durationMs?: number | null;
  pending?: boolean;
  onOpenChange: (open: boolean) => void;
  onConfirm: (range: { startSeconds: number; durationSeconds: number }) => void;
};

export function AudioTrimDialog({
  open,
  src,
  durationMs,
  pending = false,
  onOpenChange,
  onConfirm,
}: AudioTrimDialogProps) {
  const { t } = useTranslation();
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const trackRef = useRef<HTMLDivElement>(null);
  const audioRef = useRef<HTMLAudioElement>(null);
  const [peaks, setPeaks] = useState<Float32Array | null>(() => getCachedAudioPeaks(src));
  const [resolvedDurationMs, setResolvedDurationMs] = useState(
    typeof durationMs === 'number' && durationMs > 0 ? durationMs : 0,
  );
  const [range, setRange] = useState<TrimRangeMs>({ startMs: 0, endMs: 0 });
  const [previewing, setPreviewing] = useState(false);
  /** 试听播放头位置；null = 没在播（不画线）。 */
  const [playheadMs, setPlayheadMs] = useState<number | null>(null);
  const dragEdgeRef = useRef<'start' | 'end' | null>(null);

  // 解码峰值：同一 src 在播放器里通常已经解过，这里直接命中缓存。
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    const cached = getCachedAudioPeaks(src);
    if (cached) {
      setPeaks(cached);
      return;
    }
    void (async () => {
      try {
        const loaded = await loadAudioPeaks(src);
        if (!cancelled) setPeaks(loaded);
      } catch (err) {
        // 解码失败只影响「自动跳过静音」与波形显示，裁剪本身仍可用。
        console.warn('[audio-trim] decode failed', err);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [open, src]);

  // 每次打开都重置选区：优先自动跳过首尾静音，再退到整段。
  useEffect(() => {
    if (!open) {
      setPreviewing(false);
      setPlayheadMs(null);
      return;
    }
    setRange(audibleRangeMs(getCachedAudioPeaks(src) ?? peaks, resolvedDurationMs));
    setPlayheadMs(null);
    // peaks / 时长在解码完成后会各自触发一次重置，避免用半截数据定选区。
  }, [open, src, peaks, resolvedDurationMs]);

  // 试听：只在选区里播放，越过终点自动停；播放头跟着当前时间走。
  useEffect(() => {
    if (!previewing) return;
    const el = audioRef.current;
    if (!el) return;
    let raf = 0;
    const tick = () => {
      const currentMs = el.currentTime * 1000;
      if (currentMs >= range.endMs) {
        el.pause();
        setPreviewing(false);
        setPlayheadMs(null);
        return;
      }
      setPlayheadMs(currentMs);
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [previewing, range.endMs]);

  const togglePreview = useCallback(() => {
    const el = audioRef.current;
    if (!el) return;
    if (previewing) {
      el.pause();
      setPreviewing(false);
      setPlayheadMs(null);
      return;
    }
    // 从播放头接着播（点过波形时），否则从选区开头播。
    const resumeMs =
      playheadMs !== null && playheadMs >= range.startMs && playheadMs < range.endMs
        ? playheadMs
        : range.startMs;
    el.currentTime = resumeMs / 1000;
    setPlayheadMs(resumeMs);
    void el
      .play()
      .then(() => setPreviewing(true))
      .catch(() => {
        setPreviewing(false);
        setPlayheadMs(null);
      });
  }, [playheadMs, previewing, range.endMs, range.startMs]);

  // 波形绘制：选区内用强调色，选区外压暗。
  useEffect(() => {
    const canvas = canvasRef.current;
    const track = trackRef.current;
    if (!canvas || !track) return;
    const draw = () => {
      const dpr = window.devicePixelRatio || 1;
      const cssW = track.clientWidth;
      const cssH = track.clientHeight;
      if (cssW <= 0 || cssH <= 0) return;
      if (
        canvas.width !== Math.round(cssW * dpr) ||
        canvas.height !== Math.round(cssH * dpr)
      ) {
        canvas.width = Math.round(cssW * dpr);
        canvas.height = Math.round(cssH * dpr);
        canvas.style.width = `${cssW}px`;
        canvas.style.height = `${cssH}px`;
      }
      const ctx = canvas.getContext('2d');
      if (!ctx) return;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, cssW, cssH);
      const midY = cssH / 2;
      const maxBar = cssH * 0.8;
      const totalMs = resolvedDurationMs || 0;
      const step = 3;
      for (let x = 0; x <= cssW; x += step) {
        const ratio = cssW > 0 ? x / cssW : 0;
        let amp = 0.12;
        if (peaks && peaks.length > 0) {
          amp = peaks[Math.min(peaks.length - 1, Math.floor(ratio * peaks.length))];
        }
        const h = Math.max(2, amp * maxBar);
        const ms = ratio * totalMs;
        const inRange = ms >= range.startMs && ms <= range.endMs;
        ctx.fillStyle = inRange ? ACCENT : TRACK;
        ctx.beginPath();
        ctx.roundRect(x, midY - h / 2, 2, h, 1);
        ctx.fill();
      }
    };
    draw();
    const observer = new ResizeObserver(draw);
    observer.observe(track);
    return () => observer.disconnect();
  }, [peaks, range.endMs, range.startMs, resolvedDurationMs]);

  const msFromClientX = useCallback(
    (clientX: number): number => {
      const track = trackRef.current;
      if (!track) return 0;
      const rect = track.getBoundingClientRect();
      if (rect.width <= 0) return 0;
      const ratio = (clientX - rect.left) / rect.width;
      return Math.min(resolvedDurationMs, Math.max(0, ratio * resolvedDurationMs));
    },
    [resolvedDurationMs],
  );

  const seekTo = useCallback(
    (ms: number) => {
      const clamped = Math.min(resolvedDurationMs, Math.max(0, ms));
      const el = audioRef.current;
      if (el) el.currentTime = clamped / 1000;
      setPlayheadMs(clamped);
    },
    [resolvedDurationMs],
  );

  const handlePointerDown = useCallback(
    (edge: 'start' | 'end') => (event: React.PointerEvent<HTMLDivElement>) => {
      event.stopPropagation();
      dragEdgeRef.current = edge;
      (event.currentTarget as HTMLDivElement).setPointerCapture(event.pointerId);
    },
    [],
  );

  const handlePointerMove = useCallback(
    (event: React.PointerEvent<HTMLDivElement>) => {
      const edge = dragEdgeRef.current;
      if (!edge) return;
      event.stopPropagation();
      const ms = msFromClientX(event.clientX);
      setRange((current) =>
        clampTrimRangeMs(
          edge === 'start'
            ? { startMs: ms, endMs: current.endMs }
            : { startMs: current.startMs, endMs: ms },
          resolvedDurationMs,
        ),
      );
    },
    [msFromClientX, resolvedDurationMs],
  );

  const handlePointerUp = useCallback((event: React.PointerEvent<HTMLDivElement>) => {
    dragEdgeRef.current = null;
    try {
      (event.currentTarget as HTMLDivElement).releasePointerCapture(event.pointerId);
    } catch {
      /* noop */
    }
  }, []);

  const lengthMs = Math.max(0, range.endMs - range.startMs);
  const canConfirm = resolvedDurationMs > 0 && lengthMs >= MIN_TRIM_MS && !pending;
  const leftPercent = useMemo(
    () => (resolvedDurationMs > 0 ? (range.startMs / resolvedDurationMs) * 100 : 0),
    [range.startMs, resolvedDurationMs],
  );
  const rightPercent = useMemo(
    () => (resolvedDurationMs > 0 ? (range.endMs / resolvedDurationMs) * 100 : 100),
    [range.endMs, resolvedDurationMs],
  );
  // 播放头只在选区内显示：拖手柄把选区挪走后，旧的播放头不该还留在外面。
  const playheadInRange =
    playheadMs !== null && playheadMs >= range.startMs && playheadMs <= range.endMs;
  const playheadPercent = useMemo(
    () =>
      playheadInRange && playheadMs !== null && resolvedDurationMs > 0
        ? (playheadMs / resolvedDurationMs) * 100
        : null,
    [playheadInRange, playheadMs, resolvedDurationMs],
  );

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        // 裁剪请求在飞时不许关闭，避免用户以为没提交又点一次。
        if (pending && !next) return;
        onOpenChange(next);
      }}
    >
      <DialogContent className="gap-4 overflow-hidden rounded-2xl border border-white/8 bg-background/68 p-7 shadow-none backdrop-blur-3xl sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>{t('node.audio.trim.title')}</DialogTitle>
        </DialogHeader>

        <p className="text-xs leading-5 text-muted-foreground">
          {t('node.audio.trim.hint')}
        </p>

        <div
          ref={trackRef}
          data-testid="audio-trim-track"
          className="relative h-24 w-full touch-none select-none overflow-hidden rounded-lg bg-black/30"
          onPointerDown={(event) => {
            // 点波形定位：先只移动播放头，按「试听」从这里开始播。
            event.stopPropagation();
            seekTo(msFromClientX(event.clientX));
          }}
        >
          <canvas ref={canvasRef} className="block h-full w-full" />
          {/* 选区高亮：只描两条边界，中间交给波形本色 */}
          <div
            className="pointer-events-none absolute inset-y-0 border-y border-[rgb(56,189,248)]/70 bg-[rgb(56,189,248)]/8"
            style={{ left: `${leftPercent}%`, right: `${100 - rightPercent}%` }}
          />
          {/* 试听播放头：跟着 currentTime 实时走 */}
          {playheadPercent !== null && (
            <div
              data-testid="audio-trim-playhead"
              className="pointer-events-none absolute inset-y-0 z-20 w-px bg-white/85 shadow-[0_0_6px_rgba(255,255,255,0.55)]"
              style={{ left: `${playheadPercent}%` }}
            />
          )}
          <div
            role="slider"
            aria-label={t('node.audio.trim.start')}
            aria-valuemin={0}
            aria-valuemax={resolvedDurationMs}
            aria-valuenow={range.startMs}
            tabIndex={0}
            className="nodrag absolute inset-y-0 z-10 -ml-2 w-4 cursor-ew-resize"
            style={{ left: `${leftPercent}%` }}
            onPointerDown={handlePointerDown('start')}
            onPointerMove={handlePointerMove}
            onPointerUp={handlePointerUp}
          >
            <div className="absolute inset-y-0 left-1/2 w-0.5 -translate-x-1/2 bg-[rgb(56,189,248)]" />
            <div className="absolute left-1/2 top-0 size-2.5 -translate-x-1/2 rounded-sm bg-[rgb(56,189,248)]" />
          </div>
          <div
            role="slider"
            aria-label={t('node.audio.trim.end')}
            aria-valuemin={0}
            aria-valuemax={resolvedDurationMs}
            aria-valuenow={range.endMs}
            tabIndex={0}
            className="nodrag absolute inset-y-0 z-10 -ml-2 w-4 cursor-ew-resize"
            style={{ left: `${rightPercent}%` }}
            onPointerDown={handlePointerDown('end')}
            onPointerMove={handlePointerMove}
            onPointerUp={handlePointerUp}
          >
            <div className="absolute inset-y-0 left-1/2 w-0.5 -translate-x-1/2 bg-[rgb(56,189,248)]" />
            <div className="absolute left-1/2 top-0 size-2.5 -translate-x-1/2 rounded-sm bg-[rgb(56,189,248)]" />
          </div>
        </div>

        <div className="grid grid-cols-3 gap-3 text-xs">
          <div className="rounded-lg bg-white/4 px-3 py-2">
            <div className="text-muted-foreground">{t('node.audio.trim.start')}</div>
            <div className="mt-0.5 font-medium tabular-nums">
              {formatTrimClock(range.startMs)}
            </div>
          </div>
          <div className="rounded-lg bg-white/4 px-3 py-2">
            <div className="text-muted-foreground">{t('node.audio.trim.end')}</div>
            <div className="mt-0.5 font-medium tabular-nums">
              {formatTrimClock(range.endMs)}
            </div>
          </div>
          <div className="rounded-lg bg-white/4 px-3 py-2">
            <div className="text-muted-foreground">{t('node.audio.trim.length')}</div>
            <div className="mt-0.5 font-medium tabular-nums">{formatTrimClock(lengthMs)}</div>
          </div>
        </div>

        <audio
          ref={audioRef}
          src={src}
          preload="metadata"
          className="hidden"
          onLoadedMetadata={(event) => {
            const value = event.currentTarget.duration;
            if (Number.isFinite(value) && value > 0) {
              setResolvedDurationMs(Math.round(value * 1000));
            }
          }}
          onEnded={() => {
            // 文件真实时长可能短于记录时长，此时选区终点还没到就播完了，
            // 播放头必须一起收掉，不能留在屏幕上。
            setPreviewing(false);
            setPlayheadMs(null);
          }}
        />

        <DialogFooter className="gap-2 sm:justify-between">
          <div className="flex items-center gap-2">
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={togglePreview}
              disabled={resolvedDurationMs <= 0}
            >
              {previewing ? (
                <Pause className="mr-1.5 size-3.5" />
              ) : (
                <Play className="mr-1.5 size-3.5" />
              )}
              {t('node.audio.trim.preview')}
            </Button>
            <span
              className="text-xs tabular-nums text-muted-foreground"
              aria-live="off"
            >
              {formatTrimClock(playheadInRange && playheadMs !== null ? playheadMs : range.startMs)}
            </span>
          </div>
          <div className="flex items-center gap-2">
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => onOpenChange(false)}
              disabled={pending}
            >
              {t('common.cancel')}
            </Button>
            <Button
              type="button"
              size="sm"
              disabled={!canConfirm}
              onClick={() =>
                onConfirm({
                  startSeconds: range.startMs / 1000,
                  durationSeconds: lengthMs / 1000,
                })
              }
            >
              {pending && <Loader2 className="mr-1.5 size-3.5 animate-spin" />}
              {t('node.audio.trim.confirm')}
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default AudioTrimDialog;

/** 峰值密度导出给测试用，避免测试与实现各写一份常量。 */
export const AUDIO_TRIM_BUCKETS_PER_SEC = PEAK_BUCKETS_PER_SEC;
