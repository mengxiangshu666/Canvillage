// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
export type TaskStatus =
  | "submitting"
  | "queued"
  | "pending"
  | "starting"
  | "running"
  | "waiting"
  | "completed"
  | "failed"
  | "cancelled";

export interface TaskState {
  task_key: string;
  task_id: string;
  task_type: string;
  username: string;
  project: string;
  project_id?: string;
  episode: number;
  beat_num: number | null;
  scope: string | null;
  status: TaskStatus;
  progress: number;
  current_task: string;
  result: unknown | null;
  metadata?: Record<string, unknown> | null;
  error: string | null;
  error_code?: string | null;
  error_diagnostic?: Record<string, unknown> | null;
  logs: string[];
  task_type_label?: string;
  display_name?: string;
  created_at: string;
  updated_at: string;
  completed_at: string;
  expires_at?: string | null;
  task_acceptance_receipt?: TaskAcceptanceReceipt | null;
  production_cost_receipt?: ProductionCostReceipt | null;
  production_cost_summary?: ProductionCostSummary | null;
}

export interface TaskAcceptanceReceipt {
  schema: "task_acceptance_receipt.v1" | string;
  task_id: string;
  task_key: string;
  task_type: string;
  project_id: string;
  status: string;
  accepted_at: string;
  run_id?: string;
  command_id?: string;
  trace_id?: string;
  source_turn_id?: string;
}

export interface ProductionCostReceipt {
  schema: "production_cost_receipt.v1";
  task_id?: string;
  run_id?: string;
  command_id?: string;
  model_id?: string;
  provider?: string;
  provider_task_id?: string;
  media_kind?: string;
  quantity?: number;
  estimated_cost?: Record<string, number>;
  reserved_cost?: Record<string, number>;
  actual_cost?: Record<string, number>;
  wasted_cost?: Record<string, number>;
  result_status?: string;
  duration_ms?: number;
}

export interface ProductionCostSummary {
  schema: "production_cost_summary.v1";
  receipt_count: number;
  quantity: number;
  duration_ms: number;
  status_counts: Record<string, number>;
  estimated_cost: Record<string, number>;
  reserved_cost: Record<string, number>;
  actual_cost: Record<string, number>;
  wasted_cost: Record<string, number>;
}

export type StreamHealth = "connecting" | "connected" | "reconnecting" | "polling" | "failed";

export type TaskEvent =
  | { type: "task_updated"; task: TaskState; previous: TaskState | null }
  | { type: "task_complete"; task: TaskState; previous: TaskState | null }
  | { type: "task_failed"; task: TaskState; previous: TaskState | null }
  | { type: "task_removed"; taskKey: string };

export type TaskEventType = TaskEvent["type"];
export type TaskEventListener = (e: TaskEvent) => void;
