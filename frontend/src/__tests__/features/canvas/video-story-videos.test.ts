// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { beforeEach, describe, expect, it } from 'vitest';

import {
  CANVAS_NODE_TYPES,
  isImageGenNode,
  isVideoNode,
  type VideoStoryRow,
} from '@/features/canvas/domain/canvasNodes';
import { resetStoryboardSettle } from '@/features/canvas/nodes/script/storyboardSettle';
import { scatterVideoStoryShots } from '@/features/canvas/nodes/videoStory/scatterVideoStoryShots';
import {
  VIDEO_STORY_SHOT_VIDEO_EDGE_ROLE,
  VIDEO_STORY_SHOT_VIDEO_IMAGE_FIELD,
  planVideoStoryShotVideos,
  scatterVideoStoryShotVideos,
} from '@/features/canvas/nodes/videoStory/videoStoryShotVideos';
import { useCanvasStore } from '@/stores/canvasStore';

/**
 * 「镜头图 → 逐镜视频」落盘（T-015）。
 *
 * 这层锁的是**生命周期与准入**，不是公式（公式在 video-story-shots.test.ts 里）：
 * - 首帧只能来自上游节点 → 派生时必须有一条真实的镜头图 → 视频节点边；
 * - 只有出好图的镜能派（没图的行跳过，不是悄悄降级成文生视频）；
 * - 重复点不重复派生；行集合换过就重建；提示词改过只重跑那一镜；
 * - 边被人删掉之后能补回来（身份存在节点自己身上，不依赖边）。
 */

const SOURCE_ID = 'video-story-node';
const MODEL = 'direct/image-msfkiqdq-ed3hhy';
const VIDEO_MODEL = 'direct/video-abc123';
const SOURCE_SIZE = { width: 720, height: 360 };

function rows(): VideoStoryRow[] {
  return [
    {
      shotNumber: 1,
      startTime: '00:00',
      endTime: '00:04',
      visualDescription: '雨夜祠堂外景',
      imagePrompt: '推近祠堂门环',
      videoMotionPrompt: '镜头缓缓推近门环，雨丝斜落',
      shotSize: '特写',
      keyframeUrl: '/static/projects/p1/frames/f1.jpg',
    },
    {
      shotNumber: 2,
      startTime: '00:04',
      endTime: '00:09',
      visualDescription: '阿雀推门而入',
      videoMotionPrompt: '门被推开，阿雀侧身走入',
      shotSize: '中景',
    },
  ];
}

