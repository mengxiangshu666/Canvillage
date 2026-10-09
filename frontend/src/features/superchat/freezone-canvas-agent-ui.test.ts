// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { fireEvent, render, screen } from "@testing-library/react";
import { createElement } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  clampFreezoneAgentWidth,
  defaultFreezoneAgentWidth,
  deriveAgentPlanFromMessages,
  FREEZONE_AGENT_DRAWER_CLASS,
  FREEZONE_AGENT_WELCOME_CLASS,
  FreezoneAgentContextCard,
  FreezoneAgentCompactStatus,
  FreezoneAgentRunBar,
  humanizeAgentExecutionStage,
  FreezoneComposerSkillDrawer,
  FreezoneComposerTags,
  FreezoneWelcomeSkills,
  LIBTV_CHAT_RICH_INPUT_PLACEHOLDER,
  LIBTV_HEADER_SUBTITLE,
  LIBTV_SKILL_CARD_CLASS,
  LIBTV_WELCOME_CREATE_TOGETHER,
  LIBTV_WELCOME_GREETING,
  LIBTV_WELCOME_SKILL_REFRESH,
  LIBTV_WELCOME_SKILL_TITLES,
  skillIconFor,
} from "./freezone-canvas-agent-ui";
import { CANVAS_AGENT_SKILLS } from "./canvas-agent-skills";
import { deriveDirectorConsoleState } from "./director-console-model";
import type { AgentExecutionTimeline } from "./agent-execution-timeline";
import type { WorkflowRun } from "@/types/workflow-runtime";
import { useCanvasStore, type CanvasNode } from "@/stores/canvasStore";
import {
  loadDismissedWorkflowFailureKeys,
  saveDismissedWorkflowFailureKey,
  workflowCanvasRecoveryFromRun,
  workflowFailureDisplayKey,
  workflowRunHasActionableFailures,
  workflowRunHasRetryableFailures,
  workflowStepHasBlockingCanvasRecovery,
} from "./workflow-failure-dismissal";

describe("freezone LibTV-parity agent chrome", () => {
  it("keeps LibTV drawer and welcome class contracts", () => {
    expect(FREEZONE_AGENT_DRAWER_CLASS).toBe("canvas-agent-drawer-chat");
    expect(FREEZONE_AGENT_WELCOME_CLASS).toBe("chat-welcome-root");
    expect(LIBTV_SKILL_CARD_CLASS).toContain("rounded-2xl");
    expect(LIBTV_SKILL_CARD_CLASS).not.toContain("neo-agent-skill-card");
    expect(CANVAS_AGENT_SKILLS).toHaveLength(9);
  });

  it("copies LibTV welcome / composer surface contract", () => {
    expect(LIBTV_WELCOME_GREETING).toContain("小树");
    expect(LIBTV_WELCOME_SKILL_REFRESH).toBe("换一批");
    expect(LIBTV_WELCOME_SKILL_TITLES.length).toBe(17);
    expect(LIBTV_WELCOME_SKILL_TITLES[0]).toContain("说个想法");
    expect(LIBTV_HEADER_SUBTITLE).toBe("对齐目标，直接落地");
    expect(LIBTV_CHAT_RICH_INPUT_PLACEHOLDER).toMatch(/@|\/ /);
    expect(LIBTV_WELCOME_CREATE_TOGETHER).toContain("小树");
  });

  it("lets the standalone canvas collect welcome skills into the bottom drawer", () => {
    const { container } = render(createElement(FreezoneWelcomeSkills, {
      selectedIds: ["canvas-director"],
      onToggle: vi.fn(),
      showSkillPicker: false,
    }));

    expect(screen.getByText("今天一起创作点什么？")).toBeInTheDocument();
    expect(screen.queryByText("更多能力")).not.toBeInTheDocument();
    expect(container.querySelector(".neo-agent-welcome")).toBeNull();
    expect(container.querySelector(".neo-agent-skill-card")).toBeNull();
  });

  it("renders the canvas-only empty state as a compact LibTV-style Skill grid", () => {
    render(createElement(FreezoneWelcomeSkills, {
      selectedIds: [],
      onToggle: vi.fn(),
      compact: true,
    }));

    expect(screen.getByText("选择一个 Skill，让创作更快一步")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "换一批" })).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: /使用技能/ })).toHaveLength(4);
    expect(screen.queryByText("今天一起创作点什么？")).not.toBeInTheDocument();
  });

  it("director console v1 is always derivable for freezone chrome", () => {
    const state = deriveDirectorConsoleState({
      skillIds: [],
      pinnedNodes: [],
      selectedNode: null,
      planSteps: [],
      attachmentCount: 0,
      busy: false,
      hasUserMessages: false,
    });
    expect(state.counts.skillCatalog).toBe(9);
    expect(state.counts.skills).toBe(0);
    expect(state.nextStep.label.length).toBeGreaterThan(0);
  });

  it("exports the neutral Agent mark contract", async () => {
    const mod = await import("./freezone-canvas-agent-ui");
    expect(Object.keys(mod)).toContain("AgentMark");
    expect(typeof mod.AgentMark).toBe("function");
  });

  it("matches LibTV default widths and clamps resize", () => {
    expect(defaultFreezoneAgentWidth(1600)).toBe(370);
    expect(defaultFreezoneAgentWidth(1400)).toBe(360);
    expect(clampFreezoneAgentWidth(200)).toBe(320);
    expect(clampFreezoneAgentWidth(900)).toBe(560);
    expect(clampFreezoneAgentWidth(380)).toBe(380);
  });

  it("maps each director skill to a dedicated icon", () => {
    for (const skill of CANVAS_AGENT_SKILLS) {
      expect(skillIconFor(skill.id)).toBeTruthy();
      expect(skillIconFor(skill.id).displayName || skillIconFor(skill.id).name).toBeTruthy();
    }
    expect(skillIconFor("one-click-film")).not.toBe(skillIconFor("storyboard"));
  });

  it("keeps the collapsed Agent state readable and opens the detailed director console on demand", () => {
    const onExpand = vi.fn();
    render(createElement(FreezoneAgentContextCard, {
      state: {
        mountedSkills: [CANVAS_AGENT_SKILLS[0]],
        pinnedNodes: [],
        selectedNode: { id: "shot-1", type: "imageGenNode", label: "主角登场镜头" },
        gates: [],
        nextStep: {
          id: "continue-structure",
          label: "继续结构编排或点名节点 ID",
          detail: "优先连线/分镜/prompt 骨架；媒体按本轮任务授权执行",
          priority: "primary",
        },
        counts: { skills: 1, pins: 0, planSteps: 2, attachments: 0, skillCatalog: 8 },
        readiness: "ready",
      },
      onExpand,
    }));

    expect(screen.getByText("小树工作台")).toBeInTheDocument();
    expect(screen.getByText("可下令")).toBeInTheDocument();
    expect(screen.getByText("当前参考 · 1 个已指定能力")).toBeInTheDocument();
    expect(screen.queryByText("主角登场镜头")).not.toBeInTheDocument();
    expect(screen.getByText("继续结构编排或点名节点 ID")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "打开小树工作台：可下令" }));
    expect(onExpand).toHaveBeenCalledOnce();
  });
});

