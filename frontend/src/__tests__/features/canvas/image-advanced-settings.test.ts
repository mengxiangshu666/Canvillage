// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from "vitest";

import { buildImageAdvancedSettings } from "@/features/canvas/domain/imageAdvancedSettings";

describe("buildImageAdvancedSettings", () => {
  it("returns null when there is nothing to send", () => {
    expect(buildImageAdvancedSettings(undefined)).toBeNull();
    expect(buildImageAdvancedSettings(null)).toBeNull();
    expect(buildImageAdvancedSettings({})).toBeNull();
  });

  it("hides `quality` because the request already carries it as a first-class field", () => {
    expect(buildImageAdvancedSettings({ quality: "high" })).toBeNull();
    expect(buildImageAdvancedSettings({ quality: "high", stylize: 100 })).toEqual({
      stylize: 100,
    });
  });

  it("drops empty values but keeps 0 and false", () => {
    expect(
      buildImageAdvancedSettings({
        personalisation: "",
        stylize: 0,
        chaos: null,
        weird: undefined,
        niji: false,
      }),
    ).toEqual({ stylize: 0, niji: false });
  });

  it("carries the whole declared parameter map through unchanged", () => {
    expect(
      buildImageAdvancedSettings({
        personalisation: "abc123",
        stylize: 250,
        weird: 100,
        chaos: 5,
      }),
    ).toEqual({ personalisation: "abc123", stylize: 250, weird: 100, chaos: 5 });
  });
});
