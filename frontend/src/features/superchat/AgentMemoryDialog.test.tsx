import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { AgentMemoryItem } from "@/api/agent-memory";

import { AgentMemoryDialog } from "./AgentMemoryDialog";

const listAgentMemories = vi.fn();
const getAgentMemoryStats = vi.fn();
const createAgentMemory = vi.fn();
const createManualAgentMemory = vi.fn();
const updateAgentMemory = vi.fn();
const promoteAgentMemory = vi.fn();
const deleteAgentMemory = vi.fn();
const getTasteGraph = vi.fn();
const getGrowthMemoryContract = vi.fn();

vi.mock("@/api/agent-memory", () => ({
  listAgentMemories: (...args: unknown[]) => listAgentMemories(...args),
  getAgentMemoryStats: (...args: unknown[]) => getAgentMemoryStats(...args),
  createAgentMemory: (...args: unknown[]) => createAgentMemory(...args),
  createManualAgentMemory: (...args: unknown[]) => createManualAgentMemory(...args),
  updateAgentMemory: (...args: unknown[]) => updateAgentMemory(...args),
  promoteAgentMemory: (...args: unknown[]) => promoteAgentMemory(...args),
  deleteAgentMemory: (...args: unknown[]) => deleteAgentMemory(...args),
  getTasteGraph: (...args: unknown[]) => getTasteGraph(...args),
  getGrowthMemoryContract: (...args: unknown[]) => getGrowthMemoryContract(...args),
}));

function memory(overrides: Partial<AgentMemoryItem>): AgentMemoryItem {
  return {
    id: 1,
    scope_kind: "user",
    scope_id: null,
    kind: "learned_rule",
    source: "user_explicit",
    content: "所有项目都保持克制的悬疑氛围",
    status: "confirmed",
    confidence: 1,
    locked: true,
    applies_when: {},
    provenance: {
      source: "user_explicit",
      source_id: "rule-1",
      evidence: [],
      distilled: true,
      compiled: true,
      memory_schema: "xiaoshu.memory.v2",
      memory_key: "user.rule.test",
      action: [],
      avoid: [],
    },
    evidence_count: 1,
    retrieved_count: 3,
    applied_count: 0,
    positive_count: 0,
    negative_count: 0,
    last_verified_at: null,
    version: 1,
    promoted_from_id: null,
    supersedes_id: null,
    created_at: "2026-08-17T12:00:00Z",
    updated_at: "2026-08-17T12:00:00Z",
    ...overrides,
  };
}

