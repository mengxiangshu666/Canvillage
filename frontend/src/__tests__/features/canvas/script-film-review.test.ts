// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from 'vitest';

import {
  bindScriptFilmTechnicalOutput,
  emptyScriptFilmReview,
  reviewScriptFilmBoundary,
  reviewScriptFilmWhole,
  scriptFilmBoundaries,
  scriptFilmReviewStatus,
  scriptFilmTimelineSignature,
} from '@/features/canvas/nodes/script/scriptFilmReview';
import type {
  ComposeClip,
  ComposeTimelineState,
  ComposeTrack,
} from '@/features/canvas/compose/timelineModel';

function clip(overrides: Partial<ComposeClip>): ComposeClip {
  return {
    id: overrides.id ?? 'c1',
    nodeId: overrides.nodeId ?? null,
    kind: overrides.kind ?? 'video',
    sourceUrl: overrides.sourceUrl ?? '/a.mp4',
    displayName: null,
    thumbUrl: null,
    durationMs: overrides.durationMs ?? 6000,
    timelineStartMs: overrides.timelineStartMs ?? 0,
    trimStartMs: overrides.trimStartMs ?? 0,
    trimEndMs: overrides.trimEndMs ?? 4000,
    volume: overrides.volume ?? 1,
    muted: overrides.muted ?? false,
    speed: overrides.speed ?? 1,
  };
}

function track(kind: 'video' | 'audio', clips: ComposeClip[]): ComposeTrack {
  return { id: `track_${kind}`, kind, clips };
}

function timeline(): ComposeTimelineState {
  return {
    resolution: '1080p',
    tracks: [
      track('video', [
        clip({ id: 'v1', nodeId: 'shot-1', sourceUrl: '/a.mp4', timelineStartMs: 0, trimStartMs: 0, trimEndMs: 4000 }),
        clip({ id: 'v2', nodeId: 'shot-2', sourceUrl: '/b.mp4', timelineStartMs: 4000, trimStartMs: 1000, trimEndMs: 5000 }),
      ]),
      track('audio', [
        clip({ id: 'a1', nodeId: 'audio-1', kind: 'audio', sourceUrl: '/room.wav', timelineStartMs: 0, trimStartMs: 0, trimEndMs: 9000 }),
      ]),
    ],
  };
}

describe('M11 实际接点与整片审片', () => {
  it('接点窗口取实际使用范围，通过后仍需当前成片整片看听', () => {
    const state = timeline();
    const boundaries = scriptFilmBoundaries(state);
    expect(boundaries).toHaveLength(1);
    expect(boundaries[0]).toMatchObject({
      boundary_id: 'shot-1->shot-2',
      left: { sourceStartMs: 3400, sourceEndMs: 4000 },
      right: { sourceStartMs: 1000, sourceEndMs: 1600 },
      window_ms: 600,
    });

    let review = emptyScriptFilmReview();
    review = reviewScriptFilmBoundary(review, boundaries[0], 'accepted', {
      now: '2026-10-23T00:00:00Z',
    });
    let status = scriptFilmReviewStatus(state, review);
    expect(status.acceptedCount).toBe(1);
    expect(status.technicalOutputCurrent).toBe(false);
    expect(status.formalReady).toBe(false);

    review = bindScriptFilmTechnicalOutput(
      review,
      state,
      '/export/first.mp4',
      '2026-10-23T00:01:00Z',
      { duration_seconds: 9.2, sha256: 'abc123', size_bytes: 1024 },
    );
    expect(review.technical_output).toMatchObject({
      duration_seconds: 9.2,
      sha256: 'abc123',
      size_bytes: 1024,
    });
    review = reviewScriptFilmWhole(review, state, 'accepted', {
      now: '2026-10-23T00:02:00Z',
    });
    status = scriptFilmReviewStatus(state, review);
    expect(status.formalReady).toBe(true);
  });

  it('改裁剪、变速、声音或换素材都会让对应接点与整片认可失效', () => {
    const state = timeline();
    const boundaries = scriptFilmBoundaries(state);
    let review = emptyScriptFilmReview();
    review = reviewScriptFilmBoundary(review, boundaries[0], 'accepted');
    review = bindScriptFilmTechnicalOutput(review, state, '/export/first.mp4');
    review = reviewScriptFilmWhole(review, state, 'accepted');
    expect(scriptFilmReviewStatus(state, review).formalReady).toBe(true);

    const trimChanged = structuredClone(state);
    const videoTrack = trimChanged.tracks[0];
    videoTrack.clips[0].trimEndMs = 3500;
    videoTrack.clips[1].timelineStartMs = 3500;
    const trimStatus = scriptFilmReviewStatus(trimChanged, review);
    expect(trimStatus.staleCount).toBe(1);
    expect(trimStatus.formalReady).toBe(false);
    expect(trimStatus.wholeFilmCurrent).toBe(false);
    expect(trimStatus.technicalOutputCurrent).toBe(false);

    const speedChanged = structuredClone(state);
    speedChanged.tracks[0].clips[1].speed = 2;
    expect(scriptFilmReviewStatus(speedChanged, review).staleCount).toBe(1);

    const audioChanged = structuredClone(state);
    audioChanged.tracks[1].clips[0].volume = 0.4;
    expect(scriptFilmReviewStatus(audioChanged, review).staleCount).toBe(1);

    const replaced = structuredClone(state);
    replaced.tracks[0].clips[1].sourceUrl = '/b-v2.mp4';
    expect(scriptFilmReviewStatus(replaced, review).staleCount).toBe(1);
  });

  it('返工记录会阻止正式认可，且签名区分带间隙的接点', () => {
    const state = timeline();
    const boundaries = scriptFilmBoundaries(state);
    let review = reviewScriptFilmBoundary(emptyScriptFilmReview(), boundaries[0], 'rework', {
      note: '手没有接触，钥匙已经瞬移',
    });
    const status = scriptFilmReviewStatus(state, review);
    expect(status.reworkCount).toBe(1);
    expect(status.formalReady).toBe(false);
    expect(status.blockingReasons.join('|')).toContain('返工');

    const gapped = timeline();
    gapped.tracks[0].clips[1].timelineStartMs = 4500;
    expect(scriptFilmBoundaries(gapped)[0].signature)
      .not.toBe(boundaries[0].signature);
    expect(scriptFilmTimelineSignature(gapped))
      .not.toBe(scriptFilmTimelineSignature(state));
  });
});
