// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// Village Infinite Canvas project-scoped task endpoints — read task state, subscribe to SSE.
//
// We use native EventSource because Village Infinite Canvas auth is cookie-based and
// HttpOnly cookies are sent on the EventSource handshake automatically
// (no header needed). If the cookie is missing/expired, the stream returns
// a 401 immediately and we surface that to the caller.

import { apiCall } from "./client";
import { handleSessionExpired } from "@/lib/api";
import { SESSION_EXPIRED_EVENT } from "@/lib/session-expiry";
import { readUrl } from "@/lib/url-params";

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
  task_type: string;
  task_key: string;
  project_id?: string;
  username: string;
  project: string;
  episode: number;
  beat_num?: number | null;
  scope?: string | null;
  status: TaskStatus;
  progress?: number | null;
  current_task?: string | null;
  result?: Record<string, unknown> | null;
  error?: string | null;
  logs?: string[];
  created_at?: string;
  updated_at?: string;
  metadata?: Record<string, unknown> | null;
  error_code?: string | null;
  error_diagnostic?: Record<string, unknown> | null;
}

type TaskSettlement = Pick<
  TaskState,
  "task_key" | "status" | "error" | "error_diagnostic"
> & {
  result?: unknown;
};

export class TaskCompletionError extends Error {
  constructor(
    message: string,
    public readonly status: TaskStatus,
    public readonly taskKey: string,
    public readonly diagnostic?: Record<string, unknown> | null,
  ) {
    super(message);
    this.name = "TaskCompletionError";
  }
}

export class TaskMonitoringTimeoutError extends Error {
  public readonly recoverable = true;

  constructor(
    public readonly taskKey: string,
    public readonly projectId: string,
  ) {
    super("任务监控暂时超时，后台任务可能仍在运行；刷新页面后会继续恢复");
    this.name = "TaskMonitoringTimeoutError";
  }
}

export type TaskMonitorDeadlineDecision = "keep" | "extend" | "expire";

export function taskMonitorDeadlineDecision(
  task: Pick<TaskState, "status"> | undefined,
  now: number,
  expiresAt: number,
): TaskMonitorDeadlineDecision {
  if (now <= expiresAt) return "keep";
  if (
    task &&
    task.status !== "completed" &&
    task.status !== "failed" &&
    task.status !== "cancelled"
  ) {
    return "extend";
  }
  return "expire";
}

function resolveTaskProjectId(projectId?: string): string {
  const resolved = (projectId ?? readUrl().project ?? "").trim();
  if (!resolved) {
    throw new Error("project_id is required for task monitoring");
  }
  return resolved;
}

export async function listTasks(projectId?: string): Promise<TaskState[]> {
  const resolved = resolveTaskProjectId(projectId);
  return await apiCall<TaskState[]>(
    `projects/${encodeURIComponent(resolved)}/tasks`,
  );
}

export interface SseHandle {
  close(): void;
}

export interface TaskStreamHandler {
  onTask: (task: TaskState) => void;
  onError?: (err: Event) => void;
  onAuthRevoked?: () => void;
  projectId?: string;
  /**
   * Probe the browser session after repeated transport failures. `false` means
   * the credential is gone (terminal), `true` means it is alive, and `null`
   * means the probe itself was inconclusive (network/5xx) so reconnecting
   * continues. Without a probe a dead-but-silent session reconnects forever.
   */
  checkSession?: () => Promise<boolean | null>;
  /** Fired once when reconnect attempts are exhausted; the stream is closed. */
  onUnrecoverable?: () => void;
  /** For testing. */
  backoffMs?: number[];
  slowRetryMs?: number;
  maxConsecutiveFailures?: number;
}

const STREAM_BACKOFF_MS = [1000, 2000, 4000, 8000, 16000, 30000];
// After this many consecutive failures we leave the fast backoff, slow the
// cadence down and start probing the session. Mirrors the task-center
// stream-client FSM so both SSE clients degrade the same way.
const FAILED_CYCLES_BEFORE_POLLING = 3;
const STREAM_SLOW_RETRY_MS = 30_000;
// Hard ceiling: a permanently unreachable endpoint — or a session the probe can
// never confirm — stops the stream instead of retrying for the life of the tab.
// awaitTaskCompletion's HTTP poller still owns correctness once we give up.
const STREAM_MAX_CONSECUTIVE_FAILURES = 8;

/**
 * Open a project SSE stream that fans every `task_updated` event out to the
 * registered handler. Reconnects with exponential backoff on transient errors,
 * slows down and probes the session after repeated failures, and gives up
 * entirely once the failure budget is spent.
 */
