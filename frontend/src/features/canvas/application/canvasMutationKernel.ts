// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab

import { sha1Text } from "@/lib/sha1";
import {
  useCanvasStore,
  type CanvasEdge,
  type CanvasNode,
} from "@/stores/canvasStore";

import {
  CANVAS_MUTATION_OUTBOX_STATUS_EVENT,
  createDefaultCanvasMutationOutbox,
  type CanvasMutationOutbox,
} from "./canvasMutationOutbox";
export { CANVAS_MUTATION_OUTBOX_STATUS_EVENT } from "./canvasMutationOutbox";
import type {
  CanvasMutationApplyResult,
  CanvasMutationEntityPatch,
  CanvasGraphPort,
  CanvasMutationGraph,
  CanvasMutationMetrics,
  CanvasMutationOperationType,
  CanvasMutationOutboxStatusSnapshot,
  CanvasMutationScope,
  CanvasMutationSource,
  CanvasMutationTransaction,
  CanvasMutationValueChange,
} from "./canvasMutationTypes";
export type { CanvasMutationSource } from "./canvasMutationTypes";

export const CANVAS_MUTATION_STATE_EVENT = "village-canvas:mutation-state";
export const CANVAS_MUTATION_CONFLICT_EVENT = "village-canvas:mutation-conflict";
export const CANVAS_MUTATION_METRICS_EVENT = "village-canvas:mutation-metrics";

export const CANVAS_MUTATION_PERFORMANCE_THRESHOLDS = {
  largeNodeCount: 1_000,
  largeEdgeCount: 5_000,
  normalDiffWarnMs: 16,
  largeDiffWarnMs: 50,
} as const;

const OBSERVED_MUTATION_PERSIST_MS = 120;
const IGNORED_NODE_ROOT_FIELDS = new Set([
  "selected",
  "dragging",
  "measured",
  "positionAbsolute",
  "resizing",
]);
/**
 * 只写「运行态」的字段：值随任务进度高频变化、刷新后由任务订阅重建，把它们放进操作
 * 日志只会淹没真正的编辑。
 *
 * **`scriptResult` 不在这里。** 它是脚本节点表格的**可编辑内容**（`handleCellCommit`
 * 逐格写回），同时是 LibTV `linkedImageGroupId` 那条级联链的判定源。早先它被当成
 * 生成结果排除掉，后果是：用户在表格里改一行提示词，内核产出 **0 个补丁** ——
 * 操作日志 / outbox / agent 都看不到这次编辑（自动保存走 `nodeSignature` 另一条路，
 * 所以改动仍会落盘，两个子系统对同一字段口径相反）。
 */
const EPHEMERAL_NODE_DATA_FIELDS = new Set([
  "frames",
  "heightPx",
  "progress",
  "status",
  "widthPx",
]);
const EPHEMERAL_NODE_DATA_PATTERN = /^(?:error|generation|isGenerating|isUploading|job|skillRun|task|upload)/i;
/**
 * URL 承载字段（`imageUrl` / `referenceImageUrl` / `videoUrl` …）不进操作日志：它们由
 * 生成、上传、脚本派生写回，值是长串而不是用户敲进去的内容，进日志只会淹没真正的编辑。
 *
 * 复数形式（`referenceImageUrls`：节点自带的**整组**参考图，脚本行派生多个角色时是
 * 数组）必须与单数同口径 —— 否则同一次「换参考图」会按写哪个字段得到不同结论：
 * 只改 `referenceImageUrl` 产出 0 个补丁，带上组就产出 1 个带 URL 数组的补丁。
 */
const URL_NODE_DATA_FIELD_PATTERN = /(?:Urls?|URI)$/i;
const SECRET_KEY_PATTERN = /(api[-_]?key|authorization|password|secret|token)/i;

export interface CanvasMutationIntent extends CanvasMutationScope {
  commandId: string;
  turnId?: string;
  source: CanvasMutationSource;
  baseRevision: number | null;
  commandPayload?: unknown;
}

interface ObservedMutationBatch extends CanvasMutationScope {
  transactionId: string;
  commandId: string;
  before: CanvasMutationGraph;
  after: CanvasMutationGraph;
  baseRevision: number | null;
  createdAt: number;
  timer: ReturnType<typeof setTimeout> | null;
}

export interface CanvasMutationDispatchInput extends CanvasMutationIntent {
  transactionId?: string;
  graphPort?: CanvasGraphPort;
  beforeGraph?: CanvasMutationGraph;
  afterGraph?: CanvasMutationGraph;
}

interface RecoverCanvasMutationScopeInput extends CanvasMutationScope {
  revision: number | null;
  remoteGraph: CanvasMutationGraph;
  currentGraph: CanvasMutationGraph;
  applyGraph: (graph: CanvasMutationGraph) => void;
  persist: () => Promise<boolean>;
  resolveAuthority?: (
    transaction: CanvasMutationTransaction,
  ) => Promise<"applied" | "pending" | "rejected">;
  refreshRemoteGraph?: () => Promise<CanvasMutationGraph>;
  isActive?: () => boolean;
}

export interface CanvasMutationRecoveryResult {
  committed: number;
  recovered: number;
  conflicted: number;
  retryPending: boolean;
  authorityPending: number;
}

const activeMutationDepth = new Map<string, number>();
const intents = new Map<string, CanvasMutationIntent>();
const transactions = new Map<string, CanvasMutationTransaction>();
const observedBatches = new Map<string, ObservedMutationBatch>();
let mountedScopeKey: string | null = null;
let outbox: CanvasMutationOutbox = createDefaultCanvasMutationOutbox();
let persistenceTail: Promise<void> = Promise.resolve();
const recoveryPromises = new Map<string, Promise<CanvasMutationRecoveryResult>>();
const defaultCanvasGraphPort: CanvasGraphPort = {
  read: () => {
    const state = useCanvasStore.getState();
    return { nodes: state.nodes, edges: state.edges };
  },
  apply: (graph) => {
    useCanvasStore.setState({ nodes: graph.nodes, edges: graph.edges });
  },
};

