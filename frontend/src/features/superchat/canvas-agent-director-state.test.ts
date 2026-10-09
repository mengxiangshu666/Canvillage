import { describe, expect, it } from "vitest";
import type { ModelGatewayConfig } from "@/lib/queries/model-gateway";

import {
  buildCanvasAgentModelCatalog,
  buildCanvasReferenceManifest,
  buildCanvasDirectorState,
} from "./canvas-agent-director-state";

type DecodedAssetLineage = {
  ids: string[];
  canvasNodeCount: number;
  edges: Array<[number, number, string | null]>;
  rootIndexes: number[];
  leafIndexes: number[];
  mediaIndexes: number[];
  parentLinks: Array<[number, number]>;
  assetLinks: Array<[number, number]>;
};

function decodeAssetLineage(state: Record<string, unknown>): DecodedAssetLineage {
  const lineage = state.asset_lineage as {
    complete: boolean;
    pages: string[];
  };
  expect(lineage.complete).toBe(true);
  const payload = JSON.parse(lineage.pages.join("")) as {
    i: string[];
    n: number;
    e: Array<[number, number, string | null]>;
    r: number[];
    l: number[];
    m: number[];
    p: Array<[number, number]>;
    a: Array<[number, number]>;
  };
  return {
    ids: payload.i,
    canvasNodeCount: payload.n,
    edges: payload.e,
    rootIndexes: payload.r,
    leafIndexes: payload.l,
    mediaIndexes: payload.m,
    parentLinks: payload.p,
    assetLinks: payload.a,
  };
}

function idsAt(lineage: DecodedAssetLineage, indexes: readonly number[]): string[] {
  return indexes.map((index) => lineage.ids[index]);
}