export function openTaskStream(handler: TaskStreamHandler): SseHandle {
  const projectId = resolveTaskProjectId(handler.projectId);
  const backoffs = handler.backoffMs ?? STREAM_BACKOFF_MS;
  const slowRetryMs = handler.slowRetryMs ?? STREAM_SLOW_RETRY_MS;
  const maxConsecutiveFailures =
    handler.maxConsecutiveFailures ?? STREAM_MAX_CONSECUTIVE_FAILURES;
  let es: EventSource | null = null;
  let closed = false;
  let attempt = 0;
  let authRevokedNotified = false;
  let probeInFlight = false;
  let reconnectTimer: number | null = null;

  const close = () => {
    closed = true;
    if (reconnectTimer != null) window.clearTimeout(reconnectTimer);
    reconnectTimer = null;
    es?.close();
    es = null;
  };
  // Session loss and the probe can both conclude the same thing — notify the
  // caller exactly once so subscribers are not rejected twice.
  const notifyAuthRevoked = () => {
    if (authRevokedNotified) return;
    authRevokedNotified = true;
    close();
    handler.onAuthRevoked?.();
  };

  const scheduleReconnect = (delay: number) => {
    if (closed) return;
    reconnectTimer = window.setTimeout(connect, delay);
  };

  const handleSessionExpired = () => {
    window.removeEventListener(SESSION_EXPIRED_EVENT, handleSessionExpired);
    notifyAuthRevoked();
  };
  window.addEventListener(SESSION_EXPIRED_EVENT, handleSessionExpired);

  const connect = () => {
    if (closed) return;
    es = new EventSource(
      `/api/v1/projects/${encodeURIComponent(projectId)}/tasks/stream?snapshot=false`,
      { withCredentials: true },
    );

    es.addEventListener("task_updated", (event) => {
      // A delivered event proves the stream is healthy: reset the fast budget.
      attempt = 0;
      try {
        const data = JSON.parse((event as MessageEvent).data);
        handler.onTask(data as TaskState);
      } catch (err) {
        console.warn("[freezone] task_updated parse failed", err);
      }
    });
    es.addEventListener("auth_revoked", () => {
      notifyAuthRevoked();
    });
    es.onerror = (err) => {
      handler.onError?.(err);
      es?.close();
      es = null;
      if (closed) return;
      attempt += 1;

      if (attempt >= maxConsecutiveFailures) {
        close();
        handler.onUnrecoverable?.();
        return;
      }

      const slow = attempt >= FAILED_CYCLES_BEFORE_POLLING;
      if (slow && handler.checkSession && !probeInFlight) {
        // Slow path: ask the server whether the session still exists. If it is
        // gone, stop reconnecting instead of looping on a rejected credential.
        probeInFlight = true;
        void handler
          .checkSession()
          .then((active) => {
            if (closed) return;
            if (active === false) {
              notifyAuthRevoked();
              return;
            }
            scheduleReconnect(slowRetryMs);
          })
          .catch(() => scheduleReconnect(slowRetryMs))
          .finally(() => {
            probeInFlight = false;
          });
        return;
      }

      const delay = slow
        ? slowRetryMs
        : Math.min(
            30_000,
            backoffs[Math.min(attempt - 1, backoffs.length - 1)] ?? 1_000,
          );
      scheduleReconnect(delay);
    };
  };

  connect();

  return {
    close() {
      close();
      window.removeEventListener(SESSION_EXPIRED_EVENT, handleSessionExpired);
    },
  };
}

// ---------------------------------------------------------------------- //
// In-process job tracker: callers can `await` a freezone job by task_key
// and the underlying SSE stream resolves the promise on completion / failure.

interface PendingSubscriber {
  resolve: (task: TaskState) => void;
  reject: (err: Error) => void;
}

interface PendingTaskMonitor {
  subscribers: Set<PendingSubscriber>;
  projectId: string;
  expiresAt: number;
}

interface ProjectPoller {
  timer: number | null;
  inFlight: boolean;
}

const DEFAULT_POLL_INTERVAL_MS = 4000;
// Video providers can legitimately need the backend's full 60-minute recovery
// window. Keep the browser attached slightly longer so it never manufactures a
// local failure while the durable backend still owns a live provider task.
const DEFAULT_MAX_POLL_MS = 65 * 60 * 1000;
const pendingByTaskKey = new Map<string, PendingTaskMonitor>();
const sharedStreamsByProject = new Map<string, SseHandle>();
const pollersByProject = new Map<string, ProjectPoller>();

interface ProjectTaskSource {
  read: () => Promise<TaskSettlement[]>;
  polling: boolean;
  sessionError?: Error;
}

