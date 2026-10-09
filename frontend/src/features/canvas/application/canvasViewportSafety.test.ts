import { describe, expect, it } from 'vitest';

import {
  canvasSafeFitPadding,
  safeAreaAdjustedFlowCenter,
  viewportForRects,
} from './canvasViewportSafety';

describe('canvas viewport safety', () => {
  it('builds asymmetric pixel padding for fitView', () => {
    expect(
      canvasSafeFitPadding({ top: 12, right: 420, bottom: 88, left: 320 }, 20),
    ).toEqual({
      top: '32px',
      right: '440px',
      bottom: '108px',
      left: '340px',
    });
  });

  it('moves a target into the center of the remaining visible area', () => {
    expect(
      safeAreaAdjustedFlowCenter({
        centerX: 800,
        centerY: 500,
        zoom: 1,
        insets: { top: 20, right: 420, bottom: 80, left: 320 },
      }),
    ).toEqual({ x: 850, y: 530 });
  });

  it('converts screen-space insets through the current zoom', () => {
    expect(
      safeAreaAdjustedFlowCenter({
        centerX: 600,
        centerY: 300,
        zoom: 2,
        insets: { top: 0, right: 0, bottom: 100, left: 200 },
      }),
    ).toEqual({ x: 550, y: 325 });
  });

  it('sanitizes invalid inset and zoom values', () => {
    expect(
      safeAreaAdjustedFlowCenter({
        centerX: 10,
        centerY: 20,
        zoom: 0,
        insets: { top: Number.NaN, right: -4, bottom: 0, left: 0 },
      }),
    ).toEqual({ x: 10, y: 20 });
  });
});

describe('viewportForRects', () => {
  const insets = { top: 0, right: 0, bottom: 52, left: 0 };

  /** Screen-space box of a flow rect under a viewport — what the user actually sees. */
  function screenBox(
    rect: { x: number; y: number; width: number; height: number },
    viewport: { x: number; y: number; zoom: number },
  ) {
    return {
      left: rect.x * viewport.zoom + viewport.x,
      top: rect.y * viewport.zoom + viewport.y,
      right: (rect.x + rect.width) * viewport.zoom + viewport.x,
      bottom: (rect.y + rect.height) * viewport.zoom + viewport.y,
    };
  }

  it('brings every rect of a derived row on screen, not just the first', () => {
    // The regression this guards: focusing only the first member left the rest
    // outside the viewport, and off-viewport nodes never mount (and therefore
    // never run their auto-generate effect).
    const rects = [0, 1, 2, 3].map((index) => ({
      x: 1200 + index * 628,
      y: 400,
      width: 580,
      height: 360,
    }));
    const viewport = viewportForRects({
      rects,
      viewportWidth: 1600,
      viewportHeight: 900,
      insets,
    })!;

    expect(viewport).toBeTruthy();
    for (const rect of rects) {
      const box = screenBox(rect, viewport);
      expect(box.left).toBeGreaterThanOrEqual(-0.5);
      expect(box.right).toBeLessThanOrEqual(1600.5);
      expect(box.top).toBeGreaterThanOrEqual(-0.5);
      expect(box.bottom).toBeLessThanOrEqual(900.5);
    }
  });

  it('never zooms past maxZoom for a single small node', () => {
    const viewport = viewportForRects({
      rects: [{ x: 0, y: 0, width: 580, height: 380 }],
      viewportWidth: 1600,
      viewportHeight: 900,
      insets,
      maxZoom: 0.72,
    })!;
    expect(viewport.zoom).toBe(0.72);
  });

  it('keeps the block clear of asymmetric safe insets', () => {
    const withDrawer = { top: 0, right: 420, bottom: 52, left: 320 };
    const rects = [{ x: 0, y: 0, width: 580, height: 360 }];
    const viewport = viewportForRects({
      rects,
      viewportWidth: 1600,
      viewportHeight: 900,
      insets: withDrawer,
    })!;
    const box = screenBox(rects[0], viewport);
    expect(box.left).toBeGreaterThanOrEqual(withDrawer.left);
    expect(box.right).toBeLessThanOrEqual(1600 - withDrawer.right + 0.5);
    expect(box.bottom).toBeLessThanOrEqual(900 - withDrawer.bottom + 0.5);
  });

  it('returns null while the canvas has no measured size yet', () => {
    // Callers fall back to a single-node focus in this window instead of
    // computing a degenerate viewport.
    expect(
      viewportForRects({
        rects: [{ x: 0, y: 0, width: 580, height: 360 }],
        viewportWidth: 0,
        viewportHeight: 0,
        insets,
      }),
    ).toBeNull();
    expect(viewportForRects({ rects: [], viewportWidth: 1600, viewportHeight: 900, insets })).toBeNull();
  });

  it('never returns a zero or negative zoom for a huge block', () => {
    const viewport = viewportForRects({
      rects: [{ x: 0, y: 0, width: 60000, height: 40000 }],
      viewportWidth: 1600,
      viewportHeight: 900,
      insets,
    })!;
    expect(viewport.zoom).toBeGreaterThan(0);
  });
});
