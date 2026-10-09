import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  awaitTaskCompletion,
  openTaskStream,
  taskMonitorDeadlineDecision,
  cancelProjectTask,
  isTaskMonitoring,
  settleTask,
  registerProjectTaskSource,
  type TaskState,
  type TaskStatus,
} from "./tasks";

import { apiCall } from "./client";
import { SESSION_EXPIRED_EVENT } from "@/lib/session-expiry";

vi.mock("./client", () => ({
  apiCall: vi.fn(),
}));

function task(status: TaskStatus): TaskState {
  return {
    task_type: "freezone_video_gen",
    task_key: "task-1",
    username: "local",
    project: "demo",
    episode: 0,
    status,
  };
}

describe("task monitor deadline recovery", () => {
  beforeEach(() => {
    vi.mocked(apiCall).mockReset();
    class MockEventSource {
      onerror: ((event: Event) => void) | null = null;
      addEventListener() {}
      close() {}
    }
    vi.stubGlobal("EventSource", MockEventSource);
  });

  it("keeps monitoring before the deadline", () => {
    expect(taskMonitorDeadlineDecision(undefined, 99, 100)).toBe("keep");
  });

  it.each(["submitting", "queued", "pending", "starting", "running", "waiting"] as const)(
    "extends monitoring for visible %s tasks",
    (status) => {
      expect(taskMonitorDeadlineDecision(task(status), 101, 100)).toBe("extend");
    },
  );

  it("expires only when the task disappeared after the deadline", () => {
    expect(taskMonitorDeadlineDecision(undefined, 101, 100)).toBe("expire");
  });

  it.each(["completed", "failed", "cancelled"] as const)(
    "does not extend terminal status %s",
    (status) => {
      expect(taskMonitorDeadlineDecision(task(status), 101, 100)).toBe("expire");
    },
  );

  it("cancels an exact scoped project task", async () => {
    vi.mocked(apiCall).mockResolvedValue(undefined);

    await cancelProjectTask({
      projectId: "project/demo",
      taskType: "freezone_video_gen",
      episode: 0,
      scope: "job-123",
    });

    expect(apiCall).toHaveBeenCalledWith(
      "projects/project%2Fdemo/tasks/freezone_video_gen/0",
      { method: "DELETE", searchParams: { scope: "job-123" } },
    );
  });

  it("fans one task settlement out to every waiter without orphaning the first caller", async () => {
    const first = awaitTaskCompletion("task-shared", "demo");
    const second = awaitTaskCompletion("task-shared", "demo");
    expect(isTaskMonitoring("task-shared")).toBe(true);

    const completed = {
      ...task("completed"),
      task_key: "task-shared",
      result: { output_url: "/outputs/result.png" },
    };
    settleTask(completed);

    await expect(first).resolves.toBe(completed);
    await expect(second).resolves.toBe(completed);
    expect(isTaskMonitoring("task-shared")).toBe(false);
  });

  it("rejects every waiter when a shared task fails", async () => {
    const first = awaitTaskCompletion("task-failed", "demo");
    const second = awaitTaskCompletion("task-failed", "demo");

    settleTask({
      ...task("failed"),
      task_key: "task-failed",
      error: "ReadTimeout",
    });

    await expect(first).rejects.toMatchObject({
      message: "ReadTimeout",
      status: "failed",
      taskKey: "task-failed",
    });
    await expect(second).rejects.toMatchObject({
      message: "ReadTimeout",
      status: "failed",
      taskKey: "task-failed",
    });
    expect(isTaskMonitoring("task-failed")).toBe(false);
  });

  it("keeps the shared task monitored when only one waiter aborts", async () => {
    const controller = new AbortController();
    const aborted = awaitTaskCompletion("task-abort-one", "demo", {
      signal: controller.signal,
    });
    const survivor = awaitTaskCompletion("task-abort-one", "demo");

    controller.abort(new DOMException("caller left", "AbortError"));
    await expect(aborted).rejects.toMatchObject({ name: "AbortError" });
    expect(isTaskMonitoring("task-abort-one")).toBe(true);

    const completed = {
      ...task("completed"),
      task_key: "task-abort-one",
    };
    settleTask(completed);

    await expect(survivor).resolves.toBe(completed);
    expect(isTaskMonitoring("task-abort-one")).toBe(false);
  });
});

