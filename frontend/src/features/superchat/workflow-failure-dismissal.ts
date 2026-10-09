import type { WorkflowRun, WorkflowStepState } from "@/types/workflow-runtime";

export const DISMISSED_WORKFLOW_FAILURES_STORAGE_KEY =
  "st.freezone.dismissedWorkflowFailures.v1";

const MAX_DISMISSED_FAILURES = 100;
const CANVAS_COMMAND_RECOVERY_SCHEMA = "canvas_command_recovery.v1";
const WORKFLOW_STEP_RECOVERY_SCHEMA = "workflow_step_recovery.v1";
const COMPOSE_AUTHORIZATION_REQUEST_SCHEMA = "workflow_compose_authorization_request.v1";
const SHA256_PATTERN = /^[0-9a-f]{64}$/;
type FailedItemFingerprint = [id: string, error: string, attempt: number | null];
type FailedStepFingerprint = [id: string, status: string, error: string, items: FailedItemFingerprint[]];

export interface WorkflowComposeAuthorizationRequest {
  schema: typeof COMPOSE_AUTHORIZATION_REQUEST_SCHEMA;
  run_id: string;
  step_id: "final_film";
  source_result_signature: string;
  requires_user_action: true;
}

export interface WorkflowMediaAuthorizationRequest {
  step_id: "storyboard_images" | "shot_videos";
  error_code:
    | "workflow_storyboard_paid_media_not_authorized"
    | "workflow_storyboard_image_failed"
    | "workflow_shot_video_paid_media_not_authorized"
    | "workflow_shot_video_failed";
  media_kind: "image" | "video";
  recovery_action:
    | "request_media_authorization"
    | "retry_failed_items";
  retry_scope: "whole_step" | "failed_items_only";
  item_ids: string[];
}

export interface WorkflowCanvasRecovery {
  schema: typeof CANVAS_COMMAND_RECOVERY_SCHEMA;
  action: string;
  title: string;
  instruction: string;
  next_action: string;
  auto_retry_allowed: boolean;
  requires_paid_media: boolean;
  error_code: string;
  step_id: string;
  reason_code?: string;
  stale_reason?: string;
  script_node_id?: string;
  target_node_id?: string;
  shot_id?: string;
  asset_id?: string;
  authorization_request?: WorkflowComposeAuthorizationRequest;
}

export interface WorkflowStepRecovery {
  schema: typeof WORKFLOW_STEP_RECOVERY_SCHEMA;
  workflow_run_id: string;
  step_id: string;
  error_code: string;
  action: string;
  next_action: string;
  rerun_scope: string;
  item_ids: string[];
  job_ids: string[];
  target_node_ids?: string[];
  asset_ids?: string[];
  requires_paid_media: boolean;
  auto_retry_allowed: boolean;
  instruction: string;
  authorization_request?: WorkflowComposeAuthorizationRequest;
}

export type WorkflowRecovery = WorkflowCanvasRecovery | WorkflowStepRecovery;

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}

function boundedStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  const result: string[] = [];
  for (const item of value.slice(0, 32)) {
    if (typeof item !== "string") continue;
    const text = item.trim().slice(0, 240);
    if (text && !result.includes(text)) result.push(text);
  }
  return result;
}

function boundedWorkflowItemIds(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  const result: string[] = [];
  for (const item of value.slice(0, 500)) {
    if (typeof item !== "string") continue;
    const text = item.trim().slice(0, 240);
    if (text && !result.includes(text)) result.push(text);
  }
  return result;
}

function workflowCanvasRecoveryFromValue(
  value: unknown,
): WorkflowCanvasRecovery | null {
  if (!isRecord(value)) return null;
  if (value.schema !== CANVAS_COMMAND_RECOVERY_SCHEMA) return null;
  if (
    typeof value.action !== "string"
    || typeof value.title !== "string"
    || typeof value.instruction !== "string"
    || typeof value.next_action !== "string"
    || typeof value.error_code !== "string"
    || typeof value.step_id !== "string"
    || typeof value.auto_retry_allowed !== "boolean"
    || typeof value.requires_paid_media !== "boolean"
  ) {
    return null;
  }
  return value as unknown as WorkflowCanvasRecovery;
}

function workflowStepRecoveryFromValue(value: unknown): WorkflowStepRecovery | null {
  if (!isRecord(value)) return null;
  if (value.schema !== WORKFLOW_STEP_RECOVERY_SCHEMA) return null;
  if (
    typeof value.workflow_run_id !== "string"
    || !value.workflow_run_id.trim()
    || typeof value.action !== "string"
    || typeof value.next_action !== "string"
    || typeof value.error_code !== "string"
    || typeof value.step_id !== "string"
    || typeof value.instruction !== "string"
    || typeof value.auto_retry_allowed !== "boolean"
    || typeof value.requires_paid_media !== "boolean"
  ) {
    return null;
  }
  const targetNodeIds = boundedStringArray(value.target_node_ids);
  const assetIds = boundedStringArray(value.asset_ids);
  return {
    ...value,
    ...(targetNodeIds.length > 0 ? { target_node_ids: targetNodeIds } : {}),
    ...(assetIds.length > 0 ? { asset_ids: assetIds } : {}),
  } as unknown as WorkflowStepRecovery;
}

