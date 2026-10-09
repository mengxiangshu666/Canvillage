// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { beforeEach, describe, expect, it } from 'vitest';

import type { FreezoneStoryScriptRow } from '@/api/ops';
import {
  CANVAS_NODE_TYPES,
  isImageGenNode,
  isStoryboardGroupNode,
  type VideoCreativeHandoff,
} from '@/features/canvas/domain/canvasNodes';
import {
  scriptAssetImageNodes,
  scriptAssetId,
} from '@/features/canvas/nodes/script/scriptAssets';
import {
  generateScriptStoryboard,
  regenerateStoryboardGroupImages,
  storyboardMemberImageUrl,
  storyboardMemberNeedsImage,
} from '@/features/canvas/nodes/script/generateScriptStoryboard';
import {
  collapseStoryboardMembers,
  resetStoryboardSettle,
  storyboardBatchSettled,
} from '@/features/canvas/nodes/script/storyboardSettle';
import { collectStoryboardDownloadItems } from '@/features/canvas/nodes/script/storyboardDownload';
import { useCanvasStore } from '@/stores/canvasStore';
import { scatterScriptShotVideos } from '@/features/canvas/nodes/script/scriptShotVideos';

const SCRIPT_NODE_ID = 'script-node';
const SCRIPT_SIZE = { width: 800, height: 400 };
const MODEL = 'direct/image-msfkiqdq-ed3hhy';

function rows(): FreezoneStoryScriptRow[] {
  return [
    {
      shot_no: '1',
      duration: '3',
      visual_description: '开场',
      shot_prompt: '镜头推近祠堂',
      character_1: '阿雀',
      character_image_1: 'a.png',
    },
    { shot_no: '2', visual_description: '收尾' },
  ];
}

function threeRows(): FreezoneStoryScriptRow[] {
  return [...rows(), { shot_no: '3', visual_description: '收镜' }];
}

/** 双人镜头：一行里两个角色槽都填了角色图（社区语料 21.3% 的图节点带 ≥2 张参考图）。 */
function twoCharacterRows(): FreezoneStoryScriptRow[] {
  return [
    {
      shot_no: '1',
      visual_description: '对峙',
      shot_prompt: '两人面对面',
      character_1: '阿雀',
      character_image_1: 'a.png',
      character_2: '老周',
      character_image_2: 'b.png',
      reference: 'frame.png',
    },
  ];
}

function seedScriptNode() {
  useCanvasStore.getState().setCanvasData(
    [
      {
        id: SCRIPT_NODE_ID,
        type: CANVAS_NODE_TYPES.script,
        position: { x: 0, y: 0 },
        style: { width: SCRIPT_SIZE.width, height: SCRIPT_SIZE.height },
        data: {
          scriptResult: { title: '天空之跃', rows: rows() },
          scriptTitle: '天空之跃',
        },
      },
    ] as never,
    [],
  );
}

function seedSecondaryScriptNode(): string {
  const state = useCanvasStore.getState();
  state.addNode(
    CANVAS_NODE_TYPES.script,
    { x: 1600, y: 0 },
    {
      scriptResult: {
        title: '另一部片',
        rows: [{ shot_no: '1', visual_description: '同名角色登场', character_1: '阿雀' }],
      },
      scriptTitle: '另一部片',
    },
  );
  return (
    useCanvasStore
      .getState()
      .nodes.find((node) => node.data.scriptTitle === '另一部片')?.id ?? ''
  );
}

function imageNodes() {
  return useCanvasStore.getState().nodes.filter(isImageGenNode);
}

function generate(options: {
  rows?: FreezoneStoryScriptRow[];
  generateImages?: boolean;
  config?: Record<string, unknown>;
}) {
  return generateScriptStoryboard({
    scriptNodeId: SCRIPT_NODE_ID,
    rows: options.rows ?? rows(),
    scriptTitle: '天空之跃',
    scriptSize: SCRIPT_SIZE,
    config: { model: MODEL, aspectRatio: '16:9', ...(options.config ?? {}) },
    generateImages: options.generateImages,
  });
}

