// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import ky from "ky";
import type { ReactNode } from "react";
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", () => ({
  api: ky.create({ baseUrl: "http://localhost:3000/" }),
}));

import {
  useExportStoryLab,
  useGenerateStoryLabStage,
  usePublishStoryLab,
  useSaveStoryLabConfig,
  useSaveStoryLabResult,
  useStoryLab,
} from "@/lib/queries/story-lab";
import type { StoryLabConfig } from "@/types/story-lab";

const server = setupServer();

beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function wrapper({ children }: { children: ReactNode }) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}

const config: StoryLabConfig = {
  title: "风雪山神庙",
  logline: "落魄教头在风雪夜识破陷阱并完成命运反击。",
  work_type: "micro_drama",
  genre: "古装悬疑",
  theme: "背叛与觉醒",
  target_units: 12,
  target_length: 1200,
  target_duration_seconds: 90,
  point_of_view: "第三人称",
  audience: "短剧观众",
  style_mode: "infer",
  style_id: "",
  style_name: "",
  style_prompt: "",
};

describe("Story Lab query contracts", () => {
  it("restores persisted configuration and artifacts on load", async () => {
    server.use(
      http.get("http://localhost:3000/api/v1/projects/demo/story-lab", () =>
        HttpResponse.json({
          ok: true,
          data: {
            config,
            stages: {
              bible: {
                stage: "bible",
                prompt_version: "story-lab.bible.v1",
                model: "fixture-model",
                result: { premise: config.logline },
              },
            },
          },
        }),
      ),
    );

    const { result } = renderHook(() => useStoryLab("demo"), { wrapper });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.data.config?.title).toBe("风雪山神庙");
    expect(result.current.data?.data.stages.bible?.result).toEqual({
      premise: config.logline,
    });
  });

  it("saves the complete creative contract without client-only fields", async () => {
    let received: unknown;
    server.use(
      http.put("http://localhost:3000/api/v1/projects/demo/story-lab/config", async ({ request }) => {
        received = await request.clone().json();
        return HttpResponse.json({ ok: true, data: { config, stages: {} } });
      }),
    );

    const { result } = renderHook(() => useSaveStoryLabConfig("demo"), { wrapper });
    await act(async () => {
      await result.current.mutateAsync(config);
    });

    expect(received).toEqual(config);
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
  });

  it("starts a scoped async stage task instead of waiting for model output", async () => {
    let received: unknown;
    server.use(
      http.post("http://localhost:3000/api/v1/projects/demo/story-lab/generate", async ({ request }) => {
        received = await request.clone().json();
        return HttpResponse.json({
          ok: true,
          data: {
            task_type: "story_lab_outline",
            stage: "outline",
            scope: "outline",
            task_id: "task-outline",
            backend: "arq",
            queue: "default",
          },
        });
      }),
    );

    const { result } = renderHook(() => useGenerateStoryLabStage("demo"), { wrapper });
    let response: Awaited<ReturnType<typeof result.current.mutateAsync>> | undefined;
    await act(async () => {
      response = await result.current.mutateAsync({
        stage: "outline",
        instructions: "强化第三幕反转",
      });
    });

    expect(received).toEqual({ stage: "outline", instructions: "强化第三幕反转" });
    expect(response?.data.task_type).toBe("story_lab_outline");
  });

  it("saves manual stage edits through the canonical stages endpoint", async () => {
    let received: unknown;
    server.use(
      http.put("http://localhost:3000/api/v1/projects/demo/story-lab/stages/bible", async ({ request }) => {
        received = await request.clone().json();
        return HttpResponse.json({
          ok: true,
          data: {
            stage: "bible",
            prompt_version: "story-lab.bible.v1",
            model: "manual",
            source: "manual",
            result: { premise: "人工修订" },
          },
        });
      }),
    );

    const { result } = renderHook(() => useSaveStoryLabResult("demo"), { wrapper });
    await act(async () => {
      await result.current.mutateAsync({
        stage: "bible",
        result: { premise: "人工修订" },
        editorNote: "锁定角色动机",
      });
    });

    expect(received).toEqual({
      result: { premise: "人工修订" },
      note: "锁定角色动机",
    });
  });

  it("publishes the draft through the existing ingest task contract", async () => {
    let received: unknown;
    server.use(
      http.post("http://localhost:3000/api/v1/projects/demo/story-lab/publish", async ({ request }) => {
        received = await request.clone().json();
        return HttpResponse.json({
          ok: true,
          data: {
            filename: "风雪山神庙.txt",
            task_type: "ingest_fast",
            task_id: "task-ingest",
            backend: "arq",
            queue: "default",
          },
        });
      }),
    );

    const { result } = renderHook(() => usePublishStoryLab("demo"), { wrapper });
    let response: Awaited<ReturnType<typeof result.current.mutateAsync>> | undefined;
    await act(async () => {
      response = await result.current.mutateAsync({
        filename: "风雪山神庙.txt",
        rebuild: true,
      });
    });

    expect(received).toEqual({ filename: "风雪山神庙.txt", rebuild: true });
    expect(response?.data.task_type).toBe("ingest_fast");
  });

  it("returns the canonical export download URL", async () => {
    server.use(
      http.post("http://localhost:3000/api/v1/projects/demo/story-lab/export", () =>
        HttpResponse.json({
          ok: true,
          data: {
            filename: "风雪山神庙.txt",
            download_url: "/api/v1/projects/demo/story-lab/exports/风雪山神庙.txt",
          },
        }),
      ),
    );

    const { result } = renderHook(() => useExportStoryLab("demo"), { wrapper });
    let response: Awaited<ReturnType<typeof result.current.mutateAsync>> | undefined;
    await act(async () => {
      response = await result.current.mutateAsync(undefined);
    });

    expect(response?.data.download_url).toContain("/story-lab/exports/");
  });
});
