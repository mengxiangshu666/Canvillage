// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import {
  markUpdateAvailable,
  requestAppUpdateReload,
} from "@/lib/app-update-available";
import { BUILD_ID } from "@/lib/app-version";

// Poll a tiny build-emitted manifest (/version.json) and compare the deployed
// buildId against BUILD_ID — the compile-time constant baked into THIS running
// bundle (see the emit-version-json plugin in vite.config.ts). Because the
// baseline is the running code's own fingerprint (not a fetched-then-remembered
// value), there is no seed race: a deploy that lands between page load and the
// first poll is still caught. It's also independent of the backend, so CDN-only
// frontend deploys are detected even when the API never restarts.
//
// This deliberately keys off buildId, NOT the display version: APP_VERSION
// falls back to a hardcoded default when CI doesn't inject one, so comparing it
// would make two different deploys look identical and silently never prompt.
const POLL_INTERVAL_MS = 120_000;
const AUTO_RELOAD_BUILD_KEY = "village-canvas:auto-update-reload-build";
const AUTO_RELOAD_PERSIST_KEY = "village-canvas:auto-update-reload-build:persistent";
const AUTO_RELOAD_PARAM = "__village_canvas_build";
const AUTO_RELOAD_TS_PARAM = "__village_canvas_ts";
let lastReservedAutoReloadBuildId: string | null = null;

export function deployedVersionDiffers(
  deployed: string | null,
  running: string,
): boolean {
  return deployed !== null && deployed !== running;
}

async function fetchDeployedBuildId(): Promise<string | null> {
  try {
    // Cache-bust + no-store so a CDN/proxy can't hand back a stale manifest.
    const response = await fetch(`/version.json?_v=${Date.now()}`, {
      cache: "no-store",
      credentials: "same-origin",
    });
    if (!response.ok) return null;
    const data: unknown = await response.json();
    const buildId = (data as { buildId?: unknown } | null)?.buildId;
    return typeof buildId === "string" && buildId.length > 0 ? buildId : null;
  } catch {
    return null;
  }
}

export function reserveAutoReloadForDeployedBuild(deployedBuildId: string): boolean {
  if (lastReservedAutoReloadBuildId === deployedBuildId) return false;
  try {
    const currentUrl = new URL(window.location.href);
    if (currentUrl.searchParams.get(AUTO_RELOAD_PARAM) === deployedBuildId) {
      window.sessionStorage.setItem(AUTO_RELOAD_BUILD_KEY, deployedBuildId);
      window.localStorage.setItem(AUTO_RELOAD_PERSIST_KEY, deployedBuildId);
      lastReservedAutoReloadBuildId = deployedBuildId;
      return false;
    }
  } catch {
    // ignore URL/storage failures and fall back to the other guards below
  }
  try {
    if (window.sessionStorage.getItem(AUTO_RELOAD_BUILD_KEY) === deployedBuildId) {
      lastReservedAutoReloadBuildId = deployedBuildId;
      return false;
    }
    if (window.localStorage.getItem(AUTO_RELOAD_PERSIST_KEY) === deployedBuildId) {
      lastReservedAutoReloadBuildId = deployedBuildId;
      return false;
    }
    window.sessionStorage.setItem(AUTO_RELOAD_BUILD_KEY, deployedBuildId);
    window.localStorage.setItem(AUTO_RELOAD_PERSIST_KEY, deployedBuildId);
  } catch {
    // Storage may be blocked in hardened webviews. Keep a module-level guard so
    // a single running bundle still cannot schedule a reload storm.
  }
  lastReservedAutoReloadBuildId = deployedBuildId;
  return true;
}

function clearAutoReloadGuardForCurrentBuild(deployedBuildId: string): void {
  if (deployedBuildId !== BUILD_ID) return;
  lastReservedAutoReloadBuildId = null;
  try {
    if (window.sessionStorage.getItem(AUTO_RELOAD_BUILD_KEY) === deployedBuildId) {
      window.sessionStorage.removeItem(AUTO_RELOAD_BUILD_KEY);
    }
    if (window.localStorage.getItem(AUTO_RELOAD_PERSIST_KEY) === deployedBuildId) {
      window.localStorage.removeItem(AUTO_RELOAD_PERSIST_KEY);
    }
  } catch {
    // ignore
  }
}

export function buildAutoReloadUrl(currentHref: string, deployedBuildId: string): string {
  const url = new URL(currentHref);
  url.searchParams.set(AUTO_RELOAD_PARAM, deployedBuildId);
  url.searchParams.set(AUTO_RELOAD_TS_PARAM, Date.now().toString(36));
  return url.toString();
}

function reloadToDeployedBuild(deployedBuildId: string): void {
  window.location.replace(buildAutoReloadUrl(window.location.href, deployedBuildId));
}

export function installVersionUpdateWatch(): () => void {
  if (typeof window === "undefined") return () => undefined;
  // Only meaningful against a built bundle whose APP_VERSION pairs with a
  // deployed version.json. The dev server has neither and handles updates via
  // HMR, so there is nothing to watch.
  if (!import.meta.env.PROD) return () => undefined;

  let stopped = false;
  let inFlight = false;
  let intervalId = 0;

  const stop = (): void => {
    if (stopped) return;
    stopped = true;
    window.clearInterval(intervalId);
    document.removeEventListener("visibilitychange", onVisible);
  };

  const check = async (): Promise<void> => {
    // Skip while hidden: a backgrounded tab left open for hours shouldn't keep
    // hitting the origin. onVisible re-checks promptly the moment it refocuses.
    if (inFlight || stopped || document.visibilityState !== "visible") return;
    inFlight = true;
    const deployed = await fetchDeployedBuildId();
    inFlight = false;
    if (stopped) return;
    if (deployed !== null) clearAutoReloadGuardForCurrentBuild(deployed);
    if (deployedVersionDiffers(deployed, BUILD_ID)) {
      markUpdateAvailable();
      if (deployed !== null && reserveAutoReloadForDeployedBuild(deployed)) {
        requestAppUpdateReload(() => reloadToDeployedBuild(deployed));
      }
      // The signal is sticky; no reason to keep polling once we've nudged.
      stop();
    }
  };

  const onVisible = (): void => {
    if (document.visibilityState === "visible") void check();
  };

  // Check once now, then poll while visible and re-check on refocus (covers the
  // common "left it open overnight, came back" case promptly).
  void check();
  intervalId = window.setInterval(() => void check(), POLL_INTERVAL_MS);
  document.addEventListener("visibilitychange", onVisible);

  return stop;
}

export function resetVersionUpdateWatchForTests(): void {
  lastReservedAutoReloadBuildId = null;
  try {
    window.sessionStorage.removeItem(AUTO_RELOAD_BUILD_KEY);
    window.localStorage.removeItem(AUTO_RELOAD_PERSIST_KEY);
  } catch {
    // ignore
  }
}