/** 把某一镜标记成「出好图了」，用来模拟一次成功的生成。 */
function markGenerated(nodeId: string, url = 'shot.png') {
  useCanvasStore.getState().updateNodeData(nodeId, { imageUrl: url, isGenerating: false, canvas_auto_generate_once: false });
}

/** 出图阶段（生成分镜带出图）成员是散开的普通节点，收拢看「全部落定」。 */
function settleAll() {
  if (!storyboardBatchSettled(pendingMembers)) return null;
  return collapseStoryboardMembers({
    scriptNodeId: SCRIPT_NODE_ID,
    memberIds: pendingMembers,
    groupLabel: '分镜图 · 天空之跃',
    aspectKey: '16:9',
  });
}

let pendingMembers: string[] = [];

describe('生成分镜 · 派生分镜图组', () => {
  it('生成分镜在首图完成后生成计划状态图，视频搭建复用同一张真实图片', () => {
    seedScriptNode();
    const planned = rows().map((row, index) => index === 0 ? {
      ...row,
      video_motion_prompt: '滑板冲上坡沿，随后腾空', duration: 6,
      shot_prompt: '[画面构图：全景] + [主体/人物空间与互动关系：首帧准备姿态] + [光影几何：日光] + [视觉风格/质感：3D动画]',
      shot_purpose: '看清起跳前的支撑关系',
      cut_reason: '动作结果出现后切到落点',
      sequence_ids: ['S1'],
      keyframe_plan: [
        { role: 'contact_state', state: '前轮压住坡沿，双脚踩板', purpose: '锁定支撑点', required: false },
        { role: 'contact_state', state: '前轮压住坡沿,双脚踩板。', purpose: '锁定支撑点', required: true },
      ],
    } : { ...row, video_motion_prompt: '镜头拉远', duration: 6 });
    const directorPlan = {
      story_promise: '敢于起跳',
      visual_bible: { visual_style: '共同动画风格', lighting: '坡道日光', color_progression: '下一段转暖' },
      sequences: [
        { sequence_id: 'S1', shot_nos: [1], performance_plan: '准备后放松', turn: '下一镜庆祝', staging_plan: '平台和坡沿相邻' },
        { sequence_id: 'foreign', shot_nos: [99], staging_plan: '无关屋顶追逐' },
      ],
    };
    useCanvasStore.getState().updateNodeData(SCRIPT_NODE_ID, { scriptResult: { title: '测试', rows: planned, director_plan: directorPlan } });
    const result = generate({ rows: planned, generateImages: true });
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(useCanvasStore.getState().nodes.filter(node => node.data.scriptShotKeyframeSourceNodeId)).toHaveLength(0);
    useCanvasStore.getState().updateNodeData(result.nodeIds[0], { imageUrl: '/opening-0.png', isGenerating: true, canvas_auto_generate_once: false });
    markGenerated(result.nodeIds[1], '/opening-1.png');
    expect(useCanvasStore.getState().nodes.filter(node => node.data.scriptShotKeyframeSourceNodeId)).toHaveLength(0);
    result.nodeIds.forEach((id, index) => markGenerated(id, `/opening-${index}.png`));
    const states = useCanvasStore.getState().nodes.filter(node => node.data.scriptShotKeyframeSourceNodeId === SCRIPT_NODE_ID);
    expect(states).toHaveLength(1);
    expect(states[0].data.canvas_auto_generate_once).toBe(true);
    expect((states[0].data.scriptCreativeHandoff as VideoCreativeHandoff).keyframePlan).toEqual([
      { role: 'contact_state', state: '前轮压住坡沿，双脚踩板', purpose: '锁定支撑点', required: true },
    ]);
    expect(useCanvasStore.getState().pendingFocusNodeIds).toContain(states[0].id);
    expect(states[0].data.referenceImageUrls).toContain('/opening-0.png');
    expect(states[0].data.prompt).toContain('可见状态：前轮压住坡沿');
    expect(states[0].data.prompt).toContain('[光影几何：日光]');
    expect(states[0].data.prompt).not.toContain('首帧准备姿态');
    expect(states[0].data.prompt).toContain('共同动画风格');
    expect(states[0].data.prompt).not.toContain('下一镜庆祝');
    expect(states[0].data.prompt).not.toContain('下一段转暖');
    expect(states[0].data.scriptCreativeHandoff).toMatchObject({
      shotPurpose: '看清起跳前的支撑关系',
      cutReason: '动作结果出现后切到落点',
      sequenceIds: ['S1'],
      directorContext: { storyPromise: '敢于起跳', sequences: [{ sequenceId: 'S1', performancePlan: '准备后放松' }] },
    });
    markGenerated(states[0].id, '/contact.png');
    const video = scatterScriptShotVideos({ scriptNodeId: SCRIPT_NODE_ID, model: 'video-model' });
    expect(video.ok).toBe(true);
    expect(useCanvasStore.getState().nodes.filter(node => node.data.scriptShotKeyframeSourceNodeId)).toHaveLength(1);
    expect(useCanvasStore.getState().nodes.find(node => node.id === states[0].id)?.data.imageUrl).toBe('/contact.png');
    const firstVideo = useCanvasStore.getState().nodes.find(node => node.type === CANVAS_NODE_TYPES.video && node.data.scriptShotRowKey === states[0].data.scriptShotKeyframeRowKey);
    const opening = useCanvasStore.getState().nodes.find(node => node.id === result.nodeIds[0]);
    const keyframeHandoff = states[0].data.scriptCreativeHandoff as VideoCreativeHandoff;
    expect({ ...keyframeHandoff, referenceResponsibilities: keyframeHandoff.referenceResponsibilities?.filter(item => item.scope === 'storyboard') })
      .toEqual(opening?.data.scriptCreativeHandoff);
    expect(keyframeHandoff.referenceResponsibilities?.filter(item => item.scope === 'keyframe').map(item => [item.imageNumber, item.role]))
      .toEqual([[1, 'frame_design'], [2, 'character']]);
    expect(states[0].data.referenceImageUrls).toEqual(['/opening-0.png', rows()[0].character_image_1]);
    expect(states[0].data.prompt).toContain('角色 阿雀 引用@图片2');
    const videoHandoff = (firstVideo?.data.shotContractFacts as { creativeHandoff?: VideoCreativeHandoff } | undefined)?.creativeHandoff;
    expect({ ...videoHandoff, referenceResponsibilities: videoHandoff?.referenceResponsibilities?.filter(item => item.scope === 'storyboard') })
      .toEqual(opening?.data.scriptCreativeHandoff);
    expect(videoHandoff?.referenceResponsibilities?.filter(item => item.scope === 'video').map(item => [item.imageNumber, item.role]))
      .toEqual([[1, 'opening_frame'], [2, 'state_frame'], [3, 'character']]);
    expect(firstVideo?.data.prompt).toContain('本镜观看目的：看清起跳前的支撑关系');
    expect(firstVideo?.data.prompt).not.toContain('动作结果出现后切到落点');
    expect(firstVideo?.data.prompt).toContain('@图片2是本镜状态关键帧');
    expect(useCanvasStore.getState().edges.some(edge => edge.source === states[0].id && edge.target === firstVideo?.id)).toBe(true);
    expect(generate({ rows: planned, generateImages: true })).toMatchObject({ ok: true, armed: 0 });
  });

  it('首图已齐时重新生成分镜也会补状态图，不重跑已完成首图', () => {
    seedScriptNode();
    const planned = rows().map((row, index) => index === 0 ? {
      ...row, keyframe_plan: [{ role: 'ending_state', state: '滑板落地仍向前滑', purpose: '锁定落点', required: false }],
    } : row);
    useCanvasStore.getState().updateNodeData(SCRIPT_NODE_ID, { scriptResult: { title: '测试', rows: planned } });
    const result = generate({ rows: planned });
    if (!result.ok || !result.groupId) throw new Error('missing storyboard');
    result.nodeIds.forEach(id => markGenerated(id));
    const rearmed = regenerateStoryboardGroupImages(result.groupId);
    expect(rearmed).toMatchObject({ ok: true, armed: 1 });
    result.nodeIds.forEach(id => expect(useCanvasStore.getState().nodes.find(node => node.id === id)?.data.canvas_auto_generate_once).toBe(false));
    const state = useCanvasStore.getState().nodes.find(node => node.data.scriptShotKeyframeSourceNodeId);
    expect(state?.data.canvas_auto_generate_once).toBe(true);
    expect(rearmed.focusNodeIds).toContain(state?.id);
  });

  beforeEach(() => {
    resetStoryboardSettle();
    pendingMembers = [];
    seedScriptNode();
  });

  it('逐行建图片节点、成组、连线，并在脚本节点记录分镜组 id', () => {
    const result = generate({});
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.mode).toBe('created');
    expect(result.nodeIds).toHaveLength(2);
    expect(result.groupLabel).toBe('分镜图 · 天空之跃');

    const state = useCanvasStore.getState();
    const shots = imageNodes();
    expect(shots).toHaveLength(2);
    // 提示词取「分镜提示词」，缺失回落「画面描述」（对齐 LibTV），
    // 行里有角色图时前面再挂资产图锚定块。
    expect(shots[0].data.prompt).toContain('角色 阿雀 的参考图是 图片1');
    expect(shots[0].data.prompt).toContain('不复制参考图的拼版');
    expect(shots[0].data.prompt).toContain('镜头推近祠堂');
    expect(shots[1].data.prompt).toContain('收尾');
    expect(shots.every(shot => String(shot.data.prompt).includes('RENDER QUALITY:'))).toBe(true);
    expect(shots.every((shot) => shot.data.model === MODEL)).toBe(true);
    // 角色图 → 节点参考图；行标识 → scriptRowKey（等价 LibTV scriptRowHiddenUuid）
    expect(shots[0].data.referenceImageUrl).toBe('a.png');
    expect(shots[0].data.scriptRowKey).toBe('shot:1');
    expect(shots[1].data.scriptRowKey).toBe('shot:2');

    const group = state.nodes.find(isStoryboardGroupNode);
    expect(group?.id).toBe(result.groupId);
    expect(group?.data.label).toBe('分镜图 · 天空之跃');

    // 分组容器不参与连线，血缘按逐镜边表达：脚本 → 每张分镜图。
    // （并组时 store 会把成员两端的边改指到组节点，所以这里查血缘标记而不是目标。）
    const storyboardEdges = state.edges.filter(
      (candidate) => candidate.data?.role === 'storyboard',
    );
    expect(storyboardEdges).toHaveLength(2);
    // 分镜图都成了分镜组成员（隐藏成员由组节点画缩略图）。
    expect(state.nodes.filter((node) => node.parentId === result.groupId)).toHaveLength(2);

    const scriptNode = state.nodes.find((node) => node.id === SCRIPT_NODE_ID);
    expect(scriptNode?.data.linkedImageGroupId).toBe(result.groupId);
    expect(scriptNode?.data.imageGenConfig).toMatchObject({
      model: MODEL,
      aspectRatio: '16:9',
    });
  });

  it('分镜图组落在脚本节点右侧，且行序 = 宫格读取顺序', () => {
    const result = generate({});
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    const state = useCanvasStore.getState();
    const group = state.nodes.find((node) => node.id === result.groupId);
    expect(group?.position.x).toBeGreaterThan(SCRIPT_SIZE.width);
    // 成员在组内按宫格排（第 2 镜在第 1 镜右侧、同一行）。
    const members = result.nodeIds.map((nodeId) =>
      state.nodes.find((node) => node.id === nodeId),
    );
    expect(members[0]?.position.y).toBe(members[1]?.position.y);
    expect(members[1]?.position.x).toBeGreaterThan(members[0]?.position.x ?? 0);
  });

  it('generateImages 为真时先散开出图、全部落定后才并回分镜图组', () => {
    const quiet = generate({ generateImages: false });
    expect(quiet.ok).toBe(true);
    if (quiet.ok) expect(quiet.armed).toBe(0);
    // 不出图 → 直接并组（没有中间态）。
    expect(useCanvasStore.getState().nodes.filter(isStoryboardGroupNode)).toHaveLength(1);
    expect(imageNodes().every((shot) => shot.data.canvas_auto_generate_once === false)).toBe(true);

    resetStoryboardSettle();
    seedScriptNode();

    const loud = generate({ generateImages: true });
    expect(loud.ok).toBe(true);
    if (!loud.ok) return;
    expect(loud.armed).toBe(2);
    pendingMembers = loud.nodeIds;
    // 出图阶段：成员是可挂载的普通节点（隐藏成员不挂载 → 不会提交），还没有组。
    const during = useCanvasStore.getState();
    expect(during.nodes.filter(isStoryboardGroupNode)).toHaveLength(0);
    const shots = imageNodes();
    expect(shots.every((shot) => shot.data.canvas_auto_generate_once === true)).toBe(true);
    expect(shots.every((shot) => shot.hidden !== true)).toBe(true);
    expect(shots.every((shot) => !shot.parentId)).toBe(true);

    // 只出一张还不收：还有一张没落定。
    markGenerated(loud.nodeIds[0]);
    expect(settleAll()).toBeNull();
    expect(useCanvasStore.getState().nodes.filter(isStoryboardGroupNode)).toHaveLength(0);

    markGenerated(loud.nodeIds[1]);
    const groupId = settleAll();
    expect(groupId).toBeTruthy();
    const settled = useCanvasStore.getState();
    expect(settled.nodes.filter(isStoryboardGroupNode)).toHaveLength(1);
    // 收拢后成员回到组里当隐藏缩略图，脚本节点记下新的组 id。
    expect(settled.nodes.filter((node) => node.parentId === groupId)).toHaveLength(2);
    expect(settled.nodes.find((node) => node.id === SCRIPT_NODE_ID)?.data.linkedImageGroupId)
      .toBe(groupId);
    expect(collectStoryboardDownloadItems(groupId ?? '').map((item) => item.url))
      .toEqual(['shot.png', 'shot.png']);
  });

  it('行数没变 → 原地重跑未出图的：先把组散开（否则隐藏成员不会提交），不丢已出的图', () => {
    const first = generate({});
    expect(first.ok).toBe(true);
    if (!first.ok) return;
    markGenerated(first.nodeIds[0]);

    const second = generate({ generateImages: true });
    expect(second.ok).toBe(true);
    if (!second.ok) return;
    expect(second.mode).toBe('rearmed');
    expect(second.armed).toBe(1);
    pendingMembers = second.nodeIds;

    const state = useCanvasStore.getState();
    expect(imageNodes()).toHaveLength(2);
    // 重跑期间组被散开成可见节点（隐藏成员不挂载 → 永远不会提交出图）。
    expect(state.nodes.filter(isStoryboardGroupNode)).toHaveLength(0);
    expect(
      state.nodes.filter((node) => second.nodeIds.includes(node.id)).every(
        (node) => node.hidden !== true && !node.parentId,
      ),
    ).toBe(true);
    // 已出图的那张不被重新排队，未出图的那张被排队。
    expect(state.nodes.find((node) => node.id === first.nodeIds[0])?.data.canvas_auto_generate_once)
      .toBe(false);
    expect(state.nodes.find((node) => node.id === first.nodeIds[1])?.data.canvas_auto_generate_once)
      .toBe(true);
    // 已出的图还在（重跑不重算已完成的）。
    expect(storyboardMemberImageUrl(
      state.nodes.find((node) => node.id === first.nodeIds[0])!,
    )).toBe('shot.png');

    // 两张都落定后自动并回分镜图组。
    markGenerated(first.nodeIds[1], 'shot-2.png');
    const groupId = settleAll();
    expect(useCanvasStore.getState().nodes.filter(isStoryboardGroupNode)).toHaveLength(1);
    expect(collectStoryboardDownloadItems(groupId ?? '').map((item) => item.url))
      .toEqual(['shot.png', 'shot-2.png']);
  });

  it('行数变了 → 重建整组，避免行与图错位', () => {
    const first = generate({});
    expect(first.ok).toBe(true);
    if (!first.ok) return;

    const second = generate({ rows: threeRows(), generateImages: true });
    expect(second.ok).toBe(true);
    if (!second.ok) return;
    expect(second.mode).toBe('rebuilt');
    expect(second.nodeIds).toHaveLength(3);
    pendingMembers = second.nodeIds;

    const state = useCanvasStore.getState();
    expect(state.nodes.some((node) => node.id === first.groupId)).toBe(false);
    expect(imageNodes()).toHaveLength(3);
    expect(state.nodes.filter(isStoryboardGroupNode)).toHaveLength(0);

    second.nodeIds.forEach((nodeId, index) => markGenerated(nodeId, `shot-${index}.png`));
    const groupId = settleAll();
    expect(useCanvasStore.getState().nodes.filter(isStoryboardGroupNode)).toHaveLength(1);
    expect(useCanvasStore.getState().nodes.find((node) => node.id === SCRIPT_NODE_ID)
      ?.data.linkedImageGroupId).toBe(groupId);
    expect(collectStoryboardDownloadItems(groupId ?? '')).toHaveLength(3);
  });

  it('行数相同但行键整批换过 → 同样重建，不能把旧身份留在节点上', () => {
    const first = generate({});
    expect(first.ok).toBe(true);
    if (!first.ok) return;

    const renamed = [
      { shot_no: '11', visual_description: '换过的第一镜' },
      { shot_no: '12', visual_description: '换过的第二镜' },
    ];
    const second = generate({ rows: renamed });
    expect(second.ok).toBe(true);
    if (!second.ok) return;
    expect(second.mode).toBe('rebuilt');
    expect(
      useCanvasStore.getState().nodes.some((node) => node.id === first.groupId),
    ).toBe(false);
    expect(imageNodes().map((node) => node.data.scriptRowKey).sort()).toEqual([
      'shot:11',
      'shot:12',
    ]);
  });

  it('有稳定 shot_id 时只改镜号，仍复用原分镜节点', () => {
    const stableRows: FreezoneStoryScriptRow[] = [
      { shot_id: 'shot_stable_1', shot_no: '1', visual_description: '开场' },
      { shot_id: 'shot_stable_2', shot_no: '2', visual_description: '收尾' },
    ];
    const first = generate({ rows: stableRows });
    expect(first.ok).toBe(true);
    if (!first.ok) return;

    const renamed = stableRows.map((row, index) => ({
      ...row,
      shot_no: String(11 + index),
      display_shot_no: String(11 + index),
    }));
    const second = generate({ rows: renamed });
    expect(second.ok).toBe(true);
    if (!second.ok) return;
    expect(second.mode).toBe('rearmed');
    expect(second.nodeIds).toEqual(first.nodeIds);
    expect(imageNodes().map((node) => node.data.scriptShotId).sort()).toEqual([
      'shot_stable_1',
      'shot_stable_2',
    ]);
  });

  it('重建不会把新组顶到旧组下面（旧组在算落位前就该消失）', () => {
    const first = generate({});
    expect(first.ok).toBe(true);
    if (!first.ok) return;
    // 第一次的组在这次调用里会被删掉，所以先记下它的落位。
    const firstY = useCanvasStore
      .getState()
      .nodes.find((node) => node.id === first.groupId)?.position.y;
    expect(firstY).toBeDefined();

    const second = generate({ rows: threeRows() });
    expect(second.ok).toBe(true);
    if (!second.ok) return;
    const secondY = useCanvasStore
      .getState()
      .nodes.find((node) => node.id === second.groupId)?.position.y;
    // 旧组 1600+ px 高，若被当成障碍物，第二次会整整下移一个组高。
    expect(Math.abs((secondY ?? 0) - (firstY ?? 0))).toBeLessThan(100);
  });

  it('没选模型 / 没有分镜行时给出可读原因，不动画布', () => {
    const noModel = generate({ config: { model: undefined } });
    expect(noModel.ok).toBe(false);
    if (!noModel.ok) expect(noModel.reason).toContain('分镜图模型');

    const noRows = generate({ rows: [] });
    expect(noRows.ok).toBe(false);
    expect(imageNodes()).toHaveLength(0);

    const missingNode = generateScriptStoryboard({
      scriptNodeId: 'nope',
      rows: rows(),
      scriptTitle: '天空之跃',
      scriptSize: SCRIPT_SIZE,
      config: { model: MODEL },
    });
    expect(missingNode.ok).toBe(false);
    expect(imageNodes()).toHaveLength(0);
  });

  it('只有一镜时不成组，只留一个图片节点并直连脚本节点', () => {
    const result = generate({ rows: [{ shot_no: '1', visual_description: '唯一一镜' }] });
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.groupId).toBeNull();
    expect(imageNodes()).toHaveLength(1);
    const edge = useCanvasStore
      .getState()
      .edges.find((candidate) => candidate.target === result.nodeIds[0]);
    expect(edge?.source).toBe(SCRIPT_NODE_ID);
  });

  it('多角色镜头把该行**全部**角色图带成参考图组（对齐 LibTV params.imageList）', () => {
    // 回归用例：此前只取 characterImageUrls[0]，双人镜头的第二个角色在提交时整条
    // 丢掉。第 1 张仍是 referenceImageUrl（@图片1 的编号基线），整组另存一处。
    const result = generate({ rows: twoCharacterRows() });
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    const shot = useCanvasStore
      .getState()
      .nodes.find((node) => node.id === result.nodeIds[0])!;
    expect(shot.data.referenceImageUrl).toBe('a.png');
    expect(shot.data.referenceImageUrls).toEqual(['a.png', 'b.png']);
    // 快照同口径：整组按序拼接（只比首张会让「第二张角色卡换了」漏判）。
    expect(shot.data.scriptRowReference).toBe('a.png\nb.png');
  });

  it('该行没有角色图时才退到「参考」列，且不带出空数组', () => {
    const result = generate({
      rows: [{ shot_no: '1', visual_description: '空镜', reference: 'frame.png' }],
    });
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    const shot = useCanvasStore
      .getState()
      .nodes.find((node) => node.id === result.nodeIds[0])!;
    expect(shot.data.referenceImageUrl).toBe('frame.png');
    expect(shot.data.referenceImageUrls).toEqual(['frame.png']);
  });

  it('角色图与参考列都空时字段存在但不带任何 URL（不落 undefined 假值）', () => {
    // 单独一个画布：同一画布上再跑一次同张数的 generate 会走「原地重跑」分支，
    // 那条分支刻意不重写节点数据（见 refreshRowSnapshotPatch 的所有权判据）。
    useCanvasStore.getState().setCanvasData([], []);
    seedScriptNode();
    const result = generate({ rows: [{ shot_no: '1', visual_description: '纯文字' }] });
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    const plain = useCanvasStore
      .getState()
      .nodes.find((node) => node.id === result.nodeIds[0])!;
    expect(plain.data.referenceImageUrl).toBeNull();
    expect(plain.data.referenceImageUrls).toEqual([]);
    expect(plain.data.scriptRowReference).toBeNull();
  });
});

