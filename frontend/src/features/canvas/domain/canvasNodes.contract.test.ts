import { describe, expect, it } from 'vitest';

import {
  CANVAS_NODE_TYPES,
  isAudioNode,
  isGroupNode,
  isScriptNode,
  isStoryboardGroupNode,
  isUploadNode,
  isVideoNode,
  resolveKnownNodeEdge,
  type CanvasNode,
} from './canvasNodes';

function node(type: string, data: Record<string, unknown>) {
  return {
    id: `${type}-1`,
    type,
    position: { x: 0, y: 0 },
    data,
  } as CanvasNode;
}

describe('canvasNodes contract', () => {
  it('keeps the persisted node type names stable', () => {
    expect(CANVAS_NODE_TYPES).toMatchObject({
      upload: 'uploadNode',
      group: 'groupNode',
      script: 'scriptNode',
      video: 'videoNode',
      audio: 'audioNode',
      storyboardGen: 'storyboardGenNode',
    });
  });

  it('narrows only the matching node contract', () => {
    const upload = node(CANVAS_NODE_TYPES.upload, { imageUrl: null, aspectRatio: '1:1' });
    const video = node(CANVAS_NODE_TYPES.video, { videoUrl: null, aspectRatio: '16:9' });
    const script = node(CANVAS_NODE_TYPES.script, {});
    const audio = node(CANVAS_NODE_TYPES.audio, {});

    expect(isUploadNode(upload)).toBe(true);
    expect(isVideoNode(video)).toBe(true);
    expect(isScriptNode(script)).toBe(true);
    expect(isAudioNode(audio)).toBe(true);
    expect(isVideoNode(upload)).toBe(false);
  });

  it('recognizes only explicit storyboard groups', () => {
    expect(
      isStoryboardGroupNode(
        node(CANVAS_NODE_TYPES.group, { label: 'group', storyboardGroup: true })
      )
    ).toBe(true);
    expect(
      isStoryboardGroupNode(node(CANVAS_NODE_TYPES.group, { label: 'group' }))
    ).toBe(false);
    expect(isGroupNode(node(CANVAS_NODE_TYPES.group, { label: 'group' }))).toBe(true);
  });

  it('rejects unusable measured node edges', () => {
    expect(resolveKnownNodeEdge(320)).toBe(320);
    expect(resolveKnownNodeEdge(0)).toBeNull();
    expect(resolveKnownNodeEdge(-1)).toBeNull();
    expect(resolveKnownNodeEdge(Number.NaN)).toBeNull();
    expect(resolveKnownNodeEdge(undefined)).toBeNull();
  });
});
