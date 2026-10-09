// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { cleanup, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  useCanvasWorkflowRuns: vi.fn(),
  useWorkflowRun: vi.fn(),
  useRetryWorkflowStep: vi.fn(),
  useRouterState: vi.fn(),
}));

vi.mock("@tanstack/react-router", () => ({
  Link: ({ children }: { children: ReactNode }) => <a>{children}</a>,
  useRouterState: mocks.useRouterState,
}));

vi.mock("@/lib/queries/workflow-runs", () => ({
  useCanvasWorkflowRuns: mocks.useCanvasWorkflowRuns,
  useWorkflowRun: mocks.useWorkflowRun,
  useRetryWorkflowStep: mocks.useRetryWorkflowStep,
}));

vi.mock("@/lib/url-params", () => ({
  readLastCanvas: () => "canvas-1",
}));

import { WorkflowRunOverview } from "./workflow-run-overview";
import type { WorkflowRun } from "@/types/workflow-runtime";

function makeRun(overrides: Partial<WorkflowRun> = {}): WorkflowRun {
  return {
    id: "run-default",
    workflow_id: "freezone-final-film",
    workflow_version: 1,
    project_id: "project-1",
    canvas_id: "canvas-1",
    run_mode: "draft",
    status: "completed",
    current_frontier: [],
    step_states: {},
    inputs: {},
    artifacts: {},
    error: "",
    revision: 1,
    event_seq: 1,
    idempotency_key: "idem-default",
    created_at: "2026-10-03T00:00:00Z",
    updated_at: "2026-10-03T00:00:00Z",
    ...overrides,
  } as WorkflowRun;
}

describe("WorkflowRunOverview", () => {
  beforeEach(() => {
    mocks.useRouterState.mockImplementation(({ select }) =>
      select({ location: { search: { canvas: "canvas-1" } } }),
    );
    mocks.useCanvasWorkflowRuns.mockReturnValue({
      data: [],
      isFetching: false,
      isError: false,
      isLoading: false,
    });
    mocks.useWorkflowRun.mockReturnValue({
      data: undefined,
      isError: false,
      isLoading: false,
    });
    mocks.useRetryWorkflowStep.mockReturnValue({
      isPending: false,
      mutateAsync: vi.fn(),
    });
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("renders older active and paused runs alongside recent completed runs", () => {
    mocks.useCanvasWorkflowRuns.mockReturnValue({
      data: [
        makeRun({
          id: "old-running",
          status: "running",
          created_at: "2026-09-01T00:00:00Z",
        }),
        makeRun({ id: "newest", created_at: "2026-10-03T00:00:00Z" }),
        makeRun({ id: "second-newest", created_at: "2026-10-02T00:00:00Z" }),
        makeRun({ id: "third-newest", created_at: "2026-10-01T00:00:00Z" }),
        makeRun({
          id: "old-paused",
          status: "paused",
          created_at: "2026-08-01T00:00:00Z",
        }),
        makeRun({
          id: "old-failed",
          status: "failed",
          error_code: "workflow_script_task_failed",
          error: "脚本步骤失败，保留原 Run 可重试。",
          step_states: {
            script_contract: {
              id: "script_contract",
              label: "生成脚本合同",
              type: "local_task",
              handler: "workflow.script_contract",
              depends_on: [],
              status: "failed",
              attempt: 1,
              error: "缺少必要脚本字段",
              started_at: "",
              completed_at: "",
            },
          },
          artifacts: {
            script_contract: {
              recovery: {
                schema: "workflow_step_recovery.v1",
                workflow_run_id: "old-failed",
                step_id: "script_contract",
                action: "repair_script_contract",
                instruction: "先补齐缺失字段，再继续原 Run。",
              },
            },
          },
          created_at: "2026-07-15T00:00:00Z",
        }),
        makeRun({
          id: "older-done",
          created_at: "2026-07-01T00:00:00Z",
        }),
      ],
      isFetching: false,
      isError: false,
      isLoading: false,
    });

    render(<WorkflowRunOverview projectId="project-1" />);

    expect(screen.getByText("old-running")).toBeInTheDocument();
    expect(screen.getByText("old-paused")).toBeInTheDocument();
    expect(screen.getByText("old-failed")).toBeInTheDocument();
    expect(screen.getByText(/缺少必要脚本字段/)).toBeInTheDocument();
    expect(
      screen.getByText(/先补齐缺失字段，再继续原 Run。/),
    ).toBeInTheDocument();
    expect(screen.getByText("newest")).toBeInTheDocument();
    expect(screen.queryByText("older-done")).not.toBeInTheDocument();
  });

  it("keeps the last known runs visible when a refresh fails", () => {
    mocks.useCanvasWorkflowRuns.mockReturnValue({
      data: [makeRun({ id: "last-known-run", status: "running" })],
      isFetching: false,
      isError: true,
      isLoading: false,
    });

    render(<WorkflowRunOverview projectId="project-1" />);

    expect(screen.getByRole("status")).toHaveTextContent(
      "状态暂时无法更新，以下是最近一次读取的工作流状态。",
    );
    expect(screen.getByText("last-known-run")).toBeInTheDocument();
    expect(screen.queryByText("工作流状态读取失败。")).not.toBeInTheDocument();
  });

  it("loads, retains, and highlights an explicitly requested older run", () => {
    const requestedRun = makeRun({
      id: "requested-run",
      created_at: "2026-08-01T00:00:00Z",
    });
    mocks.useRouterState.mockImplementation(({ select }) =>
      select({
        location: {
          search: { canvas: "canvas-1", workflowRunId: "requested-run" },
        },
      }),
    );
    mocks.useCanvasWorkflowRuns.mockReturnValue({
      data: [
        makeRun({ id: "recent-1", created_at: "2026-10-03T00:00:00Z" }),
        makeRun({ id: "recent-2", created_at: "2026-10-02T00:00:00Z" }),
        makeRun({ id: "recent-3", created_at: "2026-10-01T00:00:00Z" }),
      ],
      isFetching: false,
      isError: false,
      isLoading: false,
    });
    mocks.useWorkflowRun.mockReturnValue({
      data: requestedRun,
      isError: false,
      isLoading: false,
    });

    render(<WorkflowRunOverview projectId="project-1" />);

    expect(screen.getByText("requested-run")).toBeInTheDocument();
    expect(document.getElementById("workflow-run-requested-run")).toHaveAttribute(
      "aria-current",
      "location",
    );
  });

  it("does not display a requested run from another canvas", () => {
    mocks.useRouterState.mockImplementation(({ select }) =>
      select({
        location: {
          search: { canvas: "canvas-1", workflowRunId: "foreign-run" },
        },
      }),
    );
    mocks.useWorkflowRun.mockReturnValue({
      data: makeRun({ id: "foreign-run", canvas_id: "canvas-2" }),
      isError: false,
      isLoading: false,
    });

    render(<WorkflowRunOverview projectId="project-1" />);

    expect(screen.queryByText("foreign-run")).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(
      "任务关联的工作流不属于当前画布，未展示。",
    );
  });
});
