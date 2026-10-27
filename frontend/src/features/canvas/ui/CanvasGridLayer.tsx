// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { AdaptiveCanvasGrid } from './AdaptiveCanvasGrid';
import { CanvasPointerAura } from './CanvasPointerAura';

/**
 * 网格层：点阵背景 + 指针光效共用同一组 gap/dotSize/color。
 * 收成一个组件，避免 Canvas.tsx 里重复展开两层。
 */
export function CanvasGridLayer({
  gap,
  dotSize,
  color,
}: {
  gap: number;
  dotSize: number;
  color: string;
}) {
  return (
    <>
      <AdaptiveCanvasGrid gap={gap} dotSize={dotSize} color={color} />
      <CanvasPointerAura gap={gap} dotSize={dotSize} color={color} />
    </>
  );
}
