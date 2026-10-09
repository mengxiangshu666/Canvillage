// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from "vitest";

import { composeBuildId } from "../../../build-id";

describe("composeBuildId", () => {
  it("changes across dirty-tree builds of the same commit", () => {
    const first = composeBuildId("aaee2ca-dirty", 1_753_286_400_001);
    const second = composeBuildId("aaee2ca-dirty", 1_753_286_400_002);

    expect(first).not.toBe(second);
    expect(first).toContain("aaee2ca-dirty");
    expect(second).toContain("aaee2ca-dirty");
  });

  it("still emits a unique timestamped id outside a git checkout", () => {
    expect(composeBuildId(null, 1_753_286_400_001)).toContain("nogit");
  });
});
