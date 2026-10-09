// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from "vitest";

import {
  buildGeneratedRightsPatch,
  buildRightsPatch,
  hasNodeRights,
  normalizeCopyrightChain,
  normalizeProtectionType,
  readDefaultCopyrightName,
  writeDefaultCopyrightName,
} from "@/features/canvas/domain/nodeRights";

describe("nodeRights", () => {
  it("keeps a named copyright chain and trims the name", () => {
    expect(normalizeCopyrightChain({ name: "  村长工作室 ", uuid: "u-1" })).toEqual({
      name: "村长工作室",
      uuid: "u-1",
    });
  });

  it("treats a nameless chain as no credit instead of an empty badge", () => {
    expect(normalizeCopyrightChain({ name: "   ", uuid: "u-1" })).toBeNull();
    expect(normalizeCopyrightChain(null)).toBeNull();
    expect(normalizeCopyrightChain("村长")).toBeNull();
    expect(normalizeCopyrightChain([{ name: "村长" }])).toBeNull();
  });

  it("defaults a missing uuid to empty string rather than dropping the credit", () => {
    expect(normalizeCopyrightChain({ name: "村长" })).toEqual({ name: "村长", uuid: "" });
  });

  it("only accepts watermark as a protection type", () => {
    expect(normalizeProtectionType("watermark")).toBe("watermark");
    expect(normalizeProtectionType(" WaterMark ")).toBe("watermark");
    expect(normalizeProtectionType("drm")).toBeNull();
    expect(normalizeProtectionType(null)).toBeNull();
  });

  it("omits unresolved fields so a generated patch cannot wipe a manual credit", () => {
    expect(buildRightsPatch({})).toEqual({});
    expect(buildRightsPatch({ copyrightChain: { name: "村长" } })).toEqual({
      copyrightChain: { name: "村长", uuid: "" },
    });
    expect(buildRightsPatch({ protectionType: "watermark" })).toEqual({
      protectionType: "watermark",
    });
  });

  it("reports node rights only when something is displayable", () => {
    expect(hasNodeRights({})).toBe(false);
    expect(hasNodeRights(null)).toBe(false);
    expect(hasNodeRights({ copyrightChain: { name: "" } })).toBe(false);
    expect(hasNodeRights({ protectionType: "watermark" })).toBe(true);
    expect(hasNodeRights({ copyrightChain: { name: "村长" } })).toBe(true);
  });

  it("remembers the last credit locally and survives unusable storage", () => {
    writeDefaultCopyrightName("村长工作室");
    expect(readDefaultCopyrightName()).toBe("村长工作室");

    writeDefaultCopyrightName("");
    expect(readDefaultCopyrightName()).toBe("");
  });

  it("auto-credits generated media only once a default credit exists", () => {
    writeDefaultCopyrightName("");
    expect(buildGeneratedRightsPatch({})).toEqual({});

    writeDefaultCopyrightName("村长工作室");
    expect(buildGeneratedRightsPatch({})).toEqual({
      copyrightChain: { name: "村长工作室", uuid: "" },
    });
  });

  it("never overwrites a credit the user already set on the node", () => {
    writeDefaultCopyrightName("村长工作室");
    expect(
      buildGeneratedRightsPatch({ copyrightChain: { name: "别人", uuid: "u-9" } }),
    ).toEqual({});
  });
});
