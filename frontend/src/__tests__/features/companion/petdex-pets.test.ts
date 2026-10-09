// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from "vitest";

import {
  DEFAULT_BUILTIN_PET_SLUG,
  PETDEX_STATES,
  petdexStateForAction,
  resolveActivePet,
  type PetdexCatalogEntry,
} from "@/features/companion/petdex/petdex-pets";

describe("petdexStateForAction", () => {
  it("maps task states to petdex rows: running→Running, success→Waving, failure→Failed", () => {
    expect(petdexStateForAction("typing")).toBe(PETDEX_STATES.running); // 任务进行中, row 7
    expect(petdexStateForAction("flag")).toBe(PETDEX_STATES.waving); // 任务成功, row 3
    expect(petdexStateForAction("repair")).toBe(PETDEX_STATES.failed); // 任务失败, row 5
  });

  it("falls back to idle for the idle action", () => {
    expect(petdexStateForAction("idle")).toBe(PETDEX_STATES.idle);
  });

  it("each state row/frames matches the petdex 9-state layout", () => {
    expect(PETDEX_STATES.idle).toEqual({ row: 0, frames: 6 });
    expect(PETDEX_STATES.runRight).toEqual({ row: 1, frames: 8 });
    expect(PETDEX_STATES.failed).toEqual({ row: 5, frames: 8 });
    expect(PETDEX_STATES.review).toEqual({ row: 8, frames: 6 });
  });
});

describe("resolveActivePet", () => {
  const zhizhi: PetdexCatalogEntry = {
    slug: "zhizhi",
    displayName: "枝枝",
    spritesheetUrl: "/petdex/zhizhi.webp",
  };
  const extra: PetdexCatalogEntry = {
    slug: "extra-pet",
    displayName: "第二只",
    spritesheetUrl: "/petdex/extra-pet.webp",
  };
  const imported: PetdexCatalogEntry = {
    slug: "mine",
    displayName: "我导入的",
    spritesheetUrl: "blob:mine",
    imported: true,
  };
  const noImported = new Map<string, PetdexCatalogEntry>();

  it("takes the built-in entry from the catalog, not from the persisted copy", () => {
    const stale: PetdexCatalogEntry = {
      slug: "zhizhi",
      displayName: "枝枝",
      spritesheetUrl: "/petdex/zhizhi.webp?old=1",
    };
    expect(
      resolveActivePet({
        kind: "zhizhi",
        selected: stale,
        importedBySlug: noImported,
        catalog: [zhizhi],
      }),
    ).toBe(zhizhi);
  });

  it("falls back to the first catalog entry when the selected pet was removed", () => {
    const removed: PetdexCatalogEntry = {
      slug: "glow-wolf-pet",
      displayName: "流光小狼崽",
      spritesheetUrl: "/petdex/glow-wolf-pet.png",
    };
    expect(
      resolveActivePet({
        kind: removed.slug,
        selected: removed,
        importedBySlug: noImported,
        catalog: [zhizhi, extra],
      }),
    ).toBe(zhizhi);
  });

  it("defaults to the shipped pet when nothing was ever selected", () => {
    expect(
      resolveActivePet({
        kind: DEFAULT_BUILTIN_PET_SLUG,
        selected: null,
        importedBySlug: noImported,
        catalog: [zhizhi],
      }),
    ).toBe(zhizhi);
  });

  it("resolves an imported pet by slug through the session blob map", () => {
    expect(
      resolveActivePet({
        kind: "mine",
        selected: imported,
        importedBySlug: new Map([["mine", imported]]),
        catalog: [zhizhi],
      }),
    ).toBe(imported);
  });

  it("falls back instead of rendering nothing when an imported blob is gone", () => {
    expect(
      resolveActivePet({
        kind: "mine",
        selected: imported,
        importedBySlug: noImported,
        catalog: [zhizhi],
      }),
    ).toBe(zhizhi);
  });

  it("returns null while the catalog has not loaded yet", () => {
    expect(
      resolveActivePet({
        kind: DEFAULT_BUILTIN_PET_SLUG,
        selected: null,
        importedBySlug: noImported,
        catalog: [],
      }),
    ).toBeNull();
  });
});
