// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
/**
 * Real canvas media references for Freezone Agent.
 * These become ChatAttachment payloads that the backend inlines as ACP vision images.
 */
import type { CanvasAgentNodeRef } from "@/features/superchat/canvas-agent-skills";
import type { ChatAttachment } from "@/features/superchat/types";
import { resolveMediaUrl } from "@/lib/media-url";
import { useCanvasStore } from "@/stores/canvasStore";

function guessMimeFromUrl(url: string): string {
  const lower = url.toLowerCase().split("?")[0] ?? "";
  if (lower.endsWith(".png")) return "image/png";
  if (lower.endsWith(".webp")) return "image/webp";
  if (lower.endsWith(".gif")) return "image/gif";
  if (lower.endsWith(".avif")) return "image/avif";
  if (lower.endsWith(".jpg") || lower.endsWith(".jpeg")) return "image/jpeg";
  return "image/png";
}

function nodeMediaUrl(data: Record<string, unknown> | undefined): string | null {
  if (!data) return null;
  const candidates = [
    data.imageUrl,
    data.previewImageUrl,
    data.thumbnailUrl,
    data.coverUrl,
    data.videoUrl, // video: still send URL metadata; vision uses image fields preferentially
  ];
  for (const candidate of candidates) {
    if (typeof candidate !== "string" || !candidate.trim()) continue;
    const resolved = resolveMediaUrl(candidate.trim());
    if (resolved) return resolved;
  }
  return null;
}

export function canvasNodeToReferenceAttachment(nodeId: string): ChatAttachment | null {
  const node = useCanvasStore.getState().nodes.find((item) => item.id === nodeId);
  if (!node) return null;
  const data = node.data as Record<string, unknown> | undefined;
  const url = nodeMediaUrl(data);
  if (!url) return null;
  const label = String(data?.displayName ?? data?.label ?? node.type ?? node.id);
  const isVideo = typeof data?.videoUrl === "string" && Boolean(data.videoUrl) && url === resolveMediaUrl(String(data.videoUrl));
  // Prefer still image fields for vision; if only video URL exists, mark as video ref (text + url).
  const imageLike = !isVideo || Boolean(data?.imageUrl || data?.previewImageUrl || data?.thumbnailUrl);
  return {
    id: `canvas-ref-${node.id}`,
    type: imageLike ? "canvas_image" : "video",
    kind: imageLike ? "image" : "video",
    mimeType: imageLike ? guessMimeFromUrl(url) : "video/mp4",
    fileName: `${label.replace(/[\\/:*?"<>|]+/g, "_").slice(0, 80)}.${imageLike ? "png" : "mp4"}`,
    url,
    label: `画布节点 · ${label}`,
    nodeId: node.id,
    source: "canvas_node",
  };
}

export function buildCanvasReferenceAttachments(input: {
  pinnedNodes: readonly CanvasAgentNodeRef[];
}): ChatAttachment[] {
  const ids: string[] = [];
  for (const node of input.pinnedNodes) {
    if (!ids.includes(node.id)) ids.push(node.id);
  }
  const attachments: ChatAttachment[] = [];
  for (const id of ids) {
    const attachment = canvasNodeToReferenceAttachment(id);
    if (attachment) attachments.push(attachment);
  }
  return attachments;
}

export async function fileToImageAttachment(file: File): Promise<ChatAttachment | null> {
  if (!file.type.startsWith("image/")) return null;
  if (file.size <= 0 || file.size > 5 * 1024 * 1024) return null;
  const dataUrl = await new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(reader.error ?? new Error("read failed"));
    reader.readAsDataURL(file);
  });
  if (!dataUrl.startsWith("data:image/")) return null;
  return {
    id: `paste-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    type: "image",
    kind: "image",
    mimeType: file.type || "image/png",
    fileName: file.name || "pasted-image.png",
    fileSize: file.size,
    content: dataUrl,
    label: file.name || "粘贴图片",
    source: "paste",
  };
}

export function hasExplicitUserImageAttachment(
  attachments: readonly ChatAttachment[],
): boolean {
  return attachments.some((item) => {
    // Auto canvas refs must never count as "user explicit" — they are the
    // thing we skip when the user already pasted/uploaded a real image.
    if (item.source === "canvas_node" || item.type === "canvas_image") return false;
    if (item.source === "paste") return true;
    const kind = String(item.kind || item.type || "").toLowerCase();
    const mime = String(item.mimeType || "").toLowerCase();
    // Prefer data-URL / inline content (paste & file picker), not remote canvas URLs.
    if (kind === "image" && Boolean(item.content)) return true;
    if (kind === "image" && mime.startsWith("image/") && !item.url) return true;
    return false;
  });
}

export function mergeReferenceAttachments(
  existing: readonly ChatAttachment[],
  incoming: readonly ChatAttachment[],
): ChatAttachment[] {
  const out = [...existing];
  for (const item of incoming) {
    const key = item.id || item.url || item.content?.slice(0, 64);
    if (!key) continue;
    if (out.some((current) => (current.id || current.url || current.content?.slice(0, 64)) === key)) {
      continue;
    }
    out.push(item);
  }
  return out;
}
