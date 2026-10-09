// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab

export const STORY_LAB_WORK_TYPES = [
  "long_novel",
  "short_novel",
  "screenplay",
  "micro_drama",
  "comic_narration",
] as const;

export type StoryLabWorkType = (typeof STORY_LAB_WORK_TYPES)[number];
export type StoryLabStyleMode = "infer" | "confirmed";
export type StoryLabGenerationStage = "bible" | "outline" | "draft" | "audit";
export type StoryLabArtifactStatus =
  | "pending"
  | "generating"
  | "completed"
  | "failed";

export interface StoryLabConfig {
  title: string;
  logline: string;
  work_type: StoryLabWorkType;
  genre: string;
  theme: string;
  target_units: number;
  target_length: number;
  target_duration_seconds: number | null;
  point_of_view: string;
  audience: string;
  style_mode: StoryLabStyleMode;
  style_id: string;
  style_name: string;
  style_prompt: string;
}

export interface StoryLabProvenance {
  model?: string | null;
  prompt_version?: string | null;
  generated_at?: string | null;
  updated_at?: string | null;
  [key: string]: unknown;
}

export interface StoryLabArtifact {
  stage: StoryLabGenerationStage;
  status?: StoryLabArtifactStatus;
  result: Record<string, unknown>;
  editor_note?: string | null;
  prompt_version?: string | null;
  model?: string | null;
  source?: string | null;
  provenance?: StoryLabProvenance | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export type StoryLabArtifacts = Partial<
  Record<StoryLabGenerationStage, StoryLabArtifact>
>;

export interface StoryLabState {
  config: StoryLabConfig | null;
  stages: StoryLabArtifacts;
  updated_at?: string | null;
}

export interface StoryLabTaskReceipt {
  task_type: string;
  stage?: StoryLabGenerationStage;
  scope?: string;
  task_id?: string;
  task_key?: string;
  backend?: string;
  queue?: string;
}

export interface StoryLabExportReceipt {
  filename: string;
  download_url?: string | null;
  url?: string | null;
}

export interface StoryLabPublishReceipt extends StoryLabTaskReceipt {
  filename: string;
}

export const EMPTY_STORY_LAB_CONFIG: StoryLabConfig = {
  title: "",
  logline: "",
  work_type: "micro_drama",
  genre: "",
  theme: "",
  target_units: 12,
  target_length: 1500,
  target_duration_seconds: null,
  point_of_view: "third_person",
  audience: "",
  style_mode: "infer",
  style_id: "",
  style_name: "",
  style_prompt: "",
};
