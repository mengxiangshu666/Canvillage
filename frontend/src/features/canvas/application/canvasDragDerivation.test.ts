// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it, vi } from 'vitest';

import { createDragStableCanvasDerivation } from './canvasDragDerivation';

describe('drag-stable canvas derivation', () => {
  it('derives once across position-only frames and recomputes at drag end', () => {
    const derive = vi.fn((nodes: readonly { selected: boolean }[]) =>
      nodes.filter((node) => node.selected).length,
    );
    const selectCount = createDragStableCanvasDerivation(derive);
    const dragSnapshot = { nodes: 'before-drag' };

    expect(selectCount([{ selected: true }], dragSnapshot)).toBe(1);
    expect(selectCount([{ selected: true }], dragSnapshot)).toBe(1);
    expect(selectCount([{ selected: true }], dragSnapshot)).toBe(1);
    expect(derive).toHaveBeenCalledTimes(1);

    expect(selectCount([{ selected: false }], null)).toBe(0);
    expect(derive).toHaveBeenCalledTimes(2);
  });

  it('keeps the result stable for repeated reads of the same nodes array', () => {
    const derive = vi.fn((nodes: readonly number[]) => nodes.join(','));
    const selectValue = createDragStableCanvasDerivation(derive);
    const nodes = [1, 2, 3];

    expect(selectValue(nodes, null)).toBe('1,2,3');
    expect(selectValue(nodes, null)).toBe('1,2,3');
    expect(derive).toHaveBeenCalledOnce();
  });
});
