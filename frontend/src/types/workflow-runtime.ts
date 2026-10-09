export type WorkflowRunStatus = "running" | "paused" | "failed" | "completed" | "cancelled";
export type WorkflowStepStatus = "pending" | "running" | "failed" | "completed" | "skipped";
export type WorkflowItemStatus = "pending" | "running" | "failed" | "completed" | "cancelled" | "dismissed";
export type WorkflowExecutionMode = "atomic" | "itemized" | "best_effort";
export type WorkflowRetryScope = "whole_step" | "failed_items_only";
export type WorkflowRuntimePhase =
  | "queued"
  | "planning"
  | "observing"
  | "acting"
  | "waiting_executor"
  | "verifying"
  | "waiting_user"
  | "recoverable_error"
  | "terminal";
export type WorkflowReleaseStatus =
  | "not_applicable"
  | "unverified"
  | "blocked"
  | "ready";

export interface WorkflowReleaseReadiness {
  schema: "release_readiness_contract.v1";
  status: WorkflowReleaseStatus;
  reason_code: string;
  can_publish: boolean;
  required: boolean;
  failed_checks: string[];
  not_run_checks: string[];
  missing_checks: string[];
}

export interface WorkflowStepState {
  id: string;
  label: string;
  type: string;
  handler: string;
  depends_on: string[];
  requires?: string[];
  produces?: string[];
  writes_canvas?: boolean;
  checkpoint?: boolean;
  max_attempts?: number;
  retry_policy?: "never" | "manual" | "automatic";
  execution_mode?: WorkflowExecutionMode;
  failure_policy?: "stop_run" | "continue_step" | "continue_run" | "wait_for_user";
  retry_scope?: WorkflowRetryScope;
  status: WorkflowStepStatus;
  attempt: number;
  progress?: number;
  progress_message?: string;
  item_summary?: WorkflowItemSummary;
  item_retry_seq?: number;
  retry_scope_used?: WorkflowRetryScope;
  retry_item_ids?: string[];
  error: string;
  started_at: string;
  completed_at: string;
}

export interface WorkflowItemState {
  id: string;
  label?: string;
  node_id?: string;
  status: WorkflowItemStatus;
  attempt?: number;
  progress?: number;
  error?: string;
  updated_at?: string;
  dismissed_at?: string;
  dismissed_reason?: string;
  output?: unknown;
  job?: unknown;
}

export interface WorkflowItemSummary {
  total: number;
  completed: number;
  failed: number;
  running: number;
  pending: number;
  cancelled: number;
  dismissed?: number;
}

export interface WorkflowStepDefinition {
  id: string;
  label: string;
  type: string;
  handler: string;
  depends_on: string[];
  requires: string[];
  produces: string[];
  writes_canvas: boolean;
  checkpoint: boolean;
  parallel_group: string;
  max_attempts: number;
  retry_policy: "never" | "manual" | "automatic";
  execution_mode?: WorkflowExecutionMode;
  failure_policy?: "stop_run" | "continue_step" | "continue_run" | "wait_for_user";
  retry_scope?: WorkflowRetryScope;
}

export interface WorkflowDefinition {
  schema_version: "canvas_workflow_definition.v2" | string;
  id: string;
  version: number;
  title: string;
  description: string;
  starter_workflow_id: string;
  input_schema: Record<string, unknown>;
  steps: WorkflowStepDefinition[];
  outputs: string[];
}

export interface WorkflowRun {
  id: string;
  workflow_id: string;
  workflow_version: number;
  project_id: string;
  canvas_id: string;
  run_mode: "draft" | "auto";
  status: WorkflowRunStatus;
  contract_version?: 1 | 2;
  goal?: string;
  success_criteria?: string[];
  runtime_phase?: WorkflowRuntimePhase;
  checkpoint?: Record<string, unknown>;
  last_verified_canvas_revision?: number | null;
  next_action?: string;
  error_code?: string;
  terminal_reason?: string;
  source_turn_id?: string;
  project_context?: {
    schema?: string;
    project_id: string;
    canvas_id: string;
    canvas_revision?: number | null;
    observed_canvas_revision?: number | null;
    workflow_run_id?: string;
    source_turn_id?: string;
    model_plan_revision?: string;
    selected_node_ids?: string[];
    pinned_node_ids?: string[];
  };
  model_plan_snapshot?: {
    schema?: string;
    model_plan_revision?: string;
    bindings?: Record<string, Record<string, unknown>>;
    missing_roles?: string[];
    fallback_policy?: "explicit-only";
  };
  model_plan_revision?: string;
  completed_at?: string;
  current_frontier: string[];
  step_states: Record<string, WorkflowStepState>;
  inputs: Record<string, unknown>;
  artifacts: Record<string, unknown>;
  release_readiness?: WorkflowReleaseReadiness;
  error: string;
  revision: number;
  event_seq: number;
  idempotency_key: string;
  created_at: string;
  updated_at: string;
  reused?: boolean;
  event_applied?: boolean;
  command_applied?: boolean;
}

