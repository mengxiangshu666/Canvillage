// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { CANVAS_NODE_TYPES, type CanvasNode, type CanvasEdge, type VideoGenMode, type VideoCreativeHandoff, type VideoShotContractFacts } from '@/features/canvas/domain/canvasNodes';
import { inferVideoReferenceRole, referenceRoleFromVideoEdge, type VideoReferenceRole } from '@/features/canvas/domain/videoReferenceRoles';
import { useCanvasStore } from '@/stores/canvasStore';
import { sortUpstreamByReferenceOrder, upstreamNodesInEdgeOrder } from '../referenceOrdering';
import { submittableImageUrl } from '../videoNodeModelRules';
import { type ScriptShotRefEntry } from './scriptShotRefs';
import { type ScriptVideoModelCapabilities } from './scriptVideoDuration';
import { scriptReferenceResponsibility } from './scriptCreativeHandoff';
import { stripManagedScriptVideoReferences } from '@/features/canvas/domain/promptSegments';
export { scriptVideoReferenceBlock, stripManagedScriptVideoReferences } from '@/features/canvas/domain/promptSegments';

export const SCRIPT_VIDEO_ASSET_EDGE_ROLE = 'scriptShotAssetReference';
const HANDOFF_REFERENCE_ROLES: Partial<Record<VideoReferenceRole, NonNullable<VideoCreativeHandoff['referenceResponsibilities']>[number]['role']>> = {
  identity: 'character', scene: 'scene', prop: 'prop',
  first_frame: 'opening_frame', keyframe: 'state_frame', last_frame: 'end_frame',
  continuity: 'continuity_frame', motion: 'motion', style: 'style',
};

/** Only managed reference numbering and whitespace are excluded from version checks. */
export function scriptVideoExecutionPromptKey(value: string): string {
  return stripManagedScriptVideoReferences(value).replace(/\s+/g, ' ').trim();
}

export function scriptVideoExecutionPromptMatches(data: CanvasNode['data']): boolean {
  const facts = data.shotContractFacts as VideoShotContractFacts | null | undefined;
  const expected = facts?.executionPrompt;
  if (typeof expected !== 'string' || !expected.trim()) return false;
  return scriptVideoExecutionPromptKey(String(data.prompt ?? '')) === scriptVideoExecutionPromptKey(expected);
}

/** Submitted images may include video tails captured after the graph was read. */
export function compileScriptVideoReferences(prompt: string, videoNodeId: string, graph: { nodes: CanvasNode[]; edges: CanvasEdge[] }, submittedImages?: readonly { node: CanvasNode; role?: VideoReferenceRole }[]): {
  prompt: string;
  references?: NonNullable<VideoCreativeHandoff['referenceResponsibilities']>;
} {
  const video = graph.nodes.find(node => node.id === videoNodeId);
  if (!video || (!video.data.scriptShotSourceNodeId && !graph.edges.some(edge => edge.target === videoNodeId && edge.data?.role === SCRIPT_VIDEO_ASSET_EDGE_ROLE))) return { prompt };
  const order = Array.isArray(video.data.referenceOrder) ? video.data.referenceOrder.filter((id): id is string => typeof id === 'string') : undefined;
  const images = submittedImages ?? sortUpstreamByReferenceOrder(upstreamNodesInEdgeOrder(graph.nodes, graph.edges, videoNodeId), order)
    .filter(node => submittableImageUrl(node) && !node.data.videoUrl).map(node => ({ node, role: undefined }));
  const references = images.map(({ node, role: submittedRole }, index) => {
    const edge = graph.edges.find(item => item.source === node.id && item.target === videoNodeId);
    const inputRole = submittedRole ?? (video.data.genMode === 'firstLastFrame' ? (index === 0 ? 'first_frame' : 'last_frame')
      : referenceRoleFromVideoEdge(edge?.data?.role, edge?.data?.label) ?? inferVideoReferenceRole(node.data));
    const role = HANDOFF_REFERENCE_ROLES[inputRole ?? 'generic'] ?? 'reference';
    const wording = scriptReferenceResponsibility(role);
    return {
      scope: 'video' as const,
      imageNumber: index + 1,
      role,
      name: String(edge?.data?.label || node.data.displayName || '参考图').replace(/^(角色|场景|道具)\s+/, '$1'),
      sourceNodeId: node.id,
      ...wording,
      ...(role === 'state_frame' ? {
        responsibility: `是本镜状态关键帧，动作发展到${node.data.scriptShotKeyframeState || '此画面状态'}${node.data.scriptShotKeyframePurpose ? `（参考用途：${node.data.scriptShotKeyframePurpose}，不另设动作，状态优先）` : ''}；${wording.responsibility}`,
      } : {}),
    };
  });
  const lines = references.map(reference => {
    const ref = `@图片${reference.imageNumber}`;
    const prefix = ['character', 'scene', 'prop'].includes(reference.role) ? `${reference.name}引用${ref}，` : ref;
    return `${prefix}${reference.responsibility}；${reference.prohibited}`;
  });
  const body = stripManagedScriptVideoReferences(prompt);
  return {
    prompt: [lines.length ? `[视频参考用途：${lines.join('；')}。资产图只锁定设定，不复制多视图拼版、标注或文字；服装装备以本镜状态为准。动作与运镜按本镜剧本执行。]` : '', body].filter(Boolean).join('\n'),
    references,
  };
}