describe("freezone composer node previews", () => {
  const imageNode = {
    id: "shot-1",
    type: "imageGenNode",
    label: "果味升空·镜头1蓄势",
    hasImage: true,
    previewUrl: "/static/projects/project-1/images/shot-1.png",
    previewKind: "image" as const,
  };

  it("does not render a thumbnail for a merely selected node", () => {
    render(createElement(FreezoneComposerTags, {
      skillIds: [],
      pinnedNodes: [],
      onRemoveSkill: vi.fn(),
    }));

    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    expect(screen.queryByText(/果味升空/)).not.toBeInTheDocument();
  });

  it("renders a pinned image as an inline reference token and keeps its unpin action", () => {
    const onUnpinNode = vi.fn();
    render(createElement(FreezoneComposerTags, {
      skillIds: [],
      pinnedNodes: [imageNode],
      onRemoveSkill: vi.fn(),
      onUnpinNode,
    }));

    expect(screen.getByRole("img", { name: "果味升空·镜头1蓄势 节点预览" })).toBeInTheDocument();
    expect(screen.getByText("图片")).toBeInTheDocument();
    expect(screen.queryByText("@果味升空·镜头1蓄势")).not.toBeInTheDocument();
    expect(screen.queryByText("已引用节点")).not.toBeInTheDocument();
    fireEvent.click(screen.getByTitle("取消固定 Agent 引用"));
    expect(onUnpinNode).toHaveBeenCalledWith("shot-1");
  });

  it("renders a video first frame with a play marker", () => {
    const view = render(createElement(FreezoneComposerTags, {
      skillIds: [],
      pinnedNodes: [{
        id: "video-1",
        type: "videoNode",
        label: "果味升空·镜头2起飞",
        hasVideo: true,
        previewUrl: "/static/projects/project-1/videos/shot-2.mp4",
        previewKind: "video",
      }],
      onRemoveSkill: vi.fn(),
    }));

    expect(view.container.querySelector("video")).toHaveAttribute(
      "src",
      "/static/projects/project-1/videos/shot-2.mp4",
    );
    expect(screen.getByRole("img", { name: "果味升空·镜头2起飞 节点预览" })).toBeInTheDocument();
  });

  it("renders text and audio as quiet visual previews", () => {
    render(createElement(FreezoneComposerTags, {
      skillIds: [],
      pinnedNodes: [
        { id: "text-1", type: "textAnnotationNode", label: "提示词", previewKind: "text" },
        { id: "audio-1", type: "audioNode", label: "旁白", previewKind: "audio", previewUrl: "/voice.wav" },
      ],
      onRemoveSkill: vi.fn(),
    }));

    expect(screen.getByRole("img", { name: "提示词 节点预览" })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "旁白 节点预览" })).toBeInTheDocument();
    expect(screen.getByText("文本")).toBeInTheDocument();
    expect(screen.getByText("音频")).toBeInTheDocument();
    expect(screen.queryByText("提示词")).not.toBeInTheDocument();
    expect(screen.queryByText("旁白")).not.toBeInTheDocument();
  });

  it("hides empty nodes and does not fall back after preview failure", () => {
    const view = render(createElement(FreezoneComposerTags, {
      skillIds: [],
      pinnedNodes: [
        { id: "empty-1", type: "textAnnotationNode", label: "空文本" },
        imageNode,
      ],
      onRemoveSkill: vi.fn(),
    }));

    expect(screen.queryByText("空文本")).not.toBeInTheDocument();
    const image = view.container.querySelector("img");
    expect(image).not.toBeNull();
    fireEvent.error(image!);
    expect(screen.queryByRole("img", { name: /果味升空/ })).not.toBeInTheDocument();
    expect(screen.queryByText("图片")).not.toBeInTheDocument();
    expect(screen.queryByText(/加入|已引用|果味升空/)).not.toBeInTheDocument();
  });
});