export interface WorkflowRunCreate {
  workflow_id: string;
  canvas_id: string;
  run_mode: "draft" | "auto";
  inputs: Record<string, unknown>;
  idempotency_key: string;
  contract_version?: 1 | 2;
  goal?: string;
  success_criteria?: string[];
  source_turn_id?: string;
  canvas_revision?: number;
  selected_node_ids?: string[];
  pinned_node_ids?: string[];
  model_bindings?: Record<string, string>;
}

export interface WorkflowRunCommand {
  command: "pause" | "resume" | "cancel" | "retry" | "steer" | "dismiss_failed_items";
  step_id?: string;
  direction?: string;
  retry_scope?: WorkflowRetryScope;
  item_ids?: string[];
  idempotency_key: string;
  expected_revision?: number;
}

export interface WorkflowRunEventCreate {
  event_id: string;
  type:
    | "run_started"
    | "step_started"
    | "step_output_ready"
    | "step_progress"
    | "step_items_updated"
    | "step_completed"
    | "step_failed"
    | "canvas_applied"
    | "receipt_recorded"
    | "verification_passed"
    | "verification_failed"
    | "steering_added"
    | "run_paused"
    | "run_resumed"
    | "run_cancelled"
    | "step_retried";
  step_id?: string;
  success?: boolean;
  payload?: Record<string, unknown>;
  error?: string;
  expected_revision?: number;
}

export interface WorkflowRunEvent {
  run_id: string;
  event_id: string;
  seq: number;
  type: string;
  step_id: string;
  payload: Record<string, unknown>;
  error: string;
  source: string;
  created_at: string;
}

export interface WorkflowRunEventPage {
  items: WorkflowRunEvent[];
  after_seq: number;
  next_seq: number;
  latest_seq: number;
  has_more: boolean;
}

export interface CanvasWorkflowRuntimeContext {
  workflow_run_id: string;
  workflow_id: string;
  workflow_version: number;
  status: WorkflowRunStatus;
  current_frontier: string[];
  current_step: string;
  starter_workflow_id: string;
  canvas_structure_command_id: string;
  structure_already_applied: boolean;
  canvas_created_node_ids: string[];
  revision: number;
  runtime_phase?: WorkflowRuntimePhase;
  next_action?: string;
  last_verified_canvas_revision?: number | null;
}

export interface WorkflowComposeAuthorization {
  schema: "workflow_compose_authorization.v1";
  id: string;
  project_id: string;
  canvas_id: string;
  run_id: string;
  step_id: "final_film";
  source_result_signature: string;
  created_at_ms: number;
  expires_at_ms: number;
  consumed_at_ms: number;
}

export interface WorkflowComposeAuthorizationCreate {
  canvas_id: string;
  step_id: "final_film";
  ttl_seconds?: number;
}

export interface WorkflowCanvasAssetBindingCommandRequest {
  canvas_id: string;
  step_id: string;
  command_id: string;
  source_turn_id?: string;
  expected_run_revision?: number;
}

export interface WorkflowCanvasAssetBindingRepairResult {
  schema: "workflow_canvas_asset_binding_repair.v1";
  status: "repaired" | "idempotent_replay";
  run_id: string;
  run_revision: number;
  step_id: string;
  kept_node_ids?: string[];
  detached_node_ids?: string[];
  media_replay_started: false;
  next_action: "revalidate_readiness";
  canvas_receipt: Record<string, unknown>;
}

export interface WorkflowCanvasAssetBindingReadinessResult {
  schema: "workflow_canvas_asset_binding_readiness.v1";
  ready: boolean;
  reason_code: string;
  run_id: string;
  step_id: string;
  run_revision: number;
  run_revision_before?: number;
  canvas_revision: number | null;
  resolved_node_ids: string[];
  issues: Array<Record<string, unknown>>;
  fingerprint: string;
  status: "authorization_required" | "idempotent_replay" | "not_ready";
  recovery?: Record<string, unknown>;
  authorization_request?: Record<string, unknown> | null;
  media_submission_started: false;
}
