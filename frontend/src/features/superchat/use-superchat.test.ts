// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from "vitest";

import {
  advanceAgentRuntimeCursor,
  optimisticCanvasEnvelopeFromPatch,
  canvasPatchAfterOptimisticConfirmation,
  recoveryPacketKey,
  shouldIgnoreRecoveryPacket,
  chatBusyNotice,
  clearConversationLocalState,
  dedupeChatMessages,
  filterStaleDirectorClarificationMessages,
  isUsableAgentModel,
  modelsWithPersistedSelection,
  shouldKeepActiveTurnAfterScopeSnapshot,
  scopeMatches,
  upsertAssistantMessage,
} from "./use-superchat";
import type { AgentRuntimeSnapshot, ChatMessage, ModelEntry } from "./types";
import type { ClientFrame } from "./types";

function message(role: ChatMessage["role"], turnId: string, text: string): ChatMessage {
  return {
    id: `${role}-${turnId}`,
    role,
    text,
    turnId,
    timestamp: role === "user" ? 1 : 2,
  };
}

describe("useSuperChat active turn reconciliation", () => {
  it("keeps only the current director clarification in cached history", () => {
    const questions: ChatMessage[] = ["用途？", "风格？", "画幅？", "声音？"].map((text, index) => ({
      ...message("assistant", `director-${index}`, text),
      raw: { metadata: { backend: "director-preflight" } },
    }));

    expect(filterStaleDirectorClarificationMessages(questions).map((item) => item.text)).toEqual(["声音？"]);
  });

  it("removes cached clarification steps after a real assistant result", () => {
    const stale: ChatMessage = {
      ...message("assistant", "director-1", "用途？"),
      raw: { metadata: { backend: "director-preflight" } },
    };
    const completed = message("assistant", "done-1", "已完成");

    expect(filterStaleDirectorClarificationMessages([stale, completed]).map((item) => item.text)).toEqual(["已完成"]);
  });

  it("removes legacy clarification text when cached metadata is absent", () => {
    const stale = [
      "这支片主要给谁看、发布在哪里，还是只做内部样片？",
      "你要什么视觉风格和情绪基调？",
      "成片准备使用什么画幅和平台规格？",
      "声音怎么处理：对白、旁白、音乐和字幕分别要不要？",
    ].map((text, index) => message("assistant", `legacy-${index}`, text));

    expect(filterStaleDirectorClarificationMessages(stale).map((item) => item.text)).toEqual([
      "声音怎么处理：对白、旁白、音乐和字幕分别要不要？",
    ]);
    expect(filterStaleDirectorClarificationMessages([...stale, message("assistant", "done-2", "已完成")]).map((item) => item.text)).toEqual(["已完成"]);
  });

  it("retains a missing persisted Agent model as a disabled stale option", () => {
    const live: ModelEntry = { id: "live", label: "Live", enabled: true };
    const models = modelsWithPersistedSelection([live], "removed-model");

    expect(models[0]).toMatchObject({
      id: "removed-model",
      stale: true,
      disabled: true,
    });
    expect(models[1]).toBe(live);
    expect(isUsableAgentModel(models[0])).toBe(false);
    expect(isUsableAgentModel(live)).toBe(true);
  });

  it("does not add a stale duplicate when the persisted Agent model is live", () => {
    const live: ModelEntry = { id: "live", label: "Live" };
    expect(modelsWithPersistedSelection([live], "live")).toEqual([live]);
  });

  it("clears only the deleted conversation local runtime state", () => {
    const scopeKey = "village-canvas:project:project-a:canvas:canvas-a:conversation:conversation-a";
    const keys = [
      `superchat:messages:v2:${scopeKey}`,
      `superchat:active-turn:${scopeKey}`,
      `superchat:agent-model:${scopeKey}`,
      `superchat:pinned:${scopeKey}`,
      `superchat:deleted:${scopeKey}`,
      `superchat:canvas-command-receipts:v1:${scopeKey}`,
    ];
    keys.forEach((key) => localStorage.setItem(key, "stored"));
    localStorage.setItem("unrelated", "keep");

    clearConversationLocalState("project-a", "canvas-a", "conversation-a");

    expect(keys.map((key) => localStorage.getItem(key))).toEqual(keys.map(() => null));
    expect(localStorage.getItem("unrelated")).toBe("keep");
  });

  it("keeps the canvas Agent on the configured direct model", () => {
    const villageFrame: ClientFrame = {
      type: "chat.message",
      text: "优化提示词",
      agent_engine: "village",
      model: "deepseek-v4-flash",
      research_enabled: true,
    };
    expect(villageFrame.model).toBe("deepseek-v4-flash");
    expect(villageFrame.research_enabled).toBe(true);
    expect(villageFrame.agent_engine).toBe("village");
  });

  it("rejects late scope snapshots from another conversation", () => {
    const active = {
      kind: "project" as const,
      id: "project-a",
      canvas_id: "canvas-a",
      conversation_id: "conversation-b",
    };

    expect(scopeMatches({ ...active }, active)).toBe(true);
    expect(scopeMatches({ ...active, conversation_id: "conversation-a" }, active)).toBe(false);
    expect(scopeMatches({ ...active, conversation_id: undefined }, active)).toBe(false);
    expect(scopeMatches(
      { ...active, conversation_id: undefined },
      { ...active, conversation_id: undefined },
    )).toBe(true);
  });
  it("surfaces a rejected concurrent turn instead of leaving it spinning", () => {
    expect(chatBusyNotice(undefined)).toContain("没有启动");
    expect(chatBusyNotice("当前用户已有 AI 对话正在处理中")).toBe(
      "当前用户已有 AI 对话正在处理中",
    );
  });

  it("releases stale optimistic turns when the server scope is no longer busy", () => {
    const pending = [message("user", "turn-stale", "搭一个节点")];

    expect(shouldKeepActiveTurnAfterScopeSnapshot(false, "turn-stale", pending)).toBe(false);
  });

  it("keeps a pending local turn only while the server still reports busy", () => {
    const pending = [message("user", "turn-live", "搭一个节点")];
    const completed = [...pending, message("assistant", "turn-live", "已完成")];

    expect(shouldKeepActiveTurnAfterScopeSnapshot(true, "turn-live", pending)).toBe(true);
    expect(shouldKeepActiveTurnAfterScopeSnapshot(true, "turn-live", completed)).toBe(false);
  });

  it("accepts new runtime events and drops duplicate or older sequence numbers", () => {
    const runtime = (seq: number): AgentRuntimeSnapshot => ({
      session_id: "session-a",
      turn_id: "turn-a",
      canvas_id: "canvas-a",
      status: "running",
      seq,
    });

    const first = advanceAgentRuntimeCursor(null, runtime(4));
    const duplicate = advanceAgentRuntimeCursor(first.cursor, runtime(4));
    const stale = advanceAgentRuntimeCursor(first.cursor, runtime(3));
    const next = advanceAgentRuntimeCursor(first.cursor, runtime(5));
    const rotated = advanceAgentRuntimeCursor(next.cursor, {
      ...runtime(1),
      session_id: "session-b",
    });

    expect(first.accepted).toBe(true);
    expect(duplicate.accepted).toBe(false);
    expect(stale.accepted).toBe(false);
    expect(next.accepted).toBe(true);
    expect(rotated.accepted).toBe(true);
  });

  it("claims one recovery id and ignores duplicate recoverable frames", () => {
    const consumed = new Set<string>();
    const packet = { recovery_id: "recovery-1" };
    const key = recoveryPacketKey("project:demo", packet.recovery_id);

    expect(shouldIgnoreRecoveryPacket("project:demo", packet, consumed, null)).toBe(false);
    consumed.add(key);
    expect(shouldIgnoreRecoveryPacket("project:demo", packet, consumed, null)).toBe(true);
    expect(shouldIgnoreRecoveryPacket("project:demo", packet, new Set(), key)).toBe(true);
    expect(shouldIgnoreRecoveryPacket("project:other", packet, consumed, null)).toBe(false);
  });

  it("keeps authoritative UI reconciliation after a safe optimistic command is confirmed", () => {
    const patch = {
      projectId: "project-a",
      canvasId: "canvas-a",
      commandId: "command-a",
      revision: 12,
      serverApplied: true,
      snapshotRequired: false,
      uiReconcileRequired: true,
    };

    expect(canvasPatchAfterOptimisticConfirmation(patch, true)).toMatchObject({
      revision: 12,
      snapshotRequired: false,
      uiReconcileRequired: true,
    });
    expect(canvasPatchAfterOptimisticConfirmation(patch, false)).toBe(patch);
  });

  it("rebuilds a deterministic create preview from an authoritative canvas patch", () => {
    const envelope = optimisticCanvasEnvelopeFromPatch({
      projectId: "project-a",
      canvasId: "canvas-a",
      commandId: "command-a",
      turnId: "turn-a",
      revision: 12,
      serverApplied: true,
      commands: [{
        type: "create_canvas_node",
        node_type: "textAnnotationNode",
        text: "实时出现",
        created_node_id: "agent-node-a",
        placement: { anchor: "viewport_center", layout: "grid" },
      }],
    });

    expect(envelope).toMatchObject({
      command_id: "command-a",
      optimistic: true,
      commands: [{ created_node_id: "agent-node-a" }],
    });
  });

  it("keeps the persisted assistant message when chat.done flushes the same turn", () => {
    const persisted: ChatMessage = {
      id: "42",
      role: "assistant",
      text: "记住了：只说结果和下一步。",
      turnId: "turn-a",
      timestamp: 20,
      raw: { id: 42, backend: "hermes" },
    };

    const reconciled = upsertAssistantMessage(
      [message("user", "turn-a", "说人话"), persisted],
      "turn-a",
      persisted.text,
    );
    const assistants = reconciled.filter((entry) => entry.role === "assistant");

    expect(assistants).toHaveLength(1);
    expect(assistants[0]).toMatchObject({ id: "42", turnId: "turn-a" });
    expect(assistants[0].raw).toEqual({ id: 42, backend: "hermes" });
  });

  it("collapses duplicate assistant entries within one turn but preserves equal replies across turns", () => {
    const reconciled = dedupeChatMessages([
      message("assistant", "turn-a", "已完成"),
      { ...message("assistant", "turn-a", "已完成"), id: "server-42", timestamp: 3 },
      message("assistant", "turn-b", "已完成"),
    ]);

    expect(reconciled.filter((entry) => entry.turnId === "turn-a")).toHaveLength(1);
    expect(reconciled.filter((entry) => entry.role === "assistant")).toHaveLength(2);
  });

  it("repairs only immediate legacy duplicates without turn ids", () => {
    const base: ChatMessage = {
      id: "legacy-a",
      role: "assistant",
      text: "继续就说。",
      timestamp: 1_000,
    };

    expect(dedupeChatMessages([
      base,
      { ...base, id: "legacy-b", timestamp: 1_200 },
    ])).toHaveLength(1);
    expect(dedupeChatMessages([
      base,
      { ...base, id: "legacy-later", timestamp: 5_000 },
    ])).toHaveLength(2);
  });
});
