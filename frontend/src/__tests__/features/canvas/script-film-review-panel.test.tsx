// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';

import type { ComposeClip, ComposeTimelineState } from '@/features/canvas/compose/timelineModel';
import { ScriptFilmReviewPanel } from '@/features/canvas/nodes/script/ScriptFilmReviewPanel';
import { emptyScriptFilmReview } from '@/features/canvas/nodes/script/scriptFilmReview';
import {
  fetchFreezoneVideoCutResult,
  submitFreezoneVideoCut,
} from '@/api/ops';
import { awaitTaskCompletion } from '@/api/tasks';

vi.mock('@/api/ops', () => ({
  submitFreezoneVideoCut: vi.fn(),
  fetchFreezoneVideoCutResult: vi.fn(),
}));
vi.mock('@/api/tasks', () => ({ awaitTaskCompletion: vi.fn() }));

function clip(id: string, nodeId: string, timelineStartMs: number): ComposeClip {
  return {
    id,
    nodeId,
    kind: 'video',
    sourceUrl: `/${id}.mp4`,
    displayName: null,
    thumbUrl: null,
    durationMs: 4000,
    timelineStartMs,
    trimStartMs: 0,
    trimEndMs: 4000,
    volume: 1,
    muted: false,
    speed: 1,
  };
}

const timeline: ComposeTimelineState = {
  resolution: '1080p',
  tracks: [{ id: 'track_video', kind: 'video', clips: [clip('a', 'shot-1', 0), clip('b', 'shot-2', 4000)] }],
};

afterEach(cleanup);

describe('M11 接点审片面板', () => {
  it('显示实际接点窗口，点击通过后写回带签名的记录', () => {
    const onChange = vi.fn();
    render(
      <ScriptFilmReviewPanel
        timeline={timeline}
        review={emptyScriptFilmReview()}
        onChange={onChange}
        onClose={() => {}}
      />,
    );
    expect(screen.getByText(/接点 1 · shot-1 → shot-2/)).toBeInTheDocument();
    expect(screen.getByText(/前段尾窗：3\.40s–4\.00s/)).toBeInTheDocument();
    expect(screen.getByText(/后段头窗：0\.00s–0\.60s/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /正常速度看听通过/ }));
    const next = onChange.mock.calls[0][0];
    expect(next.boundaries).toHaveLength(1);
    expect(next.boundaries[0].status).toBe('accepted');
    expect(next.boundaries[0].signature).toMatch(/^boundary-/);
  });

  it('没有当前成片时不允许整片通过，返工可以记录', () => {
    const onChange = vi.fn();
    render(
      <ScriptFilmReviewPanel
        timeline={timeline}
        review={emptyScriptFilmReview()}
        onChange={onChange}
        onClose={() => {}}
      />,
    );
    expect(screen.getByRole('button', { name: '整片看听通过' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: '整片需返工' }));
    expect(onChange.mock.calls[0][0].whole_film.status).toBe('rework');
  });

  it('有项目时用现有切段接口切出真实前后窗口，不改审片状态', async () => {
    vi.mocked(submitFreezoneVideoCut).mockResolvedValue({ task_key: 't', job_id: 'j' } as never);
    vi.mocked(awaitTaskCompletion).mockResolvedValue(undefined as never);
    vi.mocked(fetchFreezoneVideoCutResult)
      .mockResolvedValueOnce({
        segments: [{ index: 0, start: 3.4, end: 4, url: '/left.mp4', size: 10, duration_seconds: 0.6 }],
        segment_count: 1,
        source_duration_seconds: 4,
      })
      .mockResolvedValueOnce({
        segments: [{ index: 0, start: 0, end: 0.6, url: '/right.mp4', size: 10, duration_seconds: 0.6 }],
        segment_count: 1,
        source_duration_seconds: 4,
      });
    const onChange = vi.fn();
    render(
      <ScriptFilmReviewPanel
        timeline={timeline}
        review={emptyScriptFilmReview()}
        project="project-1"
        onChange={onChange}
        onClose={() => {}}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /切出前后窗口联看/ }));
    await waitFor(() => expect(screen.getByText('前段尾窗（实际切出）')).toBeInTheDocument());
    expect(submitFreezoneVideoCut).toHaveBeenCalledTimes(2);
    expect(submitFreezoneVideoCut).toHaveBeenCalledWith('project-1', {
      sourceUrl: '/a.mp4',
      segments: [{ index: 0, start: 3.4, end: 4 }],
    });
    expect(submitFreezoneVideoCut).toHaveBeenCalledWith('project-1', {
      sourceUrl: '/b.mp4',
      segments: [{ index: 0, start: 0, end: 0.6 }],
    });
    expect(onChange).not.toHaveBeenCalled();
  });
});
