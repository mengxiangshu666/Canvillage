// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { sampleTask } from "@/__mocks__/msw/handlers/tasks";
import { useTaskCenterStore } from "@/task-center/store";
import { useTaskActivity } from "@/task-center/use-task-activity";
import type { TaskState } from "@/task-center/types";

const buildScenes = (overrides: Partial<TaskState> = {}): TaskState =>
  sampleTask({
    task_key: "task:build_scenes:alice:demo:0",
    task_id: "run-1",
    task_type: "build_scenes",
    episode: 0,
    status: "running",
    progress: 0.4,
    current_task: "抽取场景…",
    ...overrides,
  });

function mountProvider(projectId = "demo") {
  act(() => {
    useTaskCenterStore.getState().setProject(projectId);
  });
}

function hydrateWith(tasks: TaskState[]) {
  act(() => {
    useTaskCenterStore.getState().hydrate(tasks);
    useTaskCenterStore.getState().markHydrated();
  });
}

function render() {
  return renderHook(() => useTaskActivity("build_scenes", { episode: 0 }));
}

beforeEach(() => {
  useTaskCenterStore.getState().reset();
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
  useTaskCenterStore.getState().reset();
});

describe("useTaskActivity", () => {
  it("补水完成前报告 isRestoring，避免硬刷新时重复入队", () => {
    const { result } = render();
    mountProvider();

    expect(result.current.isRestoring).toBe(true);
    expect(result.current.isActive).toBe(false);

    hydrateWith([buildScenes()]);

    expect(result.current.isRestoring).toBe(false);
    expect(result.current.isActive).toBe(true);
  });

  it("没有 TaskCenterProvider 时不报 isRestoring", () => {
    const { result } = render();

    expect(result.current.isRestoring).toBe(false);
    expect(result.current.isActive).toBe(false);
  });

  it("补水后认出后台仍在跑的任务，并提供进度和步骤", () => {
    const { result } = render();
    hydrateWith([buildScenes({ progress: 0.4, current_task: "抽取场景…" })]);

    expect(result.current.isActive).toBe(true);
    expect(result.current.progress).toBe(0.4);
    expect(result.current.currentTask).toBe("抽取场景…");
    expect(result.current.task?.task_type).toBe("build_scenes");
  });

  it("忽略其他类型和其他集数的任务", () => {
    const { result } = render();
    hydrateWith([
      sampleTask({ task_key: "a", task_type: "build_characters", episode: 0 }),
      buildScenes({ task_key: "b", episode: 3 }),
    ]);

    expect(result.current.isActive).toBe(false);
  });

  it("终态任务不算活跃", () => {
    const { result } = render();
    hydrateWith([buildScenes({ status: "completed", progress: 1 })]);

    expect(result.current.isActive).toBe(false);
  });

  it("markStarted 覆盖任务进入 TaskCenter 前的空窗", () => {
    const { result } = render();
    hydrateWith([]);

    act(() => result.current.markStarted({ taskId: "run-2" }));
    expect(result.current.isActive).toBe(true);

    act(() => {
      useTaskCenterStore.getState().upsert(buildScenes({ task_id: "run-2" }));
    });
    expect(result.current.isActive).toBe(true);
    expect(result.current.task).not.toBeNull();
  });

  it("任务始终未出现时，乐观窗口到点收回 loading", () => {
    const { result } = render();
    hydrateWith([]);

    act(() => result.current.markStarted({ taskId: "run-2" }));
    expect(result.current.isActive).toBe(true);

    act(() => {
      vi.advanceTimersByTime(15_000);
    });
    expect(result.current.isActive).toBe(false);
  });

  it("SSE 先于入队响应到达时，任务结算后立即收回 loading", () => {
    const { result } = render();
    hydrateWith([]);

    act(() => {
      useTaskCenterStore.getState().upsert(buildScenes({ task_id: "run-2" }));
    });
    act(() => result.current.markStarted({ taskId: "run-2" }));
    expect(result.current.isActive).toBe(true);

    act(() => {
      useTaskCenterStore
        .getState()
        .upsert(buildScenes({ task_id: "run-2", status: "completed", progress: 1 }));
    });
    expect(result.current.isActive).toBe(false);
  });

  it("首轮观察已是终态时按 task_id 立即收回 loading", () => {
    const { result } = render();
    hydrateWith([]);

    act(() => result.current.markStarted({ taskId: "run-2" }));
    act(() => {
      useTaskCenterStore
        .getState()
        .hydrate([buildScenes({ task_id: "run-2", status: "completed", progress: 1 })]);
    });

    expect(result.current.isActive).toBe(false);
  });

  it("任务入队后立即失败时按 task_id 立即收回 loading", () => {
    const { result } = render();
    hydrateWith([]);

    act(() => result.current.markStarted({ taskId: "run-2" }));
    act(() => {
      useTaskCenterStore
        .getState()
        .upsert(buildScenes({ task_id: "run-2", status: "failed", error: "boom" }));
    });

    expect(result.current.isActive).toBe(false);
  });

  it("旧 task_id 的终态记录不会提前结束新一轮乐观窗口", () => {
    const { result } = render();
    hydrateWith([buildScenes({ task_id: "run-1", status: "completed", progress: 1 })]);

    act(() => result.current.markStarted({ taskId: "run-2" }));

    expect(result.current.isActive).toBe(true);
  });

  it("未携带 task_id 的既有调用仍退回超时兜底", () => {
    const { result } = render();
    hydrateWith([buildScenes({ task_id: "run-1", status: "completed", progress: 1 })]);

    act(() => result.current.markStarted());
    expect(result.current.isActive).toBe(true);

    act(() => {
      vi.advanceTimersByTime(15_000);
    });
    expect(result.current.isActive).toBe(false);
  });

  it("正常任务从活跃态结算后收回 loading", () => {
    const { result } = render();
    hydrateWith([buildScenes()]);

    act(() => {
      useTaskCenterStore
        .getState()
        .upsert(buildScenes({ status: "completed", progress: 1 }));
    });
    expect(result.current.isActive).toBe(false);
    expect(result.current.progress).toBe(0);
  });
});