class StreamEventSource {
  static instances: StreamEventSource[] = [];
  readyState = 1;
  onerror: ((event: Event) => void) | null = null;
  private listeners = new Map<string, Array<(event: MessageEvent) => void>>();

  constructor(
    public readonly url: string,
    public readonly options?: EventSourceInit,
  ) {
    StreamEventSource.instances.push(this);
  }

  addEventListener(type: string, listener: EventListenerOrEventListenerObject) {
    const callback =
      typeof listener === "function"
        ? (listener as (event: MessageEvent) => void)
        : (event: MessageEvent) => listener.handleEvent(event);
    const entries = this.listeners.get(type) ?? [];
    entries.push(callback);
    this.listeners.set(type, entries);
  }

  dispatch(type: string, data: unknown) {
    const event = new MessageEvent(type, { data: JSON.stringify(data) });
    for (const callback of this.listeners.get(type) ?? []) callback(event);
  }

  triggerError() {
    this.onerror?.(new Event("error"));
  }

  close() {
    this.readyState = 2;
  }
}

describe("shared project task source", () => {
  beforeEach(() => {
    StreamEventSource.instances.length = 0;
    vi.stubGlobal("EventSource", StreamEventSource);
    vi.mocked(apiCall).mockReset();
    vi.useFakeTimers();
  });
  afterEach(() => vi.useRealTimers());

  it("uses one accepted snapshot read for simultaneous waiters, with slower healthy reconciliation", async () => {
    const read = vi.fn(async () => [task("running")]);
    const source = registerProjectTaskSource("shared", read);
    const controller = new AbortController();
    const first = awaitTaskCompletion("task-1", "shared", { signal: controller.signal });
    const second = awaitTaskCompletion("task-1", "shared", { signal: controller.signal });
    try {
      await vi.advanceTimersByTimeAsync(14_999);
      expect(read).not.toHaveBeenCalled();
      expect(StreamEventSource.instances).toHaveLength(0);
      expect(apiCall).not.toHaveBeenCalled();
      await vi.advanceTimersByTimeAsync(1);
      expect(read).toHaveBeenCalledOnce();
      read.mockResolvedValue([task("completed")]);
      await vi.advanceTimersByTimeAsync(15_000);
      await expect(first).resolves.toMatchObject({ status: "completed" });
      await expect(second).resolves.toMatchObject({ status: "completed" });
      expect(read).toHaveBeenCalledTimes(2);
      await vi.advanceTimersByTimeAsync(30_000);
      expect(read).toHaveBeenCalledTimes(2);
    } finally {
      controller.abort();
      source.close();
    }
  });

  it("shares fast disconnected reads even without waiters and stops when healthy", async () => {
    const read = vi.fn(async () => []);
    const source = registerProjectTaskSource("disconnected", read);
    try {
      source.setPolling(true);
      await vi.advanceTimersByTimeAsync(15_000);
      expect(read).toHaveBeenCalledTimes(3);
      source.setPolling(false);
      await vi.advanceTimersByTimeAsync(15_000);
      expect(read).toHaveBeenCalledTimes(3);
    } finally {
      source.close();
    }
  });

  it("reduces healthy 60-second reconciliation from 15 reads to 4 with the same pending task", async () => {
    vi.mocked(apiCall).mockResolvedValue([task("running")]);
    const standalone = awaitTaskCompletion("task-1", "standalone-count");
    await vi.advanceTimersByTimeAsync(60_000);
    expect(apiCall).toHaveBeenCalledTimes(15);
    settleTask(task("completed"));
    await standalone;

    const read = vi.fn(async () => [task("running")]);
    const source = registerProjectTaskSource("shared-count", read);
    const shared = awaitTaskCompletion("task-1", "shared-count");
    try {
      await vi.advanceTimersByTimeAsync(60_000);
      expect(read).toHaveBeenCalledTimes(4);
      // The task center and canvas now use the same transport owner.
      expect(StreamEventSource.instances).toHaveLength(1);
      settleTask(task("completed"));
      await shared;
    } finally {
      source.close();
    }
  });

  it("hands an existing standalone waiter to the source and back on source exit", async () => {
    const read = vi.fn(async () => [task("running")]);
    const waiting = awaitTaskCompletion("task-1", "handoff");
    expect(StreamEventSource.instances).toHaveLength(1);
    const source = registerProjectTaskSource("handoff", read);
    expect(StreamEventSource.instances[0].readyState).toBe(2);
    await vi.advanceTimersByTimeAsync(4000);
    expect(apiCall).not.toHaveBeenCalled();
    source.close();
    expect(StreamEventSource.instances).toHaveLength(2);
    vi.mocked(apiCall).mockResolvedValue([task("completed")]);
    await vi.advanceTimersByTimeAsync(4000);
    await expect(waiting).resolves.toMatchObject({ status: "completed" });
    expect(apiCall).toHaveBeenCalledOnce();
    expect(StreamEventSource.instances[1].readyState).toBe(2);
  });

  it("ignores an obsolete read finishing after the source exits", async () => {
    let finish!: (tasks: TaskState[]) => void;
    const source = registerProjectTaskSource("race", () => new Promise<TaskState[]>((resolve) => { finish = resolve; }));
    const waiting = awaitTaskCompletion("task-1", "race");
    await vi.advanceTimersByTimeAsync(15_000);
    source.close();
    finish([task("failed")]);
    await vi.advanceTimersByTimeAsync(0);
    expect(isTaskMonitoring("task-1")).toBe(true);
    vi.mocked(apiCall).mockResolvedValue([task("completed")]);
    await vi.advanceTimersByTimeAsync(4000);
    await expect(waiting).resolves.toMatchObject({ status: "completed" });
  });

  it("rejects shared waiters and stops fallback reads on session loss", async () => {
    const read = vi.fn(async () => []);
    const source = registerProjectTaskSource("expired", read);
    source.setPolling(true);
    const waiting = awaitTaskCompletion("task-1", "expired");
    const rejected = expect(waiting).rejects.toThrow("auth revoked");
    window.dispatchEvent(new Event(SESSION_EXPIRED_EVENT));
    await rejected;
    await vi.advanceTimersByTimeAsync(30_000);
    expect(read).not.toHaveBeenCalled();
    source.close();
    expect(StreamEventSource.instances).toHaveLength(0);
  });

  it("does not let a replaced source reject the new owner's waiter on session loss", async () => {
    const old = registerProjectTaskSource("replace", async () => []);
    const current = registerProjectTaskSource("replace", async () => []);
    // Closing an obsolete owner must only release that owner's listener.
    old.close(new Error("old session"));
    const waiting = awaitTaskCompletion("task-1", "replace");
    settleTask(task("completed"));
    await expect(waiting).resolves.toMatchObject({ status: "completed" });
    current.close();
  });

  it("keeps one in-flight read while transport health changes", async () => {
    let finish!: (tasks: TaskState[]) => void;
    const read = vi.fn(() => new Promise<TaskState[]>((resolve) => { finish = resolve; }));
    const source = registerProjectTaskSource("slow-read", read);
    const waiting = awaitTaskCompletion("task-1", "slow-read");
    await vi.advanceTimersByTimeAsync(15_000);
    expect(read).toHaveBeenCalledOnce();
    source.setPolling(true);
    await vi.advanceTimersByTimeAsync(5000);
    expect(read).toHaveBeenCalledOnce();
    finish([task("completed")]);
    await expect(waiting).resolves.toMatchObject({ status: "completed" });
    source.close();
  });

  it("refuses new shared waits after session loss without reopening a connection", async () => {
    const source = registerProjectTaskSource("lost-session", async () => []);
    window.dispatchEvent(new Event(SESSION_EXPIRED_EVENT));
    await expect(awaitTaskCompletion("task-1", "lost-session")).rejects.toThrow("auth revoked");
    expect(StreamEventSource.instances).toHaveLength(0);
    source.close();
  });
});

