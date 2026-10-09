// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import {
  Background,
  BackgroundVariant,
  useStore,
} from '@xyflow/react';

import { resolveCanvasGridRenderMetrics } from './canvasViewSettingsStore';

export function AdaptiveCanvasGrid({
  gap,
  dotSize,
  color,
}: {
  gap: number;
  dotSize: number;
  color: string;
}) {
  const zoom = useStore((state) => state.transform[2]);
  const metrics = resolveCanvasGridRenderMetrics(gap, dotSize, zoom);

  return (
    <Background
      variant={BackgroundVariant.Dots}
      gap={metrics.gap}
      size={metrics.dotSize}
      color={color}
    />
  );
}
