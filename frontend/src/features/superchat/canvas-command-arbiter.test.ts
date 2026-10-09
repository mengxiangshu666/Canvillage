import { describe, expect, it } from "vitest";

import { arbitrateCanvasCommand } from "./canvas-command-arbiter";

describe("canvas command revision arbiter", () => {
  it("never replays a server-applied command into the optimistic store", () => {
    expect(arbitrateCanvasCommand({ revision: 8, server_applied: true }, 10)).toEqual({
      decision: "skip",
      reason: "authoritative_server_commit",
    });
    expect(arbitrateCanvasCommand({
      revision: 10,
      server_applied: true,
      commands: [{ type: "update_node_label" }],
    }, 10).decision).toBe("skip");
  });

  it("routes server-created nodes through authoritative snapshot reconciliation", () => {
    expect(arbitrateCanvasCommand({
      revision: 10,
      server_applied: true,
      commands: [{ type: "create_image_prompt_node" }],
    }, 10)).toEqual({
      decision: "skip",
      reason: "authoritative_server_commit",
    });

    expect(arbitrateCanvasCommand({
      revision: 8,
      server_applied: true,
      commands: [{ type: "create_shot_sequence" }],
    }, 10).decision).toBe("skip");

    expect(arbitrateCanvasCommand({
      revision: 8,
      server_applied: true,
      commands: [{ type: "create_canvas_node" }],
    }, 10).decision).toBe("skip");

    expect(arbitrateCanvasCommand({
      revision: 8,
      server_applied: true,
      commands: [{ type: "insert_starter_workflow" }],
    }, 10).decision).toBe("skip");
  });

  it("uses the same snapshot path for a newer authoritative revision", () => {
    expect(arbitrateCanvasCommand({ revision: 11, server_applied: true }, 10).decision).toBe("skip");
  });

  it("treats a revision as authoritative even when compatibility frames omit the flag", () => {
    expect(arbitrateCanvasCommand({ revision: 8, server_applied: false }, 10).decision).toBe("skip");
    expect(arbitrateCanvasCommand({ revision: 8 }, 10).decision).toBe("skip");
    expect(arbitrateCanvasCommand({ server_applied: true }, 10).decision).toBe("skip");
  });

  it("treats Agent turn and run identities as authoritative compatibility signals", () => {
    expect(arbitrateCanvasCommand({
      turn_id: "turn-1",
      commands: [{ type: "create_canvas_node" }],
    }, 10).decision).toBe("skip");
    expect(arbitrateCanvasCommand({
      run_id: "run-1",
      commands: [{ type: "create_canvas_node" }],
    }, 10).decision).toBe("skip");
    expect(arbitrateCanvasCommand({ turn_id: "  ", run_id: "" }, 10).decision).toBe("apply");
  });

  it("keeps explicitly marked browser proposals executable even with a revision", () => {
    expect(arbitrateCanvasCommand({
      canvas_command_emitted: true,
      revision: 8,
      server_applied: false,
      turn_id: "turn-local-proposal",
      commands: [{ type: "create_canvas_node" }],
    }, 10)).toEqual({
      decision: "apply",
      reason: "client_only_command",
    });
  });

  it("keeps only identity-free revisionless client commands eligible", () => {
    expect(arbitrateCanvasCommand({ server_applied: false }, 10).decision).toBe("apply");
    expect(arbitrateCanvasCommand({}, 10).decision).toBe("apply");
  });

  it("flags an authoritative frame whose revision predates the live canvas as stale", () => {
    expect(arbitrateCanvasCommand({ revision: 8 }, 10)).toEqual({
      decision: "skip",
      reason: "stale_revision",
    });
    expect(arbitrateCanvasCommand({ revision: 8, server_applied: false }, 12)).toEqual({
      decision: "skip",
      reason: "stale_revision",
    });
  });

  it("keeps an explicit server_applied commit authoritative even when its revision is older", () => {
    // The frame proves the server already wrote it, so the reason must stay the
    // commit signal rather than the weaker "stale echo" one.
    expect(arbitrateCanvasCommand({ revision: 8, server_applied: true }, 10)).toEqual({
      decision: "skip",
      reason: "authoritative_server_commit",
    });
  });

  it("does not call a current-or-newer revision stale", () => {
    expect(arbitrateCanvasCommand({ revision: 10 }, 10).reason).toBe("authoritative_server_commit");
    expect(arbitrateCanvasCommand({ revision: 11 }, 10).reason).toBe("authoritative_server_commit");
  });

  it("never flags a stale revision without a comparable current canvas revision", () => {
    expect(arbitrateCanvasCommand({ revision: 8 }, null).reason).toBe("authoritative_server_commit");
    expect(arbitrateCanvasCommand({ revision: 8 }, undefined).reason).toBe("authoritative_server_commit");
    expect(arbitrateCanvasCommand({ revision: 8 }, 0).reason).toBe("authoritative_server_commit");
  });

  it("keeps a marked browser proposal applying regardless of revision age", () => {
    expect(arbitrateCanvasCommand({
      canvas_command_emitted: true,
      revision: 3,
      commands: [{ type: "create_canvas_node" }],
    }, 10).decision).toBe("apply");
  });
});
