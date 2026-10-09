// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from "vitest";

import {
  buildAgentExecutionTimeline,
  formatAgentExecutionDuration,
  humanizeAgentToolName,
} from "./agent-execution-timeline";
import type { VillageAgentEvent } from "./village-agent-events";

function event(
  eventId: string,
  seq: number,
  type: string,
  status: string,
  createdAt: string,
  payload: Record<string, unknown> = {},
): VillageAgentEvent {
  return {
    schema: "village_agent_event.v1",
    event_id: eventId,
    seq,
    type,
    status,
    created_at: createdAt,
    turn_id: "turn-1",
    project_id: "project-1",
    canvas_id: "canvas-1",
    payload,
  };
}

describe("Agent execution timeline", () => {
  it("reports executor startup before any real execution event arrives", () => {
    const timeline = buildAgentExecutionTimeline({
      activeTurnId: "turn-1",
      busy: true,
      events: [],
    });

    expect(timeline?.steps).toEqual([
      expect.objectContaining({
        id: "starting",
        label: "正在启动执行器",
        status: "running",
      }),
    ]);
    expect(timeline?.steps.some((step) => step.label.includes("读取项目"))).toBe(false);
  });

  it("aggregates repeated tool calls and uses real event timestamps for duration", () => {
    const timeline = buildAgentExecutionTimeline({
      activeTurnId: "turn-1",
      busy: true,
      progress: {
        turnId: "turn-1",
        stage: "canvas.command",
        message: "正在写入画布",
        elapsedSeconds: 2,
      },
      events: [
        event("start", 1, "run.started", "running", "2026-08-20T12:00:00.000Z"),
        event("call-1", 2, "tool.call", "running", "2026-08-20T12:00:00.010Z", {
          name: "village_canvas_get_sketches",
        }),
        event("result-1", 3, "tool.result", "completed", "2026-08-20T12:00:00.061Z", {
          name: "village_canvas_get_sketches",
        }),
        event("call-2", 4, "tool.call", "running", "2026-08-20T12:00:00.100Z", {
          name: "village_canvas_get_sketches",
        }),
        event("result-2", 5, "tool.result", "completed", "2026-08-20T12:00:00.434Z", {
          name: "village_canvas_get_sketches",
        }),
      ],
      planSteps: [
        { id: "observe", label: "读取真实项目 / 画布状态", status: "done" },
        { id: "act", label: "写入结构、资产或生产步骤", status: "running" },
      ],
    });

    const detail = timeline?.steps.find((step) => step.id === "act")?.details[0];
    expect(timeline).toMatchObject({
      title: "正在继续处理你的请求",
      stageLabel: "正在写入画布",
      elapsedMs: 2_000,
    });
    expect(detail).toMatchObject({
      label: "读取分镜",
      technicalName: "village_canvas_get_sketches",
      count: 2,
      status: "completed",
      durationMs: 385,
    });
  });

  it("keeps workflow steps authoritative and attaches correlated event detail", () => {
    const timeline = buildAgentExecutionTimeline({
      busy: true,
      events: [{
        ...event("workflow-progress", 7, "step.progress", "running", "2026-08-20T12:00:01Z"),
        workflow_run_id: "workflow-1",
        step_id: "story",
      }],
      workflowRun: {
        id: "workflow-1",
        workflow_id: "one-click-film",
        workflow_version: 2,
        project_id: "project-1",
        canvas_id: "canvas-1",
        run_mode: "auto",
        status: "running",
        current_frontier: ["story"],
        step_states: {
          story: {
            id: "story",
            label: "生成故事结构",
            type: "agent",
            handler: "story",
            depends_on: [],
            status: "running",
            attempt: 1,
            error: "",
            started_at: "2026-08-20T12:00:00Z",
            completed_at: "",
          },
        },
        inputs: {},
        artifacts: {},
        error: "",
        revision: 1,
        event_seq: 7,
        idempotency_key: "workflow-1",
        created_at: "2026-08-20T12:00:00Z",
        updated_at: "2026-08-20T12:00:01Z",
      },
    });

    expect(timeline?.steps).toHaveLength(1);
    expect(timeline?.steps[0]).toMatchObject({
      id: "workflow:story",
      label: "生成故事结构",
      status: "running",
    });
    expect(timeline?.steps[0].details[0]).toMatchObject({
      technicalName: "step.progress",
      status: "running",
    });
  });

  it("does not invent durations when an event has no real start/end pair", () => {
    const timeline = buildAgentExecutionTimeline({
      busy: true,
      events: [event("receipt", 1, "canvas.receipt", "completed", "2026-08-20T12:00:00Z")],
    });
    expect(timeline?.steps[0].details[0].durationMs).toBeUndefined();
    expect(formatAgentExecutionDuration(undefined)).toBeNull();
    expect(formatAgentExecutionDuration(51)).toBe("51ms");
    expect(humanizeAgentToolName("village_canvas_get_character_media")).toBe("读取角色素材");
  });

  it("labels the canvas dispatch router as an execution action", () => {
    expect(humanizeAgentToolName("village_canvas_dispatch_action")).toBe("执行画布操作");
    expect(humanizeAgentToolName("village.ui.dispatch_action")).toBe("执行画布操作");
  });

  it("surfaces receipt verification evidence instead of generic tool success", () => {
    const timeline = buildAgentExecutionTimeline({
      busy: true,
      events: [event("receipt-tool", 1, "tool.result", "completed", "2026-08-20T12:00:00Z", {
        name: "village_canvas_dispatch_action",
        execution: {
          receipt: {
            schema: "canvas_command_receipt.v2",
            revision: 19,
            applied_ops: 1,
          },
          verification: { status: "receipt_verified" },
        },
      })],
    });

    const detail = timeline?.steps[0].details[0];
    expect(detail).toMatchObject({
      technicalName: "village_canvas_dispatch_action",
      status: "completed",
      message: "回执已核验 · 画布版本 19 · 应用 1 项",
    });
  });

  it("keeps a missing receipt pending until verification returns", () => {
    const timeline = buildAgentExecutionTimeline({
      busy: true,
      events: [event("receipt-missing", 1, "canvas.receipt", "completed", "2026-08-20T12:00:00Z", {
        verification: { status: "receipt_missing" },
      })],
    });

    const detail = timeline?.steps[0].details[0];
    expect(detail).toMatchObject({
      label: "等待画布回执",
      status: "pending",
      message: "保存回执暂未返回，正在核对",
    });
  });

  it("does not turn a missing receipt from a tool result into a failure", () => {
    const timeline = buildAgentExecutionTimeline({
      busy: true,
      events: [event("receipt-tool-missing", 1, "tool.result", "completed", "2026-08-20T12:00:00Z", {
        name: "village_canvas_dispatch_action",
        verification: { status: "receipt_missing" },
      })],
    });

    const detail = timeline?.steps[0].details[0];
    expect(detail).toMatchObject({
      label: "执行画布操作",
      status: "pending",
      message: "保存回执暂未返回，正在核对",
    });
  });

  it("keeps dispatch failures visible with their diagnostic code", () => {
    const timeline = buildAgentExecutionTimeline({
      busy: true,
      events: [event("dispatch-failed", 1, "tool.result", "failed", "2026-08-20T12:00:00Z", {
        name: "village_canvas_dispatch_action",
        error: "画布写入失败",
        diagnostic_code: "FZ_SERVER_APPLY_FAILED",
      })],
    });

    const detail = timeline?.steps[0].details[0];
    expect(detail).toMatchObject({
      label: "执行画布操作",
      technicalName: "village_canvas_dispatch_action",
      status: "failed",
      message: "画布写入失败（FZ_SERVER_APPLY_FAILED）",
    });
  });
});
