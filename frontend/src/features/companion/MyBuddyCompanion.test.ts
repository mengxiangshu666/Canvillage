// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from "vitest";

import {
  resolveCompanionViewportPosition,
  resolveCompanionTapIntent,
  shouldRenderCompanion,
} from "./MyBuddyCompanion";

describe("MyBuddyCompanion agent entry intent", () => {
  it("uses the current companion body as the Agent entry when wired", () => {
    expect(resolveCompanionTapIntent({ hasAgentEntry: true })).toBe("activate-agent");
  });

  it("keeps the pet state preview available outside Agent-entry mode", () => {
    expect(resolveCompanionTapIntent({ hasAgentEntry: false })).toBe("cycle-pet");
  });

  it("lets advanced users preview a pet state with Shift or Alt even in Agent-entry mode", () => {
    expect(
      resolveCompanionTapIntent({ hasAgentEntry: true, alternateAction: true }),
    ).toBe("cycle-pet");
  });

  it("keeps the functional 搭子 entry visible when decorative companions are hidden", () => {
    expect(shouldRenderCompanion(true, true)).toBe(true);
    expect(shouldRenderCompanion(true, false)).toBe(false);
    expect(shouldRenderCompanion(false, false)).toBe(true);
  });

  it("uses a temporary Agent edge anchor without overwriting the saved canvas position", () => {
    expect(resolveCompanionViewportPosition({ agentActive: false, left: 240, top: 128 })).toEqual({
      left: 240,
      top: 128,
    });
    expect(resolveCompanionViewportPosition({ agentActive: true, left: 240, top: 128 })).toEqual({
      left: expect.stringContaining("--village-agent-companion-left"),
      top: expect.stringContaining("--village-agent-companion-top"),
    });
  });
});
