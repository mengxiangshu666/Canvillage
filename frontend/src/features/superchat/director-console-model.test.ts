// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from "vitest";

import { deriveDirectorConsoleState } from "./director-console-model";

const pin = {
  id: "n1",
  type: "imageGenNode",
  label: "主角",
  hasImage: true,
  hasPrompt: true,
};

describe("deriveDirectorConsoleState", () => {
  it("empty console asks to mount skill or pin", () => {
    const state = deriveDirectorConsoleState({
      skillIds: [],
      pinnedNodes: [],
      selectedNode: null,
    });
    expect(state.readiness).toBe("empty");
    expect(state.nextStep.id).toBe("mount-context");
    expect(state.gates).toHaveLength(4);
    expect(state.gates.map((gate) => gate.id)).toEqual([
      "interpretation",
      "plan",
      "canvas_write",
      "paid_media",
    ]);
  });

  it("pins without skills still suggest mounting a skill", () => {
    const state = deriveDirectorConsoleState({
      skillIds: [],
      pinnedNodes: [pin],
      selectedNode: null,
    });
    expect(state.readiness).toBe("partial");
    expect(state.counts.pins).toBe(1);
    expect(state.nextStep.id).toBe("mount-skill");
  });

  it("skills + pins become ready for a structure order", () => {
    const state = deriveDirectorConsoleState({
      skillIds: ["canvas-director", "storyboard"],
      pinnedNodes: [pin],
      selectedNode: null,
      hasUserMessages: false,
    });
    expect(state.readiness).toBe("ready");
    expect(state.counts.skills).toBe(2);
    expect(state.mountedSkills.map((skill) => skill.id)).toEqual([
      "canvas-director",
      "storyboard",
    ]);
    expect(state.nextStep.id).toBe("first-order");
    expect(state.gates.find((gate) => gate.id === "interpretation")?.tone).toBe("ready");
  });

  it("busy run overrides next step", () => {
    const state = deriveDirectorConsoleState({
      skillIds: ["canvas-director"],
      pinnedNodes: [pin],
      selectedNode: null,
      busy: true,
      planSteps: [{ id: "t1", label: "snapshot", status: "running" }],
    });
    expect(state.readiness).toBe("running");
    expect(state.nextStep.id).toBe("wait-run");
    expect(state.gates.find((gate) => gate.id === "plan")?.tone).toBe("active");
  });

  it("locks media in draft mode while canvas writes stay directly executable", () => {
    const state = deriveDirectorConsoleState({
      skillIds: ["one-click-film"],
      pinnedNodes: [],
      selectedNode: pin,
      runMode: "draft",
    });
    expect(state.gates.find((gate) => gate.id === "paid_media")?.tone).toBe("warn");
    expect(state.gates.find((gate) => gate.id === "canvas_write")?.tone).toBe("ready");
    expect(state.gates.find((gate) => gate.id === "canvas_write")?.detail).toContain("直接执行");
    expect(state.gates.find((gate) => gate.id === "paid_media")?.detail).toContain("草稿模式");
  });

  it("shows current-turn auto authorization instead of a second confirmation gate", () => {
    const state = deriveDirectorConsoleState({
      skillIds: ["one-click-film"],
      pinnedNodes: [],
      selectedNode: pin,
      runMode: "auto",
    });
    const mediaGate = state.gates.find((gate) => gate.id === "paid_media");
    expect(mediaGate?.tone).toBe("ready");
    expect(mediaGate?.label).toBe("自动生成授权");
    expect(mediaGate?.detail).toContain("不再逐节点确认");
  });
});