describe("freezone composer skill drawer", () => {
  it("shows installed store skills and opens the full store", () => {
    const onOpenStore = vi.fn();
    const storeSkill = {
      id: "store:custom.lighting-director",
      skillKey: "lighting-director",
      label: "灯光导演",
      description: "规划场景灯光",
      activation: "读取场景并回写灯光提示词",
      accent: "from-cyan-500/20 to-sky-500/5 border-cyan-300/20",
    };
    render(createElement(FreezoneComposerSkillDrawer, {
      skillIds: [storeSkill.id],
      skills: [...CANVAS_AGENT_SKILLS, storeSkill],
      onToggleSkill: vi.fn(),
      onOpenStore,
    }));

    expect(screen.getAllByText("灯光导演").length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole("button", { name: "技能商店" }));
    expect(onOpenStore).toHaveBeenCalledOnce();
  });

  it("collects manually selected skills into a drawer and toggles existing skills", () => {
    const onToggleSkill = vi.fn();
    render(createElement(FreezoneComposerSkillDrawer, {
      skillIds: ["canvas-director", "storyboard"],
      onToggleSkill,
    }));

    expect(screen.getByText("2 已指定")).toBeInTheDocument();
    expect(screen.getAllByText("画布导演").length).toBeGreaterThan(0);
    expect(screen.getAllByText("分镜大师").length).toBeGreaterThan(0);
    fireEvent.click(screen.getByTitle("取消指定 画布导演"));
    expect(onToggleSkill).toHaveBeenCalledWith("canvas-director");
  });

  it("keeps the empty drawer in automatic matching mode", () => {
    const onToggleSkill = vi.fn();
    render(createElement(FreezoneComposerSkillDrawer, {
      skillIds: [],
      onToggleSkill,
    }));

    expect(screen.getByText("自动匹配")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /提示词总监/ }));
    expect(onToggleSkill).toHaveBeenCalledWith("prompt-engineer");
  });

  it("searches and filters the mounted skill catalog without changing selection", () => {
    render(createElement(FreezoneComposerSkillDrawer, {
      skillIds: ["canvas-director"],
      onToggleSkill: vi.fn(),
    }));

    fireEvent.change(screen.getByRole("textbox", { name: "搜索技能" }), {
      target: { value: "一致性" },
    });
    expect(screen.getByText("角色一致性")).toBeInTheDocument();
    expect(screen.queryByText("提示词总监")).not.toBeInTheDocument();

    fireEvent.change(screen.getByRole("textbox", { name: "搜索技能" }), {
      target: { value: "" },
    });
    fireEvent.click(screen.getByRole("tab", { name: "角色" }));
    expect(screen.getByText("角色表演导演")).toBeInTheDocument();
    expect(screen.queryByTitle("读取节点、连线、引用和任务，规划最短执行路径")).not.toBeInTheDocument();
    expect(screen.getByText("1 已指定")).toBeInTheDocument();
  });
});

