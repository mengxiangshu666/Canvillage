import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  isCanvasMeasurementDeferred,
  onCanvasMeasurementResume,
  setCanvasGestureActive,
  setCanvasLowDetail,
} from './canvasLod';

describe('canvas LOD measurement gate', () => {
  beforeEach(() => {
    setCanvasGestureActive(false);
    setCanvasLowDetail(false);
  });

  afterEach(() => {
    setCanvasGestureActive(false);
    setCanvasLowDetail(false);
  });

  it('defers layout measurements while a gesture or low-detail mode is active', () => {
    expect(isCanvasMeasurementDeferred()).toBe(false);
    setCanvasGestureActive(true);
    expect(isCanvasMeasurementDeferred()).toBe(true);
    setCanvasGestureActive(false);
    expect(isCanvasMeasurementDeferred()).toBe(false);

    setCanvasLowDetail(true);
    expect(isCanvasMeasurementDeferred()).toBe(true);
    setCanvasLowDetail(false);
    expect(isCanvasMeasurementDeferred()).toBe(false);
  });

  it('notifies only when all measurement deferrals have ended', () => {
    const listener = vi.fn();
    const unsubscribe = onCanvasMeasurementResume(listener);

    setCanvasGestureActive(true);
    setCanvasLowDetail(true);
    setCanvasGestureActive(false);
    expect(listener).not.toHaveBeenCalled();

    setCanvasLowDetail(false);
    expect(listener).toHaveBeenCalledTimes(1);

    setCanvasLowDetail(false);
    setCanvasGestureActive(false);
    expect(listener).toHaveBeenCalledTimes(1);

    unsubscribe();
    setCanvasGestureActive(true);
    setCanvasGestureActive(false);
    expect(listener).toHaveBeenCalledTimes(1);
  });
});
