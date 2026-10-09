// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { afterEach, describe, expect, it } from "vitest";

import {
  buildStructureProposal,
  clearStructureProposals,
  dismissStructureProposal,
  getPendingStructureProposals,
  isAutoApplySafeEnvelope,
  isNavigationOnlyEnvelope,
  queueStructureProposal,
  summarizeStructureOperation,
  takeStructureProposal,
  type StructureCommandEnvelope,
} from "./structure-proposal-store";
import {
  resolveCanvasNodeRef,
  resolveStructureEnvelopeTargets,
} from "./canvas-agent-coupling";

function envelope(commands: StructureCommandEnvelope["commands"], id = "cmd-1"): StructureCommandEnvelope {
  return {
    schema: "canvas_chat_commands.v1",
    project_id: "p",
    canvas_id: "c",
    command_id: id,
    commands,
  };
}

afterEach(() => {
  clearStructureProposals();
});

describe("structure proposal V2 model", () => {
  it("summarizes create shot sequence creates", () => {
    const summary = summarizeStructureOperation({
      type: "create_shot_sequence",
      prompts: ["a", "b", ""],
    });
    expect(summary.creates).toBe(2);
    expect(summary.kind).toBe("create");
  });

  it("keeps delete/prompt impact visible without requiring confirmation", () => {
    const del = buildStructureProposal(envelope([
      { type: "delete_node", node_id: "n1" },
    ]));
    expect(del?.risk).toBe("high");
    expect(del?.summaries[0]?.requiresConfirm).toBe(false);
    const prompt = buildStructureProposal(envelope([
      { type: "update_node_prompt", node_id: "n1", prompt: "x" },
    ]));
    expect(prompt?.risk).toBe("medium");
    expect(prompt?.summaries[0]?.requiresConfirm).toBe(false);
  });

  it("detects navigation-only envelopes", () => {
    expect(isNavigationOnlyEnvelope(envelope([
      { type: "focus_node", node_id: "n1" },
      { type: "select_node", node_id: "n2" },
    ]))).toBe(true);
    expect(isNavigationOnlyEnvelope(envelope([
      { type: "annotate", text: "hi" },
    ]))).toBe(false);
  });

  it("queues, replaces same id, take and dismiss", () => {
    queueStructureProposal(envelope([{ type: "annotate", text: "a" }], "c1"));
    queueStructureProposal(envelope([{ type: "annotate", text: "b" }], "c1"));
    expect(getPendingStructureProposals()).toHaveLength(1);
    expect(getPendingStructureProposals()[0]?.summaries[0]?.label).toContain("b");

    queueStructureProposal(envelope([{ type: "connect_nodes", source: "a", target: "b" }], "c2"));
    expect(getPendingStructureProposals()).toHaveLength(2);

    const taken = takeStructureProposal("c2");
    expect(taken?.id).toBe("c2");
    expect(getPendingStructureProposals()).toHaveLength(1);

    dismissStructureProposal("c1");
    expect(getPendingStructureProposals()).toHaveLength(0);
  });

  it("isolates proposals and matching command ids by project/canvas scope", () => {
    const first = envelope([{ type: "delete_node", node_id: "a" }], "same-id");
    const second = {
      ...envelope([{ type: "delete_node", node_id: "b" }], "same-id"),
      project_id: "p",
      canvas_id: "other",
    };
    queueStructureProposal(first);
    queueStructureProposal(second);

    expect(getPendingStructureProposals({ projectId: "p", canvasId: "c" })).toHaveLength(1);
    expect(getPendingStructureProposals({ projectId: "p", canvasId: "other" })).toHaveLength(1);
    expect(takeStructureProposal("same-id", { projectId: "p", canvasId: "c" })?.envelope.canvas_id).toBe("c");
    expect(getPendingStructureProposals({ projectId: "p", canvasId: "other" })).toHaveLength(1);
  });

  it("collects affected node ids for ghost preview", () => {
    const proposal = buildStructureProposal(envelope([
      { type: "update_node_prompt", node_id: "n9", prompt: "p" },
      { type: "connect_nodes", source: "n9", target: "n8" },
    ]));
    expect(proposal?.affectedNodeIds.sort()).toEqual(["n8", "n9"]);
    expect(proposal?.writeCount).toBe(2);
  });

  it("auto-applies every supported structure operation", () => {
    expect(isAutoApplySafeEnvelope(envelope([
      { type: "create_shot_sequence", prompts: ["a", "b", "c"] },
      { type: "connect_nodes", source: "$selected", target: "n2" },
    ]))).toBe(true);
    expect(isAutoApplySafeEnvelope(envelope([
      { type: "create_canvas_node", node_type: "scriptNode", display_name: "脚本" },
      { type: "insert_starter_workflow", workflow_id: "first-last-frame-transition" },
    ]))).toBe(true);
    expect(isAutoApplySafeEnvelope(envelope([
      { type: "delete_node", node_id: "n1" },
    ]))).toBe(true);
    expect(isAutoApplySafeEnvelope(envelope([
      { type: "update_node_prompt", node_id: "n1", prompt: "x" },
    ]))).toBe(true);
    expect(isAutoApplySafeEnvelope(envelope([
      { type: "future_unknown_command", node_id: "n1" },
    ]))).toBe(false);
  });

  it("resolves $selected and $pinned aliases", () => {
    expect(resolveCanvasNodeRef("$selected", { selectedNodeId: "sel-1", pinnedNodeIds: ["p0", "p1"] })).toBe("sel-1");
    expect(resolveCanvasNodeRef("$pinned", { selectedNodeId: "sel-1", pinnedNodeIds: ["p0", "p1"] })).toBe("p0");
    expect(resolveCanvasNodeRef("$pinned:1", { selectedNodeId: "sel-1", pinnedNodeIds: ["p0", "p1"] })).toBe("p1");
    const resolved = resolveStructureEnvelopeTargets(
      envelope([
        { type: "focus_node", node_id: "$selected" },
        { type: "connect_nodes", source: "$selected", target: "$pinned" },
      ]),
      { selectedNodeId: "A", pinnedNodeIds: ["B"] },
    );
    expect(resolved.commands[0]?.node_id).toBe("A");
    expect(resolved.commands[1]?.source).toBe("A");
    expect(resolved.commands[1]?.target).toBe("B");
  });
});