describe("freezone compact execution status", () => {
  beforeEach(() => window.localStorage.clear());
  it("does not keep a card after execution reaches a terminal state", () => {
    const { container } = render(createElement(FreezoneAgentCompactStatus, {
      busy: false,
      onStop: vi.fn(),
      onResumeRecovery: vi.fn(),
    }));

    expect(container).toBeEmptyDOMElement();
  });

  it("keeps a completed film visible when the release gate blocks publishing", () => {
    const workflowRun = {
      id: "run-release",
      status: "completed",
      artifacts: {},
      step_states: {},
      current_frontier: [],
      release_readiness: {
        schema: "release_readiness_contract.v1",
        status: "blocked",
        reason_code: "delivery_qc_failed",
        can_publish: false,
        required: true,
        failed_checks: ["freeze_frames", "audio_activity"],
        not_run_checks: ["loudness"],
        missing_checks: [],
      },
    } as unknown as WorkflowRun;
    const { container } = render(createElement(FreezoneAgentCompactStatus, {
      busy: false,
      workflowRun,
      onStop: vi.fn(),
      onResumeRecovery: vi.fn(),
    }));

    expect(screen.getByText("成片已生成，但发布门未通过")).toBeInTheDocument();
    expect(screen.getByText("发布被阻断")).toBeInTheDocument();
    expect(screen.getByText(/静帧、音频有效性、响度/)).toBeInTheDocument();
    const release = container.querySelector("[data-workflow-release-readiness='v1']");
    expect(release).toHaveAttribute("data-release-status", "blocked");
    expect(release).toHaveAttribute("data-can-publish", "false");
  });

  it("shows one real stop action only while execution is active", () => {
    const onStop = vi.fn();
    render(createElement(FreezoneAgentCompactStatus, {
      busy: true,
      label: "正在写入画布",
      progressLabel: "步骤 1/2",
      stage: "canvas.command",
      elapsedSeconds: 72,
      lastProgressAgeSeconds: 3,
      workerAlive: true,
      onStop,
      onResumeRecovery: vi.fn(),
    }));

    expect(screen.getByText("正在写入画布")).toBeInTheDocument();
    expect(screen.getByText("步骤 1/2")).toBeInTheDocument();
    expect(screen.getByText("写入画布 · 已运行 1 分 12 秒")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "停止" }));
    expect(onStop).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("button", { name: "详情" })).not.toBeInTheDocument();
  });

  it("shows delayed progress honestly without declaring the run dead", () => {
    const { container } = render(createElement(FreezoneAgentCompactStatus, {
      busy: true,
      label: "等待上游模型",
      stage: "assistant.stream",
      lastProgressAgeSeconds: 26,
      workerAlive: true,
      onStop: vi.fn(),
      onResumeRecovery: vi.fn(),
    }));

    expect(screen.getByText("等待模型回传 · 等待回传 26 秒")).toBeInTheDocument();
    expect(container.querySelector('[data-agent-health="waiting"]')).toBeInTheDocument();
    expect(humanizeAgentExecutionStage("workflow.verify")).toBe("核验结果");
  });

  it("keeps the real recovery action without restoring the large card", () => {
    const onResumeRecovery = vi.fn();
    render(createElement(FreezoneAgentCompactStatus, {
      busy: false,
      recovery: {
        message: "连接中断，已保存现场",
        autoRetrying: false,
        packet: {
          schema: "village_canvas.chat_recovery.v1",
          recovery_id: "recovery-a",
          retryable: true,
        },
      },
      onStop: vi.fn(),
      onResumeRecovery,
    }));

    expect(screen.getByText("已保存恢复点")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "继续" }));
    expect(onResumeRecovery).toHaveBeenCalledTimes(1);
  });

  it("renders real business steps and expandable technical details", () => {
    const timeline: AgentExecutionTimeline = {
      title: "正在继续处理你的请求",
      stageLabel: "正在写入画布",
      status: "running",
      elapsedMs: 2_000,
      completedCount: 1,
      totalCount: 2,
      steps: [
        {
          id: "observe",
          label: "读取真实项目 / 画布状态",
          status: "completed",
          durationMs: 31,
          details: [],
        },
        {
          id: "act",
          label: "写入结构、资产或生产步骤",
          status: "running",
          details: [{
            id: "tool:get-sketches",
            label: "读取分镜",
            technicalName: "village_canvas_get_sketches",
            status: "completed",
            count: 2,
            durationMs: 385,
          }],
        },
      ],
    };
    render(createElement(FreezoneAgentCompactStatus, {
      busy: true,
      timeline,
      onStop: vi.fn(),
      onResumeRecovery: vi.fn(),
    }));

    expect(screen.getByText("正在继续处理你的请求")).toBeInTheDocument();
    expect(screen.getByText("步骤 1/2")).toBeInTheDocument();
    expect(screen.getByText("读取真实项目 / 画布状态")).toBeInTheDocument();
    expect(screen.getByText("读取分镜")).toBeInTheDocument();
    expect(screen.getByText("31ms")).toBeInTheDocument();
    expect(screen.getByText("385ms")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "收起执行详情" }));
    expect(screen.queryByText("读取分镜")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "展开执行详情" }));
    expect(screen.getByText("读取分镜")).toBeInTheDocument();
  });

  it("lets the operator retry or delete selected failed workflow items", () => {
    const onRetryWorkflowItems = vi.fn();
    const onDismissWorkflowItems = vi.fn();
    const workflowRun = {
      id: "run-item-retry",
      workflow_id: "one-click-film",
      workflow_version: 2,
      project_id: "project-1",
      canvas_id: "canvas-1",
      run_mode: "auto",
      status: "failed",
      current_frontier: ["media_generation"],
      step_states: {
        media_generation: {
          id: "media_generation",
          label: "执行媒体生成",
          type: "media_batch",
          handler: "canvas.run_generation_nodes",
          depends_on: [],
          execution_mode: "itemized",
          retry_scope: "failed_items_only",
          status: "failed",
          attempt: 1,
          error: "有媒体项失败",
          started_at: "2026-08-21T00:00:00Z",
          completed_at: "2026-08-21T00:01:00Z",
        },
      },
      inputs: {},
      artifacts: {
        media_generation: {
          item_states: {
            "shot-1": { status: "failed", label: "镜头 1", error: "上游超时", attempt: 1 },
            "shot-2": { status: "completed", label: "镜头 2", attempt: 1 },
          },
        },
      },
      error: "有媒体项失败",
      revision: 7,
      event_seq: 4,
      idempotency_key: "start-run-item-retry",
      created_at: "2026-08-21T00:00:00Z",
      updated_at: "2026-08-21T00:01:00Z",
    } as unknown as WorkflowRun;

    render(createElement(FreezoneAgentCompactStatus, {
      busy: false,
      workflowRun,
      onRetryWorkflowItems,
      onDismissWorkflowItems,
      onStop: vi.fn(),
      onResumeRecovery: vi.fn(),
    }));

    expect(screen.getByText("失败项可单独重试")).toBeInTheDocument();
    expect(screen.getByText("镜头 1")).toBeInTheDocument();
    expect(screen.queryByText("镜头 2")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: "重试选中项（1）" }));
    expect(onRetryWorkflowItems).toHaveBeenCalledWith([
      { stepId: "media_generation", itemIds: ["shot-1"] },
    ]);
    fireEvent.click(screen.getByRole("button", { name: "删除选中项（1）" }));
    expect(screen.getByText("删除选中的失败记录？")).toBeInTheDocument();
    expect(screen.getByText(/画布节点、已成功素材和任务审计都会保留/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "确认删除" }));
    expect(onDismissWorkflowItems).toHaveBeenCalledWith([
      { stepId: "media_generation", itemIds: ["shot-1"] },
    ]);
  });

  it("lets the operator retry a failed atomic step as a whole", () => {
    const onRetryWorkflowStep = vi.fn();
    const workflowRun = {
      id: "run-step-retry",
      workflow_id: "one-click-film",
      workflow_version: 2,
      project_id: "project-1",
      canvas_id: "canvas-1",
      run_mode: "auto",
      status: "failed",
      current_frontier: ["quality_review"],
      step_states: {
        quality_review: {
          id: "quality_review",
          label: "质量与连续性验收",
          type: "quality_gate",
          handler: "canvas.delivery_qc",
          depends_on: ["media_generation"],
          execution_mode: "atomic",
          retry_scope: "whole_step",
          retry_policy: "manual",
          status: "failed",
          attempt: 1,
          error: "视觉连续性验收未通过",
          started_at: "2026-09-13T00:00:00Z",
          completed_at: "2026-09-13T00:01:00Z",
        },
      },
      inputs: {},
      artifacts: {},
      error: "视觉连续性验收未通过",
      revision: 8,
      event_seq: 5,
      idempotency_key: "start-run-step-retry",
      created_at: "2026-09-13T00:00:00Z",
      updated_at: "2026-09-13T00:01:00Z",
    } as WorkflowRun;

    expect(workflowRunHasRetryableFailures(workflowRun)).toBe(true);
    render(createElement(FreezoneAgentCompactStatus, {
      busy: false,
      workflowRun,
      onRetryWorkflowStep,
      onStop: vi.fn(),
      onResumeRecovery: vi.fn(),
    }));

    expect(screen.getByText("整步失败可重试")).toBeInTheDocument();
    expect(screen.getByText("视觉连续性验收未通过")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重试整个步骤" }));
    expect(onRetryWorkflowStep).toHaveBeenCalledWith("quality_review");

    const exhausted = {
      ...workflowRun,
      step_states: {
        quality_review: {
          ...workflowRun.step_states.quality_review,
          attempt: 3,
          max_attempts: 3,
        },
      },
    } as WorkflowRun;
    expect(workflowRunHasRetryableFailures(exhausted)).toBe(false);
  });

  it("shows the server recovery contract without offering a blind media retry", () => {
    const workflowRun = {
      id: "run-canvas-recovery",
      workflow_id: "one-click-film",
      workflow_version: 2,
      project_id: "project-1",
      canvas_id: "canvas-1",
      run_mode: "auto",
      status: "failed",
      next_action: "recover:regenerate_storyboard:media_generation",
      current_frontier: ["media_generation"],
      step_states: {
        media_generation: {
          id: "media_generation",
          label: "执行媒体生成",
          type: "media_batch",
          handler: "canvas.run_generation_nodes",
          depends_on: ["asset_slots"],
          execution_mode: "itemized",
          retry_scope: "failed_items_only",
          retry_policy: "manual",
          max_attempts: 3,
          status: "failed",
          attempt: 1,
          error: "分镜图生成时带的参考图与当前脚本 / 资产状态不一致。",
          started_at: "2026-09-18T00:00:00Z",
          completed_at: "2026-09-18T00:01:00Z",
        },
      },
      inputs: {},
      artifacts: {
        media_generation: {
          status: "receipt_failed",
          canvas_receipt: {
            error_code: "canvas_script_media_not_ready",
            recovery: {
              schema: "canvas_command_recovery.v1",
              action: "regenerate_storyboard",
              title: "先重出分镜图",
              instruction: "当前脚本、资产或参考图与分镜图快照不一致。先重出分镜图，再重新排队视频。",
              next_action: "recover:regenerate_storyboard:media_generation",
              auto_retry_allowed: false,
              requires_paid_media: true,
              error_code: "canvas_script_media_not_ready",
              step_id: "media_generation",
              stale_reason: "reference-changed",
              shot_id: "shot-1",
            },
          },
        },
      },
      error: "分镜图生成时带的参考图与当前脚本 / 资产状态不一致。",
      revision: 9,
      event_seq: 6,
      idempotency_key: "start-run-canvas-recovery",
      created_at: "2026-09-18T00:00:00Z",
      updated_at: "2026-09-18T00:01:00Z",
    } as WorkflowRun;

    expect(workflowCanvasRecoveryFromRun(workflowRun)?.action).toBe("regenerate_storyboard");
    expect(workflowStepHasBlockingCanvasRecovery(workflowRun, "media_generation")).toBe(true);
    expect(workflowRunHasRetryableFailures(workflowRun)).toBe(false);
    expect(workflowRunHasActionableFailures(workflowRun)).toBe(true);

    const { container } = render(createElement(FreezoneAgentCompactStatus, {
      busy: false,
      workflowRun,
      onRetryWorkflowItems: vi.fn(),
      onRetryWorkflowStep: vi.fn(),
      onStop: vi.fn(),
      onResumeRecovery: vi.fn(),
    }));

    expect(screen.getByText("需要先修复前置条件")).toBeInTheDocument();
    expect(screen.getByText("付费命令未执行 · 修复前置条件后再重新出图")).toBeInTheDocument();
    expect(screen.getByText("先重出分镜图")).toBeInTheDocument();
    expect(screen.getByText(/当前脚本、资产或参考图与分镜图快照不一致/)).toBeInTheDocument();
    expect(screen.getByText("不会自动重放原付费命令")).toBeInTheDocument();
    expect(screen.getByText("修复前置条件后需重新出图")).toBeInTheDocument();
    expect(container.querySelector('[data-workflow-canvas-recovery="v1"]')).not.toBeNull();
    expect(screen.queryByText("整步失败可重试")).not.toBeInTheDocument();
    expect(screen.queryByText("失败项可单独重试")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /重试/ })).not.toBeInTheDocument();
  });

  it("offers one explicit final-compose authorization action", () => {
    const signature = "d".repeat(64);
    const workflowRun = {
      id: "run-compose-auth",
      workflow_id: "freezone-final-film",
      workflow_version: 2,
      project_id: "project-1",
      canvas_id: "canvas-1",
      run_mode: "auto",
      status: "failed",
      current_frontier: ["final_film"],
      step_states: {
        final_film: {
          id: "final_film",
          label: "最终合成",
          type: "media",
          handler: "final_film",
          depends_on: ["shot_videos"],
          status: "failed",
          attempt: 1,
          error: "等待最终合成授权",
          started_at: "2026-09-18T00:00:00Z",
          completed_at: "2026-09-18T00:01:00Z",
        },
      },
      inputs: {},
      artifacts: {
        shot_videos: { result_signature: signature },
        final_film: {
          recovery: {
            schema: "workflow_step_recovery.v1",
            workflow_run_id: "run-compose-auth",
            action: "request_compose_authorization",
            title: "等待最终合成授权",
            instruction: "确认后只恢复原 Run 的最终合成。",
            next_action: "recover:request_compose_authorization:final_film",
            auto_retry_allowed: false,
            requires_paid_media: false,
            error_code: "workflow_final_film_not_authorized",
            step_id: "final_film",
            authorization_request: {
              schema: "workflow_compose_authorization_request.v1",
              run_id: "run-compose-auth",
              step_id: "final_film",
              source_result_signature: signature,
              requires_user_action: true,
            },
          },
        },
      },
      error: "等待最终合成授权",
      revision: 7,
      event_seq: 10,
      idempotency_key: "run-compose-auth",
      created_at: "2026-09-18T00:00:00Z",
      updated_at: "2026-09-18T00:01:00Z",
    } as WorkflowRun;
    const onAuthorizeCompose = vi.fn();

    render(createElement(FreezoneAgentCompactStatus, {
      busy: false,
      workflowRun,
      onAuthorizeCompose,
      onStop: vi.fn(),
      onResumeRecovery: vi.fn(),
    }));

    const button = screen.getByRole("button", { name: "授权并继续合成" });
    fireEvent.click(button);
    expect(onAuthorizeCompose).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("button", { name: /重试/ })).not.toBeInTheDocument();
  });

  it("offers the matching image or video media authorization action", () => {
    const workflowRun = {
      id: "run-media-auth",
      project_id: "project-1",
      canvas_id: "canvas-1",
      status: "failed",
      revision: 12,
      step_states: {
        storyboard_images: {
          id: "storyboard_images",
          label: "分镜图",
          status: "failed",
          attempt: 1,
          execution_mode: "itemized",
        },
      },
      artifacts: {
        storyboard_images: {
          recovery: {
            schema: "workflow_step_recovery.v1",
            workflow_run_id: "run-media-auth",
            action: "request_media_authorization",
            title: "等待付费媒体授权",
            instruction: "确认后只恢复原 Run 的分镜图步骤。",
            next_action: "recover:request_media_authorization:storyboard_images",
            rerun_scope: "current_step",
            item_ids: [],
            job_ids: [],
            auto_retry_allowed: false,
            requires_paid_media: true,
            error_code: "workflow_storyboard_paid_media_not_authorized",
            step_id: "storyboard_images",
          },
        },
      },
    } as unknown as WorkflowRun;
    const onAuthorizeMedia = vi.fn();

    render(createElement(FreezoneAgentCompactStatus, {
      busy: false,
      workflowRun,
      onAuthorizeMedia,
      onStop: vi.fn(),
      onResumeRecovery: vi.fn(),
    }));

    fireEvent.click(screen.getByRole("button", { name: "授权并恢复出图" }));
    expect(onAuthorizeMedia).toHaveBeenCalledWith("storyboard_images");
    expect(screen.queryByRole("button", { name: /重试/ })).not.toBeInTheDocument();
  });

  it("binds a failed-video retry authorization to the exact failed items", () => {
    const workflowRun = {
      id: "run-media-item-retry",
      project_id: "project-1",
      canvas_id: "canvas-1",
      status: "failed",
      revision: 13,
      step_states: {
        shot_videos: {
          id: "shot_videos",
          label: "逐镜视频",
          status: "failed",
          attempt: 1,
          execution_mode: "itemized",
        },
      },
      artifacts: {
        shot_videos: {
          recovery: {
            schema: "workflow_step_recovery.v1",
            workflow_run_id: "run-media-item-retry",
            action: "retry_failed_items",
            title: "等待失败视频恢复授权",
            instruction: "只重试失败视频 item。",
            next_action: "recover:retry_failed_items:shot_videos",
            rerun_scope: "failed_items_only",
            item_ids: ["shot-2"],
            job_ids: [],
            auto_retry_allowed: false,
            requires_paid_media: true,
            error_code: "workflow_shot_video_failed",
            step_id: "shot_videos",
          },
          item_states: {
            "shot-2": {
              status: "failed",
              label: "镜头 2",
              error: "provider failed",
              attempt: 1,
            },
          },
        },
      },
    } as unknown as WorkflowRun;
    const onAuthorizeMedia = vi.fn();

    const { rerender } = render(createElement(FreezoneAgentCompactStatus, {
      busy: true,
      workflowRun,
      onAuthorizeMedia,
      onStop: vi.fn(),
      onResumeRecovery: vi.fn(),
    }));

    expect(
      screen.getByRole("button", { name: "授权并重试失败视频（1）" }),
    ).toBeDisabled();

    rerender(createElement(FreezoneAgentCompactStatus, {
      busy: false,
      workflowRun,
      onAuthorizeMedia,
      onStop: vi.fn(),
      onResumeRecovery: vi.fn(),
    }));
    fireEvent.click(
      screen.getByRole("button", { name: "授权并重试失败视频（1）" }),
    );
    expect(onAuthorizeMedia).toHaveBeenCalledWith(
      "shot_videos",
      ["shot-2"],
    );
    expect(
      screen.queryByRole("button", { name: /重试选中项/ }),
    ).not.toBeInTheDocument();
  });

  it("locates canvas asset recovery targets without authorizing media", () => {
    const workflowRun = {
      id: "run-asset-recovery",
      project_id: "project-1",
      canvas_id: "canvas-1",
      status: "failed",
      revision: 18,
      step_states: {
        storyboard_images: {
          id: "storyboard_images",
          label: "分镜图",
          status: "failed",
          attempt: 1,
        },
      },
      artifacts: {
        storyboard_images: {
          recovery: {
            schema: "workflow_step_recovery.v1",
            workflow_run_id: "run-asset-recovery",
            action: "repair_canvas_asset_binding",
            title: "先修正资产节点",
            instruction: "保留唯一、就绪、身份一致的资产节点后再重试。",
            next_action: "recover:repair_canvas_asset_binding:storyboard_images",
            rerun_scope: "canvas_asset_binding",
            item_ids: [],
            job_ids: [],
            target_node_ids: ["asset-a", "asset-b"],
            asset_ids: ["scene:darkroom"],
            auto_retry_allowed: false,
            requires_paid_media: false,
            error_code: "workflow_storyboard_canvas_asset_ambiguous",
            step_id: "storyboard_images",
          },
        },
      },
    } as unknown as WorkflowRun;
    const onAuthorizeMedia = vi.fn();
    useCanvasStore.setState({
      nodes: [
        {
          id: "asset-b",
          type: "imageGenNode",
          position: { x: 0, y: 0 },
          data: {},
        } as CanvasNode,
      ],
      edges: [],
      selectedNodeId: null,
      pendingFocusNodeId: null,
      pendingFocusNodeIds: null,
    });

    render(createElement(FreezoneAgentCompactStatus, {
      busy: false,
      workflowRun,
      onAuthorizeMedia,
      onStop: vi.fn(),
      onResumeRecovery: vi.fn(),
    }));

    fireEvent.click(screen.getByRole("button", { name: "定位问题节点" }));
    expect(useCanvasStore.getState().selectedNodeId).toBe("asset-b");
    expect(useCanvasStore.getState().nodes.find((node) => node.id === "asset-b")?.selected).toBe(true);
    expect(useCanvasStore.getState().pendingFocusNodeId).toBeNull();
    expect(useCanvasStore.getState().pendingFocusNodeIds).toEqual(["asset-b"]);
    expect(onAuthorizeMedia).not.toHaveBeenCalled();
    expect(
      screen.queryByRole("button", { name: /授权并恢复/ }),
    ).not.toBeInTheDocument();
    useCanvasStore.setState({
      nodes: [],
      edges: [],
      selectedNodeId: null,
      pendingFocusNodeId: null,
      pendingFocusNodeIds: null,
    });
  });

  it("delegates duplicate asset repair to the server-owned callback", () => {
    const workflowRun = {
      id: "run-asset-repair",
      project_id: "project-1",
      canvas_id: "canvas-1",
      status: "failed",
      revision: 19,
      step_states: {
        storyboard_images: {
          id: "storyboard_images",
          label: "分镜图",
          status: "failed",
          attempt: 1,
        },
      },
      artifacts: {
        storyboard_images: {
          recovery: {
            schema: "workflow_step_recovery.v1",
            workflow_run_id: "run-asset-repair",
            action: "repair_canvas_asset_binding",
            title: "先修正资产节点",
            instruction: "保留唯一、就绪、身份一致的资产节点后再重试。",
            next_action: "recover:repair_canvas_asset_binding:storyboard_images",
            rerun_scope: "canvas_asset_binding",
            item_ids: [],
            job_ids: [],
            target_node_ids: ["asset-empty", "asset-ready", "asset-failed"],
            asset_ids: ["scene:darkroom"],
            auto_retry_allowed: false,
            requires_paid_media: false,
            error_code: "workflow_storyboard_canvas_asset_ambiguous",
            step_id: "storyboard_images",
          },
        },
      },
    } as unknown as WorkflowRun;
    useCanvasStore.setState({
      nodes: [
        {
          id: "asset-empty",
          type: "imageGenNode",
          position: { x: 0, y: 0 },
          data: {
            scriptAssetId: "scene:darkroom",
            scriptAssetOwnerId: "script-a",
          },
        } as CanvasNode,
        {
          id: "asset-ready",
          type: "imageGenNode",
          position: { x: 100, y: 0 },
          data: {
            scriptAssetId: "scene:darkroom",
            scriptAssetOwnerId: "script-a",
            imageUrl: "/projects/p/darkroom.png",
            label: "暗房",
          },
        } as CanvasNode,
        {
          id: "asset-failed",
          type: "imageGenNode",
          position: { x: 200, y: 0 },
          data: {
            scriptAssetId: "scene:darkroom",
            scriptAssetOwnerId: "script-a",
            generationError: "上一次生成失败",
          },
        } as CanvasNode,
      ],
      edges: [],
      selectedNodeId: null,
      pendingFocusNodeId: null,
      pendingFocusNodeIds: null,
    });

    const onRepairWorkflowAssetBindings = vi.fn();
    render(createElement(FreezoneAgentCompactStatus, {
      busy: false,
      workflowRun,
      onRepairWorkflowAssetBindings,
      onStop: vi.fn(),
      onResumeRecovery: vi.fn(),
    }));

    fireEvent.click(screen.getByRole("button", { name: "整理重复绑定（1 组）" }));

    expect(onRepairWorkflowAssetBindings).toHaveBeenCalledTimes(1);
    const nodes = useCanvasStore.getState().nodes;
    expect(nodes).toHaveLength(3);
    expect(nodes.map((node) => node.data.scriptAssetId)).toEqual([
      "scene:darkroom",
      "scene:darkroom",
      "scene:darkroom",
    ]);
    expect(useCanvasStore.getState().selectedNodeId).toBeNull();
    expect(useCanvasStore.getState().pendingFocusNodeIds).toBeNull();

    useCanvasStore.setState({
      nodes: [],
      edges: [],
      selectedNodeId: null,
      pendingFocusNodeId: null,
      pendingFocusNodeIds: null,
    });
  });

  it("lets the operator dismiss a terminal failure card without losing retry data", () => {
    const workflowRun = {
      id: "run-dismiss-failure",
      project_id: "project-1",
      canvas_id: "canvas-1",
      status: "failed",
      revision: 3,
      step_states: {
        media_generation: {
          id: "media_generation",
          label: "执行媒体生成",
          execution_mode: "itemized",
          retry_scope: "failed_items_only",
        },
      },
      artifacts: {
        media_generation: {
          item_states: {
            "shot-1": { status: "failed", label: "镜头 1", error: "HTTP 401", attempt: 2 },
          },
        },
      },
    } as unknown as WorkflowRun;

    const onDismiss = vi.fn();
    const { rerender } = render(createElement(FreezoneAgentCompactStatus, {
      busy: false,
      workflowRun,
      onDismiss,
      onRetryWorkflowItems: vi.fn(),
      onStop: vi.fn(),
      onResumeRecovery: vi.fn(),
    }));

    expect(screen.getByText("执行媒体生成")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "关闭失败详情" }));
    expect(screen.queryByText("失败项可单独重试")).not.toBeInTheDocument();
    expect(onDismiss).toHaveBeenCalledWith(expect.stringContaining("project-1:canvas-1:run-dismiss-failure:failed:"));

    rerender(createElement(FreezoneAgentCompactStatus, {
      busy: true,
      workflowRun,
      onDismiss,
      onRetryWorkflowItems: vi.fn(),
      onStop: vi.fn(),
      onResumeRecovery: vi.fn(),
    }));
    expect(screen.queryByText("失败项可单独重试")).not.toBeInTheDocument();

    rerender(createElement(FreezoneAgentCompactStatus, {
      busy: false,
      workflowRun: { ...workflowRun, revision: 4 },
      onDismiss,
      onRetryWorkflowItems: vi.fn(),
      onStop: vi.fn(),
      onResumeRecovery: vi.fn(),
    }));
    expect(screen.queryByText("失败项可单独重试")).not.toBeInTheDocument();

    rerender(createElement(FreezoneAgentCompactStatus, {
      busy: false,
      workflowRun: {
        ...workflowRun,
        revision: 5,
        error: "HTTP 502",
      },
      onDismiss,
      onRetryWorkflowItems: vi.fn(),
      onStop: vi.fn(),
      onResumeRecovery: vi.fn(),
    }));
    expect(screen.getByText("失败项可单独重试")).toBeInTheDocument();
  });

  it("persists a dismissed failure fingerprint across component recreation", () => {
    const workflowRun = {
      id: "run-persisted-dismissal",
      project_id: "project-1",
      canvas_id: "canvas-1",
      status: "failed",
      revision: 8,
    } as WorkflowRun;
    const key = workflowFailureDisplayKey(workflowRun);
    expect(key).not.toBeNull();

    saveDismissedWorkflowFailureKey(key!);

    expect(loadDismissedWorkflowFailureKeys().has(key!)).toBe(true);
  });

  it("does not render dismissed item records or an empty terminal card", () => {
    const workflowRun = {
      id: "run-dismissed-items",
      project_id: "project-1",
      canvas_id: "canvas-1",
      status: "failed",
      revision: 9,
      step_states: {
        media_generation: {
          id: "media_generation",
          label: "执行媒体生成",
          execution_mode: "itemized",
          retry_scope: "failed_items_only",
        },
      },
      artifacts: {
        media_generation: {
          item_states: {
            "shot-1": { status: "dismissed", label: "镜头 1", error: "HTTP 401", attempt: 2 },
            "shot-2": { status: "completed", label: "镜头 2", attempt: 1 },
          },
        },
      },
    } as unknown as WorkflowRun;

    const { container } = render(createElement(FreezoneAgentCompactStatus, {
      busy: false,
      workflowRun,
      onStop: vi.fn(),
      onResumeRecovery: vi.fn(),
    }));

    expect(container).toBeEmptyDOMElement();
    expect(screen.queryByText("镜头 1")).not.toBeInTheDocument();
  });
});

