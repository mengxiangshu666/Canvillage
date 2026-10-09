// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api";

import {
  DEFAULT_CHAT_CONVERSATION_ID,
  deleteChatConversation,
  loadActiveConversationId,
  saveActiveConversationId,
} from "./chat-conversations";

describe("chat conversation selection", () => {
  beforeEach(() => localStorage.clear());
  afterEach(() => vi.restoreAllMocks());

  it("uses the legacy-compatible main conversation by default", () => {
    expect(loadActiveConversationId("project-a", "canvas-a"))
      .toBe(DEFAULT_CHAT_CONVERSATION_ID);
  });

  it("keeps each project canvas on its own selected conversation", () => {
    saveActiveConversationId("project-a", "canvas-a", "conversation-a");
    saveActiveConversationId("project-a", "canvas-b", "conversation-b");

    expect(loadActiveConversationId("project-a", "canvas-a")).toBe("conversation-a");
    expect(loadActiveConversationId("project-a", "canvas-b")).toBe("conversation-b");
  });

  it("deletes the exact project canvas conversation through the backend", async () => {
    const deleteRequest = vi.spyOn(api, "delete").mockReturnValue(
      Promise.resolve(
        new Response(
          JSON.stringify({
            ok: true,
            data: {
              id: "conversation-a",
              deleted: true,
              message_count: 3,
              ui_event_count: 1,
              recovery_count: 1,
              checkpoint_count: 2,
              auxiliary_cleanup_failed: false,
              session_discarded: true,
            },
          }),
          { status: 200, headers: { "content-type": "application/json" } },
        ),
      ) as ReturnType<typeof api.delete>,
    );

    const result = await deleteChatConversation(
      "project one",
      "canvas-a",
      "conversation-a",
    );

    expect(result).toMatchObject({ id: "conversation-a", deleted: true });
    expect(deleteRequest).toHaveBeenCalledWith(
      "api/v1/chat/conversations/conversation-a?project=project+one&canvas_id=canvas-a",
    );
  });
});
