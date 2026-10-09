// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { beforeEach, describe, expect, it } from 'vitest';

import type { CanvasUserTemplateDetail } from '@/api/canvas';
import { addCanvasUserTemplateToCanvas } from '@/features/canvas/application/canvasUserTemplateActions';
import { useCanvasStore } from '@/stores/canvasStore';

const template: CanvasUserTemplateDetail = {
  id: 'ct_test',
  title: '雨夜追逐',
  description: '',
  schema: 'canvas_user_template.v1',
  node_count: 2,
  edge_count: 1,
  created_at: '2026-09-17T00:00:00Z',
  updated_at: '2026-09-17T00:00:00Z',
  nodes: [
    {
      key: 'image',
      type: 'imageGenNode',
      offset: { x: 0, y: 0 },
      data: { prompt: '雨夜街头' },
    },
    {
      key: 'video',
      type: 'videoNode',
      offset: { x: 500, y: 0 },
      data: { prompt: '角色向前奔跑' },
    },
  ],
  edges: [{ source: 'image', target: 'video', relation: 'references' }],
};

describe('canvas user template store action', () => {
  beforeEach(() => {
    useCanvasStore.setState({
      nodes: [],
      edges: [],
      history: { past: [], future: [] },
      selectedNodeId: null,
      dragHistorySnapshot: null,
      userEditsSinceHydrate: 0,
      lastMutationSource: null,
    });
  });

  it('inserts a template as one undoable canvas edit', () => {
    const created = addCanvasUserTemplateToCanvas(template, { x: 100, y: 200 });

    expect(created?.title).toBe('雨夜追逐');
    expect(created?.nodeIds).toHaveLength(2);
    const state = useCanvasStore.getState();
    expect(state.nodes).toHaveLength(2);
    expect(state.edges).toHaveLength(1);
    expect(state.history.past).toHaveLength(1);
    expect(state.selectedNodeId).toBe(created?.nodeIds[1]);
    expect(state.userEditsSinceHydrate).toBe(1);
    expect(state.lastMutationSource).toBe('user_edit');
    expect(state.edges[0]).toMatchObject({
      source: created?.nodeIds[0],
      target: created?.nodeIds[1],
      data: { relation: 'references' },
    });

    expect(state.undo()).toBe(true);
    expect(useCanvasStore.getState().nodes).toHaveLength(0);
    expect(useCanvasStore.getState().edges).toHaveLength(0);
  });
});