const taskSourcesByProject = new Map<string, ProjectTaskSource>();
const SHARED_STREAM_RECONCILE_MS = 15_000;
const SHARED_SOURCE_FALLBACK_MS = 5000;

/** Let the mounted task center own the project's stream and read fallback. */
export function registerProjectTaskSource(
  projectId: string,
  read: () => Promise<TaskSettlement[]>,
): { setPolling(polling: boolean): void; close(error?: Error): void } {
  const resolved = resolveTaskProjectId(projectId);
  const source: ProjectTaskSource = { read, polling: false };
  taskSourcesByProject.set(resolved, source);
  sharedStreamsByProject.get(resolved)?.close();
  sharedStreamsByProject.delete(resolved);
  resetProjectPoller(resolved);
  const sessionExpired = () => {
    if (taskSourcesByProject.get(resolved) !== source) return;
    source.polling = false;
    source.sessionError = new Error("auth revoked");
    rejectProjectPending(resolved, source.sessionError);
  };
  window.addEventListener(SESSION_EXPIRED_EVENT, sessionExpired);
  return {
    setPolling(polling) {
      if (taskSourcesByProject.get(resolved) !== source || source.sessionError || source.polling === polling) return;
      source.polling = polling;
      resetProjectPoller(resolved);
    },
    close(error) {
      window.removeEventListener(SESSION_EXPIRED_EVENT, sessionExpired);
      if (taskSourcesByProject.get(resolved) !== source) return;
      if (error) {
        source.polling = false;
        rejectProjectPending(resolved, error);
      }
      taskSourcesByProject.delete(resolved);
      resetProjectPoller(resolved);
      if (pendingCountForProject(resolved) > 0) ensureSharedStream(resolved);
    },
  };
}

function projectNeedsPolling(projectId: string): boolean {
  return pendingCountForProject(projectId) > 0 ||
    taskSourcesByProject.get(projectId)?.polling === true;
}

function resetProjectPoller(projectId: string): void {
  const poller = pollersByProject.get(projectId);
  if (poller?.timer != null) window.clearTimeout(poller.timer);
  // Health changes affect the next tick, never start a second read while the
  // existing request is pending. Its completion will schedule the new cadence.
  if (poller?.inFlight) return;
  pollersByProject.delete(projectId);
  if (projectNeedsPolling(projectId)) ensureProjectPoller(projectId);
}

export function isTaskMonitoring(taskKey: string): boolean {
  return pendingByTaskKey.has(taskKey);
}

export interface CancelProjectTaskInput {
  projectId: string;
  taskType: string;
  episode?: number;
  beatNum?: number;
  scope?: string;
}

/** Cancel one exact project task. Freezone scopes are normally the job id. */
export async function cancelProjectTask({
  projectId,
  taskType,
  episode = 0,
  beatNum,
  scope,
}: CancelProjectTaskInput): Promise<void> {
  const searchParams: Record<string, string> = {};
  if (beatNum !== undefined) searchParams.beat_num = String(beatNum);
  if (scope) searchParams.scope = scope;
  await apiCall<void>(
    `projects/${encodeURIComponent(projectId)}/tasks/${encodeURIComponent(taskType)}/${episode}`,
    {
      method: "DELETE",
      ...(Object.keys(searchParams).length ? { searchParams } : {}),
    },
  );
}

function closeAllTaskMonitoring(err?: Error): void {
  for (const [, stream] of sharedStreamsByProject) {
    stream.close();
  }
  sharedStreamsByProject.clear();

  for (const [, poller] of pollersByProject) {
    if (poller.timer != null) {
      window.clearTimeout(poller.timer);
    }
  }
  pollersByProject.clear();

  if (err) {
    for (const [, pending] of pendingByTaskKey) {
      pending.subscribers.forEach((subscriber) => subscriber.reject(err));
    }
    pendingByTaskKey.clear();
  }
}

function pendingCountForProject(projectId: string): number {
  let count = 0;
  for (const pending of pendingByTaskKey.values()) {
    if (pending.projectId === projectId) count += 1;
  }
  return count;
}

function maybeStopProjectMonitoring(projectId: string): void {
  if (projectNeedsPolling(projectId)) return;

  const poller = pollersByProject.get(projectId);
  if (poller) {
    if (poller.timer != null) {
      window.clearTimeout(poller.timer);
    }
    pollersByProject.delete(projectId);
  }

  // No job awaiting this project anymore — tear down the shared SSE stream too,
  // otherwise an idle connection (and its backoff reconnects) keeps hitting
  // /tasks/stream forever. It re-opens lazily on the next awaitTaskCompletion.
  const stream = sharedStreamsByProject.get(projectId);
  if (stream) {
    stream.close();
    sharedStreamsByProject.delete(projectId);
  }
}