describe("AgentMemoryDialog", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    listAgentMemories.mockResolvedValue([
      memory({}),
      memory({
        id: 2,
        kind: "candidate_experience",
        content: "单一异常细节比元素堆叠更有效",
        status: "candidate",
        confidence: 0.45,
        locked: false,
      }),
    ]);
    getAgentMemoryStats.mockResolvedValue({
      total: 2,
      effective: 1,
      compiled: 2,
      embedded: 1,
      archived: 0,
      pending_events: 1,
      successful_applications: 0,
      episode_count: 0,
      feedback_count: 0,
      positive_feedback: 0,
      negative_feedback: 0,
      verifier_events: 0,
      workflow_evidence: 0,
      validated_experience: 0,
      by_status: { confirmed: 1, candidate: 1 },
      by_scope: { user: 2 },
      by_kind: { learned_rule: 1, candidate_experience: 1 },
    });
    getTasteGraph.mockResolvedValue({
      schema: "taste_graph.v1",
      revision: "r1",
      project_id: "",
      source: {
        project: "TasteGraph-Skill",
        commit: "abc123",
        license: "MIT",
        integration: "memory_index_projection",
      },
      hard_loves: [],
      hard_antis: [],
      consultation: { do: [], avoid: [] },
      affinity_graph: { nodes: [], edges: [] },
      stats: { eligible: 0, rejected: 0, love_count: 0, anti_count: 0 },
    });
    getGrowthMemoryContract.mockResolvedValue({
      role: "growth_distiller",
      modelEnv: "NOVELVIDEO_GROWTH_MODEL",
      thinkingLevelEnv: "NOVELVIDEO_GROWTH_THINKING_LEVEL",
      modelRef: "direct/agent-9df1c7093446fd7a",
      modelSource: "default_text_model",
      autoTextModelEnv: "NOVELVIDEO_TEXT_MODEL",
      transport: "openai-compatible",
      credentialSource: "env",
      sideEffects: ["returns_candidate_only"],
      schemaVersion: "growth_memory_contract.v1",
    });
  });

  it("shows the useful memory sections and promotes a candidate", async () => {
    promoteAgentMemory.mockResolvedValue(memory({
      id: 2,
      kind: "verified_experience",
      content: "单一异常细节比元素堆叠更有效",
      status: "confirmed",
      confidence: 0.85,
      locked: false,
    }));

    render(<AgentMemoryDialog open onOpenChange={vi.fn()} />);

    expect(await screen.findByRole("dialog")).toHaveClass("village-agent-dialog-v4--memory");
    expect(await screen.findByText("所有项目都保持克制的悬疑氛围")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "候选经验 · 1" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "候选经验 · 1" }));
    expect(await screen.findByText("单一异常细节比元素堆叠更有效")).toBeInTheDocument();
    fireEvent.click(screen.getByTitle("确认为长期经验"));
    await waitFor(() => expect(promoteAgentMemory).toHaveBeenCalledWith(2));
  });

  it("queues a raw teaching event for model distillation", async () => {
    createAgentMemory.mockResolvedValue({
      accepted: true,
      event_id: 3,
      status: "pending_distillation",
      receipt: {
        schema: "growth_distillation_receipt.v1",
        event_id: 3,
        project_id: "",
        task_id: "memory-ui-3",
        status: "pending",
        decision: "",
        terminal: false,
        memory_id: 0,
        attempt_count: 0,
        result_available: false,
        reason: "",
        created_at: "2026-09-02T00:00:00Z",
        processed_at: "",
        next_attempt_at: "",
      },
    });
    render(<AgentMemoryDialog open onOpenChange={vi.fn()} />);

    const input = await screen.findByPlaceholderText("写下经验，小树会提炼触发条件、动作和验收标准");
    fireEvent.change(input, { target: { value: "节点完成必须有真实回执" } });
    fireEvent.click(screen.getByRole("button", { name: "提炼" }));

    await waitFor(() => expect(createAgentMemory).toHaveBeenCalledWith({
      content: "节点完成必须有真实回执",
      kind: "learned_rule",
      locked: true,
    }));
    expect(await screen.findByText(/事件 #3 · 等待提炼/)).toBeInTheDocument();
  });

  it("saves a manual memory immediately from every tab", async () => {
    const created = memory({
      id: 9,
      content: "视频节点默认关闭原生音频。",
      source: "memory_ui_manual",
      kind: "learned_rule",
      locked: true,
    });
    createManualAgentMemory.mockResolvedValue(created);
    render(<AgentMemoryDialog open onOpenChange={vi.fn()} />);

    fireEvent.click(await screen.findByRole("button", { name: /我的偏好 ·/ }));
    fireEvent.click(screen.getByRole("button", { name: "手动添加" }));
    fireEvent.change(screen.getByPlaceholderText("写一条具体规则或偏好，例如：视频节点默认关闭原生音频"), {
      target: { value: "视频节点默认关闭原生音频" },
    });
    fireEvent.click(screen.getByRole("button", { name: "直接保存" }));

    await waitFor(() => expect(createManualAgentMemory).toHaveBeenCalledWith({
      content: "视频节点默认关闭原生音频",
      kind: "learned_rule",
      locked: true,
    }));
    expect(await screen.findByText("视频节点默认关闭原生音频。", { exact: true })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /执行规则 ·/ })).toHaveClass("bg-white");
    expect(screen.getByRole("button", { name: "手动添加" })).toBeInTheDocument();
  });

  it("keeps the memory drawer inside a narrow viewport and wraps long rules", async () => {
    const longRule = `${"超长规则 ".repeat(80)}LONG_TOKEN_WITHOUT_SPACES`;
    listAgentMemories.mockResolvedValue([memory({ content: longRule })]);

    render(<AgentMemoryDialog open onOpenChange={vi.fn()} />);

    const dialog = await screen.findByRole("dialog");
    expect(dialog.className).toContain("w-[calc(100vw-2rem)]");
    const content = await screen.findByText(longRule);
    expect(content.className).toContain("break-words");
    expect(content.className).toContain("[overflow-wrap:anywhere]");
  });

  it("does not present project facts or profile files as verified experience", async () => {
    listAgentMemories.mockResolvedValue([
      memory({ id: 4, kind: "project_fact", content: "项目内的角色事实" }),
      memory({ id: 5, kind: "user_profile", content: "画像文件内容" }),
    ]);

    render(<AgentMemoryDialog open onOpenChange={vi.fn()} />);

    expect(await screen.findByRole("button", { name: "已验证经验 · 0" })).toBeInTheDocument();
  });

  it("shows approved and rejected execution episodes as practice cases", async () => {
    listAgentMemories.mockResolvedValue([
      memory({
        id: 6,
        scope_kind: "project",
        kind: "episodic_example",
        content: "任务情景：创建文字节点\n结果：用户明确采用。",
      }),
      memory({
        id: 7,
        scope_kind: "project",
        kind: "failure_episode",
        content: "失败情景：视频节点参数不匹配",
      }),
    ]);

    render(<AgentMemoryDialog open onOpenChange={vi.fn()} />);

    const tab = await screen.findByRole("button", { name: "实践案例 · 2" });
    fireEvent.click(tab);
    expect(await screen.findByText(/任务情景：创建文字节点/)).toBeInTheDocument();
    expect(screen.getByText(/失败情景：视频节点参数不匹配/)).toBeInTheDocument();
    expect(screen.getByText("· 用户明确采用")).toBeInTheDocument();
    expect(screen.getByText("· 已进入失败复盘")).toBeInTheDocument();
  });
});