/** 行数相同但行标识整批换过 —— 判定必须落到「重建」而不是「原地重跑」。 */
function renamedRows(): VideoStoryRow[] {
  return [
    { shotNumber: 11, imagePrompt: '换过的第一镜画面', videoMotionPrompt: '换过的第一镜运镜' },
    { shotNumber: 12, imagePrompt: '换过的第二镜画面', videoMotionPrompt: '换过的第二镜运镜' },
  ];
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

/**
 * 铺好前置条件：把分镜行散成镜头图节点并逐个标记「已出图」。
 * 逐镜出视频的准入就是「这一镜有镜头图」，所以每条用例都得先走这一步。
 */
function seedShotImages(options: { generated?: boolean; generateImages?: boolean } = {}) {
  const scatter = scatterVideoStoryShots({
    videoStoryNodeId: SOURCE_ID,
    model: MODEL,
    aspectRatio: '16:9',
    generateImages: options.generateImages,
  });
  if (!scatter.ok) throw new Error(`前置镜头图散开失败：${scatter.reason}`);
  if (options.generated !== false) {
    scatter.nodeIds.forEach((nodeId, index) => {
      useCanvasStore.getState().updateNodeData(nodeId, { imageUrl: `shot-${index}.png` });
    });
  }
  return scatter.nodeIds;
}

function videoNodes() {
  return useCanvasStore.getState().nodes.filter(isVideoNode);
}function imageNodes() {
  return useCanvasStore.getState().nodes.filter(isImageGenNode);
}

function setRows(nextRows: VideoStoryRow[]) {
  useCanvasStore.getState().updateNodeData(SOURCE_ID, { rows: nextRows });
}

function scatterVideos(options: {
  generateVideos?: boolean;
  model?: string | null;
  aspectRatio?: string;
  defaultDurationSec?: number;
} = {}) {
  return scatterVideoStoryShotVideos({
    videoStoryNodeId: SOURCE_ID,
    model: options.model === undefined ? VIDEO_MODEL : options.model,
    aspectRatio: options.aspectRatio,
    defaultDurationSec: options.defaultDurationSec,
    generateVideos: options.generateVideos,
  });
}

/** 镜头图 → 某视频节点的那条首帧边（方向与角色都要对）。 */
function firstFrameEdge(videoNodeId: string) {
  return useCanvasStore
    .getState()
    .edges.find((edge) => edge.target === videoNodeId
      && edge.data?.role === VIDEO_STORY_SHOT_VIDEO_EDGE_ROLE);
}

describe('逐镜出视频 · 从镜头图派生视频节点', () => {
  beforeEach(() => {
    resetStoryboardSettle();
    seedSourceNode();
  });

  it('逐镜建视频节点、连首帧边，首帧取该镜镜头图', () => {
    seedShotImages();
    const result = scatterVideos({});
    expect(result.ok).toBe(true);
    if (!result.ok) return;

    expect(result.mode).toBe('created');
    expect(result.nodeIds).toHaveLength(2);
    expect(result.armed).toBe(0);
    expect(result.skippedNoImage).toBe(0);
    expect(result.skippedNoPrompt).toBe(0);

    const videos = videoNodes();
    expect(videos).toHaveLength(2);
    expect(videos.every((node) => node.data.genMode === 'imageToVideo')).toBe(true);
    expect(videos.every((node) => node.data.model === VIDEO_MODEL)).toBe(true);
    expect(videos.every((node) => node.data.count === 1)).toBe(true);
    // 只建节点时不该把付费标志打上。
    expect(videos.every((node) => node.data.canvas_auto_generate_once === false)).toBe(true);

    // 提示词取该行的**运动**提示词（拉片给的运动描述），不是画面提示词。
    const byKey = new Map(videos.map((node) => [node.data.videoStoryRowKey, node]));
    expect(byKey.get('1')?.data.prompt).toBe('镜头缓缓推近门环，雨丝斜落');
    expect(byKey.get('2')?.data.prompt).toBe('门被推开，阿雀侧身走入');
    // 画面提示词绝不能串进来 —— 那是镜头图那一跳用的事实。
    expect(byKey.get('1')?.data.prompt).not.toBe('推近祠堂门环');

    // 行身份 + 镜头语言留档。
    expect(byKey.get('1')?.data.videoStorySourceNodeId).toBe(SOURCE_ID);
    expect(byKey.get('1')?.data.shot_size).toBe('特写');

    // 首帧：一条真实的镜头图 → 视频节点边，且身份字段指向同一个来源节点。
    const frame = firstFrameEdge(result.nodeIds[0]);
    expect(frame).toBeTruthy();
    const sourceImage = useCanvasStore
      .getState()
      .nodes.find((node) => node.id === frame?.source);
    expect(sourceImage && isImageGenNode(sourceImage)).toBe(true);
    expect(videos[0].data[VIDEO_STORY_SHOT_VIDEO_IMAGE_FIELD]).toBe(sourceImage?.id);
    // 边必须是「有图的」那个镜头图节点 —— i2v 提交只认上游的 imageUrl。
    expect(sourceImage?.data.imageUrl).toBeTruthy();
    expect(firstFrameEdge(result.nodeIds[1])).toBeTruthy();
  });

  it('时长按该行时间码，没有时间码的行落到兜底档', () => {
    seedSourceNode([
      { shotNumber: 1, startTime: '00:00', endTime: '00:04', imagePrompt: '第一镜画面', videoMotionPrompt: '四秒' },
      { shotNumber: 2, imagePrompt: '第二镜画面', videoMotionPrompt: '没有时间码' },
    ]);
    seedShotImages();
    const result = scatterVideos({ defaultDurationSec: 6 });
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    const byKey = new Map(videoNodes().map((node) => [node.data.videoStoryRowKey, node]));
    expect(byKey.get('1')?.data.durationSec).toBe(4);
    expect(byKey.get('2')?.data.durationSec).toBe(6);
  });

  it('时间码支持 MM:SS / HH:MM:SS / 全角冒号 / 裸秒数，退路是时长列', () => {
    seedSourceNode([
      { shotNumber: 1, startTime: '01:00', endTime: '01:07', imagePrompt: '画面一', videoMotionPrompt: '七秒' },
      { shotNumber: 2, startTime: '00:00:30', endTime: '00:00:38', imagePrompt: '画面二', videoMotionPrompt: '八秒' },
      { shotNumber: 3, startTime: '0：10', endTime: '0：14', imagePrompt: '画面三', videoMotionPrompt: '四秒' },
      { shotNumber: 4, duration: '9', imagePrompt: '画面四', videoMotionPrompt: '时长列' },
      { shotNumber: 5, startTime: '00:00', endTime: '00:20', duration: '3', imagePrompt: '画面五', videoMotionPrompt: '区间优先' },
    ]);
    seedShotImages();
    const result = scatterVideos({});
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    const byKey = new Map(videoNodes().map((node) => [node.data.videoStoryRowKey, node]));
    expect(byKey.get('1')?.data.durationSec).toBe(7);
    expect(byKey.get('2')?.data.durationSec).toBe(8);
    expect(byKey.get('3')?.data.durationSec).toBe(4);
    expect(byKey.get('4')?.data.durationSec).toBe(9);
    // 区间与「时长」列都在时以区间为准：那一镜真正占的时间就是它。
    expect(byKey.get('5')?.data.durationSec).toBe(20);
  });

  it('没有镜头图的行不出现（不降级成文生视频），弹层账本如实计数', () => {
    const shotImageIds = seedShotImages({ generated: false });
    // 只给第一镜出图。
    useCanvasStore.getState().updateNodeData(shotImageIds[0], { imageUrl: 'shot-0.png' });

    const plan = planVideoStoryShotVideos(SOURCE_ID);
    expect(plan.ok).toBe(true);
    if (!plan.ok) return;
    expect(plan.shotCount).toBe(1);
    expect(plan.pendingCount).toBe(1);
    expect(plan.skippedNoImage).toBe(1);

    const result = scatterVideos({});
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.nodeIds).toHaveLength(1);
    expect(result.skippedNoImage).toBe(1);
    expect(videoNodes()).toHaveLength(1);
  });

  it('一镜镜头图都没有时拒绝派生，并给出可读原因（不动画布）', () => {
    seedShotImages({ generated: false });
    const result = scatterVideos({ generateVideos: true });
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.reason).toContain('镜头图');
    expect(videoNodes()).toHaveLength(0);
  });

  it('运动提示词也拼不出来的行不落节点', () => {
    // 画面提示词有（镜头图能出），但运动提示词与画面描述都没有 → 视频这一跳没有可跑的东西。
    seedSourceNode([
      { shotNumber: 1, imagePrompt: '只有画面提示词' },
      { shotNumber: 2, imagePrompt: '第二镜画面', visualDescription: '有画面描述就够当运动提示词' },
    ]);
    seedShotImages();
    const result = scatterVideos({});
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.nodeIds).toHaveLength(1);
    expect(result.skippedNoPrompt).toBe(1);
    expect(videoNodes()[0].data.videoStoryRowKey).toBe('2');
  });

  it('generateVideos 为真时给每条打上一次性提交标志，armed 等于条数', () => {
    seedShotImages();
    const result = scatterVideos({ generateVideos: true });
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.armed).toBe(2);
    expect(
      videoNodes().every((node) => node.data.canvas_auto_generate_once === true),
    ).toBe(true);
  });

  it('重复点不会重复派生（幂等），只按需重新排队', () => {
    seedShotImages();
    const first = scatterVideos({});
    expect(first.ok).toBe(true);
    if (!first.ok) return;

    // 先把两条都标成出好片了。
    first.nodeIds.forEach((nodeId, index) => {
      useCanvasStore.getState().updateNodeData(nodeId, { videoUrl: `v-${index}.mp4` });
    });

    const second = scatterVideos({ generateVideos: true });
    expect(second.ok).toBe(true);
    if (!second.ok) return;
    expect(second.mode).toBe('rearmed');
    expect(second.nodeIds.sort()).toEqual(first.nodeIds.sort());
    // 都出齐了 → 一条都不该重新排队（不给付费接口白跑）。
    expect(second.armed).toBe(0);
    expect(videoNodes()).toHaveLength(2);
  });

  it('没出片的那些会被重新排队，已出片的不动', () => {
    seedShotImages();
    const first = scatterVideos({});
    expect(first.ok).toBe(true);
    if (!first.ok) return;
    useCanvasStore.getState().updateNodeData(first.nodeIds[0], { videoUrl: 'v-0.mp4' });

    const second = scatterVideos({ generateVideos: true });
    expect(second.ok).toBe(true);
    if (!second.ok) return;
    expect(second.mode).toBe('rearmed');
    expect(second.armed).toBe(1);
    const state = useCanvasStore.getState();
    expect(state.nodes.find((node) => node.id === first.nodeIds[0])?.data.canvas_auto_generate_once)
      .toBe(false);
    expect(state.nodes.find((node) => node.id === first.nodeIds[1])?.data.canvas_auto_generate_once)
      .toBe(true);
    expect(state.nodes.find((node) => node.id === first.nodeIds[0])?.data.videoUrl).toBe('v-0.mp4');
  });

  it('表里的运动提示词改过 → 那一镜重新排队（派生快照对不上）', () => {
    seedShotImages();
    const first = scatterVideos({});
    expect(first.ok).toBe(true);
    if (!first.ok) return;
    first.nodeIds.forEach((nodeId, index) => {
      useCanvasStore.getState().updateNodeData(nodeId, { videoUrl: `v-${index}.mp4` });
    });

    const edited = rows();
    edited[1] = { ...edited[1], videoMotionPrompt: '改过的运镜' };
    setRows(edited);

    const plan = planVideoStoryShotVideos(SOURCE_ID);
    expect(plan.ok).toBe(true);
    if (!plan.ok) return;
    expect(plan.mode).toBe('regenerate');
    expect(plan.pendingCount).toBe(1);
    expect(plan.plannedSeconds).toBe(5);
  });

  it('上次失败的镜头也算未落地，会重新排队', () => {
    seedShotImages();
    const first = scatterVideos({});
    expect(first.ok).toBe(true);
    if (!first.ok) return;
    first.nodeIds.forEach((nodeId, index) => {
      useCanvasStore.getState().updateNodeData(nodeId, {
        videoUrl: `v-${index}.mp4`,
        generationError: index === 0 ? '上游超时' : null,
      });
    });
    const plan = planVideoStoryShotVideos(SOURCE_ID);
    expect(plan.ok).toBe(true);
    if (!plan.ok) return;
    expect(plan.pendingCount).toBe(1);
  });

  it('行标识整批换过（数量相同）→ 重建，不把新提示词写到旧行号上', () => {
    seedShotImages();
    const first = scatterVideos({});
    expect(first.ok).toBe(true);
    if (!first.ok) return;
    const firstIds = [...first.nodeIds];

    setRows(renamedRows());
    // 新行也要有图才能派 —— 镜头图仍是按行标识关联的，这里补一批新行镜头图。
    const newImages = seedShotImages();

    const second = scatterVideos({});
    expect(second.ok).toBe(true);
    if (!second.ok) return;
    expect(second.mode).toBe('rebuilt');
    const state = useCanvasStore.getState();
    // 旧视频节点整批删掉。
    firstIds.forEach((nodeId) => {
      expect(state.nodes.some((node) => node.id === nodeId)).toBe(false);
    });
    const byKey = new Map(videoNodes().map((node) => [node.data.videoStoryRowKey, node]));
    expect([...byKey.keys()].sort()).toEqual(['11', '12']);
    expect(byKey.get('11')?.data.prompt).toBe('换过的第一镜运镜');
    // 新派生的视频节点连到新一批镜头图，不是旧的那批。
    const frame = firstFrameEdge(second.nodeIds[0]);
    expect(newImages).toContain(frame?.source);
  });

  it('镜头图重建过（图没了）→ 边跟着指向新的那张，身份字段同步更新', () => {
    seedShotImages();
    const first = scatterVideos({});
    expect(first.ok).toBe(true);
    if (!first.ok) return;

    // 模拟「镜头图重出」：把旧镜头图删掉（边被连带删），再按同样行标识补一张新的。
    const oldImages = imageNodes().map((node) => node.id);
    useCanvasStore.getState().deleteNodes(oldImages);
    expect(imageNodes()).toHaveLength(0);
    expect(videoNodes()).toHaveLength(first.nodeIds.length); // 视频节点本身还在
    expect(useCanvasStore.getState().edges.filter(
      (edge) => edge.data?.role === VIDEO_STORY_SHOT_VIDEO_EDGE_ROLE,
    )).toHaveLength(0);

    const newImages = seedShotImages();
    const rearm = scatterVideos({});
    expect(rearm.ok).toBe(true);
    if (!rearm.ok) return;
    expect(rearm.mode).toBe('rearmed');
    // 边被补回来了，且指向新的那张镜头图。
    const frame = firstFrameEdge(first.nodeIds[0]);
    expect(newImages).toContain(frame?.source);
    expect(
      videoNodes().find((node) => node.id === first.nodeIds[0])
        ?.data[VIDEO_STORY_SHOT_VIDEO_IMAGE_FIELD],
    ).toBe(frame?.source);
  });

  it('用户手删了首帧边 → 下次进来会补回来（身份存在节点上，不依赖边）', () => {
    seedShotImages();
    const first = scatterVideos({});
    expect(first.ok).toBe(true);
    if (!first.ok) return;
    const edge = firstFrameEdge(first.nodeIds[0]);
    expect(edge).toBeTruthy();
    if (!edge) return;
    useCanvasStore.getState().deleteEdge(edge.id);
    expect(firstFrameEdge(first.nodeIds[0])).toBeFalsy();

    const plan = planVideoStoryShotVideos(SOURCE_ID);
    // 边丢了也不能被当成「一个都没派过」——否则重复点击会再派生一批付费节点。
    expect(plan.ok).toBe(true);
    if (!plan.ok) return;
    expect(plan.mode).toBe('regenerate');
    expect(plan.derivedCount).toBe(2);

    const again = scatterVideos({});
    expect(again.ok).toBe(true);
    if (!again.ok) return;
    expect(videoNodes()).toHaveLength(2);
    expect(firstFrameEdge(first.nodeIds[0])).toBeTruthy();
  });

  it('视频节点散在镜头图那块右侧，且行序 = 宫格读取顺序', () => {
    seedShotImages();
    const result = scatterVideos({});
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    const state = useCanvasStore.getState();
    const images = imageNodes();
    const videos = videoNodes();
    const imageRight = Math.max(
      ...images.map((node) => node.position.x + (node.measured?.width ?? 580)),
    );
    expect(videos.every((node) => node.position.x >= imageRight)).toBe(true);
    const a = state.nodes.find((node) => node.id === result.nodeIds[0]);
    const b = state.nodes.find((node) => node.id === result.nodeIds[1]);
    // 两列一行：同一行 y 相同、第二个在右边 —— 与行序一致。
    expect(a?.position.y).toBe(b?.position.y);
    expect(b?.position.x).toBeGreaterThan(a?.position.x ?? 0);
  });

  it('没选模型也可以只建节点，但不该把空模型写进节点覆盖默认绑定', () => {
    seedShotImages();
    const result = scatterVideos({ model: '' });
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.nodeIds).toHaveLength(2);
    // 空模型不写 data.model：视频节点自己会退回「上次选的 / 渠道默认」，写空反而
    // 会盖掉它的兜底，让节点一进来就是不可提交状态。
    expect(videoNodes().every((node) => !node.data.model)).toBe(true);
  });

  it('视频故事节点不存在 / 表里没内容时给出可读原因', () => {
    const missing = scatterVideoStoryShotVideos({
      videoStoryNodeId: 'nope',
      model: VIDEO_MODEL,
    });
    expect(missing.ok).toBe(false);

    setRows([]);
    const empty = scatterVideos({});
    expect(empty.ok).toBe(false);
    if (!empty.ok) expect(empty.reason).toContain('分镜表');
    expect(videoNodes()).toHaveLength(0);
  });
});

