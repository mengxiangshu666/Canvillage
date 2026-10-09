export interface NormalizedRegionRect {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface NormalizedPoint {
  x: number;
  y: number;
}

function clamp01(value: number): number {
  return Math.max(0, Math.min(1, value));
}

export function normalizedPointFromClient(
  clientX: number,
  clientY: number,
  bounds: Pick<DOMRect, "left" | "top" | "width" | "height">,
): NormalizedPoint {
  return {
    x: clamp01((clientX - bounds.left) / Math.max(1, bounds.width)),
    y: clamp01((clientY - bounds.top) / Math.max(1, bounds.height)),
  };
}

export function normalizedRegionFromPoints(
  start: NormalizedPoint,
  end: NormalizedPoint,
  minimumSize = 0.035,
): NormalizedRegionRect | null {
  const x = clamp01(Math.min(start.x, end.x));
  const y = clamp01(Math.min(start.y, end.y));
  const width = Math.min(1 - x, Math.abs(end.x - start.x));
  const height = Math.min(1 - y, Math.abs(end.y - start.y));
  if (width < minimumSize || height < minimumSize) return null;
  return { x, y, width, height };
}

export function moveNormalizedRegion(
  region: NormalizedRegionRect,
  deltaX: number,
  deltaY: number,
): NormalizedRegionRect {
  return {
    ...region,
    x: Math.max(0, Math.min(1 - region.width, region.x + deltaX)),
    y: Math.max(0, Math.min(1 - region.height, region.y + deltaY)),
  };
}

export function resizeNormalizedRegion(
  region: NormalizedRegionRect,
  point: NormalizedPoint,
  minimumSize = 0.035,
): NormalizedRegionRect {
  return {
    ...region,
    width: Math.max(minimumSize, Math.min(1 - region.x, point.x - region.x)),
    height: Math.max(minimumSize, Math.min(1 - region.y, point.y - region.y)),
  };
}

export function containedImageRect(input: {
  containerWidth: number;
  containerHeight: number;
  naturalWidth: number;
  naturalHeight: number;
}): { left: number; top: number; width: number; height: number } {
  const containerWidth = Math.max(1, input.containerWidth);
  const containerHeight = Math.max(1, input.containerHeight);
  const imageRatio = Math.max(1, input.naturalWidth) / Math.max(1, input.naturalHeight);
  const containerRatio = containerWidth / containerHeight;
  if (imageRatio >= containerRatio) {
    const width = containerWidth;
    const height = width / imageRatio;
    return { left: 0, top: (containerHeight - height) / 2, width, height };
  }
  const height = containerHeight;
  const width = height * imageRatio;
  return { left: (containerWidth - width) / 2, top: 0, width, height };
}
