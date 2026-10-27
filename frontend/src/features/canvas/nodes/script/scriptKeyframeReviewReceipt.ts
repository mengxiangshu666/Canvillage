// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import type { CanvasEdge, CanvasNode } from '@/features/canvas/domain/canvasNodes';
import { useCanvasStore } from '@/stores/canvasStore';

/**
 * 关键帧三态验收。
 *
 * 和后端 `keyframe_review.py` 同一条合同：PASS 必须绑定当前画面哈希、参考哈希
 * 和实际观察；REJECT / UNREVIEWED 必须写原因。前端把回执存在关键帧图片节点上，
 * 图片或脚本指纹一变，旧回执立即失效，不会拿旧 PASS 继续出片。
 */

export type ScriptKeyframeReviewState = 'PASS' | 'REJECT' | 'UNREVIEWED';

export interface ScriptKeyframeReview {
  state: ScriptKeyframeReviewState;
  note: string;
  identityMatchesReference: boolean;
  matchesShotRequirement: boolean;
  imageUrl: string;
  frameFingerprint: string;
  frameSha256: string;
  referenceUrl: string;
  referenceSha256: string;
  reviewedAt: string;
}

const SHA256_RE = /^[0-9a-f]{64}$/;

export function keyframeFingerprint(node: CanvasNode): string {
  const data = node.data as Record<string, unknown>;
  return [
    String(data.scriptShotKeyframeSourceNodeId ?? ''),
    String(data.scriptShotKeyframeRowKey ?? ''),
    String(data.scriptShotKeyframeIndex ?? ''),
    String(data.imageUrl ?? data.previewImageUrl ?? ''),
  ].join('|');
}

function reviewRecord(value: unknown): Partial<ScriptKeyframeReview> | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Partial<ScriptKeyframeReview>
    : null;
}

/** 只返回仍然对当前画面有效的回执；换了图或脚本指纹变化都算过期。 */
export function currentKeyframeReview(node: CanvasNode): ScriptKeyframeReview | null {
  const data = node.data as Record<string, unknown>;
  const receipt = reviewRecord(data.scriptKeyframeReview);
  const imageUrl = String(data.imageUrl ?? data.previewImageUrl ?? '');
  if (!receipt || !imageUrl || data.isGenerating || data.generationError) return null;
  if (!['PASS', 'REJECT', 'UNREVIEWED'].includes(String(receipt.state ?? ''))) return null;
  if (receipt.imageUrl !== imageUrl || receipt.frameFingerprint !== keyframeFingerprint(node)) return null;
  if (typeof receipt.note !== 'string') return null;
  if (receipt.state === 'PASS') {
    if (!receipt.identityMatchesReference || !receipt.matchesShotRequirement) return null;
    if (!receipt.note.trim()) return null;
    if (!SHA256_RE.test(String(receipt.frameSha256 ?? ''))) return null;
    if (!SHA256_RE.test(String(receipt.referenceSha256 ?? ''))) return null;
  }
  if (receipt.state !== 'PASS' && !receipt.note.trim()) return null;
  return receipt as ScriptKeyframeReview;
}

export async function sha256Url(url: string): Promise<string> {
  if (!url) return '';
  const response = await fetch(url, { credentials: 'include' });
  if (!response.ok) throw new Error(`画面读取失败：HTTP ${response.status}`);
  const bytes = await response.arrayBuffer();
  const digest = await crypto.subtle.digest('SHA-256', bytes);
  return [...new Uint8Array(digest)].map(byte => byte.toString(16).padStart(2, '0')).join('');
}

export function referenceImageUrlForNode(
  nodeId: string,
  graph: { nodes: CanvasNode[]; edges: CanvasEdge[] },
): string {
  const incoming = graph.edges.filter(edge => edge.target === nodeId);
  for (const edge of incoming) {
    const source = graph.nodes.find(node => node.id === edge.source);
    const url = String(source?.data.imageUrl ?? source?.data.previewImageUrl ?? '');
    if (url) return url;
  }
  return '';
}

export function saveKeyframeReview(
  node: CanvasNode,
  input: {
    state: ScriptKeyframeReviewState;
    note: string;
    identityMatchesReference: boolean;
    matchesShotRequirement: boolean;
    frameSha256: string;
    referenceUrl: string;
    referenceSha256: string;
  },
): string | null {
  const data = node.data as Record<string, unknown>;
  const imageUrl = String(data.imageUrl ?? data.previewImageUrl ?? '');
  if (!imageUrl) return '这张关键帧还没有生成完成';
  if (data.isGenerating) return '关键帧还在生成中，完成后才能验收';
  if (data.generationError) return '关键帧生成失败，请先重新生成';
  const note = input.note.trim();
  if (input.state === 'PASS') {
    if (!input.identityMatchesReference || !input.matchesShotRequirement) {
      return 'PASS 需要同时确认身份一致和符合分镜要求';
    }
    if (!note) return 'PASS 需要写下实际观察，不能只点通过';
    if (!SHA256_RE.test(input.frameSha256)) return '画面哈希还没有算好，请重试';
    if (!SHA256_RE.test(input.referenceSha256)) return '参考图哈希还没有算好，请重试';
  }
  if (input.state !== 'PASS' && !note) return '请填写退回原因或未审原因';
  useCanvasStore.getState().updateNodeData(node.id, {
    scriptKeyframeReview: {
      ...input,
      note,
      imageUrl,
      frameFingerprint: keyframeFingerprint(node),
      frameSha256: input.frameSha256.toLowerCase(),
      referenceUrl: input.referenceUrl,
      referenceSha256: input.referenceSha256.toLowerCase(),
      reviewedAt: new Date().toISOString(),
    } satisfies ScriptKeyframeReview,
  });
  return null;
}
