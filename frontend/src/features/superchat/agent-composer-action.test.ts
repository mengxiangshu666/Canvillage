import { describe, expect, it } from "vitest";

import {
  agentComposerCanSteer,
  agentComposerPrimaryAction,
  agentComposerPrimaryActionDisabled,
} from "./agent-composer-action";

describe("agent composer primary action", () => {
  it("keeps send available as a steer action while the canvas Agent is busy", () => {
    const action = agentComposerPrimaryAction({ busy: true, canvasProduct: true });

    expect(action).toBe("steer");
    expect(agentComposerPrimaryActionDisabled(action, true)).toBe(false);
    expect(agentComposerPrimaryActionDisabled(action, false)).toBe(true);
  });

  it("preserves the legacy stop action outside the canvas product", () => {
    const action = agentComposerPrimaryAction({ busy: true, canvasProduct: false });

    expect(action).toBe("stop");
    expect(agentComposerPrimaryActionDisabled(action, false)).toBe(false);
  });

  it("uses the normal send action while idle", () => {
    expect(agentComposerPrimaryAction({ busy: false, canvasProduct: true })).toBe("send");
  });

  it("steers plain text immediately but queues referenced media and nodes", () => {
    expect(agentComposerCanSteer({ text: "改成夜景", attachmentCount: 0, pinnedNodeCount: 0 })).toBe(true);
    expect(agentComposerCanSteer({ text: "参考这张图", attachmentCount: 1, pinnedNodeCount: 0 })).toBe(false);
    expect(agentComposerCanSteer({ text: "修改这个节点", attachmentCount: 0, pinnedNodeCount: 1 })).toBe(false);
  });
});
