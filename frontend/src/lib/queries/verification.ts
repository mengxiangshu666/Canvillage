// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
//
// Episode-level quality verification.
//
// The backend has shipped a full T1–T8 verification stack
// (`src/novelvideo/verification/`) with 13 live endpoints, but nothing in the
// product ever called them: the only human-facing "质检" surface was a
// prompt-only Agent skill that asked the model to eyeball what the backend
// already measures numerically. These hooks are the missing door.
//
// Two cost classes, and the UI must not blur them:
//   - pixel-level (similarity, sketch-colors): zero LLM, free, fast
//   - LLM review (score, consistency, continuity, episode-overview, sketch-select):
//     each burns real model calls
//
// Every endpoint answers HTTP 200 even when it fails, with `{ok:false, error}`.
// `unwrap` turns that into a real rejection so callers get one error path.
import { useMutation } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { p } from "@/lib/api-path";

type VerifyEnvelope<T> =
  | { ok: true; data: T }
  | { ok: false; error: string; details?: unknown };

function unwrap<T>(res: VerifyEnvelope<T>): T {
  if (!res.ok) {
    // `details` carries structured validation payloads (e.g. labels.jsonl
    // line errors) — surface them rather than a bare message.
    const detail = res.details ? ` (${JSON.stringify(res.details)})` : "";
    throw new Error(`${res.error}${detail}`);
  }
  return res.data;
}

// LLM-backed verification on a full episode can legitimately run for minutes;
// the 30 s default would abort mid-review on a healthy backend.
const VERIFY_TIMEOUT_MS = 15 * 60 * 1000;

// ── Response shapes (mirror src/novelvideo/verification/models.py) ───────────

export interface VerificationIssue {
  type: string;
  severity: "critical" | "warning" | "info" | string;
  description: string;
  confidence: number;
}

/** Single beat, one-shot: `beats/{n}/verify` and `beats/{n}/verify-frame`. */
export interface BeatVerifyResult {
  passed: boolean;
  score: number;
  issues: VerificationIssue[];
  summary: string;
  suggested_action: "none" | "regenerate" | "edit_script" | string;
  edit_suggestion?: string;
  beat_number?: number;
  verify_type?: string;
  image_path?: string;
  frame_path?: string;
  sketch_path?: string;
  description_used?: string;
  report_text?: string;
  report_path?: string;
}

/** T3: content-match scoring. */
export interface BeatScoreResult {
  beat_number: number;
  pool_id: string;
  script_match: number;
  identity_clarity: number;
  total: number;
  report_path?: string;
}

export interface ContinuityTransition {
  from_beat: number;
  to_beat: number;
  spatial_consistency: number;
  action_continuity: number;
  scene_transition: number;
  total: number;
  issues: string[];
}

/** T6: narration continuity across adjacent beats. */
export interface ContinuityResult {
  transitions: ContinuityTransition[];
  weak_transitions: number[];
  overall_score: number;
  report_path?: string;
}

export interface ConsistencyDimension {
  dimension: string;
  score: number;
  severity: string;
  description: string;
}

export interface CharacterConsistencyReport {
  character: string;
  identity_id: string;
  beats_checked: number[];
  dimensions: ConsistencyDimension[];
  face_score: number;
  clothing_score: number;
  passed: boolean;
}

export interface ConsistencyResult {
  characters: CharacterConsistencyReport[];
  summary: string;
  overall_passed: boolean;
  report_path?: string;
}

export interface SimilarityPair {
  beat_a: number;
  beat_b: number;
  similarity: number;
  warning: boolean;
}

/** T7: pixel-level duplicate detection, zero LLM. */
export interface SimilarityResult {
  pairs: SimilarityPair[];
  duplicate_beats: number[];
  overall_passed: boolean;
  report_path?: string;
}

export interface ColorMismatch {
  identity_id: string;
  color_hex: string;
  color_name: string;
  issue_type: "missing" | "extra" | string;
}

export interface ColorVerifyBeatResult {
  beat_number: number;
  status: "pass" | "fail" | "warn" | string;
  expected: string[];
  detected: string[];
  missing: ColorMismatch[];
  extra: ColorMismatch[];
  sketch_path: string;
}

/** Step 12.4: pixel-level colour cross-check, zero LLM. */
export interface ColorVerifyResult {
  total_beats: number;
  passed_beats: number;
  failed_beats: number;
  warned_beats: number;
  failed_beat_numbers: number[];
  beat_results: ColorVerifyBeatResult[];
  overall_passed: boolean;
  report_text?: string;
  report_path?: string;
}

export interface EpisodeIssue {
  beat_number: number;
  issue_type: string;
  severity: "critical" | "warning" | string;
  description: string;
  suggested_action: "swap_candidate" | "regenerate" | "info" | string;
  related_beats: number[];
}

/** T8: director's-eye review of the whole episode in one LLM call. */
export interface EpisodeOverviewResult {
  visual_rhythm: number;
  composition_diversity: number;
  narrative_arc: number;
  style_unity: number;
  total: number;
  issues: EpisodeIssue[];
  overall_passed: boolean;
  summary: string;
  report_text?: string;
  report_path?: string;
}

export interface SketchSelectCandidate {
  pool_id: string;
  path?: string;
  disqualified?: boolean;
  reason?: string;
  score?: { script_match: number; identity_clarity: number; total: number } | null;
}

export interface SketchSelectBeatResult {
  beat_number: number;
  candidates: SketchSelectCandidate[];
  selected_pool_id: string | null;
  selected_reason: string;
  selected_score: number | null;
  selected_passed_t1_t2: boolean | null;
  selected_is_provisional: boolean;
  selection_confidence: number;
  recommended_action: string;
}

