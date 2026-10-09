// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { beforeEach, describe, expect, it } from 'vitest';
import type { FreezoneStoryScriptRow } from '@/api/scriptContract';
import { CANVAS_NODE_TYPES, type CanvasNode, type VideoCreativeHandoff } from '@/features/canvas/domain/canvasNodes';
import { useCanvasStore } from '@/stores/canvasStore';
import { buildScriptShotSpecs } from './scriptStoryboard';
import { ensureShotKeyframeNodes } from './scriptKeyframeImages';
import { scriptKeyframeVisualContext } from './scriptKeyframePlan';
import { scriptShotKeyframeReadiness } from './scriptShotKeyframes';

const opening = (): CanvasNode => ({
  id: 'opening', type: CANVAS_NODE_TYPES.imageGen, position: { x: 0, y: 0 },
  data: { imageUrl: '/opening.png', model: 'image-model', referenceImageUrls: ['/character.png', '/manual.png'] },
}) as CanvasNode;

function ensure(row: FreezoneStoryScriptRow) {
  const shot = buildScriptShotSpecs([row])[0];
  return ensureShotKeyframeNodes({ scriptNodeId: 'script', shotImageNode: useCanvasStore.getState().nodes[0],
    generateImages: true, spec: { ...shot, rowFingerprint: 'row-version',
      keyframeContext: scriptKeyframeVisualContext(row), assetReferences: shot.references } });
}

const independent = { role: 'spatial_reveal', generation_strategy: 'independent' as const,
  state: '女孩持剑，敌人化雾', framing: '女孩过肩看向敌人', purpose: '看清敌人的位置', required: true };
const row: FreezoneStoryScriptRow = { shot_no: 1, start_state: independent.state,
  character_1: '女孩', character_image_1: '/character.png',
  shot_prompt: '[画面构图：女孩正面全身] + [光影几何：月光]',
  keyframe_plan: [independent, { role: 'ending_state', state: '女孩放下剑，双脚站稳', purpose: '结束支撑', required: false }] };

describe('keyframe generation by reference purpose', () => {
  beforeEach(() => useCanvasStore.getState().setCanvasData([opening()], []));

  it('composes independent views from assets and manual references while state edits retain the opening', () => {
    const ids = ensure(row);
    const [view, edit] = ids.map(id => useCanvasStore.getState().nodes.find(node => node.id === id)!);
    expect(view.data.referenceImageUrls).toEqual(['/character.png', '/manual.png']);
    expect(view.data.prompt).toContain('女孩过肩看向敌人');
    expect(view.data.prompt).not.toMatch(/先看@图片1|女孩正面全身/);
    const handoff = view.data.scriptCreativeHandoff as VideoCreativeHandoff;
    expect(handoff.referenceResponsibilities?.filter(item => item.scope === 'keyframe').map(item => [item.imageNumber, item.role]))
      .toEqual([[1, 'character'], [2, 'reference']]);
    expect(view.data.prompt).toContain('角色 女孩 引用@图片1');
    expect(edit.data.referenceImageUrls).toEqual(['/opening.png', '/character.png', '/manual.png']);
    expect(edit.data.prompt).toContain('先看@图片1');
    expect(edit.data.prompt).toContain('角色 女孩 引用@图片2');
    expect(view.data.canvas_auto_generate_once).toBe(true);
    expect(edit.data.canvas_auto_generate_once).toBe(true);
  });

  it('can generate an independent composition without substituting the opening for absent assets', () => {
    useCanvasStore.getState().updateNodeData('opening', { referenceImageUrls: [] });
    const [id] = ensure({ keyframe_plan: [independent] });
    const data = useCanvasStore.getState().nodes.find(node => node.id === id)!.data;
    expect(data.referenceImageUrl).toBeNull();
    expect(data.referenceImageUrls).toEqual([]);
    expect(data.canvas_auto_generate_once).toBe(true);
    expect(data.prompt).not.toContain('@图片1');
  });

  it('reuses finished views and invalidates their result when the requested composition changes', () => {
    const [id] = ensure({ ...row, keyframe_plan: [independent] });
    useCanvasStore.getState().updateNodeData(id, { imageUrl: '/view.png', canvas_auto_generate_once: false });
    expect(ensure({ ...row, keyframe_plan: [independent] })).toEqual([id]);
    expect(useCanvasStore.getState().nodes.find(node => node.id === id)?.data.imageUrl).toBe('/view.png');
    ensure({ ...row, keyframe_plan: [{ ...independent, framing: '俯视女孩与敌人的间距' }] });
    expect(useCanvasStore.getState().nodes.find(node => node.id === id)?.data).toMatchObject({
      imageUrl: null, canvas_auto_generate_once: true, scriptShotKeyframeFraming: '俯视女孩与敌人的间距',
    });
  });

  it('keeps completed legacy images and their original prompt after a template update', () => {
    const editRow = { ...row, keyframe_plan: row.keyframe_plan?.slice(1) };
    const [id] = ensure(editRow);
    useCanvasStore.getState().updateNodeData(id, { imageUrl: '/legacy.png', prompt: '旧版生成提示词',
      scriptShotKeyframeStrategy: undefined, scriptShotKeyframeFraming: undefined, canvas_auto_generate_once: false });
    expect(ensure(editRow)).toEqual([id]);
    expect(useCanvasStore.getState().nodes.find(node => node.id === id)?.data).toMatchObject({
      imageUrl: '/legacy.png', prompt: '旧版生成提示词', canvas_auto_generate_once: false,
    });
  });

  it('keeps an incomplete required view visible, blocks automatic generation and allows correction', () => {
    const [id] = ensure({ ...row, keyframe_plan: [{ ...independent, framing: '' }] });
    const data = useCanvasStore.getState().nodes.find(node => node.id === id)!.data;
    expect(data.canvas_auto_generate_once).toBe(false);
    expect(data.generationError).toContain('缺少景别');
    const video = { id: 'video', type: CANVAS_NODE_TYPES.video, data: {}, position: { x: 0, y: 0 } } as CanvasNode;
    const graph = { nodes: [...useCanvasStore.getState().nodes, video], edges: [{ id: 'frame-edge', source: id,
      target: video.id, data: { role: 'scriptShotKeyframe' } }] };
    expect(scriptShotKeyframeReadiness(video, graph)).toMatchObject({ ok: false, pending: false });
    expect(ensure({ ...row, keyframe_plan: [independent] })).toEqual([id]);
    expect(useCanvasStore.getState().nodes.find(node => node.id === id)?.data).toMatchObject({
      canvas_auto_generate_once: true, generationError: null,
    });
  });
});
