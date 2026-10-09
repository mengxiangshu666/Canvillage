// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import type { TaskState } from "./types";
import { stageForTaskType } from "@/lib/episode-stage-registry";

export const isTerminal = (t: TaskState): boolean =>
  t.status === "completed" || t.status === "failed" || t.status === "cancelled";

export const isActive = (t: TaskState): boolean =>
  t.status === "submitting" ||
  t.status === "queued" ||
  t.status === "pending" ||
  t.status === "starting" ||
  t.status === "running" ||
  t.status === "waiting";

export const ageMs = (t: TaskState, now: number = Date.now()): number =>
  now - Date.parse(t.updated_at);

type TFn = (key: string, options?: Record<string, unknown>) => string;

const ACTIVITY_I18N_KEYS: Record<string, string> = {
  retrieving_model_knowledge: "retrievingModelKnowledge",
  translating_text: "translatingText",
  validating_timeline: "validatingTimeline",
  analyzing_video: "analyzingVideo",
  upscaling_video: "upscalingVideo",
  separating_audio_video: "separatingAudioVideo",
  preparing_audio_speech: "preparingAudioSpeech",
  calling_tts_provider: "callingTtsProvider",
  generating_story_script: "generatingStoryScript",
  reverse_prompting_image: "reversePromptingImage",
  completed: "completed",
  complete: "completed",
  done: "completed",
  failed: "failed",
};

function isInternalRunScope(scope: string | null | undefined): boolean {
  return /^scene_run_[a-z0-9]+$/i.test(scope ?? "") || /^prop_run_[a-z0-9]+$/i.test(scope ?? "");
}

export const displayLabel = (t: TaskState, tFn: TFn): string => {
  if (t.display_name) return t.display_name;

  const parts = [t.task_type_label || tFn(`tasks.types.${t.task_type}`)];
  if (t.episode > 0) parts.push(`ep${t.episode}`);
  if (t.beat_num != null) parts.push(`beat ${t.beat_num}`);
  if (t.scope && !isInternalRunScope(t.scope)) parts.push(t.scope);
  return parts.join(" · ");
};

/** Convert backend runner tokens into copy that is readable in the task UI. */
export function taskActivityLabel(task: TaskState, tFn: TFn): string {
  const raw = String(task.current_task || "").trim();
  const normalized = raw.toLowerCase().replace(/[\s-]+/g, "_");
  const activityKey = ACTIVITY_I18N_KEYS[normalized];
  if (activityKey) {
    return tFn(`taskCenter.activity.${activityKey}`, { defaultValue: raw });
  }
  if (!raw) return tFn(`taskCenter.status.${task.status}`);
  if (task.status === "completed" && /^(?:完成|completed|complete|done)$/i.test(raw)) {
    return tFn("taskCenter.activity.completed", { defaultValue: raw });
  }
  if (task.status === "failed" && /^(?:失败|failed|error)$/i.test(raw)) {
    return tFn("taskCenter.activity.failed", { defaultValue: raw });
  }
  return raw;
}

function taskMetadata(task: TaskState): Record<string, unknown> {
  const result = task.result && typeof task.result === "object"
    ? task.result as Record<string, unknown>
    : null;
  const resultMetadata = result?.task_metadata && typeof result.task_metadata === "object"
    ? result.task_metadata as Record<string, unknown>
    : null;
  return { ...(resultMetadata ?? {}), ...(task.metadata ?? {}) };
}

export function taskMetadataString(task: TaskState, key: string): string {
  const value = taskMetadata(task)[key];
  return typeof value === "string" ? value.trim() : "";
}

export function taskCanvasNodeId(task: TaskState): string | null {
  return taskMetadataString(task, "node_id") || null;
}

export interface OriginDeepLink {
  to: string;
  params: Record<string, string>;
}

export const originDeepLink = (t: TaskState): OriginDeepLink | null => {
  const stage = stageForTaskType(t.task_type);
  if (!stage) return null;
  // stage.routeSegment already starts with "/" (e.g., "/sketches")
  return {
    to: `/projects/$project/episodes/$episode${stage.routeSegment}`,
    params: { project: t.project_id ?? t.project, episode: String(t.episode) },
  };
};
