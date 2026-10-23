// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import {
  clipLengthMs,
  type ComposeClip,
  type ComposeTimelineState,
} from '@/features/canvas/compose/timelineModel';

export const SCRIPT_FILM_REVIEW_SCHEMA = 'script-film-review.v1';

export type ScriptBoundaryReviewStatus = 'pending' | 'accepted' | 'rework';
export type ScriptFilmReviewMethod = 'normal_speed_with_sound' | 'mute_visual' | 'sound_only';

export interface ScriptBoundarySide {
  nodeId: string;
  /** 用户能认出的片段名；没有时回退 nodeId。 */
  label: string;
  sourceUrl: string;
  /** 实际使用范围（源文件毫秒），不是脚本计划时长。 */
  sourceStartMs: number;
  sourceEndMs: number;
  timelineStartMs: number;
  speed: number;
  volume: number;
  muted: boolean;
}

export interface ScriptFilmBoundary {
  boundary_id: string;
  /** 左侧片段结尾窗口；实际看/听从这里开始。 */
  left: ScriptBoundarySide;
  /** 右侧片段开头窗口。 */
  right: ScriptBoundarySide;
  /** 窗口长度由动作/语句需要决定，默认只作为起点，不是统一硬标准。 */
  window_ms: number;
  /** 两侧片段与窗口内声音的当前版本签名。 */
  signature: string;
}

export interface ScriptFilmBoundaryReview {
  boundary_id: string;
  signature: string;
  status: ScriptBoundaryReviewStatus;
  method?: ScriptFilmReviewMethod;
  note?: string;
  reviewed_at?: string;
}

export interface ScriptFilmWholeReview {
  status: ScriptBoundaryReviewStatus;
  signature?: string;
  method?: ScriptFilmReviewMethod;
  note?: string;
  reviewed_at?: string;
}

export interface ScriptFilmTechnicalOutput {
  url: string;
  timeline_signature: string;
  composed_at: string;
  /** 后端 ffprobe 的实际时长；未知时为空，不拿时间线长度冒充。 */
  duration_seconds?: number | null;
  /** 输出文件摘要；老任务没有时为空。 */
  sha256?: string | null;
  size_bytes?: number | null;
}

export interface ScriptFilmReview {
  schema: typeof SCRIPT_FILM_REVIEW_SCHEMA;
  boundaries: ScriptFilmBoundaryReview[];
  whole_film: ScriptFilmWholeReview;
  technical_output?: ScriptFilmTechnicalOutput | null;
}

export interface ScriptFilmReviewStatus {
  boundaryTotal: number;
  acceptedCount: number;
  reworkCount: number;
  pendingCount: number;
  staleCount: number;
  wholeFilmStatus: ScriptBoundaryReviewStatus;
  wholeFilmCurrent: boolean;
  technicalOutputCurrent: boolean;
  formalReady: boolean;
  blockingReasons: string[];
}

function stableJson(value: unknown): string {
  const seen = new WeakSet<object>();
  const visit = (input: unknown): unknown => {
    if (Array.isArray(input)) return input.map(visit);
    if (input && typeof input === 'object') {
      if (seen.has(input as object)) return '[circular]';
      seen.add(input as object);
      return Object.fromEntries(
        Object.entries(input as Record<string, unknown>)
          .sort(([left], [right]) => left.localeCompare(right))
          .map(([key, entry]) => [key, visit(entry)]),
      );
    }
    return input;
  };
  return JSON.stringify(visit(value));
}

