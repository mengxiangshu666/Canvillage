// Copyright (c) 2026 ClaymoreLab
import { beforeEach, describe, expect, it } from "vitest";

import {
  DEFAULT_CANVAS_AGENT_RUN_MODE,
  loadCanvasAgentRunMode,
  normalizeCanvasAgentRunMode,
  saveCanvasAgentRunMode,
} from "./canvas-agent-run-mode";

describe("canvas Agent run mode", () => {
  beforeEach(() => localStorage.clear());

  it("defaults to real execution for a fresh Freezone session", () => {
    expect(DEFAULT_CANVAS_AGENT_RUN_MODE).toBe("auto");
    expect(loadCanvasAgentRunMode()).toBe("auto");
  });

  it("keeps an explicit draft opt-out persistent", () => {
    expect(saveCanvasAgentRunMode("draft")).toBe("draft");
    expect(loadCanvasAgentRunMode()).toBe("draft");
    expect(saveCanvasAgentRunMode("auto")).toBe("auto");
    expect(loadCanvasAgentRunMode()).toBe("auto");
  });

  it("normalizes stale or unknown values to the real execution default", () => {
    expect(normalizeCanvasAgentRunMode("unknown")).toBe("auto");
    expect(normalizeCanvasAgentRunMode(null)).toBe("auto");
    expect(normalizeCanvasAgentRunMode("draft")).toBe("draft");
  });
});
