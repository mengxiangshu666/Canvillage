import { apiClient } from "@/api/client";

export type AgentMemoryStatus =
  | "candidate"
  | "validated"
  | "confirmed"
  | "deprecated"
  | "conflicted"
  | "archived";

export type AgentMemoryScope = "professional" | "user" | "project";
export type AgentMemoryCreateKind = "learned_rule" | "preference" | "verified_experience";

export interface AgentMemoryItem {
  id: number;
  scope_kind: AgentMemoryScope;
  scope_id: string | null;
  kind: string;
  source: string;
  content: string;
  status: AgentMemoryStatus;
  confidence: number;
  locked: boolean;
  applies_when: Record<string, unknown>;
  provenance: {
    source: string;
    source_id: string;
    evidence: Array<Record<string, unknown>>;
    origin_project?: string;
    origin_event_id?: number;
    distilled: boolean;
    compiled: boolean;
    normalized?: boolean;
    distillation_level?: string;
    memory_schema?: string;
    memory_key?: string;
    rule_type?: string;
    hook_id?: string;
    hook_mode?: string;
    executable?: boolean;
    validation?: Record<string, unknown>;
    action: string[];
    avoid: string[];
    compile_reason?: string;
    episode_id?: number;
    feedback_event_id?: number;
    feedback_outcome?: "positive" | "negative";
    origin_turn_id?: string;
    run_id?: string;
  };
  evidence_count: number;
  retrieved_count: number;
  applied_count: number;
  positive_count: number;
  negative_count: number;
  last_verified_at: string | null;
  version: number;
  promoted_from_id: number | null;
  supersedes_id: number | null;
  created_at: string;
  updated_at: string;
}

export interface AgentMemoryStats {
  total: number;
  effective: number;
  compiled: number;
  embedded: number;
  archived: number;
  pending_events: number;
  successful_applications: number;
  episode_count: number;
  feedback_count: number;
  positive_feedback: number;
  negative_feedback: number;
  verifier_events: number;
  workflow_evidence: number;
  validated_experience: number;
  by_status: Record<string, number>;
  by_scope: Record<string, number>;
  by_kind: Record<string, number>;
}

export interface AgentMemoryPreviewResult {
  schema: "xiaoshu.memory_hook_preview.v1";
  original: string;
  transformed: string;
  applied_rules: string[];
  warnings: string[];
  requires_review: boolean;
  diff_summary: string;
  project_id: string;
  task_stage: string;
  node_type: string;
}

/**
 * Taste-graph node — the backend's *evidence-backed* projection of eligible
 * preference memories (`novelvideo/chat/taste_graph.py`).
 *
 * This is deliberately not the same thing as the memory list above: the list
 * shows every stored record, while this only contains records that pass the
 * eligibility gate (structured schema or locked or has evidence, positive
 * outweighing negative) and attaches a derived strength/confidence. The
 * endpoint has been live since the TasteGraph-Skill integration but no UI ever
 * read it.
 */
export interface TasteGraphNode {
  id: string;
  memory_id: number;
  label: string;
  category: string;
  description: string;
  polarity: "love" | "anti";
  confidence: "H" | "M" | "L";
  strength: number;
  evidence_ids: string[];
  domains: string[];
  status: AgentMemoryStatus;
  locked: boolean;
}

export interface TasteGraph {
  schema: "taste_graph.v1";
  revision: string;
  project_id: string;
  source: {
    project: string;
    commit: string;
    license: string;
    integration: string;
  };
  hard_loves: TasteGraphNode[];
  hard_antis: TasteGraphNode[];
  consultation: { do: string[]; avoid: string[] };
  affinity_graph: {
    nodes: Array<Record<string, unknown>>;
    edges: Array<Record<string, unknown>>;
  };
  stats: {
    eligible: number;
    rejected: number;
    love_count: number;
    anti_count: number;
  };
}

/**
 * The growth-distiller role contract. Reports which model resolves the
 * distillation role and whether it is unconfigured — this is the diagnostic
 * you need when memories sit in `pending_distillation` forever.
 */
export interface GrowthMemoryContract {
  role: string;
  modelEnv: string;
  thinkingLevelEnv: string;
  modelRef: string;
  modelSource: "explicit" | "default_text_model" | "unconfigured";
  autoTextModelEnv: string;
  transport: string;
  credentialSource: string;
  sideEffects: string[];
  schemaVersion: string;
}

export interface AgentMemoryLearningAccepted {
  accepted: true;
  event_id: number;
  status: "pending_distillation";
  receipt: AgentMemoryDistillationReceipt;
}

