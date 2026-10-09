import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CANVAS_PATCH_EVENT } from "./canvas-patch-events";
import {
  activeWorkflowRunFromList,
  emitWorkflowRun,
  mergeWorkflowRun,
  recentlyCreatedWorkflowRun,
  subscribeWorkflowRunLive,
  WORKFLOW_RUN_EVENT,
  workflowCanvasPatchFromEvent,
  workflowCanvasPatchFromRun,
  workflowRunFromEnvelope,
  workflowRunCursorStorageKey,
} from "./workflow-run-live";
import type { WorkflowRun, WorkflowRunEvent } from "@/types/workflow-runtime";

class MockEventSource {
  static instances: MockEventSource[] = [];

  readonly url: string;
  readonly withCredentials: boolean;
  readonly listeners = new Map<string, Array<(event: Event) => void>>();
  closed = false;

  constructor(url: string | URL, init?: EventSourceInit) {
    this.url = String(url);
    this.withCredentials = init?.withCredentials ?? false;
    MockEventSource.instances.push(this);
  }

  addEventListener(type: string, callback: EventListenerOrEventListenerObject) {
    const listener = typeof callback === "function"
      ? callback
      : (event: Event) => callback.handleEvent(event);
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener]);
  }

  dispatch(type: string, data: unknown) {
    const event = new MessageEvent(type, { data: JSON.stringify(data) });
    for (const listener of this.listeners.get(type) ?? []) listener(event);
  }

  close() {
    this.closed = true;
  }
}

function event(overrides: Partial<WorkflowRunEvent> = {}): WorkflowRunEvent {
  return {
    run_id: "run-1",
    event_id: "event-1",
    seq: 1,
    type: "receipt_recorded",
    step_id: "canvas_structure",
    payload: {
      command_id: "command-1",
      canvas_revision: 7,
    },
    error: "",
    source: "canvas_gateway",
    created_at: "2026-08-16T00:00:00Z",
    ...overrides,
  };
}

function run(overrides: Partial<WorkflowRun> = {}): WorkflowRun {
  return {
    id: "run-1",
    workflow_id: "one-click-film",
    workflow_version: 1,
    project_id: "project-1",
    canvas_id: "canvas-1",
    run_mode: "draft",
    status: "running",
    current_frontier: ["canvas_structure"],
    step_states: {},
    inputs: {},
    artifacts: {},
    error: "",
    revision: 1,
    event_seq: 1,
    idempotency_key: "start-1",
    created_at: "2026-08-16T00:00:00Z",
    updated_at: "2026-08-16T00:00:00Z",
    ...overrides,
  };
}

