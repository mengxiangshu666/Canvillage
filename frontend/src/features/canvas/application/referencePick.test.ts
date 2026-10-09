// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from 'vitest';

import { CANVAS_NODE_TYPES, type CanvasNode } from '../domain/canvasNodes';
import { collectReferencePickTargets, referenceKindOf } from './referencePick';

function node(id: string, type: CanvasNode['type'], data: Record<string, unknown>): CanvasNode {
  return { id, type, position: { x: 0, y: 0 }, data } as CanvasNode;
}

describe('reference picker rules', () => {
  const nodes: CanvasNode[] = [
    node('target-image', CANVAS_NODE_TYPES.imageGen, {}),
    node('target-video', CANVAS_NODE_TYPES.video, {}),
    node('image', CANVAS_NODE_TYPES.upload, { imageUrl: '/static/image.png' }),
    node('video', CANVAS_NODE_TYPES.video, { videoUrl: '/static/video.mp4' }),
    node('audio', CANVAS_NODE_TYPES.audio, { audioUrl: '/static/audio.mp3' }),
    node('text', CANVAS_NODE_TYPES.textAnnotation, { content: '雨夜古刹' }),
  ];

  it('detects the material actually exposed by each node', () => {
    expect(referenceKindOf(nodes[2])).toBe('image');
    expect(referenceKindOf(nodes[3])).toBe('video');
    expect(referenceKindOf(nodes[4])).toBe('audio');
    expect(referenceKindOf(nodes[5])).toBe('text');
  });

  it('limits image generation references to image and text', () => {
    const targets = collectReferencePickTargets(
      nodes,
      'target-image',
      CANVAS_NODE_TYPES.imageGen,
    );
    expect([...targets.candidates.keys()]).toEqual(['image', 'text']);
    expect(targets.rejections.get('video')).toContain('视频');
    expect(targets.rejections.get('audio')).toContain('音频');
  });

  it('lets video generation consume every supported media kind', () => {
    const targets = collectReferencePickTargets(
      nodes,
      'target-video',
      CANVAS_NODE_TYPES.video,
    );
    expect([...targets.candidates.keys()]).toEqual([
      'target-image',
      'image',
      'video',
      'audio',
      'text',
    ]);
  });
});
