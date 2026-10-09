// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { CanvasSettingsPanel } from '@/features/canvas/ui/CanvasSettingsPanel';
import {
  DEFAULT_CANVAS_VIEW_SETTINGS,
  resolveCanvasGridRenderMetrics,
  useCanvasViewSettingsStore,
} from '@/features/canvas/ui/canvasViewSettingsStore';
import { useSnapAlignStore } from '@/features/canvas/snap-align/snapAlignStore';
import { useTrackpadPanStore } from '@/features/canvas/trackpad-pan/trackpadPanStore';
import { useSettingsStore } from '@/stores/settingsStore';

describe('CanvasSettingsPanel', () => {
  beforeEach(() => {
    window.localStorage.clear();
    useCanvasViewSettingsStore.getState().reset();
    useSnapAlignStore.setState({ enabled: false, guides: { vertical: [], horizontal: [] } });
    useTrackpadPanStore.setState({ enabled: true });
    useSettingsStore.setState({ showNodePrice: true, canvasEdgeRoutingMode: 'spline' });
  });

  it('writes display and interaction choices into the live canvas stores', () => {
    render(<CanvasSettingsPanel onClose={vi.fn()} />);

    fireEvent.click(screen.getByRole('switch', { name: '网格底纹' }));
    fireEvent.click(screen.getByRole('switch', { name: '吸附到网格' }));
    fireEvent.click(screen.getByRole('switch', { name: '导航小地图' }));
    fireEvent.click(screen.getByRole('switch', { name: '性能数据' }));
    fireEvent.click(screen.getByRole('switch', { name: '智能对齐' }));
    fireEvent.click(screen.getByRole('button', { name: '智能避让' }));
    fireEvent.change(screen.getByLabelText('自定义画布颜色'), {
      target: { value: '#334455' },
    });
    fireEvent.click(screen.getByRole('button', { name: '连线色彩：樱粉' }));

    expect(useCanvasViewSettingsStore.getState()).toMatchObject({
      canvasTheme: 'custom',
      customCanvasColor: '#334455',
      edgeColor: '#f472b6',
      showGrid: false,
      snapToGrid: true,
      minimapPinned: true,
      performanceHudEnabled: true,
    });
    expect(useSnapAlignStore.getState().enabled).toBe(true);
    expect(useSettingsStore.getState().canvasEdgeRoutingMode).toBe('smartOrthogonal');
  });

  it('restores the product defaults without touching canvas content', () => {
    useCanvasViewSettingsStore.setState({
      canvasTheme: 'softGray',
      customCanvasColor: '#334455',
      edgeColor: '#22d3ee',
      showGrid: false,
      gridGap: 12,
      snapToGrid: true,
      minimapPinned: true,
      performanceHudEnabled: true,
    });
    useSnapAlignStore.setState({ enabled: true });
    useTrackpadPanStore.setState({ enabled: false });
    useSettingsStore.setState({ showNodePrice: false, canvasEdgeRoutingMode: 'orthogonal' });

    render(<CanvasSettingsPanel onClose={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: '恢复默认' }));

    expect(useCanvasViewSettingsStore.getState()).toMatchObject(DEFAULT_CANVAS_VIEW_SETTINGS);
    expect(useSnapAlignStore.getState().enabled).toBe(false);
    expect(useTrackpadPanStore.getState().enabled).toBe(true);
    expect(useSettingsStore.getState()).toMatchObject({
      showNodePrice: true,
      canvasEdgeRoutingMode: 'spline',
    });
  });

  it('keeps grid dots readable and reduces density when zoomed out', () => {
    expect(resolveCanvasGridRenderMetrics(20, 2, 1)).toEqual({ gap: 20, dotSize: 2 });
    expect(resolveCanvasGridRenderMetrics(20, 2, 0.48)).toEqual({
      gap: 40,
      dotSize: 2 / 0.48,
    });
    expect(resolveCanvasGridRenderMetrics(20, 2, 0.25)).toEqual({
      gap: 80,
      dotSize: 8,
    });
  });
});
