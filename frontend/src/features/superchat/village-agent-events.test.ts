// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from "vitest";

import type { ServerFrame } from "@/features/superchat/types";
import {
  mergeVillageAgentEvents,
  normalizeVillageAgentEvent,
  villageAgentEventFromChatFrame,
  villageAgentEventFromWorkflowEnvelope,
  villageAgentEventMatchesScope,
} from "@/features/superchat/village-agent-events";

describe("VillageAgentEvent v1", () => {
  it("normalizes the compatibility envelope without changing the legacy frame", () => {
    const frame = {
      type: "canvas.patch",
      project_id: "project-1",
      canvas_id: "canvas-1",
      command_id: "command-1",
      revision: 8,
      agent_event: {
        schema: "village_agent_event.v1",
        event_id: "event-1",
        seq: 4,
        type: "canvas.receipt",
        status: "completed",
        turn_id: "turn-1",
        run_id: "run-1",
        project_id: "project-1",
        canvas_id: "canvas-1",
        command_id: "command-1",
        revision: 8,
        payload: { legacy_type: "canvas.patch" },
      },
    } as ServerFrame;

    const event = villageAgentEventFromChatFrame(frame);
    expect(frame.type).toBe("canvas.patch");
    expect(event).toMatchObject({
      event_id: "event-1",
      type: "canvas.receipt",
      command_id: "command-1",
      revision: 8,
    });
    expect(villageAgentEventMatchesScope(event!, {
      kind: "project",
      id: "project-1",
      canvas_id: "canvas-1",
    })).toBe(true);
  });

  it("keeps an old backend usable through the bounded legacy adapter", () => {
    const event = villageAgentEventFromChatFrame({
      type: "tool.result",
      turn_id: "turn-2",
      name: "village_canvas_apply_commands",
      success: false,
      error: "failed",
    });

    expect(event).toMatchObject({
      type: "tool.result",
      status: "failed",
      turn_id: "turn-2",
    });
  });

  it("normalizes WorkflowRun SSE and deduplicates replay by event id", () => {
    const event = villageAgentEventFromWorkflowEnvelope({
      run_id: "workflow-run-1",
      event: {
        event_id: "source-event-1",
        seq: 6,
        type: "step_progress",
        step_id: "story",
      },
      agent_event: {
        schema: "village_agent_event.v1",
        event_id: "event-workflow-1",
        seq: 6,
        type: "step.progress",
        status: "running",
        workflow_run_id: "workflow-run-1",
        step_id: "story",
        payload: {},
      },
    });
    const newer = normalizeVillageAgentEvent({
      ...event,
      seq: 7,
      status: "completed",
    });

    expect(mergeVillageAgentEvents([event!], [event!, newer!])).toEqual([newer]);
  });
});
