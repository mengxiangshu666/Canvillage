import { describe, expect, it } from 'vitest';

import { formatElapsedDuration, resolveGenerationProgress } from './NodeGenerationOverlay';

describe('resolveGenerationProgress', () => {
  it('renders the authoritative backend value, including a real 0%', () => {
    expect(resolveGenerationProgress(0)).toBe(0);
    expect(resolveGenerationProgress(0.1)).toBe(10);
    expect(resolveGenerationProgress(0.865)).toBe(87);
    expect(resolveGenerationProgress(1)).toBe(100);
  });

  it('clamps out-of-range values instead of trusting them', () => {
    expect(resolveGenerationProgress(-0.5)).toBe(0);
    expect(resolveGenerationProgress(1.4)).toBe(100);
  });

  it('returns null when the backend has not reported progress', () => {
    // The whole point of this task: without a backend number there is no
    // number. The old contract fabricated one from a hardcoded expected
    // duration, which is indistinguishable from a real reading.
    expect(resolveGenerationProgress(null)).toBeNull();
    expect(resolveGenerationProgress(undefined)).toBeNull();
  });

  it('treats non-finite input as "no reading" rather than NaN%', () => {
    expect(resolveGenerationProgress(Number.NaN)).toBeNull();
    expect(resolveGenerationProgress(Number.POSITIVE_INFINITY)).toBeNull();
  });
});

describe('formatElapsedDuration', () => {
  it('reads in seconds below a minute', () => {
    expect(formatElapsedDuration(0)).toBe('0s');
    expect(formatElapsedDuration(12_400)).toBe('12s');
    expect(formatElapsedDuration(59_900)).toBe('59s');
  });

  it('switches to minute-second above a minute, padding the seconds', () => {
    expect(formatElapsedDuration(60_000)).toBe('1m00s');
    expect(formatElapsedDuration(83_000)).toBe('1m23s');
    expect(formatElapsedDuration(3_605_000)).toBe('60m05s');
  });

  it('never goes negative on a skewed clock', () => {
    expect(formatElapsedDuration(-5_000)).toBe('0s');
  });
});
