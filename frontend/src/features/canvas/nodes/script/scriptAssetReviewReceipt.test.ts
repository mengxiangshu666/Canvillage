import { describe, expect, it } from 'vitest';
import { currentAssetReview, saveAssetReview } from './scriptAssetReviewReceipt';
import { assetContributesAsReference, collectScriptAssetLedger, scriptAssetImageNodes } from './scriptAssets';
import { useCanvasStore } from '@/stores/canvasStore';
import { CANVAS_NODE_TYPES } from '@/features/canvas/domain/canvasNodes';
import { buildScriptShotRefEntries } from './scriptShotRefs';
import { generateScriptAssetImages } from './scriptAssetGen';

describe('asset visual review provenance', () => {
  it('does not reintroduce a rejected bitmap through frame or asset aliases', () => {
    const before = useCanvasStore.getState();
    const row = { shot_no: 1, prop_tags: '滑板', character_1: '阿波', character_image_1: 'bad.png', reference: 'bad.png' };
    const asset = collectScriptAssetLedger([row], new Map()).props[0];
    try {
      useCanvasStore.getState().setCanvasData([{ id: 'asset', type: CANVAS_NODE_TYPES.imageGen, position: { x: 0, y: 0 }, data: { imageUrl: 'bad.png', scriptAssetId: asset.id, scriptAssetOwnerId: 'script', scriptAssetRevision: asset.revision, scriptAssetContentHash: asset.contentHash, scriptAssetIdentityLocks: asset.identityLocks } }], []);
      const current = collectScriptAssetLedger([row], scriptAssetImageNodes('script')).props[0];
      expect(saveAssetReview(current, { status: 'blocked', checks: { clean: false, identity: true, structure: true }, views: [], notes: '彩色噪点' })).toBeNull();
      const ledger = collectScriptAssetLedger([row], scriptAssetImageNodes('script'));
      expect(buildScriptShotRefEntries(row, ledger)).toEqual([]);
      expect(buildScriptShotRefEntries({ ...row, character_image_1: '' }, ledger)).toEqual([]);
      expect(buildScriptShotRefEntries({ ...row, character_image_1: 'good.png', reference: '' }, ledger)).toEqual([]);
      expect(buildScriptShotRefEntries({ ...row, character_image_1: '', reference: 'other-frame.png' }, ledger).map(entry => entry.imageUrl)).toEqual(['other-frame.png']);
      useCanvasStore.getState().updateNodeData('asset', { imageUrl: 'new.png' });
      const refreshed = collectScriptAssetLedger([row], scriptAssetImageNodes('script'));
      expect(buildScriptShotRefEntries(row, refreshed).map(entry => entry.imageUrl)).toContain('bad.png');
    } finally { useCanvasStore.getState().setCanvasData(before.nodes, before.edges); }
  });
  it('rejects incomplete and stale receipts', () => {
    const receipt = { assetId: 'character:a', ownerId: 'script', imageUrl: 'a.png', revision: 1, contentHash: 'hash', status: 'passed', checks: { clean: true, identity: true, structure: true }, views: ['front'], notes: '' };
    const data = { scriptAssetId: receipt.assetId, scriptAssetOwnerId: receipt.ownerId, imageUrl: 'a.png', scriptAssetRevision: 1, scriptAssetContentHash: 'hash', scriptAssetVisualReview: receipt };
    expect(currentAssetReview(data)).toEqual(receipt);
    for (const update of [{ scriptAssetId: 'other' }, { scriptAssetOwnerId: 'other' }, { scriptAssetId: null }]) expect(currentAssetReview({ ...data, ...update })).toBeNull();
    for (const update of [{ imageUrl: 'b.png' }, { scriptAssetRevision: 2 }, { scriptAssetContentHash: 'new' }, { isGenerating: true }, { scriptAssetVisualReview: { ...receipt, checks: { ...receipt.checks, clean: false } } }]) expect(currentAssetReview({ ...data, ...update })).toBeNull();
  });

  it('saves only current generated assets and removes rejected images from reference use', () => {
    const before = useCanvasStore.getState();
    const rows = [{ shot_no: 1, prop_tags: '滑板', prop_descriptions: { 滑板: '青色板面' } }];
    const asset = collectScriptAssetLedger(rows, new Map()).props[0];
    try {
      useCanvasStore.getState().setCanvasData([{ id: 'asset', type: CANVAS_NODE_TYPES.imageGen, position: { x: 0, y: 0 }, data: { imageUrl: 'a.png', scriptAssetId: asset.id, scriptAssetOwnerId: 'script', scriptAssetRevision: asset.revision, scriptAssetContentHash: asset.contentHash, scriptAssetIdentityLocks: asset.identityLocks } }], []);
      const current = collectScriptAssetLedger(rows, scriptAssetImageNodes('script')).props[0];
      expect(assetContributesAsReference(current)).toBe(true);
      const input = { status: 'blocked' as const, checks: { clean: false, identity: true, structure: true }, views: [], notes: '暗部随机噪点' };
      expect(saveAssetReview(current, { ...input, notes: '' })).toBeTruthy();
      expect(saveAssetReview(current, input)).toBeNull();
      useCanvasStore.getState().updateNodeData('asset', { scriptAssetOwnerId: 'other-script' });
      expect(saveAssetReview(current, input)).toBeTruthy();
      expect(currentAssetReview(useCanvasStore.getState().nodes[0].data)).toBeNull();
      useCanvasStore.getState().updateNodeData('asset', { scriptAssetOwnerId: 'script' });
      expect(assetContributesAsReference(collectScriptAssetLedger(rows, scriptAssetImageNodes('script')).props[0])).toBe(false);
      useCanvasStore.getState().updateNodeData('asset', { imageUrl: 'b.png' });
      expect(saveAssetReview(current, input)).toBeTruthy();
      expect(collectScriptAssetLedger(rows, scriptAssetImageNodes('script')).props[0].visualReview).toBeNull();
    } finally { useCanvasStore.getState().setCanvasData(before.nodes, before.edges); }
  });

  it('restores review after JSON reload and prevents character fallback after rejection', () => {
    const before = useCanvasStore.getState();
    const row = { shot_no: 1, character_1: '阿波', character_image_1: 'old-character.png' };
    const asset = collectScriptAssetLedger([row], new Map()).characters[0];
    try {
      useCanvasStore.getState().setCanvasData([{ id: 'asset', type: CANVAS_NODE_TYPES.imageGen, position: { x: 0, y: 0 }, data: { imageUrl: 'new-character.png', scriptAssetId: asset.id, scriptAssetOwnerId: 'script', scriptAssetRevision: asset.revision, scriptAssetContentHash: asset.contentHash, scriptAssetIdentityLocks: asset.identityLocks } }], []);
      const current = collectScriptAssetLedger([row], scriptAssetImageNodes('script')).characters[0];
      expect(saveAssetReview(current, { status: 'blocked', checks: { clean: false, identity: true, structure: true }, views: [], notes: '毛发噪点' })).toBeNull();
      const serialized = JSON.stringify({ nodes: useCanvasStore.getState().nodes, edges: [] });
      useCanvasStore.getState().setCanvasData([], []);
      const restored = JSON.parse(serialized);
      useCanvasStore.getState().setCanvasData(restored.nodes, restored.edges);
      const ledger = collectScriptAssetLedger([row], scriptAssetImageNodes('script'));
      expect(ledger.characters[0].visualReview?.notes).toBe('毛发噪点');
      expect(buildScriptShotRefEntries(row, ledger)).toEqual([]);
    } finally { useCanvasStore.getState().setCanvasData(before.nodes, before.edges); }
  });

  it('prepares only current rejected notes for asset regeneration without submitting', () => {
    const before = useCanvasStore.getState();
    const rows = [{ shot_no: 1, prop_tags: '滑板' }];
    const asset = collectScriptAssetLedger(rows, new Map()).props[0];
    try {
      useCanvasStore.getState().setCanvasData([
        { id: 'script', type: CANVAS_NODE_TYPES.script, position: { x: 0, y: 0 }, data: {} },
        { id: 'asset', type: CANVAS_NODE_TYPES.imageGen, position: { x: 0, y: 0 }, data: { imageUrl: 'board.png', scriptAssetId: asset.id, scriptAssetOwnerId: 'script', scriptAssetRevision: asset.revision, scriptAssetContentHash: asset.contentHash, scriptAssetIdentityLocks: asset.identityLocks } },
      ], []);
      const current = collectScriptAssetLedger(rows, scriptAssetImageNodes('script')).props[0];
      saveAssetReview(current, { status: 'blocked', checks: { clean: false, identity: true, structure: true }, views: [], notes: '暗部噪点，保留板面纹理' });
      const prepare = () => generateScriptAssetImages({ scriptNodeId: 'script', assets: [current], scriptSize: { width: 800, height: 400 }, config: { model: 'test-image' }, generateImages: false, style: '剪纸风格' });
      expect(prepare().ok).toBe(true);
      let data = useCanvasStore.getState().nodes.find(node => node.id === 'asset')!.data;
      expect(data.prompt).toContain('暗部噪点，保留板面纹理');
      expect(data.prompt).toContain('不作为替换角色身份');
      expect(data.prompt).toContain('剪纸风格');
      expect(data.prompt).toContain('RENDER QUALITY:');
      expect(data.canvas_auto_generate_once).toBe(false);
      useCanvasStore.getState().updateNodeData('asset', { imageUrl: 'new.png' });
      prepare();
      data = useCanvasStore.getState().nodes.find(node => node.id === 'asset')!.data;
      expect(data.prompt).not.toContain('暗部噪点，保留板面纹理');
    } finally { useCanvasStore.getState().setCanvasData(before.nodes, before.edges); }
  });
});
