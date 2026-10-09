// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import type { FreezoneStoryScriptRow } from '@/api/scriptContract';
import { CANVAS_NODE_TYPES, type CanvasNode } from '@/features/canvas/domain/canvasNodes';
import { resolveAbsolutePosition, useCanvasStore } from '@/stores/canvasStore';
import { findStoryboardBlockOrigin, STORYBOARD_IMAGE_CELL_WIDTH, STORYBOARD_IMAGE_CELL_HEIGHT } from './scriptStoryboard';
import { withScriptImageQuality } from './scriptRenderQuality';
import { cellText, isScriptNoValue } from './scriptViews';

export function scriptEndFramePrompt(row: FreezoneStoryScriptRow): string {
  const ending = cellText(row, 'end_state');
  if (isScriptNoValue(ending)) return '';
  const prop = cellText(row, 'prop_state_end');
  const camera = cellText(row, 'camera_movement');
  return withScriptImageQuality([
    `尾帧契约：只画本镜结束瞬间的一张画面。结束可见状态：${ending}。`,
    !isScriptNoValue(prop) ? `结束道具状态：${prop}。` : '',
    '资产图锚定：图片1是本镜起始画面，只提供角色身份、服装、场景结构、道具设计与美术风格；姿态、位置及接触关系服从结束状态。',
    !isScriptNoValue(camera) ? `按本镜摄影机轨迹的末端机位成像：${camera}。不把运动轨迹画成箭头或多幅图。` : '',
    '只画结束时可见的状态，不画动作过程、时间轴或拼贴，不提前画下一镜，不添加文字。结束正在运动时允许自然运动模糊，不强迫停住。',
  ].filter(Boolean).join('\n'));
}

/** Prepare only unpaid drafts; generated old frames remain available on the canvas. */
export function prepareScriptEndFrameDraft(video: CanvasNode, first: CanvasNode, row: FreezoneStoryScriptRow, fingerprint: string): void {
  const prompt = scriptEndFramePrompt(row);
  if (!prompt || !first.data.imageUrl) return;
  const state = useCanvasStore.getState();
  const incoming = state.edges.filter(edge => edge.target === video.id && edge.source !== first.id);
  if (incoming.some(edge => edge.data?.role !== 'scriptShotEndFrame')) return;
  const matching = state.nodes.filter(node => node.data.scriptShotEndFrameForVideo === video.id
    && node.data.scriptShotRowFingerprint === fingerprint
    && node.data.scriptShotEndFrameSourceUrl === first.data.imageUrl);
  if (matching.length > 1) return;
  const nodeMap = new Map(state.nodes.map(node => [node.id, node]));
  const absolute = resolveAbsolutePosition(video, nodeMap);
  const position = findStoryboardBlockOrigin({
    script: { ...absolute, width: video.measured?.width ?? 580, height: video.measured?.height ?? 380 },
    gridWidth: STORYBOARD_IMAGE_CELL_WIDTH, gridHeight: STORYBOARD_IMAGE_CELL_HEIGHT,
    occupied: state.nodes.map(node => ({ ...resolveAbsolutePosition(node, nodeMap),
      width: node.measured?.width ?? 580, height: node.measured?.height ?? 380 })),
  });
  const frameId = matching[0]?.id ?? state.addNode(CANVAS_NODE_TYPES.imageGen,
    position, {
      label: `${String(video.data.label || '镜头')} · 尾帧草稿`,
      prompt, model: first.data.model, size: first.data.size,
      requestAspectRatio: first.data.requestAspectRatio,
      referenceImageUrl: first.data.imageUrl,
      referenceImageUrls: [first.data.imageUrl], count: 1,
      canvas_auto_generate_once: false,
      scriptShotId: video.data.scriptShotId,
      scriptShotRowFingerprint: fingerprint,
      scriptShotEndFrameForVideo: video.id,
      scriptShotEndFrameSourceUrl: first.data.imageUrl,
    });
  if (!frameId) return;
  for (const edge of incoming) if (edge.source !== frameId) state.deleteEdge(edge.id);
  if (!state.edges.some(edge => edge.source === frameId && edge.target === video.id)) {
    state.addEdgeWithData(frameId, video.id, { role: 'scriptShotEndFrame', edgeKind: 'mainline_data', propagates: true, label: '尾帧' }, { sourceHandle: 'source', targetHandle: 'target' });
  }
  state.updateNodeData(video.id, { genMode: 'firstLastFrame' });
}
