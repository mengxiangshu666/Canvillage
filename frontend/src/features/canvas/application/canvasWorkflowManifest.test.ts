// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from 'vitest';

import { CANVAS_NODE_TYPES } from '@/features/canvas/domain/canvasNodes';
import { CANVAS_STARTER_WORKFLOWS } from './starterWorkflows';
import { buildCanvasWorkflowManifest } from './canvasWorkflowManifest';

describe('canvas workflow manifest', () => {
  it('exposes every canvas node type and every starter workflow to Agent planning', () => {
    const manifest = buildCanvasWorkflowManifest();

    expect(manifest.commands).toEqual(expect.arrayContaining([
      expect.stringContaining('create_canvas_node'),
      expect.stringContaining('insert_starter_workflow'),
      expect.stringContaining('delete_node'),
      expect.stringContaining('update_node_prompt'),
      expect.stringContaining('move_node'),
      expect.stringContaining('remove_edge'),
    ]));
    expect(manifest.node_types.map((item) => item.node_type).sort())
      .toEqual(Object.values(CANVAS_NODE_TYPES).sort());
    expect(manifest.starter_workflows.map((item) => item.id).sort())
      .toEqual(CANVAS_STARTER_WORKFLOWS.map((workflow) => workflow.id).sort());
  });

  it('carries tool-level image powers that previously lived only in the canvas UI', () => {
    const manifest = buildCanvasWorkflowManifest();
    const imageGen = manifest.node_types.find((item) => item.node_type === CANVAS_NODE_TYPES.imageGen);

    expect(imageGen).toMatchObject({
      label: '图片节点',
      capabilities: expect.arrayContaining([
        'image-generation',
        'image-crop',
        'image-annotate',
        'storyboard-split-input',
      ]),
    });
  });

  it('exposes structured video executor mappings instead of capability labels only', () => {
    const manifest = buildCanvasWorkflowManifest();
    const video = manifest.node_types.find((item) => item.node_type === CANVAS_NODE_TYPES.video);
    expect(video?.capability_contracts).toEqual(expect.arrayContaining([
      expect.objectContaining({
        id: 'video-capture-first-frame',
        status: 'wired',
        execution: expect.objectContaining({
          kind: 'canvas_event',
          event: 'video-node/capture-frame',
        }),
        output_kinds: ['image'],
      }),
      expect.objectContaining({
        id: 'depth-motion-capture',
        status: 'not_wired',
        execution: expect.objectContaining({ kind: 'unwired' }),
      }),
    ]));
  });
});
