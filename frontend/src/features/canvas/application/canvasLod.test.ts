// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  CANVAS_ONLY_RENDER_VISIBLE_ELEMENTS,
  LOD_SHELL_EXEMPT_TYPES,
  LOW_DETAIL_ZOOM_THRESHOLD,
  isCanvasGestureActive,
  isCanvasHydrateBurstActive,
  isCanvasLowDetail,
  isCanvasMotionSuppressed,
  getNodeMediaCurrentTime,
  isNodeMediaActive,
  isLowDetailZoom,
  notifyCanvasHydrateViewport,
  onCanvasHydrateViewport,
  requestShellUpgrade,
  setNodeMediaActive,
  setCanvasGestureActive,
  setCanvasLowDetail,
  subscribeCanvasMotionState,
} from './canvasLod';

describe('canvas LOD', () => {
  let frames: FrameRequestCallback[];

  beforeEach(() => {
    frames = [];
    vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => {
      frames.push(callback);
      return frames.length;
    });
    setCanvasGestureActive(false);
    setCanvasLowDetail(false);
  });

  afterEach(() => {
    setCanvasGestureActive(false);
    for (let index = 0; index < 50 && frames.length > 0; index += 1) {
      const callbacks = frames.splice(0);
      callbacks.forEach((callback) => callback(0));
    }
    vi.unstubAllGlobals();
  });

  const tick = () => {
    const callbacks = frames.splice(0);
    callbacks.forEach((callback) => callback(0));
  };

  it('uses a strict low-detail threshold', () => {
    expect(isLowDetailZoom(LOW_DETAIL_ZOOM_THRESHOLD - 0.01)).toBe(true);
    expect(isLowDetailZoom(LOW_DETAIL_ZOOM_THRESHOLD)).toBe(false);
    expect(isLowDetailZoom(1)).toBe(false);
    expect(isLowDetailZoom(Number.NaN)).toBe(false);
  });

  it('keeps viewport virtualization enabled in every zoom mode', () => {
    expect(CANVAS_ONLY_RENDER_VISIBLE_ELEMENTS).toBe(true);
  });

  it('tracks gesture and low-detail state independently', () => {
    setCanvasGestureActive(true);
    setCanvasLowDetail(true);
    expect(isCanvasGestureActive()).toBe(true);
    expect(isCanvasLowDetail()).toBe(true);
  });

  it('suppresses decorative motion during gestures and at low detail', () => {
    expect(isCanvasMotionSuppressed()).toBe(false);

    setCanvasGestureActive(true);
    expect(isCanvasMotionSuppressed()).toBe(true);
    setCanvasGestureActive(false);
    expect(isCanvasMotionSuppressed()).toBe(false);

    setCanvasLowDetail(true);
    expect(isCanvasMotionSuppressed()).toBe(true);
    setCanvasLowDetail(false);
    expect(isCanvasMotionSuppressed()).toBe(false);
  });

  it('notifies motion-state subscribers only when the flag flips', () => {
    const listener = vi.fn();
    const unsubscribe = subscribeCanvasMotionState(listener);

    setCanvasGestureActive(true);
    expect(listener).toHaveBeenCalledTimes(1);
    // 同值写入必须早退，否则每条边都会在每个拖动帧重渲染一次。
    setCanvasGestureActive(true);
    expect(listener).toHaveBeenCalledTimes(1);

    setCanvasGestureActive(false);
    expect(listener).toHaveBeenCalledTimes(2);

    setCanvasLowDetail(true);
    expect(listener).toHaveBeenCalledTimes(3);

    unsubscribe();
    setCanvasLowDetail(false);
    expect(listener).toHaveBeenCalledTimes(3);
  });

  it('tracks active media synchronously for LOD decisions', () => {
    const nodeId = 'video-node-playback-test';

    expect(isNodeMediaActive(nodeId)).toBe(false);
    setNodeMediaActive(nodeId, true, 1.25);
    expect(isNodeMediaActive(nodeId)).toBe(true);
    expect(getNodeMediaCurrentTime(nodeId)).toBe(1.25);
    setNodeMediaActive(nodeId, false);
    expect(isNodeMediaActive(nodeId)).toBe(false);
    expect(getNodeMediaCurrentTime(nodeId)).toBeNull();
  });

  it('keeps editing-sensitive nodes out of shells', () => {
    expect([...LOD_SHELL_EXEMPT_TYPES].sort()).toEqual([
      'beatContextNode',
      'groupNode',
      'skillNode',
    ]);
  });

  it('notifies the mounted canvas of the hydrate zoom', () => {
    const listener = vi.fn();
    const unsubscribe = onCanvasHydrateViewport(listener);
    notifyCanvasHydrateViewport(0.2);
    expect(listener).toHaveBeenCalledWith(0.2);
    unsubscribe();
    notifyCanvasHydrateViewport(1);
    expect(listener).toHaveBeenCalledTimes(1);
  });

  it('keeps the hydrate shell burst active for two frames', async () => {
    const { beginCanvasHydrateBurst } = await import('./canvasLod');
    beginCanvasHydrateBurst();
    expect(isCanvasHydrateBurstActive()).toBe(true);
    tick();
    expect(isCanvasHydrateBurstActive()).toBe(true);
    tick();
    expect(isCanvasHydrateBurstActive()).toBe(false);
  });

  it('grants at most three shell upgrades per frame and pauses during gestures', () => {
    const grants: number[] = [];
    for (let index = 0; index < 7; index += 1) {
      requestShellUpgrade(() => grants.push(index));
    }
    setCanvasGestureActive(true);
    tick();
    expect(grants).toEqual([]);
    setCanvasGestureActive(false);
    tick();
    expect(grants).toEqual([0, 1, 2]);
    tick();
    expect(grants).toEqual([0, 1, 2, 3, 4, 5]);
    tick();
    expect(grants).toEqual([0, 1, 2, 3, 4, 5, 6]);
  });
});
