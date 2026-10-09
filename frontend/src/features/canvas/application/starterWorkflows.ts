// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import type { XYPosition } from '@xyflow/react';

import starterWorkflowDefinitions from '../../../../../src/novelvideo/assets/canvas_starter_workflows.json';
// 社区共识配方是**生成数据**，不是手写目录：scripts/maintenance/import_community_assets.py
// 从社区画布快照（4,933 个项目 / 591,892 个节点 / 759,028 条边）按固定规则推导出来，
// 每条都带 source_evidence（用过它的项目数、语料里的 combo 名）。后端
// `canvas_command_gateway._starter_workflows()` 读**同一个文件**，所以起步器面板里的 id
// 与 Agent 的 insert_starter_workflow 认得的 id 是同一份，不存在两套。
// 要改配方就改语料或改管线，别手改这个 JSON —— 管线有 --check 会比对。
import communityWorkflowDefinitions from '../../../../../src/novelvideo/assets/community_starter_workflows.json';
import type {
  CanvasEdge,
  CanvasNode,
  CanvasNodeData,
  CanvasNodeType,
} from '@/features/canvas/domain/canvasNodes';

import { canvasNodeFactory } from './canvasServices';

/**
 * 人工设计的 17 条骨架。刻意留成封闭联合类型 —— `starterWorkflowCatalog` 的呈现表按它
 * 建索引（`Record<BuiltinId, Presentation>`），封闭才能让「漏了一条」变成编译错误。
 */
export type CanvasBuiltinStarterWorkflowId =
  | 'story-continuity-film'
  | 'single-reference-video'
  | 'multi-reference-video'
  | 'refine-then-video'
  | 'storyboard-to-video'
  | 'video-composition'
  | 'panorama-shot-planning'
  | 'product-showcase-video'
  | 'first-last-frame-transition'
  | 'motion-reference-redraw'
  | 'script-voice-video'
  | 'music-driven-video'
  | 'video-analysis-recut'
  | 'image-to-3d-shot'
  | 'original-vertical-short-film'
  | 'product-advertisement';

/**
 * 目录里任何一条工作流的 id。开放集：社区配方跟着语料变，写死成联合类型就等于
 * 「导入管线每加一条配方都得改前端类型」—— 那就不是管线了。
 */
export type CanvasStarterWorkflowId = CanvasBuiltinStarterWorkflowId | (string & {});

export type CanvasStarterWorkflowIcon =
  | 'bookOpen'
  | 'film'
  | 'layers'
  | 'sparkles'
  | 'grid'
  | 'clapperboard'
  | 'compass'
  | 'music';

interface StarterNodeSpec {
  key: string;
  type: CanvasNodeType;
  offset: XYPosition;
  data: Partial<CanvasNodeData>;
}

interface StarterEdgeSpec {
  source: string;
  target: string;
}

export interface CanvasStarterWorkflowDefinition {
  id: CanvasStarterWorkflowId;
  title: string;
  description: string;
  icon: CanvasStarterWorkflowIcon;
  template_kind?: string;
  delivery_level?: 'idea' | 'storyboard' | 'shot_draft' | 'media_draft' | 'final_film';
  required_inputs?: readonly string[];
  optional_inputs?: readonly string[];
  required_roles?: readonly string[];
  outputs?: readonly string[];
  does_not_produce?: readonly string[];
  quality_gates?: readonly string[];
  /** 社区配方才有的来源证据（内置 17 条没有）。 */
  source_evidence?: {
    corpus?: string;
    combo?: string;
    projects?: number;
    node_count?: number;
  };
  nodes: readonly StarterNodeSpec[];
  edges: readonly StarterEdgeSpec[];
}

export interface CanvasStarterWorkflowCreation {
  title: string;
  nodeIds: string[];
  nodes: CanvasNode[];
  edges: CanvasEdge[];
}

/** 内置 17 条。顺序即呈现顺序，`starterWorkflows.test.ts` 钉着它。 */
export const CANVAS_BUILTIN_STARTER_WORKFLOWS =
  starterWorkflowDefinitions as unknown as readonly CanvasStarterWorkflowDefinition[];

/** 社区配方（生成的），按语料里的项目数从多到少。 */
export const CANVAS_COMMUNITY_STARTER_WORKFLOWS =
  communityWorkflowDefinitions as unknown as readonly CanvasStarterWorkflowDefinition[];

/**
 * 起步器面板看到的全表：生成的社区配方排前面（它们是「大多数人真这么做」），
 * 人工骨架排后面（它们是「我们认为你应该这么做」）。两者 id 不可能撞：
 * 内置全是 `xxx-yyy`，社区配方一律 `community-` 开头。
 */
export const CANVAS_STARTER_WORKFLOWS: readonly CanvasStarterWorkflowDefinition[] = [
  ...CANVAS_COMMUNITY_STARTER_WORKFLOWS,
  ...CANVAS_BUILTIN_STARTER_WORKFLOWS,
];

export function getCanvasStarterWorkflow(
  id: CanvasStarterWorkflowId,
): CanvasStarterWorkflowDefinition | null {
  return CANVAS_STARTER_WORKFLOWS.find((workflow) => workflow.id === id) ?? null;
}

/** Create a fresh, self-contained node graph at the supplied canvas position. */
export function createCanvasStarterWorkflow(
  id: CanvasStarterWorkflowId,
  origin: XYPosition,
): CanvasStarterWorkflowCreation | null {
  const workflow = getCanvasStarterWorkflow(id);
  if (!workflow) {
    return null;
  }

  const nodes = workflow.nodes.map((spec) =>
    canvasNodeFactory.createNode(
      spec.type,
      { x: origin.x + spec.offset.x, y: origin.y + spec.offset.y },
      { ...spec.data },
    ),
  );
  const nodeByKey = new Map(workflow.nodes.map((spec, index) => [spec.key, nodes[index]] as const));
  const edges = workflow.edges.flatMap((edgeSpec) => {
    const source = nodeByKey.get(edgeSpec.source);
    const target = nodeByKey.get(edgeSpec.target);
    if (!source || !target) {
      return [];
    }
    return [{
      id: `starter-${source.id}-${target.id}`,
      source: source.id,
      target: target.id,
      sourceHandle: 'source',
      targetHandle: 'target',
      type: 'disconnectableEdge',
    } satisfies CanvasEdge];
  });

  return {
    title: workflow.title,
    nodeIds: nodes.map((node) => node.id),
    nodes,
    edges,
  };
}
