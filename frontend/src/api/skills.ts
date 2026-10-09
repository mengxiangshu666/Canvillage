// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { apiCall, apiClient } from "./client";
import type {
  ResolvedSkillInput,
  SkillDefinition,
  SkillMediaType,
  SkillOutputRole,
} from "@/features/freezone/context/skillRoles";

const REGISTRY_CACHE_TTL_MS = 5 * 60 * 1000;

let registryCache:
  | {
      loadedAt: number;
      value: SkillDefinition[];
    }
  | null = null;
let registryInFlight: Promise<SkillDefinition[]> | null = null;

export interface SkillRunRequest {
  schema_version?: string;
  skill_node_id: string;
  canvas_id?: string;
  idempotency_key?: string;
  resolved_inputs: ResolvedSkillInput[];
  parameters?: Record<string, unknown>;
}

export interface SkillRunResponse {
  schema_version?: string;
  run_id: string;
  status: string;
  task_key?: string | null;
  task_type?: string | null;
  job_id?: string | null;
  error?: SkillErrorEnvelope | null;
}

export interface SkillErrorEnvelope {
  code: string;
  category: string;
  message: string;
  retryable: boolean;
  user_action_hint?: string | null;
}

export interface SkillRunOutput {
  schema_version?: string;
  role: SkillOutputRole;
  media_type: SkillMediaType;
  node_type: string;
  pushable: boolean;
  image_url?: string | null;
  text?: string | null;
  json_value?: unknown;
  graph_patch?: CanvasGraphPatch | null;
  slot_target?: Record<string, unknown> | null;
  [key: string]: unknown;
}

export interface CanvasGraphPatchOperation {
  op:
    | "add_node"
    | "update_node"
    | "delete_node"
    | "add_edge"
    | "update_edge"
    | "delete_edge";
  node?: Record<string, unknown> | null;
  edge?: Record<string, unknown> | null;
  node_id?: string | null;
  edge_id?: string | null;
  data?: Record<string, unknown> | null;
  [key: string]: unknown;
}

export interface CanvasGraphPatch {
  schema_version: "graph_patch.v1" | string;
  operations: CanvasGraphPatchOperation[];
  requires_apply: boolean;
  summary?: string | null;
}

export interface SkillRunResult {
  schema_version?: string;
  run_id: string;
  status: string;
  outputs: SkillRunOutput[];
  task_key?: string | null;
  task_type?: string | null;
  job_id?: string | null;
  error?: SkillErrorEnvelope | string | null;
}

// 输入的必填/可选一律以后端 skill 注册表为准（`skill_registry.py` 的 `_input(..., required=)`），
// 前端不再做「必填覆盖」。原先这里有一张 REQUIRED_INPUT_OVERRIDES 表，键写成
// `freezone_scene_360`（task_type 形式），而注册表里真实的 skill id 是点号形式
// `freezone.scene_360` —— 键从来没命中过，是一段死代码。而且它想强制的三个输入里
// 有两个（scene 提示词、scene_reverse_master 背面图）后端本来就标成可选，硬提成必填
// 反而会把「只给场景主图」这个正当用法挡掉。

export async function getSkillRegistry(): Promise<SkillDefinition[]> {
  const now = Date.now();
  if (registryCache && now - registryCache.loadedAt < REGISTRY_CACHE_TTL_MS) {
    return registryCache.value;
  }
  if (registryInFlight) {
    return registryInFlight;
  }

  registryInFlight = apiCall<SkillDefinition[]>("freezone/skills")
    .then((value) => {
      registryCache = { loadedAt: Date.now(), value };
      return value;
    })
    .finally(() => {
      registryInFlight = null;
    });
  return registryInFlight;
}

export async function runSkill(
  project: string,
  skillId: string,
  request: SkillRunRequest,
): Promise<SkillRunResponse> {
  return await apiClient(
    `projects/${encodeURIComponent(project)}/freezone/skills/${encodeURIComponent(skillId)}/run`,
    { method: "POST", json: request },
  ).json<SkillRunResponse>();
}

export async function getSkillRunResult(
  project: string,
  runId: string,
): Promise<SkillRunResult> {
  return await apiClient(
    `projects/${encodeURIComponent(project)}/freezone/skills/runs/${encodeURIComponent(runId)}/result`,
  ).json<SkillRunResult>();
}
