// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import type { Beat } from "@/types/episode";
import type { Task } from "@/types/task";
import type { BeatStageState, BeatStates } from "@/types/beat-state";
import {
  EPISODE_STAGE_REGISTRY,
  type StageDef,
  type StageId,
} from "@/lib/episode-stage-registry";
import { SCOPED_TASK_TYPES } from "@/lib/task-types";

const ACTIVE_STATUSES = new Set(["submitting", "queued", "pending", "starting", "running"]);

// Module-level constants — computed once, never per-call.
const STAGES: Array<Exclude<StageId, "compose">> = [
  "script",
  "sketch",
  "audio",
  "video",
];

const STAGE_DEFS: Record<Exclude<StageId, "compose">, StageDef> = {
  script: EPISODE_STAGE_REGISTRY.find((s) => s.id === "script")!,
  sketch: EPISODE_STAGE_REGISTRY.find((s) => s.id === "sketch")!,
  audio: EPISODE_STAGE_REGISTRY.find((s) => s.id === "audio")!,
  video: EPISODE_STAGE_REGISTRY.find((s) => s.id === "video")!,
};

/**
 * Pure derivation — exported for unit testing. `useBeatStates` is a thin
 * memoized wrapper on top that subscribes to TanStack Query caches.
 */
export function deriveBeatStates(beats: Beat[], tasks: Task[]): BeatStates {
  // Pre-index tasks by task_type — O(T) build, then O(1) per lookup.
  const taskIndex = new Map<string, Task[]>();
  for (const task of tasks) {
    let bucket = taskIndex.get(task.task_type);
    if (!bucket) {
      bucket = [];
      taskIndex.set(task.task_type, bucket);
    }
    bucket.push(task);
  }

  const result: BeatStates = {};

  for (const beat of beats) {
    const stateForBeat: Record<Exclude<StageId, "compose">, BeatStageState> = {
      script: "missing",
      sketch: "missing",
      audio: "missing",
      video: "missing",
    };

    for (const stage of STAGES) {
      stateForBeat[stage] = deriveSingle(stage, beat, taskIndex, STAGE_DEFS[stage]);
    }

    result[beat.beat_number] = stateForBeat;
  }

  return result;
}

function deriveSingle(
  stage: Exclude<StageId, "compose">,
  beat: Beat,
  taskIndex: Map<string, Task[]>,
  def: StageDef,
): BeatStageState {
  // 1. ready — evaluate first, never mask existing assets
  if (stage === "script") {
    // Script readiness is gauged by the visual description: every beat (incl.
    // silent / action shots that carry no spoken line) is "ready" once it has a
    // 画面描述. Spoken text (narration_segment) is optional and intentionally
    // not required here. See script-beat-preview's readiness display.
    if (beat.visual_description && beat.visual_description.trim().length > 0) {
      return "ready";
    }
  } else if (stage === "sketch" && beat.sketch_url) {
    return "ready";
  } else if (stage === "audio" && beat.audio_url) {
    return "ready";
  } else if (stage === "video" && beat.video_url) {
    return "ready";
  }

  // Collect tasks relevant to this stage from the pre-built index.
  const taskTypes = def.taskTypes as readonly string[];
  const relevant: Task[] = [];
  for (const tt of taskTypes) {
    const bucket = taskIndex.get(tt);
    if (bucket) {
      for (const t of bucket) relevant.push(t);
    }
  }

  // 2. generating — scoped task on this beat, OR batch task with this beat still missing
  const active = relevant.find(
    (t) =>
      ACTIVE_STATUSES.has(t.status) &&
      taskAppliesToBeat(stage, t, beat.beat_number),
  );
  if (active) return "generating";

  // 3. failed — attribute grid/direct sketch tasks only to their target beats.
  const failed = relevant.find(
    (t) =>
      t.status === "failed" &&
      taskAppliesToBeat(stage, t, beat.beat_number) &&
      (isScopedTaskType(t.task_type) ||
        (stage === "sketch" &&
          t.task_type === "sketch_generation" &&
          (t.beat_num !== undefined || Boolean(t.scope)))),
  );
  if (failed) return "failed";

  return "missing";
}

function isScopedTaskType(type: string): boolean {
  return SCOPED_TASK_TYPES.has(type as never);
}

/**
 * Resolve the per-beat boundary for sketch tasks.
 *
 * `sketch_generation` is a batch task type, but the generation route now
 * dispatches one row per grid (`scope=grid_N`). Those rows carry their exact
 * beat list in the runner log once execution starts (and newer workers may
 * also expose it as metadata/result). Treating a scoped grid row as an
 * episode-wide batch makes one running grid paint every missing beat as
 * generating and hides failures in sibling grids.
 */
function taskAppliesToBeat(
  stage: Exclude<StageId, "compose">,
  task: Task,
  beatNumber: number,
): boolean {
  if (isScopedTaskType(task.task_type)) {
    return task.beat_num === beatNumber;
  }
  if (stage !== "sketch" || task.task_type !== "sketch_generation") {
    return true;
  }

  const explicitBeat = positiveBeatNumber(task.beat_num);
  if (explicitBeat !== null) return explicitBeat === beatNumber;

  const scope = String(task.scope ?? "").trim();
  if (!scope) return true;

  const targetBeats = sketchTaskBeatNumbers(task);
  if (targetBeats.length > 0) return targetBeats.includes(beatNumber);

  // A scoped grid/direct-render row without target metadata is not safe to
  // spread across the episode. Keep the stage-level task center as the
  // progress indicator until the row exposes its target list.
  if (/^grid_\d+$/.test(scope) || scope.startsWith("director_control_to_sketch:")) {
    return false;
  }
  return true;
}

function sketchTaskBeatNumbers(task: Task): number[] {
  const values: number[] = [];
  const addFrom = (value: unknown) => {
    if (!Array.isArray(value)) return;
    for (const item of value) {
      const parsed = positiveBeatNumber(item);
      if (parsed !== null && !values.includes(parsed)) values.push(parsed);
    }
  };
  const readContainer = (value: unknown) => {
    if (!value || typeof value !== "object") return;
    const record = value as Record<string, unknown>;
    for (const key of ["beat_numbers", "target_beat_numbers", "updated_beats", "generated_beats"]) {
      addFrom(record[key]);
    }
  };

  readContainer(task);
  readContainer(task.metadata);
  readContainer(task.result);
  if (task.result && typeof task.result === "object") {
    readContainer((task.result as Record<string, unknown>).task_metadata);
  }
  if (values.length > 0) return values;

  for (const line of task.logs ?? []) {
    const match = /\bbeats?\s*\[([^\]]*)\]/i.exec(String(line));
    if (!match) continue;
    for (const token of match[1].split(",")) {
      const parsed = positiveBeatNumber(token);
      if (parsed !== null && !values.includes(parsed)) values.push(parsed);
    }
  }
  return values;
}

function positiveBeatNumber(value: unknown): number | null {
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : null;
}
