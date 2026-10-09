// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab

import type { CanvasEdge, CanvasNode } from "@/features/canvas/domain/canvasNodes";

export type CanvasMutationSource =
  | "human"
  | "agent"
  | "workflow"
  | "skill"
  | "system";

export type CanvasMutationState =
  | "prepared"
  | "local_applied"
  | "queued"
  | "server_acknowledged"
  | "committing"
  | "rebasing"
  | "committed"
  | "rolled_back"
  | "conflicted";

export type CanvasMutationOutboxStatus = "healthy" | "degraded";
export type CanvasMutationOutboxStorage = "indexeddb" | "memory";

export type CanvasMutationOperationType =
  | "create_node"
  | "update_node"
  | "move_node"
  | "connect_nodes"
  | "remove_edge"
  | "delete_node"
  | "update_edge";

export interface CanvasMutationScope {
  projectId: string;
  canvasId: string;
}

export interface CanvasMutationGraph {
  nodes: CanvasNode[];
  edges: CanvasEdge[];
}

export interface CanvasGraphPort {
  read(): CanvasMutationGraph;
  apply(graph: CanvasMutationGraph): void;
}

export interface CanvasMutationOutboxStatusSnapshot {
  status: CanvasMutationOutboxStatus;
  storage: CanvasMutationOutboxStorage;
  pendingCount: number;
  lastError?: string;
  updatedAt: number;
}

export interface CanvasMutationMetrics {
  source: CanvasMutationSource;
  projectId: string;
  canvasId: string;
  commandId: string;
  transactionId?: string;
  nodeCount: number;
  edgeCount: number;
  operationCount: number;
  diffDurationMs: number;
  largeCanvas: boolean;
  slowDiff: boolean;
  createdAt: number;
}

export interface CanvasMutationValueChange {
  path: string[];
  beforeExists: boolean;
  before: unknown;
  afterExists: boolean;
  after: unknown;
  beforeFingerprint: string;
  afterFingerprint: string;
}

export interface CanvasMutationEntityPatch {
  type: CanvasMutationOperationType;
  entity: "node" | "edge";
  entityId: string;
  before: CanvasNode | CanvasEdge | null;
  after: CanvasNode | CanvasEdge | null;
  beforeFingerprint: string;
  afterFingerprint: string;
  changes: CanvasMutationValueChange[];
}

export interface CanvasMutationTransaction extends CanvasMutationScope {
  schema: "canvas_mutation_transaction.v1";
  transactionId: string;
  commandId: string;
  turnId?: string;
  baseRevision: number | null;
  source: CanvasMutationSource;
  forwardOps: CanvasMutationEntityPatch[];
  inverseOps: CanvasMutationEntityPatch[];
  affectedNodeIds: string[];
  affectedEdgeIds: string[];
  touchedFields: Record<string, string[]>;
  state: CanvasMutationState;
  acknowledgedRevision?: number;
  committedRevision?: number;
  createdAt: number;
  updatedAt: number;
  commandPayload?: unknown;
}

export interface CanvasMutationApplyResult {
  graph: CanvasMutationGraph;
  changed: boolean;
  conflicts: Array<{
    entity: "node" | "edge";
    entityId: string;
    path?: string[];
  }>;
}
