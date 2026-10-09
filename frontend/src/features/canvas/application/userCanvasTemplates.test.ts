// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from 'vitest';

import type { CanvasUserTemplateDetail } from '@/api/canvas';
import type {
  CanvasEdge,
  CanvasNode,
  CanvasNodeData,
} from '@/features/canvas/domain/canvasNodes';

import {
  createCanvasUserTemplate,
  extractCanvasUserTemplateGraph,
  sanitizeCanvasTemplateNodeData,
} from './userCanvasTemplates';

function node(
  id: string,
  type: CanvasNode['type'],
  position: { x: number; y: number },
  data: Partial<CanvasNodeData> = {},
  extra: Partial<CanvasNode> = {},
): CanvasNode {
  return {
    id,
    type,
    position,
    data: data as CanvasNodeData,
    ...extra,
  };
}

describe('user canvas templates', () => {
  it('extracts a selected subtree without generated media or in-flight state', () => {
    const nodes = [
      node(
        'group-1',
        'groupNode',
        { x: 100, y: 200 },
        {},
        { width: 800, height: 500 },
      ),
      node(
        'image-1',
        'imageGenNode',
        { x: 60, y: 80 },
        {
          prompt: '雨夜街头',
          model: 'image-model',
          imageUrl: 'https://example.test/generated.png',
          isGenerating: true,
          generationError: 'upstream failed',
        },
        { parentId: 'group-1' },
      ),
      node('video-1', 'videoNode', { x: 1000, y: 200 }, {
        prompt: '角色向前奔跑',
        videoUrl: 'https://example.test/generated.mp4',
      }),
    ];
    const edges: CanvasEdge[] = [{
      id: 'edge-1',
      source: 'image-1',
      target: 'video-1',
      sourceHandle: 'source',
      targetHandle: 'target',
      type: 'disconnectableEdge',
      data: { relation: 'references' },
    }];

    const graph = extractCanvasUserTemplateGraph({
      nodes,
      edges,
      selectedNodeIds: ['group-1', 'video-1'],
    });

    expect(graph).not.toBeNull();
    expect(graph?.nodes).toHaveLength(3);
    expect(graph?.nodes[0]).toMatchObject({
      key: 'n1',
      type: 'groupNode',
      offset: { x: 0, y: 0 },
      width: 800,
      height: 500,
    });
    expect(graph?.nodes[1]).toMatchObject({
      key: 'n2',
      type: 'imageGenNode',
      parent_key: 'n1',
      offset: { x: 60, y: 80 },
      data: {
        prompt: '雨夜街头',
        model: 'image-model',
      },
    });
    expect(graph?.nodes[1].data).not.toHaveProperty('imageUrl');
    expect(graph?.nodes[1].data).not.toHaveProperty('isGenerating');
    expect(graph?.nodes[1].data).not.toHaveProperty('generationError');
    expect(graph?.nodes[2].data).toEqual({ prompt: '角色向前奔跑' });
    expect(graph?.edges).toEqual([{
      source: 'n2',
      target: 'n3',
      source_handle: 'source',
      target_handle: 'target',
      relation: 'references',
    }]);
  });

  it('rebuilds node ids, parent coordinates, and edge endpoints on insertion', () => {
    const template: CanvasUserTemplateDetail = {
      id: 'ct_test',
      title: '雨夜追逐',
      description: '',
      schema: 'canvas_user_template.v1',
      node_count: 3,
      edge_count: 1,
      created_at: '2026-09-17T00:00:00Z',
      updated_at: '2026-09-17T00:00:00Z',
      nodes: [
        {
          key: 'group',
          type: 'groupNode',
          offset: { x: 0, y: 0 },
          width: 800,
          height: 500,
          data: {},
        },
        {
          key: 'image',
          type: 'imageGenNode',
          parent_key: 'group',
          offset: { x: 60, y: 80 },
          data: {
            prompt: '雨夜街头',
            imageUrl: 'https://example.test/should-be-stripped.png',
          },
        },
        {
          key: 'video',
          type: 'videoNode',
          offset: { x: 900, y: 0 },
          data: {
            prompt: '角色向前奔跑',
            isGenerating: true,
          },
        },
      ],
      edges: [{
        source: 'image',
        target: 'video',
        relation: 'references',
      }],
    };

    const created = createCanvasUserTemplate(template, { x: 1000, y: 500 });

    expect(created).not.toBeNull();
    expect(created?.nodes).toHaveLength(3);
    const group = created?.nodes.find((item) => item.type === 'groupNode');
    const image = created?.nodes.find((item) => item.type === 'imageGenNode');
    const video = created?.nodes.find((item) => item.type === 'videoNode');
    expect(group).toBeDefined();
    expect(image).toBeDefined();
    expect(video).toBeDefined();
    if (!group || !image || !video) return;
    expect(group.position).toEqual({ x: 1000, y: 500 });
    expect(image.parentId).toBe(group.id);
    expect(image.position).toEqual({ x: 60, y: 80 });
    expect(video.position).toEqual({ x: 1900, y: 500 });
    expect(new Set(created?.nodes.map((item) => item.id)).size).toBe(3);
    expect(created?.nodes.map((item) => item.id)).not.toContain('group');
    expect(image.data).toMatchObject({ prompt: '雨夜街头' });
    expect(video.data).toMatchObject({ prompt: '角色向前奔跑' });
    expect(video.data.isGenerating).not.toBe(true);
    expect(created?.edges[0]).toMatchObject({
      source: image.id,
      target: video.id,
      sourceHandle: 'source',
      targetHandle: 'target',
      data: { relation: 'references' },
    });
  });

  it('drops runtime fields from node data before persistence', () => {
    expect(sanitizeCanvasTemplateNodeData({
      prompt: 'keep',
      imageUrl: 'drop',
      productionMetadata: { status: 'drop' },
      nested: { value: 'keep' },
    })).toEqual({
      prompt: 'keep',
      nested: { value: 'keep' },
    });
  });
});
