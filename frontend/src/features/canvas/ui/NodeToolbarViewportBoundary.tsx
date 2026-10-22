// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { useViewport } from '@xyflow/react';

const VIEWPORT_MARGIN = 8;

interface ToolbarOffset {
  x: number;
  y: number;
}

interface ViewportSize {
  width: number;
  height: number;
}

/**
 * Returns the extra translation needed to keep a node-toolbar child inside the
 * browser viewport. `rect` includes any translation already applied by `offset`,
 * so subtracting it recovers the toolbar's natural position.
 */
export function clampToolbarOffset(
  rect: { left: number; top: number; width: number; height: number },
  viewport: ViewportSize,
  offset: ToolbarOffset,
  margin = VIEWPORT_MARGIN,
): ToolbarOffset {
  const naturalLeft = rect.left - offset.x;
  const naturalTop = rect.top - offset.y;
  const maxLeft = Math.max(margin, viewport.width - margin - rect.width);
  const maxTop = Math.max(margin, viewport.height - margin - rect.height);
  const targetLeft = Math.min(Math.max(naturalLeft, margin), maxLeft);
  const targetTop = Math.min(Math.max(naturalTop, margin), maxTop);

  return {
    x: targetLeft - naturalLeft,
    y: targetTop - naturalTop,
  };
}

/**
 * React Flow anchors NodeToolbar children to a node without considering browser
 * edges. This boundary keeps the child visible while preserving its anchor.
 */
export function NodeToolbarViewportBoundary({ children }: { children: ReactNode }) {
  const ref = useRef<HTMLDivElement | null>(null);
  const viewport = useViewport();
  const [offset, setOffset] = useState<ToolbarOffset>({ x: 0, y: 0 });

  useLayoutEffect(() => {
    const element = ref.current;
    if (!element) return;

    const measure = () => {
      const rect = element.getBoundingClientRect();
      if (rect.width === 0 || rect.height === 0) return;
      setOffset((current) => {
        const next = clampToolbarOffset(
          rect,
          {
            width: document.documentElement.clientWidth,
            height: document.documentElement.clientHeight,
          },
          current,
        );
        return Math.abs(next.x - current.x) < 0.5 && Math.abs(next.y - current.y) < 0.5
          ? current
          : next;
      });
    };

    measure();
    const observer =
      typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(measure);
    observer?.observe(element);
    window.addEventListener('resize', measure);
    return () => {
      observer?.disconnect();
      window.removeEventListener('resize', measure);
    };
  }, [viewport.x, viewport.y, viewport.zoom]);

  return (
    <div
      ref={ref}
      data-node-toolbar-viewport-boundary
      style={{ transform: `translate3d(${offset.x}px, ${offset.y}px, 0)` }}
    >
      {children}
    </div>
  );
}
