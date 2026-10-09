// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useEffect, useRef, useState } from 'react';
import { Activity } from 'lucide-react';

import {
  CANVAS_CONTROL_GLASS_CLASS,
  CANVAS_CONTROL_ICON_BUTTON_ACTIVE_CLASS,
  CANVAS_CONTROL_ICON_BUTTON_CLASS,
} from './canvasControlStyles';

type CanvasFpsMeterProps = {
  nodeCount?: number;
  edgeCount?: number;
  visibleNodeCount?: number;
  renderCount?: number;
  enabled?: boolean;
  onEnabledChange?: (enabled: boolean) => void;
};

type CanvasPerformanceSample = {
  fps: number;
  p95FrameMs: number;
  droppedFrames: number;
  longTasks: number;
};

export function isCanvasPerformanceDebugAvailable(
  search = typeof window === 'undefined' ? '' : window.location.search,
  development = import.meta.env.DEV,
): boolean {
  return development || new URLSearchParams(search).get('__canvas_perf') === '1';
}

/**
 * 画布右上角的轻量性能 HUD：默认只渲染一个按钮；点击开启后才启动 rAF 统计 FPS，
 * 同时展示节点/连线/可视节点/渲染次数，便于精准定位大画布卡顿点。
 */
export function CanvasFpsMeter({
  nodeCount = 0,
  edgeCount = 0,
  visibleNodeCount,
  renderCount,
  enabled: controlledEnabled,
  onEnabledChange,
}: CanvasFpsMeterProps) {
  const [internalEnabled, setInternalEnabled] = useState(false);
  const enabled = controlledEnabled ?? internalEnabled;
  const [sample, setSample] = useState<CanvasPerformanceSample | null>(null);
  const rafRef = useRef(0);

  useEffect(() => {
    if (!enabled) {
      setSample(null);
      return;
    }
    let lastFrameAt: number | null = null;
    let frameDurations: number[] = [];
    let longTasks = 0;
    let windowStart = performance.now();
    let observer: PerformanceObserver | null = null;
    try {
      observer = new PerformanceObserver((list) => {
        longTasks += list.getEntries().length;
      });
      observer.observe({ entryTypes: ['longtask'] });
    } catch {
      observer = null;
    }
    const tick = (now: number) => {
      if (lastFrameAt !== null) frameDurations.push(now - lastFrameAt);
      lastFrameAt = now;
      const elapsed = now - windowStart;
      if (elapsed >= 1000 && frameDurations.length > 0) {
        const sorted = [...frameDurations].sort((a, b) => a - b);
        const p50 = sorted[Math.floor(sorted.length * 0.5)] ?? 16.7;
        const p95 = sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * 0.95))] ?? p50;
        const average = frameDurations.reduce((sum, value) => sum + value, 0)
          / frameDurations.length;
        const dropThreshold = Math.max(16.7, p50 * 1.75);
        setSample({
          fps: Math.round(1000 / Math.max(average, 0.1)),
          p95FrameMs: p95,
          droppedFrames: frameDurations.filter((value) => value > dropThreshold).length,
          longTasks,
        });
        frameDurations = [];
        longTasks = 0;
        windowStart = now;
      }
      rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);
    return () => {
      cancelAnimationFrame(rafRef.current);
      observer?.disconnect();
    };
  }, [enabled]);

  const fps = sample?.fps ?? null;
  const fpsColor =
    sample == null
      ? 'text-text-muted'
      : sample.p95FrameMs <= 8
        ? 'text-emerald-400'
        : sample.p95FrameMs <= 16.7
          ? 'text-amber-400'
          : 'text-red-400';

  return (
    <div
      className="canvas-fps-meter-dock nopan nowheel pointer-events-auto group absolute left-3 top-3 z-30 flex items-center gap-1.5"
      onPointerDown={(event) => event.stopPropagation()}
    >
      {enabled && (
        <div
          className={`flex items-center gap-2 rounded-full px-2.5 py-1 text-[11px] font-medium tabular-nums ${CANVAS_CONTROL_GLASS_CLASS}`}
          data-testid="canvas-performance-hud"
        >
          <span className="flex items-center gap-1">
            <span className={fpsColor}>{fps ?? '--'}</span>
            <span className="text-text-muted">FPS</span>
          </span>
          <span className="h-3 w-px bg-white/12" aria-hidden="true" />
          <span title="最近一秒最慢 5% 帧的耗时">
            <span className="text-text-muted">P95</span>
            <span className="ml-1 text-text">{sample?.p95FrameMs.toFixed(1) ?? '--'}ms</span>
          </span>
          <span title="最近一秒明显掉帧次数">
            <span className="text-text-muted">掉帧</span>
            <span className="ml-1 text-text">{sample?.droppedFrames ?? 0}</span>
          </span>
          <span title="最近一秒超过 50ms 的主线程任务">
            <span className="text-text-muted">长任务</span>
            <span className="ml-1 text-text">{sample?.longTasks ?? 0}</span>
          </span>
          <span className="h-3 w-px bg-white/12" aria-hidden="true" />
          <span title="画布节点总数">
            <span className="text-text-muted">节点</span>
            <span className="ml-1 text-text">{nodeCount}</span>
          </span>
          <span title="画布连线总数">
            <span className="text-text-muted">边</span>
            <span className="ml-1 text-text">{edgeCount}</span>
          </span>
          {typeof visibleNodeCount === 'number' && (
            <span title="当前视口估算可见节点数">
              <span className="text-text-muted">可视</span>
              <span className="ml-1 text-text">{visibleNodeCount}</span>
            </span>
          )}
          {typeof renderCount === 'number' && (
            <span title="Canvas 组件本次挂载后的渲染次数">
              <span className="text-text-muted">渲染</span>
              <span className="ml-1 text-text">{renderCount}</span>
            </span>
          )}
        </div>
      )}
      <button
        type="button"
        onClick={() => {
          const next = !enabled;
          if (controlledEnabled === undefined) setInternalEnabled(next);
          onEnabledChange?.(next);
        }}
        className={`${CANVAS_CONTROL_ICON_BUTTON_CLASS} ${
          enabled
            ? CANVAS_CONTROL_ICON_BUTTON_ACTIVE_CLASS
            : 'text-text-muted hover:bg-white/10 hover:text-text'
        }`}
        aria-pressed={enabled}
        aria-label={enabled ? '关闭 FPS 显示' : '开启 FPS 显示'}
      >
        <Activity className="h-3.5 w-3.5" />
      </button>
      <span className="pointer-events-none absolute left-0 top-full mt-1.5 whitespace-nowrap rounded-md border border-[rgba(255,255,255,0.12)] bg-bg-dark/95 px-2 py-1 text-[11px] text-text-dark opacity-0 shadow-lg transition-opacity duration-100 group-hover:opacity-100">
        {enabled ? '关闭 FPS 显示' : '开启 FPS 显示'}
      </span>
    </div>
  );
}
