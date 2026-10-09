// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from "vitest";

import {
  extractCanvasAgentUserRequest,
  extractMessageText,
  stripInternalContextBlocks,
} from "./message";

const ENVELOPE = `[CANVAS_AGENT_REQUEST_V1]
USER_REQUEST:
你应该拉动那个原图片作为参考图，新建立一个节点。

ACTIVE_SKILLS:
- skill_key: village-canvas-canvas-director
  title: 画布导演
  activation: 先 freezone_get_canvas_snapshot

PINNED_NODES:
- node_id: 2acee7b8-2562-4ad3-aef0-0ebe117d746a

CURRENT_CANVAS_CONTEXT:
{"project_id":"x","canvas_id":"y"}
[/CANVAS_AGENT_REQUEST_V1]`;

const V2_ENVELOPE = `[CANVAS_AGENT_REQUEST_V2]{"v":2,"request":"把原图拉成参考图，再建立一个节点。","ACTIVE_SKILLS":["village-canvas-canvas-director"],"canvas":{"project_id":"x","canvas_id":"y"},"pins":[]}[/CANVAS_AGENT_REQUEST_V2]`;

describe("canvas agent envelope display strip", () => {
  it("extracts USER_REQUEST from full envelope", () => {
    expect(extractCanvasAgentUserRequest(ENVELOPE)).toBe(
      "你应该拉动那个原图片作为参考图，新建立一个节点。",
    );
  });

  it("extracts the original request from a V2 JSON envelope", () => {
    expect(extractCanvasAgentUserRequest(V2_ENVELOPE)).toBe(
      "把原图拉成参考图，再建立一个节点。",
    );
  });

  it("shows only human request in stripInternalContextBlocks", () => {
    expect(stripInternalContextBlocks(ENVELOPE)).toBe(
      "你应该拉动那个原图片作为参考图，新建立一个节点。",
    );
    expect(stripInternalContextBlocks(ENVELOPE)).not.toContain("CANVAS_AGENT_REQUEST");
    expect(stripInternalContextBlocks(ENVELOPE)).not.toContain("ACTIVE_SKILLS");
  });

  it("shows only the original V2 request in the chat UI", () => {
    expect(stripInternalContextBlocks(V2_ENVELOPE)).toBe(
      "把原图拉成参考图，再建立一个节点。",
    );
    expect(extractMessageText({ role: "user", content: V2_ENVELOPE })).toBe(
      "把原图拉成参考图，再建立一个节点。",
    );
  });

  it("extractMessageText strips envelope from history payloads", () => {
    expect(extractMessageText({ role: "user", content: ENVELOPE })).toBe(
      "你应该拉动那个原图片作为参考图，新建立一个节点。",
    );
  });

  it("leaves plain user text unchanged", () => {
    expect(stripInternalContextBlocks("你好，帮我连线")).toBe("你好，帮我连线");
  });

  it("still strips VILLAGE_CANVAS_* internal blocks", () => {
    const text = "可见正文\n[VILLAGE_CANVAS_FOO]\nsecret\n[/VILLAGE_CANVAS_FOO]\n尾";
    expect(stripInternalContextBlocks(text)).toBe("可见正文\n尾");
  });
});
