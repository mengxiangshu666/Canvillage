import { describe, expect, it, vi } from "vitest";

import { buildFastCanvasAgentKernelPlan } from "./canvas-agent-kernel";
import {
  buildWorkflowCanvasEnvelope,
  canvasWorkflowMediaBatchCheckpoint,
  canvasWorkflowFailureCheckpoint,
  applyPendingWorkflowCanvasCommand,
  pendingWorkflowCanvasCommand,
  startKnownCanvasWorkflow,
  workflowRuntimeIdForPlan,
} from "./canvas-workflow-fast-start";
import { CANVAS_COMMAND_RECEIPT_EVENT } from "./canvas-command-receipts";
import type { WorkflowRun } from "@/types/workflow-runtime";

function run(overrides: Partial<WorkflowRun> = {}): WorkflowRun {
  return {
    id: "wfr-1",
    workflow_id: "one-click-film",
    workflow_version: 1,
    project_id: "project-1",
    canvas_id: "canvas-1",
    run_mode: "draft",
    status: "running",
    current_frontier: ["canvas_structure"],
    step_states: {},
    inputs: { request: "做一个水果短片" },
    artifacts: { starter_workflow_id: "story-continuity-film" },
    error: "",
    revision: 0,
    event_seq: 0,
    idempotency_key: "start-1",
    created_at: "2026-08-14T00:00:00Z",
    updated_at: "2026-08-14T00:00:00Z",
    ...overrides,
  };
}