describe("canvas Agent director state", () => {
  it("projects UI referenceOrder into a stable image-to-node manifest", () => {
    const manifest = buildCanvasReferenceManifest(
      [
        {
          id: "character-node",
          type: "imageGenNode",
          data: {
            displayName: "林默角色立绘",
            imageUrl: "/character.png",
            assetId: "character-primary",
            assetUri: "hogi://assets/character-primary",
            nodeRole: "character",
          },
        },
        {
          id: "scene-node",
          type: "uploadNode",
          data: {
            displayName: "地下室场景",
            previewImageUrl: "/basement.png",
            asset_id: "basement-master",
            output_role: "scene_master",
          },
        },
        {
          id: "shot-node",
          type: "videoNode",
          data: {
            displayName: "镜头 1",
            referenceOrder: ["scene-node", "character-node"],
          },
        },
      ],
      [
        { id: "edge-character", source: "character-node", target: "shot-node" },
        { id: "edge-scene", source: "scene-node", target: "shot-node" },
      ],
      "shot-node",
    );

    expect(manifest).toMatchObject({
      schema: "village.reference-manifest.v1",
      target_count: 1,
      truncated: false,
      binding_policy: {
        label_is_display_only: true,
        bind_by_node_id_or_asset_id: true,
        order_source: "referenceOrder_then_edge_order",
        ambiguous_match_requires_confirmation: true,
      },
    });
    expect(manifest.targets[0]).toMatchObject({
      target_node_id: "shot-node",
      target_display_name: "镜头 1",
    });
    expect(manifest.targets[0].references).toEqual([
      expect.objectContaining({
        label: "图片1",
        type_index: 1,
        node_id: "scene-node",
        asset_id: "basement-master",
        role: "scene",
        role_label: "场景空间锚点",
        order: 0,
        connected: true,
      }),
      expect.objectContaining({
        label: "图片2",
        type_index: 2,
        node_id: "character-node",
        asset_id: "character-primary",
        node_uri: "canvas://default/nodes/character-node",
        asset_uri: "hogi://assets/character-primary",
        role: "identity",
        role_label: "角色身份锚点",
        order: 1,
        connected: true,
      }),
    ]);
  });

  it("keeps the manifest bounded and prioritizes the selected video target", () => {
    const nodes = Array.from({ length: 26 }, (_, index) => ({
      id: `video-${index}`,
      type: "videoNode",
      data: { displayName: `镜头 ${index}` },
    }));
    const manifest = buildCanvasReferenceManifest(nodes, [], "video-25");

    expect(manifest.target_count).toBe(26);
    expect(manifest.truncated).toBe(true);
    expect(manifest.targets).toHaveLength(24);
    expect(manifest.targets[0].target_node_id).toBe("video-25");
  });

  it("uses snake_case reference order and committed image slots", () => {
    const state = buildCanvasDirectorState(
      [
        { id: "img-a", type: "imageGenNode", data: { committed_slot_url: "/a.png" } },
        { id: "img-b", type: "imageGenNode", data: { imageUrl: "/b.png" } },
        { id: "shot", type: "videoNode", data: { reference_order: ["img-b", "img-a"] } },
      ],
      [
        { id: "edge-a", source: "img-a", target: "shot" },
        { id: "edge-b", source: "img-b", target: "shot" },
      ],
      "shot",
    );

    const manifest = state.reference_manifest as {
      targets: Array<{ references: Array<{ node_id: string }> }>;
    };
    expect(manifest.targets[0].references.map((item) => item.node_id)).toEqual([
      "img-b",
      "img-a",
    ]);
  });

  it("exposes only configured model capabilities without endpoint credentials", () => {
    const catalog = buildCanvasAgentModelCatalog({
      mode: "unified",
      effective: { source: "local", baseUrl: "", apiKeyPreview: "", configured: false },
      unified: {
        source: "local",
        baseUrl: "",
        apiKeyPreview: "",
        configured: false,
        environment: { baseUrl: "", apiKeyPreview: "", configured: false },
      },
      official: {
        source: "local",
        baseUrl: "",
        apiKeyPreview: "",
        configured: false,
        environment: { baseUrl: "", apiKeyPreview: "", configured: false },
      },
      custom: {
        baseUrl: "",
        apiKeyPreview: "",
        configured: false,
        adminBaseUrl: "",
        tokenName: "",
        tokenId: "",
      },
      directModels: {
        image: [{
          id: "image-a",
          label: "主生图",
          modelId: "gpt-image-2",
          baseUrl: "https://secret.example/v1",
          enabled: true,
          isDefault: true,
          configured: true,
          apiKeyPreview: "sk-...",
          protocol: "openai-images",
          runtimeReady: true,
          supportedModes: ["textToImage", "imageToImage"],
          useCase: "已识别：文生图、图生图",
          parameterDefaults: { resolution: "2K" },
        }],
      },
      directVideoModels: [{
        id: "video-a",
        label: "主视频",
        modelId: "jimeng-seedance-2.0-fast",
        baseUrl: "https://video-secret.example/v1",
        enabled: true,
        isDefault: true,
        configured: true,
        apiKeyPreview: "sk-...",
        protocol: "openai-video",
        runtimeReady: true,
        supportedModes: ["textToVideo", "imageToVideo"],
        referenceLimits: { imageToVideo: { image: 1, video: 0, audio: 0 } },
        parameterDefaults: { resolution: "720p", durationSeconds: 5 },
      }],
    });

    expect(catalog.models).toEqual([
      expect.objectContaining({
        id: "direct/image-a",
        kind: "image",
        is_default: true,
        supported_modes: ["textToImage", "imageToImage"],
      }),
      expect.objectContaining({
        id: "direct/video-a",
        kind: "video",
        is_default: true,
        supported_modes: ["textToVideo", "imageToVideo"],
      }),
    ]);
    expect(catalog.total_models).toBe(2);
    expect(catalog.truncated).toBe(false);
    expect(JSON.stringify(catalog)).not.toContain("secret.example");
    expect(JSON.stringify(catalog)).not.toContain("apiKeyPreview");
  });

  it("projects bounded topology, continuity anchors and asset lineage", () => {
    const state = buildCanvasDirectorState(
      [
        { id: "char-1", type: "characterPortraitNode", data: { displayName: "女主身份锚点", imageUrl: "/a.png", asset_id: "character-a", assetUri: "hogi://assets/character-a" } },
        { id: "scene-1", type: "sceneWorldNode", data: { displayName: "雨夜街道" } },
        { id: "shot-1", type: "videoNode", parentId: "group-1", data: { displayName: "镜头1", model: "direct/video-a", generationMode: "imageToVideo", videoUrl: "/shot.mp4" } },
        { id: "deliver-1", type: "videoComposeNode", data: { displayName: "成片合成", outputVideoUrl: "/film.mp4" } },
      ],
      [
        { id: "e1", source: "char-1", target: "shot-1" },
        { id: "e2", source: "scene-1", target: "shot-1" },
        { id: "e3", source: "shot-1", target: "deliver-1" },
      ],
      "shot-1",
      "style-a",
      "canvas-a",
    );

    expect(state).toMatchObject({
      schema: "village.director-state.v1",
      selected_node_id: "shot-1",
      project_style_id: "style-a",
      total_nodes: 4,
      total_edges: 3,
      continuity: {
        identity_anchors: ["char-1"],
        scene_anchors: ["scene-1"],
        shot_nodes: ["shot-1"],
        delivery_nodes: ["deliver-1"],
      },
      asset_lineage: {
        schema: "village.asset-lineage.v2",
        encoding: "indexed-json-pages-v1",
        complete: true,
        total_canvas_nodes: 4,
        total_edges: 3,
      },
      identity_policy: {
        node_uri: "canvas://{canvas_id}/nodes/{node_id}",
        label_is_display_only: true,
        bind_by: ["node_id", "asset_id", "asset_uri", "node_uri"],
      },
    });
    expect(state.nodes).toEqual([
      expect.objectContaining({
        id: "char-1",
        node_uri: "canvas://canvas-a/nodes/char-1",
        parent_id: null,
        asset_id: "character-a",
        asset_uri: "hogi://assets/character-a",
      }),
      expect.objectContaining({ id: "scene-1", parent_id: null, asset_id: null }),
      expect.objectContaining({ id: "shot-1", parent_id: "group-1", asset_id: null }),
      expect.objectContaining({ id: "deliver-1", parent_id: null, asset_id: null }),
    ]);
    expect(state.edges).toEqual([
      { id: "e1", source: "char-1", target: "shot-1" },
      { id: "e2", source: "scene-1", target: "shot-1" },
      { id: "e3", source: "shot-1", target: "deliver-1" },
    ]);
    const lineage = decodeAssetLineage(state);
    expect(lineage.ids.slice(0, lineage.canvasNodeCount)).toEqual([
      "char-1",
      "scene-1",
      "shot-1",
      "deliver-1",
    ]);
    expect(idsAt(lineage, lineage.rootIndexes)).toEqual(["char-1", "scene-1"]);
    expect(idsAt(lineage, lineage.leafIndexes)).toEqual(["deliver-1"]);
    expect(idsAt(lineage, lineage.mediaIndexes)).toEqual(["char-1", "shot-1", "deliver-1"]);
    expect(lineage.edges.map(([source, target, edgeId]) => [
      lineage.ids[source],
      lineage.ids[target],
      edgeId,
    ])).toEqual([
      ["char-1", "shot-1", "e1"],
      ["scene-1", "shot-1", "e2"],
      ["shot-1", "deliver-1", "e3"],
    ]);
  });

  it("treats an isolated node as both a lineage root and leaf", () => {
    const state = buildCanvasDirectorState(
      [{ id: "isolated", type: "imageGenNode", data: { imageUrl: "/still.png" } }],
      [],
    );

    const lineage = decodeAssetLineage(state);
    expect(idsAt(lineage, lineage.rootIndexes)).toEqual(["isolated"]);
    expect(idsAt(lineage, lineage.leafIndexes)).toEqual(["isolated"]);
    expect(idsAt(lineage, lineage.mediaIndexes)).toEqual(["isolated"]);
    expect(lineage.edges).toEqual([]);
  });

  it("computes lineage from projected edges beyond the top-level edge limit", () => {
    const state = buildCanvasDirectorState(
      [
        { id: "source", type: "imageGenNode" },
        { id: "middle", type: "videoNode" },
        { id: "sink", type: "videoComposeNode" },
      ],
      [
        ...Array.from({ length: 160 }, (_, index) => ({
          id: `duplicate-${index}`,
          source: "source",
          target: "middle",
        })),
        { id: "decisive-after-limit", source: "middle", target: "sink" },
      ],
    );

    expect(state.edges).toHaveLength(160);
    expect(state.edges).not.toContainEqual({
      id: "decisive-after-limit",
      source: "middle",
      target: "sink",
    });
    const lineage = decodeAssetLineage(state);
    expect(idsAt(lineage, lineage.rootIndexes)).toEqual(["source"]);
    expect(idsAt(lineage, lineage.leafIndexes)).toEqual(["sink"]);
    expect(lineage.mediaIndexes).toEqual([]);
    expect(lineage.edges).toHaveLength(161);
    expect(lineage.edges[lineage.edges.length - 1]).toEqual([1, 2, "decisive-after-limit"]);
  });

  it("bounds large model catalogs before they enter the Agent prompt", () => {
    const config = {
      directModels: {
        image: Array.from({ length: 55 }, (_, index) => ({
          id: `image-${index}`,
          label: `生图 ${index}`,
          modelId: `image-model-${index}`,
          baseUrl: "https://example.test/v1",
          enabled: true,
          isDefault: index === 0,
          configured: true,
          apiKeyPreview: "masked",
          protocol: "openai-images",
          runtimeReady: true,
          supportedModes: ["textToImage"],
        })),
      },
    } as unknown as ModelGatewayConfig;

    const catalog = buildCanvasAgentModelCatalog(config);
    expect(catalog.total_models).toBe(55);
    expect(catalog.truncated).toBe(true);
    expect(catalog.models).toHaveLength(48);

    const directorState = buildCanvasDirectorState(
      Array.from({ length: 81 }, (_, index) => ({
        id: `node-${index}`,
        type: "imageGenNode",
        ...(index === 80
          ? { parentId: "group-outside-summary", data: { asset_id: "asset-outside-summary" } }
          : {}),
      })),
      [
        { id: "outside-in", source: "node-80", target: "node-0" },
        { id: "outside-out", source: "node-1", target: "node-80" },
        ...Array.from({ length: 159 }, (_, index) => ({
          id: `edge-${index}`,
          source: `node-${index % 80}`,
          target: `node-${(index + 1) % 80}`,
        })),
      ],
    );
    expect(directorState.truncated).toBe(true);
    expect(directorState.nodes).toHaveLength(80);
    expect(directorState.edges).toHaveLength(160);
    const assetLineage = directorState.asset_lineage as {
      page_count: number;
      page_max_bytes: number;
      pages: string[];
    };
    expect(assetLineage.page_count).toBe(assetLineage.pages.length);
    expect(assetLineage.pages.length).toBeGreaterThan(1);
    for (const page of assetLineage.pages) {
      expect(new TextEncoder().encode(JSON.stringify(page)).byteLength)
        .toBeLessThanOrEqual(assetLineage.page_max_bytes);
    }
    const lineage = decodeAssetLineage(directorState);
    expect(lineage.canvasNodeCount).toBe(81);
    expect(lineage.ids.slice(0, lineage.canvasNodeCount)).toContain("node-80");
    expect(lineage.edges).toHaveLength(161);
    expect(lineage.parentLinks.map(([node, parent]) => [
      lineage.ids[node],
      lineage.ids[parent],
    ])).toContainEqual(["node-80", "group-outside-summary"]);
    expect(lineage.assetLinks.map(([node, asset]) => [
      lineage.ids[node],
      lineage.ids[asset],
    ])).toContainEqual(["node-80", "asset-outside-summary"]);
    expect(lineage.edges.slice(0, 2).map(([source, target, edgeId]) => [
      lineage.ids[source],
      lineage.ids[target],
      edgeId,
    ])).toEqual([
      ["node-80", "node-0", "outside-in"],
      ["node-1", "node-80", "outside-out"],
    ]);
    expect(idsAt(lineage, lineage.rootIndexes)).toEqual([]);
    expect(idsAt(lineage, lineage.leafIndexes)).toEqual([]);
  });
});