export function settleTask(task: TaskSettlement): void {
  const pending = pendingByTaskKey.get(task.task_key);
  if (!pending) return;
  if (task.status === "completed") {
    pendingByTaskKey.delete(task.task_key);
    pending.subscribers.forEach((subscriber) =>
      subscriber.resolve(task as TaskState),
    );
    maybeStopProjectMonitoring(pending.projectId);
  } else if (task.status === "failed" || task.status === "cancelled") {
    pendingByTaskKey.delete(task.task_key);
    const error = new TaskCompletionError(
      task.error ?? `task ${task.status}`,
      task.status,
      task.task_key,
      task.error_diagnostic ?? null,
    );
    pending.subscribers.forEach((subscriber) => subscriber.reject(error));
    maybeStopProjectMonitoring(pending.projectId);
  }
}

/** Stop an in-memory awaiter immediately after the server accepts cancellation. */
export function cancelTaskMonitoring(taskKey: string): boolean {
  const pending = pendingByTaskKey.get(taskKey);
  if (!pending) return false;
  pendingByTaskKey.delete(taskKey);
  const error = new TaskCompletionError("任务已由用户终止", "cancelled", taskKey);
  pending.subscribers.forEach((subscriber) => subscriber.reject(error));
  maybeStopProjectMonitoring(pending.projectId);
  return true;
}

function rejectProjectPending(projectId: string, err: Error): void {
  for (const [taskKey, pending] of pendingByTaskKey) {
    if (pending.projectId !== projectId) continue;
    pendingByTaskKey.delete(taskKey);
    pending.subscribers.forEach((subscriber) => subscriber.reject(err));
  }
  maybeStopProjectMonitoring(projectId);
}

/**
 * Ask `/auth/me` whether the browser session still exists. EventSource cannot
 * observe HTTP status, so a cookie that silently went bad looks identical to a
 * network blip; this probe is what lets the stream stop instead of reconnecting
 * forever. `false` is terminal auth loss, `true` is a live session, and `null`
 * means the probe itself was inconclusive so reconnecting should continue.
 */
async function probeSessionActive(): Promise<boolean | null> {
  try {
    const response = await fetch("/api/v1/auth/me", {
      credentials: "include",
      headers: { Accept: "application/json" },
    });
    if (response.status === 401 || response.status === 403) {
      await handleSessionExpired();
      return false;
    }
    return response.ok ? true : null;
  } catch {
    return null;
  }
}

function ensureSharedStream(projectId?: string) {
  const resolved = resolveTaskProjectId(projectId);
  if (taskSourcesByProject.has(resolved)) return;
  if (sharedStreamsByProject.has(resolved)) return;
  let stream: SseHandle | null = null;
  stream = openTaskStream({
    projectId: resolved,
    onTask: (task) => {
      settleTask(task);
    },
    onAuthRevoked: () => {
      rejectProjectPending(resolved, new Error("auth revoked"));
    },
    checkSession: probeSessionActive,
    onUnrecoverable: () => {
      // Reconnect budget spent while the session still looked alive (endpoint
      // down, captive proxy, …). Drop the dead handle so a later
      // awaitTaskCompletion can open a fresh stream; the HTTP poller keeps
      // monitoring in the meantime.
      if (sharedStreamsByProject.get(resolved) === stream) {
        sharedStreamsByProject.delete(resolved);
      }
    },
  });
  sharedStreamsByProject.set(resolved, stream);
}

/**
 * Shared HTTP polling fallback for {@link awaitTaskCompletion}. SSE is the
 * primary channel, but the stream can drop events during reconnect windows,
 * idle disconnects, or proxy hiccups. Keep one poller per project so concurrent
 * jobs share a single `/projects/:project/tasks` request cadence.
 */
