// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from "vitest";

import {
  buildFastCanvasAgentKernelPlan,
  compactCanvasAgentKernelPlan,
} from "./canvas-agent-kernel";
import { buildCanvasAgentRequest } from "./canvas-agent-skills";
import { CANVAS_AGENT_AUTO_PAID_START_LIMIT } from "./canvas-agent-run-mode";

function parseV2Envelope(value: string): Record<string, unknown> {
  const match = value.match(
    /^\[CANVAS_AGENT_REQUEST_V2\]([\s\S]+)\[\/CANVAS_AGENT_REQUEST_V2\]$/,
  );
  expect(match).not.toBeNull();
  return JSON.parse(match![1]) as Record<string, unknown>;
}

describe("fast canvas Agent kernel", () => {
  it("routes a fruit short drama into an executable draft workflow plan", () => {
    const plan = buildFastCanvasAgentKernelPlan({
      userText: "帮我做一个水果短剧，先把工作流搭出来",
      selectedSkillIds: ["canvas-director"],
      runMode: "draft",
      pinnedNodes: [],
    });

    expect(plan.intent.id).toBe("one_click_film");
    expect(plan.skillIds).toEqual(expect.arrayContaining(["one-click-film", "storyboard", "delivery-qc"]));
    expect(plan.heroSkillIds).toEqual(["storyboard"]);
    expect(plan.executionRoute).toMatchObject({
      lane: "canvas_execute",
      mayWriteCanvas: true,
      mayStartWorkflow: false,
    });
    expect(plan.taskAuthorization).toMatchObject({
      scope: "current_turn",
      run_mode: "draft",
      allow_structure: true,
      allow_paid_media: false,
      max_paid_starts: 0,
      require_video_confirmation: false,
    });
    expect(plan.workflowCandidates.length).toBeGreaterThan(0);
    expect(plan.draftActions).toEqual(expect.arrayContaining([
      expect.objectContaining({ type: "inspect_canvas" }),
      expect.objectContaining({ type: "connect_nodes" }),
    ]));
    expect(plan.draftActions.some((action) => action.type === "insert_starter_workflow")).toBe(false);
    expect(plan.immediateGuidance.join(" ")).not.toContain("优先工作流");
    expect(plan.spendGuard).toMatchObject({ mayStartPaidMedia: false });
    expect(plan.immediateGuidance.join(" ")).toContain("本轮路由：canvas_execute");
  });

  it("keeps workflow-build recommendations advisory instead of inserting a starter", () => {
    const plan = buildFastCanvasAgentKernelPlan({
      userText: "自动搭建一个视频制作工作流",
      selectedSkillIds: [],
      runMode: "draft",
      pinnedNodes: [],
    });

    expect(plan.intent.id).toBe("workflow_build");
    expect(plan.workflowCandidates[0]).toEqual(expect.objectContaining({ id: expect.any(String), title: expect.any(String) }));
    expect(plan.draftActions.some((action) => action.type === "insert_starter_workflow")).toBe(false);
    expect(plan.executionRoute).toMatchObject({
      lane: "canvas_execute",
      mayStartWorkflow: false,
    });
    expect(plan.immediateGuidance.join(" ")).toContain("placement(anchor=viewport_center, layout=grid)");
    expect(plan.immediateGuidance.join(" ")).toContain("不要为读取视口重复拉快照");
  });

  it("compiles storyboard work into one receipt-verifiable shot sequence", () => {
    const plan = buildFastCanvasAgentKernelPlan({
      userText: "把这段剧情拆成五个可拍分镜",
      selectedSkillIds: [],
      runMode: "draft",
      pinnedNodes: [{ id: "script-1", type: "scriptNode", label: "开场剧本", hasPrompt: true }],
    });

    expect(plan.intent.id).toBe("storyboard");
    expect(plan.heroSkillIds).toEqual(["storyboard"]);
    expect(plan.draftActions.some((action) => action.type === "insert_starter_workflow")).toBe(false);
    expect(plan.draftActions).toEqual(expect.arrayContaining([
      expect.objectContaining({
        type: "create_shot_sequence",
        command: expect.objectContaining({
          command: "create_shot_sequence",
          may_start_generation: false,
        }),
      }),
    ]));
  });

  it("preserves an explicit template ID instead of using the first recommendation", () => {
    const plan = buildFastCanvasAgentKernelPlan({
      userText: "按我选择的模板搭建工作流",
      selectedSkillIds: [],
      runMode: "draft",
      starterWorkflowId: "  video-composition  ",
    });
    expect(plan.draftActions).toContainEqual(expect.objectContaining({
      type: "insert_starter_workflow",
      command: expect.objectContaining({ workflow_id: "video-composition" }),
    }));
    expect(plan.immediateGuidance.join(" ")).toContain("已选择模板：video-composition");
  });

  it("leaves flag-only template selection to the server", () => {
    const plan = buildFastCanvasAgentKernelPlan({
      userText: "帮我搭建工作流",
      selectedSkillIds: [],
      runMode: "draft",
      useStarterWorkflow: true,
    });
    expect(plan.useStarterWorkflow).toBe(true);
    expect(plan.starterWorkflowId).toBeUndefined();
    expect(plan.draftActions.some((action) => action.type === "insert_starter_workflow")).toBe(false);
  });

  it("keeps failure rescue on evidence and one-variable repair instead of inserting a workflow", () => {
    const plan = buildFastCanvasAgentKernelPlan({
      userText: "视频任务 503 失败了，保留成功素材并从失败点恢复",
      selectedSkillIds: [],
      runMode: "auto",
      pinnedNodes: [],
    });

    expect(plan.intent.id).toBe("failure_rescue");
    expect(plan.heroSkillIds).toEqual(["failure-rescue"]);
    expect(plan.executionRoute).toMatchObject({
      lane: "canvas_execute",
      mayWriteCanvas: true,
      mayStartWorkflow: false,
    });
    expect(plan.draftActions.some((action) => action.type === "insert_starter_workflow")).toBe(false);
    expect(plan.draftActions).toEqual(expect.arrayContaining([
      expect.objectContaining({ type: "read_failure_evidence" }),
      expect.objectContaining({ type: "repair_minimum_variable" }),
    ]));
    expect(plan.spendGuard.mayStartPaidMedia).toBe(false);
  });

  it("does not turn a mounted Hero skill into a receipt gate for ordinary chat", () => {
    const plan = buildFastCanvasAgentKernelPlan({
      userText: "最终运行验收：只回复运行正常，不要调用工具。",
      selectedSkillIds: ["failure-rescue"],
      runMode: "draft",
      pinnedNodes: [],
    });
    const payload = parseV2Envelope(buildCanvasAgentRequest({
      userText: "最终运行验收：只回复运行正常，不要调用工具。",
      skillIds: plan.skillIds,
      explicitSkillIds: ["failure-rescue"],
      canvasContext: '{"project_id":"demo","canvas_id":"canvas-1"}',
      fastKernelPlan: plan,
      forceStructured: true,
    }));

    expect(plan.intent.id).toBe("delivery_qc");
    expect(plan.heroSkillIds).toEqual([]);
    expect(payload).not.toHaveProperty("fast_kernel_plan");
    expect(payload).not.toHaveProperty("kernel_policy");
    expect(payload.hero_skill_contracts).toContainEqual(expect.objectContaining({
      id: "failure-rescue",
    }));
  });

  it.each([
    {
      label: "分镜大师",
      userText: "把这段剧情拆成五个可拍分镜",
    },
    {
      label: "角色一致性",
      userText: "修复人物跑脸，固定身份、服装和骨相",
    },
    {
      label: "失败救援",
      userText: "视频任务失败了，保留成功素材并从失败点恢复",
    },
  ])("does not promote the $label keyword candidate into an execution contract", ({
    label,
    userText,
  }) => {
    const plan = buildFastCanvasAgentKernelPlan({
      userText,
      selectedSkillIds: [],
      runMode: "draft",
      selectedNode: { id: "node-1", type: "imageGenNode", label: "目标镜头", hasPrompt: true },
      pinnedNodes: [{ id: "anchor-1", type: "imageNode", label: "角色锚点", hasImage: true }],
    });
    const payload = parseV2Envelope(buildCanvasAgentRequest({
      userText,
      skillIds: [],
      canvasContext: '{"project_id":"demo","canvas_id":"canvas-1","revision":7}',
      runMode: "draft",
      fastKernelPlan: plan,
      forceStructured: true,
    }));

    expect(payload).not.toHaveProperty("fast_kernel_plan");
    expect(payload).not.toHaveProperty("kernel_policy");
    expect(payload).not.toHaveProperty("hero_skill_contracts");
    expect(payload.director_method_catalog).toContainEqual(expect.objectContaining({ label }));
    expect(payload.director_reasoning_contract).toMatchObject({ mode: "model_directed" });
  });

  it("routes prompt polish work to prompt polish and blocks paid media", () => {
    const plan = buildFastCanvasAgentKernelPlan({
      userText: "优化角色正负提示词，让运镜更稳，不要生成测试",
      selectedSkillIds: ["canvas-director"],
      runMode: "auto",
      selectedNode: { id: "node-prompt", type: "imageNode", label: "角色参考", hasPrompt: true },
      pinnedNodes: [{ id: "node-img", type: "imageNode", label: "角色参考", hasImage: true }],
    });

    expect(plan.intent.id).toBe("prompt_polish");
    expect(plan.skillIds).toEqual(expect.arrayContaining(["prompt-engineer", "identity-continuity"]));
    expect(plan.draftActions).toEqual(expect.arrayContaining([
      expect.objectContaining({ type: "update_node_prompt" }),
    ]));
    expect(plan.executionRoute).toMatchObject({
      lane: "canvas_execute",
      mayWriteCanvas: true,
      mayStartWorkflow: false,
    });
    expect(plan.spendGuard).toMatchObject({ mayStartPaidMedia: false });
    expect(plan.immediateGuidance.join(" ")).toContain("本轮路由：canvas_execute");
  });

  it("allows auto media only after the kernel passes spend guard", () => {
    const plan = buildFastCanvasAgentKernelPlan({
      userText: "做一个产品广告片并自动生成",
      selectedSkillIds: ["canvas-director"],
      runMode: "auto",
      pinnedNodes: [],
    });

    expect(plan.intent.id).toBe("one_click_film");
    expect(plan.spendGuard.mayStartPaidMedia).toBe(true);
    expect(plan.taskAuthorization).toMatchObject({
      run_mode: "auto",
      allow_paid_media: true,
      max_paid_starts: CANVAS_AGENT_AUTO_PAID_START_LIMIT,
    });
    expect(plan.draftActions[0]).toEqual(expect.objectContaining({ type: "inspect_canvas" }));
  });

  it("compacts the kernel plan to the backend snake_case contract", () => {
    const compact = compactCanvasAgentKernelPlan(buildFastCanvasAgentKernelPlan({
      userText: "帮我搭工作流",
      selectedSkillIds: [],
      runMode: "draft",
      pinnedNodes: [],
    }));

    expect(compact).toMatchObject({
      intent: expect.objectContaining({ id: "workflow_build" }),
      hero_skill_ids: ["storyboard"],
      execution_route: expect.objectContaining({
        lane: "canvas_execute",
        may_write_canvas: true,
        may_start_workflow: false,
      }),
      task_authorization: expect.objectContaining({
        scope: "current_turn",
        run_mode: "draft",
        allow_structure: true,
        allow_paid_media: false,
        max_paid_starts: 0,
        require_video_confirmation: false,
      }),
      response_mode: "draft",
      workflow_candidates: expect.any(Array),
      draft_actions: expect.any(Array),
      spend_guard: { may_start_paid_media: false, reason: expect.any(String) },
    });
  });

  it("tells the Agent to continue a receipt-verified workflow instead of reinserting it", () => {
    const plan = buildFastCanvasAgentKernelPlan({
      userText: "帮我做一个水果短片",
      selectedSkillIds: [],
      runMode: "draft",
      pinnedNodes: [],
    });
    const payload = parseV2Envelope(buildCanvasAgentRequest({
      userText: "帮我做一个水果短片",
      skillIds: plan.skillIds,
      canvasContext: '{"project_id":"demo","canvas_id":"canvas-1"}',
      runMode: "draft",
      fastKernelPlan: plan,
      workflowRuntime: {
        workflow_run_id: "wfr-1",
        workflow_id: "one-click-film",
        workflow_version: 1,
        status: "running",
        current_frontier: ["story_and_shots"],
        current_step: "story_and_shots",
        starter_workflow_id: "story-continuity-film",
        canvas_structure_command_id: "workflow:wfr-1:canvas_structure:v1",
        structure_already_applied: true,
        canvas_created_node_ids: ["node-a"],
        revision: 1,
      },
      forceStructured: true,
    }));

    expect(payload.workflow_runtime).toEqual(expect.objectContaining({
      workflow_run_id: "wfr-1",
      current_step: "story_and_shots",
      structure_already_applied: true,
    }));
    expect(payload.workflow_runtime_policy).toContain("禁止重复插入模板");
  });
});
