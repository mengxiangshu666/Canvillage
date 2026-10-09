// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  beginCanvasInteraction,
  resetCanvasInteractionPerformanceForTests,
} from './canvasInteractionPerformance';

describe('canvas interaction performance mode', () => {
  afterEach(() => {
    resetCanvasInteractionPerformanceForTests();
    document.body.innerHTML = '';
    vi.restoreAllMocks();
  });

  it('keeps performance mode active until the last interaction ends', () => {
    const endPan = beginCanvasInteraction('canvas-pan');
    const endDrag = beginCanvasInteraction('node-drag');

    expect(document.documentElement.dataset.villageInteracting).toBe('true');
    expect(document.documentElement.dataset.villageInteractionKind).toBe('node-drag');

    endDrag();
    expect(document.documentElement.dataset.villageInteracting).toBe('true');
    expect(document.documentElement.dataset.villageInteractionKind).toBe('canvas-pan');

    endPan();
    expect(document.documentElement.dataset.villageInteracting).toBeUndefined();
  });

  it('pauses playing node previews and schedules their resume', () => {
    vi.spyOn(window, 'requestAnimationFrame').mockImplementation((callback) => {
      callback(16);
      return 1;
    });
    const node = document.createElement('div');
    node.className = 'village-canvas-stage';
    node.innerHTML = '<div class="react-flow__node"><video></video></div>';
    document.body.append(node);
    const video = node.querySelector('video')!;
    Object.defineProperty(video, 'paused', { configurable: true, value: false });
    const pause = vi.spyOn(video, 'pause').mockImplementation(() => undefined);
    const play = vi.spyOn(video, 'play').mockResolvedValue(undefined);

    const end = beginCanvasInteraction('companion-drag');
    expect(pause).toHaveBeenCalledOnce();
    end();
    expect(play).toHaveBeenCalledOnce();
  });
});
