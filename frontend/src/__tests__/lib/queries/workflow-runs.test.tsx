// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import {
  onlineManager,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

const { listWorkflowRuns, subscribeWorkflowRunSnapshots } = vi.hoisted(() => ({
  listWorkflowRuns: vi.fn(),
  subscribeWorkflowRunSnapshots: vi.fn((_options: {
    onRun: (run: WorkflowRun) => void;
    onError: () => void;
  }) => ({ close: vi.fn() })),
}));

vi.mock("@/api/workflow-runtime", () => ({
  listWorkflowRuns,
  commandWorkflowRun: vi.fn(),
  subscribeWorkflowRunSnapshots,
}));

import { useCanvasWorkflowRuns } from "@/lib/queries/workflow-runs";
import type { WorkflowRun } from "@/types/workflow-runtime";

function makeRun(status: WorkflowRun["status"]): WorkflowRun {
  return {
    id: "run-server-state",
    workflow_id: "freezone-final-film",
    workflow_version: 1,
    project_id: "fixture-project",
    canvas_id: "fixture-canvas",
    run_mode: "draft",
    status,
    current_frontier: [],
    step_states: {},
    inputs: {},
    artifacts: {},
    error: "",
    revision: 1,
    event_seq: 1,
    idempotency_key: "fixture-run",
    created_at: "2026-10-03T00:00:00Z",
    updated_at: "2026-10-03T00:00:00Z",
  } as WorkflowRun;
}

function createWrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchInterval: false } },
  });
  return {
    client,
    wrapper: ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    ),
  };
}

describe("useCanvasWorkflowRuns", () => {
  afterEach(() => {
    cleanup();
    onlineManager.setOnline(true);
    vi.clearAllMocks();
  });

  it("feeds the active run SSE snapshot into the shared list cache", async () => {
    const initialRun = makeRun("running");
    const liveRun = { ...initialRun, revision: 2, event_seq: 2 };
    const close = vi.fn();
    let onRun: ((run: WorkflowRun) => void) | undefined;
    let onError: (() => void) | undefined;
    listWorkflowRuns.mockResolvedValue([initialRun]);
    subscribeWorkflowRunSnapshots.mockImplementation((options) => {
      onRun = options.onRun;
      onError = options.onError;
      return { close, cursor: () => 0 };
    });

    const environment = createWrapper();
    const mounted = renderHook(
      () => useCanvasWorkflowRuns("fixture-project", "fixture-canvas"),
      { wrapper: environment.wrapper },
    );
    await waitFor(() => expect(mounted.result.current.data).toEqual([initialRun]));
    const interval = () => {
      const cached = environment.client.getQueryCache().find({
        queryKey: ["workflow-runs", "fixture-project", "fixture-canvas"],
      })!;
      const value = (cached.options as { refetchInterval?: unknown }).refetchInterval;
      return typeof value === "function" ? value(cached) : value;
    };
    expect(interval()).toBe(3_000);
    expect(subscribeWorkflowRunSnapshots).toHaveBeenCalledWith(
      expect.objectContaining({
        projectId: "fixture-project",
        canvasId: "fixture-canvas",
        runId: initialRun.id,
      }),
    );

    act(() => onRun?.(liveRun));
    await waitFor(() => expect(mounted.result.current.data).toEqual([liveRun]));
    expect(interval()).toBe(15_000);
    act(() => onError?.());
    await waitFor(() => expect(interval()).toBe(3_000));
    mounted.unmount();
    expect(close).toHaveBeenCalledTimes(1);
  });

  it("reloads the current canvas state with a fresh query client", async () => {
    const serverRun = makeRun("running");
    listWorkflowRuns.mockResolvedValue([serverRun]);
    const firstEnvironment = createWrapper();

    const firstMount = renderHook(
      () => useCanvasWorkflowRuns("fixture-project", "fixture-canvas"),
      { wrapper: firstEnvironment.wrapper },
    );
    await waitFor(() =>
      expect(firstMount.result.current.data).toEqual([serverRun]),
    );
    firstMount.unmount();

    const refreshedRun = { ...serverRun, revision: 2 };
    listWorkflowRuns.mockResolvedValue([refreshedRun]);
    const reloadEnvironment = createWrapper();
    const afterReload = renderHook(
      () => useCanvasWorkflowRuns("fixture-project", "fixture-canvas"),
      { wrapper: reloadEnvironment.wrapper },
    );

    await waitFor(() =>
      expect(afterReload.result.current.data).toEqual([refreshedRun]),
    );
    expect(listWorkflowRuns).toHaveBeenCalledTimes(2);
    expect(listWorkflowRuns).toHaveBeenNthCalledWith(
      1,
      "fixture-project",
      "fixture-canvas",
      expect.any(AbortSignal),
    );
    expect(listWorkflowRuns).toHaveBeenNthCalledWith(
      2,
      "fixture-project",
      "fixture-canvas",
      expect.any(AbortSignal),
    );
    afterReload.unmount();
  });

  it("keeps cached runs visible and refetches after the connection returns", async () => {
    const previousRun = makeRun("running");
    const environment = createWrapper();
    listWorkflowRuns.mockResolvedValueOnce([previousRun]);

    const mounted = renderHook(
      () => useCanvasWorkflowRuns("fixture-project", "fixture-canvas"),
      { wrapper: environment.wrapper },
    );
    await waitFor(() =>
      expect(mounted.result.current.data).toEqual([previousRun]),
    );

    listWorkflowRuns.mockRejectedValueOnce(new Error("temporarily offline"));
    const failedRefresh = await mounted.result.current.refetch();
    expect(failedRefresh.isError).toBe(true);
    expect(mounted.result.current.data).toEqual([previousRun]);

    const latestRun = { ...previousRun, revision: 2 };
    listWorkflowRuns.mockResolvedValueOnce([latestRun]);
    onlineManager.setOnline(false);
    await waitFor(() => expect(onlineManager.isOnline()).toBe(false));
    expect(listWorkflowRuns).toHaveBeenCalledTimes(2);

    onlineManager.setOnline(true);
    await waitFor(() =>
      expect(mounted.result.current.data).toEqual([latestRun]),
    );
    expect(listWorkflowRuns).toHaveBeenCalledTimes(3);
    mounted.unmount();
  });
});
