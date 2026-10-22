// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useCallback, useEffect, useLayoutEffect, useState, type RefObject } from 'react';

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
    /** Measured after mount so a short panel stays attached instead of using an estimate. */
    popoverRef?: RefObject<HTMLElement | null>;
  },
): AnchoredPopoverPosition | null {
  const { width, height, align = 'start', gap = 8, margin = 12, popoverRef } = options;
  const [position, setPosition] = useState<AnchoredPopoverPosition | null>(null);

  const syncPosition = useCallback(() => {
    const trigger = triggerRef.current;
    if (!trigger || typeof window === 'undefined') return;
    const measuredHeight = popoverRef?.current?.getBoundingClientRect().height;
    setPosition(computeAnchoredPopoverPosition(trigger, {
      width,
      height: measuredHeight && measuredHeight > 0 ? measuredHeight : height,
      align,
      gap,
      margin,
    }));
  }, [align, gap, height, margin, popoverRef, triggerRef, width]);

  useLayoutEffect(() => {
    if (!isOpen) {
      setPosition(null);
      return;
    }
    syncPosition();
    // The portal mounts after this effect runs, so observe it on the next frame.
    let observer: ResizeObserver | null = null;
    const frame = requestAnimationFrame(() => {
      syncPosition();
      const popover = popoverRef?.current;
      if (!popover || typeof ResizeObserver === 'undefined') return;
      observer = new ResizeObserver(syncPosition);
      observer.observe(popover);
    });
    window.addEventListener('resize', syncPosition);
    window.addEventListener('scroll', syncPosition, true);
    return () => {
      cancelAnimationFrame(frame);
      observer?.disconnect();
      window.removeEventListener('resize', syncPosition);
      window.removeEventListener('scroll', syncPosition, true);
    };
  }, [isOpen, popoverRef, syncPosition]);

  // React Flow zoom/pan changes an ancestor transform, not window scroll or size.
  useEffect(() => {
    if (!isOpen) return;
    const trigger = triggerRef.current;
    if (!trigger) return;
    const targets = [
      trigger.closest('.react-flow__viewport'),
      trigger.closest('.react-flow__node'),
    ].filter((target): target is Element => target != null);
    if (targets.length === 0) return;
    let frame = 0;
    const scheduleSync = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(syncPosition);
    };
    const observer = new MutationObserver(scheduleSync);
    for (const target of targets) {
      observer.observe(target, { attributes: true, attributeFilter: ['style', 'class'] });
    }
    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
    };
  }, [isOpen, syncPosition, triggerRef]);

  return position;
}
