// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { render } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { NodeGenerationOverlay } from '../ui/NodeGenerationOverlay';
import {
  __generationTickerTimerCount,
  subscribeGenerationTick,
} from './generationTicker';

afterEach(() => {
  vi.restoreAllMocks();
});

describe('generationTicker', () => {
  it('drives any number of generating overlays from a single interval', () => {
    const spy = vi.spyOn(window, 'setInterval');
    const overlays = [0, 1, 2, 3, 4].map((index) => (
      <NodeGenerationOverlay key={index} startedAt={Date.now()} progress={null} />
    ));

    const { unmount } = render(<>{overlays}</>);

    expect(__generationTickerTimerCount()).toBe(1);
    expect(spy).toHaveBeenCalledTimes(1);
    expect(spy.mock.calls[0]?.[1]).toBe(120);

    unmount();
    expect(__generationTickerTimerCount()).toBe(0);
    expect(window.clearInterval).toBeDefined();
  });

  it('notifies every subscriber from one tick and stops when the last one leaves', () => {
    vi.useFakeTimers();
    try {
      const first = vi.fn();
      const second = vi.fn();
      const offFirst = subscribeGenerationTick(first);
      const offSecond = subscribeGenerationTick(second);

      vi.advanceTimersByTime(120);
      expect(first).toHaveBeenCalledTimes(1);
      expect(second).toHaveBeenCalledTimes(1);

      offFirst();
      expect(__generationTickerTimerCount()).toBe(1);
      offSecond();
      expect(__generationTickerTimerCount()).toBe(0);
    } finally {
      vi.useRealTimers();
    }
  });
});
