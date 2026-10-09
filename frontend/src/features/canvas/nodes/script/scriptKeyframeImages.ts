// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { CANVAS_NODE_TYPES, type CanvasNode, type ImageGenNodeData, type VideoCreativeHandoff } from '@/features/canvas/domain/canvasNodes';
import type { FreezoneStoryDirectorPlan } from '@/api/scriptContract';
import { readScriptShotId } from '@/features/canvas/domain/scriptShotIdentity';
import { resolveAbsolutePosition, useCanvasStore } from '@/stores/canvasStore';
import { collectScriptAssetLedger, scriptAssetImageNodes } from './scriptAssets';
import { buildScriptShotSpecs, findStoryboardBlockOrigin, STORYBOARD_IMAGE_CELL_WIDTH, STORYBOARD_IMAGE_CELL_HEIGHT } from './scriptStoryboard';
import { storyboardImageNodesForScript } from './scriptStoryboardMembers';
import { scriptRowsOf } from './scriptStaleness';
import { scriptRowFingerprint } from './scriptRowFingerprint';
import { withScriptImageQuality } from './scriptRenderQuality';
import { scriptKeyframeStatePrompt, scriptKeyframeVisualContext, type ScriptKeyframePlanItem } from './scriptKeyframePlan';
import type { ScriptShotRefEntry } from './scriptShotRefs';
import { scriptReferenceResponsibility, scriptDirectorVisualContext, scriptSceneSpatialContext } from './scriptCreativeHandoff';

interface KeyframeImageSpec {
  rowKey: string;
  rowFingerprint: string;
  shotNumber: string;
  keyframePlan: ScriptKeyframePlanItem[];
  keyframeContext: string;
  assetReferences: ScriptShotRefEntry[];
  creativeHandoff: VideoCreativeHandoff;
}

function completedImage(node: CanvasNode | undefined): string | null {
  if (!node || node.data.isGenerating || node.data.canvas_auto_generate_once || node.data.generationError) return null;
  return typeof node.data.imageUrl === 'string' && node.data.imageUrl ? node.data.imageUrl : null;
}

