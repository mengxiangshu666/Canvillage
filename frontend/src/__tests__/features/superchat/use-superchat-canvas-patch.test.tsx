// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  CANVAS_AGENT_COMMAND_EVENT,
  CANVAS_PATCH_EVENT,
  CANVAS_RECONNECT_EVENT,
  canvasPatchNotificationFromFrame,
} from "@/features/superchat/canvas-patch-events";
import { useSuperChat } from "@/features/superchat/use-superchat";
import type { ServerFrame } from "@/features/superchat/types";
import { emitCanvasCommandReceipt } from "@/features/superchat/canvas-command-receipts";
import { loadCanvasReceiptOutbox } from "@/features/superchat/canvas-command-receipt-outbox";
import { WORKFLOW_RUN_EVENT } from "@/features/superchat/workflow-run-live";
import type { WorkflowRun } from "@/types/workflow-runtime";
import { api } from "@/lib/api";

class MockWebSocket {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSING = 2;
  static readonly CLOSED = 3;
  static instances: MockWebSocket[] = [];

  readyState = MockWebSocket.CONNECTING;
  onopen: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  onclose: ((event: CloseEvent) => void) | null = null;
  send = vi.fn();

  constructor(readonly url: string) {
    MockWebSocket.instances.push(this);
  }

  open(): void {
    this.readyState = MockWebSocket.OPEN;
    this.onopen?.(new Event("open"));
  }

  message(frame: ServerFrame): void {
    this.onmessage?.({ data: JSON.stringify(frame) } as MessageEvent);
  }

  drop(code = 1006): void {
    this.readyState = MockWebSocket.CLOSED;
    this.onclose?.({ code } as CloseEvent);
  }

  close = vi.fn((): void => {
    this.readyState = MockWebSocket.CLOSED;
  });
}

