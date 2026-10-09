// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
//
// The taste graph and growth contract were live backend endpoints with no
// reader in the product. These tests pin the request shape the new
// 口味图谱 tab depends on.
import { beforeEach, describe, expect, it, vi } from "vitest";

import { apiClient } from "@/api/client";
import { getGrowthMemoryContract, getTasteGraph } from "@/api/agent-memory";

vi.mock("@/api/client", () => ({
  apiClient: vi.fn(),
}));

function jsonResponse(body: unknown) {
  return { json: async () => body };
}

describe("agent memory projections", () => {
  beforeEach(() => {
    vi.mocked(apiClient).mockReset();
  });

  it("omits the project query param when no project is given", async () => {
    vi.mocked(apiClient).mockReturnValueOnce(
      jsonResponse({
        schema: "taste_graph.v1",
        revision: "abc",
        project_id: "",
        source: { project: "TasteGraph-Skill", commit: "x", license: "MIT", integration: "y" },
        hard_loves: [],
        hard_antis: [],
        consultation: { do: [], avoid: [] },
        affinity_graph: { nodes: [], edges: [] },
        stats: { eligible: 0, rejected: 0, love_count: 0, anti_count: 0 },
      }) as never,
    );

    await getTasteGraph();

    expect(apiClient).toHaveBeenCalledWith("chat/memories/taste-graph");
  });

  it("scopes project preferences by passing the project id", async () => {
    vi.mocked(apiClient).mockReturnValueOnce(
      jsonResponse({
        schema: "taste_graph.v1",
        revision: "abc",
        project_id: "01M250K6NH4YVRAQQ5VEHT21WC",
        source: { project: "TasteGraph-Skill", commit: "x", license: "MIT", integration: "y" },
        hard_loves: [],
        hard_antis: [],
        consultation: { do: [], avoid: [] },
        affinity_graph: { nodes: [], edges: [] },
        stats: { eligible: 1, rejected: 0, love_count: 1, anti_count: 0 },
      }) as never,
    );

    await getTasteGraph("01M250K6NH4YVRAQQ5VEHT21WC");

    expect(apiClient).toHaveBeenCalledWith(
      "chat/memories/taste-graph?project=01M250K6NH4YVRAQQ5VEHT21WC",
    );
  });

  it("ignores a whitespace-only project so a blank canvas id does not scope it", async () => {
    vi.mocked(apiClient).mockReturnValueOnce(jsonResponse({ stats: {} }) as never);

    await getTasteGraph("   ");

    expect(apiClient).toHaveBeenCalledWith("chat/memories/taste-graph");
  });

  it("loads the growth distiller contract from its own endpoint", async () => {
    vi.mocked(apiClient).mockReturnValueOnce(
      jsonResponse({
        role: "growth_distiller",
        modelEnv: "GROWTH_DISTILLER_MODEL",
        thinkingLevelEnv: "GROWTH_DISTILLER_THINKING_LEVEL",
        modelRef: "direct/agent-9df1c7093446fd7a",
        modelSource: "default_text_model",
        autoTextModelEnv: "GROWTH_DISTILLER_AUTO_TEXT_MODEL",
        transport: "existing_text_model_registry",
        credentialSource: "existing_model_gateway",
        sideEffects: ["returns_candidate_only"],
        schemaVersion: "xiaoshu.director_recipe_candidate.v1",
      }) as never,
    );

    const contract = await getGrowthMemoryContract();

    expect(apiClient).toHaveBeenCalledWith("chat/memories/growth-contract");
    expect(contract.sideEffects).toEqual(["returns_candidate_only"]);
    expect(contract.modelSource).toBe("default_text_model");
  });
});