/** Orchestrator: load candidates → T1/T2 eliminate → T3 score → T4 compare. */
export interface SketchSelectResult {
  total_beats: number;
  selected_count: number;
  beat_results: SketchSelectBeatResult[];
  needs_regeneration: number[];
  no_candidates: number[];
  all_disqualified: number[];
  accepted_beats: number[];
  provisional_beats: number[];
  summary: string;
  promoted_count?: number;
  report_path?: string;
}

// ── Hooks ───────────────────────────────────────────────────────────────────

export interface SketchSelectOptions {
  qualityThreshold?: number;
  scoreGapForAutoSelect?: number;
  colorPrefilter?: boolean;
  factCheck?: boolean;
  /**
   * Writes accepted selections into `sketches/epXXX/`. Opt-in, matching the
   * backend default of `false` — verifying must never silently rewrite the
   * sketches the user already chose.
   */
  promoteSelected?: boolean;
}

export function useVerifySimilarity(project: string, episode: number) {
  return useMutation({
    mutationFn: async () =>
      unwrap(
        await api
          .post(p`api/v1/projects/${project}/episodes/${episode}/verify/similarity`, {
            json: {},
            timeout: VERIFY_TIMEOUT_MS,
          })
          .json<VerifyEnvelope<SimilarityResult>>(),
      ),
  });
}

export function useVerifySketchColors(project: string, episode: number) {
  return useMutation({
    mutationFn: async () =>
      unwrap(
        await api
          .post(
            p`api/v1/projects/${project}/episodes/${episode}/verify/sketch-colors`,
            { json: {}, timeout: VERIFY_TIMEOUT_MS },
          )
          .json<VerifyEnvelope<ColorVerifyResult>>(),
      ),
  });
}

export function useVerifyContinuity(project: string, episode: number) {
  return useMutation({
    mutationFn: async (input?: { beatRange?: number[]; windowSize?: number }) =>
      unwrap(
        await api
          .post(p`api/v1/projects/${project}/episodes/${episode}/verify/continuity`, {
            json: {
              beat_range: input?.beatRange ?? [],
              window_size: input?.windowSize ?? 2,
            },
            timeout: VERIFY_TIMEOUT_MS,
          })
          .json<VerifyEnvelope<ContinuityResult>>(),
      ),
  });
}

export function useVerifyConsistency(
  project: string,
  episode: number,
  verifyType: "sketch" | "frame" = "sketch",
) {
  return useMutation({
    mutationFn: async () =>
      unwrap(
        await api
          .post(p`api/v1/projects/${project}/episodes/${episode}/verify/consistency`, {
            json: { verify_type: verifyType },
            timeout: VERIFY_TIMEOUT_MS,
          })
          .json<VerifyEnvelope<ConsistencyResult>>(),
      ),
  });
}

export function useVerifyEpisodeOverview(project: string, episode: number) {
  return useMutation({
    mutationFn: async () =>
      unwrap(
        await api
          .post(
            p`api/v1/projects/${project}/episodes/${episode}/verify/episode-overview`,
            { json: {}, timeout: VERIFY_TIMEOUT_MS },
          )
          .json<VerifyEnvelope<EpisodeOverviewResult>>(),
      ),
  });
}

export function useSketchSelect(project: string, episode: number) {
  return useMutation({
    mutationFn: async (options: SketchSelectOptions = {}) =>
      unwrap(
        await api
          .post(p`api/v1/projects/${project}/episodes/${episode}/verify/sketch-select`, {
            json: {
              quality_threshold: options.qualityThreshold ?? 7.0,
              score_gap_for_auto_select: options.scoreGapForAutoSelect ?? 1.0,
              color_prefilter: options.colorPrefilter ?? true,
              fact_check: options.factCheck ?? true,
              promote_selected: options.promoteSelected ?? false,
            },
            timeout: VERIFY_TIMEOUT_MS,
          })
          .json<VerifyEnvelope<SketchSelectResult>>(),
      ),
  });
}

export function useScoreBeat(project: string, episode: number, beatNum: number) {
  return useMutation({
    mutationFn: async (input?: { poolId?: string }) =>
      unwrap(
        await api
          .post(
            p`api/v1/projects/${project}/episodes/${episode}/beats/${beatNum}/score`,
            { json: { pool_id: input?.poolId ?? "" }, timeout: VERIFY_TIMEOUT_MS },
          )
          .json<VerifyEnvelope<BeatScoreResult>>(),
      ),
  });
}

export function useVerifyBeat(project: string, episode: number, beatNum: number) {
  return useMutation({
    mutationFn: async (input?: { type?: "sketch" | "frame" }) =>
      unwrap(
        await api
          .post(
            p`api/v1/projects/${project}/episodes/${episode}/beats/${beatNum}/verify`,
            { json: { type: input?.type ?? "sketch" }, timeout: VERIFY_TIMEOUT_MS },
          )
          .json<VerifyEnvelope<BeatVerifyResult>>(),
      ),
  });
}

export function useVerifyFrame(project: string, episode: number, beatNum: number) {
  return useMutation({
    mutationFn: async () =>
      unwrap(
        await api
          .post(
            p`api/v1/projects/${project}/episodes/${episode}/beats/${beatNum}/verify-frame`,
            { json: {}, timeout: VERIFY_TIMEOUT_MS },
          )
          .json<VerifyEnvelope<BeatVerifyResult>>(),
      ),
  });
}
