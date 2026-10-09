// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab

import type { WorkflowRun } from "@/types/workflow-runtime";
import type { ChatAttachment } from "@/types/chat-attachment";
export type { ChatAttachment } from "@/types/chat-attachment";

export type ClientFrame =
  | {
      type: "chat.message";
      scope?: ChatScope;
      text: string;
      turn_id?: string;
      model?: string;
      agent_engine?: AgentEngine;
      attachments?: ChatAttachment[];
      research_enabled?: boolean;
    }
  | { type: "scope.set"; scope: ChatScope; since_seq?: number }
  | { type: "chat.resume"; recovery_id: string; turn_id?: string };

export type ChatScope = {
  kind: "home" | "project";
  id?: string | null;
  canvas_id?: string | null;
  conversation_id?: string | null;
};

export type AgentRuntimeSnapshot = {
  session_id: string;
  run_id?: string | null;
  run_seq?: number;
  turn_id?: string | null;
  canvas_id: string;
  status: "idle" | "running" | "completed" | "failed" | "cancelled" | string;
  seq: number;
  event_id?: string;
  created_at?: number;
  started_at?: number | null;
  updated_at?: number;
  last_event?: string | null;
  active?: boolean;
  /** Present on event metadata frames; snapshots may omit it. */
  event_type?: string;
  goal?: string | null;
  plan?: Array<Record<string, unknown>>;
  current_step?: string | null;
  completed_steps?: string[];
  created_node_ids?: string[];
  task_handles?: Array<Record<string, unknown>>;
  preserved_assets?: Array<Record<string, unknown>>;
  failure_checkpoint?: Record<string, unknown> | null;
  steering_queue?: Array<{ text?: string; turn_id?: string | null; created_at?: number }>;
  verification?: Record<string, unknown>;
};

export type AgentEngine = "village";

export type AgentRuntimeEvent = {
  session_id: string;
  /** Stable on new runtimes; omitted only by legacy persisted event tails. */
  run_id?: string;
  turn_id: string;
  event_id: string;
  seq: number;
  event_type: string;
  status: AgentRuntimeSnapshot["status"];
  created_at: number;
  canvas_id?: string;
};

export type CanvasAgentTelemetry = {
  turnId: string | null;
  patchCount: number;
  commandCount: number;
  receiptCount: number;
  appliedCount: number;
  skippedCount: number;
  createdNodeCount: number;
  backgroundTaskCount?: number;
  failedCount: number;
  revision: number | null;
  lastCommandId: string | null;
};

export type RelayInstanceInfo = {
  instanceId: string;
  instanceName: string;
  ip?: string;
  connectedAt?: number;
  busy?: boolean;
};

export type ModelEntry = {
  id: string;
  label: string;
  /** True when the entry is retained only to explain a removed selection. */
  stale?: boolean;
  description?: string;
  tier?: "standard" | "pro" | "custom" | string;
  reasoning?: boolean;
  default?: boolean;
  contextLength?: number;
  maxOutputTokens?: number;
  contextSource?: string;
  enabled?: boolean;
  disabled?: boolean;
  disabledReason?: string | null;
};

export type AgentEngineEntry = {
  id: AgentEngine;
  label: string;
  description?: string;
  available: boolean;
  reason?: string;
  automatic?: boolean;
};

export type CanvasPatchFrame = {
  type: "canvas.patch";
  run_id?: string;
  project_id: string;
  canvas_id: string;
  revision: number;
  command_id?: string;
  turn_id?: string;
  schema?: string;
  commands?: Array<Record<string, unknown>>;
  server_applied?: boolean;
  snapshot_required?: boolean;
  ui_reconcile_required?: boolean;
  structure_status?: string | null;
};

export type AgentWorkflowStep = {
  id: string;
  label: string;
  status: "done" | "running" | "pending" | "failed" | "blocked";
};

export type AgentWorkflowRunContinuation = {
  run_id?: string | null;
  run_status?: string | null;
  receipt_pending: boolean;
};

export type AgentWorkflowState = {
  schema: "village_canvas.workflow.v1" | string;
  status: string;
  active_tool?: string | null;
  steps: AgentWorkflowStep[];
  budget?: Record<string, number>;
  awaiting_confirmation?: boolean;
  failed_tool?: string;
  workflow_run_continuation?: AgentWorkflowRunContinuation;
};

export type ChatUiEvent = {
  id?: number | string;
  type: string;
  turn_id?: string;
  created_at?: string;
  workflow?: AgentWorkflowState;
  [key: string]: unknown;
};

export type ChatProgressState = {
  turnId?: string;
  stage: string;
  message: string;
  toolName?: string | null;
  elapsedSeconds?: number | null;
  lastProgressAgeSeconds?: number | null;
  lastEvent?: string | null;
  workerAlive?: boolean | null;
  heartbeat?: boolean;
  workflow?: AgentWorkflowState | null;
};

