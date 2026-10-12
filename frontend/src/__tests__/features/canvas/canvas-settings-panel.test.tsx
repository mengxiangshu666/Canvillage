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
      edgeFlowColor: '#f472b6',
      edgeFlowSpeed: 2,
      edgeFlowSize: 1.5,
      edgeFlowWidth: 8,
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

  it('persists independent flow choices and can return to following the line', () => {
    render(<CanvasSettingsPanel onClose={vi.fn()} />);
    fireEvent.change(screen.getByLabelText('自定义流光色彩'), { target: { value: '#abcdef' } });
    fireEvent.change(screen.getByRole('slider', { name: '流动速度' }), { target: { value: '2' } });
    fireEvent.change(screen.getByRole('slider', { name: '光点大小' }), { target: { value: '1.5' } });
    fireEvent.change(screen.getByRole('slider', { name: '光点粗细' }), { target: { value: '8' } });
    fireEvent.click(screen.getByRole('button', { name: '连线色彩：樱粉' }));

    const choices = { edgeColor: '#f472b6', edgeFlowColor: '#abcdef', edgeFlowSpeed: 2, edgeFlowSize: 1.5, edgeFlowWidth: 8 };
    expect(useCanvasViewSettingsStore.getState()).toMatchObject(choices);
    expect(JSON.parse(window.localStorage.getItem('canvas.viewSettings.v1')!)).toMatchObject(choices);
    expect(screen.getByRole('switch', { name: '流光跟随连线颜色' })).toHaveAttribute('aria-checked', 'false');

    fireEvent.click(screen.getByRole('switch', { name: '流光跟随连线颜色' }));
    expect(useCanvasViewSettingsStore.getState().edgeFlowColor).toBeNull();
    expect(screen.getByLabelText('自定义流光色彩')).toHaveValue('#f472b6');
    fireEvent.click(screen.getByRole('button', { name: '流光色彩：极光青' }));
    expect(useCanvasViewSettingsStore.getState().edgeFlowColor).toBe('#22d3ee');
  });

  it('bounds numeric flow settings and rejects invalid colors and non-finite numbers', () => {
    const settings = useCanvasViewSettingsStore.getState();
    settings.setEdgeFlowSpeed(100);
    settings.setEdgeFlowSize(-2);
    settings.setEdgeFlowWidth(100);
    expect(useCanvasViewSettingsStore.getState()).toMatchObject({ edgeFlowSpeed: 4, edgeFlowSize: 0.25, edgeFlowWidth: 12 });
    settings.setEdgeFlowColor('url(javascript:bad)');
    settings.setEdgeFlowSpeed(Number.NaN);
    settings.setEdgeFlowSize(Number.POSITIVE_INFINITY);
    settings.setEdgeFlowWidth(Number.NEGATIVE_INFINITY);
    expect(useCanvasViewSettingsStore.getState()).toMatchObject({ edgeFlowColor: null, edgeFlowSpeed: 1, edgeFlowSize: 1, edgeFlowWidth: 4 });
  });

  it('migrates saved settings that predate independent flow controls', async () => {
    window.localStorage.setItem('canvas.viewSettings.v1', JSON.stringify({ edgeColor: '#60a5fa' }));
    vi.resetModules();
    const { useCanvasViewSettingsStore: restored } = await import('@/features/canvas/ui/canvasViewSettingsStore');
    expect(restored.getState()).toMatchObject({ edgeColor: '#60a5fa', edgeFlowColor: null, edgeFlowSpeed: 1, edgeFlowSize: 1, edgeFlowWidth: 4 });
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
