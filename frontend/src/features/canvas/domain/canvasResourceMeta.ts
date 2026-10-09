// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
/**
 * Facts about a produced media file, as reported by the backend that wrote it.
 *
 * These describe the artifact itself (bytes on disk, container type, real
 * playback length) rather than the request that asked for it — a video node's
 * top-level `durationSec` is the *requested* length the panel shows, so a
 * provider that ignores the duration knob makes the two disagree visibly
 * instead of one silently overwriting the other.
 *
 * Every field is optional: an unknown fact is omitted, never written as zero.
 * Both the live-canvas nodes and the generation-history records carry this
 * shape, so it is parsed in one place.
 */
export interface CanvasResourceMeta {
  /** Size on disk in bytes. */
  byteSize?: number;
  mimeType?: string;
  /** Real container length in seconds (video/audio). */
  durationSec?: number;
  /** Intrinsic pixel dimensions. */
  width?: number;
  height?: number;
  kind?: string;
}

function positiveNumber(value: unknown): number | undefined {
  if (typeof value === 'number' && Number.isFinite(value) && value > 0) return value;
  if (typeof value === 'string' && value.trim()) {
    const parsed = Number(value);
    if (Number.isFinite(parsed) && parsed > 0) return parsed;
  }
  return undefined;
}

function nonEmptyString(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim() ? value.trim() : undefined;
}

/**
 * Normalize a `resourceMeta` payload from either source. Returns null when the
 * value carries no usable fact, so callers render nothing instead of an empty
 * badge (and so a card can tell "no metadata" from "metadata says zero").
 */
export function parseResourceMeta(value: unknown): CanvasResourceMeta | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const raw = value as Record<string, unknown>;
  const meta: CanvasResourceMeta = {};
  const byteSize = positiveNumber(raw.byteSize ?? raw.byte_size);
  if (byteSize !== undefined) meta.byteSize = Math.round(byteSize);
  const durationSec = positiveNumber(raw.durationSec ?? raw.duration_sec);
  if (durationSec !== undefined) meta.durationSec = durationSec;
  const width = positiveNumber(raw.width ?? raw.widthPx);
  if (width !== undefined) meta.width = Math.round(width);
  const height = positiveNumber(raw.height ?? raw.heightPx);
  if (height !== undefined) meta.height = Math.round(height);
  const mimeType = nonEmptyString(raw.mimeType ?? raw.mime_type);
  if (mimeType !== undefined) meta.mimeType = mimeType;
  const kind = nonEmptyString(raw.kind);
  if (kind !== undefined) meta.kind = kind;
  return Object.keys(meta).length > 0 ? meta : null;
}

const BYTE_UNITS = ['B', 'KB', 'MB', 'GB', 'TB'] as const;

/** Human byte size with one decimal above KB; null for a missing/zero size. */
export function formatByteSize(byteSize: number | undefined): string | null {
  if (typeof byteSize !== 'number' || !Number.isFinite(byteSize) || byteSize <= 0) {
    return null;
  }
  let value = byteSize;
  let unit = 0;
  while (value >= 1024 && unit < BYTE_UNITS.length - 1) {
    value /= 1024;
    unit += 1;
  }
  const rounded = unit === 0 ? Math.round(value) : Math.round(value * 10) / 10;
  return `${rounded} ${BYTE_UNITS[unit]}`;
}

/** Seconds as `m:ss`, or null when the length is unknown. */
export function formatDurationSec(durationSec: number | undefined): string | null {
  if (typeof durationSec !== 'number' || !Number.isFinite(durationSec) || durationSec <= 0) {
    return null;
  }
  const total = Math.round(durationSec);
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  return `${minutes}:${seconds.toString().padStart(2, '0')}`;
}

/**
 * The card's one-line fact strip: dimensions, size, duration — whichever the
 * backend actually reported. Null when it reported none, so callers can skip
 * the badge entirely.
 */
export function resourceMetaSummary(meta: CanvasResourceMeta | null | undefined): string | null {
  if (!meta) return null;
  const parts: string[] = [];
  if (meta.width && meta.height) parts.push(`${meta.width}×${meta.height}`);
  const size = formatByteSize(meta.byteSize);
  if (size) parts.push(size);
  const duration = formatDurationSec(meta.durationSec);
  if (duration) parts.push(duration);
  return parts.length > 0 ? parts.join(' · ') : null;
}

/**
 * 后端对一次视频任务的时长对账：请求的秒数 vs 容器实测的秒数。
 *
 * 与 `resourceMeta`（只描述产物本身）不同，这里同时携带两边和**后端的裁决**
 * （`match`），前端只展示、不重新推断——节点当前配置不是那次请求的证据。
 */
export interface DurationCheck {
  requestedSeconds?: number;
  actualSeconds?: number;
  match?: boolean;
}

/**
 * Normalize a `durationCheck` payload from a generation result. Returns null
 * when no side is provable, so cards render nothing instead of inventing an
 * attribution.
 */
export function parseDurationCheck(value: unknown): DurationCheck | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const raw = value as Record<string, unknown>;
  const requested = positiveNumber(raw.requestedSeconds ?? raw.requested_seconds);
  const actual = positiveNumber(raw.actualSeconds ?? raw.actual_seconds);
  const match = typeof raw.match === 'boolean' ? raw.match : undefined;
  if (requested === undefined && actual === undefined && match === undefined) {
    return null;
  }
  const check: DurationCheck = {};
  if (requested !== undefined) check.requestedSeconds = requested;
  if (actual !== undefined) check.actualSeconds = actual;
  if (match !== undefined) check.match = match;
  return check;
}

/** Seconds for compact labels: integers stay bare, fractions keep up to 2 decimals. */
export function formatDurationSecondsShort(seconds: number): string {
  const safe = Number.isFinite(seconds) && seconds > 0 ? seconds : 0;
  const text = (Math.round(safe * 100) / 100).toFixed(2).replace(/\.?0+$/, '');
  return `${text}s`;
}

/**
 * "请求 30s · 实得 15.05s"，缺任一侧返回 null——绝不拿节点当前配置冒充请求值。
 */
export function durationCheckLabel(
  check: DurationCheck | null | undefined,
): string | null {
  if (!check) return null;
  const { requestedSeconds, actualSeconds } = check;
  if (requestedSeconds === undefined || actualSeconds === undefined) return null;
  return `请求 ${formatDurationSecondsShort(requestedSeconds)} · 实得 ${formatDurationSecondsShort(actualSeconds)}`;
}
