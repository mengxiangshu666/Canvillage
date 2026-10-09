// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
//
// Contract lock for the frontend's verification query layer.
//
// The 13 backend verification routes had no caller before this module existed.
// These assertions pin the wire contract the UI depends on, so a future rename
// of a path segment or a change of the envelope shape fails here rather than
// silently turning the panel into a wall of "not found".
import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { createElement, type ReactNode } from "react";

import { api } from "@/lib/api";
import {
  useScoreBeat,
  useSketchSelect,
  useVerifyEpisodeOverview,
  useVerifySimilarity,
  useVerifySketchColors,
} from "@/lib/queries/verification";

vi.mock("@/lib/api", () => ({
  api: {
    post: vi.fn(),
  },
}));

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { mutations: { retry: false } },
  });
  return createElement(QueryClientProvider, { client }, children);
}

/** Minimal ky Response stand-in; the hooks only call `.json()`. */
function jsonResponse(body: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  };
}

describe("verification query layer", () => {
  beforeEach(() => {
    vi.mocked(api.post).mockReset();
  });

  it("posts the duplicate scan to the episode similarity route", async () => {
    vi.mocked(api.post).mockReturnValueOnce(
      jsonResponse({
        ok: true,
        data: { pairs: [], duplicate_beats: [], overall_passed: true },
      }) as never,
    );

    const { result } = renderHook(() => useVerifySimilarity("my project", 3), { wrapper });
    const data = await result.current.mutateAsync();

    expect(api.post).toHaveBeenCalledTimes(1);
    const [url, options] = vi.mocked(api.post).mock.calls[0];
    expect(url).toBe("api/v1/projects/my%20project/episodes/3/verify/similarity");
    expect(options).toMatchObject({ json: {} });
    expect(data.overall_passed).toBe(true);
  });

  it("posts the colour cross-check to the sketch-colors route", async () => {
    vi.mocked(api.post).mockReturnValueOnce(
      jsonResponse({
        ok: true,
        data: {
          total_beats: 53,
          passed_beats: 30,
          failed_beats: 13,
          warned_beats: 10,
          failed_beat_numbers: [27, 28],
          beat_results: [],
          overall_passed: false,
        },
      }) as never,
    );

    const { result } = renderHook(() => useVerifySketchColors("p", 1), { wrapper });
    const data = await result.current.mutateAsync();

    expect(vi.mocked(api.post).mock.calls[0][0]).toBe(
      "api/v1/projects/p/episodes/1/verify/sketch-colors",
    );
    expect(data.failed_beat_numbers).toEqual([27, 28]);
  });

  it("sends sketch-select options with promote off unless asked", async () => {
    vi.mocked(api.post).mockReturnValueOnce(
      jsonResponse({
        ok: true,
        data: {
          total_beats: 2,
          selected_count: 2,
          beat_results: [],
          needs_regeneration: [],
          no_candidates: [],
          all_disqualified: [],
          accepted_beats: [],
          provisional_beats: [],
          summary: "2/2 beats selected",
        },
      }) as never,
    );

    const { result } = renderHook(() => useSketchSelect("p", 1), { wrapper });
    await result.current.mutateAsync({});

    const [, options] = vi.mocked(api.post).mock.calls[0];
    expect(options).toMatchObject({
      json: {
        quality_threshold: 7.0,
        score_gap_for_auto_select: 1.0,
        color_prefilter: true,
        fact_check: true,
        // Verifying must never rewrite the user's chosen sketches by default.
        promote_selected: false,
      },
    });
  });

  it("forwards an explicit promote_selected=true opt-in", async () => {
    vi.mocked(api.post).mockReturnValueOnce(
      jsonResponse({
        ok: true,
        data: {
          total_beats: 1,
          selected_count: 1,
          beat_results: [],
          needs_regeneration: [],
          no_candidates: [],
          all_disqualified: [],
          accepted_beats: [],
          provisional_beats: [],
          summary: "1/1 beats selected",
          promoted_count: 1,
        },
      }) as never,
    );

    const { result } = renderHook(() => useSketchSelect("p", 1), { wrapper });
    const data = await result.current.mutateAsync({ promoteSelected: true });

    const [, options] = vi.mocked(api.post).mock.calls[0];
    expect((options as { json: { promote_selected: boolean } }).json.promote_selected).toBe(true);
    expect(data.promoted_count).toBe(1);
  });

  it("targets the beat-scoped score route with the beat number", async () => {
    vi.mocked(api.post).mockReturnValueOnce(
      jsonResponse({
        ok: true,
        data: {
          beat_number: 7,
          pool_id: "latest",
          script_match: 9.5,
          identity_clarity: 10,
          total: 9.75,
        },
      }) as never,
    );

    const { result } = renderHook(() => useScoreBeat("p", 2, 7), { wrapper });
    const data = await result.current.mutateAsync(undefined);

    expect(vi.mocked(api.post).mock.calls[0][0]).toBe(
      "api/v1/projects/p/episodes/2/beats/7/score",
    );
    expect(data.total).toBe(9.75);
  });

  it("posts the episode review to episode-overview", async () => {
    vi.mocked(api.post).mockReturnValueOnce(
      jsonResponse({
        ok: true,
        data: {
          visual_rhythm: 7,
          composition_diversity: 8,
          narrative_arc: 6,
          style_unity: 9,
          total: 7.5,
          issues: [],
          overall_passed: true,
          summary: "ok",
        },
      }) as never,
    );

    const { result } = renderHook(() => useVerifyEpisodeOverview("p", 1), { wrapper });
    await result.current.mutateAsync();

    expect(vi.mocked(api.post).mock.calls[0][0]).toBe(
      "api/v1/projects/p/episodes/1/verify/episode-overview",
    );
  });

  it("turns the always-200 {ok:false} envelope into a rejection", async () => {
    // The backend answers 200 with ok:false on "no beats / no pool index";
    // without this the panel would render the error string as a result.
    vi.mocked(api.post).mockReturnValueOnce(
      jsonResponse({
        ok: false,
        error: "No pool index found. Generate sketches first.",
      }) as never,
    );

    const { result } = renderHook(() => useVerifySimilarity("p", 1), { wrapper });

    await expect(result.current.mutateAsync()).rejects.toThrow(
      "No pool index found. Generate sketches first.",
    );
  });

  it("accepts a URL-encoded project id without rewriting it", async () => {
    vi.mocked(api.post).mockReturnValueOnce(
      jsonResponse({ ok: true, data: { pairs: [], duplicate_beats: [], overall_passed: true } }) as never,
    );

    const { result } = renderHook(
      () => useVerifySimilarity("01M250K6NH4YVRAQQ5VEHT21WC", 1),
      { wrapper },
    );
    await waitFor(() => expect(result.current).toBeTruthy());
    await result.current.mutateAsync();

    expect(vi.mocked(api.post).mock.calls[0][0]).toBe(
      "api/v1/projects/01M250K6NH4YVRAQQ5VEHT21WC/episodes/1/verify/similarity",
    );
  });
});