describe("freezone workflow plan derivation", () => {
  it("does not claim canvas observation before the first real event", () => {
    const steps = deriveAgentPlanFromMessages([], true);

    expect(steps).toEqual([{
      id: "starting",
      label: "正在启动执行器",
      status: "running",
    }]);
    expect(steps.some((step) => step.label.includes("读取项目"))).toBe(false);
  });

  it("prefers the receipt-backed active workflow over hidden tool chatter", () => {
    const steps = deriveAgentPlanFromMessages([], true, {
      schema: "village_canvas.workflow.v1",
      status: "verifying",
      steps: [
        { id: "observe", label: "读取真实项目 / 画布状态", status: "done" },
        { id: "plan", label: "确定本轮导演计划", status: "done" },
        { id: "act", label: "写入结构、资产或生产步骤", status: "done" },
        { id: "verify", label: "核对工具回执与下一步", status: "running" },
      ],
    });

    expect(steps).toEqual(expect.arrayContaining([
      expect.objectContaining({ id: "observe", status: "done" }),
      expect.objectContaining({ id: "verify", status: "running" }),
    ]));
  });

  it("does not revive historical workflow chrome after a page refresh", () => {
    const steps = deriveAgentPlanFromMessages([
      {
        id: "assistant-1",
        role: "assistant",
        text: "已完成结构整理。",
        timestamp: 1,
        uiEvents: [{
          type: "agent.workflow",
          workflow: {
            schema: "village_canvas.workflow.v1",
            status: "awaiting_confirmation",
            awaiting_confirmation: true,
            steps: [
              { id: "observe", label: "读取真实项目 / 画布状态", status: "done" },
              { id: "plan", label: "确定本轮导演计划", status: "done" },
              { id: "act", label: "写入结构、资产或生产步骤", status: "done" },
              { id: "verify", label: "核对工具回执与下一步", status: "done" },
            ],
          },
        }],
      },
    ], false);

    expect(steps).toEqual([]);
  });

  it("shows an accepted workflow run as observing until a terminal receipt arrives", () => {
    render(createElement(FreezoneAgentRunBar, {
      running: true,
      connected: true,
      workflow: {
        schema: "village_canvas.workflow.v1",
        status: "observing",
        steps: [],
        workflow_run_continuation: {
          run_id: "wfr-observing",
          run_status: "running",
          receipt_pending: true,
        },
      },
    }));

    expect(screen.getByText("观察中")).toBeInTheDocument();
  });
});