/** Storyboard generation and video derivation share the same state-image nodes. */
export function ensureShotKeyframeNodes(params: {
  scriptNodeId: string;
  spec: KeyframeImageSpec;
  shotImageNode?: CanvasNode;
  generateImages: boolean;
}): string[] {
  const { scriptNodeId, spec, shotImageNode, generateImages } = params;
  const first = completedImage(shotImageNode);
  if (!shotImageNode || !first) return [];
  const store = useCanvasStore.getState();
  const absolute = resolveAbsolutePosition(shotImageNode, new Map(store.nodes.map(node => [node.id, node])));
  const baseRefs = Array.isArray(shotImageNode.data.referenceImageUrls)
    ? shotImageNode.data.referenceImageUrls.filter((url): url is string => typeof url === 'string' && url.length > 0) : [];
  const refs = [...new Set([first, ...baseRefs, ...spec.assetReferences.map(reference => reference.imageUrl)])];
  const referenceResponsibilities: NonNullable<VideoCreativeHandoff['referenceResponsibilities']> = refs.map((url, index) => {
    const asset = spec.assetReferences.find(reference => reference.imageUrl === url && reference.assetId);
    const role = index === 0 ? 'frame_design' : asset?.role ?? 'reference';
    return {
      scope: 'keyframe' as const, imageNumber: index + 1, role,
      name: index === 0 ? '本镜首图' : asset ? `${asset.roleLabel} ${asset.name}` : '参考图',
      ...(index === 0 ? { sourceNodeId: shotImageNode.id } : {}),
      ...scriptReferenceResponsibility(role),
    };
  });
  const creativeHandoff: VideoCreativeHandoff = { ...spec.creativeHandoff, referenceResponsibilities: [
    ...(spec.creativeHandoff.referenceResponsibilities ?? []).filter(reference => reference.scope === 'storyboard'),
    ...referenceResponsibilities,
  ] };
  const referenceInstructions = referenceResponsibilities.slice(1).map(reference =>
    `${reference.name} 引用@图片${reference.imageNumber}：${reference.responsibility}；${reference.prohibited}；本张姿态、位置和变化服从目标状态。`,
  ).join('\n');
  const existingForRow = store.nodes.filter(node => node.data.scriptShotKeyframeSourceNodeId === scriptNodeId
    && node.data.scriptShotKeyframeRowKey === spec.rowKey);
  const ids: string[] = [];
  spec.keyframePlan.forEach((item, index) => {
    const candidates = existingForRow.filter(node => node.data.scriptShotKeyframeIndex === index);
    const current = candidates.find(node => node.data.scriptShotKeyframeFingerprint === spec.rowFingerprint && completedImage(node))
      ?? candidates.find(node => node.data.scriptShotKeyframeFingerprint === spec.rowFingerprint) ?? candidates[0];
    const duplicateIds = candidates.filter(node => node.id !== current?.id).map(node => node.id);
    if (duplicateIds.length) useCanvasStore.getState().deleteNodes(duplicateIds);
    const context = [scriptDirectorVisualContext(creativeHandoff), scriptSceneSpatialContext(creativeHandoff), spec.keyframeContext].filter(Boolean).join('\n');
    const prompt = withScriptImageQuality(scriptKeyframeStatePrompt(referenceInstructions, item, index, context));
    const refreshed = current && (current.data.scriptShotKeyframeFingerprint !== spec.rowFingerprint
      || current.data.prompt !== prompt || JSON.stringify(current.data.referenceImageUrls) !== JSON.stringify(refs));
    const label = `分镜 #${spec.shotNumber} · 状态画面 ${index + 1}`;
    const patch = {
      label, displayName: label, prompt,
      model: shotImageNode.data.model, size: shotImageNode.data.size,
      requestAspectRatio: shotImageNode.data.requestAspectRatio, deliverySpec: shotImageNode.data.deliverySpec,
      count: 1, referenceImageUrl: refs[0], referenceImageUrls: refs,
      scriptShotKeyframeSourceNodeId: scriptNodeId, scriptShotKeyframeRowKey: spec.rowKey,
      scriptShotKeyframeFingerprint: spec.rowFingerprint, scriptShotKeyframeIndex: index,
      scriptShotKeyframeRole: item.role, scriptShotKeyframeState: item.state, scriptShotKeyframePurpose: item.purpose,
      scriptCreativeHandoff: creativeHandoff,
      ...(refreshed ? { imageUrl: null, previewImageUrl: null, generationBatch: null, generationError: null } : {}),
      ...(generateImages && !current?.data.isGenerating && (!completedImage(current) || refreshed)
        ? { canvas_auto_generate_once: true, generationError: null } : {}),
    } as Partial<ImageGenNodeData>;
    const live = useCanvasStore.getState();
    const byId = new Map(live.nodes.map(node => [node.id, node]));
    const position = current?.position ?? findStoryboardBlockOrigin({
      script: { ...absolute, width: STORYBOARD_IMAGE_CELL_WIDTH, height: STORYBOARD_IMAGE_CELL_HEIGHT },
      gridWidth: STORYBOARD_IMAGE_CELL_WIDTH, gridHeight: STORYBOARD_IMAGE_CELL_HEIGHT,
      occupied: live.nodes.map(node => ({ ...resolveAbsolutePosition(node, byId),
        width: node.measured?.width ?? STORYBOARD_IMAGE_CELL_WIDTH, height: node.measured?.height ?? STORYBOARD_IMAGE_CELL_HEIGHT })),
    });
    const id = current?.id ?? live.addNode(CANVAS_NODE_TYPES.imageGen, position, patch);
    if (!id) return;
    if (current) useCanvasStore.getState().updateNodeData(id, patch);
    ids.push(id);
  });
  const wanted = new Set(ids);
  const staleIds = useCanvasStore.getState().nodes.filter(node => node.data.scriptShotKeyframeSourceNodeId === scriptNodeId
    && node.data.scriptShotKeyframeRowKey === spec.rowKey && !wanted.has(node.id)).map(node => node.id);
  if (staleIds.length) useCanvasStore.getState().deleteNodes(staleIds);
  return ids;
}

export function ensureScriptKeyframeImages(scriptNodeId: string, generateImages: boolean): string[] {
  const rows = scriptRowsOf(scriptNodeId);
  const ledger = collectScriptAssetLedger(rows, scriptAssetImageNodes(scriptNodeId));
  const images = storyboardImageNodesForScript(scriptNodeId);
  const directorPlan = (useCanvasStore.getState().nodes.find((node) => node.id === scriptNodeId)?.data?.scriptResult as { director_plan?: FreezoneStoryDirectorPlan | null } | undefined)?.director_plan;
  const shots = buildScriptShotSpecs(rows, ledger, undefined, directorPlan);
  const queued: string[] = [];
  shots.forEach((shot, index) => {
    const shotImageNode = images.find(node => readScriptShotId(node.data) === shot.rowKey);
    const ids = ensureShotKeyframeNodes({ scriptNodeId, shotImageNode, generateImages, spec: {
      rowKey: shot.rowKey, rowFingerprint: scriptRowFingerprint(rows[index], index), shotNumber: shot.shotNumber,
      keyframePlan: shot.keyframePlan, keyframeContext: scriptKeyframeVisualContext(rows[index]),
      assetReferences: shot.references.filter(reference => reference.assetId),
      creativeHandoff: shot.creativeHandoff,
    } });
    queued.push(...ids.filter(id => useCanvasStore.getState().nodes.find(node => node.id === id)?.data.canvas_auto_generate_once));
  });
  return queued;
}