function ensureProjectPoller(projectId: string): void {
  if (pollersByProject.has(projectId)) return;

  const poller: ProjectPoller = { timer: null, inFlight: false };
  pollersByProject.set(projectId, poller);

  const schedule = () => {
    if (pollersByProject.get(projectId) !== poller) return;
    const source = taskSourcesByProject.get(projectId);
    const delay = source
      ? source.polling ? SHARED_SOURCE_FALLBACK_MS : SHARED_STREAM_RECONCILE_MS
      : DEFAULT_POLL_INTERVAL_MS;
    poller.timer = window.setTimeout(run, delay);
  };

  const run = async () => {
    poller.timer = null;
    if (!projectNeedsPolling(projectId)) {
      pollersByProject.delete(projectId);
      return;
    }
    if (poller.inFlight) {
      schedule();
      return;
    }

    poller.inFlight = true;
    try {
      const source = taskSourcesByProject.get(projectId);
      const tasks = source ? await source.read() : await listTasks(projectId);
      // An unmounted/switched source cannot settle a new owner's waiters.
      if (pollersByProject.get(projectId) === poller &&
          taskSourcesByProject.get(projectId) === source) {
        reconcileTaskSnapshot(projectId, tasks);
      }
    } catch {
      // transient list failure — try again next tick
    } finally {
      poller.inFlight = false;
    }

    if (pollersByProject.get(projectId) !== poller) return;
    if (!projectNeedsPolling(projectId)) {
      pollersByProject.delete(projectId);
      return;
    }
    schedule();
  };

  schedule();
}

/** Consume accepted server snapshots from the same source as the task center. */
export function reconcileTaskSnapshot(projectId: string, tasks: TaskSettlement[]): void {
  const tasksByKey = new Map(tasks.map((task) => [task.task_key, task]));
  const now = Date.now();
  for (const [taskKey, pending] of pendingByTaskKey) {
    if (pending.projectId !== projectId) continue;
    const found = tasksByKey.get(taskKey);
    if (found) settleTask(found);
    if (pendingByTaskKey.has(taskKey) && now > pending.expiresAt) {
      const decision = taskMonitorDeadlineDecision(found, now, pending.expiresAt);
      if (decision === "extend") pending.expiresAt = now + DEFAULT_MAX_POLL_MS;
      else if (decision === "expire") {
        pendingByTaskKey.delete(taskKey);
        const error = new TaskMonitoringTimeoutError(taskKey, projectId);
        pending.subscribers.forEach((subscriber) => subscriber.reject(error));
      }
    }
  }
  maybeStopProjectMonitoring(projectId);
}

export function awaitTaskCompletion(
  taskKey: string,
  projectId: string,
  options: { signal?: AbortSignal } = {},
): Promise<TaskState> {
  const resolved = resolveTaskProjectId(projectId);
  const signal = options.signal;
  const sessionError = taskSourcesByProject.get(resolved)?.sessionError;
  if (sessionError) return Promise.reject(sessionError);
  if (signal?.aborted) {
    return Promise.reject(
      signal.reason ?? new DOMException("Task monitoring aborted", "AbortError"),
    );
  }
  const existingMonitor = pendingByTaskKey.get(taskKey);
  if (existingMonitor && existingMonitor.projectId !== resolved) {
    return Promise.reject(
      new Error(`task ${taskKey} is already monitored under another project`),
    );
  }
  ensureSharedStream(resolved);
  ensureProjectPoller(resolved);
  const subscriber: PendingSubscriber = {
    resolve: () => undefined,
    reject: () => undefined,
  };
  let onAbort: (() => void) | null = null;
  const promise = new Promise<TaskState>((resolve, reject) => {
    subscriber.resolve = resolve;
    subscriber.reject = reject;
    onAbort = () => {
      const pending = pendingByTaskKey.get(taskKey);
      if (!pending) return;
      pending.subscribers.delete(subscriber);
      if (pending.subscribers.size === 0) {
        pendingByTaskKey.delete(taskKey);
      }
      reject(
        signal?.reason ?? new DOMException("Task monitoring aborted", "AbortError"),
      );
      maybeStopProjectMonitoring(resolved);
    };
    signal?.addEventListener("abort", onAbort, { once: true });
    const existing = pendingByTaskKey.get(taskKey);
    if (existing) {
      existing.subscribers.add(subscriber);
      existing.expiresAt = Math.max(
        existing.expiresAt,
        Date.now() + DEFAULT_MAX_POLL_MS,
      );
    } else {
      pendingByTaskKey.set(taskKey, {
        subscribers: new Set([subscriber]),
        projectId: resolved,
        expiresAt: Date.now() + DEFAULT_MAX_POLL_MS,
      });
    }
  });
  return promise.finally(() => {
    if (onAbort) signal?.removeEventListener("abort", onAbort);
    const pending = pendingByTaskKey.get(taskKey);
    pending?.subscribers.delete(subscriber);
    if (pending?.subscribers.size === 0) {
      pendingByTaskKey.delete(taskKey);
    }
    maybeStopProjectMonitoring(resolved);
  });
}

if (import.meta.hot) {
  import.meta.hot.dispose(() => {
    closeAllTaskMonitoring(new Error("task monitor reloaded"));
  });
}
