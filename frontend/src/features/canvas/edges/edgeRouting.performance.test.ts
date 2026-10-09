// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { existsSync, mkdirSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { performance } from 'node:perf_hooks';
import { Position } from '@xyflow/react';
import { describe, expect, it } from 'vitest';

import type { CanvasNode } from '@/features/canvas/domain/canvasNodes';
import { buildOrthogonalRoute, selectCanvasRoutingSnapshot } from './edgeRouting';

type BenchmarkCase = {
  nodeCount: number;
  edgeCount: number;
  snapshotBuildMs: number;
  routeMs: number;
  routeP95Ms: number;
  routesPerSecond: number;
};

const benchmarkMode = process.env.CANVAS_ROUTING_BENCHMARK_MODE ?? 'smart';
const useSnapshot = benchmarkMode !== 'no-snapshot';
const useSmartAvoidance = benchmarkMode !== 'dragging';

const NODE_COUNTS = [100, 300, 500] as const;
const EDGE_COUNTS = [100, 300, 1000] as const;
const WARMUP_ROUNDS = 2;
const MEASURE_ROUNDS = 8;

function benchmarkNode(id: string, index: number): CanvasNode {
  const column = index % 25;
  const row = Math.floor(index / 25);
  return {
    id,
    type: 'textAnnotationNode',
    position: { x: column * 280, y: row * 250 },
    measured: { width: 220, height: 180 },
    data: {},
  } as CanvasNode;
}

function benchmarkEdges(nodes: CanvasNode[], count: number) {
  const edges: Array<{
    source: CanvasNode;
    target: CanvasNode;
  }> = [];
  for (let index = 0; index < count; index += 1) {
    const sourceIndex = index % Math.max(1, nodes.length - 2);
    const targetIndex = Math.min(
      nodes.length - 1,
      sourceIndex + 1 + ((index * 17) % Math.max(1, nodes.length - sourceIndex - 1)),
    );
    const source = nodes[sourceIndex];
    const target = nodes[targetIndex === sourceIndex ? (sourceIndex + 1) % nodes.length : targetIndex];
    edges.push({ source, target });
  }
  return edges;
}

function measureCase(nodeCount: number, edgeCount: number): BenchmarkCase {
  const nodes = Array.from({ length: nodeCount }, (_, index) => benchmarkNode(`node-${index}`, index));
  const edges = benchmarkEdges(nodes, edgeCount);
  const snapshotStart = performance.now();
  const snapshot = useSnapshot ? selectCanvasRoutingSnapshot(nodes) : undefined;
  const snapshotBuildMs = performance.now() - snapshotStart;

  for (let round = 0; round < WARMUP_ROUNDS; round += 1) {
    for (const { source, target } of edges) {
      buildOrthogonalRoute({
        sourceId: source.id,
        targetId: target.id,
        sourceX: source.position.x + 220,
        sourceY: source.position.y + 90,
        sourcePosition: Position.Right,
        targetX: target.position.x,
        targetY: target.position.y + 90,
        targetPosition: Position.Left,
        nodes: useSnapshot ? [] : nodes,
        smartAvoidance: useSmartAvoidance,
        routingSnapshot: snapshot,
      });
    }
  }

  const roundDurations: number[] = [];
  for (let round = 0; round < MEASURE_ROUNDS; round += 1) {
    const start = performance.now();
    for (const { source, target } of edges) {
      const route = buildOrthogonalRoute({
        sourceId: source.id,
        targetId: target.id,
        sourceX: source.position.x + 220,
        sourceY: source.position.y + 90,
        sourcePosition: Position.Right,
        targetX: target.position.x,
        targetY: target.position.y + 90,
        targetPosition: Position.Left,
        nodes: useSnapshot ? [] : nodes,
        smartAvoidance: useSmartAvoidance,
        routingSnapshot: snapshot,
      });
      if (!route.path.startsWith('M ')) {
        throw new Error(`empty route for ${source.id} -> ${target.id}`);
      }
    }
    roundDurations.push(performance.now() - start);
  }

  const sorted = [...roundDurations].sort((left, right) => left - right);
  const routeMs = roundDurations.reduce((sum, value) => sum + value, 0) / roundDurations.length;
  const routeP95Ms = sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * 0.95))] ?? routeMs;
  return {
    nodeCount,
    edgeCount,
    snapshotBuildMs: Number(snapshotBuildMs.toFixed(3)),
    routeMs: Number(routeMs.toFixed(3)),
    routeP95Ms: Number(routeP95Ms.toFixed(3)),
    routesPerSecond: Number(((edgeCount * 1000) / Math.max(routeMs, 0.001)).toFixed(1)),
  };
}

function writeBenchmarkArtifact(cases: BenchmarkCase[]): void {
  const output = process.env.CANVAS_ROUTING_BENCHMARK_OUTPUT;
  if (!output) return;
  const absolute = resolve(output);
  if (!existsSync(dirname(absolute))) mkdirSync(dirname(absolute), { recursive: true });
  writeFileSync(
    absolute,
    JSON.stringify(
      {
        generatedAt: new Date().toISOString(),
        nodeCounts: NODE_COUNTS,
        edgeCounts: EDGE_COUNTS,
        warmupRounds: WARMUP_ROUNDS,
        measureRounds: MEASURE_ROUNDS,
        cases,
      },
      null,
      2,
    ) + '\n',
    'utf8',
  );
}

describe('smartOrthogonal routing performance contract', () => {
  it('routes the supported large-canvas matrix and records a reproducible baseline', () => {
    const cases = NODE_COUNTS.flatMap((nodeCount) =>
      EDGE_COUNTS.map((edgeCount) => measureCase(nodeCount, edgeCount)),
    );
    writeBenchmarkArtifact(cases);

    for (const result of cases) {
      expect(result.routeMs).toBeGreaterThanOrEqual(0);
      expect(result.routeP95Ms).toBeGreaterThanOrEqual(result.routeMs * 0.8);
      expect(result.routesPerSecond).toBeGreaterThan(0);
    }
  }, 120_000);
});
