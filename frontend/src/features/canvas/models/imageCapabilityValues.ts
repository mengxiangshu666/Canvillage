/**
 * Syntax gates for model-provided image values.
 *
 * These are deliberately not capability allowlists. The selected model's
 * runtime contract decides what to display; these helpers only prevent
 * malformed values from reaching the node store or Agent command bridge.
 */

const ASPECT_RATIO_PATTERN = /^\d{1,6}(?:\.\d{1,4})?:\d{1,6}(?:\.\d{1,4})?$/;
const DIMENSION_PATTERN = /^(\d{2,5})\s*[xX×]\s*(\d{2,5})$/;
const K_SIZE_PATTERN = /^\d{1,2}(?:\.\d{1,3})?\s*[kK]$/;
const VIDEO_RESOLUTION_PATTERN = /^(?:[1-9]\d{2,5}p|[1-9]\d{0,2}k|[1-9]\d{2,5}x[1-9]\d{2,5})$/i;

export function normalizeImageAspectRatioValue(value: string): string | null {
  const normalized = value.trim().replace(/：/g, ':');
  if (normalized === 'auto') return 'auto';
  if (!ASPECT_RATIO_PATTERN.test(normalized)) return null;
  const [width, height] = normalized.split(':').map(Number);
  return Number.isFinite(width) && Number.isFinite(height) && width > 0 && height > 0
    ? normalized
    : null;
}

export function isValidImageAspectRatio(value: unknown, allowAuto = true): value is string {
  if (typeof value !== 'string') return false;
  const normalized = normalizeImageAspectRatioValue(value);
  return normalized !== null && (allowAuto || normalized !== 'auto');
}

export function normalizeImageSizeValue(value: string): string | null {
  const normalized = value.trim().replace(/\s*[xX×]\s*/g, 'x');
  if (/^(?:512|0\.5K|1K|2K|3K|4K)$/i.test(normalized)) return normalized;
  const dimensions = DIMENSION_PATTERN.exec(normalized);
  if (dimensions) {
    const width = Number(dimensions[1]);
    const height = Number(dimensions[2]);
    return width > 0 && height > 0 ? normalized : null;
  }
  return K_SIZE_PATTERN.test(normalized) ? normalized : null;
}

export function isValidImageSize(value: unknown): value is string {
  return typeof value === 'string' && normalizeImageSizeValue(value) !== null;
}

/** Keep provider-declared video resolution labels extensible at UI boundaries. */
export function normalizeVideoResolutionValue(value: string): string | null {
  const normalized = value.trim().toLowerCase().replace(/×/g, 'x');
  return VIDEO_RESOLUTION_PATTERN.test(normalized) ? normalized : null;
}

export function normalizeVideoQualityValue(value: string): string | null {
  const resolution = normalizeVideoResolutionValue(value);
  if (!resolution) return null;
  if (resolution.includes('x')) return resolution;
  if (/[横竖]|\([^)]*\)/.test(resolution)) return resolution;
  return resolution.endsWith('p')
    ? `${resolution.slice(0, -1)}P`
    : resolution.toUpperCase();
}