export function scriptVideoReferencePrompt(prompt: string, videoNodeId: string, graph: { nodes: CanvasNode[]; edges: CanvasEdge[] }): string {
  return compileScriptVideoReferences(prompt, videoNodeId, graph).prompt;
}

/** Keep storyboard provenance while replacing the current video image numbering. */
export function scriptVideoReferenceFacts(facts: VideoShotContractFacts | null | undefined, references: VideoCreativeHandoff['referenceResponsibilities']): VideoShotContractFacts | undefined {
  if (!facts || !references) return undefined;
  const creativeHandoff = facts.creativeHandoff ?? {};
  return { ...facts, creativeHandoff: {
    ...creativeHandoff,
    referenceResponsibilities: [...(creativeHandoff.referenceResponsibilities ?? []).filter(item => item.scope === 'storyboard'), ...references],
  } };
}

export function scriptAssetVideoMode(count: number, model?: ScriptVideoModelCapabilities | null): VideoGenMode | null {
  if (model?.supportedModes === undefined) {
    return count <= (model?.referenceLimits?.allReference?.image ?? 9) ? 'allReference' : null;
  }
  for (const mode of ['allReference', 'imageReference', 'imageToVideo'] as const) {
    const limit = model.referenceLimits?.[mode]?.image ?? (mode === 'imageToVideo' ? 1 : 9);
    if (model.supportedModes?.includes(mode) && limit >= count) return mode;
  }
  return null;
}

