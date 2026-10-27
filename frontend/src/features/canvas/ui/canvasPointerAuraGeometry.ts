// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab

/**
 * 画布指针光效的纯几何与配色。
 *
 * 画布点阵由 `@xyflow/react` 的 `<Background variant="dots">` 用 SVG pattern
 * 绘制，没有独立的 DOM 节点，所以「让指针附近的点凸起来」只能另画一层
 * canvas 覆盖上去。覆盖层的点必须和底层 SVG 点严丝合缝地对齐，否则放大后
 * 会看到两层点互相错位；对齐公式照抄 XYFlow 的 pattern 计算：
 *
 *   pattern 单元格 = gap * zoom
 *   pattern 起点     = translate % 单元格
 *   pattern 内部平移 = -(1 + 单元格 / 2)   // offset 为 0 时的 XYFlow 默认值
 *   点的圆心         = 半径 - (1 + 单元格 / 2)
 *
 * 所以所有屏幕坐标下的点都落在 `origin + k * 单元格` 这条格线上。
 */

export type Rgb = readonly [number, number, number];

export interface PointerAuraViewport {
  /** `resolveCanvasGridRenderMetrics` 算出的可见点间距（世界单位）。 */
  gap: number;
  /** 同上，已按缩放补偿过的点直径（世界单位）。 */
  dotSize: number;
  /** 当前视口缩放。 */
  zoom: number;
  /** 视口平移，屏幕像素。 */
  translateX: number;
  translateY: number;
}

export interface PointerAuraGrid {
  /** 相邻两个点在屏幕上的间距。 */
  cell: number;
  /** 底图点的半径，屏幕像素。 */
  dotRadius: number;
  /** 第 0 列点的圆心横坐标；其余点为 originX + k * cell。 */
  originX: number;
  originY: number;
}

const MIN_CELL = 4;
const MIN_DOT_RADIUS = 0.35;

const FALLBACK_GRID_COLOR: Rgb = [176, 176, 183];

function positiveModulo(value: number, divisor: number): number {
  const remainder = value % divisor;
  return remainder < 0 ? remainder + divisor : remainder;
}

export function resolvePointerAuraGrid({
  gap,
  dotSize,
  zoom,
  translateX,
  translateY,
}: PointerAuraViewport): PointerAuraGrid {
  const safeZoom = Number.isFinite(zoom) && zoom > 0 ? zoom : 1;
  const safeGap = Number.isFinite(gap) && gap > 0 ? gap : 1;
  const safeDotSize = Number.isFinite(dotSize) && dotSize > 0 ? dotSize : 1;
  const safeX = Number.isFinite(translateX) ? translateX : 0;
  const safeY = Number.isFinite(translateY) ? translateY : 0;
  const cell = Math.max(MIN_CELL, safeGap * safeZoom);
  const dotRadius = Math.max(MIN_DOT_RADIUS, (safeDotSize * safeZoom) / 2);
  const shift = dotRadius - 1 - cell / 2;
  return {
    cell,
    dotRadius,
    originX: positiveModulo(safeX, cell) + shift,
    originY: positiveModulo(safeY, cell) + shift,
  };
}

/**
 * 指针周围逐点回调。`intensity` 是 0→1 的平滑衰减，中心为 1、边界为 0，
 * 采用 smoothstep 让「凸起」的那一片有宽度，而不是只有紧贴指针的一两个点。
 */
export function forEachPointerAuraDot(
  grid: PointerAuraGrid,
  centerX: number,
  centerY: number,
  reach: number,
  visit: (x: number, y: number, intensity: number) => void,
): void {
  if (
    !(reach > 0) ||
    !Number.isFinite(centerX) ||
    !Number.isFinite(centerY) ||
    !(grid.cell > 0)
  ) {
    return;
  }

  const reachSquared = reach * reach;
  const firstColumn = Math.floor((centerX - reach - grid.originX) / grid.cell);
  const lastColumn = Math.ceil((centerX + reach - grid.originX) / grid.cell);
  const firstRow = Math.floor((centerY - reach - grid.originY) / grid.cell);
  const lastRow = Math.ceil((centerY + reach - grid.originY) / grid.cell);

  for (let row = firstRow; row <= lastRow; row += 1) {
    const y = grid.originY + row * grid.cell;
    const dy = y - centerY;
    for (let column = firstColumn; column <= lastColumn; column += 1) {
      const x = grid.originX + column * grid.cell;
      const dx = x - centerX;
      const distanceSquared = dx * dx + dy * dy;
      if (distanceSquared > reachSquared) continue;
      const linear = 1 - Math.sqrt(distanceSquared) / reach;
      visit(x, y, linear * linear * (3 - 2 * linear));
    }
  }
}

export function parseHexColor(value: string): Rgb | null {
  const match = /^#?([0-9a-f]{6})$/i.exec(value.trim());
  if (!match) return null;
  const parsed = Number.parseInt(match[1], 16);
  return [(parsed >> 16) & 255, (parsed >> 8) & 255, parsed & 255];
}

export function resolveGridRgb(color: string): Rgb {
  return parseHexColor(color) ?? FALLBACK_GRID_COLOR;
}

export function mixRgb(color: Rgb, target: Rgb, amount: number): Rgb {
  const ratio = Math.min(1, Math.max(0, Number.isFinite(amount) ? amount : 0));
  return [
    Math.round(color[0] + (target[0] - color[0]) * ratio),
    Math.round(color[1] + (target[1] - color[1]) * ratio),
    Math.round(color[2] + (target[2] - color[2]) * ratio),
  ];
}

export function rgba(color: Rgb, alpha: number): string {
  const safeAlpha = Math.min(1, Math.max(0, Number.isFinite(alpha) ? alpha : 0));
  return `rgba(${color[0]}, ${color[1]}, ${color[2]}, ${
    Math.round(safeAlpha * 1000) / 1000
  })`;
}
