import { expect, it } from 'vitest';
import { scriptPreviewFrames } from '@/features/canvas/nodes/script/scriptPreviewFrames';
import { buildScriptRowSnapshots } from '@/features/canvas/nodes/script/scriptStaleness';
import type { CanvasNode, CanvasEdge } from '@/features/canvas/domain/canvasNodes';

const rows = [{ shot_no: 1, duration: 2, shot_prompt: '楼缘', reference: '/input.png' }, { shot_no: 2, duration: 3, shot_prompt: '落地' }];
const snapshots = buildScriptRowSnapshots(rows);
function member(index: number, patch: Record<string, unknown> = {}): CanvasNode {
  const row = snapshots[index];
  return { id: `image${index}`, type: 'imageGenNode', position: { x: 0, y: 0 }, data: { scriptRowKey: row.rowKey, scriptRowPrompt: row.prompt, scriptRowReference: row.reference, scriptRowAssetSnapshot: row.assetRevision, imageUrl: `/shot${index}.png`, ...patch } } as CanvasNode;
}
function graph(members: CanvasNode[]) {
  return { nodes: [{ id: 'script', type: 'scriptNode', position: { x: 0, y: 0 }, data: {} } as CanvasNode, ...members], edges: members.map(node => ({ id: `edge${node.id}`, source: 'script', target: node.id, data: { role: 'storyboard' } } as CanvasEdge)) };
}
it('uses stable row identities and script ownership instead of canvas order', () => {
  const state = graph([member(1), member(0)]);
  state.nodes.push({ ...member(0), id: 'foreign', data: { scriptRowKey: snapshots[0].rowKey, imageUrl: '/foreign.png' } });
  expect(scriptPreviewFrames('script', rows, state).map(frame => frame.url)).toEqual(['/shot0.png', '/shot1.png']);
});
it('rejects stale, failed, generating and duplicate results without using their input portraits', () => {
  for (const patch of [{ scriptRowPrompt: '旧内容' }, { generationError: '失败' }, { isGenerating: true }, { canvas_auto_generate_once: true }, { imageUrl: null, referenceImageUrl: '/portrait.png' }]) {
    expect(scriptPreviewFrames('script', rows, graph([member(0, patch)]))[0].url).toBeNull();
  }
  const duplicate = { ...member(0), id: 'duplicate' };
  expect(scriptPreviewFrames('script', rows, graph([member(0), duplicate]))[0]).toEqual({ url: null, label: '这一镜有多个分镜结果，请先确认使用哪张' });
});
it('labels references before generation and reflects regenerated output', () => {
  expect(scriptPreviewFrames('script', rows, graph([]))[0]).toEqual({ url: '/input.png', label: '脚本参考帧 · 尚无生成分镜' });
  expect(scriptPreviewFrames('script', rows, graph([member(0, { imageUrl: '/new.png' })]))[0].url).toBe('/new.png');
});
