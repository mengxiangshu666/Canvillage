// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab

import { describe, expect, it } from "vitest";

import { normalizeAgentFleetSnapshot } from "./agent-fleet";

describe("agent fleet contract", () => {
  it("normalizes the arbitration envelope without losing handoff metadata", () => {
    const snapshot = normalizeAgentFleetSnapshot({
      schema: "expert_plan.v1",
      fleet: {
        schema: "agent_fleet.v1",
        fleet_revision: "agent-fleet.v1:abc",
        mode: "dynamic",
        selected_agents: [{
          agent_id: "director",
          label: "导演",
          phase: "plan",
          required_capabilities: ["canvas.snapshot"],
          depends_on: [],
          side_effect: "none",
          handler: { handler_id: "handler.director", invocation: "director.plan" },
        }, {
          agent_id: "production_executor",
          label: "执行器",
          phase: "execute",
          required_capabilities: ["canvas.command"],
          depends_on: ["director"],
          side_effect: "delegated",
          handler: { handler_id: "handler.production_executor", invocation: "production.execute" },
        }],
        deferred_agents: [{ agent_id: "memory", label: "记忆", reason: "not_triggered_or_team_limit" }],
        dispatch_groups: [["director"], ["production_executor"]],
        policy: { planner: "director", executor: "production_executor", max_agents: 5 },
        execution_plan: {
          planner: "director",
          executor: "production_executor",
          handoff_policy: "receipt_backed",
          tasks: [{
            task_id: "agent-task:production_executor",
            agent_id: "production_executor",
            phase: "execute",
            depends_on: ["agent-task:director"],
            handoff_from: ["agent-task:director"],
            side_effect: "delegated",
            completion_evidence: "receipt_or_verifier",
            output_contract: { artifact_required: true, consumer_agent_ids: [] },
          }],
        },
      },
    });

    expect(snapshot).toMatchObject({
      schema: "agent_fleet.v1",
      fleet_revision: "agent-fleet.v1:abc",
      selected_agents: [
        { agent_id: "director", handler: { handler_id: "handler.director" } },
        { agent_id: "production_executor", side_effect: "delegated" },
      ],
      dispatch_groups: [["director"], ["production_executor"]],
      execution_plan: {
        handoff_policy: "receipt_backed",
        tasks: [{ output_contract: { artifact_required: true } }],
      },
    });
  });

  it("rejects an empty payload instead of rendering an invented roster", () => {
    expect(normalizeAgentFleetSnapshot({ schema: "expert_plan.v1" })).toBeNull();
  });
});

