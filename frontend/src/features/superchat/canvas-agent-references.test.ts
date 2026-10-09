// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { afterEach, describe, expect, it } from "vitest";

import {
  buildCanvasReferenceAttachments,
  hasExplicitUserImageAttachment,
  mergeReferenceAttachments,
} from "./canvas-agent-references";
import type { ChatAttachment } from "./types";
import { useCanvasStore } from "@/stores/canvasStore";

describe("canvas agent real references", () => {
  afterEach(() => {
    useCanvasStore.setState({ nodes: [] });
  });

  it("uses only explicitly pinned nodes, never the current selection", () => {
    useCanvasStore.setState({
      nodes: [
        {
          id: "selected",
          type: "imageGenNode",
          position: { x: 0, y: 0 },
          data: { imageUrl: "/selected.png" },
        },
        {
          id: "pinned",
          type: "imageGenNode",
          position: { x: 0, y: 0 },
          data: { imageUrl: "/pinned.png" },
        },
      ] as never,
    });

    const staleInput = {
      pinnedNodes: [{ id: "pinned", type: "imageGenNode", label: "固定节点" }],
      selectedNodeId: "selected",
    } as unknown as Parameters<typeof buildCanvasReferenceAttachments>[0];
    const attachments = buildCanvasReferenceAttachments(staleInput);

    expect(attachments.map((item) => item.nodeId)).toEqual(["pinned"]);
  });

  it("merges unique canvas and paste attachments", () => {
    const merged = mergeReferenceAttachments(
      [{ id: "a", url: "/static/projects/p/a.png", type: "canvas_image", kind: "image" }],
      [
        { id: "a", url: "/static/projects/p/a.png", type: "canvas_image", kind: "image" },
        { id: "b", content: "data:image/png;base64,xx", type: "image", kind: "image" },
      ],
    );
    expect(merged).toHaveLength(2);
    expect(merged.map((item) => item.id)).toEqual(["a", "b"]);
  });

  it("detects explicit paste image attachments", () => {
    const paste: ChatAttachment = {
      id: "paste-1",
      type: "image",
      kind: "image",
      mimeType: "image/png",
      content: "data:image/png;base64,xx",
      source: "paste",
    };
    expect(hasExplicitUserImageAttachment([paste])).toBe(true);
  });

  it("detects user image by content without source field", () => {
    const upload: ChatAttachment = {
      id: "up-1",
      type: "image",
      kind: "image",
      mimeType: "image/jpeg",
      content: "data:image/jpeg;base64,yy",
    };
    expect(hasExplicitUserImageAttachment([upload])).toBe(true);
  });

  it("does not treat canvas_image url refs as explicit user images", () => {
    const canvasRef: ChatAttachment = {
      id: "canvas-ref-1",
      type: "canvas_image",
      kind: "image",
      mimeType: "image/png",
      url: "/static/projects/p/orange-cat.png",
      source: "canvas_node",
    };
    expect(hasExplicitUserImageAttachment([canvasRef])).toBe(false);
    expect(hasExplicitUserImageAttachment([])).toBe(false);
  });

  it("submit policy: when paste present, outbound should not need canvas merge", () => {
    const paste: ChatAttachment = {
      id: "paste-1",
      type: "image",
      kind: "image",
      mimeType: "image/png",
      content: "data:image/png;base64,white-smoke-cat",
      source: "paste",
    };
    const canvasRefs: ChatAttachment[] = [
      {
        id: "canvas-ref-orange",
        type: "canvas_image",
        kind: "image",
        mimeType: "image/png",
        url: "/static/projects/p/orange.png",
        source: "canvas_node",
      },
    ];
    // Mirrors superchat-panel submit: skip auto canvas refs when paste present
    const skipAutoCanvasRefs = hasExplicitUserImageAttachment([paste]);
    const outbound = mergeReferenceAttachments(
      [paste],
      skipAutoCanvasRefs ? [] : canvasRefs,
    );
    expect(skipAutoCanvasRefs).toBe(true);
    expect(outbound).toHaveLength(1);
    expect(outbound[0]?.id).toBe("paste-1");
    expect(outbound[0]?.source).toBe("paste");
  });
});
