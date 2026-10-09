// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it, vi } from "vitest";

import {
  buildCanvasFastCommandPlan,
  resolveLiveSelectedNodeId,
} from "./canvas-fast-command";

describe("buildCanvasFastCommandPlan", () => {
  const context = {
    projectId: "project-a",
    canvasId: "canvas-a",
    selectedNodeId: "node-a",
    selectedNodeLabel: "镜头 A",
    selectedNodePosition: { x: 100, y: 200 },
  };

  it("parses deterministic selected-node actions", () => {
    vi.spyOn(Date, "now").mockReturnValue(1000);
    expect(buildCanvasFastCommandPlan("删除当前节点", context)?.envelope.commands).toEqual([
      { type: "delete_node", node_id: "node-a" },
    ]);
    expect(buildCanvasFastCommandPlan("把这个节点改名为 开场镜头", context)?.envelope.commands).toEqual([
      { type: "update_node_label", node_id: "node-a", display_name: "开场镜头" },
    ]);
    expect(buildCanvasFastCommandPlan("把当前节点向右移动", context)?.envelope.commands).toEqual([
      { type: "move_node", node_id: "node-a", x: 420, y: 200 },
    ]);
    expect(buildCanvasFastCommandPlan("定位当前节点", context)).toMatchObject({
      reply: "已定位「镜头 A」",
      envelope: {
        commands: [{ type: "focus_node", node_id: "node-a" }],
      },
    });
    vi.restoreAllMocks();
  });

  it("leaves ambiguous or context-free requests to the full Agent", () => {
    expect(buildCanvasFastCommandPlan("帮我做一个很牛逼的分镜", context)).toBeNull();
    expect(buildCanvasFastCommandPlan("删除当前节点", {
      ...context,
      selectedNodeId: null,
    })).toBeNull();
  });

  it("creates explicit text and note nodes locally without a selected node", () => {
    const noSelection = { ...context, selectedNodeId: null };

    expect(buildCanvasFastCommandPlan("在画布中间创建一个文本节点", noSelection)?.envelope.commands).toEqual([{
      type: "create_canvas_node",
      node_type: "textAnnotationNode",
      display_name: "文本",
      text: "",
      connect_selected: false,
      placement: { anchor: "viewport_center", layout: "grid" },
    }]);
    expect(buildCanvasFastCommandPlan(
      "创建一个节点，标题“测试”，正文：“这里是正文”",
      noSelection,
    )?.envelope.commands).toEqual([{
      type: "create_canvas_node",
      node_type: "textAnnotationNode",
      display_name: "测试",
      text: "这里是正文",
      connect_selected: false,
      placement: { anchor: "viewport_center", layout: "grid" },
    }]);
    expect(buildCanvasFastCommandPlan(
      "创建一个文本节点，标题“无冒号正文”，正文“内容也要落盘”",
      noSelection,
    )?.envelope.commands).toEqual([{
      type: "create_canvas_node",
      node_type: "textAnnotationNode",
      display_name: "无冒号正文",
      text: "内容也要落盘",
      connect_selected: false,
      placement: { anchor: "viewport_center", layout: "grid" },
    }]);
    expect(buildCanvasFastCommandPlan("在当前视口中心加一个备注节点", noSelection)?.reply).toBe(
      "已创建「备注」文本节点",
    );
  });

  it("creates image, video and audio nodes with real node parameters", () => {
    const noSelection = { ...context, selectedNodeId: null };

    expect(buildCanvasFastCommandPlan(
      "创建一个图片节点，标题“雨夜街道”，提示词“霓虹灯下的雨夜街道”，比例9:16，尺寸2K，模型“image-pro”",
      noSelection,
    )?.envelope.commands).toEqual([{
      type: "create_image_prompt_node",
      prompt: "霓虹灯下的雨夜街道",
      display_name: "雨夜街道",
      aspect_ratio: "9:16",
      image_size: "2K",
      model: "image-pro",
      connect_selected: false,
      placement: { anchor: "viewport_center", layout: "grid" },
    }]);

    expect(buildCanvasFastCommandPlan(
      "新建一个视频节点，标题“开场”，提示词“镜头缓慢推进”，比例16:9，时长8秒，画质1080p，打开声音",
      noSelection,
    )?.envelope.commands).toEqual([{
      type: "create_video_prompt_node",
      prompt: "镜头缓慢推进",
      display_name: "开场",
      aspect_ratio: "16:9",
      video_quality: "1080p",
      duration_sec: 8,
      generate_audio: true,
      connect_selected: false,
      placement: { anchor: "viewport_center", layout: "grid" },
    }]);

    expect(buildCanvasFastCommandPlan(
      "添加一个音频节点，标题“旁白”，文本“夜幕降临”，模型“tts-pro”",
      noSelection,
    )?.envelope.commands).toEqual([{
      type: "create_canvas_node",
      node_type: "audioNode",
      display_name: "旁白",
      text: "夜幕降临",
      model: "tts-pro",
      connect_selected: false,
      placement: { anchor: "viewport_center", layout: "grid" },
    }]);
  });

  it("creates empty media nodes without inventing prompts", () => {
    const noSelection = { ...context, selectedNodeId: null };
    expect(buildCanvasFastCommandPlan("创建一个图片节点", noSelection)?.envelope.commands).toEqual([{
      type: "create_canvas_node",
      node_type: "imageGenNode",
      display_name: "图片",
      connect_selected: false,
      placement: { anchor: "viewport_center", layout: "grid" },
    }]);
    expect(buildCanvasFastCommandPlan("创建一个视频节点", noSelection)?.envelope.commands).toEqual([{
      type: "create_canvas_node",
      node_type: "videoNode",
      display_name: "视频",
      connect_selected: false,
      placement: { anchor: "viewport_center", layout: "grid" },
    }]);
  });

  it("creates every explicitly listed media node from natural Chinese instructions", () => {
    const noSelection = { ...context, selectedNodeId: null };
    const combined = buildCanvasFastCommandPlan(
      "搭建一个新节点。就是测试用的图片一个，视频一个。",
      noSelection,
    );

    expect(combined?.reply).toBe("已创建图片节点和视频节点");
    expect(combined?.envelope.commands).toEqual([
      {
        type: "create_canvas_node",
        node_type: "imageGenNode",
        display_name: "图片",
        connect_selected: false,
        placement: { anchor: "viewport_center", layout: "grid" },
      },
      {
        type: "create_canvas_node",
        node_type: "videoNode",
        display_name: "视频",
        connect_selected: false,
        placement: { anchor: "viewport_center", layout: "grid" },
      },
    ]);
    expect(buildCanvasFastCommandPlan(
      "我新建一个节点。图片节点就可以。",
      noSelection,
    )?.envelope.commands).toHaveLength(1);
    expect(buildCanvasFastCommandPlan(
      "不要搭建图片节点和视频节点",
      noSelection,
    )).toBeNull();
  });

  it("updates the selected node prompt or text body locally", () => {
    expect(buildCanvasFastCommandPlan(
      "把当前节点的提示词改为“电影感雨夜，慢速推进”",
      context,
    )?.envelope.commands).toEqual([{
      type: "update_node_prompt",
      node_id: "node-a",
      prompt: "电影感雨夜，慢速推进",
    }]);

    expect(buildCanvasFastCommandPlan(
      "把这个节点正文改成“修改后的正文”",
      { ...context, selectedNodeType: "textAnnotationNode" },
    )?.envelope.commands).toEqual([{
      type: "update_node_data",
      node_id: "node-a",
      node_data: { content: "修改后的正文" },
    }]);
  });

  it("connects and disconnects selected and pinned nodes deterministically", () => {
    const connected = {
      ...context,
      pinnedNodeIds: ["node-b"],
      nodes: [
        { id: "node-a", label: "镜头 A", position: { x: 100, y: 200 } },
        { id: "node-b", label: "镜头 B", position: { x: 500, y: 200 } },
      ],
    };
    expect(buildCanvasFastCommandPlan("把当前节点连接到引用节点", connected)?.envelope.commands).toEqual([{
      type: "connect_nodes",
      source: "node-a",
      target: "node-b",
    }]);
    expect(buildCanvasFastCommandPlan("把引用节点连接到当前节点", connected)?.envelope.commands).toEqual([{
      type: "connect_nodes",
      source: "node-b",
      target: "node-a",
    }]);
    expect(buildCanvasFastCommandPlan("断开当前节点和引用节点的连线", connected)?.envelope.commands).toEqual([{
      type: "remove_edge",
      source: "node-a",
      target: "node-b",
    }]);
  });

  it("moves the selected node by an explicit distance or absolute position", () => {
    expect(buildCanvasFastCommandPlan("把当前节点向右移动200像素", context)?.envelope.commands).toEqual([{
      type: "move_node",
      node_id: "node-a",
      x: 300,
      y: 200,
    }]);
    expect(buildCanvasFastCommandPlan("把当前节点移动到 x=640, y=360", context)?.envelope.commands).toEqual([{
      type: "move_node",
      node_id: "node-a",
      x: 640,
      y: 360,
    }]);
  });
});

describe("resolveLiveSelectedNodeId", () => {
  it("prefers the live store id over a stale React Flow selection flag", () => {
    expect(resolveLiveSelectedNodeId(
      [{ id: "old", selected: false }, { id: "new", selected: true }],
      "old",
    )).toBe("old");
  });

  it("falls back to the React Flow flag when the store id is missing", () => {
    expect(resolveLiveSelectedNodeId([{ id: "node-a" }], "node-a")).toBe("node-a");
    expect(resolveLiveSelectedNodeId(
      [{ id: "node-a", selected: false }, { id: "node-b", selected: true }],
      "missing-node",
    )).toBe("node-b");
    expect(resolveLiveSelectedNodeId([], null)).toBeNull();
  });
});
