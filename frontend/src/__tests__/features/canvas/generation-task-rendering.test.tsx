// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { act, renderHook } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { sampleTask } from '@/__mocks__/msw/handlers/tasks';
import { useNodeGenerationTaskState } from '@/features/canvas/application/useNodeGenerationTaskState';
import { useTaskCenterStore } from '@/task-center/store';

beforeEach(() => useTaskCenterStore.getState().reset());

describe('canvas task subscription rendering', () => {
  it('expires a missing task grace period without an unrelated store event', () => {
    vi.useFakeTimers();
    try {
      useTaskCenterStore.getState().markHydrated();
      const startedAt = Date.now();
      const { result, unmount } = renderHook(() => useNodeGenerationTaskState({
        generationTaskKey: 'never-arrived', isGenerating: true, generationStartedAt: startedAt,
      }));
      expect(result.current.isGenerating).toBe(true);
      act(() => vi.advanceTimersByTime(10_000));
      expect(result.current.isGenerating).toBe(false);
      expect(result.current.waitingForTaskRecord).toBe(false);
      unmount();
      expect(vi.getTimerCount()).toBe(0);
    } finally {
      vi.useRealTimers();
    }
  });

  it('cancels the grace timer when the authoritative task arrives', () => {
    vi.useFakeTimers();
    try {
      useTaskCenterStore.getState().markHydrated();
      const startedAt = Date.now();
      const { result, unmount } = renderHook(() => useNodeGenerationTaskState({
        generationTaskKey: 'arrived', isGenerating: true, generationStartedAt: startedAt,
      }));
      expect(vi.getTimerCount()).toBe(1);
      act(() => useTaskCenterStore.getState().upsert(sampleTask({
        task_key: 'arrived', status: 'running',
      })));
      expect(vi.getTimerCount()).toBe(0);
      act(() => vi.advanceTimersByTime(10_000));
      expect(result.current.isGenerating).toBe(true);
      act(() => useTaskCenterStore.getState().upsert(sampleTask({
        task_key: 'arrived', status: 'completed',
      })));
      expect(result.current.isGenerating).toBe(false);
      unmount();
    } finally {
      vi.useRealTimers();
    }
  });

  it('skips repeated snapshots and unrelated updates but renders authoritative changes', () => {
    const store = useTaskCenterStore.getState();
    const tasks = Array.from({ length: 40 }, (_, index) => sampleTask({
      task_id: `run-${index}`,
      task_key: `task:${index}`,
      status: 'running',
      result: { urls: [`/static/${index}.mp4`] },
    }));
    store.hydrate(tasks);
    store.markHydrated();
    let renders = 0;
    const { result } = renderHook(() => {
      renders += 1;
      return useNodeGenerationTaskState({ generationTaskKey: 'task:0' });
    });
    const initialRenders = renders;
    expect(result.current.isGenerating).toBe(true);

    for (let round = 0; round < 12; round += 1) {
      act(() => {
        store.hydrate(JSON.parse(JSON.stringify(tasks)));
        store.upsert(JSON.parse(JSON.stringify(tasks[0])));
      });
    }
    expect(renders).toBe(initialRenders);

    act(() => store.upsert({ ...tasks[1], progress: 0.75 }));
    expect(renders).toBe(initialRenders);

    act(() => store.upsert({
      ...tasks[0], status: 'completed', progress: 1,
      result: { urls: ['/static/final.mp4'] },
    }));
    expect(renders).toBe(initialRenders + 1);
    expect(result.current.isGenerating).toBe(false);
    expect(result.current.task?.result).toEqual({ urls: ['/static/final.mp4'] });

    act(() => store.upsert({
      ...tasks[0], status: 'failed', error: 'download failed',
      error_diagnostic: { retryable: true, stage: 'download' },
    }));
    expect(renders).toBe(initialRenders + 2);
    expect(result.current.task?.error_diagnostic).toEqual({ retryable: true, stage: 'download' });

    act(() => store.hydrate([tasks[1]]));
    expect(renders).toBe(initialRenders + 3);
    expect(result.current.task).toBeNull();
  });
});
