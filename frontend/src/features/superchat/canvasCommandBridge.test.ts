// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from "vitest";

import { canvasCommandEnvelopeFromToolFrame } from "./use-superchat";
import { deterministicAgentNodeId } from "./optimistic-canvas-commands";

describe("canvasCommandEnvelopeFromToolFrame", () => {
  const envelope = {
    schema: "canvas_chat_commands.v1",
    project_id: "demo",
    canvas_id: "default",
    command_id: "cmd-001",
    canvas_command_emitted: true,
    commands: [{ type: "annotate", text: "检查角色一致性" }],
  };

  it("never replays an authoritative Hermes tool trace into the browser canvas", () => {
    const frame = {
      type: "tool.result" as const,
      name: "freezone_emit_canvas_command",
      result: {
        text: [
          "参数: {\"canvas_id\":\"default\",\"commands\":[{\"type\":\"annotate\"}]}",
          "  completed",
          JSON.stringify(envelope),
        ].join("\n"),
      },
    };

    expect(canvasCommandEnvelopeFromToolFrame(frame)).toBeNull();
  });

  it("turns a create tool.call into an immediate deterministic preview", () => {
    const frame = {
      type: "tool.call" as const,
      turn_id: "turn-live-create",
      name: "freezone_emit_canvas_command",
      input: {
        project_id: "demo",
        canvas_id: "default",
        command_id: "cmd-live-create",
        commands: [{
          type: "create_image_prompt_node",
          prompt: "即时出现",
        }],
      },
    };

    expect(canvasCommandEnvelopeFromToolFrame(frame)).toEqual({
      schema: "canvas_chat_commands.v1",
      project_id: "demo",
      canvas_id: "default",
      command_id: "cmd-live-create",
      turn_id: "turn-live-create",
      commands: [{
        type: "create_image_prompt_node",
        prompt: "即时出现",
        connect_selected: false,
        created_node_id: deterministicAgentNodeId("cmd-live-create", 1),
      }],
      canvas_command_emitted: true,
      optimistic: true,
      server_applied: false,
      snapshot_required: false,
      ui_reconcile_required: false,
    });
  });

  it("previews the canonical dispatch tool using the active canvas scope", () => {
    const frame = {
      type: "tool.call" as const,
      turn_id: "turn-dispatch-create",
      name: "village_canvas_dispatch_action",
      input: {
        command_id: "cmd-dispatch-create",
        request: "添加一个分镜提示词节点",
        task: {
          operation: "create_storyboard_prompt",
          step_count: 1,
          item_count: 1,
          dependency_count: 0,
          estimated_duration_seconds: 1,
          requires_recovery: false,
          requires_delivery: false,
          contains_paid_media: false,
        },
        commands: [{
          type: "create_image_prompt_node",
          prompt: "雨夜追逐",
        }],
      },
    };

    expect(canvasCommandEnvelopeFromToolFrame(frame, {
      kind: "project",
      id: "demo",
      canvas_id: "canvas-live",
    })).toMatchObject({
      project_id: "demo",
      canvas_id: "canvas-live",
      command_id: "cmd-dispatch-create",
      turn_id: "turn-dispatch-create",
      optimistic: true,
      commands: [{
        type: "create_image_prompt_node",
        created_node_id: deterministicAgentNodeId("cmd-dispatch-create", 1),
      }],
    });
  });

  it("previews the canonical apply alias without changing server-first rules", () => {
    const frame = {
      type: "tool.call" as const,
      turn_id: "turn-apply-create",
      name: "village_canvas_apply_commands",
      input: {
        project_id: "demo",
        canvas_id: "default",
        command_id: "cmd-apply-create",
        commands: [{ type: "annotate", text: "实时导演备注" }],
      },
    };

    expect(canvasCommandEnvelopeFromToolFrame(frame)).toMatchObject({
      project_id: "demo",
      canvas_id: "default",
      command_id: "cmd-apply-create",
      optimistic: true,
    });
  });

  it("keeps destructive and mixed existing-node transactions server-first", () => {
    for (const commands of [
      [{ type: "delete_node", node_id: "node-1" }],
      [{ type: "update_node_prompt", node_id: "node-1", prompt: "new" }],
      [
        { type: "create_image_prompt_node", prompt: "new" },
        { type: "delete_node", node_id: "node-1" },
      ],
    ]) {
      expect(canvasCommandEnvelopeFromToolFrame({
        type: "tool.call",
        turn_id: "turn-server-first",
        name: "freezone_emit_canvas_command",
        input: {
          project_id: "demo",
          canvas_id: "default",
          command_id: "cmd-server-first",
          commands,
        },
      })).toBeNull();
    }
  });

  it("never replays a directly structured server tool result", () => {
    expect(canvasCommandEnvelopeFromToolFrame({
      type: "tool.result",
      name: "freezone_emit_canvas_command",
      result: envelope,
    })).toBeNull();
  });

  it("keeps node generation and task-control receipts out of the mutation path", () => {
    for (const name of ["freezone_run_node", "freezone_retry_node", "freezone_stop_task"]) {
      expect(canvasCommandEnvelopeFromToolFrame({
        type: "tool.result",
        name,
        result: envelope,
      })).toBeNull();
    }
  });

  it("never executes malformed or unrelated tool output", () => {
    expect(canvasCommandEnvelopeFromToolFrame({
      type: "tool.result",
      name: "freezone_emit_canvas_command",
      result: { text: "canvas_chat_commands.v1 {not-json" },
    })).toBeNull();
    expect(canvasCommandEnvelopeFromToolFrame({
      type: "tool.result",
      name: "village_canvas_pipeline_status",
      result: envelope,
    })).toBeNull();
  });
});
