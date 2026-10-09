// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useEffect, useMemo, useRef, useState } from "react";
import { useReducedMotion } from "@/hooks/use-reduced-motion";
import { isActive } from "@/task-center/derivations";
import { useTaskCenterStore } from "@/task-center/store";
import type { TaskState } from "@/task-center/types";
import {
  DEFAULT_MYBUDDY_ACTION,
  type MyBuddyAction,
} from "@/features/companion/mybuddy-actions";

/** 任务成功/失败的动画时长，也复用为气泡的可见时长，让气泡与动作同步收尾。 */
export const SUCCESS_ACTION_MS = 2600;
export const FAILURE_ACTION_MS = 3600;
/** 气泡默认可见时长 + 淡出时长。 */
export const BUBBLE_VISIBLE_MS = 3500;
export const BUBBLE_FADE_OUT_MS = 220;

/** 成功/失败反馈的冷却：连续完成多个任务时不反复弹同一段动画。 */
const TERMINAL_FEEDBACK_COOLDOWN_MS = 8000;

function latestTerminalTask(tasks: Iterable<TaskState>): TaskState | null {
  let latest: TaskState | null = null;
  for (const task of tasks) {
    if (task.status !== "completed" && task.status !== "failed") continue;
    if (!task.completed_at) continue;
    if (!latest || Date.parse(task.completed_at) > Date.parse(latest.completed_at)) {
      latest = task;
    }
  }
  return latest;
}

function isDocumentVisible() {
  return typeof document === "undefined" || document.visibilityState === "visible";
}

/**
 * 搭子动作控制器：把任务中心的状态映射成宠物动作（运行中 → typing、成功 → flag、
 * 失败 → repair、其余 → idle）。动作再由 `petdexStateForAction` 落到精灵图的具体行。
 */
export function useMyBuddyCompanionController(): { action: MyBuddyAction } {
  const tasks = useTaskCenterStore((state) => state.tasks);
  const isHydrated = useTaskCenterStore((state) => state.isHydrated);
  const reducedMotion = useReducedMotion();
  const [isPageVisible, setIsPageVisible] = useState(isDocumentVisible());
  const [momentAction, setMomentAction] = useState<MyBuddyAction | null>(null);
  const momentTimerRef = useRef<number | null>(null);
  const latestTerminalKeyRef = useRef<string | null>(null);
  const lastTerminalFeedbackAtRef = useRef(0);

  const hasActiveTask = useMemo(
    () => Array.from(tasks.values()).some(isActive),
    [tasks],
  );

  const action = useMemo<MyBuddyAction>(() => {
    if (reducedMotion) return DEFAULT_MYBUDDY_ACTION;
    if (momentAction) return momentAction;
    return hasActiveTask ? "typing" : DEFAULT_MYBUDDY_ACTION;
  }, [hasActiveTask, momentAction, reducedMotion]);

  useEffect(() => {
    if (typeof document === "undefined") return;
    const handleVisibilityChange = () => setIsPageVisible(isDocumentVisible());
    document.addEventListener("visibilitychange", handleVisibilityChange);
    return () => document.removeEventListener("visibilitychange", handleVisibilityChange);
  }, []);

  useEffect(
    () => () => {
      if (momentTimerRef.current) window.clearTimeout(momentTimerRef.current);
    },
    [],
  );

  useEffect(() => {
    if (!isHydrated || reducedMotion || !isPageVisible) return;

    const latest = latestTerminalTask(tasks.values());
    if (!latest) return;

    const latestKey = `${latest.task_key}:${latest.status}:${latest.completed_at}`;
    if (latestTerminalKeyRef.current === null) {
      latestTerminalKeyRef.current = latestKey;
      return;
    }
    if (latestTerminalKeyRef.current === latestKey) return;
    latestTerminalKeyRef.current = latestKey;

    const now = Date.now();
    if (now - lastTerminalFeedbackAtRef.current < TERMINAL_FEEDBACK_COOLDOWN_MS) return;
    lastTerminalFeedbackAtRef.current = now;

    if (momentTimerRef.current) window.clearTimeout(momentTimerRef.current);
    const nextAction: MyBuddyAction = latest.status === "completed" ? "flag" : "repair";
    const duration = latest.status === "completed" ? SUCCESS_ACTION_MS : FAILURE_ACTION_MS;
    setMomentAction(nextAction);
    momentTimerRef.current = window.setTimeout(() => {
      setMomentAction(null);
      momentTimerRef.current = null;
    }, duration);
  }, [isHydrated, tasks, reducedMotion, isPageVisible]);

  return { action };
}