function workflowRecoveriesFromRun(run: WorkflowRun | null | undefined): WorkflowRecovery[] {
  if (!run) return [];
  const recoveries: WorkflowRecovery[] = [];
  for (const artifact of Object.values(run.artifacts ?? {})) {
    if (!isRecord(artifact)) continue;
    const receipt = artifact.canvas_receipt;
    for (const value of [
      artifact.recovery,
      isRecord(receipt) ? receipt.recovery : null,
    ]) {
      const canvasRecovery = workflowCanvasRecoveryFromValue(value);
      if (canvasRecovery) {
        recoveries.push(canvasRecovery);
        continue;
      }
      const stepRecovery = workflowStepRecoveryFromValue(value);
      if (stepRecovery?.workflow_run_id === run.id) {
        recoveries.push(stepRecovery);
      }
    }
  }
  return recoveries;
}

export function workflowCanvasRecoveryFromRun(
  run: WorkflowRun | null | undefined,
): WorkflowCanvasRecovery | null {
  return workflowRecoveriesFromRun(run)
    .find((recovery): recovery is WorkflowCanvasRecovery => (
      recovery.schema === CANVAS_COMMAND_RECOVERY_SCHEMA
    )) ?? null;
}

export function workflowRecoveryFromRun(
  run: WorkflowRun | null | undefined,
): WorkflowRecovery | null {
  return workflowRecoveriesFromRun(run)[0] ?? null;
}

export function workflowComposeAuthorizationRequestFromRun(
  run: WorkflowRun | null | undefined,
): WorkflowComposeAuthorizationRequest | null {
  const recovery = workflowRecoveriesFromRun(run).find((candidate) => (
    candidate.action === "request_compose_authorization"
    && candidate.step_id === "final_film"
  ));
  if (!recovery) return null;
  const request = recovery.authorization_request;
  if (
    !isRecord(request)
    || request.schema !== COMPOSE_AUTHORIZATION_REQUEST_SCHEMA
    || request.run_id !== run?.id
    || request.step_id !== "final_film"
    || request.requires_user_action !== true
    || !SHA256_PATTERN.test(String(request.source_result_signature || ""))
  ) {
    return null;
  }
  return request as unknown as WorkflowComposeAuthorizationRequest;
}

export function workflowMediaAuthorizationRequestFromRun(
  run: WorkflowRun | null | undefined,
): WorkflowMediaAuthorizationRequest | null {
  if (!run) return null;
  const allowed = {
    storyboard_images: [
      {
        recovery_action: "request_media_authorization",
        error_code: "workflow_storyboard_paid_media_not_authorized",
        rerun_scope: "current_step",
        retry_scope: "whole_step",
        media_kind: "image",
      },
      {
        recovery_action: "retry_failed_items",
        error_code: "workflow_storyboard_image_failed",
        rerun_scope: "failed_items_only",
        retry_scope: "failed_items_only",
        media_kind: "image",
      },
    ],
    shot_videos: [
      {
        recovery_action: "request_media_authorization",
        error_code: "workflow_shot_video_paid_media_not_authorized",
        rerun_scope: "current_step",
        retry_scope: "whole_step",
        media_kind: "video",
      },
      {
        recovery_action: "retry_failed_items",
        error_code: "workflow_shot_video_failed",
        rerun_scope: "failed_items_only",
        retry_scope: "failed_items_only",
        media_kind: "video",
      },
    ],
  } as const;
  const recovery = workflowRecoveriesFromRun(run).find((candidate) => {
    if (
      candidate.schema !== WORKFLOW_STEP_RECOVERY_SCHEMA
      || candidate.workflow_run_id !== run.id
      || candidate.auto_retry_allowed !== false
      || candidate.requires_paid_media !== true
    ) {
      return false;
    }
    const choices = allowed[candidate.step_id as keyof typeof allowed] ?? [];
    return choices.some((choice) => (
      candidate.action === choice.recovery_action
      && candidate.error_code === choice.error_code
      && candidate.rerun_scope === choice.rerun_scope
      && candidate.next_action
        === `recover:${choice.recovery_action}:${candidate.step_id}`
    ));
  });
  if (!recovery || recovery.schema !== WORKFLOW_STEP_RECOVERY_SCHEMA) {
    return null;
  }
  const choices = allowed[recovery.step_id as keyof typeof allowed] ?? [];
  const match = choices.find((choice) => (
    recovery.action === choice.recovery_action
    && recovery.error_code === choice.error_code
    && recovery.rerun_scope === choice.rerun_scope
  ));
  if (!match) return null;
  const itemIds = match.retry_scope === "failed_items_only"
    ? boundedWorkflowItemIds(recovery.item_ids)
    : [];
  if (match.retry_scope === "failed_items_only" && itemIds.length === 0) {
    return null;
  }
  return {
    step_id: recovery.step_id as WorkflowMediaAuthorizationRequest["step_id"],
    error_code: match.error_code,
    media_kind: match.media_kind,
    recovery_action: match.recovery_action,
    retry_scope: match.retry_scope,
    item_ids: itemIds,
  };
}

