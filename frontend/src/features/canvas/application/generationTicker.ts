// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useSyncExternalStore, useState } from 'react';

/**
 * One shared 120ms clock for every "generating" surface on the canvas.
 *
 * Each generation overlay used to own a `setInterval`. With N concurrent
 * generations that is N timers waking N times per second, and each wake-up
 * re-renders a node. One interval drives all of them instead: the cost of the
 * clock stops scaling with the number of running tasks.
 *
 * The interval only exists while something is subscribed, so an idle canvas
 * has no timer at all.
 */
const TICK_MS = 120;

const listeners = new Set<() => void>();
let timer: number | null = null;
// 0 means "no tick yet": the first read stamps it. Using 0 instead of a module
// load timestamp keeps the first paint from showing a stale elapsed time.
let now = 0;

function tick(): void {
  now = Date.now();
  for (const listener of listeners) listener();
}

function ensureTimer(): void {
  if (timer !== null || listeners.size === 0) return;
  now = Date.now();
  timer = window.setInterval(tick, TICK_MS);
}

function stopTimerIfIdle(): void {
  if (timer === null || listeners.size > 0) return;
  window.clearInterval(timer);
  timer = null;
}

/** Subscribe to the shared generation clock. Returns the unsubscribe fn. */
export function subscribeGenerationTick(listener: () => void): () => void {
  listeners.add(listener);
  ensureTimer();
  return () => {
    listeners.delete(listener);
    stopTimerIfIdle();
  };
}

/** Last tick time. Stamped on first read so the initial paint is never stale. */
export function getGenerationTickTime(): number {
  if (now === 0) now = Date.now();
  return now;
}

function subscribeNothing(): () => void {
  return () => {};
}

function getDisabledSnapshot(): number {
  return 0;
}

/**
 * Re-render on the shared generation clock.
 *
 * Pass `enabled: false` to keep the hook mounted without subscribing — a node
 * that is not generating must not wake up every 120ms just because some other
 * node is.
 */
export function useGenerationTick(enabled = true): number {
  // The shared clock only advances while somebody is subscribed. A node that
  // mounts after an idle stretch would otherwise read the last tick from long
  // ago, so clamp to the mount time until the clock catches up.
  const [mountedAt] = useState(() => Date.now());
  const snapshot = useSyncExternalStore(
    enabled ? subscribeGenerationTick : subscribeNothing,
    enabled ? getGenerationTickTime : getDisabledSnapshot,
    enabled ? getGenerationTickTime : getDisabledSnapshot,
  );
  return Math.max(snapshot, mountedAt);
}

/** Test-only: how many timers this module currently owns (0 or 1). */
export function __generationTickerTimerCount(): number {
  return timer === null ? 0 : 1;
}
