// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { beforeEach, describe, expect, it } from 'vitest';

import {
  CANVAS_NODE_TYPES,
  isImageGenNode,
  isStoryboardGroupNode,
  type VideoStoryRow,
} from '@/features/canvas/domain/canvasNodes';
import {
  collapseStoryboardMembers,
  resetStoryboardSettle,
  storyboardBatchSettled,
} from '@/features/canvas/nodes/script/storyboardSettle';
import {
  VIDEO_STORY_LINKED_GROUP_FIELD,
  VIDEO_STORY_SHOT_EDGE_ROLE,
  planVideoStoryScatter,
  regenerateVideoStoryShotGroup,
  scatterVideoStoryShots,
  videoStoryOwnerNodeIdForGroup,
} from '@/features/canvas/nodes/videoStory/scatterVideoStoryShots';
import { useCanvasStore } from '@/stores/canvasStore';

/**
 * 「分镜表 → 镜头节点」落盘（T-014 / B）。
 *
 * 这层锁的是**生命周期**，不是公式（公式在 video-story-shots.test.ts）：
 * - 首次散开 → 成组 + 血缘边 + 源节点记下组 id；
 * - 重复点不重复派生（幂等）；
 * - 出图期间成员散着（隐藏成员不挂载 → 不会提交），落定后自动收回并回写组 id；
 * - 分镜行整批换过就重建，不把新提示词写到旧行号上。
 */

const SOURCE_ID = 'video-story-node';
const MODEL = 'direct/image-msfkiqdq-ed3hhy';
const SOURCE_SIZE = { width: 720, height: 360 };

function rows(): VideoStoryRow[] {
  return [
    {
      shotNumber: 1,
      startTime: '00:00',
      endTime: '00:03',
      visualDescription: '雨夜祠堂外景',
      imagePrompt: '推近祠堂门环',
      shotSize: '特写',
      keyframeUrl: '/static/projects/p1/frames/f1.jpg',
    },
    { shotNumber: 2, visualDescription: '阿雀推门而入' },
  ];
}

/** 行数相同但行标识整批换过 —— 判定必须落到「重建」而不是「原地重跑」。 */
function renamedRows(): VideoStoryRow[] {
  return [
    { shotNumber: 11, imagePrompt: '换过的第一镜' },
    { shotNumber: 12, imagePrompt: '换过的第二镜' },
  ];
}

function threeRows(): VideoStoryRow[] {
  return [...rows(), { shotNumber: 3, imagePrompt: '收镜' }];
}

function seedSourceNode(seedRows: VideoStoryRow[] = rows()) {
  useCanvasStore.getState().setCanvasData(
    [
      {
        id: SOURCE_ID,
        type: CANVAS_NODE_TYPES.videoStory,
        position: { x: 0, y: 0 },
        style: { width: SOURCE_SIZE.width, height: SOURCE_SIZE.height },
        data: { sourceVideoUrl: '/static/p1/source.mp4', rows: seedRows },
      },
    ] as never,
    [],
  );
}

function imageNodes() {
  return useCanvasStore.getState().nodes.filter(isImageGenNode);
}

function sourceNode() {
  return useCanvasStore.getState().nodes.find((node) => node.id === SOURCE_ID);
}

/**
 * 换一批分镜行 —— 只动这一个节点的 data。
 * 刻意不用 `setCanvasData`：那会整块重置画布，把上一轮派生的镜头节点一并抹掉，
 * 这样测出来的「重建」是假的（重新播种 ≠ 行变过）。
 */
function setRows(nextRows: VideoStoryRow[]) {
  useCanvasStore.getState().updateNodeData(SOURCE_ID, { rows: nextRows });
}

function scatter(options: {
  generateImages?: boolean;
  model?: string | null;
  aspectRatio?: string;
} = {}) {
  return scatterVideoStoryShots({
    videoStoryNodeId: SOURCE_ID,
    model: options.model === undefined ? MODEL : options.model,
    aspectRatio: options.aspectRatio,
    generateImages: options.generateImages,
  });
}

