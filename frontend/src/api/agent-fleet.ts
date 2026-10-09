// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab

import { api } from "@/lib/api";

export type AgentFleetAgent = {
  agent_id: string;
  label: string;
  phase: string;
  selection_reason?: string;
  required_capabilities: string[];
  depends_on: string[];
  side_effect: string;
  handler: {
    handler_id?: string;
    invocation?: string;
  };
};

export type AgentFleetTask = {
  task_id: string;
  agent_id: string;
  phase: string;
  depends_on: string[];
  handoff_from: string[];
  side_effect: string;
  completion_evidence: string;
  handler: {
    handler_id?: string;
    invocation?: string;
  };
  output_contract: {
    artifact_required?: boolean;
    consumer_agent_ids: string[];
  };
};

export type AgentFleetSnapshot = {
  schema: string;
  fleet_revision: string;
  registry_revision?: string;
  mode: string;
  selected_agents: AgentFleetAgent[];
  deferred_agents: Array<{ agent_id: string; label: string; reason?: string }>;
  dispatch_groups: string[][];
  policy: {
    planner?: string;
    executor?: string;
    side_effects?: string;
    max_agents?: number;
    state_source?: string;
    registry?: string;
  };
  execution_plan?: {
    plan_revision?: string;
    planner?: string;
    executor?: string;
    handoff_policy?: string;
    tasks: AgentFleetTask[];
  };
};

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function text(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value.trim() : fallback;
}

function strings(value: unknown, limit = 16): string[] {
  if (!Array.isArray(value)) return [];
  return value
    .map((item) => text(item))
    .filter(Boolean)
    .slice(0, limit);
}

function handler(value: unknown) {
  const item = record(value);
  return {
    ...(text(item.handler_id) ? { handler_id: text(item.handler_id) } : {}),
    ...(text(item.invocation) ? { invocation: text(item.invocation) } : {}),
  };
}

export function normalizeAgentFleetSnapshot(value: unknown): AgentFleetSnapshot | null {
  const root = record(value);
  const payload = record(root.data);
  const envelope = Object.keys(payload).length > 0 ? payload : root;
  // expert-plan returns an arbitration envelope; the fleet is its durable
  // nested contract. Accept a flattened payload too for older runtimes.
  const nestedFleet = record(envelope.fleet);
  const source = Object.keys(nestedFleet).length > 0 ? nestedFleet : envelope;
  const selected = Array.isArray(source.selected_agents) ? source.selected_agents : [];
  const deferred = Array.isArray(source.deferred_agents) ? source.deferred_agents : [];
  const selectedAgents = selected
    .map((item) => {
      const agent = record(item);
      const agentId = text(agent.agent_id);
      if (!agentId) return null;
      return {
        agent_id: agentId,
        label: text(agent.label, agentId),
        phase: text(agent.phase, "domain"),
        ...(text(agent.selection_reason) ? { selection_reason: text(agent.selection_reason) } : {}),
        required_capabilities: strings(agent.required_capabilities, 12),
        depends_on: strings(agent.depends_on, 8),
        side_effect: text(agent.side_effect, "none"),
        handler: handler(agent.handler),
      } satisfies AgentFleetAgent;
    })
    .filter((item): item is AgentFleetAgent => Boolean(item))
    .slice(0, 8);
  const deferredAgents = deferred
    .map((item) => {
      const agent = record(item);
      const agentId = text(agent.agent_id);
      return agentId
        ? { agent_id: agentId, label: text(agent.label, agentId), ...(text(agent.reason) ? { reason: text(agent.reason) } : {}) }
        : null;
    })
    .filter((item): item is { agent_id: string; label: string; reason?: string } => Boolean(item))
    .slice(0, 32);
  const rawPlan = record(source.execution_plan);
  const tasks = (Array.isArray(rawPlan.tasks) ? rawPlan.tasks : [])
    .map((item) => {
      const task = record(item);
      const taskId = text(task.task_id);
      if (!taskId) return null;
      const contract = record(task.output_contract);
      return {
        task_id: taskId,
        agent_id: text(task.agent_id),
        phase: text(task.phase, "domain"),
        depends_on: strings(task.depends_on, 16),
        handoff_from: strings(task.handoff_from, 8),
        side_effect: text(task.side_effect, "none"),
        completion_evidence: text(task.completion_evidence),
        handler: handler(task.handler),
        output_contract: {
          ...(typeof contract.artifact_required === "boolean" ? { artifact_required: contract.artifact_required } : {}),
          consumer_agent_ids: strings(contract.consumer_agent_ids, 8),
        },
      } satisfies AgentFleetTask;
    })
    .filter((item): item is AgentFleetTask => Boolean(item))
    .slice(0, 32);
  if (!selectedAgents.length && !text(source.fleet_revision)) return null;
  const policy = record(source.policy);
  return {
    schema: text(source.schema, "agent_fleet.v1"),
    fleet_revision: text(source.fleet_revision),
    ...(text(source.registry_revision) ? { registry_revision: text(source.registry_revision) } : {}),
    mode: text(source.mode, "dynamic"),
    selected_agents: selectedAgents,
    deferred_agents: deferredAgents,
    dispatch_groups: (Array.isArray(source.dispatch_groups) ? source.dispatch_groups : [])
      .filter(Array.isArray)
      .map((group) => strings(group, 8))
      .filter((group) => group.length > 0)
      .slice(0, 8),
    policy: {
      ...(text(policy.planner) ? { planner: text(policy.planner) } : {}),
      ...(text(policy.executor) ? { executor: text(policy.executor) } : {}),
      ...(text(policy.side_effects) ? { side_effects: text(policy.side_effects) } : {}),
      ...(typeof policy.max_agents === "number" ? { max_agents: policy.max_agents } : {}),
      ...(text(policy.state_source) ? { state_source: text(policy.state_source) } : {}),
      ...(text(policy.registry) ? { registry: text(policy.registry) } : {}),
    },
    ...(Object.keys(rawPlan).length > 0 ? {
      execution_plan: {
        ...(text(rawPlan.plan_revision) ? { plan_revision: text(rawPlan.plan_revision) } : {}),
        ...(text(rawPlan.planner) ? { planner: text(rawPlan.planner) } : {}),
        ...(text(rawPlan.executor) ? { executor: text(rawPlan.executor) } : {}),
        ...(text(rawPlan.handoff_policy) ? { handoff_policy: text(rawPlan.handoff_policy) } : {}),
        tasks,
      },
    } : {}),
  };
}

export async function getAgentFleetPlan(
  project: string,
  canvasId: string,
  query: string,
  signal?: AbortSignal,
): Promise<AgentFleetSnapshot | null> {
  const response = await api.get("api/v1/chat/context/expert-plan", {
    searchParams: {
      project,
      canvas_id: canvasId,
      query: query.slice(0, 2_000),
      sources: "memory,knowledge,obsidian,cognee",
    },
    signal,
  }).json<unknown>();
  return normalizeAgentFleetSnapshot(response);
}
