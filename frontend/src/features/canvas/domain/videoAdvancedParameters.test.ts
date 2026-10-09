// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from "vitest";
import {
  isRenderableVideoParameter,
  visibleVideoAdvancedParameters,
} from "./videoAdvancedParameters";

const AUTODL_MEDIA_DEFAULTS = "default_local_path:/blank.png;https://cdn.example/blank.png";

describe("visibleVideoAdvancedParameters", () => {
  it("drops AutoDL transport media slots that the node fills from upstream edges", () => {
    const parameters = [
      { key: "duration", type: "integer", advanced: false },
      { key: "prompt", type: "string", required: true, advanced: false },
      { key: "ref_image_0", type: "image", advanced: true, default: AUTODL_MEDIA_DEFAULTS },
      { key: "ref_image_1", type: "image", advanced: true, default: AUTODL_MEDIA_DEFAULTS },
      { key: "ref_audio_0", type: "audio", advanced: true, default: AUTODL_MEDIA_DEFAULTS },
      { key: "ref_video", type: "video", advanced: true },
      { key: "seed", type: "integer", advanced: true, default: 42 },
    ];

    expect(visibleVideoAdvancedParameters(parameters, []).map((item) => item.key)).toEqual([
      "seed",
    ]);
  });

  it("keeps prompt out of the popover because the node has its own editor", () => {
    expect(isRenderableVideoParameter({ key: "prompt", type: "string" })).toBe(false);
  });

  it("keeps provider-specific controls that no canvas control owns", () => {
    expect(
      visibleVideoAdvancedParameters(
        [
          { key: "motion_strength", type: "number" },
          { key: "camera_control", type: "string" },
          { key: "watermark", type: "boolean" },
        ],
        [],
      ).map((item) => item.key),
    ).toEqual(["motion_strength", "camera_control", "watermark"]);
  });

  it("folds opaque fields in, inferring their type, and still filters transport keys", () => {
    const visible = visibleVideoAdvancedParameters([], [
      { key: "supportsCameraControl", value: true },
      { key: "ref_image_0", value: AUTODL_MEDIA_DEFAULTS },
    ]);

    expect(visible).toEqual([
      {
        key: "supportsCameraControl",
        providerKey: "supportsCameraControl",
        type: "boolean",
        default: true,
        advanced: true,
        source: "catalog",
      },
    ]);
  });

  it("lets a declared parameter win over the same key seen as opaque", () => {
    const visible = visibleVideoAdvancedParameters(
      [{ key: "stylize", type: "integer", default: 100 }],
      [{ key: "stylize", value: "opaque" }],
    );

    expect(visible).toHaveLength(1);
    expect(visible[0].type).toBe("integer");
    expect(visible[0].default).toBe(100);
  });

  it("ignores blank keys and blank-typed entries", () => {
    expect(
      visibleVideoAdvancedParameters([{ key: "   ", type: "string" }, { key: "seed" }], []).map(
        (item) => item.key,
      ),
    ).toEqual(["seed"]);
  });
});
