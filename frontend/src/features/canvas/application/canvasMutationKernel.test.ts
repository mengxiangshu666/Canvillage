// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { useCanvasStore, type CanvasEdge, type CanvasNode } from "@/stores/canvasStore";

import {
  applyCanvasMutationPatches,
  CANVAS_MUTATION_METRICS_EVENT,
  CANVAS_MUTATION_OUTBOX_STATUS_EVENT,
  dispatchCanvasMutation,
  diffCanvasMutationGraph,
  flushObservedCanvasMutation,
  getCanvasMutationOutboxStatus,
  pendingCanvasMutationCount,
  recordObservedCanvasMutation,
  recoverCanvasMutationScope,
  resetCanvasMutationKernelForTests,
  runCanvasMutation,
  setCanvasMutationOutboxForTests,
  waitForCanvasMutationPersistence,
} from "./canvasMutationKernel";
import {
  createMemoryCanvasMutationOutbox,
  createResilientCanvasMutationOutbox,
  type CanvasMutationOutbox,
} from "./canvasMutationOutbox";
import type {
  CanvasMutationGraph,
  CanvasMutationOperationType,
  CanvasMutationTransaction,
} from "./canvasMutationTypes";

function node(id: string, data: Record<string, unknown>, position = { x: 0, y: 0 }): CanvasNode {
  return {
    id,
    type: "textAnnotationNode",
    position,
    data,
  } as CanvasNode;
}

function edge(id: string, source: string, target: string): CanvasEdge {
  return {
    id,
    source,
    target,
    type: "disconnectableEdge",
  } as CanvasEdge;
}

function graph(nodes: CanvasNode[], edges: CanvasEdge[] = []): CanvasMutationGraph {
  return { nodes, edges };
}

function queuedTransaction(transactionId: string): CanvasMutationTransaction {
  return {
    schema: "canvas_mutation_transaction.v1",
    transactionId,
    commandId: `command-${transactionId}`,
    projectId: "outbox-project",
    canvasId: "outbox-canvas",
    baseRevision: 1,
    source: "human",
    forwardOps: [],
    inverseOps: [],
    affectedNodeIds: [],
    affectedEdgeIds: [],
    touchedFields: {},
    state: "queued",
    createdAt: Date.now(),
    updatedAt: Date.now(),
  };
}

