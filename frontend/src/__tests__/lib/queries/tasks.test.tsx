// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import ky from "ky";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", () => ({
  api: ky.create({ baseUrl: "http://localhost:3000/" }),
}));

import { server } from "@/__mocks__/msw/server";
import { sampleTask } from "@/__mocks__/msw/handlers/tasks";
import { useDeleteTask, useTasks } from "@/lib/queries/tasks";
import { useTaskCenterStore } from "@/task-center/store";

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

describe("useTasks polling", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    useTaskCenterStore.getState().reset();
  });

  afterEach(() => {
    vi.useRealTimers();
    useTaskCenterStore.getState().reset();
  });

  it("does not poll when the task center owns the same connected project", async () => {
    let requestCount = 0;
    server.use(
      http.get("*/api/v1/projects/demo/tasks", () => {
        requestCount += 1;
        return HttpResponse.json({ ok: true, data: [] });
      }),
    );

    useTaskCenterStore.getState().setProject("demo");
    useTaskCenterStore.getState().setHealth("connected");

    renderHook(() => useTasks({ project: "demo" }), { wrapper });

    await vi.waitFor(() => expect(requestCount).toBe(1));

    await vi.advanceTimersByTimeAsync(6000);

    expect(requestCount).toBe(1);
  });

  it("keeps polling active tasks when the task center owns a different project", async () => {
    let requestCount = 0;
    server.use(
      http.get("*/api/v1/projects/demo/tasks", () => {
        requestCount += 1;
        return HttpResponse.json({
          ok: true,
          data: [sampleTask({ task_key: "running", status: "running" })],
        });
      }),
    );

    useTaskCenterStore.getState().setProject("other");
    useTaskCenterStore.getState().setHealth("connected");

    renderHook(() => useTasks({ project: "demo" }), { wrapper });

    await vi.waitFor(() => expect(requestCount).toBe(1));

    await vi.advanceTimersByTimeAsync(2500);

    expect(requestCount).toBeGreaterThan(1);
  });

  it("purges a terminal task with its exact scope", async () => {
    let requestUrl = "";
    server.use(
      http.delete("*/api/v1/projects/demo/tasks/freezone_image/1", ({ request }) => {
        requestUrl = request.url;
        return HttpResponse.json({ ok: true, data: { deleted: true } });
      }),
    );

    const { result } = renderHook(() => useDeleteTask(), { wrapper });
    await result.current.mutateAsync({
      type: "freezone_image",
      project: "demo",
      episode: 1,
      beatNum: 3,
      scope: "grid:2",
    });

    const url = new URL(requestUrl);
    expect(url.searchParams.get("purge")).toBe("true");
    expect(url.searchParams.get("beat_num")).toBe("3");
    expect(url.searchParams.get("scope")).toBe("grid:2");
  });
});
