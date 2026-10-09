// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab

export interface ProductionStageSummary {
  id: string;
  label: string;
  status: "ready" | "pending";
  count: number;
}

export interface ProductionBlocker {
  code: string;
  message: string;
  count?: number;
  task_type?: string;
  episode?: number;
}

export interface ProductionNextAction {
  id: string;
  label: string;
  route: string;
}

export interface ProductionOverview {
  project_id: string;
  generated_at: string;
  schema_version: "production-overview.v1";
  counts: Record<string, number>;
  task_summary: Record<string, number>;
  stage_summary: ProductionStageSummary[];
  blockers: ProductionBlocker[];
  attention: ProductionBlocker[];
  power_user_mode: boolean;
  next_actions: ProductionNextAction[];
}

export type ProductionRunMode = "best" | "next";
export type ProductionEntryMode = "novel_adapt" | "original";
export type ProductionRunStatus =
  | "running"
  | "pausing"
  | "paused"
  | "blocked"
  | "completed"
  | "failed"
  | "cancelled";
export type ProductionRunCommand =
  | "pause"
  | "resume"
  | "cancel"
  | "retry"
  | "skip"
  | "take_over";
export type ProductionAspectRatio = "9:16" | "16:9" | "2:3" | "1:1";

export interface ProductionControlStart {
  mode: ProductionRunMode;
  entry_mode?: ProductionEntryMode;
  uploaded_filename: string;
  target_episodes: number;
  episode: number | null;
  image_model: string;
  video_backend: string;
  model_bindings?: Partial<Record<"director" | "text" | "vision" | "image" | "video" | "audio" | "embedding", string>>;
  aspect_ratio: ProductionAspectRatio | null;
  auto_generate_paid_media: boolean;
  confirmed_paid_media: boolean;
  goal?: string;
  original_script?: string;
}

export interface ProductionModelBinding {
  role: string;
  kind: string;
  registry_id: string;
  catalog_id?: string;
  upstream_model: string;
  protocol: string;
  effective_protocol?: string;
  capabilities?: {
    runtime_ready?: boolean;
    supported_modes?: string[];
    reference_limits?: Record<string, Record<string, number>>;
    parameter_defaults?: Record<string, unknown>;
    aspect_ratio_options?: string[];
    resolution_options?: string[];
    size_options?: string[];
    size_field?: string;
    native_audio?: string;
    min_duration?: number;
    max_duration?: number;
    protocol?: string;
    effective_protocol?: string;
    family?: string;
    catalog_verification?: string;
  };
}

export type ProductionControlSettings = ProductionControlStart & {
  model_plan_revision?: string;
  model_plan_snapshot?: {
    model_plan_revision: string;
    bindings: Record<string, ProductionModelBinding>;
    missing_roles: string[];
  };
};

export interface ProductionControlRun {
  id: string;
  mode: ProductionRunMode;
  status: ProductionRunStatus;
  current_action: string;
  current_task_ids: string[];
  settings: Partial<ProductionControlSettings>;
  result: Record<string, unknown>;
  error: string;
  created_at: string;
  updated_at: string;
}

export interface ProductionChildExecution {
  id: string;
  parent_run_id: string;
  stage_id: "story" | "assets" | "storyboard" | "making" | string;
  child_type: "task" | "canvas_workflow" | string;
  child_id: string;
  correlation_id: string;
  task_type: string;
  status: string;
  progress: number;
  summary: string;
  error: string;
  created_at: string;
  updated_at: string;
  /** 任务表已回收该行、且超出宽限期时由服务端标出；此时状态只代表「无法确认」。 */
  record_missing?: boolean;
}

export interface ProductionPipelineState {
  project?: string;
  global?: Record<string, boolean>;
  current_episode: number | null;
  episode_status: Record<string, boolean> | null;
  next_step: string;
  next_step_name?: string;
}

export type ProductionContractStageExecution =
  | "required"
  | "deferred"
  | "not_requested";

