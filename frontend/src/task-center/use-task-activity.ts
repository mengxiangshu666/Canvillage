// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useCallback, useEffect, useRef, useState } from "react";

import { isActive as isActiveTask, isTerminal } from "./derivations";
import { useTaskCenterStore } from "./store";
import type { TaskState } from "./types";

/**
 * 接口已受理、但任务还没进任务中心的空窗兜底时长。
 *
 * 入队接口返回后，需要一次 SSE 或轮询往返任务才会出现在 store 里；这段空窗
 * 必须保留 loading。反过来，任务若因事件丢失或服务重启始终没有出现，也必须
 * 到点收回 loading，避免触发按钮永久不可用。
 */
const OPTIMISTIC_GRACE_MS = 15_000;

/** 任务中心里该类型第一条仍在跑的任务；没有则 null。 */
export function useActiveTaskOfType(
  taskType: string,
  options: { episode?: number } = {},
): TaskState | null {
  const { episode } = options;
  return useTaskCenterStore(
    useCallback(
      (state) => {
        for (const task of state.tasks.values()) {
          if (task.task_type !== taskType) continue;
          if (episode !== undefined && task.episode !== episode) continue;
          if (isActiveTask(task)) return task;
        }
        return null;
      },
      [taskType, episode],
    ),
  );
}

export interface TaskActivity {
  /** 任务中心中的活跃任务；乐观空窗期为 null。 */
  task: TaskState | null;
  /** 是否应展示 loading。 */
  isActive: boolean;
  /**
   * TaskCenter 已接管当前项目、但首轮 GET /tasks 还未结束。
   * 硬刷新时 store 初始为空，此时 isActive 还不可信；触发任务的控件应禁用，
   * 避免在既有任务仍运行时重复入队。
   */
  isRestoring: boolean;
  /** 0~1。 */
  progress: number;
  /** 后端回报的当前步骤文案。 */
  currentTask: string;
  /**
   * 入队接口成功返回后调用，用于兜住任务进入任务中心之前的空窗。
   *
   * 尽量传入 TaskResponse.task_id：任务可能在第一次 SSE/轮询观测到时已经是
   * completed/failed，task_key 会跨运行复用，只有 task_id 能准确判断这次运行
   * 是否已经结算。
   */
  markStarted: (override?: { taskId?: string }) => void;
}

/**
 * 将项目级构建任务的 loading 绑定到全局 TaskCenter。
 *
 * 组件本地 mutation.isPending 只覆盖一次入队 POST；真正的后台运行、刷新后的
 * 补水、SSE 重连和首轮即终态都由 TaskCenter 作为唯一事实来源处理。
 */
export function useTaskActivity(
  taskType: string,
  options: { episode?: number } = {},
): TaskActivity {
  const task = useActiveTaskOfType(taskType, options);
  const isRestoring = useTaskCenterStore(
    (state) => state.projectId != null && !state.isHydrated,
  );
  const [startedAt, setStartedAt] = useState<number | null>(null);
  const [startedTaskId, setStartedTaskId] = useState<string | null>(null);
  // 当前乐观窗口是否已经被任务中心中的活跃记录接管。
  const sawTaskRef = useRef(false);
  const activeTaskRef = useRef<TaskState | null>(task);
  activeTaskRef.current = task;

  const clearOptimistic = useCallback(() => {
    setStartedAt(null);
    setStartedTaskId(null);
  }, []);

  // task 只选择活跃态。若这次已入队任务第一次被观察到时就是终态，则仍需按
  // task_id 结算乐观窗口；否则刷新/轮询正好错过 running 的任务会白转 15 秒。
  const startedTaskSettled = useTaskCenterStore(
    useCallback(
      (state) => {
        if (startedTaskId == null) return false;
        for (const candidate of state.tasks.values()) {
          if (candidate.task_id === startedTaskId) return isTerminal(candidate);
        }
        return false;
      },
      [startedTaskId],
    ),
  );

  useEffect(() => {
    if (task) {
      sawTaskRef.current = true;
      clearOptimistic();
      return;
    }

    // 活跃记录消失即任务已完成/失败/取消。SSE 可能早于 POST 响应，因此
    // markStarted 时要结合 activeTaskRef 标记接管状态，保证此处能即时收尾。
    if (sawTaskRef.current) {
      sawTaskRef.current = false;
      clearOptimistic();
    }
  }, [task, clearOptimistic]);

  useEffect(() => {
    if (startedTaskSettled) clearOptimistic();
  }, [startedTaskSettled, clearOptimistic]);

  useEffect(() => {
    if (startedAt == null) return;
    const timer = window.setTimeout(clearOptimistic, OPTIMISTIC_GRACE_MS);
    return () => window.clearTimeout(timer);
  }, [startedAt, clearOptimistic]);

  const markStarted = useCallback((override?: { taskId?: string }) => {
    // 若 SSE 已先到，响应回来后不应把「已被任务中心接管」的信息抹掉；等该活跃
    // 任务转终态时上面的 effect 会立即收尾，而不是再白等乐观窗口。
    sawTaskRef.current = activeTaskRef.current !== null;
    const taskId = override?.taskId?.trim() || null;
    setStartedTaskId(taskId);
    setStartedAt(Date.now());
  }, []);

  return {
    task,
    isActive: task != null || startedAt != null,
    isRestoring,
    progress: task?.progress ?? 0,
    currentTask: task?.current_task ?? "",
    markStarted,
  };
}
