// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from "vitest";

import { compileExpressionRigContract, resolveMorphBindingNames } from "./ExpressionHeadPreview";
import { affectFromMoodCell } from "./emotionMoodGrid";

describe("compileExpressionRigContract", () => {
  it("compiles the same high-dimensional state used by the live preview", () => {
    const joyAffect = affectFromMoodCell(2, 4); // pleasant-joy
    const angerAffect = affectFromMoodCell(1, 0); // intense-anger
    const joy = compileExpressionRigContract(joyAffect.x, joyAffect.y);
    const anger = compileExpressionRigContract(angerAffect.x, angerAffect.y);

    expect(joy.revision).toBe("expression-rig-contract.v2");
    expect(joy.weights.mouthSmileLeft).toBeGreaterThan(0.7);
    expect(joy.features.join(" ")).toContain("mouth corners pulled upward");
    expect(anger.weights.browDownLeft).toBeGreaterThan(0.7);
    expect(anger.features.join(" ")).toMatch(/brows pulled down|nose wrinkled/);
    expect(joy.prompt).not.toEqual(anger.prompt);
    expect(joy.prompt.length).toBeLessThan(400);
    expect(joy.prompt).not.toContain("EXACT FACIAL RIG BLUEPRINT");
  });

  it("keeps a short geometry summary for uplink while retaining full feature metadata", () => {
    const helplessAffect = affectFromMoodCell(8, 4); // helpless-smile
    const helplessSmile = compileExpressionRigContract(helplessAffect.x, helplessAffect.y);
    expect(helplessSmile.prompt.length).toBeGreaterThan(20);
    expect(helplessSmile.prompt.length).toBeLessThan(500);
    expect(helplessSmile.features.length).toBeGreaterThan(0);
    expect(Object.keys(helplessSmile.weights).length).toBeGreaterThan(8);
    expect(helplessSmile.prompt).not.toContain("EXACT FACIAL RIG BLUEPRINT");
  });
});

describe("resolveMorphBindingNames", () => {
  it("drives both sides when the asset only exports xxxLeft/xxxRight", () => {
    // 仓库里唯一完好的头模就是这种命名：没有不带后缀的 browInnerUp / cheekPuff。
    const available = new Set(["browInnerUpLeft", "browInnerUpRight", "cheekPuffLeft", "cheekPuffRight", "jawOpen"]);
    const resolved = resolveMorphBindingNames(available, ["browInnerUp", "cheekPuff", "jawOpen", "eyeBlinkLeft"]);

    expect(resolved.get("browInnerUp")).toEqual(["browInnerUpLeft", "browInnerUpRight"]);
    expect(resolved.get("cheekPuff")).toEqual(["cheekPuffLeft", "cheekPuffRight"]);
    expect(resolved.get("jawOpen")).toEqual(["jawOpen"]);
    // 实例完全没有的形状必须整条丢弃，否则 playcanvas 每个缺失名都会打日志。
    expect(resolved.has("eyeBlinkLeft")).toBe(false);
  });

  it("prefers the unsuffixed name when the asset exports one", () => {
    const available = new Set(["browInnerUp", "cheekPuff", "browInnerUpLeft", "browInnerUpRight"]);
    const resolved = resolveMorphBindingNames(available, ["browInnerUp", "cheekPuff"]);

    expect(resolved.get("browInnerUp")).toEqual(["browInnerUp"]);
    expect(resolved.get("cheekPuff")).toEqual(["cheekPuff"]);
  });
});