function fnv1a(text: string): string {
  let hash = 0x811c9dc5;
  for (let index = 0; index < text.length; index += 1) {
    hash ^= text.charCodeAt(index);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return hash.toString(16).padStart(8, '0');
}

function videoClips(timeline: ComposeTimelineState): ComposeClip[] {
  return timeline.tracks
    .filter((track) => track.kind === 'video')
    .flatMap((track) => track.clips)
    .filter((clip) => clip.kind === 'video' && clip.sourceUrl)
    .sort((left, right) =>
      left.timelineStartMs - right.timelineStartMs || left.id.localeCompare(right.id));
}

function clipEndMs(clip: ComposeClip): number {
  return clip.timelineStartMs + clipLengthMs(clip);
}

function audioUnder(
  timeline: ComposeTimelineState,
  startMs: number,
  endMs: number,
): unknown[] {
  return timeline.tracks
    .filter((track) => track.kind === 'audio')
    .flatMap((track) => track.clips)
    .filter((clip) => {
      if (!clip.sourceUrl) return false;
      const clipStart = clip.timelineStartMs;
      const clipEnd = clipStart + clipLengthMs(clip);
      return clipStart < endMs && clipEnd > startMs;
    })
    .map((clip) => ({
      nodeId: clip.nodeId,
      sourceUrl: clip.sourceUrl,
      trimStartMs: clip.trimStartMs,
      trimEndMs: clip.trimEndMs,
      speed: clip.speed,
      volume: clip.volume,
      muted: clip.muted,
      timelineStartMs: clip.timelineStartMs,
    }))
    .sort((left, right) =>
      left.timelineStartMs - right.timelineStartMs
      || (left.nodeId ?? '').localeCompare(right.nodeId ?? ''));
}

function clipIdentity(clip: ComposeClip): Record<string, unknown> {
  return {
    clipId: clip.id,
    nodeId: clip.nodeId,
    sourceUrl: clip.sourceUrl,
    trimStartMs: clip.trimStartMs,
    trimEndMs: clip.trimEndMs,
    speed: clip.speed,
    volume: clip.volume,
    muted: clip.muted,
    timelineStartMs: clip.timelineStartMs,
    durationMs: clipLengthMs(clip),
  };
}

/** 整片版本签名：任何素材、裁剪、速度、顺序或声音参数变化都会失效。 */
export function scriptFilmTimelineSignature(timeline: ComposeTimelineState): string {
  const payload = {
    resolution: timeline.resolution,
    cover: timeline.cover ?? null,
    tracks: timeline.tracks.map((track) => ({
      id: track.id,
      kind: track.kind,
      clips: track.clips
        .slice()
        .sort((left, right) => left.timelineStartMs - right.timelineStartMs)
        .map(clipIdentity),
    })),
  };
  return `film-${fnv1a(stableJson(payload))}`;
}

/** 当前时间线应该有的接点（相邻视频片段的真实使用范围）。 */
export function scriptFilmBoundaries(
  timeline: ComposeTimelineState,
  defaultWindowMs = 600,
): ScriptFilmBoundary[] {
  const clips = videoClips(timeline);
  const boundaries: ScriptFilmBoundary[] = [];
  for (let index = 0; index < clips.length - 1; index += 1) {
    const leftClip = clips[index];
    const rightClip = clips[index + 1];
    const elapsed = clipEndMs(leftClip);
    const gapMs = Math.max(0, rightClip.timelineStartMs - elapsed);
    const windowMs = Math.max(200, Math.round(defaultWindowMs));
    const leftSide: ScriptBoundarySide = {
      nodeId: leftClip.nodeId ?? '',
      label: leftClip.displayName || leftClip.nodeId || leftClip.id,
      sourceUrl: leftClip.sourceUrl ?? '',
      sourceStartMs: Math.max(
        leftClip.trimStartMs,
        leftClip.trimEndMs - windowMs * leftClip.speed,
      ),
      sourceEndMs: leftClip.trimEndMs,
      timelineStartMs: Math.max(leftClip.timelineStartMs, elapsed - windowMs),
      speed: leftClip.speed,
      volume: leftClip.volume,
      muted: leftClip.muted,
    };
    const rightSide: ScriptBoundarySide = {
      nodeId: rightClip.nodeId ?? '',
      label: rightClip.displayName || rightClip.nodeId || rightClip.id,
      sourceUrl: rightClip.sourceUrl ?? '',
      sourceStartMs: rightClip.trimStartMs,
      sourceEndMs: Math.min(
        rightClip.trimEndMs,
        rightClip.trimStartMs + windowMs * rightClip.speed,
      ),
      timelineStartMs: rightClip.timelineStartMs,
      speed: rightClip.speed,
      volume: rightClip.volume,
      muted: rightClip.muted,
    };
    const signature = `boundary-${fnv1a(stableJson({
      left: clipIdentity(leftClip),
      right: clipIdentity(rightClip),
      windowMs,
      leftAudio: audioUnder(
        timeline,
        Math.max(leftClip.timelineStartMs, elapsed - windowMs),
        elapsed,
      ),
      rightAudio: audioUnder(
        timeline,
        rightClip.timelineStartMs,
        rightClip.timelineStartMs + windowMs,
      ),
      gapMs,
    }))}`;
    boundaries.push({
      boundary_id: `${leftSide.nodeId}->${rightSide.nodeId}`,
      left: leftSide,
      right: rightSide,
      window_ms: windowMs,
      signature,
    });
  }
  return boundaries;
}

export function emptyScriptFilmReview(): ScriptFilmReview {
  return {
    schema: SCRIPT_FILM_REVIEW_SCHEMA,
    boundaries: [],
    whole_film: { status: 'pending' },
    technical_output: null,
  };
}

function findBoundary(
  review: ScriptFilmReview,
  boundary: ScriptFilmBoundary,
): ScriptFilmBoundaryReview | undefined {
  return review.boundaries.find((entry) => entry.boundary_id === boundary.boundary_id);
}

/**
 * 标记一个接点。签名必须对应当前两侧素材/裁剪/速度/声音；旧签名的记录不能
 * 被"顺手点一下"覆盖成通过——那等于用旧片子的认可给新剪辑盖章。
 */
export function reviewScriptFilmBoundary(
  review: ScriptFilmReview,
  boundary: ScriptFilmBoundary,
  status: Exclude<ScriptBoundaryReviewStatus, 'pending'>,
  options: { method?: ScriptFilmReviewMethod; note?: string; now?: string } = {},
): ScriptFilmReview {
  const entry: ScriptFilmBoundaryReview = {
    boundary_id: boundary.boundary_id,
    signature: boundary.signature,
    status,
    method: options.method ?? 'normal_speed_with_sound',
    note: options.note?.trim() || undefined,
    reviewed_at: options.now ?? new Date().toISOString(),
  };
  return {
    ...review,
    boundaries: [
      ...review.boundaries.filter((item) => item.boundary_id !== boundary.boundary_id),
      entry,
    ],
  };
}

export function reviewScriptFilmWhole(
  review: ScriptFilmReview,
  timeline: ComposeTimelineState,
  status: Exclude<ScriptBoundaryReviewStatus, 'pending'>,
  options: { method?: ScriptFilmReviewMethod; note?: string; now?: string } = {},
): ScriptFilmReview {
  return {
    ...review,
    whole_film: {
      status,
      signature: scriptFilmTimelineSignature(timeline),
      method: options.method ?? 'normal_speed_with_sound',
      note: options.note?.trim() || undefined,
      reviewed_at: options.now ?? new Date().toISOString(),
    },
  };
}

export function bindScriptFilmTechnicalOutput(
  review: ScriptFilmReview,
  timeline: ComposeTimelineState,
  url: string,
  now: string = new Date().toISOString(),
  metadata: {
    duration_seconds?: number | null;
    sha256?: string | null;
    size_bytes?: number | null;
  } = {},
): ScriptFilmReview {
  return {
    ...review,
    whole_film: { status: 'pending' },
    technical_output: {
      url,
      timeline_signature: scriptFilmTimelineSignature(timeline),
      composed_at: now,
      duration_seconds:
        typeof metadata.duration_seconds === 'number'
        && Number.isFinite(metadata.duration_seconds)
        && metadata.duration_seconds > 0
          ? metadata.duration_seconds
          : null,
      sha256: metadata.sha256?.trim() || null,
      size_bytes:
        typeof metadata.size_bytes === 'number' && metadata.size_bytes > 0
          ? metadata.size_bytes
          : null,
    },
  };
}

export function scriptFilmReviewStatus(
  timeline: ComposeTimelineState,
  review: ScriptFilmReview | null | undefined,
): ScriptFilmReviewStatus {
  const current = review ?? emptyScriptFilmReview();
  const timelineSignature = scriptFilmTimelineSignature(timeline);
  const boundaries = scriptFilmBoundaries(timeline);
  let acceptedCount = 0;
  let reworkCount = 0;
  let pendingCount = 0;
  let staleCount = 0;
  const blockingReasons: string[] = [];
  boundaries.forEach((boundary) => {
    const entry = findBoundary(current, boundary);
    const label = `${boundary.left.nodeId} → ${boundary.right.nodeId}`;
    if (!entry) {
      pendingCount += 1;
      blockingReasons.push(`接点 ${label} 还没有看听记录`);
      return;
    }
    if (entry.signature !== boundary.signature) {
      staleCount += 1;
      blockingReasons.push(`接点 ${label} 的素材、裁剪、速度或声音已变化，需要重新看听`);
      return;
    }
    if (entry.status === 'accepted') acceptedCount += 1;
    else if (entry.status === 'rework') {
      reworkCount += 1;
      blockingReasons.push(`接点 ${label} 已标为返工，不能标记正式成片`);
    } else {
      pendingCount += 1;
      blockingReasons.push(`接点 ${label} 还未确认`);
    }
  });
  if (boundaries.length === 0) {
    blockingReasons.push('时间线还没有两个相邻视频片段，无法核对接点');
  }
  const wholeCurrent = current.whole_film.status === 'accepted'
    && current.whole_film.signature === timelineSignature;
  if (current.whole_film.status === 'rework') {
    blockingReasons.push('整片已标为返工，不能标记正式成片');
  } else if (!wholeCurrent) {
    blockingReasons.push('整片还没有按当前版本正常速度看听通过');
  }
  const technicalOutputCurrent = Boolean(
    current.technical_output
    && current.technical_output.timeline_signature === timelineSignature
    && current.technical_output.url,
  );
  if (!technicalOutputCurrent) {
    blockingReasons.push('还没有当前时间线导出的成片文件，最终文件未看听');
  }
  return {
    boundaryTotal: boundaries.length,
    acceptedCount,
    reworkCount,
    pendingCount,
    staleCount,
    wholeFilmStatus: current.whole_film.status,
    wholeFilmCurrent: wholeCurrent,
    technicalOutputCurrent,
    formalReady:
      boundaries.length > 0
      && acceptedCount === boundaries.length
      && reworkCount === 0
      && staleCount === 0
      && pendingCount === 0
      && wholeCurrent
      && technicalOutputCurrent,
    blockingReasons,
  };
}

/** 只用于界面显示：当前版本接点是否全部有有效认可。 */
export function scriptFilmBoundaryReviewedCount(
  timeline: ComposeTimelineState,
  review: ScriptFilmReview | null | undefined,
): { reviewed: number; total: number } {
  const current = review ?? emptyScriptFilmReview();
  const boundaries = scriptFilmBoundaries(timeline);
  const reviewed = boundaries.filter((boundary) =>
    findBoundary(current, boundary)?.signature === boundary.signature
    && findBoundary(current, boundary)?.status === 'accepted',
  ).length;
  return { reviewed, total: boundaries.length };
}