export type AgentMemoryDistillationStatus =
  | "pending"
  | "processing"
  | "retryable"
  | "compiled"
  | "evidence_only"
  | "ignored"
  | "needs_review";

export interface AgentMemoryDistillationReceipt {
  schema: "growth_distillation_receipt.v1";
  event_id: number;
  project_id: string;
  task_id: string;
  status: AgentMemoryDistillationStatus;
  decision: string;
  terminal: boolean;
  memory_id: number;
  attempt_count: number;
  result_available: boolean;
  reason: string;
  created_at: string;
  processed_at: string;
  next_attempt_at: string;
}

export async function listAgentMemories(input: {
  status?: AgentMemoryStatus;
  scopeKind?: AgentMemoryScope;
  query?: string;
  limit?: number;
} = {}): Promise<AgentMemoryItem[]> {
  const searchParams = new URLSearchParams();
  if (input.status) searchParams.set("status", input.status);
  if (input.scopeKind) searchParams.set("scope_kind", input.scopeKind);
  if (input.query?.trim()) searchParams.set("query", input.query.trim());
  searchParams.set("limit", String(input.limit ?? 200));
  const result = await apiClient(`chat/memories?${searchParams.toString()}`).json<{
    items: AgentMemoryItem[];
  }>();
  return result.items;
}

export async function getAgentMemoryStats(): Promise<AgentMemoryStats> {
  return apiClient("chat/memories/stats").json<AgentMemoryStats>();
}

/**
 * Project preferences only appear when `project` matches their owning project;
 * user/professional preferences are global. Pass the current project so the
 * panel reflects what this project actually inherits.
 */
export async function getTasteGraph(project?: string): Promise<TasteGraph> {
  const searchParams = new URLSearchParams();
  if (project?.trim()) searchParams.set("project", project.trim());
  const suffix = searchParams.toString();
  return apiClient(`chat/memories/taste-graph${suffix ? `?${suffix}` : ""}`).json<TasteGraph>();
}

export async function getGrowthMemoryContract(): Promise<GrowthMemoryContract> {
  return apiClient("chat/memories/growth-contract").json<GrowthMemoryContract>();
}

export async function previewAgentMemoryHooks(input: {
  text: string;
  task_stage?: string;
  node_type?: string;
  duration_sec?: number;
  references?: Array<Record<string, unknown>>;
  project_id?: string;
}): Promise<AgentMemoryPreviewResult> {
  return apiClient("chat/memories/preview", {
    method: "POST",
    json: input,
  }).json<AgentMemoryPreviewResult>();
}

export async function createAgentMemory(input: {
  content: string;
  kind: AgentMemoryCreateKind;
  locked?: boolean;
}): Promise<AgentMemoryLearningAccepted> {
  return apiClient("chat/memories", {
    method: "POST",
    json: input,
  }).json<AgentMemoryLearningAccepted>();
}

export async function createManualAgentMemory(input: {
  content: string;
  kind: AgentMemoryCreateKind;
  locked?: boolean;
  applies_when?: Record<string, unknown>;
}): Promise<AgentMemoryItem> {
  return apiClient("chat/memories/manual", {
    method: "POST",
    json: input,
  }).json<AgentMemoryItem>();
}

export async function getAgentMemoryEventReceipt(
  eventId: number,
): Promise<AgentMemoryDistillationReceipt> {
  const result = await apiClient(`chat/memories/events/${eventId}`).json<{
    data: AgentMemoryDistillationReceipt;
  }>();
  return result.data;
}

export async function updateAgentMemory(
  memoryId: number,
  input: Partial<Pick<AgentMemoryItem, "content" | "status" | "locked" | "confidence">>,
): Promise<AgentMemoryItem> {
  return apiClient(`chat/memories/${memoryId}`, {
    method: "PATCH",
    json: input,
  }).json<AgentMemoryItem>();
}

export async function promoteAgentMemory(memoryId: number): Promise<AgentMemoryItem> {
  return apiClient(`chat/memories/${memoryId}/promote`, {
    method: "POST",
  }).json<AgentMemoryItem>();
}

export async function addAgentMemoryEvidence(
  memoryId: number,
  input: {
    outcome: "positive" | "negative";
    evidence_ref: string;
    project?: string;
    task_id?: string;
    notes?: string;
  },
): Promise<AgentMemoryItem> {
  return apiClient(`chat/memories/${memoryId}/evidence`, {
    method: "POST",
    json: input,
  }).json<AgentMemoryItem>();
}

export async function deleteAgentMemory(memoryId: number): Promise<void> {
  await apiClient.delete(`chat/memories/${memoryId}`);
}
