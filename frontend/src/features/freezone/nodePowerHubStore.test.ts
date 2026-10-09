import { beforeEach, describe, expect, it } from "vitest";

import { useNodePowerHubStore } from "./nodePowerHubStore";

describe("nodePowerHubStore", () => {
  beforeEach(() => useNodePowerHubStore.getState().close());

  it("keeps exactly one node tool open", () => {
    useNodePowerHubStore.getState().open("expression", "node-1");
    expect(useNodePowerHubStore.getState()).toMatchObject({
      activeTool: "expression",
      nodeId: "node-1",
    });

    // 切换到另一个节点时，活动工具连同节点一起换掉，不留上一节点的残留。
    useNodePowerHubStore.getState().open("expression", "node-2");
    expect(useNodePowerHubStore.getState()).toMatchObject({
      activeTool: "expression",
      nodeId: "node-2",
    });
  });

  it("releases all canvas space on close", () => {
    useNodePowerHubStore.getState().open("expression", "node-2");
    useNodePowerHubStore.getState().close();
    expect(useNodePowerHubStore.getState()).toMatchObject({
      activeTool: null,
      nodeId: null,
    });
  });
});
