// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { isUpstreamConnectionAllowed } from '@/features/canvas/domain/nodeRegistry';
import {
  DefaultGraphContentResolver,
  joinUpstreamText,
} from '@/features/canvas/application/graphContentResolver';

import {
  CANVAS_BUILTIN_STARTER_WORKFLOWS,
  CANVAS_STARTER_WORKFLOWS,
  createCanvasStarterWorkflow,
} from './starterWorkflows';
import { recommendCanvasStarterWorkflows, starterQuickStartWorkflows } from './starterWorkflowCatalog';
import { useCanvasStore } from '@/stores/canvasStore';

describe('canvas starter workflows', () => {
  beforeEach(() => {
    useCanvasStore.setState({
      nodes: [],
      edges: [],
      selectedNodeId: null,
      history: { past: [], future: [] },
      userEditsSinceHydrate: 0,
      lastMutationSource: null,
      pendingClearIntent: false,
    });
  });

  afterEach(() => {
    useCanvasStore.setState({
      nodes: [],
      edges: [],
      selectedNodeId: null,
      history: { past: [], future: [] },
      userEditsSinceHydrate: 0,
      lastMutationSource: null,
      pendingClearIntent: false,
    });
  });

  it('creates independent, fully connected editable graphs for every starter', () => {
    for (const definition of CANVAS_STARTER_WORKFLOWS) {
      const first = createCanvasStarterWorkflow(definition.id, { x: 100, y: 200 });
      const second = createCanvasStarterWorkflow(definition.id, { x: 100, y: 200 });

      expect(first).not.toBeNull();
      expect(second).not.toBeNull();
      expect(first!.nodes).toHaveLength(definition.nodes.length);
      expect(first!.edges).toHaveLength(definition.edges.length);
      expect(new Set(first!.nodeIds)).toHaveLength(first!.nodeIds.length);
      expect(first!.nodeIds.some((id) => second!.nodeIds.includes(id))).toBe(false);

      const nodesById = new Map(first!.nodes.map((node) => [node.id, node] as const));
      for (const edge of first!.edges) {
        const source = nodesById.get(edge.source);
        const target = nodesById.get(edge.target);
        expect(source).toBeDefined();
        expect(target).toBeDefined();
        expect(isUpstreamConnectionAllowed(source!.type, target!.type)).toBe(true);
      }
    }
  });

  it('inserts every starter through the store without dropping a node or an edge', () => {
    // createCanvasStarterWorkflow 只证明「能拼出一张图」；用户点卡片走的是 store。
    // store 会额外校验两端的 handle 和建边白名单，于是「JSON 里的边在画布上根本不存在」
    // 这类数据缺陷只在用户路径上炸。曾经 original-vertical-short-film /
    // product-advertisement 把 scriptNode 连到没有输入口的 uploadNode，卡片点了就报
    // 「工作流初始化失败，请重试」—— 这条测试就是为了让那种骨架进不了主干。
    for (const definition of CANVAS_STARTER_WORKFLOWS) {
      useCanvasStore.setState({
        nodes: [],
        edges: [],
        selectedNodeId: null,
        history: { past: [], future: [] },
        userEditsSinceHydrate: 0,
        lastMutationSource: null,
        pendingClearIntent: false,
      });

      const inserted = useCanvasStore
        .getState()
        .addStarterWorkflow(definition.id, { x: 0, y: 0 });

      expect(inserted, `starter ${definition.id} must insert through the store`).not.toBeNull();
      expect(useCanvasStore.getState().nodes).toHaveLength(definition.nodes.length);
      expect(useCanvasStore.getState().edges).toHaveLength(definition.edges.length);
    }
  });

  it('merges the generated community recipes ahead of the builtin scaffolds', () => {
    // 社区配方是 import_community_assets.py 从语料生成的。这里只钉住「在目录里、
    // 排在内置前面、每条都能插出一张图」—— 具体是哪几条由语料决定，不该写死。
    const community = CANVAS_STARTER_WORKFLOWS.filter((workflow) => workflow.id.startsWith('community-'));
    expect(community.length).toBeGreaterThan(0);
    expect(CANVAS_STARTER_WORKFLOWS.slice(0, community.length).map((workflow) => workflow.id))
      .toEqual(community.map((workflow) => workflow.id));
    expect(CANVAS_STARTER_WORKFLOWS).toHaveLength(
      community.length + CANVAS_BUILTIN_STARTER_WORKFLOWS.length,
    );

    for (const recipe of community) {
      // 每条配方都得带着「多少人真这么搭」的证据 —— 没有证据的配方不该出现在这里。
      expect(recipe.source_evidence?.projects).toBeGreaterThan(0);
      expect(String(recipe.source_evidence?.combo ?? '')).not.toBe('');
      // 只复刻结构，不产出媒体：卡片上不能承诺一次生成，图里也不许有合成/导出节点。
      expect([...(recipe.outputs ?? [])]).toEqual(['editable_node_graph']);
      expect(recipe.nodes.map((node) => node.type)).not.toContain('videoComposeNode');
      // 每个非视频输入节点都必须连到视频节点上（不许有孤立节点）。
      const videoKeys = new Set(
        recipe.nodes.filter((node) => node.type === 'videoNode').map((node) => node.key),
      );
      const connected = new Set(recipe.edges.map((edge) => edge.source));
      for (const node of recipe.nodes) {
        if (videoKeys.has(node.key)) continue;
        expect(connected.has(node.key)).toBe(true);
      }
    }
  });

  it('keeps a useful no-asset workflow range instead of encoding remote media', () => {
    // 人工骨架的顺序，与 `canvas_starter_workflows.json` 里的排列一一对应。
    expect(CANVAS_BUILTIN_STARTER_WORKFLOWS.map((workflow) => workflow.id)).toEqual([
      'story-continuity-film',
      'single-reference-video',
      'multi-reference-video',
      'refine-then-video',
      'storyboard-to-video',
      'video-composition',
      'panorama-shot-planning',
      'product-showcase-video',
      'first-last-frame-transition',
      'motion-reference-redraw',
      'script-voice-video',
      'music-driven-video',
      'video-analysis-recut',
      'image-to-3d-shot',
      'original-vertical-short-film',
      'product-advertisement',
    ]);
    expect(JSON.stringify(CANVAS_STARTER_WORKFLOWS)).not.toContain('http');
  });

  it('maps production intent to capability-backed template recommendations', () => {
    expect(recommendCanvasStarterWorkflows('做一个香水产品广告展示')[0]?.id)
      .toBe('product-showcase-video');
    expect(recommendCanvasStarterWorkflows('首尾帧转场')[0]?.id)
      .toBe('first-last-frame-transition');
    expect(recommendCanvasStarterWorkflows('保留源视频动作进行重绘')[0]?.id)
      .toBe('motion-reference-redraw');
    expect(recommendCanvasStarterWorkflows('做一个带旁白配音的短片')[0]?.id)
      .toBe('script-voice-video');
    expect(recommendCanvasStarterWorkflows('解析视频以后再创作')[0]?.id)
      .toBe('video-analysis-recut');
    expect(recommendCanvasStarterWorkflows('图片转3D空间取景')[0]?.id)
      .toBe('image-to-3d-shot');
    expect(recommendCanvasStarterWorkflows('没有任何关键词')).toEqual([]);
  });

  it('keeps the empty-canvas quick-start cards pointed at real workflows', () => {
    // 首启卡片直接列出这几条；id 写错会让空画布上出现一个点不动的卡片，
    // 而这恰恰是这轮要消灭的东西。顺序也钉住（卡片按此顺序排）。
    // 刻意**不**把社区配方放进快捷卡片：它们是结构参考，不是「一键开始做片子」。
    expect(starterQuickStartWorkflows().map((workflow) => workflow.id)).toEqual([
      'original-vertical-short-film',
      'storyboard-to-video',
      'single-reference-video',
      'video-composition',
    ]);
    for (const workflow of starterQuickStartWorkflows()) {
      expect(CANVAS_STARTER_WORKFLOWS).toContain(workflow);
      expect(workflow.title.length).toBeGreaterThan(0);
      expect(workflow.nodes.length).toBeGreaterThan(0);
    }
  });

  it('never lets a structure recipe win an intent-shaped recommendation', () => {
    // 社区配方按「节点怎么搭」聚类，回答不了「我要做什么片子」。它一旦被
    // recommendCanvasStarterWorkflows 选中，就会把真正对得上意图的内置路线挤出前三。
    for (const request of [
      '做一个香水产品广告展示',
      '首尾帧转场',
      '保留源视频动作进行重绘',
      '做一个带旁白配音的短片',
      '图片转3D空间取景',
    ]) {
      expect(recommendCanvasStarterWorkflows(request).map((workflow) => workflow.id))
        .not.toContain(expect.stringMatching(/^community-/));
    }
  });

  it('ships the一期 original and product seven-node scaffolds', () => {
    for (const id of ['original-vertical-short-film', 'product-advertisement'] as const) {
      const workflow = CANVAS_STARTER_WORKFLOWS.find((definition) => definition.id === id);
      expect(workflow?.nodes.map((node) => node.type)).toEqual([
        'textAnnotationNode',
        'scriptNode',
        'uploadNode',
        'uploadNode',
        'storyboardGenNode',
        'imageGenNode',
        'videoNode',
      ]);
      // 两个参考位是上传节点，没有输入口：脚本只喂分镜，不喂参考位。
      // 一旦这里重新出现 script -> upload，store 会整条骨架拒收。
      expect(workflow?.edges).toHaveLength(6);
      const referenceKey = id === 'product-advertisement' ? 'product' : 'character';
      expect(workflow?.edges.map((edge) => `${edge.source}->${edge.target}`)).toEqual([
        'creative->script',
        'script->storyboard',
        `${referenceKey}->storyboard`,
        'scene->storyboard',
        'storyboard->image',
        'image->video',
      ]);
      const created = createCanvasStarterWorkflow(id, { x: 0, y: 0 });
      expect(created?.nodes).toHaveLength(7);
      expect(created?.edges).toHaveLength(6);
    }
  });

  it('ships a director brief through storyboard and video without creating remote media', () => {
    const workflow = CANVAS_STARTER_WORKFLOWS.find(
      (definition) => definition.id === 'story-continuity-film',
    );

    expect(workflow).toBeDefined();
    expect(workflow?.title).toBe('故事连续性起步骨架');
    expect(workflow?.nodes.map((node) => node.key)).toEqual([
      'director-brief',
      'character-reference',
      'scene-reference',
      'storyboard',
      'video',
    ]);
    expect(String(workflow?.nodes[0]?.data.content)).toContain('角色身份');
    expect(String(workflow?.nodes[4]?.data.prompt)).toContain('连续性');
    expect(workflow?.edges).toHaveLength(6);

    const created = createCanvasStarterWorkflow('story-continuity-film', { x: 0, y: 0 });
    const video = created?.nodes.find(
      (node) => node.data.displayName === '视频生成 · 延续人物与场景',
    );
    expect(video).toBeDefined();
    const contents = new DefaultGraphContentResolver().collectInputContents(
      video!.id,
      created!.nodes,
      created!.edges,
    );
    expect(joinUpstreamText(contents)).toContain('角色身份');
  });

  it('inserts and removes a whole workflow in one undo step', () => {
    const result = useCanvasStore
      .getState()
      .addStarterWorkflow('multi-reference-video', { x: 24, y: 48 });

    expect(result?.nodeIds).toHaveLength(4);
    expect(useCanvasStore.getState().nodes).toHaveLength(4);
    expect(useCanvasStore.getState().edges).toHaveLength(3);
    expect(useCanvasStore.getState().history.past).toHaveLength(1);

    useCanvasStore.getState().undo();
    expect(useCanvasStore.getState().nodes).toHaveLength(0);
    expect(useCanvasStore.getState().edges).toHaveLength(0);

    useCanvasStore.getState().redo();
    expect(useCanvasStore.getState().nodes).toHaveLength(4);
    expect(useCanvasStore.getState().edges).toHaveLength(3);
  });
});