/** 把某镜标记成「出好图了」，用来模拟一次成功的生成。 */
function markGenerated(nodeId: string, url = 'shot.png') {
  useCanvasStore.getState().updateNodeData(nodeId, { imageUrl: url });
}

let pendingMembers: string[] = [];

describe('散开分镜表 · 派生镜头节点', () => {
  beforeEach(() => {
    resetStoryboardSettle();
    pendingMembers = [];
    seedSourceNode();
  });

  it('逐镜建图片节点、成组、连线，并在源节点记录组 id', () => {
    const result = scatter({});
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.mode).toBe('created');
    expect(result.nodeIds).toHaveLength(2);
    expect(result.groupLabel).toBe('镜头表 · 2 镜');

    const state = useCanvasStore.getState();
    expect(imageNodes()).toHaveLength(2);
    // 提示词取「图像生成提示词」，缺失回落「画面描述」。
    expect(imageNodes()[0].data.prompt).toBe('推近祠堂门环');
    expect(imageNodes()[1].data.prompt).toBe('阿雀推门而入');
    expect(imageNodes().every((node) => node.data.model === MODEL)).toBe(true);

    // 关键帧进**参考图**。写进 previewImageUrl 会被 storyboardMemberNeedsImage 当成
    // 「已经有图了」→ 出图被跳过、组提前收拢，所以这里是硬约束。
    expect(imageNodes()[0].data.referenceImageUrl).toBe('/static/projects/p1/frames/f1.jpg');
    expect(imageNodes()[0].data.previewImageUrl ?? null).toBeNull();
    expect(imageNodes()[1].data.referenceImageUrl).toBeNull();

    // 行标识用镜号；镜头语言字段随节点留档。
    expect(imageNodes()[0].data.videoStoryRowKey).toBe('1');
    expect(imageNodes()[1].data.videoStoryRowKey).toBe('2');
    expect(imageNodes()[0].data.shot_size).toBe('特写');

    const group = state.nodes.find(isStoryboardGroupNode);
    expect(group?.id).toBe(result.groupId);
    expect(group?.data.label).toBe('镜头表 · 2 镜');

    // 分组容器不参与连线（groupNode 两个 handle 都是 false），血缘按逐镜边表达。
    // 并组后 store 会把成员两端的边改指到组节点，所以查血缘标记而不是目标。
    const shotEdges = state.edges.filter((edge) => edge.data?.role === VIDEO_STORY_SHOT_EDGE_ROLE);
    expect(shotEdges).toHaveLength(2);
    expect(state.nodes.filter((node) => node.parentId === result.groupId)).toHaveLength(2);

    expect(sourceNode()?.data[VIDEO_STORY_LINKED_GROUP_FIELD]).toBe(result.groupId);
    expect(sourceNode()?.data.imageGenConfig).toMatchObject({ model: MODEL, aspectRatio: '16:9' });
  });

  it('镜头组落在源节点右侧，且行序 = 宫格读取顺序', () => {
    const result = scatter({});
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    const state = useCanvasStore.getState();
    const group = state.nodes.find((node) => node.id === result.groupId);
    expect(group?.position.x).toBeGreaterThan(SOURCE_SIZE.width);
    const members = result.nodeIds.map((id) => state.nodes.find((node) => node.id === id));
    expect(members[0]?.position.y).toBe(members[1]?.position.y);
    expect(members[1]?.position.x).toBeGreaterThan(members[0]?.position.x ?? 0);
  });

  it('重复点「散开到画布」不会重复派生（幂等）', () => {
    const first = scatter({});
    expect(first.ok).toBe(true);
    if (!first.ok) return;

    const second = scatter({});
    expect(second.ok).toBe(true);
    if (!second.ok) return;
    expect(second.mode).toBe('rearmed');
    expect(second.nodeIds.sort()).toEqual(first.nodeIds.sort());
    expect(imageNodes()).toHaveLength(2);
    expect(useCanvasStore.getState().nodes.filter(isStoryboardGroupNode)).toHaveLength(1);
  });

  it('行标识整批换过（数量相同）→ 重建，而不是把新提示词写到旧行号上', () => {
    const first = scatter({});
    expect(first.ok).toBe(true);
    if (!first.ok) return;

    setRows(renamedRows());
    const second = scatter({});
    expect(second.ok).toBe(true);
    if (!second.ok) return;
    expect(second.mode).toBe('rebuilt');
    const state = useCanvasStore.getState();
    expect(state.nodes.some((node) => node.id === first.groupId)).toBe(false);
    expect(imageNodes()).toHaveLength(2);
    expect(imageNodes().map((node) => node.data.videoStoryRowKey).sort()).toEqual(['11', '12']);
    expect(imageNodes().map((node) => node.data.prompt).sort()).toEqual([
      '换过的第一镜',
      '换过的第二镜',
    ]);
    expect(sourceNode()?.data[VIDEO_STORY_LINKED_GROUP_FIELD]).toBe(second.groupId);
  });

  it('行数变了 → 重建整组，避免行与图错位', () => {
    const first = scatter({});
    expect(first.ok).toBe(true);
    if (!first.ok) return;

    setRows(threeRows());
    const second = scatter({});
    expect(second.ok).toBe(true);
    if (!second.ok) return;
    expect(second.mode).toBe('rebuilt');
    expect(second.nodeIds).toHaveLength(3);
    expect(useCanvasStore.getState().nodes.some((node) => node.id === first.groupId)).toBe(false);
    expect(imageNodes()).toHaveLength(3);
  });

  it('重建不会把新组顶到旧组下面（旧组在算落位前就该消失）', () => {
    const first = scatter({});
    expect(first.ok).toBe(true);
    if (!first.ok) return;
    const firstY = useCanvasStore
      .getState()
      .nodes.find((node) => node.id === first.groupId)?.position.y;

    setRows(threeRows());
    const second = scatter({});
    expect(second.ok).toBe(true);
    if (!second.ok) return;
    const secondY = useCanvasStore
      .getState()
      .nodes.find((node) => node.id === second.groupId)?.position.y;
    // 旧组 1600+ px 高，若被当成障碍物，第二次会整整下移一个组高（真机上漂到过 4600px）。
    expect(Math.abs((secondY ?? 0) - (firstY ?? 0))).toBeLessThan(100);
  });

  it('generateImages 为真时先散开出图、全部落定后才并回镜头组并回写组 id', () => {
    const loud = scatter({ generateImages: true });
    expect(loud.ok).toBe(true);
    if (!loud.ok) return;
    expect(loud.armed).toBe(2);
    expect(loud.groupId).toBeNull();
    pendingMembers = loud.nodeIds;

    // 出图阶段：成员是可挂载的普通节点（隐藏成员不挂载 → 不会提交），还没有组。
    const during = useCanvasStore.getState();
    expect(during.nodes.filter(isStoryboardGroupNode)).toHaveLength(0);
    const shots = imageNodes();
    expect(shots.every((node) => node.data.canvas_auto_generate_once === true)).toBe(true);
    expect(shots.every((node) => node.hidden !== true)).toBe(true);
    expect(shots.every((node) => !node.parentId)).toBe(true);
    // 组 id 先清掉：留着一个已经散掉的旧组 id 会让下次进来取到空组。
    expect(sourceNode()?.data[VIDEO_STORY_LINKED_GROUP_FIELD]).toBeNull();

    // 只出一张还不收：还有一张没落定。
    markGenerated(loud.nodeIds[0]);
    expect(storyboardBatchSettled(pendingMembers)).toBe(false);
    expect(useCanvasStore.getState().nodes.filter(isStoryboardGroupNode)).toHaveLength(0);

    markGenerated(loud.nodeIds[1], 'shot-2.png');
    const groupId = collapseStoryboardMembers({
      scriptNodeId: null,
      ownerNodeId: SOURCE_ID,
      memberIds: pendingMembers,
      groupLabel: loud.groupLabel,
      aspectKey: '16:9',
    });
    expect(groupId).toBeTruthy();
    const settled = useCanvasStore.getState();
    expect(settled.nodes.filter(isStoryboardGroupNode)).toHaveLength(1);
    expect(settled.nodes.filter((node) => node.parentId === groupId)).toHaveLength(2);
    // 回写走的是视频故事节点那条路（linkedShotGroupId），与脚本节点的
    // linkedImageGroupId 是两条独立路径。
    expect(sourceNode()?.data[VIDEO_STORY_LINKED_GROUP_FIELD]).toBe(groupId);
    expect(sourceNode()?.data.linkedImageGroupId).toBeUndefined();
  });

  it('已有组时出图先散开（否则隐藏成员不会提交），已出的图不重跑', () => {
    const first = scatter({});
    expect(first.ok).toBe(true);
    if (!first.ok) return;
    markGenerated(first.nodeIds[0]);

    const second = scatter({ generateImages: true });
    expect(second.ok).toBe(true);
    if (!second.ok) return;
    expect(second.mode).toBe('rearmed');
    expect(second.armed).toBe(1);
    pendingMembers = second.nodeIds;

    const state = useCanvasStore.getState();
    expect(imageNodes()).toHaveLength(2);
    expect(state.nodes.filter(isStoryboardGroupNode)).toHaveLength(0);
    expect(
      state.nodes
        .filter((node) => second.nodeIds.includes(node.id))
        .every((node) => node.hidden !== true && !node.parentId),
    ).toBe(true);
    // 已出图的那张不被重新排队；未出图的那张被排队。
    expect(state.nodes.find((node) => node.id === first.nodeIds[0])?.data.canvas_auto_generate_once)
      .toBe(false);
    expect(state.nodes.find((node) => node.id === first.nodeIds[1])?.data.canvas_auto_generate_once)
      .toBe(true);
    // 已出的图还在。
    expect(state.nodes.find((node) => node.id === first.nodeIds[0])?.data.imageUrl).toBe('shot.png');

    markGenerated(first.nodeIds[1], 'shot-2.png');
    const groupId = collapseStoryboardMembers({
      scriptNodeId: null,
      ownerNodeId: SOURCE_ID,
      memberIds: pendingMembers,
      groupLabel: second.groupLabel,
      aspectKey: '16:9',
    });
    expect(sourceNode()?.data[VIDEO_STORY_LINKED_GROUP_FIELD]).toBe(groupId);
  });

  it('表里提示词改过 → 那一镜重新排队（派生快照对不上）', () => {
    const first = scatter({});
    expect(first.ok).toBe(true);
    if (!first.ok) return;
    markGenerated(first.nodeIds[0]);
    markGenerated(first.nodeIds[1]);

    const edited = rows();
    edited[1] = { ...edited[1], imagePrompt: '改过的第二镜提示词' };
    setRows(edited);

    const plan = planVideoStoryScatter(SOURCE_ID);
    expect(plan.ok).toBe(true);
    if (!plan.ok) return;
    expect(plan.mode).toBe('regenerate');
    expect(plan.pendingCount).toBe(1);
  });

  it('单镜不成组，只留一个图片节点，源节点组 id 为 null', () => {
    setRows([{ shotNumber: 1, imagePrompt: '唯一一镜' }]);
    const result = scatter({});
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.groupId).toBeNull();
    expect(imageNodes()).toHaveLength(1);
    expect(sourceNode()?.data[VIDEO_STORY_LINKED_GROUP_FIELD]).toBeNull();
  });

  it('拼不出提示词的行跳过，其余照常落节点', () => {
    setRows([{ shotNumber: 1 }, { shotNumber: 2, visualDescription: '有画面描述就够了' }]);
    const result = scatter({});
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.nodeIds).toHaveLength(1);
    expect(result.skipped).toBe(1);
    expect(imageNodes()[0].data.videoStoryRowKey).toBe('2');
  });

  it('没选模型 / 没有分镜行 / 整批无提示词时给出可读原因，不动画布', () => {
    const noModel = scatter({ generateImages: true, model: '' });
    expect(noModel.ok).toBe(false);
    if (!noModel.ok) expect(noModel.reason).toContain('模型');
    expect(imageNodes()).toHaveLength(0);

    setRows([]);
    const noRows = scatter({});
    expect(noRows.ok).toBe(false);
    expect(imageNodes()).toHaveLength(0);

    seedSourceNode([{ shotNumber: 1 }, { shotNumber: 2 }]);
    const noPrompt = scatter({});
    expect(noPrompt.ok).toBe(false);
    if (!noPrompt.ok) expect(noPrompt.reason).toContain('提示词');
    expect(imageNodes()).toHaveLength(0);

    const missingNode = scatterVideoStoryShots({ videoStoryNodeId: 'nope', model: MODEL });
    expect(missingNode.ok).toBe(false);
    expect(imageNodes()).toHaveLength(0);
  });
});

