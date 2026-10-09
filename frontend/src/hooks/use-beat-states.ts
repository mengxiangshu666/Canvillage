// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useMemo } from "react";
import { useEpisodeBeats } from "@/lib/queries/episodes";
import { useProject } from "@/lib/queries/projects";
import { useTasks } from "@/lib/queries/tasks";
import { deriveBeatStates } from "@/lib/derive-beat-states";
import type { BeatStates, EpisodeCounts, StageCount } from "@/types/beat-state";
import type { Beat } from "@/types/episode";
import type { StageId } from "@/lib/episode-stage-registry";

interface UseBeatStatesResult {
  states: BeatStates;
  counts: EpisodeCounts;
  loading: boolean;
}

/**
 * Derive per-beat × per-stage state for an episode.
 *
 * Subscribes to `useEpisodeBeats` and `useTasks`; memoizes the derivation so
 * consumers don't recompute on every unrelated query tick.
 */
export function useBeatStates(project: string, episode: number): UseBeatStatesResult {
  const beatsRes = useEpisodeBeats(project, episode);
  // Filter at the query layer so poll ticks that don't touch this episode
  // don't produce a new tasks reference that would cascade into a re-derive.
  const tasksRes = useTasks({ project, episode });
  // 精品剧 (spine_template === "drama") bakes narration into the rendered video,
  // so per-beat audio is not a compose prerequisite for it.
  const configRes = useProject(project);
  const requireAudio = configRes.data?.data?.spine_template !== "drama";

  return useMemo(() => {
    const beats = beatsRes.data?.data ?? [];
    const tasks = tasksRes.data?.data ?? [];

    const states = applyAudioCompletionContract(deriveBeatStates(beats, tasks), beats);
    const counts = computeCounts(states, beats, requireAudio);

    return {
      states,
      counts,
      loading: beatsRes.isLoading || tasksRes.isLoading,
    };
  }, [beatsRes.data, tasksRes.data, beatsRes.isLoading, tasksRes.isLoading, requireAudio, project, episode]);
}

export function applyAudioCompletionContract(
  states: BeatStates,
  beats: Beat[],
): BeatStates {
  let normalized = states;
  for (const beat of beats) {
    if (beatRequiresAudio(beat)) continue;
    const stages = normalized[beat.beat_number];
    if (!stages || stages.audio === "ready") continue;
    if (normalized === states) normalized = { ...states };
    normalized[beat.beat_number] = { ...stages, audio: "ready" };
  }
  return normalized;
}

export function beatRequiresAudio(
  beat: Pick<Beat, "audio_type" | "narration_segment">,
): boolean {
  const audioType = (beat.audio_type ?? "").trim();
  if (audioType === "silence" || audioType === "action") return false;
  return Boolean((beat.narration_segment ?? "").trim());
}

export function computeCounts(
  states: BeatStates,
  beats: Beat[],
  requireAudio: boolean,
): EpisodeCounts {
  const totalBeats = beats.length;
  const stages: Array<Exclude<StageId, "compose">> = ["script", "sketch", "audio", "video"];
  const per: Record<Exclude<StageId, "compose">, StageCount> = {
    script: { ready: 0, total: totalBeats, active: 0, failed: 0 },
    sketch: { ready: 0, total: totalBeats, active: 0, failed: 0 },
    audio: { ready: 0, total: totalBeats, active: 0, failed: 0 },
    video: { ready: 0, total: totalBeats, active: 0, failed: 0 },
  };

  for (const [, stagesState] of Object.entries(states)) {
    for (const stage of stages) {
      const s = stagesState[stage];
      if (s === "ready") per[stage].ready += 1;
      else if (s === "generating") per[stage].active += 1;
      else if (s === "failed") per[stage].failed += 1;
    }
  }

  // Compose readiness mirrors the BE actor's own pre-flight
  // (the backend ray_tasks.py _run_compose_episode): only the audio and
  // video files per beat are required. Sketch is an upstream input to video
  // generation — once the video clip exists, compose (which concatenates
  // clips + audio) no longer needs the sketch/keyframe image.
  // 精品剧 (requireAudio === false) embeds narration in the video, so audio is
  // not a compose blocker for it.
  const missing: EpisodeCounts["compose"]["missing"] = [];
  for (const [beatNumStr, stagesState] of Object.entries(states)) {
    const blockers: Array<Exclude<StageId, "compose">> = [];
    if (requireAudio && stagesState.audio !== "ready") blockers.push("audio");
    if (stagesState.video !== "ready") blockers.push("video");
    if (blockers.length > 0) {
      missing.push({ beatNum: Number(beatNumStr), stages: blockers });
    }
  }

  return {
    ...per,
    compose: {
      ready: missing.length === 0 && totalBeats > 0,
      missing,
    },
  };
}
