// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useSyncExternalStore } from "react";

// Non-blocking "a new version is deployed" signal. Distinct from
// chunk-load-recovery (which blocks the whole app once a chunk 404s): this one
// is proactive and dismissible — the current version still works, we just nudge
// the user to refresh when convenient.
type UpdateState = "idle" | "available" | "dismissed";
type Listener = () => void;

let state: UpdateState = "idle";
const listeners = new Set<Listener>();
const reloadBlockers = new Set<() => boolean>();
let pendingReload: (() => void) | null = null;
let reloadTimer: number | null = null;

export function reloadAppWithCacheBust(): void {
  try {
    const url = new URL(window.location.href);
    url.searchParams.set("__village_canvas_manual_reload", Date.now().toString(36));
    window.location.replace(url.toString());
  } catch {
    window.location.reload();
  }
}

function retryPendingReload(): void {
  if (!pendingReload || reloadTimer != null || typeof window === "undefined") return;
  reloadTimer = window.setTimeout(() => {
    reloadTimer = null;
    const blocked = [...reloadBlockers].some((blocker) => {
      try {
        return blocker();
      } catch {
        return true;
      }
    });
    if (blocked) {
      retryPendingReload();
      return;
    }
    const reload = pendingReload;
    pendingReload = null;
    reload?.();
  }, 500);
}

function notify(): void {
  for (const listener of listeners) {
    listener();
  }
}

export function markUpdateAvailable(): void {
  if (state !== "idle") return;
  state = "available";
  notify();
}

/** Register volatile work that must settle before a newly deployed bundle reloads. */
export function registerAppUpdateReloadBlocker(blocker: () => boolean): () => void {
  reloadBlockers.add(blocker);
  return () => {
    reloadBlockers.delete(blocker);
    retryPendingReload();
  };
}

/**
 * Reload automatically once every registered canvas/chat blocker reports safe.
 * A short delay coalesces simultaneous build notifications and gives the current
 * autosave microtask a chance to publish its dirty state first.
 */
export function requestAppUpdateReload(
  reload: () => void = reloadAppWithCacheBust,
): void {
  pendingReload = reload;
  retryPendingReload();
}

export function dismissUpdateAvailable(): void {
  if (state !== "available") return;
  state = "dismissed";
  notify();
}

function subscribe(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

function getSnapshot(): boolean {
  return state === "available";
}

export function useUpdateAvailable(): boolean {
  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
}

export function resetUpdateAvailableForTests(): void {
  state = "idle";
  listeners.clear();
  reloadBlockers.clear();
  pendingReload = null;
  if (reloadTimer != null && typeof window !== "undefined") {
    window.clearTimeout(reloadTimer);
  }
  reloadTimer = null;
}
