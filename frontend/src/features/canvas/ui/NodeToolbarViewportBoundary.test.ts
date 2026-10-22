// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from 'vitest';

import { clampToolbarOffset } from './NodeToolbarViewportBoundary';

describe('clampToolbarOffset', () => {
  const viewport = { width: 388, height: 841 };

  it('leaves a toolbar that already fits untouched', () => {
    expect(
      clampToolbarOffset(
        { left: 16, top: 100, width: 356, height: 180 },
        viewport,
        { x: 0, y: 0 },
      ),
    ).toEqual({ x: 0, y: 0 });
  });

  it('pulls an overflowing toolbar back inside both viewport edges', () => {
    expect(
      clampToolbarOffset(
        { left: 298, top: 538, width: 364, height: 572 },
        viewport,
        { x: 0, y: 0 },
      ),
    ).toEqual({ x: -282, y: -277 });
  });

  it('is stable after its translation has been applied', () => {
    expect(
      clampToolbarOffset(
        { left: 16, top: 261, width: 364, height: 572 },
        viewport,
        { x: -282, y: -277 },
      ),
    ).toEqual({ x: -282, y: -277 });
  });
});
