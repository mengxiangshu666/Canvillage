import { describe, expect, it } from 'vitest';
import { CANVAS_NODE_TYPES, type CanvasNode, type CanvasEdge, type VideoShotContractFacts } from '@/features/canvas/domain/canvasNodes';
import { compileScriptVideoReferences, scriptVideoReferenceBlock, scriptVideoReferencePrompt, scriptVideoReferenceFacts } from './scriptShotVideoReferences';
import { scriptShotContractFactsSnapshot } from './scriptShotVideos';

describe('script video reference roles at submission', () => {
  const graph = {
    nodes: [
      ...['opening', 'state', 'character', 'scene', 'prop'].map(id => ({
        id, type: CANVAS_NODE_TYPES.upload, position: { x: 0, y: 0 },
        data: { imageUrl: `/${id}.png`, ...(id === 'state' ? {
          scriptShotKeyframeState: '右手释放[栏杆]后，左脚仍支撑在板面', scriptShotKeyframePurpose: '锁定释放结果',
        } : {}) },
      })),
      { id: 'video', type: CANVAS_NODE_TYPES.video, position: { x: 0, y: 0 },
        data: { scriptShotSourceNodeId: 'script', referenceOrder: ['opening', 'state', 'character', 'scene', 'prop'] } },
    ] as CanvasNode[],
    edges: ['opening', 'state', 'character', 'scene', 'prop'].map(id => ({
      id: `${id}-edge`, source: id, target: 'video', data: {
        role: id === 'opening' ? 'scriptShotVideo' : id === 'state' ? 'scriptShotKeyframe' : 'scriptShotAssetReference',
        label: ({ character: '角色 阿波', scene: '场景 屋顶', prop: '道具 滑板' } as Record<string, string>)[id],
      },
    })) as CanvasEdge[],
  };

  it('replaces whole nested blocks, preserves motion and is stable across recompilation and reordering', () => {
    const motion = '[主体动作：阿波握住[青色滑板 + 银色桥架]，松手后停顿，继续滑行；用户描述[视频参考用途：这是正文中的例子]]';
    const raw = `${motion}\n[视频参考用途：@图片9状态[旧接触]，旧角色引用@图片8]\n[用户说明：保留衣摆余势]`;
    const compiled = scriptVideoReferencePrompt(raw, 'video', graph);
    expect(compiled.startsWith('[视频参考用途：')).toBe(true);
    expect(compiled).toContain('@图片2是本镜状态关键帧');
    expect(compiled).toContain('右手释放[栏杆]');
    expect(compiled).toContain('角色阿波引用@图片3');
    expect(compiled).toContain('场景屋顶引用@图片4');
    expect(compiled).toContain('道具滑板引用@图片5');
    expect(compiled).toContain(motion);
    expect(compiled).toContain('[用户说明：保留衣摆余势]');
    expect(compiled).not.toMatch(/旧接触|旧角色|@图片[89]/);
    expect(scriptVideoReferencePrompt(compiled, 'video', graph)).toBe(compiled);
    const reordered = { ...graph, nodes: graph.nodes.map(node => node.id === 'video'
      ? { ...node, data: { ...node.data, referenceOrder: ['opening', 'character', 'state', 'prop', 'scene'] } } : node) };
    const updated = scriptVideoReferencePrompt(compiled, 'video', reordered);
    expect(updated).toContain('角色阿波引用@图片2');
    expect(updated).toContain('@图片3是本镜状态关键帧');
    expect(updated).toContain('道具滑板引用@图片4');
    expect(updated).toContain('场景屋顶引用@图片5');
    expect(compileScriptVideoReferences(compiled, 'video', reordered).references?.map(item => [item.imageNumber, item.sourceNodeId]))
      .toEqual([[1, 'opening'], [2, 'character'], [3, 'state'], [4, 'prop'], [5, 'scene']]);
    expect(updated.match(/\[视频参考用途：/g)).toHaveLength(2);
    expect(updated).toContain(motion);
  });

  it('reads nested role blocks completely and preserves incomplete input', () => {
    const block = '[视频参考用途：角色[阿波]，状态[已分离[栏杆]]]';
    expect(scriptVideoReferenceBlock(`前文 ${block} 后文`)?.text).toBe(block);
    expect(scriptVideoReferenceBlock('[视频参考用途：未闭合')).toBeNull();
    expect(scriptVideoReferenceBlock('[用户描述：[视频参考用途：嵌套正文]]')).toBeNull();
    expect(scriptVideoReferencePrompt('手写提示词', 'manual', graph)).toBe('手写提示词');
  });

  it('records actual video numbering separately from storyboard input and keeps source snapshots stable', () => {
    const compiled = compileScriptVideoReferences('动作', 'video', graph);
    expect(compiled.references?.map(item => [item.scope, item.imageNumber, item.role, item.sourceNodeId])).toEqual([
      ['video', 1, 'opening_frame', 'opening'], ['video', 2, 'state_frame', 'state'],
      ['video', 3, 'character', 'character'], ['video', 4, 'scene', 'scene'], ['video', 5, 'prop', 'prop'],
    ]);
    expect(compiled.references?.[1].responsibility).toContain('锁定释放结果');
    expect(compiled.references?.[1].prohibited).toContain('不覆盖开场');
    const storyboard = { scope: 'storyboard' as const, imageNumber: 1, role: 'character' as const, name: '阿波', responsibility: '锁身份', prohibited: '不替代动作' };
    const facts = { shotId: 'S1', creativeHandoff: { cutReason: '切到落点', referenceResponsibilities: [storyboard] } } as VideoShotContractFacts;
    const saved = scriptVideoReferenceFacts(facts, compiled.references)!;
    expect(saved.creativeHandoff?.referenceResponsibilities).toEqual([storyboard, ...compiled.references!]);
    expect(saved.creativeHandoff?.cutReason).toBe('切到落点');
    expect(scriptShotContractFactsSnapshot(saved)).toBe(scriptShotContractFactsSnapshot(facts));
    expect(scriptVideoReferenceFacts(saved, [])?.creativeHandoff?.referenceResponsibilities).toEqual([storyboard]);
    expect(scriptVideoReferenceFacts(undefined, compiled.references)).toBeUndefined();
    expect(scriptShotContractFactsSnapshot(scriptVideoReferenceFacts({ shotId: 'old' } as VideoShotContractFacts, compiled.references))).toBe(scriptShotContractFactsSnapshot({ shotId: 'old' }));
  });

  it('removes deleted and pending frames from numbering, then renumbers after completion', () => {
    const pending = { ...graph,
      nodes: graph.nodes.map(node => node.id === 'state' ? { ...node, type: CANVAS_NODE_TYPES.imageGen, data: { ...node.data, imageUrl: null, referenceImageUrl: '/opening.png', scriptShotKeyframeRowKey: 'S1' } } : node),
      edges: graph.edges.filter(edge => edge.source !== 'prop'),
    };
    const current = compileScriptVideoReferences('[视频参考用途：旧道具引用@图片5]动作', 'video', pending);
    expect(current.references?.map(item => [item.imageNumber, item.sourceNodeId])).toEqual([[1, 'opening'], [2, 'character'], [3, 'scene']]);
    expect(current.prompt).not.toMatch(/状态关键帧|旧道具|@图片[45]/);
    const completed = { ...pending, nodes: pending.nodes.map(node => node.id === 'state' ? { ...node, data: { ...node.data, imageUrl: '/state.png' } } : node) };
    const result = compileScriptVideoReferences(current.prompt, 'video', completed);
    expect(result.references?.[1]).toMatchObject({ imageNumber: 2, role: 'state_frame' });
    expect(result.references?.[2]).toMatchObject({ imageNumber: 3, role: 'character' });
  });
});
