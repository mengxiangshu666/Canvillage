// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  CanvasFpsMeter,
  isCanvasPerformanceDebugAvailable,
} from '@/features/canvas/ui/CanvasFpsMeter';

describe('CanvasFpsMeter', () => {
  beforeEach(() => {
    vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) =>
      window.setTimeout(() => callback(performance.now()), 16),
    );
    vi.stubGlobal('cancelAnimationFrame', (handle: number) => window.clearTimeout(handle));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('only runs the performance HUD after the user enables it', () => {
    render(
      <CanvasFpsMeter
        nodeCount={12}
        edgeCount={7}
        visibleNodeCount={5}
        renderCount={3}
      />,
    );

    expect(screen.queryByTestId('canvas-performance-hud')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '开启 FPS 显示' }));

    const hud = screen.getByTestId('canvas-performance-hud');
    expect(hud).toHaveTextContent('FPS');
    expect(hud).toHaveTextContent(/节点\s*12/);
    expect(hud).toHaveTextContent(/边\s*7/);
    expect(hud).toHaveTextContent(/可视\s*5/);
    expect(hud).toHaveTextContent(/渲染\s*3/);

    fireEvent.click(screen.getByRole('button', { name: '关闭 FPS 显示' }));
    expect(screen.queryByTestId('canvas-performance-hud')).not.toBeInTheDocument();
  });

  it('keeps the production HUD hidden unless the debug query is explicit', () => {
    expect(isCanvasPerformanceDebugAvailable('', false)).toBe(false);
    expect(isCanvasPerformanceDebugAvailable('?__canvas_perf=1', false)).toBe(true);
  });
});