describe('分镜图 · 出图状态判定', () => {
  beforeEach(() => {
    resetStoryboardSettle();
    pendingMembers = [];
    seedScriptNode();
  });

  it('参考图不算「已出图」', () => {
    const result = generate({});
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    const shot = useCanvasStore
      .getState()
      .nodes.find((node) => node.id === result.nodeIds[0])!;
    expect(shot.data.referenceImageUrl).toBe('a.png');
    expect(storyboardMemberImageUrl(shot)).toBeNull();
    expect(storyboardMemberNeedsImage(shot)).toBe(true);
    // 批量下载也不能把角色参考图当成分镜图存下来。
    expect(collectStoryboardDownloadItems(result.groupId ?? '')).toEqual([]);
  });

  it('出图失败的分镜图仍算「要重出」', () => {
    const result = generate({});
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    markGenerated(result.nodeIds[0]);
    let shot = useCanvasStore.getState().nodes.find((node) => node.id === result.nodeIds[0])!;
    expect(storyboardMemberNeedsImage(shot)).toBe(false);

    useCanvasStore.getState().updateNodeData(result.nodeIds[0], { generationError: '上游 500' });
    shot = useCanvasStore.getState().nodes.find((node) => node.id === result.nodeIds[0])!;
    expect(storyboardMemberNeedsImage(shot)).toBe(true);
  });

  it('重新生成分镜图：散开、只排队缺图/失败的，全都出好了就什么都不做', () => {
    const result = generate({});
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    const groupId = result.groupId ?? '';

    const first = regenerateStoryboardGroupImages(groupId);
    expect(first.ok).toBe(true);
    expect(first.armed).toBe(2);
    // 重跑前组被散开（隐藏成员不挂载 → 不提交），成员变成可挂载的普通节点。
    expect(useCanvasStore.getState().nodes.filter(isStoryboardGroupNode)).toHaveLength(0);
    expect(
      imageNodes().every(
        (node) => node.hidden !== true && node.data.canvas_auto_generate_once === true,
      ),
    ).toBe(true);

    // 两张都出好、收拢回组之后，再点就无事可做。
    result.nodeIds.forEach((nodeId, index) => markGenerated(nodeId, `shot-${index}.png`));
    const regrouped = collapseStoryboardMembers({
      scriptNodeId: SCRIPT_NODE_ID,
      memberIds: result.nodeIds,
      groupLabel: '分镜图 · 天空之跃',
      aspectKey: '16:9',
    });
    expect(regrouped).toBeTruthy();

    const second = regenerateStoryboardGroupImages(regrouped ?? '');
    expect(second.ok).toBe(true);
    expect(second.armed).toBe(0);
    expect(useCanvasStore.getState().nodes.filter(isStoryboardGroupNode)).toHaveLength(1);

    const missing = regenerateStoryboardGroupImages('nope');
    expect(missing.ok).toBe(false);
  });

  it('组级重跑只认自己脚本的资产图，不拿别的脚本同名角色图误判过期', () => {
    resetStoryboardSettle();
    seedScriptNode();
    const secondaryScriptId = seedSecondaryScriptNode();
    const secondaryRows = [
      { shot_no: '1', visual_description: '同名角色登场', character_1: '阿雀' },
      { shot_no: '2', visual_description: '收尾' },
    ];
    useCanvasStore.getState().updateNodeData(SCRIPT_NODE_ID, {
      scriptResult: { title: '同名角色', rows: secondaryRows },
      scriptTitle: '同名角色',
    });

    // 另一脚本先生成了「阿雀」的资产图；当前脚本的台账必须装作看不见它。
    useCanvasStore.getState().addNode(
      CANVAS_NODE_TYPES.imageGen,
      { x: 800, y: 0 },
      {
        scriptAssetId: scriptAssetId('character', '阿雀'),
        scriptAssetOwnerId: secondaryScriptId,
        imageUrl: 'foreign-aque.png',
      },
    );
    expect(
      useCanvasStore.getState().nodes.some(
        (node) => node.data.scriptAssetOwnerId === secondaryScriptId,
      ),
    ).toBe(true);
    expect(scriptAssetImageNodes(secondaryScriptId).has(scriptAssetId('character', '阿雀'))).toBe(true);
    expect(scriptAssetImageNodes(SCRIPT_NODE_ID).has(scriptAssetId('character', '阿雀'))).toBe(false);

    const generated = generate({ rows: secondaryRows });
    expect(generated.ok).toBe(true);
    if (!generated.ok) return;
    const groupId = generated.groupId;
    expect(groupId).toBeTruthy();
    generated.nodeIds.forEach((nodeId, index) => markGenerated(nodeId, `own-${index}.png`));

    const regen = regenerateStoryboardGroupImages(groupId ?? '');
    expect(regen.ok).toBe(true);
    // 自己这一镜没有资产图变化；foreign-aque.png 不该把这张已出图重新排队。
    expect(regen.armed).toBe(0);
    const ownShot = useCanvasStore.getState().nodes.find((node) => node.id === generated.nodeIds[0]);
    expect(ownShot?.data.referenceImageUrl).toBeNull();
    expect(ownShot?.data.scriptRowReference).toBeNull();
  });
});