describe("openTaskStream reconnect budget", () => {
  beforeEach(() => {
    StreamEventSource.instances.length = 0;
    vi.stubGlobal("EventSource", StreamEventSource);
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  const opts = { backoffMs: [10], slowRetryMs: 100 };

  it("stops reconnecting once the session probe reports the session is gone", async () => {
    const onAuthRevoked = vi.fn();
    const checkSession = vi.fn(async () => false);
    const handle = openTaskStream({
      projectId: "demo",
      onTask: vi.fn(),
      checkSession,
      onAuthRevoked,
      ...opts,
    });

    StreamEventSource.instances[0].triggerError();
    await vi.advanceTimersByTimeAsync(10);
    StreamEventSource.instances[1].triggerError();
    await vi.advanceTimersByTimeAsync(10);
    StreamEventSource.instances[2].triggerError();
    await vi.advanceTimersByTimeAsync(0);

    expect(checkSession).toHaveBeenCalledTimes(1);
    expect(onAuthRevoked).toHaveBeenCalledTimes(1);

    // Dead session: no further EventSource is opened, even after the slow delay.
    await vi.advanceTimersByTimeAsync(5_000);
    expect(StreamEventSource.instances).toHaveLength(3);
    handle.close();
  });

  it("gives up and reports unrecoverable after the failure ceiling", async () => {
    const onUnrecoverable = vi.fn();
    const onAuthRevoked = vi.fn();
    const checkSession = vi.fn(async () => null);
    const handle = openTaskStream({
      projectId: "demo",
      onTask: vi.fn(),
      checkSession,
      onUnrecoverable,
      onAuthRevoked,
      maxConsecutiveFailures: 4,
      ...opts,
    });

    for (let i = 0; i < 4; i += 1) {
      const latest =
        StreamEventSource.instances[StreamEventSource.instances.length - 1];
      latest.triggerError();
      // fast backoff (10ms) for the first two, then the slow probe path (100ms).
      await vi.advanceTimersByTimeAsync(i < 2 ? 10 : i === 2 ? 100 : 0);
    }

    expect(onUnrecoverable).toHaveBeenCalledTimes(1);
    expect(onAuthRevoked).not.toHaveBeenCalled();
    expect(StreamEventSource.instances).toHaveLength(4);

    await vi.advanceTimersByTimeAsync(60_000);
    expect(StreamEventSource.instances).toHaveLength(4);
    handle.close();
  });

  it("slows down instead of probing while the session probe stays inconclusive", async () => {
    const checkSession = vi.fn(async () => null);
    const handle = openTaskStream({
      projectId: "demo",
      onTask: vi.fn(),
      checkSession,
      maxConsecutiveFailures: 10,
      ...opts,
    });

    StreamEventSource.instances[0].triggerError();
    await vi.advanceTimersByTimeAsync(10);
    StreamEventSource.instances[1].triggerError();
    await vi.advanceTimersByTimeAsync(10);
    StreamEventSource.instances[2].triggerError();
    await vi.advanceTimersByTimeAsync(0);
    expect(checkSession).toHaveBeenCalledTimes(1);

    // The reconnect now waits the slow interval, not the fast backoff.
    await vi.advanceTimersByTimeAsync(99);
    expect(StreamEventSource.instances).toHaveLength(3);
    await vi.advanceTimersByTimeAsync(1);
    expect(StreamEventSource.instances).toHaveLength(4);
    handle.close();
  });

  it("resets the failure budget when a task_updated event arrives", async () => {
    const onTask = vi.fn();
    const checkSession = vi.fn(async () => true);
    const handle = openTaskStream({
      projectId: "demo",
      onTask,
      checkSession,
      ...opts,
    });

    StreamEventSource.instances[0].triggerError();
    await vi.advanceTimersByTimeAsync(10);
    const healthy = StreamEventSource.instances[1];
    healthy.dispatch("task_updated", task("running"));
    expect(onTask).toHaveBeenCalledTimes(1);

    // A delivered event resets the count, so two more failures stay on the fast
    // path and the slow-path probe is never reached.
    healthy.triggerError();
    await vi.advanceTimersByTimeAsync(10);
    StreamEventSource.instances[2].triggerError();
    await vi.advanceTimersByTimeAsync(0);
    expect(checkSession).not.toHaveBeenCalled();
    handle.close();
  });
});
