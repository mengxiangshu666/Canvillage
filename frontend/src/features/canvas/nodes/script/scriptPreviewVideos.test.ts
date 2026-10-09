import { expect, it } from 'vitest';
import type { CanvasNode } from '@/features/canvas/domain/canvasNodes';
import { scriptRowFingerprint } from './scriptRowFingerprint';
import { scriptPreviewVideos } from './scriptPreviewVideos';

it('uses only a unique current completed video per script shot in row order', () => {
  const rows = [{ shot_id: 'a', duration: 4 }, { shot_id: 'b', duration: 5 }];
  const node = (id: string, index: number, patch = {}): CanvasNode => ({ id, type: 'videoNode', position: { x: 0, y: 0 },
    data: { scriptShotSourceNodeId: 'script', scriptShotId: rows[index].shot_id,
      scriptShotRowFingerprint: scriptRowFingerprint(rows[index], index), videoUrl: `/${id}.mp4`, ...patch } });
  const graph = { nodes: [node('b', 1), node('a', 0)], edges: [] };
  expect(scriptPreviewVideos('script', rows, graph)).toEqual(['/a.mp4', '/b.mp4']);
  for (const patch of [{ isGenerating: true }, { generationError: 'failed' }, { canvas_auto_generate_once: true }, { scriptShotRowFingerprint: 'old' }, { scriptShotSourceNodeId: 'other' }]) {
    expect(scriptPreviewVideos('script', rows, { ...graph, nodes: [node('a', 0, patch)] })).toEqual([null, null]);
  }
  expect(scriptPreviewVideos('script', rows, { ...graph, nodes: [node('a1', 0), node('a2', 0)] })).toEqual([null, null]);
});

it('excludes a completed clip after its submitted first or last image changes', () => {
  const rows = [{ shot_id: 'a', duration: 4 }];
  const nodes = [
    { id: 'first', type: 'uploadNode', position: { x: 0, y: 0 }, data: { imageUrl: '/first.png' } },
    { id: 'last', type: 'uploadNode', position: { x: 0, y: 0 }, data: { imageUrl: '/last.png' } },
    { id: 'video', type: 'videoNode', position: { x: 0, y: 0 }, data: { scriptShotSourceNodeId: 'script', scriptShotId: 'a', scriptShotRowFingerprint: scriptRowFingerprint(rows[0]), videoUrl: '/done.mp4', genMode: 'firstLastFrame', scriptShotImageNodeId: 'first', scriptShotFirstFrameUrl: '/first.png', scriptShotLastFrameUrl: '/last.png' } },
  ] as CanvasNode[];
  nodes[2].data.scriptShotRenderedFrames = { videoUrl: '/done.mp4', firstFrameUrl: '/first.png', lastFrameUrl: '/last.png', rowFingerprint: scriptRowFingerprint(rows[0]) };
  const graph = { nodes, edges: [{ id: 'a', source: 'first', target: 'video' }, { id: 'b', source: 'last', target: 'video' }] };
  expect(scriptPreviewVideos('script', rows, graph)).toEqual(['/done.mp4']);
  nodes[1].data.imageUrl = '/new.png';
  expect(scriptPreviewVideos('script', rows, graph)).toEqual([null]);
  nodes[1].data.imageUrl = '/last.png';
  nodes[0].data.isGenerating = true;
  expect(scriptPreviewVideos('script', rows, graph)).toEqual([null]);
});