describe("canvas workflow fast start", () => {
  it("turns an Agent terminal failure into the current durable step checkpoint", () => {
    const workflow = run({
      current_frontier: ["story_and_shots"],
      step_states: {
        story_and_shots: {
          id: "story_and_shots",
          label: "生成故事与分镜",
          type: "agent_plan",
          handler: "agent.storyboard",
          depends_on: ["canvas_structure"],
          status: "running",
          attempt: 1,
          error: "",
          started_at: "2026-08-14T00:00:00Z",
          completed_at: "",
        },
      },
    });
    const checkpoint = canvasWorkflowFailureCheckpoint(workflow, [
      {
        id: "user-1",
        role: "user",
        text: String(workflow.inputs.request),
        turnId: "turn-1",
        timestamp: 1,
      },
      {
        id: "assistant-1",
        role: "assistant",
        text: "本轮没有完成：上游没有返回有效内容。",
        turnId: "turn-1",
        timestamp: 2,
      },
    ], false);

    expect(checkpoint).toEqual({
      eventId: "agent-failure:assistant-1:story_and_shots",
      stepId: "story_and_shots",
      turnId: "turn-1",
      error: "本轮没有完成：上游没有返回有效内容。",
    });
  });

  it("maps only known long-business intents to the durable runtime", () => {
    const known = buildFastCanvasAgentKernelPlan({
      userText: "帮我做一个水果短片",
      selectedSkillIds: [],
      runMode: "draft",
      pinnedNodes: [],
    });
    const unknown = buildFastCanvasAgentKernelPlan({
      userText: "优化这个节点的提示词，不要生成",
      selectedSkillIds: [],
      runMode: "draft",
      pinnedNodes: [],
    });

    expect(workflowRuntimeIdForPlan(known)).toBe("one-click-film");
    expect(workflowRuntimeIdForPlan(unknown)).toBeNull();
  });

  it("starts a v2 production run and leaves canvas authority on the server", async () => {
    const plan = buildFastCanvasAgentKernelPlan({
      userText: "帮我做一个水果短片",
      selectedSkillIds: [],
      runMode: "draft",
      pinnedNodes: [],
    });
    const startRun = vi.fn().mockResolvedValue(run());
    const recordEvent = vi.fn();
    const flushCanvas = vi.fn().mockResolvedValue(true);
    const emitCommand = vi.fn();

    const result = await startKnownCanvasWorkflow({
      projectId: "project-1",
      canvasId: "canvas-1",
      userText: "帮我做一个水果短片",
      runMode: "draft",
      plan,
      idempotencyKey: "start-1",
      receiptTimeoutMs: 100,
    }, {
      startRun,
      recordEvent,
      emitCommand,
      flushCanvas,
    });

    expect(startRun).toHaveBeenCalledWith(
      "project-1",
      expect.objectContaining({
        contract_version: 2,
        goal: "帮我做一个水果短片",
        inputs: {
          request: "帮我做一个水果短片",
          intent_id: plan.intent.id,
          director_mode: "production",
          run_mode: "draft",
        },
        success_criteria: expect.arrayContaining([
          expect.stringContaining("revision"),
        ]),
      }),
    );
    expect(emitCommand).not.toHaveBeenCalled();
    expect(recordEvent).not.toHaveBeenCalled();
    expect(flushCanvas).not.toHaveBeenCalled();
    expect(result?.context).toMatchObject({
      workflow_run_id: "wfr-1",
      current_step: "canvas_structure",
      structure_already_applied: false,
      canvas_created_node_ids: [],
    });
    expect(result?.run.revision).toBe(0);
  });

  it.each([
    { name: "no recommendations", selection: {}, expected: {} },
    { name: "explicit ID", selection: { starterWorkflowId: "video-composition" }, expected: { starter_workflow_id: "video-composition" } },
    { name: "explicit opt-in", selection: { useStarterWorkflow: true }, expected: { use_starter_workflow: true } },
    { name: "blank ID", selection: { starterWorkflowId: "  " }, expected: {} },
  ])("starts with $name without silently copying a recommendation", async ({ selection, expected }) => {
    const plan = buildFastCanvasAgentKernelPlan({
      userText: "帮我搭建工作流",
      selectedSkillIds: [],
      runMode: "draft",
      ...selection,
    });
    plan.workflowCandidates = [];
    const startRun = vi.fn().mockResolvedValue(run());
    await startKnownCanvasWorkflow({
      projectId: "project-1", canvasId: "canvas-1",
      userText: "帮我搭建工作流", runMode: "draft", plan,
    }, { startRun, recordEvent: vi.fn(), emitCommand: vi.fn(), flushCanvas: vi.fn() });
    expect(startRun).toHaveBeenCalledOnce();
    expect(startRun.mock.calls[0][1].inputs).toEqual({
      request: "帮我搭建工作流", intent_id: plan.intent.id,
      run_mode: "draft", director_mode: "production", ...expected,
    });
  });

  it("builds a deterministic command id for replay-safe insertion", () => {
    expect(buildWorkflowCanvasEnvelope({
      projectId: "project-1",
      canvasId: "canvas-1",
      runId: "wfr-1",
      starterWorkflowId: "story-continuity-film",
    })).toMatchObject({
      command_id: "workflow:wfr-1:canvas_structure:v1",
      run_id: "wfr-1",
      canvas_command_emitted: true,
      commands: [{
        type: "insert_starter_workflow",
        workflow_id: "story-continuity-film",
      }],
    });
  });

  it("never exposes a v2 server command to the browser executor", () => {
    const v2 = run({
      contract_version: 2,
      current_frontier: ["canvas_structure"],
      artifacts: {
        canvas_structure: {
          kind: "canvas_command",
          status: "awaiting_canvas",
          command_envelope: {
            schema: "canvas_chat_commands.v1",
            command_id: "server-owned",
            commands: [{ type: "insert_starter_workflow" }],
          },
        },
      },
    });

    expect(pendingWorkflowCanvasCommand(v2)).toBeNull();
  });

  it("turns real node task handles into a durable media progress checkpoint", () => {
    const mediaRun = run({
      current_frontier: ["media_generation"],
      step_states: {
        media_generation: {
          id: "media_generation",
          label: "执行媒体生成",
          type: "media_batch",
          handler: "canvas.run_generation_nodes",
          depends_on: [],
          status: "running",
          attempt: 1,
          error: "",
          started_at: "2026-08-14T00:00:00Z",
          completed_at: "",
        },
      },
      artifacts: {
        media_generation: {
          kind: "canvas_command",
          status: "monitoring",
          completion_mode: "media_tasks",
          target_node_ids: ["image-1", "image-2"],
        },
      },
    });
    const started = canvasWorkflowMediaBatchCheckpoint(mediaRun, [
      {
        id: "image-1",
        type: "imageGenNode",
        data: {
          isGenerating: true,
          generationTaskKey: "task-1",
          generationTaskType: "freezone_gen",
          generationTaskJobId: "job-1",
        },
      },
      {
        id: "image-2",
        type: "imageGenNode",
        data: {
          isGenerating: true,
          generationTaskKey: "task-2",
          generationTaskType: "freezone_gen",
          generationTaskJobId: "job-2",
        },
      },
    ]);

    expect(started).toMatchObject({
      eventType: "step_progress",
      stepId: "media_generation",
      payload: {
        status: "monitoring",
        jobs: expect.arrayContaining([
          expect.objectContaining({ node_id: "image-1", task_key: "task-1" }),
          expect.objectContaining({ node_id: "image-2", task_key: "task-2" }),
        ]),
      },
    });
  });

  it("only completes media after every target node has a real output", () => {
    const mediaRun = run({
      current_frontier: ["media_generation"],
      step_states: {
        media_generation: {
          id: "media_generation",
          label: "执行媒体生成",
          type: "media_batch",
          handler: "canvas.run_generation_nodes",
          depends_on: [],
          status: "running",
          attempt: 1,
          error: "",
          started_at: "2026-08-14T00:00:00Z",
          completed_at: "",
        },
      },
      artifacts: {
        media_generation: {
          kind: "canvas_command",
          status: "monitoring",
          completion_mode: "media_tasks",
          target_node_ids: ["image-1", "image-2"],
          jobs: [{ node_id: "image-1", task_key: "task-1" }],
        },
      },
    });
    const checkpoint = canvasWorkflowMediaBatchCheckpoint(mediaRun, [
      { id: "image-1", type: "imageGenNode", data: { imageUrl: "/media/one.png" } },
      { id: "image-2", type: "imageGenNode", data: { imageUrl: "/media/two.png" } },
    ]);

    expect(checkpoint).toMatchObject({
      eventType: "step_completed",
      payload: {
        media_assets: [
          { node_id: "image-1", url: "/media/one.png" },
          { node_id: "image-2", url: "/media/two.png" },
        ],
      },
    });
  });

  it("marks a media command as monitoring instead of falsely completing it on canvas receipt", async () => {
    const mediaRun = run({
      current_frontier: ["media_generation"],
      step_states: {
        media_generation: {
          id: "media_generation",
          label: "执行媒体生成",
          type: "media_batch",
          handler: "canvas.run_generation_nodes",
          depends_on: [],
          status: "running",
          attempt: 1,
          error: "",
          started_at: "2026-08-14T00:00:00Z",
          completed_at: "",
        },
      },
      artifacts: {
        media_generation: {
          kind: "canvas_command",
          status: "awaiting_canvas",
          completion_mode: "media_tasks",
          target_node_ids: ["image-1"],
          command_envelope: {
            schema: "canvas_chat_commands.v1",
            command_id: "workflow:wfr-1:media_generation:a1",
            commands: [{ type: "update_node_data", node_id: "image-1" }],
          },
        },
      },
    });
    const pending = pendingWorkflowCanvasCommand(mediaRun);
    expect(pending?.completionMode).toBe("media_tasks");
    const mediaArtifact = mediaRun.artifacts.media_generation as Record<string, unknown>;
    const recordEvent = vi.fn().mockResolvedValue(run({
      revision: 1,
      event_seq: 1,
      current_frontier: ["media_generation"],
      step_states: mediaRun.step_states,
      artifacts: {
        media_generation: {
          ...mediaArtifact,
          status: "monitoring",
        },
      },
    }));
    const emitCommand = vi.fn((envelope) => {
      window.dispatchEvent(new CustomEvent(CANVAS_COMMAND_RECEIPT_EVENT, {
        detail: {
          schema: "canvas_command_receipt.v1",
          receiptId: `${envelope.command_id}:result:1:success`,
          commandId: envelope.command_id,
          projectId: "project-1",
          canvasId: "canvas-1",
          stage: "result",
          success: true,
          applied: 1,
          createdAt: Date.now(),
        },
      }));
    });

    await applyPendingWorkflowCanvasCommand({
      projectId: "project-1",
      canvasId: "canvas-1",
      run: mediaRun,
      pending: pending!,
      receiptTimeoutMs: 100,
    }, {
      startRun: vi.fn(),
      recordEvent,
      emitCommand,
      flushCanvas: vi.fn().mockResolvedValue(true),
    });

    expect(recordEvent).toHaveBeenCalledWith(
      "project-1",
      "wfr-1",
      expect.objectContaining({ type: "step_progress", success: true }),
    );
  });
});
