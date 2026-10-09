// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  acknowledgeCanvasAgentCommandEnvelope,
  CANVAS_AGENT_COMMAND_EVENT,
  emitCanvasAgentCommandEnvelope,
  replayPendingCanvasAgentCommands,
} from "./canvas-patch-events";
import {
  isOptimisticCanvasMutationActive,
  isSafeOptimisticCreateEnvelope,
  optimisticCanvasGraphPresent,
  pendingOptimisticCanvasCommandCount,
  prepareOptimisticCanvasEnvelope,
  registerOptimisticCanvasCommand,
  resetOptimisticCanvasCommandStateForTests,
  rollbackOptimisticCanvasCommandsForTurn,
  runOptimisticCanvasMutation,
} from "./optimistic-canvas-commands";
import { applyStructureEnvelopeToStore } from "@/features/freezone/FreezoneShell";
import { useCanvasStore } from "@/stores/canvasStore";

describe("pending canvas Agent command recovery", () => {
  afterEach(() => {
    vi.useRealTimers();
    resetOptimisticCanvasCommandStateForTests();
  });

  it("replays an interrupted command once the canvas listener returns, then stops after ack", () => {
    const envelope = {
      project_id: "project-replay",
      canvas_id: "canvas-replay",
      command_id: "command-replay",
    };
    const received: unknown[] = [];
    const onCommand = (event: Event) => {
      received.push((event as CustomEvent).detail);
    };
    window.addEventListener(CANVAS_AGENT_COMMAND_EVENT, onCommand);
    emitCanvasAgentCommandEnvelope(envelope);
    window.removeEventListener(CANVAS_AGENT_COMMAND_EVENT, onCommand);

    expect(received).toEqual([envelope]);
    window.addEventListener(CANVAS_AGENT_COMMAND_EVENT, onCommand);
    expect(replayPendingCanvasAgentCommands("project-replay", "canvas-replay")).toBe(1);
    expect(received).toEqual([envelope, envelope]);

    acknowledgeCanvasAgentCommandEnvelope(envelope);
    expect(replayPendingCanvasAgentCommands("project-replay", "canvas-replay")).toBe(0);
    window.removeEventListener(CANVAS_AGENT_COMMAND_EVENT, onCommand);
  });

  it("never replays a pending command into another canvas", () => {
    const envelope = {
      project_id: "project-scope",
      canvas_id: "canvas-a",
      command_id: "command-scope",
    };
    emitCanvasAgentCommandEnvelope(envelope);

    expect(replayPendingCanvasAgentCommands("project-scope", "canvas-b")).toBe(0);
    expect(replayPendingCanvasAgentCommands("another-project", "canvas-a")).toBe(0);
    acknowledgeCanvasAgentCommandEnvelope(envelope);
  });

  it("expires abandoned commands instead of replaying stale state forever", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-09T00:00:00Z"));
    const envelope = {
      project_id: "project-expired",
      canvas_id: "canvas-expired",
      command_id: "command-expired",
    };
    emitCanvasAgentCommandEnvelope(envelope);
    vi.setSystemTime(new Date("2026-08-09T00:02:01Z"));

    expect(replayPendingCanvasAgentCommands("project-expired", "canvas-expired")).toBe(0);
    acknowledgeCanvasAgentCommandEnvelope(envelope);
  });

  it("rolls back an unfinished create preview without touching existing nodes", () => {
    useCanvasStore.getState().clearCanvas();
    const existingId = useCanvasStore.getState().addNode(
      "textAnnotationNode",
      { x: 0, y: 0 },
      { text: "existing" },
    );
    const envelope = prepareOptimisticCanvasEnvelope({
      schema: "canvas_chat_commands.v1",
      project_id: "project-preview",
      canvas_id: "canvas-preview",
      command_id: "command-preview",
      turn_id: "turn-preview",
      commands: [{ type: "create_image_prompt_node", prompt: "preview" }],
    });
    registerOptimisticCanvasCommand(envelope);
    runOptimisticCanvasMutation(envelope, () => {
      expect(isOptimisticCanvasMutationActive("project-preview", "canvas-preview")).toBe(true);
      expect(applyStructureEnvelopeToStore(envelope)).toBe(1);
    });
    expect(useCanvasStore.getState().nodes).toHaveLength(2);
    expect(pendingOptimisticCanvasCommandCount()).toBe(1);

    expect(rollbackOptimisticCanvasCommandsForTurn("turn-preview")).toEqual([{
      projectId: "project-preview",
      canvasId: "canvas-preview",
    }]);
    expect(useCanvasStore.getState().nodes.map((node) => node.id)).toEqual([existingId]);
    expect(pendingOptimisticCanvasCommandCount()).toBe(0);
  });

  it("does not confuse a registered command with a rendered optimistic graph", () => {
    useCanvasStore.getState().clearCanvas();
    const envelope = prepareOptimisticCanvasEnvelope({
      schema: "canvas_chat_commands.v1",
      project_id: "project-present",
      canvas_id: "canvas-present",
      command_id: "command-present",
      commands: [{ type: "create_image_prompt_node", prompt: "preview" }],
    });
    registerOptimisticCanvasCommand(envelope);

    expect(optimisticCanvasGraphPresent(envelope)).toBe(false);
    runOptimisticCanvasMutation(envelope, () => {
      expect(applyStructureEnvelopeToStore(envelope)).toBe(1);
    });
    expect(optimisticCanvasGraphPresent(envelope)).toBe(true);
  });
});