export function workflowStepHasBlockingCanvasRecovery(
  run: WorkflowRun | null | undefined,
  stepId: string,
): boolean {
  return workflowRecoveriesFromRun(run).some((recovery) => (
    recovery.step_id === stepId
    && recovery.auto_retry_allowed === false
  ));
}

function compactFailureText(value: unknown): string {
  if (typeof value !== "string") return "";
  return value.trim().replace(/\s+/g, " ").slice(0, 240);
}

function failureFingerprint(run: WorkflowRun): string {
  const failedSteps: FailedStepFingerprint[] = Object.values(run.step_states ?? {})
    .flatMap((step) => {
      const artifact = run.artifacts?.[step.id];
      const itemStates = artifact && typeof artifact === "object" && !Array.isArray(artifact)
        ? (artifact as { item_states?: unknown }).item_states
        : null;
      const failedItems: FailedItemFingerprint[] = itemStates && typeof itemStates === "object" && !Array.isArray(itemStates)
        ? Object.entries(itemStates)
          .filter(([, item]) => item && typeof item === "object" && (item as { status?: unknown }).status === "failed")
          .map(([id, item]) => {
            const state = item as { error?: unknown; attempt?: unknown };
            return [
              id,
              compactFailureText(state.error),
              typeof state.attempt === "number" ? state.attempt : null,
            ] as FailedItemFingerprint;
          })
          .sort((left, right) => left[0].localeCompare(right[0]))
        : [];
      if (step.status !== "failed" && failedItems.length === 0) return [];
      return [[
        step.id,
        step.status,
        compactFailureText(step.error),
        failedItems,
      ] as FailedStepFingerprint];
    })
    .sort((left, right) => left[0].localeCompare(right[0]));

  return JSON.stringify({
    errorCode: compactFailureText(run.error_code),
    error: compactFailureText(run.error),
    terminalReason: compactFailureText(run.terminal_reason),
    steps: failedSteps,
  });
}

export function workflowFailureDisplayKey(
  run: WorkflowRun | null | undefined,
): string | null {
  if (!run || !["failed", "paused"].includes(run.status)) return null;
  return [
    run.project_id,
    run.canvas_id,
    run.id,
    run.status,
    failureFingerprint(run),
  ].join(":");
}

export function workflowStepCanRetryWholeStep(
  step: WorkflowStepState | null | undefined,
): boolean {
  if (!step || step.status !== "failed") return false;
  if ((step.retry_policy ?? "manual") === "never") return false;
  const maxAttempts = step.max_attempts ?? 0;
  if (maxAttempts > 0 && (step.attempt ?? 0) >= maxAttempts) return false;
  return true;
}

export function workflowRunHasRetryableFailures(
  run: WorkflowRun | null | undefined,
): boolean {
  if (!run || !["failed", "paused"].includes(run.status)) return false;
  return Object.values(run.step_states).some((step) => {
    if (workflowStepHasBlockingCanvasRecovery(run, step.id)) return false;
    if (workflowStepCanRetryWholeStep(step)) return true;
    if (step.execution_mode !== "itemized" && step.retry_scope !== "failed_items_only") {
      return false;
    }
    const artifact = run.artifacts[step.id];
    if (!artifact || typeof artifact !== "object" || Array.isArray(artifact)) return false;
    const itemStates = (artifact as { item_states?: unknown }).item_states;
    if (!itemStates || typeof itemStates !== "object" || Array.isArray(itemStates)) return false;
    return Object.values(itemStates).some(
      (item) => item && typeof item === "object" && (item as { status?: unknown }).status === "failed",
    );
  });
}

export function workflowRunHasActionableFailures(
  run: WorkflowRun | null | undefined,
): boolean {
  return workflowRunHasRetryableFailures(run)
    || Boolean(workflowRecoveryFromRun(run));
}

export function loadDismissedWorkflowFailureKeys(): Set<string> {
  if (typeof window === "undefined") return new Set();
  try {
    const value = JSON.parse(
      window.localStorage.getItem(DISMISSED_WORKFLOW_FAILURES_STORAGE_KEY) || "[]",
    );
    if (!Array.isArray(value)) return new Set();
    return new Set(
      value
        .filter((item): item is string => typeof item === "string" && Boolean(item.trim()))
        .slice(-MAX_DISMISSED_FAILURES),
    );
  } catch {
    return new Set();
  }
}

export function saveDismissedWorkflowFailureKey(key: string): Set<string> {
  const normalized = key.trim();
  const next = loadDismissedWorkflowFailureKeys();
  if (!normalized) return next;
  next.delete(normalized);
  next.add(normalized);
  const bounded = new Set([...next].slice(-MAX_DISMISSED_FAILURES));
  if (typeof window !== "undefined") {
    try {
      window.localStorage.setItem(
        DISMISSED_WORKFLOW_FAILURES_STORAGE_KEY,
        JSON.stringify([...bounded]),
      );
    } catch {
      // Storage can be unavailable in privacy mode; in-memory dismissal still works.
    }
  }
  return bounded;
}