function nonEmptyString(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function normalizedScope(scope: CanvasMutationScope): CanvasMutationScope | null {
  const projectId = nonEmptyString(scope.projectId);
  const canvasId = nonEmptyString(scope.canvasId);
  return projectId && canvasId ? { projectId, canvasId } : null;
}

function scopeKey(scope: CanvasMutationScope): string {
  return `${scope.projectId}\u0000${scope.canvasId}`;
}

function commandKey(scope: CanvasMutationScope, commandId: string): string {
  return `${scopeKey(scope)}\u0000${commandId}`;
}

export function createCanvasStoreGraphPort(): CanvasGraphPort {
  return defaultCanvasGraphPort;
}

function activeTransactionCount(): number {
  return [...transactions.values()]
    .filter((transaction) => !["committed", "rolled_back"].includes(transaction.state))
    .length;
}

export function getCanvasMutationOutboxStatus(): CanvasMutationOutboxStatusSnapshot {
  return {
    ...outbox.getStatus(),
    pendingCount: activeTransactionCount(),
  };
}

function emitCanvasMutationOutboxStatus(): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent(CANVAS_MUTATION_OUTBOX_STATUS_EVENT, {
    detail: getCanvasMutationOutboxStatus(),
  }));
}

function performanceNow(): number {
  return typeof performance !== "undefined" ? performance.now() : Date.now();
}

function emitCanvasMutationMetrics(
  transaction: CanvasMutationTransaction,
  diffDurationMs: number,
  graph: CanvasMutationGraph,
): void {
  const nodeCount = graph.nodes.length;
  const edgeCount = graph.edges.length;
  const largeCanvas = nodeCount >= CANVAS_MUTATION_PERFORMANCE_THRESHOLDS.largeNodeCount
    || edgeCount >= CANVAS_MUTATION_PERFORMANCE_THRESHOLDS.largeEdgeCount;
  const metrics: CanvasMutationMetrics = {
    source: transaction.source,
    projectId: transaction.projectId,
    canvasId: transaction.canvasId,
    commandId: transaction.commandId,
    transactionId: transaction.transactionId,
    nodeCount,
    edgeCount,
    operationCount: transaction.forwardOps.length,
    diffDurationMs,
    largeCanvas,
    slowDiff: diffDurationMs > (
      largeCanvas
        ? CANVAS_MUTATION_PERFORMANCE_THRESHOLDS.largeDiffWarnMs
        : CANVAS_MUTATION_PERFORMANCE_THRESHOLDS.normalDiffWarnMs
    ),
    createdAt: Date.now(),
  };
  if (typeof window !== "undefined") {
    window.dispatchEvent(new CustomEvent(CANVAS_MUTATION_METRICS_EVENT, {
      detail: metrics,
    }));
  }
}

