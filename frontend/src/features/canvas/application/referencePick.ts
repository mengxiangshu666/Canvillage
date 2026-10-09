// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import {
  CANVAS_NODE_TYPES,
  type CanvasNode,
  type CanvasNodeType,
} from '../domain/canvasNodes';
import { resolveNodeDisplayName } from '../domain/nodeDisplay';
import {
  isManualConnectionAllowed,
  nodeHasSourceHandle,
  nodeHasTargetHandle,
} from '../domain/nodeRegistry';
import { extractUpstreamContent } from './graphContentResolver';

export type ReferenceKind = 'text' | 'image' | 'video' | 'audio';

const ACCEPTED_REFERENCE_KINDS: Partial<Record<CanvasNodeType, readonly ReferenceKind[]>> = {
  [CANVAS_NODE_TYPES.imageGen]: ['image', 'text'],
  [CANVAS_NODE_TYPES.video]: ['image', 'video', 'audio', 'text'],
};

const DEFAULT_REFERENCE_KIND: Partial<Record<CanvasNodeType, ReferenceKind>> = {
  [CANVAS_NODE_TYPES.textAnnotation]: 'text',
  [CANVAS_NODE_TYPES.script]: 'text',
  [CANVAS_NODE_TYPES.upload]: 'image',
  [CANVAS_NODE_TYPES.imageEdit]: 'image',
  [CANVAS_NODE_TYPES.imageGen]: 'image',
  [CANVAS_NODE_TYPES.exportImage]: 'image',
  [CANVAS_NODE_TYPES.storyboardGen]: 'image',
  [CANVAS_NODE_TYPES.video]: 'video',
  [CANVAS_NODE_TYPES.audio]: 'audio',
};

const KIND_LABEL: Record<ReferenceKind, string> = {
  text: '文本',
  image: '图片',
  video: '视频',
  audio: '音频',
};

export interface ReferencePickCandidate {
  label: string;
  kind: ReferenceKind;
}

export interface ReferencePickTargets {
  candidates: Map<string, ReferencePickCandidate>;
  rejections: Map<string, string>;
}

export function supportsReferencePick(type: CanvasNodeType | undefined): boolean {
  return type !== undefined && ACCEPTED_REFERENCE_KINDS[type] !== undefined;
}

export function referenceKindOf(node: CanvasNode): ReferenceKind | null {
  const content = extractUpstreamContent(node);
  if (content.imageUrl) return 'image';
  if (content.videoUrl) return 'video';
  if (content.audioUrl) return 'audio';
  if (content.text) return 'text';
  return DEFAULT_REFERENCE_KIND[node.type as CanvasNodeType] ?? null;
}

export function collectReferencePickTargets(
  nodes: readonly CanvasNode[],
  targetNodeId: string,
  targetNodeType: CanvasNodeType,
): ReferencePickTargets {
  const candidates = new Map<string, ReferencePickCandidate>();
  const rejections = new Map<string, string>();
  const accepted = ACCEPTED_REFERENCE_KINDS[targetNodeType];
  if (!accepted || !nodeHasTargetHandle(targetNodeType)) return { candidates, rejections };

  for (const node of nodes) {
    if (node.id === targetNodeId) continue;
    const type = node.type as CanvasNodeType | undefined;
    if (!type || type === CANVAS_NODE_TYPES.group) continue;
    const kind = referenceKindOf(node);
    if (
      kind
      && accepted.includes(kind)
      && nodeHasSourceHandle(type)
      && isManualConnectionAllowed(type, targetNodeType)
    ) {
      candidates.set(node.id, {
        label: resolveNodeDisplayName(type, node.data ?? {}),
        kind,
      });
      continue;
    }
    rejections.set(
      node.id,
      kind ? `${KIND_LABEL[kind]}暂不支持作为参考` : '这个节点不能作为参考',
    );
  }
  return { candidates, rejections };
}

