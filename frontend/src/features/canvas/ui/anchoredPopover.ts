// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useCallback, useEffect, useState, type RefObject } from 'react';

export type AnchoredPopoverAlign = 'start' | 'center' | 'end';

export interface AnchoredPopoverPosition {
  left: number;
  top: number;
}

export function computeAnchoredPopoverPosition(
  trigger: HTMLElement,
  {
    width,
    height,
    align = 'start',
    gap = 8,
    margin = 12,
  }: {
    width: number;
    height: number;
    align?: AnchoredPopoverAlign;
    gap?: number;
    margin?: number;
  },
): AnchoredPopoverPosition {
  const rect = trigger.getBoundingClientRect();
  const rawLeft =
    align === 'center'
      ? rect.left + rect.width / 2 - width / 2
      : align === 'end'
        ? rect.right - width
        : rect.left;
  const maxLeft = Math.max(margin, window.innerWidth - width - margin);
  const left = Math.min(Math.max(margin, rawLeft), maxLeft);
  const hasRoomAbove = rect.top - gap - height >= margin;
  const rawTop = hasRoomAbove ? rect.top - gap - height : rect.bottom + gap;
  const maxTop = Math.max(margin, window.innerHeight - height - margin);
  return {
    left,
    top: Math.min(Math.max(margin, rawTop), maxTop),
  };
}

export function useAnchoredNodePopoverPosition(
  isOpen: boolean,
  triggerRef: RefObject<HTMLElement | null>,
  options: {
    width: number;
    height: number;
    align?: AnchoredPopoverAlign;
    gap?: number;
    margin?: number;
  },
): AnchoredPopoverPosition | null {
  const { width, height, align = 'start', gap = 8, margin = 12 } = options;
  const [position, setPosition] = useState<AnchoredPopoverPosition | null>(null);

  const syncPosition = useCallback(() => {
    const trigger = triggerRef.current;
    if (!trigger || typeof window === 'undefined') return;
    setPosition(computeAnchoredPopoverPosition(trigger, { width, height, align, gap, margin }));
  }, [align, gap, height, margin, triggerRef, width]);

  useEffect(() => {
    if (!isOpen) {
      setPosition(null);
      return;
    }
    syncPosition();
    window.addEventListener('resize', syncPosition);
    window.addEventListener('scroll', syncPosition, true);
    return () => {
      window.removeEventListener('resize', syncPosition);
      window.removeEventListener('scroll', syncPosition, true);
    };
  }, [isOpen, syncPosition]);

  return position;
}
