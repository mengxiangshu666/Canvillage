import { afterEach, describe, expect, it } from "vitest";

import { CANVAS_NODE_TYPES } from "@/features/canvas/domain/canvasNodes";
import { useCanvasStore } from "@/stores/canvasStore";
import { currentCanvasAgentContext } from "./canvas-agent-context";

describe("canvas Agent compact context", () => {
  afterEach(() => {
    useCanvasStore.setState({
      nodes: [],
      edges: [],
      selectedNodeId: null,
      currentViewport: { x: 0, y: 0, zoom: 1 },
      canvasViewportSize: { width: 0, height: 0 },
    });
  });

  it("includes authoritative revision, Style DNA and selected model capability", () => {
    useCanvasStore.setState({ nodes: [], edges: [], selectedNodeId: null });
    const id = useCanvasStore.getState().addNode(
      CANVAS_NODE_TYPES.imageGen,
      { x: 0, y: 0 },
      {
        prompt: "portrait",
        model: "gpt-image-2",
        generationMode: "image_reference",
        capabilityId: "cap-image-reference",
        styleTemplateId: "style-template-a",
      },
    );
    useCanvasStore.getState().setSelectedNode(id);

    expect(JSON.parse(currentCanvasAgentContext("canvas-a", "project-a", 14, "style-dna-a")))
      .toMatchObject({
        project_id: "project-a",
        canvas_id: "canvas-a",
        revision: 14,
        project_style_id: "style-dna-a",
        director_state: {
          schema: "village.director-state.v1",
          selected_node_id: id,
          total_nodes: 1,
          total_edges: 0,
          continuity: {
            shot_nodes: [id],
          },
        },
        model_catalog: {
          schema: "village.direct-model-catalog.v1",
          total_models: 0,
          truncated: false,
          models: [],
          routing_policy: {
            configured_models_only: true,
            capability_match_required: true,
          },
        },
        selected_node: {
          id,
          model_id: "gpt-image-2",
          generation_mode: "image_reference",
          capability_id: "cap-image-reference",
          style_template_id: "style-template-a",
        },
      });
  });

  it("exposes stable node identity while filtering media URLs", () => {
    const id = useCanvasStore.getState().addNode(
      CANVAS_NODE_TYPES.imageGen,
      { x: 0, y: 0 },
      {
        displayName: "主角身份锚点",
        assetId: "character-1",
        assetUri: "hogi://assets/character-1",
        imageUrl: "https://cdn.example/character.png",
        nodeRole: "character",
      },
    );
    useCanvasStore.getState().setSelectedNode(id);

    const context = JSON.parse(currentCanvasAgentContext("canvas/a", "project-a", 15));
    expect(context.identity_policy).toEqual({
      node_uri: "canvas://{canvas_id}/nodes/{node_id}",
      label_is_display_only: true,
      bind_by: ["node_id", "asset_id", "asset_uri", "node_uri"],
      parent_id_is_group_context: true,
      do_not_guess_from_filename: true,
    });
    expect(context.canvas_outline[0]).toMatchObject({
      id,
      node_uri: `canvas://canvas%2Fa/nodes/${encodeURIComponent(id)}`,
      asset_id: "character-1",
      asset_uri: "hogi://assets/character-1",
      parent_id: null,
    });
    expect(context.selected_node).toMatchObject({
      node_uri: `canvas://canvas%2Fa/nodes/${encodeURIComponent(id)}`,
      asset_uri: "hogi://assets/character-1",
    });
    expect(context.canvas_outline[0]).not.toHaveProperty("imageUrl");
    expect(context.director_state.identity_policy.bind_by).toEqual([
      "node_id",
      "asset_id",
      "asset_uri",
      "node_uri",
    ]);
    expect(context.director_state.nodes[0]).toMatchObject({
      node_uri: `canvas://canvas%2Fa/nodes/${encodeURIComponent(id)}`,
      asset_uri: "hogi://assets/character-1",
    });
  });

  it("exposes the same reference manifest at the top level and in director state", () => {
    const characterId = useCanvasStore.getState().addNode(
      CANVAS_NODE_TYPES.imageGen,
      { x: 0, y: 0 },
      {
        displayName: "主角角色图",
        imageUrl: "/character.png",
        assetId: "character-primary",
        nodeRole: "character",
      },
    );
    const videoId = useCanvasStore.getState().addNode(
      CANVAS_NODE_TYPES.video,
      { x: 360, y: 0 },
      { displayName: "镜头 1", referenceOrder: [characterId] },
    );
    useCanvasStore.getState().addEdge(characterId, videoId);
    useCanvasStore.getState().setSelectedNode(videoId);

    const context = JSON.parse(currentCanvasAgentContext("canvas-a", "project-a", 16));
    expect(context.reference_manifest).toEqual(context.director_state.reference_manifest);
    expect(context.reference_manifest.targets[0]).toMatchObject({
      target_node_id: videoId,
      references: [expect.objectContaining({
        label: "图片1",
        node_id: characterId,
        asset_id: "character-primary",
        role: "identity",
      })],
    });
  });

  it("includes the live XYFlow viewport without a canvas snapshot", () => {
    useCanvasStore.setState({
      currentViewport: { x: -200, y: -100, zoom: 2 },
      canvasViewportSize: { width: 1200, height: 800 },
    });

    expect(JSON.parse(currentCanvasAgentContext("canvas-a", "project-a", 14)))
      .toMatchObject({
        placement_contract: {
          preferred: { anchor: "viewport_center", layout: "grid" },
          explicit_xy_has_priority: true,
          snapshot_not_required_for_viewport_placement: true,
        },
        viewport_context: {
          source: "browser_xyflow_live",
          available: true,
          x: -200,
          y: -100,
          zoom: 2,
          width: 1200,
          height: 800,
          world_center: { x: 400, y: 250 },
          world_bounds: { min_x: 100, min_y: 50, max_x: 700, max_y: 450 },
        },
      });
  });

  it("prioritizes selected and visible nodes before offscreen canvas order", () => {
    const outsideId = useCanvasStore.getState().addNode(
      CANVAS_NODE_TYPES.textAnnotation,
      { x: 2_000, y: 2_000 },
      { displayName: "远处说明" },
    );
    for (let index = 0; index < 80; index += 1) {
      useCanvasStore.getState().addNode(
        CANVAS_NODE_TYPES.textAnnotation,
        { x: 3_000 + index * 20, y: 3_000 },
        { displayName: `远处节点 ${index + 1}` },
      );
    }
    const visibleId = useCanvasStore.getState().addNode(
      CANVAS_NODE_TYPES.imageGen,
      { x: 120, y: 80 },
      { displayName: "眼前图片" },
    );
    const selectedId = useCanvasStore.getState().addNode(
      CANVAS_NODE_TYPES.video,
      { x: 360, y: 160 },
      { displayName: "当前镜头" },
    );
    useCanvasStore.setState({
      selectedNodeId: selectedId,
      currentViewport: { x: 0, y: 0, zoom: 2 },
      canvasViewportSize: { width: 1_200, height: 800 },
    });

    const context = JSON.parse(currentCanvasAgentContext("canvas-a", "project-a", 15));

    expect(context.canvas_outline_policy).toBe("selected_then_viewport_then_canvas_order");
    expect(context.visible_node_count).toBe(2);
    expect(context.canvas_outline).toHaveLength(80);
    expect(context.outline_truncated.nodes).toBe(true);
    expect(context.canvas_outline.slice(0, 3).map((node: { id: string }) => node.id)).toEqual([
      selectedId,
      visibleId,
      outsideId,
    ]);
    expect(context.canvas_outline[0]).toMatchObject({
      id: selectedId,
      selected: true,
      in_viewport: true,
      position: { x: 360, y: 160 },
    });
    expect(context.canvas_outline[2]).toMatchObject({
      id: outsideId,
      selected: false,
      in_viewport: false,
    });
  });
});
