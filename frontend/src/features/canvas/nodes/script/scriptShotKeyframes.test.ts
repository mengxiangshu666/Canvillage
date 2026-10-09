import { describe, expect, it } from 'vitest';
import { CANVAS_NODE_TYPES, type CanvasNode, type CanvasEdge } from '@/features/canvas/domain/canvasNodes';
import { scriptShotKeyframes, scriptShotFrameChangeReason, scriptShotKeyframeReadiness } from './scriptShotKeyframes';

function fixture() {
  const nodes = [
    { id: 'first', type: CANVAS_NODE_TYPES.upload, position: { x: 0, y: 0 }, data: { imageUrl: '/first.png', scriptShotId: 'shot:1' } },
    { id: 'last', type: CANVAS_NODE_TYPES.upload, position: { x: 100, y: 0 }, data: { imageUrl: '/last.png' } },
    { id: 'video', type: CANVAS_NODE_TYPES.video, position: { x: 200, y: 0 }, data: { genMode: 'firstLastFrame', scriptShotImageNodeId: 'first', scriptShotId: 'shot:1', scriptShotRowFingerprint: 'current', videoUrl: '/current.mp4' } },
  ] as CanvasNode[];
  const edges = [{ id: 'edge-first', source: 'first', target: 'video' }, { id: 'edge-last', source: 'last', target: 'video' }] as CanvasEdge[];
  return { nodes, edges };
}

