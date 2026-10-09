// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import {
  fetchFreezoneJobResult,
  type FreezoneJobRef,
} from "@/api/ops";
import {
  awaitTaskCompletion,
  type TaskState,
} from "@/api/tasks";

export function resolveFreezoneMediaUrl(
  result: Record<string, unknown> | null | undefined,
): string | null {
  if (!result) return null;
  for (const key of ["video_url", "image_url", "output_url", "url"]) {
    const value = result[key];
    if (typeof value === "string" && value.trim().length > 0) return value;
  }
  return null;
}

export function resolveFreezoneTaskPreviewUrl(
  task: Pick<TaskState, "metadata"> | null | undefined,
): string | null {
  const value = task?.metadata?.provider_preview_url;
  return typeof value === "string" && value.trim().length > 0 ? value : null;
}

export interface AwaitFreezoneJobMediaResultOptions {
  probeDelayMs?: number;
  /** Upper bound for the result URL probe backoff. */
  maxProbeDelayMs?: number;
  maxProbeMs?: number;
  onTaskCompletedWithoutUrl?: (task: TaskState) => void;
  signal?: AbortSignal;
}

export interface AwaitFreezoneJobMediaResult {
  url: string | null;
  result: Record<string, unknown> | null;
  task: TaskState | null;
  source: "task" | "result" | "none";
}

function abortReason(signal?: AbortSignal): unknown {
  return signal?.reason ?? new DOMException("Media result probe aborted", "AbortError");
}

function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  if (signal?.aborted) return Promise.reject(abortReason(signal));
  return new Promise((resolve, reject) => {
    const timer = globalThis.setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    const onAbort = () => {
      globalThis.clearTimeout(timer);
      reject(abortReason(signal));
    };
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

export function mediaResultProbeDelay(
  attempt: number,
  initialDelayMs: number,
  maxDelayMs: number,
): number {
  const safeAttempt = Math.max(0, Math.floor(attempt));
  const upperBound = Math.max(initialDelayMs, maxDelayMs);
  return Math.min(
    upperBound,
    Math.max(initialDelayMs, Math.round(initialDelayMs * 1.5 ** safeAttempt)),
  );
}

async function probeJobResultUrl(
  projectId: string,
  ref: FreezoneJobRef,
  options: Required<
    Pick<
      AwaitFreezoneJobMediaResultOptions,
      "probeDelayMs" | "maxProbeDelayMs" | "maxProbeMs"
    >
  >,
  signal?: AbortSignal,
): Promise<Record<string, unknown> | null> {
  const deadline = Date.now() + options.maxProbeMs;
  let first = true;
  let attempt = 0;
  while (first || Date.now() <= deadline) {
    if (signal?.aborted) throw abortReason(signal);
    first = false;
    try {
      const result = await fetchFreezoneJobResult(
        projectId,
        ref.task_type,
        ref.job_id,
        { signal },
      );
      if (typeof result?.url === "string" && result.url.trim().length > 0) {
        return { ...result };
      }
    } catch {
      // Result endpoint can legitimately be 404 / not-on-disk before the
      // durable task writes metadata. Keep probing; task failure is handled by
      // awaitTaskCompletion in the sibling race.
    }
    await sleep(
      mediaResultProbeDelay(attempt++, options.probeDelayMs, options.maxProbeDelayMs),
      signal,
    );
  }
  return null;
}

export async function awaitFreezoneJobMediaResult(
  projectId: string,
  ref: FreezoneJobRef,
  options: AwaitFreezoneJobMediaResultOptions = {},
): Promise<AwaitFreezoneJobMediaResult> {
  const probeDelayMs = Math.max(100, Math.floor(options.probeDelayMs ?? 900));
  const probeOptions = {
    probeDelayMs,
    maxProbeDelayMs: Math.max(
      probeDelayMs,
      Math.floor(options.maxProbeDelayMs ?? 6000),
    ),
    maxProbeMs: Math.max(1000, Math.floor(options.maxProbeMs ?? 90_000)),
  };

  let task: TaskState | null = null;
  let taskSettled = false;
  const probeController = new AbortController();
  const abortProbe = () => {
    if (!probeController.signal.aborted) probeController.abort();
  };
  const taskController = new AbortController();
  const onCallerAbort = () => {
    taskController.abort(abortReason(options.signal));
    probeController.abort(abortReason(options.signal));
  };
  options.signal?.addEventListener("abort", onCallerAbort, { once: true });
  if (options.signal?.aborted) onCallerAbort();

  const taskPromise = awaitTaskCompletion(ref.task_key, projectId, {
    signal: taskController.signal,
  }).then(
    (completed) => {
      task = completed;
      taskSettled = true;
      const url = resolveFreezoneMediaUrl(completed.result);
      if (!url) options.onTaskCompletedWithoutUrl?.(completed);
      return { kind: "task" as const, url, result: completed.result ?? null, task: completed };
    },
  );

  const resultPromise = probeJobResultUrl(
    projectId,
    ref,
    probeOptions,
    probeController.signal,
  ).then(
    (result) => ({ kind: "result" as const, url: resolveFreezoneMediaUrl(result), result }),
  ).catch((error) => {
    if (probeController.signal.aborted) return { kind: "result" as const, url: null, result: null };
    throw error;
  });

  try {
    const first = await Promise.race([taskPromise, resultPromise]);
    if (first.kind === "result" && first.url) {
      abortProbe();
      return { url: first.url, result: first.result, task, source: "result" };
    }
    if (first.kind === "task" && first.url) {
      abortProbe();
      return { url: first.url, result: first.result, task: first.task, source: "task" };
    }

    if (!taskSettled) {
      const completed = await taskPromise;
      if (completed.url) {
        abortProbe();
        return { url: completed.url, result: completed.result, task: completed.task, source: "task" };
      }
    }

    const late = first.kind === "result" ? first : await resultPromise;
    if (late.url) return { url: late.url, result: late.result, task, source: "result" };
    return { url: null, result: null, task, source: "none" };
  } finally {
    options.signal?.removeEventListener("abort", onCallerAbort);
    taskController.abort();
    abortProbe();
  }
}
