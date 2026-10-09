// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab

export type CanvasAgentRunMode = "draft" | "auto";

/**
 * 单个自动回合最多能声明的付费媒体启动次数，与服务端
 * `MAX_PAID_MEDIA_STARTS_PER_TURN` 是同一份契约。一部 12 镜短片要
 * 12 张分镜图 + 12 条逐镜视频，上限低于 24 会静默截断成片。
 */
export const CANVAS_AGENT_AUTO_PAID_START_LIMIT = 64;
/**
 * Freezone is a production canvas: a normal order should be executable end
 * to end. Draft remains an explicit opt-out for planning-only turns.
 */
export const DEFAULT_CANVAS_AGENT_RUN_MODE: CanvasAgentRunMode = "auto";

const CANVAS_AGENT_RUN_MODE_KEY = "village_canvas_agent_run_mode";

export const CANVAS_AGENT_RUN_MODE_OPTIONS: ReadonlyArray<{
  id: CanvasAgentRunMode;
  label: string;
  shortLabel: string;
  description: string;
}> = [
  {
    id: "draft",
    label: "草稿模式",
    shortLabel: "草稿",
    description: "只搭节点、连工作流、填参数，不点生成。",
  },
  {
    id: "auto",
    label: "实战生成模式",
    shortLabel: "实战",
    description: "通过校验后直接提交真实图片、视频和音频任务。",
  },
] as const;

export function normalizeCanvasAgentRunMode(value: unknown): CanvasAgentRunMode {
  return value === "draft" ? "draft" : DEFAULT_CANVAS_AGENT_RUN_MODE;
}

export function loadCanvasAgentRunMode(): CanvasAgentRunMode {
  if (typeof window === "undefined") return DEFAULT_CANVAS_AGENT_RUN_MODE;
  try {
    const stored = window.localStorage.getItem(CANVAS_AGENT_RUN_MODE_KEY);
    return stored === null ? DEFAULT_CANVAS_AGENT_RUN_MODE : normalizeCanvasAgentRunMode(stored);
  } catch {
    return DEFAULT_CANVAS_AGENT_RUN_MODE;
  }
}

export function saveCanvasAgentRunMode(mode: CanvasAgentRunMode): CanvasAgentRunMode {
  const normalized = normalizeCanvasAgentRunMode(mode);
  if (typeof window !== "undefined") {
    try {
      window.localStorage.setItem(CANVAS_AGENT_RUN_MODE_KEY, normalized);
    } catch {
      // Runtime mode still updates in memory when storage is unavailable.
    }
  }
  return normalized;
}

export function canvasAgentRunModeLabel(mode: CanvasAgentRunMode): string {
  return CANVAS_AGENT_RUN_MODE_OPTIONS.find((option) => option.id === mode)?.label ?? "实战生成模式";
}