describe('script first/last actual inputs', () => {
  it('does not treat a queued state keyframe reference image as a completed frame', () => {
    const graph = fixture();
    graph.nodes.push({ id: 'state', type: CANVAS_NODE_TYPES.imageGen, position: { x: 150, y: 0 }, data: { scriptShotKeyframeRowKey: 'shot:1', referenceImageUrl: '/first.png' } } as CanvasNode);
    graph.edges.push({ id: 'edge-state', source: 'state', target: 'video', data: { role: 'scriptShotKeyframe' } } as CanvasEdge);
    expect(scriptShotKeyframeReadiness(graph.nodes[2], graph)).toEqual({
      ok: false,
      pending: true,
      reason: '本镜状态关键帧尚未完成，视频会在关键帧全部出图后自动提交',
    });
    graph.nodes[3].data.imageUrl = '/state.png';
    graph.nodes[3].data.isGenerating = true;
    expect(scriptShotKeyframeReadiness(graph.nodes[2], graph).ok).toBe(false);
    graph.nodes[3].data.isGenerating = false;
    expect(scriptShotKeyframeReadiness(graph.nodes[2], graph).ok).toBe(true);
  });
  it('marks changed input images stale without invalidating a capture after its new output renders', () => {
    const graph = fixture();
    Object.assign(graph.nodes[2].data, { scriptShotFirstFrameUrl: '/first.png', scriptShotLastFrameUrl: '/last.png' });
    graph.nodes[2].data.scriptShotRenderedFrames = { videoUrl: '/current.mp4', firstFrameUrl: '/first.png', lastFrameUrl: '/last.png', rowFingerprint: 'current' };
    graph.nodes[1].data.captureMetadata = { source_kind: 'video_frame_capture', capture_mode: 'last', source_node_id: 'video', source_video_url: '/previous.mp4' };
    expect(scriptShotFrameChangeReason(graph.nodes[2], graph)).toBeNull();
    graph.nodes[1].data.imageUrl = '/changed.png';
    expect(scriptShotFrameChangeReason(graph.nodes[2], graph)).toContain('首尾帧输入已变化');
    graph.nodes[1].data.imageUrl = '/last.png';
    graph.nodes[0].data.imageUrl = '/new-first.png';
    expect(scriptShotFrameChangeReason(graph.nodes[2], graph)).toContain('首帧已变化');
  });
  it('does not associate an old output with newly queued inputs', () => {
    const graph = fixture();
    Object.assign(graph.nodes[2].data, { scriptShotFirstFrameUrl: '/first.png', scriptShotLastFrameUrl: '/last.png', scriptShotRenderedFrames: { videoUrl: '/old.mp4', firstFrameUrl: '/first.png', lastFrameUrl: '/last.png', rowFingerprint: 'current' } });
    expect(scriptShotFrameChangeReason(graph.nodes[2], graph)).toContain('当前视频');
  });
  it('accepts two completed images in visible order', () => {
    const graph = fixture();
    expect(scriptShotKeyframes(graph.nodes[2], graph)).toEqual({ ok: true, firstFrameUrl: '/first.png', lastFrameUrl: '/last.png' });
    graph.nodes[2].data.referenceOrder = ['last', 'first'];
    expect(scriptShotKeyframes(graph.nodes[2], graph).ok).toBe(false);
  });
  it('rejects an automatic ending based on another owner, old first image or old script', () => {
    const graph = fixture();
    Object.assign(graph.nodes[1].data, { scriptShotEndFrameForVideo: 'video', scriptShotEndFrameSourceUrl: '/first.png', scriptShotRowFingerprint: 'current' });
    expect(scriptShotKeyframes(graph.nodes[2], graph).ok).toBe(true);
    for (const patch of [{ scriptShotEndFrameForVideo: 'other' }, { scriptShotEndFrameSourceUrl: '/old.png' }, { scriptShotRowFingerprint: 'old' }]) {
      const original = { ...graph.nodes[1].data };
      Object.assign(graph.nodes[1].data, patch);
      expect(scriptShotKeyframes(graph.nodes[2], graph).ok).toBe(false);
      graph.nodes[1].data = original;
    }
  });
  it('rejects missing, failed, pending and reference-only images', () => {
    for (const patch of [{ imageUrl: '' }, { generationError: 'failed' }, { isGenerating: true }, { canvas_auto_generate_once: true }, { imageUrl: null, referenceImageUrl: '/ref.png' }]) {
      const graph = fixture();
      Object.assign(graph.nodes[1].data, patch);
      expect(scriptShotKeyframes(graph.nodes[2], graph).ok).toBe(false);
    }
  });
  it.each(['first', 'last'])('queued %s frame invalidates completed-film input review', id => {
    const graph = fixture();
    Object.assign(graph.nodes[2].data, { scriptShotFirstFrameUrl: '/first.png', scriptShotRenderedFrames: { videoUrl: '/current.mp4', firstFrameUrl: '/first.png', lastFrameUrl: '/last.png', rowFingerprint: 'current' } });
    graph.nodes.find(node => node.id === id)!.data.canvas_auto_generate_once = true;
    expect(scriptShotFrameChangeReason(graph.nodes[2], graph)).not.toBeNull();
  });
  it('does not use another shot or continuity reference as the desired ending', () => {
    const graph = fixture();
    graph.edges[1].data = { role: 'scriptShotContinuity' };
    expect(scriptShotKeyframes(graph.nodes[2], graph).ok).toBe(false);
    graph.edges[1].data = {};
    graph.nodes[1].data.scriptShotId = 'shot:2';
    expect(scriptShotKeyframes(graph.nodes[2], graph).ok).toBe(false);
  });
  it('accepts only an exact current-version capture from this shot', () => {
    const graph = fixture();
    graph.nodes[1].data.captureMetadata = { source_kind: 'video_frame_capture', capture_mode: 'last', source_node_id: 'video', source_video_url: '/current.mp4' };
    expect(scriptShotKeyframes(graph.nodes[2], graph).ok).toBe(true);
    graph.nodes[2].data.videoUrl = '/new.mp4';
    expect(scriptShotKeyframes(graph.nodes[2], graph).ok).toBe(false);
    graph.nodes[2].data.videoUrl = '/current.mp4';
    expect(scriptShotKeyframes(graph.nodes[2], graph, 'changed-script').ok).toBe(false);
  });
});
