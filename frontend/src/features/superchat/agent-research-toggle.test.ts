import { beforeEach, describe, expect, it } from "vitest";

import {
  loadAigcResearchEnabled,
  saveAigcResearchEnabled,
} from "./agent-research-toggle";

describe("Agent research toggle", () => {
  beforeEach(() => localStorage.clear());

  it("defaults off and persists independently per project canvas", () => {
    expect(loadAigcResearchEnabled("project-a", "canvas-a")).toBe(false);

    saveAigcResearchEnabled("project-a", "canvas-a", true);

    expect(loadAigcResearchEnabled("project-a", "canvas-a")).toBe(true);
    expect(loadAigcResearchEnabled("project-a", "canvas-b")).toBe(false);
    expect(loadAigcResearchEnabled("project-b", "canvas-a")).toBe(false);
  });
});