describe('逐镜出视频 · 计划账', () => {
  beforeEach(() => {
    resetStoryboardSettle();
    seedSourceNode();
  });

  it('planVideoStoryShotVideos 与真正落盘的口径一致', () => {
    seedShotImages();
    const plan = planVideoStoryShotVideos(SOURCE_ID);
    expect(plan.ok).toBe(true);
    if (!plan.ok) return;
    expect(plan.mode).toBe('create');
    expect(plan.shotCount).toBe(2);
    expect(plan.pendingCount).toBe(2);
    expect(plan.willRebuild).toBe(false);
    expect(plan.derivedCount).toBe(0);
    // 4 秒 + 5 秒
    expect(plan.plannedSeconds).toBe(9);

    const result = scatterVideos({});
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.nodeIds).toHaveLength(plan.shotCount);
  });

  it('行标识换过时 plan 标出 willRebuild（弹层据此说清会重出全部）', () => {
    seedShotImages();
    const first = scatterVideos({});
    expect(first.ok).toBe(true);
    if (!first.ok) return;

    setRows(renamedRows());
    seedShotImages();
    const plan = planVideoStoryShotVideos(SOURCE_ID);
    expect(plan.ok).toBe(true);
    if (!plan.ok) return;
    expect(plan.mode).toBe('create');
    expect(plan.willRebuild).toBe(true);
    expect(plan.derivedCount).toBe(2);
  });

  it('还有没出图的镜时不派它，但已出图的那几镜照常派', () => {
    const shotImageIds = seedShotImages({ generated: false });
    useCanvasStore.getState().updateNodeData(shotImageIds[0], { imageUrl: 'shot-0.png' });
    useCanvasStore.getState().updateNodeData(shotImageIds[1], { imageUrl: 'shot-1.png' });

    const plan = planVideoStoryShotVideos(SOURCE_ID);
    expect(plan.ok).toBe(true);
    if (!plan.ok) return;
    expect(plan.shotCount).toBe(2);
    expect(plan.skippedNoImage).toBe(0);
    expect(plan.skippedNoPrompt).toBe(0);

    // 现在把第二镜的图撤掉，它就该从这一轮的计划里消失。
    useCanvasStore.getState().updateNodeData(shotImageIds[1], { imageUrl: null });
    const rePlan = planVideoStoryShotVideos(SOURCE_ID);
    expect(rePlan.ok).toBe(true);
    if (!rePlan.ok) return;
    expect(rePlan.shotCount).toBe(1);
    expect(rePlan.skippedNoImage).toBe(1);
  });
});
