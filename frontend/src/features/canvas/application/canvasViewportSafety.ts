export interface CanvasSafeInsets {
  top: number;
  right: number;
  bottom: number;
  left: number;
}

type PixelPadding = `${number}px`;

function safeInset(value: number): number {
  return Number.isFinite(value) ? Math.max(0, value) : 0;
}

export function canvasSafeFitPadding(
  insets: CanvasSafeInsets,
  margin = 24,
): { top: PixelPadding; right: PixelPadding; bottom: PixelPadding; left: PixelPadding } {
  const safeMargin = safeInset(margin);
  return {
    top: `${safeInset(insets.top) + safeMargin}px`,
    right: `${safeInset(insets.right) + safeMargin}px`,
    bottom: `${safeInset(insets.bottom) + safeMargin}px`,
    left: `${safeInset(insets.left) + safeMargin}px`,
  };
}

/**
 * XYFlow's setCenter targets the full DOM rectangle. Shift that target so the
 * requested flow point lands in the center of the unobscured canvas instead.
 */
export function safeAreaAdjustedFlowCenter(input: {
  centerX: number;
  centerY: number;
  zoom: number;
  insets: CanvasSafeInsets;
}): { x: number; y: number } {
  const zoom = Number.isFinite(input.zoom) && input.zoom > 0 ? input.zoom : 1;
  const left = safeInset(input.insets.left);
  const right = safeInset(input.insets.right);
  const top = safeInset(input.insets.top);
  const bottom = safeInset(input.insets.bottom);

  return {
    x: input.centerX - (left - right) / (2 * zoom),
    y: input.centerY - (top - bottom) / (2 * zoom),
  };
}

export interface ViewportRect {
  x: number;
  y: number;
  width: number;
  height: number;
}

/**
 * Fit a whole block of freshly created nodes into the unobscured canvas.
 *
 * Why this exists next to `safeAreaAdjustedFlowCenter`: that one centers the
 * camera on a *point*, which works for a single node but strands every further
 * node outside the viewport — and the canvas renders only visible nodes
 * (`CANVAS_ONLY_RENDER_VISIBLE_ELEMENTS`), so an off-viewport node never mounts
 * and never gets its "auto generate once" effect. A derived batch (storyboard
 * images, per-shot videos) has to be framed as a block, not as its first member.
 */
export function viewportForRects(input: {
  rects: readonly ViewportRect[];
  viewportWidth: number;
  viewportHeight: number;
  insets: CanvasSafeInsets;
  /** Upper bound on the resulting zoom — a couple of nodes should not fill the screen. */
  maxZoom?: number;
  /** Space kept between the block and the safe area edge, in screen pixels. */
  margin?: number;
}): { x: number; y: number; zoom: number } | null {
  const rects = input.rects.filter(
    (rect) =>
      Number.isFinite(rect.x) &&
      Number.isFinite(rect.y) &&
      Number.isFinite(rect.width) &&
      Number.isFinite(rect.height),
  );
  if (rects.length === 0) return null;

  const safeWidth = input.viewportWidth - safeInset(input.insets.left) - safeInset(input.insets.right);
  const safeHeight = input.viewportHeight - safeInset(input.insets.top) - safeInset(input.insets.bottom);
  if (!(safeWidth > 0) || !(safeHeight > 0)) return null;

  const margin = safeInset(input.margin ?? 40);
  const usableWidth = Math.max(1, safeWidth - margin * 2);
  const usableHeight = Math.max(1, safeHeight - margin * 2);

  const minX = Math.min(...rects.map((rect) => rect.x));
  const minY = Math.min(...rects.map((rect) => rect.y));
  const maxX = Math.max(...rects.map((rect) => rect.x + rect.width));
  const maxY = Math.max(...rects.map((rect) => rect.y + rect.height));

  const fitZoom = Math.min(usableWidth / Math.max(1, maxX - minX), usableHeight / Math.max(1, maxY - minY));
  const zoom = Math.max(0.1, Math.min(input.maxZoom ?? 0.72, fitZoom));
  const center = safeAreaAdjustedFlowCenter({
    centerX: minX + (maxX - minX) / 2,
    centerY: minY + (maxY - minY) / 2,
    zoom,
    insets: input.insets,
  });

  return {
    x: input.viewportWidth / 2 - center.x * zoom,
    y: input.viewportHeight / 2 - center.y * zoom,
    zoom,
  };
}
