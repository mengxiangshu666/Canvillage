// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab

import { api } from "@/lib/api";
import { jsonWithBackendError } from "@/lib/api-errors";
import type { ChatScope } from "@/features/superchat/types";

export const DEFAULT_CHAT_CONVERSATION_ID = "main";

export type ChatConversation = {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  message_count: number;
  preview: string;
};

type ConversationListResponse = {
  ok?: boolean;
  data?: { conversations?: ChatConversation[] };
};

type ConversationCreateResponse = {
  ok?: boolean;
  data?: ChatConversation;
};

export type ChatConversationDeleteResult = {
  id: string;
  deleted: boolean;
  message_count: number;
  ui_event_count: number;
  recovery_count: number;
  checkpoint_count: number;
  auxiliary_cleanup_failed: boolean;
  session_discarded: boolean;
};

type ConversationDeleteResponse = {
  ok?: boolean;
  data?: ChatConversationDeleteResult;
};

function activeConversationStorageKey(projectId: string, canvasId: string): string {
  return `st.superchat.conversation.v1:${projectId.trim()}:${canvasId.trim() || "default"}`;
}

export function loadActiveConversationId(projectId: string, canvasId: string): string {
  if (!projectId.trim()) return DEFAULT_CHAT_CONVERSATION_ID;
  try {
    return localStorage.getItem(activeConversationStorageKey(projectId, canvasId))?.trim()
      || DEFAULT_CHAT_CONVERSATION_ID;
  } catch {
    return DEFAULT_CHAT_CONVERSATION_ID;
  }
}

export function saveActiveConversationId(
  projectId: string,
  canvasId: string,
  conversationId: string,
): void {
  if (!projectId.trim()) return;
  try {
    localStorage.setItem(
      activeConversationStorageKey(projectId, canvasId),
      conversationId.trim() || DEFAULT_CHAT_CONVERSATION_ID,
    );
  } catch {
    // 会话选择持久化只是体验增强，服务端历史仍是权威来源。
  }
}

export async function listChatConversations(
  projectId: string,
  canvasId: string,
): Promise<ChatConversation[]> {
  if (!projectId.trim()) return [];
  const params = new URLSearchParams({
    project: projectId.trim(),
    canvas_id: canvasId.trim() || "default",
  });
  const response = await api
    .get(`api/v1/chat/conversations?${params.toString()}`)
    .json<ConversationListResponse>();
  return Array.isArray(response.data?.conversations)
    ? response.data.conversations
    : [];
}

export async function createChatConversation(
  scope: ChatScope,
  title = "新对话",
): Promise<ChatConversation> {
  const response = await api
    .post("api/v1/chat/conversations", {
      json: { scope, title },
    })
    .json<ConversationCreateResponse>();
  if (!response.data?.id) throw new Error("新对话创建失败");
  return response.data;
}

export async function deleteChatConversation(
  projectId: string,
  canvasId: string,
  conversationId: string,
): Promise<ChatConversationDeleteResult> {
  const normalizedProject = projectId.trim();
  const normalizedConversation = conversationId.trim();
  if (!normalizedProject || !normalizedConversation) {
    throw new Error("缺少要删除的历史会话");
  }
  const params = new URLSearchParams({
    project: normalizedProject,
    canvas_id: canvasId.trim() || "default",
  });
  const response = await jsonWithBackendError<ConversationDeleteResponse>(
    api.delete(
      `api/v1/chat/conversations/${encodeURIComponent(normalizedConversation)}?${params.toString()}`,
    ),
  );
  if (!response.data?.deleted) throw new Error("历史会话删除失败");
  return response.data;
}