describe('镜头组反查与组级重跑', () => {
  beforeEach(() => {
    resetStoryboardSettle();
    pendingMembers = [];
    seedSourceNode();
  });

  it('planVideoStoryScatter 与真正落盘的口径一致', () => {
    const plan = planVideoStoryScatter(SOURCE_ID);
    expect(plan.ok).toBe(true);
    if (!plan.ok) return;
    expect(plan.mode).toBe('create');
    expect(plan.shotCount).toBe(2);
    expect(plan.pendingCount).toBe(2);
    expect(plan.willRebuild).toBe(false);
    expect(plan.groupLabel).toBe('镜头表 · 2 镜');

    const result = scatter({});
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.nodeIds).toHaveLength(plan.shotCount);
  });

  it('行标识换过时 plan 标出 willRebuild（弹层据此说清会重出全部）', () => {
    const first = scatter({});
    expect(first.ok).toBe(true);
    if (!first.ok) return;

    setRows(renamedRows());
    const plan = planVideoStoryScatter(SOURCE_ID);
    expect(plan.ok).toBe(true);
    if (!plan.ok) return;
    expect(plan.mode).toBe('create');
    expect(plan.willRebuild).toBe(true);
  });

  it('videoStoryOwnerNodeIdForGroup 反查源节点；组级重跑只排队未落定的', () => {
    const first = scatter({});
    expect(first.ok).toBe(true);
    if (!first.ok) return;
    const groupId = first.groupId;
    expect(groupId).toBeTruthy();
    if (!groupId) return;
    expect(videoStoryOwnerNodeIdForGroup(groupId)).toBe(SOURCE_ID);

    markGenerated(first.nodeIds[0]);
    const rearm = regenerateVideoStoryShotGroup(groupId);
    expect(rearm.ok).toBe(true);
    expect(rearm.armed).toBe(1);

    const state = useCanvasStore.getState();
    // 重跑期间组被散开成可见节点。
    expect(state.nodes.filter(isStoryboardGroupNode)).toHaveLength(0);
    expect(state.nodes.find((node) => node.id === first.nodeIds[1])?.data.canvas_auto_generate_once)
      .toBe(true);
    expect(state.nodes.find((node) => node.id === first.nodeIds[0])?.data.canvas_auto_generate_once)
      .toBe(false);
  });

  it('组里全都出齐时组级重跑不排队（不给付费接口白跑）', () => {
    const first = scatter({});
    expect(first.ok).toBe(true);
    if (!first.ok || !first.groupId) return;
    first.nodeIds.forEach((nodeId, index) => markGenerated(nodeId, `shot-${index}.png`));
    const rearm = regenerateVideoStoryShotGroup(first.groupId);
    expect(rearm.ok).toBe(true);
    expect(rearm.armed).toBe(0);
  });

  it('源节点被删掉后组级重跑退回「只看有没有图」', () => {
    const first = scatter({});
    expect(first.ok).toBe(true);
    if (!first.ok || !first.groupId) return;
    useCanvasStore.getState().deleteNodes([SOURCE_ID]);
    markGenerated(first.nodeIds[0]);
    const rearm = regenerateVideoStoryShotGroup(first.groupId);
    expect(rearm.ok).toBe(true);
    expect(rearm.armed).toBe(1);
  });
});