describe("Canvas Mutation Kernel", () => {
  const outbox = createMemoryCanvasMutationOutbox();

  beforeEach(async () => {
    await resetCanvasMutationKernelForTests();
    await setCanvasMutationOutboxForTests(outbox);
    useCanvasStore.getState().clearCanvas();
  });

  afterEach(async () => {
    await resetCanvasMutationKernelForTests();
  });

  it("diffs create, update, move, connect, disconnect, and delete as reversible patches", () => {
    const before = graph([
      node("keep", { content: "old" }),
      node("remove", { content: "remove" }),
    ], [edge("edge-remove", "keep", "remove")]);
    const after = graph([
      node("keep", { content: "new" }, { x: 40, y: 80 }),
      node("create", { content: "created" }),
    ], [edge("edge-create", "keep", "create")]);

    const patches = diffCanvasMutationGraph(before, after);
    expect(new Set(patches.map((patch) => `${patch.entity}:${patch.type}`))).toEqual(new Set([
      "node:update_node",
      "node:create_node",
      "node:delete_node",
      "edge:connect_nodes",
      "edge:remove_edge",
    ]));

    const applied = applyCanvasMutationPatches(before, patches);
    expect(applied.conflicts).toEqual([]);
    expect(applied.graph).toEqual(after);
    expect(applied.changed).toBe(true);

    const inverse = patches.slice().reverse().map((patch) => ({
      ...patch,
      type: (patch.type === "create_node"
        ? "delete_node"
        : patch.type === "delete_node"
          ? "create_node"
          : patch.type === "connect_nodes"
            ? "remove_edge"
            : patch.type) as CanvasMutationOperationType,
      before: patch.after,
      after: patch.before,
      beforeFingerprint: patch.afterFingerprint,
      afterFingerprint: patch.beforeFingerprint,
      changes: patch.changes.slice().reverse().map((change) => ({
        ...change,
        beforeExists: change.afterExists,
        before: change.after,
        afterExists: change.beforeExists,
        after: change.before,
        beforeFingerprint: change.afterFingerprint,
        afterFingerprint: change.beforeFingerprint,
      })),
    }));
    expect(applyCanvasMutationPatches(after, inverse).graph).toEqual(before);
  });

  it("logs an edited script table cell as a real node patch", () => {
    // 脚本节点表格的单元格编辑写回 data.scriptResult.rows[i][k]。它曾经和
    // progress/status 一起被当作「运行态字段」排除，导致内核产出 0 个补丁 ——
    // 用户在表格里改的提示词进不了操作日志，而自动保存走另一条签名路径仍会落盘，
    // 两个子系统对同一字段口径相反。这条用例把「表格内容是可编辑内容」钉住。
    const before = graph([node("script", {
      scriptResult: { title: "旧", rows: [{ shot_prompt: "旧的提示词" }] },
    })]);
    const after = graph([node("script", {
      scriptResult: { title: "旧", rows: [{ shot_prompt: "改过的提示词" }] },
    })]);

    const patches = diffCanvasMutationGraph(before, after);
    expect(patches).toHaveLength(1);
    expect(patches[0].type).toBe("update_node");
    // 变更点落在 `rows` 这个数组边界上（收集器只下钻普通对象，不下钻数组），
    // 已经足够让这次编辑进入操作日志与 outbox。
    expect(patches[0].changes.map((change) => change.path.join("."))).toEqual([
      "data.scriptResult.rows",
    ]);
    expect(applyCanvasMutationPatches(before, patches).graph).toEqual(after);
  });

  it("still drops runtime-only node data fields", () => {
    // 放开 scriptResult 的同时必须钉住另一半：真正的运行态字段（进度 / 状态 /
    // 抽帧结果 / 生成错误）不进操作日志，否则高频进度写会把日志淹掉。
    const before = graph([node("n1", {
      progress: 0.1,
      status: "running",
      frames: [],
      generationError: null,
      scriptResult: { rows: [] },
    })]);
    const after = graph([node("n1", {
      progress: 0.8,
      status: "done",
      frames: ["a.png"],
      generationError: "上游超时",
      scriptResult: { rows: [] },
    })]);
    expect(diffCanvasMutationGraph(before, after)).toEqual([]);

    // URL 承载字段（单数 / 复数 / URI）同口径静默：它们由生成、上传、脚本派生写回，
    // 值是长串而非用户敲的内容。复数必须与单数一致 —— 否则「换参考图」按写哪个字段
    // 得到相反结论（只改 referenceImageUrl 无补丁，带上 referenceImageUrls 就有补丁）。
    const urlBefore = graph([node("n2", {
      referenceImageUrl: "a.png",
      referenceImageUrls: ["a.png"],
      imageUrl: "x.png",
    })]);
    const urlAfter = graph([node("n2", {
      referenceImageUrl: "b.png",
      referenceImageUrls: ["b.png", "c.png"],
      imageUrl: "y.png",
    })]);
    expect(diffCanvasMutationGraph(urlBefore, urlAfter)).toEqual([]);
  });

  it("does not roll back a field changed by the user after the optimistic apply", () => {
    const before = graph([node("n1", { content: "old" })]);
    const optimistic = graph([node("n1", { content: "agent" })]);
    const userEdited = graph([node("n1", { content: "user" })]);
    const [patch] = diffCanvasMutationGraph(before, optimistic);
    const inverse = {
      ...patch,
      type: "update_node" as const,
      before: patch.after,
      after: patch.before,
      beforeFingerprint: patch.afterFingerprint,
      afterFingerprint: patch.beforeFingerprint,
      changes: patch.changes.map((change) => ({
        ...change,
        beforeExists: change.afterExists,
        before: change.after,
        afterExists: change.beforeExists,
        after: change.before,
        beforeFingerprint: change.afterFingerprint,
        afterFingerprint: change.beforeFingerprint,
      })),
    };

    const result = applyCanvasMutationPatches(userEdited, [inverse]);
    expect(result.conflicts).toHaveLength(1);
    expect(result.graph).toEqual(userEdited);
  });

  it("partially rolls back safe fields while preserving a later user edit", () => {
    const before = graph([node("n1", { content: "old", label: "old label" })]);
    const optimistic = graph([node("n1", { content: "agent", label: "agent label" })]);
    const userEdited = graph([node("n1", { content: "user", label: "agent label" })]);
    const forward = diffCanvasMutationGraph(before, optimistic)[0];
    const inverse = {
      ...forward,
      type: "update_node" as const,
      before: forward.after,
      after: forward.before,
      beforeFingerprint: forward.afterFingerprint,
      afterFingerprint: forward.beforeFingerprint,
      changes: forward.changes.slice().reverse().map((change) => ({
        ...change,
        beforeExists: change.afterExists,
        before: change.after,
        afterExists: change.beforeExists,
        after: change.before,
        beforeFingerprint: change.afterFingerprint,
        afterFingerprint: change.beforeFingerprint,
      })),
    };
    const result = applyCanvasMutationPatches(userEdited, [inverse]);
    expect(result.conflicts).toHaveLength(1);
    expect(result.changed).toBe(true);
    expect(result.graph.nodes[0].data).toEqual({ content: "user", label: "old label" });
  });

  it("persists a structural Agent mutation to the lightweight Outbox", async () => {
    runCanvasMutation({
      projectId: "project-a",
      canvasId: "canvas-a",
      commandId: "agent-command-1",
      turnId: "turn-a",
      source: "agent",
      baseRevision: 4,
      commandPayload: {
        apiKey: "do-not-store",
        preview: "data:image/png;base64,drop-me",
      },
    }, () => {
      useCanvasStore.getState().addNode(
        "textAnnotationNode",
        { x: 10, y: 20 },
        { content: "real operation" },
      );
    });

    await waitForCanvasMutationPersistence();
    expect(pendingCanvasMutationCount()).toBe(1);
    const persisted = await outbox.listScope({ projectId: "project-a", canvasId: "canvas-a" });
    expect(persisted).toHaveLength(1);
    expect(persisted[0]).toMatchObject({
      commandId: "agent-command-1",
      source: "agent",
      baseRevision: 4,
    });
    expect(JSON.stringify(persisted[0])).not.toContain("do-not-store");
    expect(JSON.stringify(persisted[0])).not.toContain("data:image");
  });

  it("scrubs sensitive values inside field-level before/after patches", async () => {
    const before = graph([node("secret", { apiKey: "OLD_SECRET" })]);
    const after = graph([node("secret", { apiKey: "NEW_SECRET" })]);
    recordObservedCanvasMutation({
      projectId: "project-secret",
      canvasId: "canvas-secret",
      baseRevision: 1,
      before,
      after,
    });
    await flushObservedCanvasMutation({ projectId: "project-secret", canvasId: "canvas-secret" });
    await waitForCanvasMutationPersistence();
    const stored = await outbox.listScope({ projectId: "project-secret", canvasId: "canvas-secret" });
    expect(JSON.stringify(stored)).not.toContain("OLD_SECRET");
    expect(JSON.stringify(stored)).not.toContain("NEW_SECRET");
  });

  it("coalesces a human graph edit into the same transaction contract", async () => {
    const before = graph([node("n1", { content: "old" })]);
    const after = graph([node("n1", { content: "new" }, { x: 120, y: 80 })]);
    recordObservedCanvasMutation({
      projectId: "project-human",
      canvasId: "canvas-human",
      baseRevision: 8,
      before,
      after,
    });

    const transaction = await flushObservedCanvasMutation({
      projectId: "project-human",
      canvasId: "canvas-human",
    });
    expect(transaction).toMatchObject({
      source: "human",
      baseRevision: 8,
      state: "queued",
      affectedNodeIds: ["n1"],
    });
    expect(transaction?.forwardOps.map((operation) => operation.type)).toEqual([
      "update_node",
    ]);

    const secondAfter = graph([node("n1", { content: "second" }, { x: 120, y: 80 })]);
    recordObservedCanvasMutation({
      projectId: "project-human",
      canvasId: "canvas-human",
      baseRevision: 9,
      before: after,
      after: secondAfter,
    });
    const secondTransaction = await flushObservedCanvasMutation({
      projectId: "project-human",
      canvasId: "canvas-human",
    });
    expect(secondTransaction?.transactionId).not.toBe(transaction?.transactionId);
    expect(secondTransaction?.baseRevision).toBe(9);
  });

  it("routes an explicit workflow mutation through the shared GraphPort and emits metrics", async () => {
    const before = graph([node("n1", { content: "old" })]);
    let current = before;
    const port = {
      read: () => current,
      apply: (next: CanvasMutationGraph) => {
        current = next;
      },
    };
    const statuses: Array<Record<string, unknown>> = [];
    const metrics: Array<Record<string, unknown>> = [];
    const onStatus = (event: Event) => {
      statuses.push((event as CustomEvent<Record<string, unknown>>).detail);
    };
    const onMetrics = (event: Event) => {
      metrics.push((event as CustomEvent<Record<string, unknown>>).detail);
    };
    window.addEventListener(CANVAS_MUTATION_OUTBOX_STATUS_EVENT, onStatus);
    window.addEventListener(CANVAS_MUTATION_METRICS_EVENT, onMetrics);
    try {
      dispatchCanvasMutation({
        projectId: "project-dispatch",
        canvasId: "canvas-dispatch",
        commandId: "workflow-command-1",
        source: "workflow",
        baseRevision: 2,
        graphPort: port,
      }, () => {
        current = graph([node("n1", { content: "new" })]);
      });
    } finally {
      window.removeEventListener(CANVAS_MUTATION_OUTBOX_STATUS_EVENT, onStatus);
      window.removeEventListener(CANVAS_MUTATION_METRICS_EVENT, onMetrics);
    }

    expect(current.nodes[0].data.content).toBe("new");
    expect(statuses[statuses.length - 1]).toMatchObject({ pendingCount: 1 });
    expect(metrics[metrics.length - 1]).toMatchObject({
      source: "workflow",
      projectId: "project-dispatch",
      canvasId: "canvas-dispatch",
      commandId: "workflow-command-1",
      operationCount: 1,
      largeCanvas: false,
    });
    expect(getCanvasMutationOutboxStatus().pendingCount).toBe(1);
  });

  it("marks a large-canvas mutation in metrics without changing the hot path", () => {
    const nodes = Array.from({ length: 1_001 }, (_, index) => (
      node(`node-${index}`, { content: index === 0 ? "old" : "stable" })
    ));
    let metrics: Record<string, unknown> | null = null;
    const onMetrics = (event: Event) => {
      metrics = (event as CustomEvent<Record<string, unknown>>).detail;
    };
    window.addEventListener(CANVAS_MUTATION_METRICS_EVENT, onMetrics);
    try {
      dispatchCanvasMutation({
        projectId: "project-large",
        canvasId: "canvas-large",
        commandId: "large-command-1",
        source: "human",
        baseRevision: 1,
        beforeGraph: graph(nodes),
        afterGraph: graph(nodes.map((item, index) => (
          index === 0 ? node(item.id, { content: "changed" }) : item
        ))),
      }, () => undefined);
    } finally {
      window.removeEventListener(CANVAS_MUTATION_METRICS_EVENT, onMetrics);
    }
    expect(metrics).toMatchObject({
      nodeCount: 1_001,
      largeCanvas: true,
      operationCount: 1,
    });
  });

  it("keeps degraded Outbox deletes and clears from resurrecting IndexedDB records", async () => {
    const backing = createMemoryCanvasMutationOutbox();
    let failing = true;
    const flaky: CanvasMutationOutbox = {
      put: async (transaction) => {
        if (failing) throw new Error("temporary put failure");
        await backing.put(transaction);
      },
      get: async (transactionId) => {
        if (failing) throw new Error("temporary get failure");
        return await backing.get(transactionId);
      },
      delete: async (transactionId) => {
        if (failing) throw new Error("temporary delete failure");
        await backing.delete(transactionId);
      },
      listScope: async (scope) => {
        if (failing) throw new Error("temporary list failure");
        return await backing.listScope(scope);
      },
      clear: async () => {
        if (failing) throw new Error("temporary clear failure");
        await backing.clear();
      },
      prune: async (now) => {
        if (failing) throw new Error("temporary prune failure");
        await backing.prune(now);
      },
      getStatus: () => backing.getStatus(),
    };
    const resilient = createResilientCanvasMutationOutbox(flaky, {
      listenForOnline: false,
    });
    const transaction = queuedTransaction("transaction-resilient");
    await resilient.put(transaction);
    expect(resilient.getStatus()).toMatchObject({
      status: "degraded",
      storage: "memory",
      pendingCount: 1,
    });

    failing = false;
    await resilient.recover();
    expect(resilient.getStatus()).toMatchObject({
      status: "healthy",
      storage: "indexeddb",
    });
    expect(await backing.listScope(transaction)).toHaveLength(1);

    failing = true;
    await resilient.delete(transaction.transactionId);
    expect(resilient.getStatus().status).toBe("degraded");
    failing = false;
    await resilient.recover();
    expect(await backing.listScope(transaction)).toEqual([]);

    await resilient.put(transaction);
    failing = true;
    await resilient.clear();
    failing = false;
    await resilient.recover();
    expect(await backing.listScope(transaction)).toEqual([]);
    resilient.dispose();
  });

  it("replays an unconfirmed Outbox transaction after refresh and commits it once", async () => {
    const remote = graph([node("n1", { content: "old" })]);
    useCanvasStore.getState().setCanvasData(remote.nodes, remote.edges);
    runCanvasMutation({
      projectId: "project-recover",
      canvasId: "canvas-recover",
      commandId: "command-recover",
      source: "human",
      baseRevision: 3,
    }, () => {
      useCanvasStore.getState().updateNodeData("n1", { content: "recovered" });
    });
    await waitForCanvasMutationPersistence();
    const persisted = await outbox.listScope({
      projectId: "project-recover",
      canvasId: "canvas-recover",
    });
    expect(persisted).toHaveLength(1);

    await resetCanvasMutationKernelForTests();
    await setCanvasMutationOutboxForTests(outbox);
    await outbox.put(persisted[0]);
    let current = remote;
    let saveCount = 0;
    const recovered = await recoverCanvasMutationScope({
      projectId: "project-recover",
      canvasId: "canvas-recover",
      revision: 3,
      remoteGraph: remote,
      currentGraph: current,
      applyGraph: (next) => {
        current = next;
      },
      persist: async () => {
        saveCount += 1;
        return true;
      },
    });

    expect(recovered).toMatchObject({ committed: 0, recovered: 1, conflicted: 0 });
    expect(current.nodes[0].data.content).toBe("recovered");
    expect(saveCount).toBe(1);
    expect(await outbox.listScope({
      projectId: "project-recover",
      canvasId: "canvas-recover",
    })).toEqual([]);
  });

  it("never promotes an unconfirmed Agent preview through the ordinary canvas save path", async () => {
    const remote = graph([node("n1", { content: "old" })]);
    useCanvasStore.getState().setCanvasData(remote.nodes, remote.edges);
    runCanvasMutation({
      projectId: "project-agent-authority",
      canvasId: "canvas-agent-authority",
      commandId: "command-agent-authority",
      source: "agent",
      baseRevision: 6,
    }, () => {
      useCanvasStore.getState().updateNodeData("n1", { content: "optimistic" });
    });
    await waitForCanvasMutationPersistence();
    const persisted = await outbox.listScope({
      projectId: "project-agent-authority",
      canvasId: "canvas-agent-authority",
    });
    await resetCanvasMutationKernelForTests();
    await setCanvasMutationOutboxForTests(outbox);
    await outbox.put(persisted[0]);

    let saveCount = 0;
    const result = await recoverCanvasMutationScope({
      projectId: "project-agent-authority",
      canvasId: "canvas-agent-authority",
      revision: 6,
      remoteGraph: remote,
      currentGraph: remote,
      applyGraph: () => {
        throw new Error("Agent preview must not be locally promoted");
      },
      persist: async () => {
        saveCount += 1;
        return true;
      },
    });

    expect(result).toMatchObject({ committed: 0, recovered: 0, conflicted: 0, authorityPending: 1 });
    expect(saveCount).toBe(0);
    expect(await outbox.listScope({
      projectId: "project-agent-authority",
      canvasId: "canvas-agent-authority",
    })).toHaveLength(1);
  });
});
