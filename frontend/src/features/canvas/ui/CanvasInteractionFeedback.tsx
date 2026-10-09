// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useEffect, useRef, type RefObject } from 'react';

const CLICK_BUBBLE_COUNT_MIN = 7;
const CLICK_BUBBLE_COUNT_VARIANTS = 3;
const CLICK_BUBBLE_SIZE_MIN = 5;
const CLICK_BUBBLE_SIZE_MAX = 8;
const CLICK_BUBBLE_RADIUS_MIN = 16;
const CLICK_BUBBLE_RADIUS_MAX = 29;
const CLICK_BUBBLE_ANGLE_JITTER = 0.3;

export interface ClickBubble {
  x: number;
  y: number;
  size: number;
  delay: number;
  opacity: number;
}

const randomBetween = (random: () => number, min: number, max: number) =>
  min + random() * (max - min);

/** Keep a loose orbit while making every click feel organic and different. */
export function createClickBubbles(random: () => number = Math.random): ClickBubble[] {
  const count = CLICK_BUBBLE_COUNT_MIN + Math.floor(random() * CLICK_BUBBLE_COUNT_VARIANTS);
  const angleStep = (Math.PI * 2) / count;

  return Array.from({ length: count }, (_, index) => {
    const angle =
      angleStep * index + randomBetween(random, -CLICK_BUBBLE_ANGLE_JITTER, CLICK_BUBBLE_ANGLE_JITTER);
    const radius = randomBetween(random, CLICK_BUBBLE_RADIUS_MIN, CLICK_BUBBLE_RADIUS_MAX);
    return {
      x: Math.round(Math.cos(angle) * radius),
      y: Math.round(Math.sin(angle) * radius),
      size: Math.round(randomBetween(random, CLICK_BUBBLE_SIZE_MIN, CLICK_BUBBLE_SIZE_MAX)),
      delay: Math.round(randomBetween(random, 0, 8)),
      opacity: Number(randomBetween(random, 0.8, 0.96).toFixed(2)),
    };
  });
}

const CLICK_TRAVEL_LIMIT_PX = 5;
const BURST_LIFETIME_MS = 560;

interface CanvasInteractionFeedbackProps {
  containerRef: RefObject<HTMLDivElement | null>;
}

function isBlankCanvasTarget(
  target: EventTarget | null,
  container: HTMLElement,
): target is Element {
  if (!(target instanceof Element) || !container.contains(target)) return false;
  if (!target.closest('.react-flow')) return false;
  return !target.closest(
    '.react-flow__node, .react-flow__edge, .react-flow__controls, .react-flow__minimap, '
      + '.nodrag, .nopan, button, input, textarea, select, [role="button"], [role="menu"]',
  );
}

export function CanvasInteractionFeedback({
  containerRef,
}: CanvasInteractionFeedbackProps) {
  const layerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const container = containerRef.current;
    const layer = layerRef.current;
    if (!container || !layer) return;

    let pendingClick: {
      pointerId: number;
      clientX: number;
      clientY: number;
    } | null = null;

    const showBurst = (clientX: number, clientY: number) => {
      const rect = container.getBoundingClientRect();
      const burst = document.createElement('span');
      burst.className = 'village-canvas-click-burst';
      burst.style.left = `${clientX - rect.left}px`;
      burst.style.top = `${clientY - rect.top}px`;
      burst.setAttribute('aria-hidden', 'true');

      for (const bubble of createClickBubbles()) {
        const element = document.createElement('i');
        element.style.setProperty('--bubble-x', `${bubble.x}px`);
        element.style.setProperty('--bubble-y', `${bubble.y}px`);
        element.style.setProperty('--bubble-start-x', `${Math.round(bubble.x * 0.12)}px`);
        element.style.setProperty('--bubble-start-y', `${Math.round(bubble.y * 0.12)}px`);
        element.style.setProperty('--bubble-mid-x', `${Math.round(bubble.x * 0.76)}px`);
        element.style.setProperty('--bubble-mid-y', `${Math.round(bubble.y * 0.76)}px`);
        element.style.setProperty('--bubble-size', `${bubble.size}px`);
        element.style.setProperty('--bubble-delay', `${bubble.delay}ms`);
        element.style.setProperty('--bubble-opacity', `${bubble.opacity}`);
        burst.appendChild(element);
      }

      layer.appendChild(burst);
      window.setTimeout(() => burst.remove(), BURST_LIFETIME_MS);
    };

    const handlePointerDown = (event: PointerEvent) => {
      if (event.button !== 0 || !isBlankCanvasTarget(event.target, container)) {
        pendingClick = null;
        return;
      }
      pendingClick = {
        pointerId: event.pointerId,
        clientX: event.clientX,
        clientY: event.clientY,
      };
    };

    const handlePointerUp = (event: PointerEvent) => {
      const pending = pendingClick;
      pendingClick = null;
      if (!pending || event.pointerId !== pending.pointerId) return;
      if (!isBlankCanvasTarget(event.target, container)) return;
      const distance = Math.hypot(
        event.clientX - pending.clientX,
        event.clientY - pending.clientY,
      );
      if (distance > CLICK_TRAVEL_LIMIT_PX) return;
      showBurst(event.clientX, event.clientY);
    };

    const handlePointerCancel = () => {
      pendingClick = null;
    };

    container.addEventListener('pointerdown', handlePointerDown, { capture: true });
    container.addEventListener('pointerup', handlePointerUp, { capture: true });
    container.addEventListener('pointercancel', handlePointerCancel, { capture: true });
    return () => {
      container.removeEventListener('pointerdown', handlePointerDown, { capture: true });
      container.removeEventListener('pointerup', handlePointerUp, { capture: true });
      container.removeEventListener('pointercancel', handlePointerCancel, { capture: true });
    };
  }, [containerRef]);

  return (
    <div
      ref={layerRef}
      className="village-canvas-interaction-layer pointer-events-none absolute inset-0 z-[9999] overflow-hidden"
      aria-hidden="true"
    />
  );
}
