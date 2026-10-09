// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { fetchFreezoneVideoModelsDetailed, type FreezoneVideoModelsResponse } from "@/api/ops";
import { useFreezoneVideoModels } from "./useFreezoneVideoModels";

vi.mock("@/api/ops", () => ({
  fetchFreezoneVideoModelsDetailed: vi.fn(),
  VIDEO_CHANNEL_OFFLINE_REASON: "offline",
}));
vi.mock("@/features/canvas/ui/ProviderModelPicker", () => ({ VIDEO_MODELS: [] }));

function response(id?: string): FreezoneVideoModelsResponse {
  return {
    models: id ? [{ id, label: id, apiModel: id, providerId: "seedance" }] : [],
    channel: { enabled: true, generationEnabled: true, disabledReason: "" },
  };
}

function deferred() {
  let resolve!: (value: FreezoneVideoModelsResponse) => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise<FreezoneVideoModelsResponse>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}

function refresh() {
  act(() => window.dispatchEvent(new CustomEvent(
    "village:direct-model-registry-changed", { detail: { kind: "video" } },
  )));
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("shared video model catalog refresh", () => {
  it.each(["empty", "failure", "old-model"])(
    "ignores an older %s response after a newer catalog arrives",
    async (outcome) => {
      const project = `race-${outcome}`;
      const requests: ReturnType<typeof deferred>[] = [];
      vi.mocked(fetchFreezoneVideoModelsDetailed).mockImplementation((name) => {
        if (name !== project) return Promise.resolve(response("other-project"));
        const request = deferred();
        requests.push(request);
        return request.promise;
      });
      const { result } = renderHook(() => useFreezoneVideoModels(project));
      await act(async () => requests[0].resolve(response("original")));
      refresh();
      refresh();
      await act(async () => requests[2].resolve(response("new-model")));
      await act(async () => {
        if (outcome === "failure") requests[1].reject(new Error("old failure"));
        else requests[1].resolve(response(outcome === "empty" ? undefined : outcome));
      });
      expect(result.current.models.map((model) => model.id)).toEqual(["new-model"]);
      expect(result.current).toMatchObject({
        isLoading: false, isFallback: false, error: null, channelEnabled: true,
      });
    },
  );

  it("preserves the catalog on current failure and accepts an intentional empty catalog", async () => {
    const project = "latest-result";
    const requests: ReturnType<typeof deferred>[] = [];
    vi.mocked(fetchFreezoneVideoModelsDetailed).mockImplementation((name) => {
      if (name !== project) return Promise.resolve(response("other-project"));
      const request = deferred();
      requests.push(request);
      return request.promise;
    });
    vi.spyOn(console, "warn").mockImplementation(() => {});
    const { result } = renderHook(() => useFreezoneVideoModels(project));
    await act(async () => requests[0].resolve(response("original")));
    refresh();
    const failure = new Error("temporary outage");
    await act(async () => requests[1].reject(failure));
    expect(result.current.models.map((model) => model.id)).toEqual(["original"]);
    expect(result.current.error).toBe(failure);
    refresh();
    await act(async () => requests[2].resolve(response()));
    expect(result.current).toMatchObject({ models: [], isLoading: false, isFallback: true, error: null });
  });
});