export type ProductionContractStageStatusValue =
  | "pending"
  | "running"
  | "completed"
  | "failed"
  | "blocked"
  | "paused"
  | "waiting_confirmation"
  | "deferred"
  | "not_requested"
  | string;

export interface ProductionContractStage {
  id: string;
  label?: string;
  runtime_step?: string;
  execution?: ProductionContractStageExecution;
  required?: boolean;
  produces?: string[];
  quality_gates?: string[];
}

export interface ProductionContractStageStatus {
  id: string;
  execution?: ProductionContractStageExecution;
  required?: boolean;
  status?: ProductionContractStageStatusValue;
  current?: boolean;
  progress?: number;
  quality_gate_statuses?: Record<string, string>;
}

export interface ProductionQualityGateReport {
  schema?: "quality_gate_report.v1" | string;
  status?: string;
  passed?: boolean | null;
  strict?: boolean;
  requested_gates?: string[];
  gate_statuses?: Record<string, string>;
  failed_gates?: string[];
  not_run_gates?: string[];
  blocking_gates?: string[];
  gate_name?: string;
}

export interface ProductionCostSummary {
  schema?: "production_cost_summary.v1" | string;
  receipt_count?: number;
  quantity?: number;
  duration_ms?: number;
  status_counts?: Record<string, number>;
  estimated_cost?: Record<string, number>;
  reserved_cost?: Record<string, number>;
  actual_cost?: Record<string, number>;
  wasted_cost?: Record<string, number>;
}

export interface ProductionFinalComposeReceipt {
  schema?: "production_final_compose_receipt.v1" | string;
  status?: string;
  exists?: boolean;
  previous_output_available?: boolean;
  episode?: number;
  filename?: string;
  size_bytes?: number;
  task_id?: string;
  updated_at?: string;
  artifact_url?: string;
}

export interface ProductionPipelineRuntime {
  stage_statuses?: ProductionContractStageStatus[];
  quality_gate_report?: ProductionQualityGateReport | null;
  cost_summary?: ProductionCostSummary | null;
  final_compose_receipt?: ProductionFinalComposeReceipt | null;
}

export interface ProductionPipelineContract {
  schema?: string;
  workflow_id?: string;
  delivery_level?: string;
  run_mode?: "draft" | "auto" | string;
  auto_generate_paid_media?: boolean;
  stages?: ProductionContractStage[];
  stage_statuses?: ProductionContractStageStatus[];
  active_stage_ids?: string[];
  deferred_stage_ids?: string[];
  required_outputs?: string[];
  quality_gates?: string[];
  policies?: Record<string, unknown>;
  contract_revision?: string;
  quality_gate_report?: ProductionQualityGateReport | null;
  cost_summary?: ProductionCostSummary | null;
  final_compose_receipt?: ProductionFinalComposeReceipt | null;
  runtime?: ProductionPipelineRuntime | null;
}

export interface ProductionControlNextAction {
  id: string;
  label: string;
  paid: boolean;
  requires_upload: boolean;
}

export interface ProductionControlSnapshot {
  pipeline: ProductionPipelineState;
  uploaded_filename: string;
  next_action: ProductionControlNextAction;
  latest_run: ProductionControlRun | null;
  history: ProductionControlRun[];
  child_executions: ProductionChildExecution[];
  production_contract?: ProductionPipelineContract | null;
  production_contract_runtime?: ProductionPipelineRuntime | null;
  delivery_level?: string;
  contract_revision?: string;
  quality_gate_report?: ProductionQualityGateReport | null;
  cost_summary?: ProductionCostSummary | null;
  final_compose_receipt?: ProductionFinalComposeReceipt | null;
  commands: Array<"start" | ProductionRunCommand>;
}

export interface ProductionUploadResult {
  filename: string;
  size: number;
  total_chars?: number;
  billable_chars?: number;
  count?: number;
}
