// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  buildAutoReloadUrl,
  deployedVersionDiffers,
  reserveAutoReloadForDeployedBuild,
  resetVersionUpdateWatchForTests,
} from "@/lib/version-update-watch";
import {
  dismissUpdateAvailable,
  markUpdateAvailable,
  registerAppUpdateReloadBlocker,
  requestAppUpdateReload,
  resetUpdateAvailableForTests,
  useUpdateAvailable,
} from "@/lib/app-update-available";

describe("deployedVersionDiffers", () => {
  it("is false when the deployed version matches the running one", () => {
    expect(deployedVersionDiffers("260630-abc123", "260630-abc123")).toBe(false);
  });

  it("is true when the deployed version differs from the running one", () => {
    expect(deployedVersionDiffers("260701-def456", "260630-abc123")).toBe(true);
  });

  it("is false when the manifest could not be read (null)", () => {
    // A failed/garbage fetch must never nag — better a missed nudge than a
    // false one on every poll.
    expect(deployedVersionDiffers(null, "260630-abc123")).toBe(false);
  });
});

describe("version update auto reload guard", () => {
  afterEach(() => {
    resetVersionUpdateWatchForTests();
    window.history.replaceState(null, "", "/");
    vi.useRealTimers();
  });

  it("allows only one automatic reload attempt for the same deployed build", () => {
    expect(reserveAutoReloadForDeployedBuild("build-new")).toBe(true);
    expect(reserveAutoReloadForDeployedBuild("build-new")).toBe(false);
    expect(reserveAutoReloadForDeployedBuild("build-newer")).toBe(true);
  });

  it("does not auto-reload again after the cache-busting URL has already been tried", () => {
    window.history.replaceState(
      null,
      "",
      "/projects/p1/freezone?__village_canvas_build=build-new",
    );

    expect(reserveAutoReloadForDeployedBuild("build-new")).toBe(false);
  });

  it("persists the reload guard across a fresh module instance in the same browser", () => {
    expect(reserveAutoReloadForDeployedBuild("build-new")).toBe(true);

    resetVersionUpdateWatchForTests();
    window.localStorage.setItem(
      "village-canvas:auto-update-reload-build:persistent",
      "build-new",
    );

    expect(reserveAutoReloadForDeployedBuild("build-new")).toBe(false);
  });

  it("builds a cache-busting reload URL without losing the current canvas route", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-06T00:00:00Z"));

    const next = new URL(buildAutoReloadUrl(
      "http://127.0.0.1:8784/projects/p1/freezone?canvas=c1",
      "build-new",
    ));

    expect(next.origin).toBe("http://127.0.0.1:8784");
    expect(next.pathname).toBe("/projects/p1/freezone");
    expect(next.searchParams.get("canvas")).toBe("c1");
    expect(next.searchParams.get("__village_canvas_build")).toBe("build-new");
    expect(next.searchParams.get("__village_canvas_ts")).toBe(Date.now().toString(36));
  });
});

describe("app-update-available store", () => {
  afterEach(() => {
    resetUpdateAvailableForTests();
    vi.useRealTimers();
  });

  it("flips to available and back to hidden on dismiss", () => {
    const { result } = renderHook(() => useUpdateAvailable());
    expect(result.current).toBe(false);

    act(() => markUpdateAvailable());
    expect(result.current).toBe(true);

    act(() => dismissUpdateAvailable());
    expect(result.current).toBe(false);
  });

  it("is idempotent: a second markUpdateAvailable after dismiss stays hidden", () => {
    const { result } = renderHook(() => useUpdateAvailable());
    act(() => markUpdateAvailable());
    act(() => dismissUpdateAvailable());
    act(() => markUpdateAvailable());
    // Once dismissed for this session we never re-nag.
    expect(result.current).toBe(false);
  });

  it("ignores dismiss before an update is available", () => {
    const { result } = renderHook(() => useUpdateAvailable());
    act(() => dismissUpdateAvailable());
    expect(result.current).toBe(false);
  });

  it("auto-reloads only after unsaved or active work has settled", async () => {
    vi.useFakeTimers();
    let blocked = true;
    const reload = vi.fn();
    const unregister = registerAppUpdateReloadBlocker(() => blocked);

    requestAppUpdateReload(reload);
    await vi.advanceTimersByTimeAsync(500);
    expect(reload).not.toHaveBeenCalled();

    blocked = false;
    await vi.advanceTimersByTimeAsync(500);
    expect(reload).toHaveBeenCalledTimes(1);
    unregister();
  });

  it("coalesces repeated update notices into one safe reload", async () => {
    vi.useFakeTimers();
    const reload = vi.fn();
    requestAppUpdateReload(reload);
    requestAppUpdateReload(reload);

    await vi.advanceTimersByTimeAsync(500);
    expect(reload).toHaveBeenCalledTimes(1);
  });
});