describe("optimistic create envelope eligibility", () => {
  it("accepts duplicate_node now that its type is aligned with the server", () => {
    const prepared = prepareOptimisticCanvasEnvelope({
      schema: "canvas_chat_commands.v1",
      project_id: "project-dup",
      canvas_id: "canvas-dup",
      command_id: "command-dup",
      commands: [{ type: "duplicate_node", node_id: "source-node" }],
    });

    expect(prepared.commands[0].created_node_id).toBeTruthy();
    expect(isSafeOptimisticCreateEnvelope(prepared)).toBe(true);
  });

  it("rejects a duplicate_node whose source node is unknown", () => {
    expect(isSafeOptimisticCreateEnvelope({
      schema: "canvas_chat_commands.v1",
      project_id: "project-dup",
      canvas_id: "canvas-dup",
      command_id: "command-dup",
      commands: [{ type: "duplicate_node", created_node_id: "agent-dup-1" }],
    })).toBe(false);
  });

  it("drops insert_starter_workflow instead of disqualifying the whole batch", () => {
    const prepared = prepareOptimisticCanvasEnvelope({
      schema: "canvas_chat_commands.v1",
      project_id: "project-mixed",
      canvas_id: "canvas-mixed",
      command_id: "command-mixed",
      commands: [
        { type: "insert_starter_workflow", workflow_id: "first-last-frame-transition" },
        { type: "create_image_prompt_node", prompt: "hero shot" },
      ],
    });

    // The known-but-unrenderable workflow insert is removed from the preview...
    expect(prepared.commands.map((command) => command.type)).toEqual([
      "create_image_prompt_node",
    ]);
    // ...so the deterministic create it was batched with still renders.
    expect(prepared.commands[0].created_node_id).toBeTruthy();
    expect(isSafeOptimisticCreateEnvelope(prepared)).toBe(true);
  });

  it("still rejects an unrecognized node-creating command", () => {
    expect(isSafeOptimisticCreateEnvelope(prepareOptimisticCanvasEnvelope({
      schema: "canvas_chat_commands.v1",
      project_id: "project-unknown",
      canvas_id: "canvas-unknown",
      command_id: "command-unknown",
      commands: [{ type: "create_mystery_node", created_node_id: "agent-x-1" }],
    }))).toBe(false);
  });
});
