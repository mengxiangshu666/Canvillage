// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
/**
 * One-shot handoff from the project centre (home) into a canvas.
 *
 * The home hero lets a user describe an idea before any canvas exists, so the
 * text (and optionally a starter route) has to survive the navigation into
 * `/projects/<id>/freezone`. A query param is the wrong channel: the freezone
 * route already owns `?canvas=`, and `writeUrl()` rewrites that query on every
 * canvas switch, so a `draft` param would be trampled or re-seed the composer
 * on reload. A single-slot session key that the freezone consumes and deletes
 * is one-shot by construction and never leaks into a shared link.
 */
import type { ChatAttachment } from "@/types/chat-attachment";
const HANDOFF_KEY = "village.home.canvas-handoff";

export interface HomeCanvasHandoff {
  /** Project the handoff was created for; a mismatch means the entry is stale. */
  projectId: string;
  /** Verbatim text the user typed in the home composer. */
  draft: string;
  /** Optional starter route to preload into a canvas that is still empty. */
  starterWorkflowId: string | null;
  attachments?: ChatAttachment[];
  skillIds?: string[];
  autoSend?: boolean;
}

function sessionStore(): Storage | null {
  if (typeof window === "undefined") return null;
  try {
    return window.sessionStorage;
  } catch {
    // Private-mode Safari and hardened browsers can throw on access.
    return null;
  }
}

function normalizeStored(value: unknown): HomeCanvasHandoff | null {
  if (!value || typeof value !== "object") return null;
  const record = value as Record<string, unknown>;
  const projectId = typeof record.projectId === "string" ? record.projectId.trim() : "";
  if (!projectId) return null;
  const draft = typeof record.draft === "string" ? record.draft : "";
  const rawStarter = typeof record.starterWorkflowId === "string"
    ? record.starterWorkflowId.trim()
    : "";
  return {
    projectId,
    draft,
    starterWorkflowId: rawStarter || null,
    ...(record.autoSend === true ? { autoSend: true } : {}),
    ...(Array.isArray(record.skillIds) && record.skillIds.length ? { skillIds: record.skillIds.filter((id): id is string => typeof id === "string") } : {}),
    ...(Array.isArray(record.attachments) && record.attachments.length ? {
      attachments: record.attachments.filter((item): item is ChatAttachment => Boolean(item && typeof item === "object" && typeof item.content === "string" && typeof item.fileName === "string")).slice(0, 2),
    } : {}),
  };
}

/** Queue a handoff for the next freezone entry. Empty handoffs are dropped. */
export function setHomeCanvasHandoff(handoff: HomeCanvasHandoff): void {
  const normalized = normalizeStored(handoff);
  const store = sessionStore();
  if (!normalized || !store) return;
  if (!normalized.draft.trim() && !normalized.starterWorkflowId && !normalized.attachments?.length) return;
  try {
    store.setItem(HANDOFF_KEY, JSON.stringify(normalized));
  } catch {
    throw new Error("首页内容暂存失败，请减少参考素材后重试");
  }
}

/**
 * Read the pending handoff for `projectId` without consuming it.
 *
 * Deliberately side-effect free so callers can run it during render (React
 * double-invokes state initializers in StrictMode; a mutating read there would
 * be consumed by the discarded pass). The entry is ignored when it belongs to
 * another project, so a stale handoff from an abandoned creation cannot seed
 * the canvas the user actually opened.
 */
export function peekHomeCanvasHandoff(projectId: string): HomeCanvasHandoff | null {
  const store = sessionStore();
  if (!store) return null;
  const raw = store.getItem(HANDOFF_KEY);
  if (!raw) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }
  const handoff = normalizeStored(parsed);
  if (!handoff || handoff.projectId !== projectId) return null;
  return handoff;
}

/**
 * Read the pending handoff for `projectId` and clear it. Prefer this in
 * effects; keep render-time reads on `peekHomeCanvasHandoff`.
 */
export function takeHomeCanvasHandoff(projectId: string): HomeCanvasHandoff | null {
  const handoff = peekHomeCanvasHandoff(projectId);
  if (handoff) {
    clearHomeCanvasHandoff();
  }
  return handoff;
}

/** Drop any pending handoff. Used when a creation attempt fails. */
export function clearHomeCanvasHandoff(): void {
  sessionStore()?.removeItem(HANDOFF_KEY);
}
