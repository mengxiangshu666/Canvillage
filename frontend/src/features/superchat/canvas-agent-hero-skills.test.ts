// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from "vitest";

import {
  CANVAS_AGENT_HERO_SKILL_CONTRACTS,
  compactHeroSkillContract,
  heroSkillContractsFor,
  heroSkillIdsForIntent,
} from "./canvas-agent-hero-skills";

describe("canvas Agent hero skill runtime contracts", () => {
  it("routes the three high-value intents to deterministic runtime contracts", () => {
    expect(heroSkillIdsForIntent("storyboard")).toEqual(["storyboard"]);
    expect(heroSkillIdsForIntent("identity_continuity")).toEqual(["identity-continuity"]);
    expect(heroSkillIdsForIntent("failure_rescue")).toEqual(["failure-rescue"]);
    expect(heroSkillIdsForIntent("model_config")).toEqual([]);
  });

  it("deduplicates explicit and intent-routed hero skills", () => {
    expect(heroSkillContractsFor({
      intentId: "storyboard",
      skillIds: ["storyboard", "identity-continuity", "storyboard"],
    }).map((contract) => contract.id)).toEqual(["storyboard", "identity-continuity"]);
  });

  it.each([
    {
      id: "storyboard" as const,
      label: "分镜大师",
      completionEvidence: ["server_applied=true", "revision", "applied_ops", "created_node_ids"],
    },
    {
      id: "identity-continuity" as const,
      label: "角色一致性",
      completionEvidence: ["revision", "applied_ops", "仅聊天分析不算完成"],
    },
    {
      id: "failure-rescue" as const,
      label: "失败救援",
      completionEvidence: ["真实任务或运行证据", "revision", "applied_ops"],
    },
  ])("exposes a receipt-gated runtime contract for $label", ({ id, label, completionEvidence }) => {
    const contract = compactHeroSkillContract(CANVAS_AGENT_HERO_SKILL_CONTRACTS[id]);
    const requiredFields = [
      "inputs",
      "observe",
      "write",
      "command_policy",
      "output",
      "quality_gate",
      "completion",
      "failure_stop",
    ] as const;

    expect(contract).toMatchObject({ id, label });
    expect(Object.keys(contract)).toEqual(expect.arrayContaining(Array.from(requiredFields)));
    for (const field of requiredFields.filter((field) => field !== "completion")) {
      expect(contract[field]).toEqual(expect.arrayContaining([expect.any(String)]));
    }

    const completionGate = [
      String(contract.completion),
      ...((contract.quality_gate as readonly string[]) ?? []),
    ].join(" ");
    for (const evidence of completionEvidence) {
      expect(completionGate).toContain(evidence);
    }
    expect(completionGate).toMatch(/server_applied|真实任务或运行证据|仅聊天分析不算完成/);
  });

  it("keeps failure rescue evidence-first and retry-bounded", () => {
    const contract = compactHeroSkillContract(CANVAS_AGENT_HERO_SKILL_CONTRACTS["failure-rescue"]);
    expect(contract).toMatchObject({
      id: "failure-rescue",
      write: expect.arrayContaining([
        "freezone_emit_canvas_command",
        "freezone_retry_node",
        "freezone_stop_task",
      ]),
    });
    expect(contract.command_policy).toContain("参数/引用问题先 update_node_prompt 或 annotate，只改一个高影响变量。");
    expect(contract.failure_stop).toContain("同一失败最多一次有效重试；没有新证据就停止，不重复提交。");
  });
});