/** Only reconcile edges owned by script derivation; keep manual references. */
export function ensureScriptVideoAssetReferences(params: {
  scriptNodeId: string;
  videoNodeId: string;
  firstFrameNodeId?: string;
  keyframeNodeIds?: readonly string[];
  references: readonly ScriptShotRefEntry[];
  model?: ScriptVideoModelCapabilities | null;
}): boolean {
  const { scriptNodeId, videoNodeId, firstFrameNodeId, keyframeNodeIds = [], references, model } = params;
  const store = useCanvasStore.getState();
  const first = store.nodes.find(node => node.id === firstFrameNodeId);
  const seen = new Set<string>(first?.data.imageUrl ? [String(first.data.imageUrl)] : []);
  const selected: Array<{ nodeId: string; reference: ScriptShotRefEntry }> = [];
  for (const reference of references) {
    if (!reference.assetId || seen.has(reference.imageUrl)) continue;
    seen.add(reference.imageUrl);
    const current = useCanvasStore.getState();
    const node = current.nodes.find(item => item.data.imageUrl === reference.imageUrl
      && item.data.scriptAssetOwnerId === scriptNodeId)
      ?? current.nodes.find(item => item.data.imageUrl === reference.imageUrl);
    const nodeId = node?.id ?? current.addNode(CANVAS_NODE_TYPES.upload,
      { x: (first?.position.x ?? 0) - 360, y: (first?.position.y ?? 0) + selected.length * 120 },
      { imageUrl: reference.imageUrl, displayName: `${reference.roleLabel} ${reference.name}`, imageOnly: true });
    if (!nodeId) return false;
    selected.push({ nodeId, reference });
  }
  const ids = new Set(selected.map(item => item.nodeId));
  for (const edge of useCanvasStore.getState().edges) {
    if (edge.target === videoNodeId && edge.data?.role === SCRIPT_VIDEO_ASSET_EDGE_ROLE && !ids.has(edge.source)) {
      useCanvasStore.getState().deleteEdge(edge.id);
    }
  }
  for (const { nodeId, reference } of selected) {
    if (useCanvasStore.getState().edges.some(edge => edge.source === nodeId && edge.target === videoNodeId)) continue;
    const edgeId = useCanvasStore.getState().addEdgeWithData(nodeId, videoNodeId,
      { edgeKind: 'mainline_data', propagates: true, role: SCRIPT_VIDEO_ASSET_EDGE_ROLE, label: `${reference.roleLabel} ${reference.name}` },
      { id: `edge_${nodeId}_to_${videoNodeId}_scriptAsset`, sourceHandle: 'source', targetHandle: 'target' });
    if (!edgeId) return false;
  }
  const current = useCanvasStore.getState();
  const video = current.nodes.find(node => node.id === videoNodeId);
  if (!video) return false;
  // Script-owned order is deterministic: opening frame, planned state frames,
  // then identity assets. Manual/continuity references stay after that block.
  const fixedPrefix = [firstFrameNodeId, ...keyframeNodeIds].filter((id): id is string => Boolean(id));
  const preferredAssets = selected.map(item => item.nodeId);
  const preferred = [...fixedPrefix, ...preferredAssets];
  const remaining = current.edges.filter(edge => edge.target === videoNodeId && !preferred.includes(edge.source)).map(edge => edge.source);
  const connected = new Set([...preferred, ...remaining]);
  const oldOrder = Array.isArray(video.data.referenceOrder)
    ? video.data.referenceOrder.filter((id): id is string => typeof id === 'string' && connected.has(id))
    : [];
  const manualOrder = oldOrder.filter(id => !fixedPrefix.includes(id));
  const order = [...new Set([...fixedPrefix, ...manualOrder, ...preferredAssets, ...remaining])];
  // Mention sync keeps @图片N correct when users reorder connected references.
  const imageOrder = order.filter(id => current.nodes.some(node => node.id === id && submittableImageUrl(node) && !node.data.videoUrl));
  const mode = (firstFrameNodeId || selected.length) && video.data.genMode !== 'firstLastFrame'
    ? scriptAssetVideoMode(imageOrder.length, model) : null;
  current.updateNodeData(videoNodeId, {
    referenceOrder: order,
    scriptVideoAssetReferenceOrder: imageOrder,
    ...(mode ? { genMode: mode } : {}),
  });
  const compiled = compileScriptVideoReferences(String(video.data.prompt ?? ''), videoNodeId, useCanvasStore.getState());
  const shotContractFacts = scriptVideoReferenceFacts(video.data.shotContractFacts as VideoShotContractFacts | undefined, compiled.references);
  current.updateNodeData(videoNodeId, { prompt: compiled.prompt, ...(shotContractFacts ? { shotContractFacts } : {}) });
  return true;
}
