// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { renderHook, waitFor } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { useAnchoredNodePopoverPosition } from '@/features/canvas/ui/anchoredPopover';

describe('useAnchoredNodePopoverPosition', () => {
  it('uses measured height and follows React Flow viewport transforms', async () => {
    const viewport = document.createElement('div');
    viewport.className = 'react-flow__viewport';
    const node = document.createElement('div');
    node.className = 'react-flow__node';
    const trigger = document.createElement('button');
    const popover = document.createElement('div');
    node.appendChild(trigger);
    viewport.appendChild(node);
    document.body.append(viewport, popover);

    let triggerRect = { left: 100, top: 300, right: 300, bottom: 328, width: 200, height: 28 };
    trigger.getBoundingClientRect = () => ({ ...triggerRect, x: triggerRect.left, y: triggerRect.top, toJSON() {} });
    popover.getBoundingClientRect = () => ({ left: 0, top: 0, right: 300, bottom: 120, width: 300, height: 120, x: 0, y: 0, toJSON() {} });

    const triggerRef = { current: trigger };
    const popoverRef = { current: popover };
    const { result } = renderHook(() => useAnchoredNodePopoverPosition(true, triggerRef, {
      width: 300,
      height: 400,
      gap: 8,
      popoverRef,
    }));

    expect(result.current).toEqual({ left: 100, top: 172 });

    triggerRect = { left: 140, top: 400, right: 340, bottom: 428, width: 200, height: 28 };
    viewport.style.transform = 'translate(40px, 100px) scale(0.8)';

    await waitFor(() => expect(result.current).toEqual({ left: 140, top: 272 }));
  });
});
