// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from "vitest";

import {
  buildVideoPromptOptimizationReferences,
  mergePromptOptimizationLockedConstraints,
} from "./promptOptimizationPayload";

describe("buildVideoPromptOptimizationReferences", () => {
  it("does not expose connected media that text-to-video will not submit", () => {
    expect(
      buildVideoPromptOptimizationReferences("textToVideo", [
        { kind: "image" },
        { kind: "video" },
      ]),
    ).toEqual([]);
  });

  it("assigns first/last frame roles using the same per-type numbering as the node", () => {
    expect(
      buildVideoPromptOptimizationReferences("firstLastFrame", [
        { kind: "image" },
        { kind: "audio" },
        { kind: "image" },
        { kind: "image" },
      ]),
    ).toEqual([
      { name: "@图片1", kind: "image", order: 1, role: "first_frame" },
      { name: "@图片2", kind: "image", order: 2, role: "last_frame" },
    ]);
  });

  it("preserves Seedance omni multimodal names and excludes over-cap items", () => {
    expect(
      buildVideoPromptOptimizationReferences("allReference", [
        { kind: "audio" },
        { kind: "image" },
        { kind: "video" },
        { kind: "image", withinCap: false },
      ]),
    ).toEqual([
      { name: "@音频1", kind: "audio", order: 1, role: "music_or_audio_reference" },
      { name: "@图片1", kind: "image", order: 2, role: "identity_scene_or_prop_reference" },
      { name: "@视频1", kind: "video", order: 3, role: "motion_or_camera_reference" },
    ]);
  });

  it("passes media urls to backend so prompt optimization can run vision pre-pass", () => {
    expect(
      buildVideoPromptOptimizationReferences("imageToVideo", [
        { kind: "image", url: "/static/user/project/freezone/_uploads/cat.png", label: "橘猫" },
      ]),
    ).toEqual([
      {
        name: "@图片1",
        kind: "image",
        order: 1,
        role: "first_frame",
        url: "/static/user/project/freezone/_uploads/cat.png",
        label: "橘猫",
      },
    ]);
  });

  it("matches HappyHorse video-edit submission limits", () => {
    const refs = buildVideoPromptOptimizationReferences("videoEdit", [
      { kind: "video" },
      { kind: "video" },
      ...Array.from({ length: 6 }, () => ({ kind: "image" as const })),
      { kind: "audio" },
    ]);
    expect(refs.map((item) => item.name)).toEqual([
      "@视频1",
      "@图片1",
      "@图片2",
      "@图片3",
      "@图片4",
      "@图片5",
    ]);
    expect(refs[0]?.role).toBe("source_video");
  });
});

describe("mergePromptOptimizationLockedConstraints", () => {
  it("restores identity, wardrobe, composition, negative, and reference locks omitted by the optimizer", () => {
    const original = [
      "电影感雨夜街头",
      "锁定身份：ROLE_A 的脸型与发色保持不变",
      "服装固定：红色风衣",
      "构图必须为半身正面",
      "负向约束：不要文字，不要水印",
      "必须沿用 @图片2 的灯光方向",
    ].join("；");
    const optimized = "雨夜街头的电影感半身人像，霓虹反射，高细节。";

    const merged = mergePromptOptimizationLockedConstraints(original, optimized);

    expect(merged).toContain("锁定身份：ROLE_A 的脸型与发色保持不变");
    expect(merged).toContain("服装固定：红色风衣");
    expect(merged).toContain("构图必须为半身正面");
    expect(merged).toContain("负向约束：不要文字，不要水印");
    expect(merged).toContain("必须沿用 @图片2 的灯光方向");
  });

  it("does not duplicate locked clauses already present in the optimized prompt", () => {
    const locked = "构图必须为半身正面";
    const merged = mergePromptOptimizationLockedConstraints(locked, `电影感；${locked}`);
    expect(merged.match(/构图必须为半身正面/g)).toHaveLength(1);
  });
});
