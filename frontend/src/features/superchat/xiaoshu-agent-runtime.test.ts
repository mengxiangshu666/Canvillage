// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab

import { describe, expect, it } from "vitest";

import {
  routeXiaoshuRequest,
} from "./xiaoshu-agent-runtime";

const CONTEXT = {
  projectId: "project-a",
  canvasId: "canvas-a",
  selectedNodeId: "node-a",
  selectedNodeLabel: "主画面",
  selectedNodeType: "imageGenNode",
};

describe("Xiaoshu agent runtime", () => {
  it("routes structural canvas work through model collaboration", () => {
    const route = routeXiaoshuRequest({
      userText: "删除当前节点",
      canvasContext: CONTEXT,
    });

    expect(route).toEqual({
      kind: "model_collaboration",
      plan: null,
      requiresModelConnection: true,
    });
  });

  it("keeps creative collaboration on the model planner path", () => {
    const route = routeXiaoshuRequest({
      userText: "我们先对齐这支片子的情绪和节奏",
      canvasContext: CONTEXT,
    });

    expect(route).toEqual({
      kind: "model_collaboration",
      plan: null,
      requiresModelConnection: true,
    });
  });

  it("routes selected-node task controls without a chat connection", () => {
    const route = routeXiaoshuRequest({
      userText: "运行当前节点",
      canvasContext: CONTEXT,
    });

    expect(route).toEqual({
      kind: "direct_task_control",
      operation: "run_node",
      nodeId: "node-a",
      reply: "已启动当前节点",
      requiresModelConnection: false,
    });
  });

  it("does not consume attachment-bearing requests as local text commands", () => {
    const route = routeXiaoshuRequest({
      userText: "删除当前节点",
      canvasContext: CONTEXT,
      hasExplicitAttachments: true,
    });

    expect(route.kind).toBe("model_collaboration");
  });

  it("does not let prompt updates bypass semantic understanding", () => {
    const route = routeXiaoshuRequest({
      userText: "把当前节点提示词改为电影感雨夜街头",
      canvasContext: CONTEXT,
    });

    expect(route).toEqual({
      kind: "model_collaboration",
      plan: null,
      requiresModelConnection: true,
    });
  });
});
