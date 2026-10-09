// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from "vitest";

import { CANVAS_NODE_TYPES, type CanvasNodeType } from "@/features/canvas/domain/canvasNodes";
import {
  canvasNodeDefinitions,
  DOWNSTREAM_SPAWN_WHITELIST,
  getAllowedDownstreamTargetTypes,
  getDownstreamSpawnTypes,
  getMenuNodeDefinitions,
  getUpstreamSpawnTypes,
  isManualConnectionAllowed,
  isUpstreamConnectionAllowed,
  UPSTREAM_SPAWN_WHITELIST,
} from "@/features/canvas/domain/nodeRegistry";

describe("canvas node registry", () => {
  it("creates standalone shot context nodes from the menu with local schema data", () => {
    const definition = canvasNodeDefinitions[CANVAS_NODE_TYPES.beatContext];
    const data = definition.createDefaultData() as Record<string, unknown>;

    expect(getMenuNodeDefinitions().map((item) => item.type)).toContain(
      CANVAS_NODE_TYPES.beatContext,
    );
    expect(definition.menuLabelKey).toBe("node.menu.beatContext");
    expect(data).toMatchObject({
      context_scope: "standalone",
      beat_context: {
        schema: "beat_context.v1",
        source: "standalone",
        title: "自定义镜头上下文",
        visual_description: "",
        narration_segment: "",
        scene_id: "",
        detected_identities: [],
        detected_props: [],
        sketch_colors: {},
        prop_marker_colors: {},
      },
      snapshot: {
        visualDescription: "",
        narrationSegment: "",
        sceneId: "",
        detectedIdentities: [],
        detectedProps: [],
        sketchColors: {},
        propMarkerColors: {},
      },
      syncStatus: "fresh",
    });
    expect(data).not.toHaveProperty("mainline_context");
  });
});

const IMAGE_NODE_TYPES = [
  CANVAS_NODE_TYPES.imageGen,
  CANVAS_NODE_TYPES.imageEdit,
  CANVAS_NODE_TYPES.upload,
  CANVAS_NODE_TYPES.exportImage,
] as const;

describe("建边白名单", () => {
  it("音频节点连不到图片节点上，且下游只有视频与视频合成", () => {
    for (const imageType of IMAGE_NODE_TYPES) {
      expect(isUpstreamConnectionAllowed(CANVAS_NODE_TYPES.audio, imageType)).toBe(false);
    }

    const allTypes = Object.keys(canvasNodeDefinitions) as CanvasNodeType[];
    const reachable = allTypes.filter((type) =>
      isUpstreamConnectionAllowed(CANVAS_NODE_TYPES.audio, type),
    );
    expect([...reachable].sort()).toEqual(
      [CANVAS_NODE_TYPES.video, CANVAS_NODE_TYPES.videoCompose].sort(),
    );
  });

  it("保留系统创建的视频到音频溯源边，但不允许用户手工建立", () => {
    expect(isUpstreamConnectionAllowed(CANVAS_NODE_TYPES.video, CANVAS_NODE_TYPES.audio)).toBe(
      true,
    );
    expect(isManualConnectionAllowed(CANVAS_NODE_TYPES.video, CANVAS_NODE_TYPES.audio)).toBe(
      false,
    );
    expect(isUpstreamConnectionAllowed(CANVAS_NODE_TYPES.imageGen, CANVAS_NODE_TYPES.audio)).toBe(
      false,
    );
  });

  it("新下游白名单不误伤既有可用连线", () => {
    expect(
      isUpstreamConnectionAllowed(CANVAS_NODE_TYPES.textAnnotation, CANVAS_NODE_TYPES.audio),
    ).toBe(true);
    expect(getAllowedDownstreamTargetTypes(CANVAS_NODE_TYPES.imageGen)).toBeNull();
    expect(
      isUpstreamConnectionAllowed(CANVAS_NODE_TYPES.imageGen, CANVAS_NODE_TYPES.video),
    ).toBe(true);
  });

  it("菜单的产品白名单不与领域建边规则冲突", () => {
    const conflicts: string[] = [];
    for (const [source, targets] of Object.entries(DOWNSTREAM_SPAWN_WHITELIST)) {
      for (const target of targets ?? []) {
        if (!isUpstreamConnectionAllowed(source as CanvasNodeType, target)) {
          conflicts.push(`下游白名单 ${source} -> ${target}`);
        }
      }
    }
    for (const [target, sources] of Object.entries(UPSTREAM_SPAWN_WHITELIST)) {
      for (const source of sources ?? []) {
        if (!isUpstreamConnectionAllowed(source, target as CanvasNodeType)) {
          conflicts.push(`上游白名单 ${source} -> ${target}`);
        }
      }
    }

    expect(conflicts).toEqual([]);
  });
});

describe("连线菜单候选", () => {
  it("不向用户暴露系统专用的视频到音频溯源边", () => {
    expect(getUpstreamSpawnTypes(CANVAS_NODE_TYPES.audio)).toEqual([
      CANVAS_NODE_TYPES.textAnnotation,
    ]);
    expect(getDownstreamSpawnTypes(CANVAS_NODE_TYPES.audio)).toEqual([
      CANVAS_NODE_TYPES.video,
      CANVAS_NODE_TYPES.videoCompose,
    ]);
  });

  it("视频节点保持既有菜单，图片节点接入分镜关键帧", () => {
    expect(getUpstreamSpawnTypes(CANVAS_NODE_TYPES.video)).toEqual([
      CANVAS_NODE_TYPES.textAnnotation,
      CANVAS_NODE_TYPES.imageGen,
      CANVAS_NODE_TYPES.audio,
    ]);
    expect(getUpstreamSpawnTypes(CANVAS_NODE_TYPES.imageGen)).toEqual([
      CANVAS_NODE_TYPES.textAnnotation,
      CANVAS_NODE_TYPES.script,
      CANVAS_NODE_TYPES.upload,
      CANVAS_NODE_TYPES.storyboardGen,
    ]);
    expect(getDownstreamSpawnTypes(CANVAS_NODE_TYPES.imageGen)).not.toContain(
      CANVAS_NODE_TYPES.audio,
    );
  });
});