function randomId(prefix: string): string {
  const random = globalThis.crypto?.randomUUID?.()
    ?? `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
  return `${prefix}-${random}`;
}

function cloneValue<T>(value: T): T {
  if (typeof structuredClone === "function") {
    try {
      return structuredClone(value);
    } catch {
      // Fall through to JSON for plain canvas records.
    }
  }
  return JSON.parse(JSON.stringify(value)) as T;
}

function stableValue(value: unknown, seen = new WeakSet<object>()): unknown {
  if (value === undefined) return { __villageUndefined: true };
  if (value === null || typeof value !== "object") return value;
  if (seen.has(value)) return { __villageCircular: true };
  seen.add(value);
  if (Array.isArray(value)) return value.map((item) => stableValue(item, seen));
  const record = value as Record<string, unknown>;
  return Object.fromEntries(
    Object.keys(record)
      .sort()
      .map((key) => [key, stableValue(record[key], seen)]),
  );
}

function fingerprint(value: unknown): string {
  return sha1Text(JSON.stringify(stableValue(value)));
}

function valueFingerprint(exists: boolean, value: unknown): string {
  return fingerprint({ exists, value });
}

function isPlainRecord(value: unknown): value is Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const prototype = Object.getPrototypeOf(value);
  return prototype === Object.prototype || prototype === null;
}

function valuesEqual(left: unknown, right: unknown): boolean {
  return fingerprint(left) === fingerprint(right);
}

function sanitizeForPersistence(value: unknown, key = ""): unknown {
  if (SECRET_KEY_PATTERN.test(key)) return undefined;
  if (typeof value === "string" && value.startsWith("data:")) return null;
  if (typeof Blob !== "undefined" && value instanceof Blob) return null;
  if (typeof File !== "undefined" && value instanceof File) return null;
  if (Array.isArray(value)) {
    return value.map((item) => sanitizeForPersistence(item));
  }
  if (!isPlainRecord(value)) return value;
  const entries = Object.entries(value)
    .map(([childKey, childValue]) => [childKey, sanitizeForPersistence(childValue, childKey)] as const)
    .filter(([, childValue]) => childValue !== undefined);
  return Object.fromEntries(entries);
}

function persistedTransaction(
  transaction: CanvasMutationTransaction,
): CanvasMutationTransaction {
  const sanitized = sanitizeForPersistence(transaction) as CanvasMutationTransaction;
  const scrub = (change: CanvasMutationValueChange): CanvasMutationValueChange => {
    if (!change.path.some((segment) => SECRET_KEY_PATTERN.test(segment))) return change;
    return {
      ...change,
      before: null,
      after: null,
      beforeExists: false,
      afterExists: false,
    };
  };
  sanitized.forwardOps = sanitized.forwardOps.map((operation) => ({
    ...operation,
    changes: operation.changes.map(scrub),
  }));
  sanitized.inverseOps = sanitized.inverseOps.map((operation) => ({
    ...operation,
    changes: operation.changes.map(scrub),
  }));
  return sanitized;
}

function emitMutationState(transaction: CanvasMutationTransaction): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent(CANVAS_MUTATION_STATE_EVENT, {
    detail: {
      transactionId: transaction.transactionId,
      commandId: transaction.commandId,
      projectId: transaction.projectId,
      canvasId: transaction.canvasId,
      source: transaction.source,
      state: transaction.state,
      affectedNodeIds: transaction.affectedNodeIds,
      affectedEdgeIds: transaction.affectedEdgeIds,
    },
  }));
}

function queuePersistence(action: () => Promise<void>): void {
  persistenceTail = persistenceTail
    .catch(() => undefined)
    .then(action)
    .catch(() => undefined);
}

function persistTransaction(transaction: CanvasMutationTransaction): void {
  queuePersistence(() => outbox.put(persistedTransaction(transaction)));
}

function deletePersistedTransaction(transactionId: string): void {
  queuePersistence(() => outbox.delete(transactionId));
  emitCanvasMutationOutboxStatus();
}

function semanticEntity(value: unknown, entity: "node" | "edge"): unknown {
  if (!isPlainRecord(value)) return value;
  if (entity !== "node") return value;
  return Object.fromEntries(
    Object.entries(value).filter(([key]) => !IGNORED_NODE_ROOT_FIELDS.has(key)),
  );
}

function entityFingerprint(value: unknown, entity: "node" | "edge"): string {
  return fingerprint(semanticEntity(value, entity));
}

function entityFingerprintForPatch(
  value: CanvasNode | CanvasEdge,
  entity: "node" | "edge",
): string {
  return entityFingerprint(value, entity);
}

function shouldIgnorePath(entity: "node" | "edge", path: string[]): boolean {
  if (entity !== "node") return false;
  if (path.length === 1 && IGNORED_NODE_ROOT_FIELDS.has(path[0])) return true;
  if (path[0] !== "data" || !path[1]) return false;
  const dataField = path[1];
  return EPHEMERAL_NODE_DATA_FIELDS.has(dataField)
    || EPHEMERAL_NODE_DATA_PATTERN.test(dataField)
    || URL_NODE_DATA_FIELD_PATTERN.test(dataField);
}

function collectValueChanges(
  before: unknown,
  after: unknown,
  entity: "node" | "edge",
  path: string[] = [],
  changes: CanvasMutationValueChange[] = [],
): CanvasMutationValueChange[] {
  if (shouldIgnorePath(entity, path)) return changes;
  if (valuesEqual(before, after)) return changes;
  if (isPlainRecord(before) && isPlainRecord(after) && path.length < 6) {
    const keys = [...new Set([...Object.keys(before), ...Object.keys(after)])].sort();
    for (const key of keys) {
      if (path.length === 0 && shouldIgnorePath(entity, [key])) continue;
      const beforeExists = Object.prototype.hasOwnProperty.call(before, key);
      const afterExists = Object.prototype.hasOwnProperty.call(after, key);
      if (!beforeExists || !afterExists) {
        changes.push({
          path: [...path, key],
          beforeExists,
          before: before[key],
          afterExists,
          after: after[key],
          beforeFingerprint: valueFingerprint(beforeExists, before[key]),
          afterFingerprint: valueFingerprint(afterExists, after[key]),
        });
        continue;
      }
      collectValueChanges(before[key], after[key], entity, [...path, key], changes);
    }
    return changes;
  }
  const beforeExists = before !== undefined;
  const afterExists = after !== undefined;
  changes.push({
    path,
    beforeExists,
    before,
    afterExists,
    after,
    beforeFingerprint: valueFingerprint(beforeExists, before),
    afterFingerprint: valueFingerprint(afterExists, after),
  });
  return changes;
}

function nodePatchType(changes: CanvasMutationValueChange[]): CanvasMutationOperationType {
  return changes.length > 0 && changes.every((change) => change.path[0] === "position")
    ? "move_node"
    : "update_node";
}

function buildEntityPatches(
  beforeValues: Array<CanvasNode | CanvasEdge>,
  afterValues: Array<CanvasNode | CanvasEdge>,
  entity: "node" | "edge",
): CanvasMutationEntityPatch[] {
  const beforeById = new Map(beforeValues.map((value) => [value.id, value] as const));
  const afterById = new Map(afterValues.map((value) => [value.id, value] as const));
  const ids = [...new Set([...beforeById.keys(), ...afterById.keys()])].sort();
  const patches: CanvasMutationEntityPatch[] = [];
  for (const entityId of ids) {
    const before = beforeById.get(entityId) ?? null;
    const after = afterById.get(entityId) ?? null;
    if (!before && !after) continue;
    if (before === after) continue;
    if (!before || !after) {
      patches.push({
        type: entity === "node"
          ? before ? "delete_node" : "create_node"
          : before ? "remove_edge" : "connect_nodes",
        entity,
        entityId,
        before: before ? cloneValue(before) : null,
        after: after ? cloneValue(after) : null,
        beforeFingerprint: before ? entityFingerprintForPatch(before, entity) : "absent",
        afterFingerprint: after ? entityFingerprintForPatch(after, entity) : "absent",
        changes: [],
      });
      continue;
    }
    const changes = collectValueChanges(before, after, entity);
    if (changes.length === 0) continue;
    patches.push({
      type: entity === "node" ? nodePatchType(changes) : "update_edge",
      entity,
      entityId,
      before: cloneValue(before),
      after: cloneValue(after),
      beforeFingerprint: entityFingerprintForPatch(before, entity),
      afterFingerprint: entityFingerprintForPatch(after, entity),
      changes,
    });
  }
  return patches;
}

function reverseOperation(type: CanvasMutationOperationType): CanvasMutationOperationType {
  if (type === "create_node") return "delete_node";
  if (type === "delete_node") return "create_node";
  if (type === "connect_nodes") return "remove_edge";
  if (type === "remove_edge") return "connect_nodes";
  return type;
}

function inversePatch(patch: CanvasMutationEntityPatch): CanvasMutationEntityPatch {
  return {
    ...patch,
    type: reverseOperation(patch.type),
    before: patch.after,
    after: patch.before,
    beforeFingerprint: patch.afterFingerprint,
    afterFingerprint: patch.beforeFingerprint,
    changes: [...patch.changes].reverse().map((change) => ({
      ...change,
      beforeExists: change.afterExists,
      before: change.after,
      afterExists: change.beforeExists,
      after: change.before,
      beforeFingerprint: change.afterFingerprint,
      afterFingerprint: change.beforeFingerprint,
    })),
  };
}

export function diffCanvasMutationGraph(
  before: CanvasMutationGraph,
  after: CanvasMutationGraph,
): CanvasMutationEntityPatch[] {
  return [
    ...buildEntityPatches(before.nodes, after.nodes, "node"),
    ...buildEntityPatches(before.edges, after.edges, "edge"),
  ];
}

function transactionFromGraphs(
  input: CanvasMutationIntent & { transactionId?: string },
  before: CanvasMutationGraph,
  after: CanvasMutationGraph,
  createdAt = Date.now(),
): CanvasMutationTransaction | null {
  const forwardOps = diffCanvasMutationGraph(before, after);
  if (forwardOps.length === 0) return null;
  const inverseOps = [...forwardOps].reverse().map(inversePatch);
  const affectedNodeIds = [...new Set(
    forwardOps.filter((op) => op.entity === "node").map((op) => op.entityId),
  )];
  const affectedEdgeIds = [...new Set(
    forwardOps.filter((op) => op.entity === "edge").map((op) => op.entityId),
  )];
  const touchedFields = Object.fromEntries(forwardOps.map((op) => [
    `${op.entity}:${op.entityId}`,
    op.changes.length > 0 ? op.changes.map((change) => change.path.join(".")) : ["*"],
  ]));
  return {
    schema: "canvas_mutation_transaction.v1",
    transactionId: input.transactionId
      ?? `${input.source === "human" ? "canvas-mutation" : "canvas-command"}-${sha1Text(
        `${input.projectId}\u0000${input.canvasId}\u0000${input.commandId}`,
      ).slice(0, 24)}`,
    commandId: input.commandId,
    projectId: input.projectId,
    canvasId: input.canvasId,
    ...(input.turnId ? { turnId: input.turnId } : {}),
    baseRevision: input.baseRevision,
    source: input.source,
    forwardOps,
    inverseOps,
    affectedNodeIds,
    affectedEdgeIds,
    touchedFields,
    state: "queued",
    createdAt,
    updatedAt: Date.now(),
    ...(input.commandPayload !== undefined ? { commandPayload: input.commandPayload } : {}),
  };
}

function readPath(root: unknown, path: string[]): { exists: boolean; value: unknown } {
  let value = root;
  for (const segment of path) {
    if (!isPlainRecord(value) || !Object.prototype.hasOwnProperty.call(value, segment)) {
      return { exists: false, value: undefined };
    }
    value = value[segment];
  }
  return { exists: true, value };
}

function writePath(
  root: Record<string, unknown>,
  path: string[],
  exists: boolean,
  value: unknown,
): void {
  if (path.length === 0) return;
  let parent = root;
  for (const segment of path.slice(0, -1)) {
    const current = parent[segment];
    if (!isPlainRecord(current)) parent[segment] = {};
    parent = parent[segment] as Record<string, unknown>;
  }
  const leaf = path[path.length - 1];
  if (exists) parent[leaf] = cloneValue(value);
  else delete parent[leaf];
}

function applyPatches(
  graph: CanvasMutationGraph,
  patches: CanvasMutationEntityPatch[],
): CanvasMutationApplyResult {
  const nodeById = new Map<string, CanvasNode>(graph.nodes.map((node) => [node.id, node]));
  const edgeById = new Map<string, CanvasEdge>(graph.edges.map((edge) => [edge.id, edge]));
  const conflicts: CanvasMutationApplyResult["conflicts"] = [];
  let changed = false;

  for (const patch of patches) {
    const current = patch.entity === "node"
      ? nodeById.get(patch.entityId) ?? null
      : edgeById.get(patch.entityId) ?? null;
    const removeCurrent = () => {
      if (patch.entity === "node") nodeById.delete(patch.entityId);
      else edgeById.delete(patch.entityId);
    };
    const setCurrent = (value: CanvasNode | CanvasEdge) => {
      if (patch.entity === "node") nodeById.set(patch.entityId, value as CanvasNode);
      else edgeById.set(patch.entityId, value as CanvasEdge);
    };
    if (patch.before === null) {
      if (current === null) {
        setCurrent(cloneValue(patch.after!) as CanvasNode | CanvasEdge);
        changed = true;
      } else {
        // A server-normalized entity can contain defaults or metadata that
        // were absent from the browser preview. Stable identity is enough to
        // prove a create was accepted; exact entity equality would strand the
        // Outbox after every harmless server normalization.
      }
      continue;
    }
    if (patch.after === null) {
      if (current === null) continue;
      if (entityFingerprint(current, patch.entity) !== patch.beforeFingerprint) {
        conflicts.push({ entity: patch.entity, entityId: patch.entityId });
        continue;
      }
      removeCurrent();
      changed = true;
      continue;
    }
    if (current === null) {
      conflicts.push({ entity: patch.entity, entityId: patch.entityId });
      continue;
    }
    const next = cloneValue(current) as unknown as Record<string, unknown>;
    let entityChanged = false;
    for (const change of patch.changes) {
      const currentValue = readPath(next, change.path);
      const currentFingerprint = valueFingerprint(currentValue.exists, currentValue.value);
      if (currentFingerprint === change.afterFingerprint) continue;
      if (currentFingerprint !== change.beforeFingerprint) {
        conflicts.push({
          entity: patch.entity,
          entityId: patch.entityId,
          path: change.path,
        });
        continue;
      }
      writePath(next, change.path, change.afterExists, change.after);
      entityChanged = true;
    }
    if (entityChanged) {
      setCurrent(next as CanvasNode | CanvasEdge);
      changed = true;
    }
  }

  const originalNodeIds = new Set(graph.nodes.map((node) => node.id));
  const originalEdgeIds = new Set(graph.edges.map((edge) => edge.id));
  return {
    graph: {
      nodes: [
        ...graph.nodes.map((node) => nodeById.get(node.id)).filter(Boolean),
        ...[...nodeById.values()].filter((node) => !originalNodeIds.has(node.id)),
      ] as typeof graph.nodes,
      edges: [
        ...graph.edges.map((edge) => edgeById.get(edge.id)).filter(Boolean),
        ...[...edgeById.values()].filter((edge) => !originalEdgeIds.has(edge.id)),
      ] as typeof graph.edges,
    },
    changed,
    conflicts,
  };
}

export function applyCanvasMutationPatches(
  graph: CanvasMutationGraph,
  patches: CanvasMutationEntityPatch[],
): CanvasMutationApplyResult {
  return applyPatches(graph, patches);
}

export function isCanvasMutationGraphSatisfied(
  transaction: CanvasMutationTransaction,
  graph: CanvasMutationGraph,
): boolean {
  const result = applyCanvasMutationPatches(graph, transaction.forwardOps);
  return result.conflicts.length === 0 && !result.changed;
}

function setTransactionState(
  transaction: CanvasMutationTransaction,
  state: CanvasMutationTransaction["state"],
): CanvasMutationTransaction {
  const next = { ...transaction, state, updatedAt: Date.now() };
  transactions.set(commandKey(next, next.commandId), next);
  persistTransaction(next);
  emitMutationState(next);
  emitCanvasMutationOutboxStatus();
  return next;
}

function rememberTransaction(transaction: CanvasMutationTransaction): void {
  transactions.set(commandKey(transaction, transaction.commandId), transaction);
  persistTransaction(transaction);
  emitMutationState(transaction);
  emitCanvasMutationOutboxStatus();
}

function removeObservedBatchForTransaction(transactionId: string): void {
  for (const [key, batch] of observedBatches) {
    if (batch.transactionId !== transactionId) continue;
    if (batch.timer) clearTimeout(batch.timer);
    observedBatches.delete(key);
  }
}

export function registerCanvasMutationIntent(intent: CanvasMutationIntent): boolean {
  const scope = normalizedScope(intent);
  const commandId = nonEmptyString(intent.commandId);
  if (!scope || !commandId) return false;
  intents.set(commandKey(scope, commandId), {
    ...intent,
    ...scope,
    commandId,
    baseRevision: Number.isSafeInteger(intent.baseRevision) ? intent.baseRevision : null,
  });
  return true;
}

export function dispatchCanvasMutation<T>(
  input: CanvasMutationDispatchInput,
  mutation: () => T,
): T {
  const scope = normalizedScope(input);
  const commandId = nonEmptyString(input.commandId);
  if (!scope || !commandId) return mutation();
  const graphPort = input.graphPort ?? defaultCanvasGraphPort;
  const key = scopeKey(scope);
  const before = input.beforeGraph ?? graphPort.read();
  activeMutationDepth.set(key, (activeMutationDepth.get(key) ?? 0) + 1);
  let result!: T;
  let failed = false;
  let failure: unknown;
  try {
    result = mutation();
  } catch (error) {
    failed = true;
    failure = error;
  } finally {
    const depth = (activeMutationDepth.get(key) ?? 1) - 1;
    if (depth > 0) activeMutationDepth.set(key, depth);
    else activeMutationDepth.delete(key);
  }
  const after = input.afterGraph ?? graphPort.read();
  const diffStartedAt = performanceNow();
  const transaction = transactionFromGraphs(
    { ...input, ...scope, commandId },
    before,
    after,
  );
  const diffDurationMs = performanceNow() - diffStartedAt;
  intents.delete(commandKey(scope, commandId));
  if (transaction) {
    rememberTransaction(transaction);
    emitCanvasMutationMetrics(transaction, diffDurationMs, after);
    if (failed) {
      const rollback = applyRollbackToStore(transaction, graphPort);
      if (rollback.conflicts.length > 0) {
        markCanvasMutationConflict(transaction, rollback.conflicts);
      } else {
        finalizeRolledBackTransaction(transaction);
      }
    }
  }
  if (failed) throw failure;
  return result;
}

/** Compatibility façade for existing Agent callers. */
export function runCanvasMutation<T>(
  input: CanvasMutationDispatchInput,
  mutation: () => T,
): T {
  return dispatchCanvasMutation(input, mutation);
}

export function runCanvasMutationGuard<T>(
  scopeInput: CanvasMutationScope,
  mutation: () => T,
): T {
  const scope = normalizedScope(scopeInput);
  if (!scope) return mutation();
  const key = scopeKey(scope);
  activeMutationDepth.set(key, (activeMutationDepth.get(key) ?? 0) + 1);
  try {
    return mutation();
  } finally {
    const depth = (activeMutationDepth.get(key) ?? 1) - 1;
    if (depth > 0) activeMutationDepth.set(key, depth);
    else activeMutationDepth.delete(key);
  }
}

export function isCanvasMutationActive(scopeInput: CanvasMutationScope): boolean {
  const scope = normalizedScope(scopeInput);
  return Boolean(scope && (activeMutationDepth.get(scopeKey(scope)) ?? 0) > 0);
}

export function recordObservedCanvasMutation(input: {
  projectId: string;
  canvasId: string;
  baseRevision: number | null;
  before: CanvasMutationGraph;
  after: CanvasMutationGraph;
}): void {
  const scope = normalizedScope(input);
  if (!scope || isCanvasMutationActive(scope)) return;
  const key = scopeKey(scope);
  const existing = observedBatches.get(key);
  const batch: ObservedMutationBatch = existing
    ? { ...existing, after: input.after }
    : {
        ...scope,
        transactionId: randomId("canvas-human"),
        commandId: randomId("human-command"),
        before: input.before,
        after: input.after,
        baseRevision: input.baseRevision,
        createdAt: Date.now(),
        timer: null,
      };
  if (batch.timer) clearTimeout(batch.timer);
  batch.timer = setTimeout(() => {
    void flushObservedCanvasMutation(scope);
  }, OBSERVED_MUTATION_PERSIST_MS);
  observedBatches.set(key, batch);
}

export async function flushObservedCanvasMutation(
  scopeInput: CanvasMutationScope,
): Promise<CanvasMutationTransaction | null> {
  const scope = normalizedScope(scopeInput);
  if (!scope) return null;
  const key = scopeKey(scope);
  const batch = observedBatches.get(key);
  if (!batch) return null;
  if (batch.timer) clearTimeout(batch.timer);
  batch.timer = null;
  observedBatches.delete(key);
  dispatchCanvasMutation({
    ...scope,
    transactionId: batch.transactionId,
    commandId: batch.commandId,
    source: "human",
    baseRevision: batch.baseRevision,
    beforeGraph: batch.before,
    afterGraph: batch.after,
  }, () => undefined);
  const transaction = transactions.get(commandKey(scope, batch.commandId)) ?? null;
  await waitForCanvasMutationPersistence();
  return transaction;
}

export async function pendingCanvasMutationTransactionIds(
  scopeInput: CanvasMutationScope,
): Promise<string[]> {
  const scope = normalizedScope(scopeInput);
  if (!scope) return [];
  await waitForCanvasMutationPersistence();
  const persisted = await outbox.listScope(scope);
  for (const transaction of persisted) {
    transactions.set(commandKey(transaction, transaction.commandId), transaction);
  }
  emitCanvasMutationOutboxStatus();
  return [...new Set(
    [...transactions.values()]
      .filter((transaction) => (
        transaction.projectId === scope.projectId
        && transaction.canvasId === scope.canvasId
        && !["committed", "rolled_back", "conflicted"].includes(transaction.state)
      ))
      .map((transaction) => transaction.transactionId),
  )];
}

export function acknowledgeCanvasMutation(input: {
  projectId: string;
  canvasId: string;
  commandId?: string;
  revision?: number | null;
}): boolean {
  const scope = normalizedScope(input);
  const commandId = nonEmptyString(input.commandId);
  if (!scope || !commandId) return false;
  const key = commandKey(scope, commandId);
  const transaction = transactions.get(key);
  const known = Boolean(transaction || intents.has(key));
  if (transaction) {
    setTransactionState({
      ...transaction,
      ...(Number.isSafeInteger(input.revision) && Number(input.revision) > 0
        ? { acknowledgedRevision: Number(input.revision) }
        : {}),
    }, "server_acknowledged");
  }
  if (!transaction) {
    intents.delete(key);
    return known;
  }
  return true;
}

export async function confirmCanvasMutationTransactions(
  transactionIds: string[],
  revision?: number | null,
): Promise<void> {
  const ids = new Set(transactionIds);
  for (const [key, transaction] of transactions) {
    if (!ids.has(transaction.transactionId)) continue;
    transactions.delete(key);
    removeObservedBatchForTransaction(transaction.transactionId);
    emitMutationState({
      ...transaction,
      state: "committed",
      ...(Number.isSafeInteger(revision) && Number(revision) > 0
        ? { committedRevision: Number(revision) }
        : {}),
      updatedAt: Date.now(),
    });
    deletePersistedTransaction(transaction.transactionId);
  }
  emitCanvasMutationOutboxStatus();
  await waitForCanvasMutationPersistence();
}

export function setCanvasMutationMountedScope(
  scopeInput: CanvasMutationScope | null,
): void {
  const scope = scopeInput ? normalizedScope(scopeInput) : null;
  mountedScopeKey = scope ? scopeKey(scope) : null;
}

export function confirmCanvasMutationsSatisfiedByGraph(input: {
  projectId: string;
  canvasId: string;
  graph: CanvasMutationGraph;
  revision?: number | null;
}): number {
  const scope = normalizedScope(input);
  if (!scope) return 0;
  const confirmed: string[] = [];
  for (const transaction of transactions.values()) {
    if (
      transaction.projectId !== scope.projectId
      || transaction.canvasId !== scope.canvasId
      || transaction.source !== "human"
      || !isCanvasMutationGraphSatisfied(transaction, input.graph)
    ) {
      continue;
    }
    confirmed.push(transaction.transactionId);
  }
  if (confirmed.length > 0) {
    void confirmCanvasMutationTransactions(confirmed, input.revision);
  }
  return confirmed.length;
}

function graphFromPort(graphPort: CanvasGraphPort): CanvasMutationGraph {
  return graphPort.read();
}

function applyRollbackToStore(
  transaction: CanvasMutationTransaction,
  graphPort: CanvasGraphPort = defaultCanvasGraphPort,
): CanvasMutationApplyResult {
  const current = graphFromPort(graphPort);
  if (mountedScopeKey !== null && mountedScopeKey !== scopeKey(transaction)) {
    return {
      graph: current,
      changed: false,
      conflicts: [{
        entity: "node",
        entityId: "__canvas_scope__",
        path: [transaction.projectId, transaction.canvasId],
      }],
    };
  }
  const rolledBack = applyCanvasMutationPatches(current, transaction.inverseOps);
  if (rolledBack.changed) runCanvasMutationGuard(transaction, () => {
    if (graphPort !== defaultCanvasGraphPort) {
      graphPort.apply(rolledBack.graph);
      return;
    }
    useCanvasStore.setState((state) => {
      const transformHistory = (graph: CanvasMutationGraph): CanvasMutationGraph => {
        const result = applyCanvasMutationPatches(graph, transaction.inverseOps);
        return result.changed ? result.graph : graph;
      };
      const selectedNodeId = state.selectedNodeId
        && rolledBack.graph.nodes.some((node) => node.id === state.selectedNodeId)
        ? state.selectedNodeId
        : null;
      const pendingFocusNodeId = state.pendingFocusNodeId
        && rolledBack.graph.nodes.some((node) => node.id === state.pendingFocusNodeId)
        ? state.pendingFocusNodeId
        : null;
      // 多节点聚焦同样只保留回滚后仍存在的成员；整批都没了就清空。
      const pendingFocusNodeIds = state.pendingFocusNodeIds
        ? state.pendingFocusNodeIds.filter((nodeId) =>
            rolledBack.graph.nodes.some((node) => node.id === nodeId))
        : null;
      return {
        nodes: rolledBack.graph.nodes,
        edges: rolledBack.graph.edges,
        selectedNodeId,
        pendingFocusNodeId,
        pendingFocusNodeIds: pendingFocusNodeIds && pendingFocusNodeIds.length > 0
          ? pendingFocusNodeIds
          : null,
        history: {
          past: state.history.past.map(transformHistory),
          future: state.history.future.map(transformHistory),
        },
      };
    });
  });
  return rolledBack;
}

function markCanvasMutationConflict(
  transaction: CanvasMutationTransaction,
  conflicts: CanvasMutationApplyResult["conflicts"],
): void {
  const conflict = setTransactionState(transaction, "conflicted");
  if (typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent(CANVAS_MUTATION_CONFLICT_EVENT, {
    detail: { transaction: conflict, conflicts },
  }));
}

function finalizeRolledBackTransaction(transaction: CanvasMutationTransaction): void {
  transactions.delete(commandKey(transaction, transaction.commandId));
  removeObservedBatchForTransaction(transaction.transactionId);
  emitMutationState({
    ...transaction,
    state: "rolled_back",
    updatedAt: Date.now(),
  });
  deletePersistedTransaction(transaction.transactionId);
  emitCanvasMutationOutboxStatus();
}

export function rollbackCanvasMutationsForTurn(
  turnIdInput: string | null | undefined,
): Array<CanvasMutationScope> {
  const turnId = nonEmptyString(turnIdInput);
  if (!turnId) return [];
  const scopes = new Map<string, CanvasMutationScope>();
  for (const [key, transaction] of [...transactions.entries()]) {
    if (transaction.turnId !== turnId) continue;
    scopes.set(scopeKey(transaction), {
      projectId: transaction.projectId,
      canvasId: transaction.canvasId,
    });
    const rollback = applyRollbackToStore(transaction);
    if (rollback.conflicts.length > 0) {
      markCanvasMutationConflict(transaction, rollback.conflicts);
      continue;
    }
    transactions.delete(key);
    finalizeRolledBackTransaction(transaction);
  }
  for (const [key, intent] of [...intents.entries()]) {
    if (intent.turnId !== turnId) continue;
    intents.delete(key);
    scopes.set(scopeKey(intent), { projectId: intent.projectId, canvasId: intent.canvasId });
  }
  return [...scopes.values()];
}

export function hasCanvasMutationCommand(commandIdInput: string): boolean {
  const commandId = nonEmptyString(commandIdInput);
  if (!commandId) return false;
  return [...transactions.values(), ...intents.values()]
    .some((transaction) => transaction.commandId === commandId);
}

export function settleCanvasMutationsForTurn(
  turnIdInput: string | null | undefined,
): Array<CanvasMutationScope> {
  const turnId = nonEmptyString(turnIdInput);
  if (!turnId) return [];
  const scopes = new Map<string, CanvasMutationScope>();
  for (const transaction of transactions.values()) {
    if (transaction.turnId !== turnId) continue;
    scopes.set(scopeKey(transaction), {
      projectId: transaction.projectId,
      canvasId: transaction.canvasId,
    });
  }
  for (const [key, intent] of [...intents.entries()]) {
    if (intent.turnId !== turnId) continue;
    intents.delete(key);
    scopes.set(scopeKey(intent), { projectId: intent.projectId, canvasId: intent.canvasId });
  }
  return [...scopes.values()];
}

async function recoverCanvasMutationScopeInternal(
  input: RecoverCanvasMutationScopeInput,
): Promise<CanvasMutationRecoveryResult> {
  const emptyResult = (): CanvasMutationRecoveryResult => ({
    committed: 0,
    recovered: 0,
    conflicted: 0,
    retryPending: false,
    authorityPending: 0,
  });
  const scope = normalizedScope(input);
  if (!scope) return emptyResult();
  if (input.isActive && !input.isActive()) return emptyResult();
  await outbox.prune();
  const persisted = (await outbox.listScope(scope))
    .filter((transaction) => !["committed", "rolled_back", "conflicted"].includes(transaction.state));
  if (persisted.length === 0) return emptyResult();
  persisted.forEach((transaction) => {
    transactions.set(commandKey(transaction, transaction.commandId), transaction);
  });

  let committed = 0;
  let conflicted = 0;
  let retryPending = false;
  let authorityPending = 0;
  let working = input.currentGraph;
  const recoverable: CanvasMutationTransaction[] = [];
  for (const transaction of persisted) {
    if (input.isActive && !input.isActive()) {
      return { committed, recovered: 0, conflicted, retryPending: true, authorityPending };
    }
    if (isCanvasMutationGraphSatisfied(transaction, input.remoteGraph)) {
      await confirmCanvasMutationTransactions([transaction.transactionId], input.revision);
      committed += 1;
      continue;
    }
    // Agent/workflow/skill commands are server-authoritative. A browser
    // refresh may query and confirm them, but must never turn a missing server
    // receipt into an ordinary snapshot PUT. Their existing command_id stays
    // queued for a later canvas.patch/reconnect reconciliation.
    if (transaction.source !== "human") {
      authorityPending += 1;
      if (!input.resolveAuthority) continue;
      let authority: "applied" | "pending" | "rejected";
      try {
        authority = await input.resolveAuthority(transaction);
      } catch {
        retryPending = true;
        continue;
      }
      if (authority === "applied") {
        acknowledgeCanvasMutation({
          projectId: transaction.projectId,
          canvasId: transaction.canvasId,
          commandId: transaction.commandId,
        });
        if (input.refreshRemoteGraph) {
          try {
            const refreshed = await input.refreshRemoteGraph();
            if (isCanvasMutationGraphSatisfied(transaction, refreshed)) {
              await confirmCanvasMutationTransactions(
                [transaction.transactionId],
                input.revision,
              );
              committed += 1;
            } else {
              retryPending = true;
            }
          } catch {
            retryPending = true;
          }
        } else {
          retryPending = true;
        }
        continue;
      }
      if (authority === "pending") {
        retryPending = true;
        continue;
      }
      const rollback = applyRollbackToStore(transaction);
      if (rollback.conflicts.length > 0) {
        conflicted += 1;
        markCanvasMutationConflict(transaction, rollback.conflicts);
      } else {
        finalizeRolledBackTransaction(transaction);
      }
      continue;
    }
    setTransactionState(transaction, "rebasing");
    const rebased = applyCanvasMutationPatches(working, transaction.forwardOps);
    if (rebased.conflicts.length > 0) {
      conflicted += 1;
      markCanvasMutationConflict(transaction, rebased.conflicts);
      continue;
    }
    working = rebased.graph;
    recoverable.push(transaction);
  }

  if (recoverable.length === 0) {
    await waitForCanvasMutationPersistence();
    return { committed, recovered: 0, conflicted, retryPending, authorityPending };
  }
  if (
    working.nodes !== input.currentGraph.nodes
    || working.edges !== input.currentGraph.edges
  ) {
    if (input.isActive && !input.isActive()) {
      return { committed, recovered: 0, conflicted, retryPending: true, authorityPending };
    }
    runCanvasMutationGuard(scope, () => input.applyGraph(working));
  }
  recoverable.forEach((transaction) => setTransactionState(transaction, "committing"));
  if (input.isActive && !input.isActive()) {
    return { committed, recovered: 0, conflicted, retryPending: true, authorityPending };
  }
  let saved = false;
  try {
    saved = await input.persist();
  } catch {
    saved = false;
  }
  if (!saved) {
    recoverable.forEach((transaction) => setTransactionState(transaction, "queued"));
    await waitForCanvasMutationPersistence();
    return {
      committed,
      recovered: 0,
      conflicted,
      retryPending: true,
      authorityPending,
    };
  }
  await confirmCanvasMutationTransactions(
    recoverable.map((transaction) => transaction.transactionId),
    input.revision,
  );
  return {
    committed,
    recovered: recoverable.length,
    conflicted,
    retryPending,
    authorityPending,
  };
}

export function recoverCanvasMutationScope(
  input: RecoverCanvasMutationScopeInput,
): Promise<CanvasMutationRecoveryResult> {
  const scope = normalizedScope(input);
  if (!scope) {
    return Promise.resolve({
      committed: 0,
      recovered: 0,
      conflicted: 0,
      retryPending: false,
      authorityPending: 0,
    });
  }
  const key = scopeKey(scope);
  const existing = recoveryPromises.get(key);
  if (existing) return existing;

  const run = async (): Promise<CanvasMutationRecoveryResult> => {
    const locks = typeof navigator !== "undefined"
      ? (navigator as Navigator & { locks?: LockManager }).locks
      : undefined;
    if (!locks) return await recoverCanvasMutationScopeInternal(input);
    const result = await locks.request(
      `village-canvas-mutation:${key}`,
      { mode: "exclusive", ifAvailable: true },
      async (lock) => {
        if (!lock) {
          return {
            committed: 0,
            recovered: 0,
            conflicted: 0,
            retryPending: true,
            authorityPending: 0,
          } satisfies CanvasMutationRecoveryResult;
        }
        return await recoverCanvasMutationScopeInternal(input);
      },
    );
    return result;
  };
  const promise = run().finally(() => {
    if (recoveryPromises.get(key) === promise) recoveryPromises.delete(key);
  });
  recoveryPromises.set(key, promise);
  return promise;
}

export function pendingCanvasMutationCount(): number {
  return transactions.size;
}

export async function waitForCanvasMutationPersistence(): Promise<void> {
  await persistenceTail.catch(() => undefined);
}

export async function setCanvasMutationOutboxForTests(
  nextOutbox: CanvasMutationOutbox,
): Promise<void> {
  await waitForCanvasMutationPersistence();
  outbox = nextOutbox;
}

export async function resetCanvasMutationKernelForTests(): Promise<void> {
  for (const batch of observedBatches.values()) {
    if (batch.timer) clearTimeout(batch.timer);
  }
  activeMutationDepth.clear();
  intents.clear();
  transactions.clear();
  observedBatches.clear();
  recoveryPromises.clear();
  mountedScopeKey = null;
  await waitForCanvasMutationPersistence();
  await outbox.clear();
}
