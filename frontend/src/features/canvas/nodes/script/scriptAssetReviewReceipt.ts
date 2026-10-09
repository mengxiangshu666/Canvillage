import type { ScriptAsset } from './scriptAssets';
import { useCanvasStore } from '@/stores/canvasStore';

export interface ScriptAssetReviewReceipt {
  assetId: string;
  ownerId: string;
  imageUrl: string;
  revision: number;
  contentHash: string;
  status: 'passed' | 'blocked';
  checks: { clean: boolean; identity: boolean; structure: boolean };
  views: string[];
  notes: string;
}

export function currentAssetReview(data: Record<string, unknown>): ScriptAssetReviewReceipt | null {
  const receipt = data.scriptAssetVisualReview as Partial<ScriptAssetReviewReceipt> | undefined;
  if (!receipt || receipt.imageUrl !== (data.imageUrl ?? data.previewImageUrl)
    || typeof receipt.assetId !== 'string' || !receipt.assetId || receipt.assetId !== data.scriptAssetId
    || typeof receipt.ownerId !== 'string' || receipt.ownerId !== (data.scriptAssetOwnerId ?? '')
    || receipt.revision !== data.scriptAssetRevision || receipt.contentHash !== data.scriptAssetContentHash
    || data.isGenerating || data.canvas_auto_generate_once || data.generationError
    || !['passed', 'blocked'].includes(receipt.status ?? '')
    || !receipt.checks || typeof receipt.checks.clean !== 'boolean'
    || typeof receipt.checks.identity !== 'boolean' || typeof receipt.checks.structure !== 'boolean'
    || !Array.isArray(receipt.views) || !receipt.views.every(view => typeof view === 'string')
    || typeof receipt.notes !== 'string') return null;
  if (receipt.status === 'passed' && !Object.values(receipt.checks).every(Boolean)) return null;
  return receipt as ScriptAssetReviewReceipt;
}

export function saveAssetReview(asset: ScriptAsset, input: Pick<ScriptAssetReviewReceipt, 'status' | 'checks' | 'views' | 'notes'>): string | null {
  const store = useCanvasStore.getState();
  const node = store.nodes.find(candidate => candidate.id === asset.generatedNodeId);
  if (!node || !asset.imageUrl || (node.data.imageUrl ?? node.data.previewImageUrl) !== asset.imageUrl
    || node.data.scriptAssetRevision !== asset.revision || node.data.scriptAssetContentHash !== asset.contentHash
    || node.data.scriptAssetId !== asset.id || (node.data.scriptAssetOwnerId ?? '') !== (asset.generatedOwnerId ?? '')
    || node.data.isGenerating || node.data.canvas_auto_generate_once || node.data.generationError) return '图片已变更或尚未生成完成，请重新审看';
  if (input.status === 'passed' && !Object.values(input.checks).every(Boolean)) return '请完成画面检查';
  if (input.status === 'blocked' && !input.notes.trim()) return '请填写需要重做的问题';
  store.updateNodeData(node.id, { scriptAssetVisualReview: {
    ...input, views: [...new Set(input.views)], notes: input.notes.trim(),
    imageUrl: asset.imageUrl, revision: asset.revision, contentHash: asset.contentHash,
    assetId: asset.id, ownerId: asset.generatedOwnerId ?? '',
  } });
  return null;
}
