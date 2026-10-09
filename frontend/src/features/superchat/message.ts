// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import type { ChatAttachment, ChatMessage, ChatRole, ChatUiEvent } from "@/features/superchat/types";
import { hasStructuredContent } from "@/features/superchat/spec-extract";

const INTERNAL_CONTEXT_BLOCK_RE =
  /\n?\[(VILLAGE_CANVAS_[A-Z0-9_]+)\][\s\S]*?\[\/\1\]\n?/g;

/** Wire-only Freezone envelope; never show scaffolding in the chat bubble. */
const CANVAS_AGENT_REQUEST_BLOCK_RE =
  /\[CANVAS_AGENT_REQUEST_V(1|2)\][\s\S]*?(?:\[\/CANVAS_AGENT_REQUEST_V\1\]|$)/gi;

const CANVAS_AGENT_REQUEST_MARKER_RE = /\[CANVAS_AGENT_REQUEST_V(?:1|2)\]/i;

function extractCanvasAgentV2UserRequest(source: string): string | null {
  const match = source.match(
    /\[CANVAS_AGENT_REQUEST_V2\]\s*([\s\S]*?)\s*\[\/CANVAS_AGENT_REQUEST_V2\]/i,
  );
  if (!match?.[1]) return null;
  try {
    const payload = JSON.parse(match[1]) as unknown;
    if (!payload || typeof payload !== "object" || Array.isArray(payload)) return null;
    const request = (payload as Record<string, unknown>).request;
    return typeof request === "string" && request.trim() ? request.trim() : null;
  } catch {
    return null;
  }
}

/**
 * Pull the human USER_REQUEST body out of a canvas agent envelope.
 * Transport still sends the full envelope to the Village Agent; UI only shows this.
 */
export function extractCanvasAgentUserRequest(text: string): string | null {
  const source = String(text ?? "");
  if (/\[CANVAS_AGENT_REQUEST_V2\]/i.test(source)) {
    const request = extractCanvasAgentV2UserRequest(source);
    if (request) return request;
  }
  if (!/\[CANVAS_AGENT_REQUEST_V1\]/i.test(source)) return null;
  const match = source.match(
    /\[CANVAS_AGENT_REQUEST_V1\]\s*USER_REQUEST\s*[:：]\s*\r?\n([\s\S]*?)\r?\n\s*ACTIVE_SKILLS\s*[:：]/i,
  );
  const body = match?.[1]?.trim();
  return body || null;
}

/** Strip internal wire envelopes so chat history stays human-readable. */
export function stripInternalContextBlocks(text: string): string {
  let out = String(text ?? "");
  if (CANVAS_AGENT_REQUEST_MARKER_RE.test(out)) {
    const userRequest = extractCanvasAgentUserRequest(out);
    const onlyEnvelope = /^\[CANVAS_AGENT_REQUEST_V(?:1|2)\]/i.test(out.trim());
    // Pure stored user payload → show the human request only.
    if (onlyEnvelope && userRequest) return userRequest;
    // Embedded in assistant dump → drop the whole scaffolding block.
    out = out.replace(CANVAS_AGENT_REQUEST_BLOCK_RE, "\n");
    if (!out.trim() && userRequest) return userRequest;
  }
  out = out.replace(INTERNAL_CONTEXT_BLOCK_RE, "\n");
  return out.replace(/\n{3,}/g, "\n\n").trim();
}

function textFromContent(content: unknown): string {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  return content
    .map((block) => {
      if (typeof block === "string") return block;
      if (!block || typeof block !== "object") return "";
      const value = block as Record<string, unknown>;
      if (typeof value.text === "string") return value.text;
      if (typeof value.content === "string") return value.content;
      return "";
    })
    .filter(Boolean)
    .join("\n");
}

export function extractMessageText(message: unknown): string {
  if (typeof message === "string") return stripInternalContextBlocks(message);
  if (!message || typeof message !== "object") return "";
  const value = message as Record<string, unknown>;
  if (typeof value.text === "string") return stripInternalContextBlocks(value.text);
  if (typeof value.message === "string") return stripInternalContextBlocks(value.message);
  if (typeof value.content === "string") return stripInternalContextBlocks(value.content);
  return stripInternalContextBlocks(textFromContent(value.content));
}

function normalizeRole(role: unknown): ChatRole {
  if (role === "user") return "user";
  if (role === "system") return "system";
  if (role === "tool" || role === "tool_result" || role === "toolResult" || role === "trace") return "tool";
  return "assistant";
}

