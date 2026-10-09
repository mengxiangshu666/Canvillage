import { beforeEach, expect, it } from 'vitest';
import type { FreezoneStoryScriptRow } from '@/api/scriptContract';
import { CANVAS_NODE_TYPES } from '@/features/canvas/domain/canvasNodes';
import { useCanvasStore } from '@/stores/canvasStore';
import { prepareScriptEndFrameDraft, scriptEndFramePrompt } from './scriptEndFrameDraft';

beforeEach(() => useCanvasStore.setState({ nodes: [], edges: [] }));

it('prepares an unpaid ending, reuses it, replaces outdated input and preserves manual pairing', () => {
  const state = useCanvasStore.getState();
  const firstId = state.addNode(CANVAS_NODE_TYPES.imageGen, { x: 0, y: 0 }, { imageUrl: '/first.png', model: 'image-model' });
  const videoId = state.addNode(CANVAS_NODE_TYPES.video, { x: 500, y: 0 }, { scriptShotId: 'shot:1' });
  state.addEdgeWithData(firstId, videoId, { role: 'scriptShotVideo' });
  const row = { end_state: '双脚落到右平台', prop_state_end: '滑板完整', start_state: '起跳', sound: '轮声' } as FreezoneStoryScriptRow;
  const prepare = () => {
    const graph = useCanvasStore.getState();
    prepareScriptEndFrameDraft(graph.nodes.find(n => n.id === videoId)!, graph.nodes.find(n => n.id === firstId)!, row, 'current');
  };
  prepare();
  prepare();
  let graph = useCanvasStore.getState();
  expect(graph.nodes).toHaveLength(3);
  const tail = graph.nodes.find(n => n.data.scriptShotEndFrameForVideo)!;
  expect(tail.data.canvas_auto_generate_once).toBe(false);
  expect(tail.data.imageUrl).toBeFalsy();
  expect(tail.data.referenceImageUrls).toEqual(['/first.png']);
  expect(tail.data.prompt).toContain('无噪点、颗粒');
  expect(tail.data.prompt).toContain('滑板完整');
  expect(tail.data.prompt).not.toContain('起跳');
  expect(tail.data.prompt).not.toContain('轮声');
  graph.updateNodeData(firstId, { imageUrl: '/new.png' });
  prepare();
  graph = useCanvasStore.getState();
  expect(graph.nodes).toHaveLength(4);
  expect(graph.nodes.some(n => n.id === tail.id)).toBe(true);
  expect(graph.edges.some(e => e.source === tail.id && e.target === videoId)).toBe(false);
  const manual = graph.addNode(CANVAS_NODE_TYPES.upload, { x: 0, y: 500 }, { imageUrl: '/manual.png' });
  graph.addEdgeWithData(manual, videoId, {});
  graph.updateNodeData(firstId, { imageUrl: '/third.png' });
  prepare();
  expect(useCanvasStore.getState().nodes).toHaveLength(5);
  expect(useCanvasStore.getState().edges.some(e => e.source === manual && e.target === videoId)).toBe(true);
});

it('requires an explicit ending rather than guessing it from action', () => {
  expect(scriptEndFramePrompt({ action: '跳跃' } as FreezoneStoryScriptRow)).toBe('');
});