export type ChatRecoveryPacket = {
  schema: "village_canvas.chat_recovery.v1" | string;
  recovery_id: string;
  thread_id?: string | null;
  session_id?: string | null;
  agent_session_id?: string | null;
  turn_id?: string | null;
  backend_turn_id?: string | null;
  canvas?: {
    project_id?: string | null;
    canvas_id?: string | null;
    revision?: number | null;
  };
  pending_tool?: string | null;
  last_event?: {
    type?: string | null;
    stage?: string | null;
    age_seconds?: number | null;
  };
  retry_reason?: string;
  retryable?: boolean;
  recovery_attempt?: number;
  auto_retry_allowed?: boolean;
  expires_in_seconds?: number;
  checkpoint_turn_id?: string | null;
};

export type ChatRecoveryState = {
  message: string;
  packet: ChatRecoveryPacket;
  autoRetrying: boolean;
};

export type SessionControlCommand =
  | "agents"
  | "compact"
  | "fast"
  | "kill"
  | "model"
  | "redirect"
  | "steer"
  | "think"
  | "usage"
  | "verbose";

export type ServerFrame = (
  | {
      type: "scope.changed";
      scope: ChatScope;
      history: unknown[];
      busy?: boolean;
      agent_runtime?: AgentRuntimeSnapshot | null;
      agent_runtime_events?: AgentRuntimeEvent[];
      agent_events?: unknown[];
      approvals?: ApprovalRequest[];
      recoveries?: Array<{
        message?: string;
        recovery?: ChatRecoveryPacket;
      }>;
    }
  | {
      type: "chat.busy";
      scope?: ChatScope;
      turn_id?: string;
      message?: string;
    }
  | {
      type: "chat.ping";
      scope?: ChatScope;
      turn_id?: string;
      stage?: string;
      tool_name?: string | null;
      last_event?: string | null;
      last_progress_age_seconds?: number | null;
      worker_alive?: boolean | null;
      workflow?: AgentWorkflowState;
    }
  | {
      type: "chat.progress";
      scope?: ChatScope;
      turn_id?: string;
      stage?: string;
      message?: string;
      tool_name?: string | null;
      elapsed_seconds?: number | null;
      last_progress_age_seconds?: number | null;
      last_event?: string | null;
      worker_alive?: boolean | null;
      heartbeat?: boolean;
      workflow?: AgentWorkflowState;
    }
  | {
      type: "thread.started";
      scope?: ChatScope;
      thread_id?: string | null;
      turn_id?: string;
    }
  | {
      type: "run.started";
      run_id?: string;
      turn_id?: string;
      scope?: ChatScope;
    }
  | {
      type: "assistant.delta";
      text?: string;
      turn_id?: string;
      accumulated?: boolean;
    }
  | {
      type: "assistant.message";
      message?: unknown;
      turn_id?: string;
    }
  | {
      type: "tool.result";
      turn_id?: string;
      call_id?: string;
      name?: string;
      success?: boolean;
      result?: unknown;
      error?: unknown;
    }
  | {
      type: "tool.call";
      turn_id?: string;
      call_id?: string;
      name?: string;
      input?: unknown;
      raw?: unknown;
    }
  | {
      type: "fe_tool.call";
      schema: "village_fe_tool_bridge.v1";
      call_id: string;
      turn_id?: string | null;
      project_id: string;
      canvas_id: string;
      name: string;
      input: Record<string, unknown>;
      timeout_ms?: number;
      agent_event?: unknown;
    }
  | CanvasPatchFrame
  | {
      type: "workflow.run";
      turn_id?: string;
      scope?: ChatScope;
      run?: WorkflowRun;
    }
  | {
      type: "approval.requested";
      approval?: ApprovalRequest;
    }
  | {
      type: "approval.resolved";
      approval_id?: string;
      decision?: string;
      status?: string;
      projectId?: string;
      canvasId?: string;
    }
  | {
      type: "task.started";
      turn_id?: string;
      scope?: ChatScope;
      project_id?: string;
      canvas_id?: string;
      command_id?: string;
      node_id: string;
      task_key: string;
      task_type: string;
      job_id: string;
      status: "running";
      background: true;
    }
  | {
      type: "chat.recoverable";
      turn_id?: string;
      scope?: ChatScope;
      message?: string;
      recovery?: ChatRecoveryPacket;
    }
  | {
      type: "chat.done";
      turn_id?: string;
      scope?: ChatScope;
      failed?: boolean;
      cancelled?: boolean;
    }
  | { type: "project.created"; project: string }
  | { type: "error"; message: string; turn_id?: string }
  | { type: string; [key: string]: unknown }
) & {
  agent_runtime?: AgentRuntimeSnapshot | null;
  agent_event?: unknown;
  run_id?: string;
};

export type ChatRole = "user" | "assistant" | "system" | "tool";

export type ChatMessage = {
  id: string;
  role: ChatRole;
  text: string;
  turnId?: string;
  displayName?: string;
  attachments?: ChatAttachment[];
  timestamp: number;
  raw?: unknown;
  uiEvents?: ChatUiEvent[];
};

export type ApprovalRequest = {
  id: string;
  kind: "exec" | "plugin";
  title: string;
  command?: string;
  description?: string;
  cwd?: string | null;
  host?: string | null;
  security?: string | null;
  agentId?: string | null;
  sessionKey?: string | null;
  projectId?: string;
  canvasId?: string;
  action?: string;
  expiresAtMs?: number;
};

export type SuperChatSettings = {
  showToolEvents: boolean;
  showStructuredSourceWhileStreaming: boolean;
  uploadTarget: "relay" | "local";
};
