// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab

import { afterEach, describe, expect, it, vi } from 'vitest';
import type { BorderBeamOptions } from 'border-beam-vanilla';

import {
  canvasNodeRuntimeIsActive,
  createCanvasNodeBorderBeamManager,
} from './canvasNodeBorderBeam';

describe('canvas node border beam', () => {
  afterEach(() => {
    vi.useRealTimers();
    document.body.innerHTML = '';
  });

  it('uses the same colorful beam engine only for selected or running nodes', async () => {
    const stage = document.createElement('div');
    const selected = document.createElement('div');
    selected.className = 'react-flow__node selected';
    const idle = document.createElement('div');
    idle.className = 'react-flow__node';
    stage.append(selected, idle);
    document.body.append(stage);

    const selectedController = {
      element: document.createElement('div'),
      update: vi.fn(),
      setActive: vi.fn(),
      destroy: vi.fn(),
    };
    const attach = vi.fn((_element: HTMLElement, _options: BorderBeamOptions) => (
      selectedController
    ));
    const manager = createCanvasNodeBorderBeamManager(stage, attach);

    expect(attach).toHaveBeenCalledOnce();
    expect(attach.mock.calls[0]?.[1]).toMatchObject({
      size: 'md',
      colorVariant: 'colorful',
      theme: 'dark',
      strength: 0.9,
      duration: 1.96,
    });
    expect(selected.querySelector('.canvas-node-live-beam')).toBeInTheDocument();
    expect(idle.querySelector('.canvas-node-live-beam')).toBeNull();
    expect(selectedController.setActive).toHaveBeenLastCalledWith(true);

    idle.classList.add('canvas-node-generating');
    manager.sync();
    expect(attach).toHaveBeenCalledTimes(2);
    expect(idle.querySelector('.canvas-node-live-beam')).toHaveAttribute(
      'data-state',
      'running',
    );

    manager.destroy();
  });

  it('fades out cleanly and resumes without creating a second controller', async () => {
    vi.useFakeTimers();
    const stage = document.createElement('div');
    const node = document.createElement('div');
    node.className = 'react-flow__node selected';
    stage.append(node);
    document.body.append(stage);
    const controller = {
      element: document.createElement('div'),
      update: vi.fn(),
      setActive: vi.fn(),
      destroy: vi.fn(),
    };
    const attach = vi.fn((_element: HTMLElement, _options: BorderBeamOptions) => controller);
    const manager = createCanvasNodeBorderBeamManager(stage, attach);

    node.classList.remove('selected');
    manager.sync();
    expect(controller.setActive).toHaveBeenLastCalledWith(false);
    expect(node.querySelector('.canvas-node-live-beam')).toHaveAttribute(
      'data-state',
      'fading',
    );

    node.classList.add('selected');
    manager.sync();
    vi.advanceTimersByTime(600);
    expect(attach).toHaveBeenCalledOnce();
    expect(controller.destroy).not.toHaveBeenCalled();

    manager.setSuspended(true);
    vi.advanceTimersByTime(600);
    expect(controller.destroy).toHaveBeenCalledOnce();
    expect(node.querySelector('.canvas-node-live-beam')).toBeNull();
    manager.destroy();
  });

  it('recognizes every real asynchronous node state without treating idle data as active', () => {
    expect(canvasNodeRuntimeIsActive({ isGenerating: true })).toBe(true);
    expect(canvasNodeRuntimeIsActive({ isUploading: true })).toBe(true);
    expect(canvasNodeRuntimeIsActive({ isAnalyzing: true })).toBe(true);
    expect(canvasNodeRuntimeIsActive({ isSeparatingAv: true })).toBe(true);
    expect(canvasNodeRuntimeIsActive({ isGenerating: false, generationStartedAt: 1 })).toBe(false);
    expect(canvasNodeRuntimeIsActive({ generationError: 'failed' })).toBe(false);
  });
});