describe("workflow run live subscription", () => {
  beforeEach(() => {
    MockEventSource.instances.length = 0;
    window.sessionStorage.clear();
    vi.stubGlobal("EventSource", MockEventSource);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    window.sessionStorage.clear();
  });

  it("turns an authoritative workflow receipt into a canvas reconcile patch", () => {
    expect(workflowCanvasPatchFromEvent(
      "project-1",
      "canvas-1",
      "run-1",
      event(),
    )).toEqual(expect.objectContaining({
      projectId: "project-1",
      canvasId: "canvas-1",
      runId: "run-1",
      commandId: "command-1",
      revision: 7,
      serverApplied: true,
      snapshotRequired: true,
      uiReconcileRequired: true,
    }));
    expect(workflowCanvasPatchFromEvent(
      "project-1",
      "canvas-1",
      "run-1",
      event({ type: "steering_added" }),
    )).toBeNull();
  });

  it("validates and dispatches a new workflow run from the chat transport", () => {
    const current = run({ revision: 0, event_seq: 0 });
    expect(workflowRunFromEnvelope({ run: current })).toEqual(current);
    expect(workflowRunFromEnvelope({
      run: { ...current, canvas_id: "" },
    })).toBeNull();

    const received: WorkflowRun[] = [];
    const listener = (event: Event) => {
      received.push((event as CustomEvent<WorkflowRun>).detail);
    };
    window.addEventListener(WORKFLOW_RUN_EVENT, listener);
    emitWorkflowRun(current);
    window.removeEventListener(WORKFLOW_RUN_EVENT, listener);
    expect(received).toEqual([current]);
  });

  it("reconciles the final canvas revision carried by a run snapshot", () => {
    const completed = run({
      status: "completed",
      last_verified_canvas_revision: 12,
    });
    expect(workflowCanvasPatchFromRun(completed)).toMatchObject({
      projectId: "project-1",
      canvasId: "canvas-1",
      runId: "run-1",
      revision: 12,
      uiReconcileRequired: true,
    });

    const patches: unknown[] = [];
    const onPatch = (raw: Event) => patches.push((raw as CustomEvent).detail);
    window.addEventListener(CANVAS_PATCH_EVENT, onPatch);
    emitWorkflowRun(completed);
    window.removeEventListener(CANVAS_PATCH_EVENT, onPatch);
    expect(patches).toHaveLength(1);
  });

  it("discovers only a workflow created around the dispatch receipt", () => {
    const observedAt = Date.parse("2026-08-16T00:01:00Z");
    const recent = run({ id: "run-recent", created_at: "2026-08-16T00:00:58Z" });
    const stale = run({ id: "run-stale", created_at: "2026-08-15T23:59:00Z" });

    expect(recentlyCreatedWorkflowRun([stale, recent], observedAt)?.id).toBe(
      "run-recent",
    );
    expect(recentlyCreatedWorkflowRun([stale], observedAt)).toBeNull();
  });

  it("resumes from the stored cursor, deduplicates events and closes on terminal", () => {
    window.sessionStorage.setItem(
      workflowRunCursorStorageKey("project-1", "run-1"),
      "4",
    );
    const onRun = vi.fn();
    const onEvent = vi.fn();
    const patches: unknown[] = [];
    const onPatch = (raw: Event) => patches.push((raw as CustomEvent).detail);
    window.addEventListener(CANVAS_PATCH_EVENT, onPatch);

    const subscription = subscribeWorkflowRunLive({
      projectId: "project-1",
      canvasId: "canvas-1",
      runId: "run-1",
      onRun,
      onEvent,
    });
    const source = MockEventSource.instances[0];

    expect(source.url).toContain("after_seq=4");
    expect(source.withCredentials).toBe(true);
    source.dispatch("workflow.event", {
      run_id: "run-1",
      event: event({ event_id: "event-5", seq: 5 }),
    });
    source.dispatch("workflow.event", {
      run_id: "run-1",
      event: event({ event_id: "event-5", seq: 5 }),
    });
    source.dispatch("workflow.snapshot", { run: run({ event_seq: 5 }), cursor: 5 });
    source.dispatch("workflow.terminal", {
      run: run({ status: "completed", event_seq: 5 }),
      cursor: 5,
    });

    expect(onEvent).toHaveBeenCalledTimes(1);
    expect(patches).toHaveLength(1);
    expect(onRun).toHaveBeenCalledTimes(2);
    expect(subscription.cursor()).toBe(5);
    expect(source.closed).toBe(true);
    expect(window.sessionStorage.getItem(
      workflowRunCursorStorageKey("project-1", "run-1"),
    )).toBe("5");

    window.removeEventListener(CANVAS_PATCH_EVENT, onPatch);
  });
});

describe("workflow run snapshot merge", () => {
  it("does not revive a completed historical run as the current Agent task", () => {
    expect(activeWorkflowRunFromList([
      run({ id: "completed-run", status: "completed" }),
    ])).toBeNull();
    expect(activeWorkflowRunFromList([
      run({ id: "completed-run", status: "completed" }),
      run({ id: "failed-run", status: "failed" }),
    ])?.id).toBe("failed-run");
  });

  it("keeps a completed film only when its release gate needs attention", () => {
    const completed = run({
      id: "completed-film",
      status: "completed",
      release_readiness: {
        schema: "release_readiness_contract.v1",
        status: "blocked",
        reason_code: "delivery_qc_failed",
        can_publish: false,
        required: true,
        failed_checks: ["freeze_frames"],
        not_run_checks: [],
        missing_checks: [],
      },
    });

    expect(activeWorkflowRunFromList([completed])?.id).toBe("completed-film");
  });

  it("keeps a newly announced run when an older list response arrives late", () => {
    const liveRun = run({
      id: "run-new",
      created_at: "2026-08-16T00:01:00Z",
      updated_at: "2026-08-16T00:01:00Z",
      revision: 0,
      event_seq: 0,
    });
    const staleListRun = run({
      id: "run-old",
      created_at: "2026-08-16T00:00:00Z",
      updated_at: "2026-08-16T00:02:00Z",
      revision: 9,
      event_seq: 12,
    });

    expect(mergeWorkflowRun(liveRun, staleListRun)).toBe(liveRun);
  });

  it("rejects stale snapshots for the same run and accepts a newer revision", () => {
    const current = run({ revision: 4, event_seq: 7 });
    const stale = run({ revision: 3, event_seq: 20 });
    const newer = run({ revision: 5, event_seq: 8, status: "completed" });

    expect(mergeWorkflowRun(current, stale)).toBe(current);
    expect(mergeWorkflowRun(current, newer)).toBe(newer);
  });

  it("accepts a validated run when the canvas scope changes", () => {
    const current = run({ canvas_id: "canvas-old" });
    const incoming = run({ canvas_id: "canvas-new", id: "run-new" });

    expect(mergeWorkflowRun(current, incoming)).toBe(incoming);
  });
});