function normalizeId(value: unknown): string | null {
  if (typeof value === "string" && value.trim()) return value;
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  return null;
}

function normalizeTimestamp(value: Record<string, unknown>): number {
  if (typeof value.timestamp === "number") return value.timestamp;
  if (typeof value.created_at === "string") {
    const parsed = Date.parse(value.created_at);
    if (Number.isFinite(parsed)) return parsed;
  }
  if (typeof value.createdAt === "string") {
    const parsed = Date.parse(value.createdAt);
    if (Number.isFinite(parsed)) return parsed;
  }
  return Date.now();
}

function normalizeTurnId(value: Record<string, unknown>): string | undefined {
  return normalizeId(value.turn_id) ?? normalizeId(value.turnId) ?? undefined;
}

function normalizeUiEvents(value: Record<string, unknown>): ChatUiEvent[] | undefined {
  if (!Array.isArray(value.ui_events)) return undefined;
  const events = value.ui_events
    .filter((event): event is Record<string, unknown> => Boolean(event) && typeof event === "object" && !Array.isArray(event))
    .filter((event) => typeof event.type === "string" && event.type.trim().length > 0)
    .map((event) => event as ChatUiEvent);
  return events.length > 0 ? events : undefined;
}

function mediaKindToType(kind: unknown): string | undefined {
  if (kind === "image" || kind === "video" || kind === "audio" || kind === "file") {
    return kind;
  }
  return undefined;
}

export function normalizeMessage(message: unknown, fallbackRole: ChatRole = "assistant"): ChatMessage | null {
  const text = extractMessageText(message).trim();
  if (!text && !hasStructuredContent(message)) return null;
  const value = message && typeof message === "object"
    ? (message as Record<string, unknown>)
    : {};
  const id =
    normalizeId(value.id)
    ?? normalizeId(value.messageId)
    ?? `msg-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  const timestamp = normalizeTimestamp(value);
  const role = "role" in value ? normalizeRole(value.role) : fallbackRole;
  const turnId = normalizeTurnId(value);
  const displayName = typeof value.displayName === "string" ? value.displayName : undefined;
  const attachments = extractAttachments(value);
  const uiEvents = normalizeUiEvents(value);
  return { id, role, text, turnId, displayName, attachments, timestamp, raw: message, uiEvents };
}

function extractAttachments(value: Record<string, unknown>): ChatAttachment[] {
  const contentAttachments = Array.isArray(value.content)
    ? value.content
    .filter((block) => block && typeof block === "object")
    .map((block) => block as Record<string, unknown>)
    .filter((block) => block.type === "image" || block.type === "file" || block.type === "audio" || block.type === "document")
    .map((block) => {
      const source = block.source && typeof block.source === "object"
        ? (block.source as Record<string, unknown>)
        : {};
      const mimeType =
        typeof block.mimeType === "string"
          ? block.mimeType
          : typeof source.media_type === "string"
            ? source.media_type
            : undefined;
      const data = typeof source.data === "string" ? source.data : undefined;
      return {
        id: typeof block.id === "string" ? block.id : undefined,
        type: typeof block.type === "string" ? block.type : undefined,
        mimeType,
        fileName: typeof block.fileName === "string" ? block.fileName : undefined,
        content: data,
      };
    })
    : [];

  const mediaAttachments = Array.isArray(value.media)
    ? value.media
        .filter((item) => item && typeof item === "object")
        .map((item) => item as Record<string, unknown>)
        .map((item): ChatAttachment => {
          const url = typeof item.url === "string" ? item.url : undefined;
          const path = typeof item.path === "string" ? item.path : undefined;
          const label = typeof item.label === "string" ? item.label : undefined;
          const kind = mediaKindToType(item.kind) ?? "file";
          return {
            id: `${kind}:${path || url || label || Math.random().toString(36).slice(2, 8)}`,
            type: kind,
            kind,
            fileName: label || path?.split("/").pop() || url?.split("/").pop(),
            content: url,
            url,
            path,
            label,
          };
        })
    : [];

  return [...contentAttachments, ...mediaAttachments];
}

export function buildLocalUserMessage(
  text: string,
  turnId: string,
  displayName?: string,
  attachments?: ChatAttachment[],
): ChatMessage {
  return {
    id: `user-${turnId}`,
    role: "user",
    text,
    turnId,
    displayName,
    attachments,
    timestamp: Date.now(),
  };
}