describe("SuperChat canvas.patch transport", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    MockWebSocket.instances = [];
    vi.stubGlobal("WebSocket", MockWebSocket);
    localStorage.clear();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("validates the structured server frame contract", () => {
    expect(
      canvasPatchNotificationFromFrame({
        type: "canvas.patch",
        project_id: " project-a ",
        canvas_id: " default ",
        revision: 8,
        command_id: " cmd-1 ",
        turn_id: " turn-1 ",
      }),
    ).toEqual({
      projectId: "project-a",
      canvasId: "default",
      revision: 8,
      commandId: "cmd-1",
      turnId: "turn-1",
    });
    expect(
      canvasPatchNotificationFromFrame({
        type: "canvas.patch",
        project_id: "project-a",
        canvas_id: "default",
        revision: 0,
      }),
    ).toBeNull();
    expect(
      canvasPatchNotificationFromFrame({
        type: "canvas.patch",
        project_id: "",
        canvas_id: "default",
        revision: 8,
      }),
    ).toBeNull();
  });

  it("never dispatches authoritative canvas.patch commands into the local executor", () => {
    const commands: unknown[] = [];
    const onCommand = (event: Event) => commands.push((event as CustomEvent).detail);
    window.addEventListener(CANVAS_AGENT_COMMAND_EVENT, onCommand);
    const patches: unknown[] = [];
    const onPatch = (event: Event) => patches.push((event as CustomEvent).detail);
    window.addEventListener(CANVAS_PATCH_EVENT, onPatch);
    const hook = renderHook(() => useSuperChat({ project: "project-a", displayName: "tester" }));
    act(() => vi.advanceTimersByTime(51));
    const socket = MockWebSocket.instances[0];
    act(() => {
      socket.open();
      socket.message({
        type: "scope.changed",
        scope: { kind: "project", id: "project-a" },
        history: [],
      });
      socket.message({
        type: "canvas.patch",
        project_id: "project-a",
        canvas_id: "default",
        revision: 8,
        command_id: "cmd-compat",
        commands: [{ type: "create_image_prompt_node", prompt: "draft" }],
        server_applied: false,
      });
    });
    expect(commands).toEqual([]);
    expect(patches).toEqual([expect.objectContaining({
      commandId: "cmd-compat",
      revision: 8,
      serverApplied: false,
    })]);
    hook.unmount();
    window.removeEventListener(CANVAS_PATCH_EVENT, onPatch);
    window.removeEventListener(CANVAS_AGENT_COMMAND_EVENT, onCommand);
  });

  it("keeps the same socket when UI-only Agent settings change", () => {
    const hook = renderHook(() =>
      useSuperChat({ project: "project-stable-ui", displayName: "tester" }),
    );
    act(() => vi.advanceTimersByTime(51));
    const socket = MockWebSocket.instances[0];
    act(() => {
      socket.open();
      socket.message({
        type: "scope.changed",
        scope: { kind: "project", id: "project-stable-ui" },
        history: [],
      });
      hook.result.current.setSettings({ showToolEvents: true });
    });
    act(() => vi.advanceTimersByTime(100));

    expect(MockWebSocket.instances).toHaveLength(1);
    expect(socket.close).not.toHaveBeenCalled();
    expect(socket.readyState).toBe(MockWebSocket.OPEN);
    hook.unmount();
  });

  it("cancels the active turn without destroying the chat transport", () => {
    const cancelJson = vi.fn().mockResolvedValue({
      ok: true,
      data: { cancelled: true, runtime_cancelled: 1 },
    });
    const cancel = vi.spyOn(api, "post").mockReturnValue({ json: cancelJson } as never);
    const hook = renderHook(() =>
      useSuperChat({ project: "project-cancel", displayName: "tester" }),
    );
    act(() => vi.advanceTimersByTime(51));
    const socket = MockWebSocket.instances[0];
    act(() => {
      socket.open();
      socket.message({
        type: "scope.changed",
        scope: { kind: "project", id: "project-cancel" },
        history: [],
      });
    });
    act(() => {
      expect(hook.result.current.send("开始执行")).toBe(true);
      hook.result.current.abort();
    });

    expect(cancel).toHaveBeenCalledWith(expect.stringContaining("api/v1/chat/cancel"));
    expect(cancel).toHaveBeenCalledWith(expect.stringContaining("canvas_id=default"));
    expect(socket.close).not.toHaveBeenCalled();
    expect(socket.readyState).toBe(MockWebSocket.OPEN);
    hook.unmount();
  });

  it("never resumes a recoverable worker loss after the user cancels the turn", () => {
    const cancelJson = vi.fn().mockResolvedValue({
      ok: true,
      data: { cancelled: true, runtime_cancelled: 1 },
    });
    vi.spyOn(api, "post").mockReturnValue({ json: cancelJson } as never);
    const hook = renderHook(() =>
      useSuperChat({ project: "project-cancel-recovery", displayName: "tester" }),
    );
    act(() => vi.advanceTimersByTime(51));
    const socket = MockWebSocket.instances[0];
    act(() => {
      socket.open();
      socket.message({
        type: "scope.changed",
        scope: { kind: "project", id: "project-cancel-recovery" },
        history: [],
      });
    });
    act(() => {
      expect(hook.result.current.send("开始执行")).toBe(true);
    });
    const sentMessage = socket.send.mock.calls
      .map(([value]) => JSON.parse(String(value)) as Record<string, unknown>)
      .find((frame) => frame.type === "chat.message");
    const turnId = String(sentMessage?.turn_id);

    act(() => hook.result.current.abort());
    act(() => {
      socket.message({
        type: "chat.recoverable",
        turn_id: turnId,
        recovery: {
          schema: "village_canvas.chat_recovery.v1",
          recovery_id: "cancelled-recovery",
          retry_reason: "worker_lost",
          auto_retry_allowed: true,
        },
      });
    });

    const resumeFrames = socket.send.mock.calls
      .map(([value]) => JSON.parse(String(value)) as Record<string, unknown>)
      .filter((frame) => frame.type === "chat.resume");
    expect(resumeFrames).toEqual([]);
    expect(hook.result.current.busy).toBe(false);
    expect(hook.result.current.recovery).toBeNull();
    hook.unmount();
  });

  it("emits authoritative patch notices and requests a reconcile after reconnect", () => {
    const patches: unknown[] = [];
    const reconnects: unknown[] = [];
    const onPatch = (event: Event) => patches.push((event as CustomEvent).detail);
    const onReconnect = (event: Event) => reconnects.push((event as CustomEvent).detail);
    window.addEventListener(CANVAS_PATCH_EVENT, onPatch);
    window.addEventListener(CANVAS_RECONNECT_EVENT, onReconnect);

    const hook = renderHook(() =>
      useSuperChat({ project: "project-a", displayName: "tester" }),
    );
    act(() => vi.advanceTimersByTime(51));
    const first = MockWebSocket.instances[0];
    act(() => {
      first.open();
      first.message({
        type: "scope.changed",
        scope: { kind: "project", id: "project-a" },
        history: [],
      });
      first.message({
        type: "canvas.patch",
        project_id: "project-a",
        canvas_id: "default",
        revision: 8,
        command_id: "cmd-1",
      });
    });

    expect(patches).toEqual([
      {
        projectId: "project-a",
        canvasId: "default",
        revision: 8,
        commandId: "cmd-1",
      },
    ]);
    expect(hook.result.current.canvasTelemetry).toMatchObject({
      patchCount: 1,
      commandCount: 1,
      revision: 8,
      lastCommandId: "cmd-1",
    });

    act(() => first.drop());
    act(() => vi.advanceTimersByTime(1_201));
    const second = MockWebSocket.instances[1];
    act(() => {
      second.open();
      second.message({
        type: "scope.changed",
        scope: { kind: "project", id: "project-a" },
        history: [],
      });
    });
    expect(reconnects).toEqual([{ projectId: "project-a" }]);

    hook.unmount();
    window.removeEventListener(CANVAS_PATCH_EVENT, onPatch);
    window.removeEventListener(CANVAS_RECONNECT_EVENT, onReconnect);
  });

  it("dispatches only a workflow.run matching the active project and canvas", () => {
    const runs: WorkflowRun[] = [];
    const onRun = (event: Event) => runs.push((event as CustomEvent<WorkflowRun>).detail);
    window.addEventListener(WORKFLOW_RUN_EVENT, onRun);
    const hook = renderHook(() => useSuperChat({
      project: "project-a",
      canvasId: "canvas-a",
      displayName: "tester",
    }));
    act(() => vi.advanceTimersByTime(51));
    const socket = MockWebSocket.instances[0];
    const baseRun: WorkflowRun = {
      id: "run-1",
      workflow_id: "one-click-film",
      workflow_version: 1,
      project_id: "project-a",
      canvas_id: "canvas-a",
      run_mode: "draft",
      status: "running",
      current_frontier: ["understand"],
      step_states: {},
      inputs: {},
      artifacts: {},
      error: "",
      revision: 0,
      event_seq: 0,
      idempotency_key: "start-1",
      created_at: "2026-08-16T00:00:00Z",
      updated_at: "2026-08-16T00:00:00Z",
    };
    act(() => {
      socket.open();
      socket.message({
        type: "scope.changed",
        scope: { kind: "project", id: "project-a", canvas_id: "canvas-a" },
        history: [],
      });
      socket.message({ type: "workflow.run", run: baseRun });
      socket.message({
        type: "workflow.run",
        run: { ...baseRun, id: "run-cross", canvas_id: "canvas-b" },
      });
    });

    expect(runs).toEqual([baseRun]);
    hook.unmount();
    window.removeEventListener(WORKFLOW_RUN_EVENT, onRun);
  });

  it("retries a failed canvas receipt after reconnect and clears the durable outbox", async () => {
    let attempts = 0;
    const receiptScopeKey = "village-canvas:project:project-receipt:main";
    vi.spyOn(api, "post").mockImplementation(() => ({
      json: async () => {
        attempts += 1;
        if (attempts === 1) throw new Error("offline");
        return { ok: true };
      },
    }) as never);
    const hook = renderHook(() =>
      useSuperChat({ project: "project-receipt", displayName: "tester" }),
    );
    act(() => vi.advanceTimersByTime(51));
    const first = MockWebSocket.instances[0];
    act(() => {
      first.open();
      first.message({
        type: "scope.changed",
        scope: { kind: "project", id: "project-receipt" },
        history: [],
      });
      first.message({
        type: "chat.progress",
        turn_id: "turn-receipt",
        stage: "running",
        message: "working",
      });
      emitCanvasCommandReceipt({
        commandId: "command-receipt",
        projectId: "project-receipt",
        canvasId: "default",
        turnId: "turn-receipt",
        stage: "result",
        attempt: 1,
        success: true,
      });
    });
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    await vi.waitFor(() => expect(attempts).toBe(1));
    expect(loadCanvasReceiptOutbox(receiptScopeKey)).toHaveLength(1);

    act(() => first.drop());
    act(() => vi.advanceTimersByTime(1_201));
    const second = MockWebSocket.instances[1];
    act(() => {
      second.open();
      second.message({
        type: "scope.changed",
        scope: { kind: "project", id: "project-receipt" },
        history: [],
      });
    });
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    await vi.waitFor(() => expect(attempts).toBe(2));
    expect(loadCanvasReceiptOutbox(receiptScopeKey)).toEqual([]);
    hook.unmount();
  });

  it("retries a failed canvas receipt while the socket stays online", async () => {
    let attempts = 0;
    const receiptScopeKey = "village-canvas:project:project-online-retry:main";
    vi.spyOn(api, "post").mockImplementation(() => ({
      json: async () => {
        attempts += 1;
        if (attempts === 1) throw new Error("temporary http failure");
        return { ok: true };
      },
    }) as never);
    const hook = renderHook(() =>
      useSuperChat({ project: "project-online-retry", displayName: "tester" }),
    );
    act(() => vi.advanceTimersByTime(51));
    const socket = MockWebSocket.instances[0];
    act(() => {
      socket.open();
      socket.message({
        type: "scope.changed",
        scope: { kind: "project", id: "project-online-retry" },
        history: [],
      });
      socket.message({
        type: "chat.progress",
        turn_id: "turn-online-retry",
        stage: "running",
        message: "working",
      });
      emitCanvasCommandReceipt({
        commandId: "command-online-retry",
        projectId: "project-online-retry",
        canvasId: "default",
        turnId: "turn-online-retry",
        stage: "result",
        attempt: 1,
        success: true,
      });
    });
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(attempts).toBe(1);
    expect(loadCanvasReceiptOutbox(receiptScopeKey)).toHaveLength(1);

    await act(async () => {
      vi.advanceTimersByTime(500);
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(attempts).toBe(2);
    expect(loadCanvasReceiptOutbox(receiptScopeKey)).toEqual([]);
    expect(socket.readyState).toBe(MockWebSocket.OPEN);
    hook.unmount();
  });

  it("keeps each receipt backoff independent when another receipt succeeds", async () => {
    const attempts = new Map<string, number>();
    vi.spyOn(api, "post").mockImplementation((_url, options) => ({
      json: async () => {
        const payload = (options as { json: { event: { receiptId: string } } }).json;
        const receiptId = payload.event.receiptId;
        attempts.set(receiptId, (attempts.get(receiptId) ?? 0) + 1);
        if (receiptId.startsWith("command-failing:")) throw new Error("still offline");
        return { ok: true };
      },
    }) as never);
    const hook = renderHook(() =>
      useSuperChat({ project: "project-independent-retry", displayName: "tester" }),
    );
    act(() => vi.advanceTimersByTime(51));
    const socket = MockWebSocket.instances[0];
    act(() => {
      socket.open();
      socket.message({
        type: "scope.changed",
        scope: { kind: "project", id: "project-independent-retry" },
        history: [],
      });
      socket.message({
        type: "chat.progress",
        turn_id: "turn-independent-retry",
        stage: "running",
        message: "working",
      });
      emitCanvasCommandReceipt({
        commandId: "command-failing",
        projectId: "project-independent-retry",
        canvasId: "default",
        turnId: "turn-independent-retry",
        stage: "result",
        attempt: 1,
        success: true,
      });
    });
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(attempts.get("command-failing:result:1:success")).toBe(1);

    act(() => vi.advanceTimersByTime(100));
    act(() => emitCanvasCommandReceipt({
      commandId: "command-success",
      projectId: "project-independent-retry",
      canvasId: "default",
      turnId: "turn-independent-retry",
      stage: "result",
      attempt: 1,
      success: true,
    }));
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(attempts.get("command-success:result:1:success")).toBe(1);
    expect(attempts.get("command-failing:result:1:success")).toBe(1);

    await act(async () => {
      vi.advanceTimersByTime(400);
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(attempts.get("command-failing:result:1:success")).toBe(2);
    hook.unmount();
  });

  it("restores typed runtime events so the Agent trail survives reconnect", () => {
    const hook = renderHook(() =>
      useSuperChat({ project: "project-a", displayName: "tester" }),
    );
    act(() => vi.advanceTimersByTime(51));
    const socket = MockWebSocket.instances[0];
    act(() => {
      socket.open();
      socket.message({
        type: "scope.changed",
        scope: { kind: "project", id: "project-a" },
        history: [],
        agent_runtime: {
          session_id: "session-a",
          turn_id: "turn-a",
          canvas_id: "default",
          status: "completed",
          seq: 2,
          last_event: "done",
        },
        agent_runtime_events: [
          {
            session_id: "session-a",
            turn_id: "turn-a",
            event_id: "event-1",
            event_type: "tool:freezone_emit_canvas_command",
            status: "running",
            seq: 1,
            created_at: 1,
          },
          {
            session_id: "session-a",
            turn_id: "turn-a",
            event_id: "event-2",
            event_type: "done",
            status: "completed",
            seq: 2,
            created_at: 2,
          },
        ],
      });
    });

    expect(hook.result.current.runtimeEvents.map((event) => event.event_type)).toEqual([
      "tool:freezone_emit_canvas_command",
      "done",
    ]);
    hook.unmount();
  });

  it("keeps long tools active from progress heartbeats and automatically resumes once", () => {
    const hook = renderHook(() =>
      useSuperChat({ project: "project-a", displayName: "tester" }),
    );
    act(() => vi.advanceTimersByTime(51));
    const socket = MockWebSocket.instances[0];
    act(() => {
      socket.open();
      socket.message({
        type: "scope.changed",
        scope: { kind: "project", id: "project-a" },
        history: [],
      });
      socket.message({
        type: "chat.progress",
        turn_id: "turn-long",
        stage: "tool.long_running",
        message: "工具 freezone_emit_canvas_command 仍在运行…",
        tool_name: "freezone_emit_canvas_command",
        elapsed_seconds: 601,
        worker_alive: true,
        heartbeat: true,
        last_progress_age_seconds: 300,
        last_event: "session/update",
      });
      socket.message({
        type: "assistant.delta",
        turn_id: "turn-long",
        text: "这是失败执行留下的临时回复",
        accumulated: true,
      });
    });
    act(() => vi.advanceTimersByTime(32));

    expect(hook.result.current.busy).toBe(true);
    expect(hook.result.current.messages).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          role: "assistant",
          turnId: "turn-long",
          text: "这是失败执行留下的临时回复",
        }),
      ]),
    );
    expect(hook.result.current.progress).toMatchObject({
      stage: "tool.long_running",
      workerAlive: true,
      heartbeat: true,
      toolName: "freezone_emit_canvas_command",
    });

    act(() => {
      socket.message({
        type: "chat.recoverable",
        turn_id: "turn-long",
        scope: { kind: "project", id: "project-a" },
        message: "已保存恢复点",
        recovery: {
          schema: "village_canvas.chat_recovery.v1",
          recovery_id: "recovery-1",
          retry_reason: "worker_lost",
          auto_retry_allowed: true,
          recovery_attempt: 0,
          pending_tool: "freezone_emit_canvas_command",
          canvas: { project_id: "project-a", canvas_id: "default", revision: 12 },
        },
      });
    });

    const resumeFrames = socket.send.mock.calls
      .map(([value]) => JSON.parse(String(value)) as Record<string, unknown>)
      .filter((frame) => frame.type === "chat.resume");
    expect(resumeFrames).toHaveLength(1);
    expect(resumeFrames[0]).toMatchObject({
      type: "chat.resume",
      recovery_id: "recovery-1",
    });
    expect(hook.result.current.messages.some(
      (message) => message.role === "assistant" && message.turnId === "turn-long",
    )).toBe(false);
    expect(hook.result.current.recovery).toMatchObject({ autoRetrying: true });

    const recoveryTurnId = String(resumeFrames[0].turn_id);
    act(() => {
      socket.message({
        type: "assistant.delta",
        turn_id: recoveryTurnId,
        text: "恢复后只保留这一条",
        accumulated: true,
      });
    });
    act(() => vi.advanceTimersByTime(32));
    act(() => {
      socket.message({
        type: "assistant.message",
        turn_id: recoveryTurnId,
        message: {
          id: "assistant-recovered",
          role: "assistant",
          content: "恢复后只保留这一条",
          turn_id: recoveryTurnId,
          created_at: new Date().toISOString(),
        },
      });
      socket.message({ type: "chat.done", turn_id: recoveryTurnId });
    });
    expect(hook.result.current.messages.filter(
      (message) => message.role === "assistant",
    )).toEqual([
      expect.objectContaining({
        id: "assistant-recovered",
        turnId: recoveryTurnId,
        text: "恢复后只保留这一条",
      }),
    ]);

    // Replaying the same recoverable frame cannot trigger a second automatic resume.
    act(() => {
      socket.message({
        type: "chat.recoverable",
        turn_id: "turn-long",
        recovery: {
          schema: "village_canvas.chat_recovery.v1",
          recovery_id: "recovery-1",
          retry_reason: "worker_lost",
          auto_retry_allowed: true,
        },
      });
    });
    expect(socket.send.mock.calls
      .map(([value]) => JSON.parse(String(value)) as Record<string, unknown>)
      .filter((frame) => frame.type === "chat.resume")).toHaveLength(1);

    hook.unmount();
  });

  it("rediscovers a durable recovery card after a scope reconnect", () => {
    const hook = renderHook(() =>
      useSuperChat({ project: "project-a", displayName: "tester" }),
    );
    act(() => vi.advanceTimersByTime(51));
    const socket = MockWebSocket.instances[0];
    act(() => {
      socket.open();
      socket.message({
        type: "scope.changed",
        scope: { kind: "project", id: "project-a" },
        history: [],
        recoveries: [{
          message: "上次任务已保存恢复点",
          recovery: {
            schema: "village_canvas.chat_recovery.v2",
            recovery_id: "durable-recovery",
            checkpoint_turn_id: "turn-original",
            retry_reason: "worker_lost",
          },
        }],
      });
    });

    expect(hook.result.current.recovery).toMatchObject({
      message: "上次任务已保存恢复点",
      packet: {
        recovery_id: "durable-recovery",
        checkpoint_turn_id: "turn-original",
      },
      autoRetrying: false,
    });
    expect(socket.send).not.toHaveBeenCalledWith(expect.stringContaining("chat.resume"));
    hook.unmount();
  });

  it("replaces an optimistic success draft with the verified server message", () => {
    const hook = renderHook(() =>
      useSuperChat({ project: "project-a", displayName: "tester" }),
    );
    act(() => vi.advanceTimersByTime(51));
    const socket = MockWebSocket.instances[0];
    act(() => {
      socket.open();
      socket.message({
        type: "scope.changed",
        scope: { kind: "project", id: "project-a" },
        history: [],
      });
      socket.message({
        type: "assistant.delta",
        turn_id: "turn-truth",
        text: "第一集已经全部完成并通过验收。",
        accumulated: true,
      });
    });
    act(() => vi.advanceTimersByTime(32));
    act(() => {
      socket.message({
        type: "assistant.message",
        turn_id: "turn-truth",
        message: {
          id: "assistant-truth",
          role: "assistant",
          content: "任务尚未完成：WorkflowRun 质量 gate 未通过。",
          turn_id: "turn-truth",
          created_at: new Date().toISOString(),
        },
      });
      socket.message({ type: "chat.done", turn_id: "turn-truth" });
    });

    expect(hook.result.current.messages.filter(
      (message) => message.role === "assistant" && message.turnId === "turn-truth",
    )).toEqual([
      expect.objectContaining({
        id: "assistant-truth",
        text: "任务尚未完成：WorkflowRun 质量 gate 未通过。",
      }),
    ]);
    hook.unmount();
  });

  it("keeps the completed director workflow receipt visible after chat.done", () => {
    const hook = renderHook(() =>
      useSuperChat({ project: "project-a", displayName: "tester" }),
    );
    act(() => vi.advanceTimersByTime(51));
    const socket = MockWebSocket.instances[0];
    act(() => {
      socket.open();
      socket.message({
        type: "scope.changed",
        scope: { kind: "project", id: "project-a" },
        history: [],
      });
      socket.message({
        type: "chat.progress",
        turn_id: "turn-workflow",
        stage: "workflow.completed",
        message: "回执核验完成。",
        workflow: {
          schema: "village_canvas.workflow.v1",
          status: "completed",
          steps: [{ id: "verify", label: "核对工具回执与下一步", status: "done" }],
        },
      });
      socket.message({ type: "chat.done", turn_id: "turn-workflow" });
    });

    expect(hook.result.current.busy).toBe(false);
    expect(hook.result.current.progress?.workflow).toMatchObject({ status: "completed" });
    hook.unmount();
  });

  it("exposes one-click recovery after the automatic attempt is exhausted", () => {
    const hook = renderHook(() =>
      useSuperChat({ project: "project-a", displayName: "tester" }),
    );
    act(() => vi.advanceTimersByTime(51));
    const socket = MockWebSocket.instances[0];
    act(() => {
      socket.open();
      socket.message({
        type: "scope.changed",
        scope: { kind: "project", id: "project-a" },
        history: [],
      });
      socket.message({
        type: "chat.recoverable",
        turn_id: "turn-failed-again",
        recovery: {
          schema: "village_canvas.chat_recovery.v1",
          recovery_id: "recovery-manual",
          retry_reason: "worker_lost",
          auto_retry_allowed: false,
          recovery_attempt: 1,
        },
      });
    });

    expect(hook.result.current.busy).toBe(false);
    expect(hook.result.current.recovery?.autoRetrying).toBe(false);
    act(() => {
      expect(hook.result.current.resumeRecovery()).toBe(true);
    });
    expect(socket.send.mock.calls
      .map(([value]) => JSON.parse(String(value)) as Record<string, unknown>)
      .some((frame) => frame.type === "chat.resume" && frame.recovery_id === "recovery-manual"))
      .toBe(true);

    hook.unmount();
  });

  it("clears a consumed recovery banner after a non-recoverable resume error", () => {
    const hook = renderHook(() =>
      useSuperChat({ project: "project-a", displayName: "tester" }),
    );
    act(() => vi.advanceTimersByTime(51));
    const socket = MockWebSocket.instances[0];
    act(() => {
      socket.open();
      socket.message({
        type: "scope.changed",
        scope: { kind: "project", id: "project-a" },
        history: [],
      });
    });
    act(() => {
      socket.message({
        type: "chat.recoverable",
        turn_id: "turn-recovery",
        recovery: {
          schema: "village_canvas.chat_recovery.v1",
          recovery_id: "recovery-consumed",
          retry_reason: "worker_lost",
          auto_retry_allowed: false,
        },
      });
    });
    expect(hook.result.current.recovery).not.toBeNull();

    act(() => {
      expect(hook.result.current.resumeRecovery()).toBe(true);
      socket.message({
        type: "error",
        turn_id: "turn-recovery-resume",
        message: "模型返回不可恢复错误",
      });
    });

    expect(hook.result.current.recovery).toBeNull();
    expect(hook.result.current.busy).toBe(false);
    hook.unmount();
  });
});
