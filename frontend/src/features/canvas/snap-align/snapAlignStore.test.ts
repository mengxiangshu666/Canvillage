// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { afterEach, describe, expect, it, vi } from 'vitest';

import { useSnapAlignStore } from './snapAlignStore';

describe('snap alignment guide updates', () => {
  afterEach(() => {
    useSnapAlignStore.setState({
      enabled: false,
      guides: { vertical: [], horizontal: [] },
    });
    vi.restoreAllMocks();
  });

  it('skips publishing identical guide frames', () => {
    const listener = vi.fn();
    const unsubscribe = useSnapAlignStore.subscribe(listener);

    useSnapAlignStore.getState().setGuides({ vertical: [120], horizontal: [] });
    useSnapAlignStore.getState().setGuides({ vertical: [120], horizontal: [] });
    useSnapAlignStore.getState().setGuides({ vertical: [120], horizontal: [80] });

    expect(listener).toHaveBeenCalledTimes(2);
    unsubscribe();
  });
});
