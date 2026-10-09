// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, it, expect, vi, beforeAll, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider, initReactI18next } from "react-i18next";
import {
  createRouter,
  createRootRoute,
  RouterProvider,
  createMemoryHistory,
  Outlet,
} from "@tanstack/react-router";
import i18n from "i18next";

import { TaskActions } from "@/components/task-center/task-actions";
import { sampleTask } from "@/__mocks__/msw/handlers/tasks";
import type { TaskState } from "@/task-center/types";

// Minimal inline i18n instance — avoids HTTP backend loading in jsdom.
// Resources mirror public/locales/en/translation.json for the keys under test.
const testI18n = i18n.createInstance();

beforeAll(async () => {
  await testI18n.use(initReactI18next).init({
    lng: "en",
    fallbackLng: "en",
    interpolation: { escapeValue: false },
    resources: {
      en: {
        translation: {
          taskCenter: {
            actions: {
              cancel: "Cancel",
              delete: "Delete record",
              deleteConfirm: "Delete this task record?",
              openOrigin: "Open origin page",
              openWorkflow: "Open workflow run",
              locateNode: "Locate node",
              copyId: "Copy task ID",
              downloadLogs: "Download logs",
            },
            toast: {
              canceled: "Canceled {{label}}",
              deleted: "Task record deleted",
              copied: "Task ID copied",
              nodeMissing: "The linked node is no longer on this canvas",
            },
          },
        },
      },
    },
  });
});

function renderActions(task: TaskState) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  // Wrap TaskActions in a minimal router so <Link> can resolve to an in-memory
  // route. The root component hosts TaskActions alongside an Outlet so child
  // route components (if any) can render without masking the action bar.
  const rootRoute = createRootRoute({
    component: () => (
      <>
        <TaskActions task={task} />
        <Outlet />
      </>
    ),
  });
  const router = createRouter({
    routeTree: rootRoute,
    history: createMemoryHistory({ initialEntries: ["/"] }),
    defaultPendingMs: 0,
  });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={testI18n}>
        {/* eslint-disable-next-line @typescript-eslint/no-explicit-any */}
        <RouterProvider router={router as any} />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

describe("TaskActions", () => {
  beforeEach(() => {
    Object.assign(navigator, {
      clipboard: { writeText: vi.fn().mockResolvedValue(undefined) },
    });
  });

  it("shows Cancel for active unscoped tasks", async () => {
    renderActions(
      sampleTask({ status: "running", beat_num: null, scope: null }),
    );
    expect(await screen.findByText(/cancel|终止/i)).toBeInTheDocument();
  });

  it("shows Cancel for active scoped tasks (beat_num present)", async () => {
    // useCancelTask now accepts beatNum + scope, so every active task is
    // precisely cancellable — the old "hide for scoped" guard was removed
    // (see task-actions.tsx). Assert the Cancel affordance is now visible
    // for beat-scoped active tasks.
    renderActions(sampleTask({ status: "running", beat_num: 3 }));
    await screen.findByText(/copy task id|复制任务/i);
    expect(screen.getByText(/cancel|终止/i)).toBeInTheDocument();
  });

  it("shows Cancel for active scoped tasks (scope present)", async () => {
    renderActions(sampleTask({ status: "running", scope: "regen__abc" }));
    await screen.findByText(/copy task id|复制任务/i);
    expect(screen.getByText(/cancel|终止/i)).toBeInTheDocument();
  });

  it("hides Cancel for terminal tasks", async () => {
    renderActions(
      sampleTask({ status: "completed", beat_num: null, scope: null }),
    );
    await screen.findByText(/copy task id|复制任务/i);
    expect(screen.queryByText(/cancel|终止/i)).not.toBeInTheDocument();
  });

  it("shows Delete record for terminal tasks", async () => {
    renderActions(sampleTask({ status: "failed" }));
    expect(await screen.findByRole("button", { name: /delete record/i })).toBeInTheDocument();
  });

  it("Copy ID calls clipboard.writeText", async () => {
    renderActions(sampleTask({ task_id: "uuid-abc" }));
    const btn = await screen.findByText(/copy task id|复制任务/i);
    fireEvent.click(btn);
    await waitFor(() =>
      expect(navigator.clipboard.writeText).toHaveBeenCalledWith("uuid-abc"),
    );
  });

  it("Download logs disabled when logs empty", async () => {
    renderActions(sampleTask({ logs: [] }));
    const label = await screen.findByText(/download logs|下载日志/i);
    const btn = label.closest("button");
    expect(btn).toBeDisabled();
  });

  it("shows a real locate-node action when task metadata links a canvas node", async () => {
    renderActions(sampleTask({ metadata: { node_id: "node-a", canvas_id: "canvas-a" } }));
    expect(await screen.findByRole("button", { name: /locate node|定位节点/i })).toBeInTheDocument();
  });

  it("links a workflow task to its exact run and canvas", async () => {
    renderActions(
      sampleTask({
        project_id: "project-1",
        metadata: {
          workflow_run_id: "run-old-17",
          canvas_id: "canvas-9",
        },
      }),
    );

    const link = await screen.findByRole("link", { name: /open workflow run/i });
    expect(link).toHaveAttribute(
      "href",
      expect.stringContaining("/projects/project-1/production"),
    );
    expect(link.getAttribute("href")).toContain("canvas=canvas-9");
    expect(link.getAttribute("href")).toContain("workflowRunId=run-old-17");
  });

  it("does not offer a workflow link without both run and canvas identity", async () => {
    renderActions(
      sampleTask({
        metadata: { workflow_run_id: "run-old-17" },
      }),
    );

    await screen.findByText(/copy task id|复制任务/i);
    expect(screen.queryByRole("link", { name: /open workflow run/i })).not.toBeInTheDocument();
  });
});
