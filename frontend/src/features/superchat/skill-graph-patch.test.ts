import { afterEach, describe, expect, it } from "vitest";

import { CANVAS_NODE_TYPES } from "@/features/canvas/domain/canvasNodes";
import { applyStructureEnvelopeToStore } from "@/features/freezone/FreezoneShell";
import { useCanvasStore } from "@/stores/canvasStore";
import { graphPatchToCanvasEnvelope } from "./skill-graph-patch";

function resetStore(): void {
  useCanvasStore.setState({
    nodes: [],
    edges: [],
    history: { past: [], future: [] },
    selectedNodeId: null,
    currentViewport: { x: 0, y: 0, zoom: 1 },
    canvasViewportSize: { width: 1200, height: 800 },
  });
}

describe("Skill graph patch bridge", () => {
  afterEach(resetStore);

  it("uses the same canvas executor for Skill nodes and role edges", () => {
    resetStore();
    const beatId = useCanvasStore.getState().addNode(
      CANVAS_NODE_TYPES.beatContext,
      { x: 0, y: 0 },
      { content: "Beat context" },
    );
    const envelope = graphPatchToCanvasEnvelope({
      schema_version: "graph_patch.v1",
      requires_apply: false,
      operations: [
        {
          op: "add_node",
          node: {
            id: "skill-sketch",
            type: "skillNode",
            position: { x: 500, y: 100 },
            data: {
              skill_id: "freezone.sketch_from_context",
              displayName: "生成草图",
            },
          },
        },
        {
          op: "add_edge",
          edge: {
            id: "edge-beat-sketch",
            source: beatId,
            target: "skill-sketch",
            targetHandle: "beat_context",
            data: { edgeKind: "role_binding", role: "beat_context" },
          },
        },
      ],
    }, {
      projectId: "project-a",
      canvasId: "canvas-a",
      commandId: "skill-run-a:graph:0",
    });

    expect(envelope).not.toBeNull();
    expect(envelope?.commands).toEqual([
      expect.objectContaining({
        type: "create_canvas_node",
        created_node_id: "skill-sketch",
        connect_selected: false,
      }),
      expect.objectContaining({
        type: "connect_nodes",
        created_edge_id: "edge-beat-sketch",
        source: beatId,
        target: "skill-sketch",
        target_handle: "beat_context",
        edge_data: expect.objectContaining({ role: "beat_context" }),
      }),
    ]);
    expect(applyStructureEnvelopeToStore(envelope!)).toBe(2);
    expect(useCanvasStore.getState().nodes.find((node) => node.id === "skill-sketch")?.data)
      .toMatchObject({
        skill_id: "freezone.sketch_from_context",
        displayName: "生成草图",
      });
    expect(useCanvasStore.getState().edges).toContainEqual(
      expect.objectContaining({
        id: "edge-beat-sketch",
        source: beatId,
        target: "skill-sketch",
        targetHandle: "beat_context",
        data: expect.objectContaining({ role: "beat_context" }),
      }),
    );
  });
});
