// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { beforeEach, describe, expect, it } from 'vitest';

import type { FreezoneStoryScriptRow } from '@/api/ops';
import {
  CANVAS_NODE_TYPES,
  isImageGenNode,
  isVideoComposeNode,
  isVideoNode,
} from '@/features/canvas/domain/canvasNodes';
import {
  generateScriptStoryboard,
  regenerateStoryboardGroupImages,
  STORYBOARD_EDGE_ROLE,
} from '@/features/canvas/nodes/script/generateScriptStoryboard';
import {
  collapseStoryboardMembers,
  resetStoryboardSettle,
} from '@/features/canvas/nodes/script/storyboardSettle';
import {
  SCRIPT_SHOT_CONTINUITY_EDGE_ROLE,
  SCRIPT_SHOT_KEYFRAME_EDGE_ROLE,
  SCRIPT_SHOT_VIDEO_EDGE_ROLE,
  SCRIPT_SHOT_VIDEO_DURATION_FIELD,
  SCRIPT_SHOT_VIDEO_FIRST_FRAME_FIELD,
  SCRIPT_SHOT_VIDEO_IMAGE_FIELD,
  SCRIPT_SHOT_VIDEO_ROW_FINGERPRINT_FIELD,
  SCRIPT_SHOT_VIDEO_ASSET_SNAPSHOT_FIELD,
  SCRIPT_SHOT_VIDEO_ROW_FIELD,
  SCRIPT_SHOT_VIDEO_SOURCE_FIELD,
  buildScriptShotVideoSpecs,
  type ScatterScriptShotVideosParams,
  scriptShotVideoPrompt,
  planScriptShotVideos,
  scatterScriptShotVideos,
  scriptShotVideoChain,
  graphSliceOf,
  scriptShotVideoNodesInRowOrder,
  shotVideoAcceptsChainReference,
  scriptTailReferenceStaleReason,
} from '@/features/canvas/nodes/script/scriptShotVideos';
import { scriptRowFingerprint } from '@/features/canvas/nodes/script/scriptRowFingerprint';
import { scriptShotKeyframes } from '@/features/canvas/nodes/script/scriptShotKeyframes';
import { scriptAssetVideoMode, scriptVideoExecutionPromptMatches, scriptVideoExecutionPromptKey } from '@/features/canvas/nodes/script/scriptShotVideoReferences';
import { collectScriptAssetLedger, scriptAssetImageNodes, sha256Hex } from '@/features/canvas/nodes/script/scriptAssets';
import { saveAssetReview } from '@/features/canvas/nodes/script/scriptAssetReviewReceipt';
import {
  SCRIPT_COMPOSE_SOURCE_FIELD,
  assembleScriptFilm,
  scriptFilmStatus,
} from '@/features/canvas/nodes/script/scriptShotCompose';
import { useCanvasStore } from '@/stores/canvasStore';

/**
 * 「脚本分镜图 → 逐镜视频 → 成片」这条链（T-039）。
 *
 * 锁的是**生命周期与准入**，不是公式：
 * - 首帧只能来自上游节点 → 派生时必须有一条真实的分镜图 → 视频节点边；
 * - 只有出好图的镜能派（没图的镜跳过，不悄悄降级成文生视频）；
 * - 提示词取该行的 `video_motion_prompt`，**不是**画面提示词；
 * - 重复点不重复派生；行集合换过就重建；提示词改过只重跑那一镜；
 * - 边被删能补回来（身份在节点自己身上，不依赖边）；
 * - 成片按**行序**接进合成节点，且幂等。
 */

const SCRIPT_ID = 'script-node';
const SCRIPT_SIZE = { width: 800, height: 400 };
const IMAGE_MODEL = 'direct/image-abc123';
const VIDEO_MODEL = 'direct/video-abc123';

function generatedVideoPatch(nodeId: string, url: string) {
  const node = useCanvasStore.getState().nodes.find(node => node.id === nodeId)!;
  return { videoUrl: url, videoGenerationSource: {
    schema: 'video_generation_source.v1' as const, task_type: 'freezone_video_gen' as const, job_id: `job-${nodeId}`,
    output_url: url, execution_prompt_sha256: sha256Hex(scriptVideoExecutionPromptKey(String(node.data.prompt))),
  } };
}

function completeShot(nodeId: string, url: string) {
  useCanvasStore.getState().updateNodeData(nodeId, generatedVideoPatch(nodeId, url));
}

function rows(): FreezoneStoryScriptRow[] {
  return [
    {
      shot_no: '1',
      duration: '4',
      shot: '特写',
      visual_description: '祠堂门环被雨打湿',
      shot_prompt: '推近祠堂门环',
      video_motion_prompt: '镜头缓缓推近门环，雨丝斜落',
    },
    {
      shot_no: '2',
      duration: '5',
      shot: '中景',
      visual_description: '阿雀推门而入',
      shot_prompt: '中景拍阿雀推门',
      video_motion_prompt: '门被推开，阿雀侧身走入',
    },
  ];
}

describe('道具状态连续性', () => {
  it('缺失模式声明默认全能参考，保留明确限制', () => {
    expect(scriptAssetVideoMode(4)).toBe('allReference');
    expect(scriptAssetVideoMode(4, {})).toBe('allReference');
    expect(scriptAssetVideoMode(4, { referenceLimits: { allReference: { image: 3 } } })).toBeNull();
    expect(scriptAssetVideoMode(4, { supportedModes: [] })).toBeNull();
    expect(scriptAssetVideoMode(4, { supportedModes: ['imageToVideo'] })).toBeNull();
  });
  it.each([true, false])('声明模式=%s：视频接入三族资产且提示词按真实图片顺序引用', (declared) => {
    const source = [{ shot_no: '1', shot_prompt: '阿波站在桥上', video_motion_prompt: '阿波踩着滑板向前', duration: '5', character_1: '阿波', scene_tags: '高架桥', prop_tags: '滑板' }];
    seedScriptNode(source);
    const assets = collectScriptAssetLedger(source, new Map()).all;
    const state = useCanvasStore.getState();
    state.setCanvasData([...state.nodes, ...assets.map((asset, index) => ({
      id: `asset-${index}`, type: CANVAS_NODE_TYPES.imageGen, position: { x: 900, y: index * 400 },
      data: { imageUrl: `asset-${index}.png`, scriptAssetId: asset.id, scriptAssetOwnerId: SCRIPT_ID, scriptAssetRevision: asset.revision, scriptAssetContentHash: asset.contentHash, scriptAssetIdentityLocks: asset.identityLocks },
    }))], []);
    const [frame] = seedStoryboardImages();
    const capabilities = { ...(declared ? { supportedModes: ['allReference'] } : {}), referenceLimits: { allReference: { image: 4 } }, durationOptions: [5] };
    expect(scatterVideos({ modelCapabilities: capabilities }).ok).toBe(true);
    const video = videoNodes()[0];
    expect(video.data.genMode).toBe('allReference');
    expect(video.data.referenceOrder).toEqual([frame, 'asset-0', 'asset-1', 'asset-2']);
    expect(video.data.prompt).toContain('角色阿波引用@图片2');
    expect(video.data.prompt).toContain('场景高架桥引用@图片3');
    expect(video.data.prompt).toContain('道具滑板引用@图片4');
    const count = useCanvasStore.getState().edges.length;
    expect(scatterVideos({ modelCapabilities: capabilities }).ok).toBe(true);
    expect(useCanvasStore.getState().edges).toHaveLength(count);
    useCanvasStore.getState().updateNodeData(video.id, { referenceOrder: [frame, 'asset-2', 'asset-0', 'asset-1'] });
    scatterVideos({ modelCapabilities: capabilities });
    expect(videoNodes()[0].data.prompt).toContain('道具滑板引用@图片2');
    const before = JSON.stringify(useCanvasStore.getState().nodes);
    expect(scatterVideos({ generateVideos: true, modelCapabilities: { ...capabilities, referenceLimits: { allReference: { image: 3 } } } }).ok).toBe(false);
    expect(JSON.stringify(useCanvasStore.getState().nodes)).toBe(before);
    expect(scatterVideos({ generateVideos: true, modelCapabilities: capabilities }).ok).toBe(true);
    expect(videoNodes()[0].data.prompt).toContain('道具滑板引用@图片2');
  });
  it('快照计划使用快照资产，订阅链包含本脚本资产而不包含别的脚本资产', () => {
    const source = [{ shot_no: '1', shot_prompt: '滑板静置', video_motion_prompt: '滑板向前滑行', duration: '5', prop_tags: '滑板' }];
    seedScriptNode(source);
    const asset = collectScriptAssetLedger(source, new Map()).props[0];
    const store = useCanvasStore.getState();
    store.setCanvasData([...store.nodes,
      { id: 'asset', type: CANVAS_NODE_TYPES.imageGen, position: { x: 900, y: 0 }, data: { imageUrl: 'board.png', scriptAssetId: asset.id, scriptAssetOwnerId: SCRIPT_ID, scriptAssetRevision: asset.revision, scriptAssetContentHash: asset.contentHash, scriptAssetIdentityLocks: asset.identityLocks } },
      { id: 'other-asset', type: CANVAS_NODE_TYPES.imageGen, position: { x: 900, y: 800 }, data: { imageUrl: 'other.png', scriptAssetId: asset.id, scriptAssetOwnerId: 'other-script' } },
    ], []);
    seedStoryboardImages();
    const state = useCanvasStore.getState();
    const snapshot = structuredClone({ nodes: state.nodes, edges: state.edges });
    const selected = graphSliceOf(scriptShotVideoChain(SCRIPT_ID, snapshot.nodes, snapshot.edges));
    expect(selected.nodes.map(node => node.id)).toContain('asset');
    expect(selected.nodes.map(node => node.id)).not.toContain('other-asset');
    expect(planScriptShotVideos(SCRIPT_ID, snapshot).ok).toBe(true);
    const current = collectScriptAssetLedger(source, scriptAssetImageNodes(SCRIPT_ID)).props[0];
    saveAssetReview(current, { status: 'blocked', checks: { clean: false, identity: true, structure: true }, views: [], notes: '噪点' });
    expect(planScriptShotVideos(SCRIPT_ID).ok).toBe(false);
    expect(planScriptShotVideos(SCRIPT_ID, snapshot).ok).toBe(true);
    const updated = useCanvasStore.getState();
    expect(planScriptShotVideos(SCRIPT_ID, graphSliceOf(scriptShotVideoChain(SCRIPT_ID, updated.nodes, updated.edges))).ok).toBe(false);
  });
  it.each([{ isGenerating: true }, { canvas_auto_generate_once: true }, { generationError: '生成失败' }])('首帧仍有旧图片但状态为%j时不能出视频', state => {
    seedScriptNode();
    seedStoryboardImages();
    for (const node of imageNodes()) useCanvasStore.getState().updateNodeData(node.id, state);
    expect(planScriptShotVideos(SCRIPT_ID).ok).toBe(false);
    const before = JSON.stringify({ nodes: useCanvasStore.getState().nodes, edges: useCanvasStore.getState().edges });
    expect(scatterVideos({ generateVideos: true }).ok).toBe(false);
    expect(JSON.stringify({ nodes: useCanvasStore.getState().nodes, edges: useCanvasStore.getState().edges })).toBe(before);
  });
  it('资产退回后阻止旧分镜继续用于出视频，计划与执行均不改画布', () => {
    const source = [{ shot_no: '1', shot_prompt: '滑板静置', video_motion_prompt: '滑板向前滑行', duration: '5', prop_tags: '滑板' }];
    seedScriptNode(source);
    const asset = collectScriptAssetLedger(source, new Map()).props[0];
    const store = useCanvasStore.getState();
    store.setCanvasData([...store.nodes, { id: 'asset', type: CANVAS_NODE_TYPES.imageGen, position: { x: 900, y: 0 }, data: { imageUrl: 'board.png', scriptAssetId: asset.id, scriptAssetOwnerId: SCRIPT_ID, scriptAssetRevision: asset.revision, scriptAssetContentHash: asset.contentHash, scriptAssetIdentityLocks: asset.identityLocks } }], []);
    generateScriptStoryboard({ scriptNodeId: SCRIPT_ID, rows: source, scriptSize: SCRIPT_SIZE, config: { model: IMAGE_MODEL }, generateImages: false });
    for (const node of useCanvasStore.getState().nodes.filter(node => node.data.scriptRowPrompt)) useCanvasStore.getState().updateNodeData(node.id, { imageUrl: 'old-shot.png' });
    expect(planScriptShotVideos(SCRIPT_ID).ok).toBe(true);
    const current = collectScriptAssetLedger(source, scriptAssetImageNodes(SCRIPT_ID)).props[0];
    expect(saveAssetReview(current, { status: 'blocked', checks: { clean: false, identity: true, structure: true }, views: [], notes: '暗部噪点' })).toBeNull();
    const before = JSON.stringify({ nodes: useCanvasStore.getState().nodes, edges: useCanvasStore.getState().edges });
    const plan = planScriptShotVideos(SCRIPT_ID);
    expect(plan.ok).toBe(false);
    if (!plan.ok) expect(plan.reason).toContain('1');
    const result = scatterScriptShotVideos({ scriptNodeId: SCRIPT_ID, model: VIDEO_MODEL, generateVideos: false });
    expect(result.ok).toBe(false);
    expect(JSON.stringify({ nodes: useCanvasStore.getState().nodes, edges: useCanvasStore.getState().edges })).toBe(before);
  });
  it.each(['无', 'none', 'N/A', '-'])('占位运动稿%s不阻止真实动作回落', marker => {
    const result = scriptShotVideoPrompt({ video_motion_prompt: marker, character_action: '抬眼看门口，松开握紧的手', camera_movement: '固定机位' });
    expect(result.source).toBe('fallback');
    expect(result.prompt).toContain('抬眼看门口，松开握紧的手');
    expect(result.prompt).toContain('摄影机安排：固定机位');
    expect(result.prompt).not.toContain(`\n${marker}\n`);
  });
  it('静止观察是有效运动安排，不替换成其他动作', () => {
    const row = { video_motion_prompt: '固定机位，人物静止观察', character_action: '快步离开' };
    const result = scriptShotVideoPrompt(row);
    expect(result.source).toBe('motion');
    expect(result.prompt).toContain('人物静止观察');
    expect(result.prompt).not.toContain('快步离开');
    expect(buildScriptShotVideoSpecs([row])[0].action).toBe(row.video_motion_prompt);
    expect(row.character_action).toBe('快步离开');
  });
  it.each(['all_reference', 'first_last_frame', 'text_to_video'])('结构化%s运动稿动作段成为执行事实，摄影/声音/对白不混入', generationMode => {
    const action = '阿波右手释放栏杆，双脚由滑板支撑。'.repeat(40) + '视线由[红门 + 水塔]转向南侧落点';
    const camera = '先固定于水塔西侧，随后跟拍到南侧平台，保持遮挡时点';
    const motion = `[摄影机运镜轨迹与速度：${camera}] + [主体极其具体的物理动作细节或状态变化：${action}] + [环境动态：树叶飘动] + [音效：脚步声] + [对话台词：阿波说“等等”] + [时长：5s]`;
    const row = { generation_mode: generationMode, shot_prompt: '起始仍握栏杆', video_motion_prompt: motion, character_action: '持续握紧栏杆' };
    const spec = buildScriptShotVideoSpecs([row])[0];
    expect(spec.action).toBe(action);
    expect(spec.cameraMovement).toBe(camera);
    expect(spec.prompt).toContain(action);
    expect(spec.prompt).not.toContain('持续握紧栏杆');
    expect(spec.action).not.toContain('树叶飘动');
    expect(spec.action).not.toContain('等等');
    expect(spec.action).not.toContain('时长');
    expect(spec.continuityOut.camera_endpoint).toBe(camera);
  });
  it('括号外动作正文及多个明确动作段完整保存，显式摄影仍优先', () => {
    const spec = buildScriptShotVideoSpecs([{ character_action: '向右离开', camera_movement: '固定机位',
      video_motion_prompt: '[运镜轨迹] 跟拍向右 + [主体动作] 抬眼看向红门 + [物理动作与状态变化：右手松开栏杆，双脚仍踩板] + [时长：5s]' }])[0];
    expect(spec.action).toBe('抬眼看向红门；右手松开栏杆，双脚仍踩板');
    expect(spec.cameraMovement).toBe('固定机位');
    expect(spec.prompt).toContain('[运镜轨迹] 固定机位');
    expect(spec.prompt).not.toContain('跟拍向右');
  });
  it('正文与动作事实共同去掉旧装备块，空运动稿沿用概述回退', () => {
    const oldState = '<character_state>旧红夹克，首帧保持握杆</character_state>';
    const row = { character_action: '继续握杆', character_state_start: { 阿波: '蓝夹克' },
      video_motion_prompt: `[主体动作：先听门声，然后松手${oldState}]` };
    const spec = buildScriptShotVideoSpecs([row])[0];
    expect(spec.action).toBe('先听门声，然后松手');
    expect(spec.prompt).not.toContain('旧红夹克');
    const fallback = buildScriptShotVideoSpecs([{ ...row, video_motion_prompt: oldState }])[0];
    expect(fallback.promptSource).toBe('fallback');
    expect(fallback.action).toBe(row.character_action);
    for (const marker of ['无', 'none', 'N/A', '']) {
      const legacy = buildScriptShotVideoSpecs([{ character_action: '保持观察', video_motion_prompt: marker }])[0];
      expect(legacy.action).toBe('保持观察');
    }
  });
  it('派生镜头事实不把占位文字当动作或摄影机轨迹', () => {
    const spec = buildScriptShotVideoSpecs([{ video_motion_prompt: '无', character_action: 'none', shot_prompt: 'N/A', visual_description: '雨滴落在门环上' }])[0];
    expect(spec.action).toBe('雨滴落在门环上');
    expect(spec.cameraMovement).toBe('保持构图稳定，仅跟随本镜主体动作');
    expect(spec.promptSource).toBe('fallback');
    expect(spec.prompt).toContain('雨滴落在门环上');
  });
  it('把起始、变化、结束状态带进视频提示词', () => {
    const result = scriptShotVideoPrompt({
      shot_no: '1',
      video_motion_prompt: '镜头跟随蒲扇缓慢展开',
      prop_state_start: '静置合拢',
      prop_state_change: '手部展开扇面',
      prop_state_end: '手持展开',
    });
    expect(result.prompt).toContain('道具连续性：起始：静置合拢 → 变化：手部展开扇面 → 结束：手持展开');
    expect(result.source).toBe('motion');
  });
  it('把观看目的交给当前镜，把切镜交接保留在结构化合同', () => {
    const row = {
      video_motion_prompt: '人物抬头看向门外',
      shot_purpose: '让观众先看懂人物发现了门外的异常',
      cut_reason: '在视线锁定后切到门外，下一镜补充被发现的对象',
      film_language: '主观视线匹配',
      sequence_ids: ['S1'],
    };
    const result = scriptShotVideoPrompt(row);
    expect(result.prompt).toContain('本镜观看目的：让观众先看懂人物发现了门外的异常');
    expect(result.prompt).not.toContain(row.cut_reason);
    const spec = buildScriptShotVideoSpecs([row])[0];
    expect(spec.creativeHandoff).toMatchObject({
      shotPurpose: row.shot_purpose,
      cutReason: row.cut_reason,
      filmLanguage: row.film_language,
      sequenceIds: row.sequence_ids,
    });
  });
  it('保留缺项状态的时间含义，不把结束当起始或占位符当事实', () => {
    const result = scriptShotVideoPrompt({ video_motion_prompt: '落地滑行', prop_state_start: '无', prop_state_end: '滑板完整', end_state: '双脚踩在板上' });
    expect(result.prompt).toContain('道具连续性：结束：滑板完整');
    expect(result.prompt).toContain('可见状态接力：结束：双脚踩在板上');
    expect(result.prompt).not.toContain('起始：');
  });
  it('明确运镜优先于从动作文本推测摄影机', () => {
    const spec = buildScriptShotVideoSpecs([{ video_motion_prompt: '镜头跟拍跑动', camera_movement: '固定机位，主体从左侧进入右侧离开', end_state: '离开右侧画框' }])[0];
    expect(spec.cameraMovement).toBe('固定机位，主体从左侧进入右侧离开');
    expect(spec.continuityOut.camera_endpoint).toBe(spec.cameraMovement);
  });
  it('长执行事实保留末尾路线、视线、接触、支撑和连续衔接', () => {
    const subject = '展示平台与栏杆的相对位置。'.repeat(20) + '南侧落点';
    const action = '沿平台继续滑行，'.repeat(80) + '右手释放栏杆，双脚仍由滑板支撑，视线锁定南侧红门';
    const camera = '沿外墙平稳跟拍，'.repeat(50) + '到南侧平台停机，机位仍在水塔西侧';
    const ending = '平台、滑板和栏杆轮廓清晰。'.repeat(40) + '人物落稳南侧平台，手已离杆，双脚踩板，看向红门';
    const transition = '保持落点与视线方向。'.repeat(20) + 'continuous_action';
    const specs = buildScriptShotVideoSpecs([
      { visual_description: subject, character_action: action, camera_movement: camera, end_state: ending,
        transition_plan: transition, video_motion_prompt: `[主体动作：${action}]` },
      { video_motion_prompt: '继续看向红门' },
    ]);
    expect(specs[0].subject).toBe(subject);
    expect(specs[0].action).toBe(action);
    expect(specs[0].cameraMovement).toBe(camera);
    expect(specs[0].endState).toBe(ending);
    expect(specs[0].transition).toBe(transition);
    expect(specs[0].continuityOut).toMatchObject({ action_state: ending, camera_endpoint: camera, seam: 'continuous' });
    expect(specs[1].continuityIn).toMatchObject({ subject, action_state: ending, frame: ending, seam: transition });
  });
  it('历史缺项回退只规范空白，保留实际选中的完整文字', () => {
    const image = '平台的地标、人物间距与支撑关系，'.repeat(50) + '双脚由滑板支撑';
    const camera = '镜头沿外墙平稳跟拍，'.repeat(50) + '最后停在水塔西侧';
    const spec = buildScriptShotVideoSpecs([{ shot_prompt: `  ${image}\n `, camera_movement: `\n${camera} ` }])[0];
    expect(spec.subject).toBe(image);
    expect(spec.action).toBe(image);
    expect(spec.cameraMovement).toBe(camera);
    expect(spec.endState).toBe(`本镜动作完成：${image}`);
  });
  it('结构化运动提示同步明确运镜，不保留两个相反的运镜指令', () => {
    const motion = '[明确的摄影机运镜轨迹与速度：跟拍推进] + [主体极其具体的物理动作细节或状态变化：从左到右跑动] + [环境物理动态：树叶摆动] + [音效与氛围描述：脚步声] + [对话台词与语气：无] + [时长：5s]';
    const result = scriptShotVideoPrompt({ video_motion_prompt: motion, camera_movement: '固定机位' });
    expect(result.prompt).toContain('[明确的摄影机运镜轨迹与速度：固定机位]');
    expect(result.prompt).not.toContain('跟拍推进');
    expect(result.prompt).toContain('从左到右跑动');
    expect(result.prompt).toContain('脚步声');
  });
  it('非结构化旧运动稿也不会丢掉明确运镜', () => {
    const result = scriptShotVideoPrompt({ video_motion_prompt: '阿波向前滑行', camera_movement: '固定机位' });
    expect(result.prompt).toContain('摄影机安排：固定机位');
    expect(scriptShotVideoPrompt({ camera_movement: '固定机位' }).prompt).toBe('');
  });
  it('缺运动稿时仍把已有动作和摄影安排交给模型', () => {
    const result = scriptShotVideoPrompt({
      shot_prompt: '阿波站在门边，红夹克，室内暖光',
      character_action: '阿波先看向门外，手松开门把，退半步',
      camera_movement: '固定机位',
    });
    expect(result.prompt).toContain('阿波先看向门外，手松开门把，退半步');
    expect(result.prompt).toContain('摄影机安排：固定机位');
    expect(result.prompt).not.toContain('室内暖光');
    expect(result.source).toBe('fallback');
  });
  it('无动作的旧稿保留画面描述及明确运镜，占位动作不作事实', () => {
    const result = scriptShotVideoPrompt({
      visual_description: '雨水顺门环流下', character_action: '无', camera_movement: '缓慢推进',
    });
    expect(result.prompt).toContain('雨水顺门环流下');
    expect(result.prompt).toContain('摄影机安排：缓慢推进');
    expect(result.prompt).not.toContain('主体动作：无');
  });
  it('文生回落同时保留画面设计与动作而不冒充已有运动稿', () => {
    const result = scriptShotVideoPrompt({
      generation_mode: 'text_to_video', shot_prompt: '雨中铜门环',
      character_action: '雨水沿铜环汇聚后滴落', camera_movement: '固定机位',
    });
    expect(result.prompt).toContain('画面设计：雨中铜门环');
    expect(result.prompt).toContain('雨水沿铜环汇聚后滴落');
    expect(result.prompt).toContain('摄影机安排：固定机位');
    expect(result.source).toBe('fallback');
  });
});

/** 行数相同但行标识整批换过 —— 判定必须落到「重建」而不是「原地重跑」。 */
function renamedRows(): FreezoneStoryScriptRow[] {
  return [
    { shot_no: '11', duration: '3', visual_description: '换过的第一镜', video_motion_prompt: '换过的第一镜运镜' },
    { shot_no: '12', duration: '3', visual_description: '换过的第二镜', video_motion_prompt: '换过的第二镜运镜' },
  ];
}

function seedScriptNode(seedRows: FreezoneStoryScriptRow[] = rows()) {
  useCanvasStore.getState().setCanvasData(
    [
      {
        id: SCRIPT_ID,
        type: CANVAS_NODE_TYPES.script,
        position: { x: 0, y: 0 },
        style: { width: SCRIPT_SIZE.width, height: SCRIPT_SIZE.height },
        data: { scriptResult: { title: '天空之跃', rows: seedRows }, scriptTitle: '天空之跃' },
      },
    ] as never,
    [],
  );
}

function threeRows(): FreezoneStoryScriptRow[] {
  return [
    ...rows(),
    { shot_no: '3', duration: '4', visual_description: '收镜', video_motion_prompt: '镜头拉远' },
  ];
}

/**
 * 铺好前置条件：把分镜行派生成分镜图节点并逐个标记「已出图」。
 * 逐镜出视频的准入就是「这一镜有分镜图」，所以每条用例都得先走这一步。
 *
 * `scatter: true` 走**出图散开**那条路（`generateImages`）——那时成员还没并组、
 * `linkedImageGroupId` 仍是 null，是「只靠血缘边认出这批图」的唯一真实状态。
 */
function seedStoryboardImages(
  options: {
    generated?: boolean;
    rows?: FreezoneStoryScriptRow[];
    scatter?: boolean;
  } = {},
) {
  const scriptRows = options.rows ?? (useCanvasStore.getState().nodes.find(node => node.id === SCRIPT_ID)?.data.scriptResult as { rows: FreezoneStoryScriptRow[] }).rows;
  const result = generateScriptStoryboard({
    scriptNodeId: SCRIPT_ID,
    rows: scriptRows,
    scriptTitle: '天空之跃',
    scriptSize: SCRIPT_SIZE,
    config: { model: IMAGE_MODEL, aspectRatio: '16:9' },
    generateImages: options.scatter,
  });
  if (!result.ok) throw new Error(`前置分镜图派生失败：${result.reason}`);
  if (options.generated !== false) {
    result.nodeIds.forEach((nodeId, index) => {
      useCanvasStore.getState().updateNodeData(nodeId, { imageUrl: `frame-${index}.png`, isGenerating: false, canvas_auto_generate_once: false, generationError: null });
    });
  }
  return result.nodeIds;
}

/** 脚本行整批换过之后重建这批分镜图（行数变化 / 用户重跑都会走到这里）。 */
function rebuildStoryboardImages(nextRows: FreezoneStoryScriptRow[]) {
  const stale = imageNodes().map((node) => node.id);
  if (stale.length > 0) useCanvasStore.getState().deleteNodes(stale);
  return seedStoryboardImages({ rows: nextRows });
}

function videoNodes() {
  return useCanvasStore.getState().nodes.filter(isVideoNode);
}

function imageNodes() {
  return useCanvasStore.getState().nodes.filter(isImageGenNode);
}

function setRows(nextRows: FreezoneStoryScriptRow[]) {
  useCanvasStore.getState().updateNodeData(SCRIPT_ID, {
    scriptResult: { title: '天空之跃', rows: nextRows },
  });
}

function scatterVideos(options: {
  modelCapabilities?: ScatterScriptShotVideosParams['modelCapabilities'];
  generateVideos?: boolean;
  model?: string | null;
  aspectRatio?: string;
  defaultDurationSec?: number;
} = {}) {
  return scatterScriptShotVideos({
    scriptNodeId: SCRIPT_ID,
    model: options.model === undefined ? VIDEO_MODEL : options.model,
    modelCapabilities: options.modelCapabilities,
    aspectRatio: options.aspectRatio,
    defaultDurationSec: options.defaultDurationSec,
    generateVideos: options.generateVideos,
  });
}

/** 分镜图 → 某视频节点的那条首帧边（方向与角色都要对）。 */
function firstFrameEdge(videoNodeId: string) {
  return useCanvasStore
    .getState()
    .edges.find(
      (edge) => edge.target === videoNodeId && edge.data?.role === SCRIPT_SHOT_VIDEO_EDGE_ROLE,
    );
}

function videoByRowKey(): Map<string, NonNullable<ReturnType<typeof videoNodes>[number]>> {
  return new Map(
    videoNodes().map((node) => [String(node.data[SCRIPT_SHOT_VIDEO_ROW_FIELD]), node]),
  );
}

describe('逐镜出视频 · 从脚本分镜图派生视频节点', () => {
  beforeEach(() => {
    resetStoryboardSettle();
    seedScriptNode();
  });

  it('实际生成正文绑定包含时长扩展，手改保留，显式重做释放旧冻结且幂等', () => {
    seedStoryboardImages();
    const capabilities = { durationOptions: [5], supportedModes: ['allReference'] };
    expect(scatterVideos({ modelCapabilities: capabilities }).ok).toBe(true);
    const video = videoNodes()[0];
    const boundPrompt = video.data.shotContractFacts!.executionPrompt!;
    expect(boundPrompt).toContain('生成片段完整时长 5s');
    expect(scriptVideoExecutionPromptMatches(video.data)).toBe(true);
    useCanvasStore.getState().updateNodeData(video.id, {
      videoUrl: '/old.mp4', prompt: '用户改为固定机位，人物停住',
      shotContract: { old: true }, shot_contract: { old: true },
    });
    expect(scatterVideos({ modelCapabilities: capabilities }).ok).toBe(true);
    expect(videoNodes()[0].data.prompt).toContain('用户改为固定机位');
    expect(videoNodes()[0].data.shotContractFacts!.executionPrompt).toBe(boundPrompt);
    const plan = planScriptShotVideos(SCRIPT_ID, useCanvasStore.getState(), { modelCapabilities: capabilities });
    expect(plan.ok && plan.pendingCount).toBe(2);
    expect(scatterVideos({ generateVideos: true, modelCapabilities: capabilities }).ok).toBe(true);
    const refreshed = videoNodes()[0];
    expect(scriptVideoExecutionPromptMatches(refreshed.data)).toBe(true);
    expect(refreshed.data.shotContract).toBeNull();
    expect(refreshed.data.shot_contract).toBeNull();
    expect(refreshed.data.videoUrl).toBe('/old.mp4');
    expect(refreshed.id).toBe(video.id);
    expect(assembleScriptFilm({ scriptNodeId: SCRIPT_ID }).ok).toBe(false);
    videoNodes().forEach(node => useCanvasStore.getState().updateNodeData(node.id, { ...generatedVideoPatch(node.id, '/done.mp4'), canvas_auto_generate_once: false }));
    const again = scatterVideos({ generateVideos: true, modelCapabilities: capabilities });
    expect(again.ok && again.armed).toBe(0);
  });

  it('动作概述与运动稿不同时，旧节点重做同步事实与正文且保留原始概述', () => {
    const action = '人物留在平台，先听门外声音，再看向红门，右手松开栏杆，双脚保持滑板支撑';
    const row = { ...rows()[0], character_action: '向右快步离开',
      video_motion_prompt: `[摄影机运镜：固定机位] + [主体动作：${action}] + [时长：4s]`,
      start_state: '右手握杆，双脚踩板', end_state: '右手离杆，双脚仍踩板，视线停在红门' };
    seedScriptNode([row]);
    seedStoryboardImages();
    expect(scatterVideos().ok).toBe(true);
    const video = videoNodes()[0];
    expect(video.data.action).toBe(action);
    expect(video.data.shotContractFacts?.action).toBe(action);
    expect(video.data.prompt).toContain(action);
    expect(video.data.prompt).not.toContain(row.character_action);
    useCanvasStore.getState().updateNodeData(video.id, { videoUrl: '/previous.mp4', action: row.character_action,
      shotContractFacts: { ...video.data.shotContractFacts!, action: row.character_action } });
    const stale = planScriptShotVideos(SCRIPT_ID);
    expect(stale.ok && stale.pendingCount).toBe(1);
    const refreshed = scatterVideos({ generateVideos: true });
    expect(refreshed.ok && refreshed.armed).toBe(1);
    expect(videoNodes()[0].id).toBe(video.id);
    expect(videoNodes()[0].data.action).toBe(action);
    expect(videoNodes()[0].data.shotContractFacts?.action).toBe(action);
    expect(videoNodes()[0].data.videoUrl).toBe('/previous.mp4');
    expect((useCanvasStore.getState().nodes.find(node => node.id === SCRIPT_ID)?.data.scriptResult as { rows: FreezoneStoryScriptRow[] }).rows[0].character_action).toBe(row.character_action);
    useCanvasStore.getState().updateNodeData(video.id, { canvas_auto_generate_once: false });
    completeShot(video.id, '/updated.mp4');
    const again = scatterVideos({ generateVideos: true });
    expect(again.ok && again.armed).toBe(0);
  });

  it('旧截短执行事实按原重跑入口更新完整且幂等，保留已有视频与节点身份', () => {
    const row = { ...rows()[0], visual_description: '雨中平台的空间关系。'.repeat(30) + '南侧红门',
      character_action: '沿平台继续滑行，'.repeat(80) + '手已离杆，双脚由滑板支撑',
      camera_movement: '沿外墙平稳跟拍，'.repeat(50) + '停在水塔西侧',
      end_state: '平台与栏杆的轮廓仍清晰。'.repeat(40) + '落稳南侧平台，看向红门',
      transition_plan: '保持落点与视线。'.repeat(20) + 'direct_cut' };
    row.video_motion_prompt = `[主体动作：${row.character_action}]`;
    seedScriptNode([row]);
    seedStoryboardImages();
    expect(scatterVideos().ok).toBe(true);
    const video = videoNodes()[0];
    const fullFacts = video.data.shotContractFacts!;
    expect(fullFacts).toMatchObject({ subject: row.visual_description, action: row.character_action,
      cameraMovement: row.camera_movement, lastFrame: row.end_state, transition: row.transition_plan });
    useCanvasStore.getState().updateNodeData(video.id, { videoUrl: '/existing.mp4', prompt: '用户手工保留的正文',
      shotContractFacts: { ...fullFacts, subject: row.visual_description.slice(0, 160), action: row.character_action.slice(0, 500),
        cameraMovement: row.camera_movement.slice(0, 300), lastFrame: row.end_state.slice(0, 400), transition: row.transition_plan.slice(0, 120) } });
    expect(scatterVideos().ok).toBe(true);
    expect(videoNodes()[0].data.prompt).toContain('用户手工保留的正文');
    expect(videoNodes()[0].data.shotContractFacts?.lastFrame).toBe(row.end_state.slice(0, 400));
    const refreshed = scatterVideos({ generateVideos: true });
    expect(refreshed.ok && refreshed.armed).toBe(1);
    const updated = videoNodes()[0];
    expect(updated.id).toBe(video.id);
    expect(updated.data.shotContractFacts).toEqual(fullFacts);
    expect(updated.data.prompt).toContain(row.end_state);
    expect(updated.data.videoUrl).toBe('/existing.mp4');
    useCanvasStore.getState().updateNodeData(video.id, { canvas_auto_generate_once: false });
    completeShot(video.id, '/updated.mp4');
    const again = scatterVideos({ generateVideos: true });
    expect(again.ok && again.armed).toBe(0);
  });

  it('明确文生镜头无分镜图也能规划派生，保留画面设计与动作', () => {
    const row = { ...rows()[0], generation_mode: 'text_to_video', start_state: '门环静置，雨水沿边缘流下' };
    seedScriptNode([row]);
    const capabilities = { supportedModes: ['textToVideo'], durationOptions: [4, 5] };
    const plan = planScriptShotVideos(SCRIPT_ID, undefined, { modelCapabilities: capabilities });
    expect(plan.ok).toBe(true);
    if (!plan.ok) return;
    expect(plan.shotCount).toBe(1);
    expect(plan.rows[0].issues).not.toContain('noImage');
    const result = scatterVideos({ generateVideos: true, modelCapabilities: capabilities });
    expect(result.ok).toBe(true);
    const video = videoNodes()[0];
    expect(video.data.genMode).toBe('textToVideo');
    expect(video.data.prompt).toContain(row.shot_prompt);
    expect(video.data.prompt).toContain(row.video_motion_prompt);
    expect(video.data.shotContractFacts?.firstFrame).toBe(row.start_state);
    expect(firstFrameEdge(video.id)).toBeUndefined();
    useCanvasStore.getState().updateNodeData(video.id, { ...generatedVideoPatch(video.id, '/done.mp4'), canvas_auto_generate_once: false });
    const again = scatterVideos({ generateVideos: true, modelCapabilities: capabilities });
    expect(again.ok && again.armed).toBe(0);
    setRows([{ ...row, video_motion_prompt: '雨滴落下，镜头拉远' }]);
    const revised = scatterVideos({ generateVideos: true, modelCapabilities: capabilities });
    expect(revised.ok && revised.armed).toBe(1);
    expect(videoNodes()[0].id).toBe(video.id);
    expect(videoNodes()[0].data.videoUrl).toBe('/done.mp4');
  });

  it('文生不静默绕过模型支持或缺失画面设计', () => {
    seedScriptNode([{ ...rows()[0], generation_mode: 'text_to_video', start_state: '门环静置' }]);
    expect(scatterVideos({ generateVideos: true }).ok).toBe(false);
    expect(scatterVideos({ generateVideos: true, modelCapabilities: { supportedModes: ['imageToVideo'] } }).ok).toBe(false);
    expect(videoNodes()).toHaveLength(0);
    expect(scriptShotVideoPrompt({ generation_mode: 'text_to_video', video_motion_prompt: '向前运动' }).prompt).toBe('');
  });

  it('文生缺起始状态或锁定资产时拒绝计费，不用文字冒充参考图', () => {
    const capabilities = { supportedModes: ['textToVideo'], durationOptions: [4, 5] };
    seedScriptNode([{ ...rows()[0], generation_mode: 'text_to_video' }]);
    const noStart = scatterVideos({ generateVideos: true, modelCapabilities: capabilities });
    expect(!noStart.ok && noStart.reason).toContain('可见起始状态');
    seedScriptNode([{ ...rows()[0], generation_mode: 'text_to_video', start_state: '阿波站立', character_1: '阿波', character_description_1: '绿色毛发红夹克' }]);
    const locked = scatterVideos({ generateVideos: true, modelCapabilities: capabilities });
    expect(!locked.ok && locked.reason).toContain('需要锁定的资产');
    expect(videoNodes()).toHaveLength(0);
  });

  it('混合文生图生各用自己的输入，不给文生挂图片', () => {
    const mixed = [{ ...rows()[0], generation_mode: 'text_to_video', start_state: '门环静置' }, rows()[1]];
    seedScriptNode(mixed);
    seedStoryboardImages({ rows: mixed });
    const result = scatterVideos({ generateVideos: true, modelCapabilities: { supportedModes: ['textToVideo', 'imageToVideo'], durationOptions: [4, 5] } });
    expect(result.ok).toBe(true);
    const videos = videoByRowKey();
    expect(videos.get('shot:1')?.data.genMode).toBe('textToVideo');
    expect(firstFrameEdge(videos.get('shot:1')!.id)).toBeUndefined();
    expect(videos.get('shot:2')?.data.genMode).toBe('imageToVideo');
    expect(firstFrameEdge(videos.get('shot:2')!.id)).toBeDefined();
  });

  it('文生切回图生用本镜图，已有图片连线切文生时停住并保留素材', () => {
    const row = { ...rows()[0], generation_mode: 'text_to_video', start_state: '门环静置' };
    const capabilities = { supportedModes: ['textToVideo', 'imageToVideo'], durationOptions: [4, 5] };
    seedScriptNode([row]);
    scatterVideos({ generateVideos: true, modelCapabilities: capabilities });
    const video = videoNodes()[0];
    useCanvasStore.getState().updateNodeData(video.id, { videoUrl: '/old.mp4', canvas_auto_generate_once: false });
    const imageRows = [{ ...row, generation_mode: 'image_to_video' }];
    setRows(imageRows);
    seedStoryboardImages({ rows: imageRows });
    const toImage = scatterVideos({ generateVideos: true, modelCapabilities: capabilities });
    expect(toImage.ok && toImage.armed).toBe(1);
    expect(videoNodes()[0].data.genMode).toBe('imageToVideo');
    expect(firstFrameEdge(video.id)).toBeDefined();
    useCanvasStore.getState().updateNodeData(video.id, { canvas_auto_generate_once: false });
    setRows([row]);
    const toText = scatterVideos({ generateVideos: true, modelCapabilities: capabilities });
    expect(toText.ok).toBe(false);
    expect(!toText.ok && toText.reason).toContain('仍连接图片');
    expect(videoNodes()[0].data.videoUrl).toBe('/old.mp4');
    expect(firstFrameEdge(video.id)).toBeDefined();
    expect(videoNodes()[0].data.canvas_auto_generate_once).toBe(false);
  });

  it('行指纹与 Python 固定向量一致', () => {
    const row = {
      shot_id: 'shot_fixed',
      shot_no: 7,
      duration: '5',
      visual_description: '雨落',
      video_motion_prompt: '镜头前推',
    } as FreezoneStoryScriptRow;
    expect(scriptRowFingerprint(row)).toBe(
      '81e112e6e5f275c0575addce41d42cc7f6035b42728a2e29046da940961c30be',
    );
  });

  it('导演规划尚未同步时拒绝派生与正式成片，保留全部节点和边', () => {
    seedStoryboardImages();
    const imageIds = useCanvasStore.getState().nodes.filter(isImageGenNode).map(node => node.id);
    const groupId = useCanvasStore.getState().mergeStoryboardGroup(imageIds);
    expect(groupId).toBeTruthy();
    useCanvasStore.getState().updateNodeData(SCRIPT_ID, { linkedImageGroupId: groupId });
    useCanvasStore.getState().updateNodeData(SCRIPT_ID, { scriptDirectorPlanNeedsSync: true });
    const before = useCanvasStore.getState();
    const nodes = before.nodes;
    const edges = before.edges;
    const images = generateScriptStoryboard({ scriptNodeId: SCRIPT_ID, rows: rows(), scriptSize: SCRIPT_SIZE, config: { model: IMAGE_MODEL }, generateImages: true });
    for (const result of [images, scatterVideos(), assembleScriptFilm({ scriptNodeId: SCRIPT_ID })]) {
      expect(result.ok).toBe(false);
      if (!result.ok) expect(result.reason).toContain('请先重新生成脚本');
    }
    expect(useCanvasStore.getState().nodes).toBe(nodes);
    expect(useCanvasStore.getState().edges).toBe(edges);
    expect(regenerateStoryboardGroupImages(groupId!).reason).toContain('请先重新生成脚本');
    expect(useCanvasStore.getState().nodes).toBe(nodes);
    expect(useCanvasStore.getState().edges).toBe(edges);
    useCanvasStore.getState().updateNodeData(SCRIPT_ID, { scriptDirectorPlanNeedsSync: false });
    expect(scatterVideos().ok).toBe(true);
  });

  it('逐镜建视频节点、连首帧边，首帧取该镜分镜图', () => {
    seedStoryboardImages();
    const result = scatterVideos();
    expect(result.ok).toBe(true);
    if (!result.ok) return;

    expect(result.mode).toBe('created');
    expect(result.nodeIds).toHaveLength(2);
    expect(result.armed).toBe(0);
    expect(result.skippedNoImage).toBe(0);
    expect(result.skippedNoPrompt).toBe(0);

    const videos = videoNodes();
    expect(videos).toHaveLength(2);
    expect(videos.every((node) => node.data.genMode === 'allReference')).toBe(true);
    expect(videos.every((node) => node.data.model === VIDEO_MODEL)).toBe(true);
    expect(videos.every((node) => node.data.count === 1)).toBe(true);
    expect(videos.every((node) => node.data.deliverySpec?.aspectRatio === '16:9')).toBe(true);
    expect(videos.every((node) => node.data.shotContractFacts?.shotId)).toBe(true);
    // 只建节点时不该把付费标志打上。
    expect(videos.every((node) => node.data.canvas_auto_generate_once === false)).toBe(true);

    const byKey = videoByRowKey();
    // 提示词取该行的**视频运动**提示词，不是画面提示词。
    expect(byKey.get('shot:1')?.data.prompt).toContain(scriptShotVideoPrompt(rows()[0]).prompt);
    expect(byKey.get('shot:2')?.data.prompt).toContain(scriptShotVideoPrompt(rows()[1]).prompt);
    expect(byKey.get('shot:1')?.data.prompt).toContain('@图片1是本镜起始画面参考');
    expect(byKey.get('shot:1')?.data.prompt).not.toBe('推近祠堂门环');
    // 时长按该行的时长列。
    expect(byKey.get('shot:1')?.data.durationSec).toBe(4);
    expect(byKey.get('shot:2')?.data.durationSec).toBe(5);

    // 首帧：一条真实的分镜图 → 视频节点边，身份字段指向同一个来源节点。
    const frame = firstFrameEdge(result.nodeIds[0]);
    expect(frame).toBeTruthy();
    const sourceImage = useCanvasStore
      .getState()
      .nodes.find((node) => node.id === frame?.source);
    expect(sourceImage && isImageGenNode(sourceImage)).toBe(true);
    expect(videos[0].data[SCRIPT_SHOT_VIDEO_IMAGE_FIELD]).toBe(sourceImage?.id);
    expect(sourceImage?.data.imageUrl).toBeTruthy();
    // 归属写在节点自己身上：脚本节点的订阅选择器（`scriptShotVideoChain`）靠这个字段
    // 认出哪些视频是自己的 —— 只认边的话，用户手删一条边那镜就从账上消失了。
    expect(videos.every((node) => node.data[SCRIPT_SHOT_VIDEO_SOURCE_FIELD] === SCRIPT_ID)).toBe(true);
    // 留档镜号 / 景别，便于排错时对上表。
    expect(videos[0].data.shot_size).toBe('特写');
  });

  it('两个脚本节点各自成片链，A 的计划和重建不能夺走 B 的镜头视频', () => {
    seedStoryboardImages();
    const first = scatterVideos();
    expect(first.ok).toBe(true);
    if (!first.ok) return;

    const secondScriptId = useCanvasStore.getState().addNode(
      CANVAS_NODE_TYPES.script,
      { x: 1600, y: 0 },
      {
        scriptResult: { title: '第二脚本', rows: rows() },
        scriptTitle: '第二脚本',
      } as never,
    );
    expect(secondScriptId).toBeTruthy();
    if (!secondScriptId) return;
    const secondStoryboard = generateScriptStoryboard({
      scriptNodeId: secondScriptId,
      rows: rows(),
      scriptTitle: '第二脚本',
      scriptSize: SCRIPT_SIZE,
      config: { model: IMAGE_MODEL, aspectRatio: '16:9' },
    });
    expect(secondStoryboard.ok).toBe(true);
    if (!secondStoryboard.ok) return;
    secondStoryboard.nodeIds.forEach((nodeId, index) => {
      useCanvasStore.getState().updateNodeData(nodeId, { imageUrl: `b-frame-${index}.png` });
    });
    const secondVideos = scatterScriptShotVideos({
      scriptNodeId: secondScriptId,
      model: VIDEO_MODEL,
    });
    expect(secondVideos.ok).toBe(true);
    if (!secondVideos.ok) return;
    expect(videoNodes()).toHaveLength(4);

    const planA = planScriptShotVideos(SCRIPT_ID);
    expect(planA.ok).toBe(true);
    if (!planA.ok) return;
    expect(planA.derivedCount).toBe(2);

    const againA = scatterVideos();
    expect(againA.ok).toBe(true);
    if (!againA.ok) return;
    expect(againA.mode).toBe('rearmed');
    expect(againA.addedCount).toBe(0);
    expect(videoNodes()).toHaveLength(4);
    secondVideos.nodeIds.forEach((nodeId) => {
      expect(useCanvasStore.getState().nodes.some((node) => node.id === nodeId)).toBe(true);
    });
  });

  it('没有分镜图的行不出现（不降级成文生视频），弹层账本如实计数', () => {
    const imageIds = seedStoryboardImages({ generated: false });
    useCanvasStore.getState().updateNodeData(imageIds[0], { imageUrl: 'frame-0.png' });

    const plan = planScriptShotVideos(SCRIPT_ID);
    expect(plan.ok).toBe(true);
    if (!plan.ok) return;
    expect(plan.shotCount).toBe(1);
    expect(plan.pendingCount).toBe(1);
    expect(plan.skippedNoImage).toBe(1);

    const result = scatterVideos();
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.nodeIds).toHaveLength(1);
    expect(result.skippedNoImage).toBe(1);
    expect(videoNodes()).toHaveLength(1);
  });

  it('后补出来的分镜图增量接上，不重建也不丢已出好的视频', () => {
    const imageIds = seedStoryboardImages({ generated: false });
    useCanvasStore.getState().updateNodeData(imageIds[0], { imageUrl: 'frame-0.png' });

    const first = scatterVideos({ generateVideos: true });
    expect(first.ok).toBe(true);
    if (!first.ok) return;
    expect(first.mode).toBe('created');
    expect(first.addedCount).toBe(1);
    const firstVideoId = first.nodeIds[0];
    completeShot(firstVideoId, 'v-0.mp4');

    useCanvasStore.getState().updateNodeData(imageIds[1], { imageUrl: 'frame-1.png' });
    const plan = planScriptShotVideos(SCRIPT_ID);
    expect(plan.ok).toBe(true);
    if (!plan.ok) return;
    expect(plan.mode).toBe('regenerate');
    expect(plan.willRebuild).toBe(false);
    expect(plan.derivedCount).toBe(1);
    expect(plan.pendingCount).toBe(1);

    const second = scatterVideos({ generateVideos: true });
    expect(second.ok).toBe(true);
    if (!second.ok) return;
    expect(second.mode).toBe('rearmed');
    expect(second.addedCount).toBe(1);
    expect(second.armed).toBe(1);
    expect(second.nodeIds).toContain(firstVideoId);
    expect(videoNodes()).toHaveLength(2);
    expect(
      useCanvasStore.getState().nodes.find((node) => node.id === firstVideoId)?.data.videoUrl,
    ).toBe('v-0.mp4');
  });

  it('一镜分镜图都没有时拒绝派生，并给出可读原因（不动画布）', () => {
    seedStoryboardImages({ generated: false });
    const result = scatterVideos({ generateVideos: true });
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.reason).toContain('分镜图');
    expect(videoNodes()).toHaveLength(0);
  });

  it('没出图的分镜图**不会**拿它的参考图当首帧', () => {
    // 分镜图的 referenceImageUrl 是脚本行的角色图 —— 拿它当首帧会把角色头像当画面。
    const imageIds = seedStoryboardImages({ generated: false });
    useCanvasStore.getState().updateNodeData(imageIds[0], {
      referenceImageUrl: 'character-face.png',
    });
    const result = scatterVideos();
    expect(result.ok).toBe(false);
  });

  it('没有 video_motion_prompt 时退回画面提示词，并如实标出兜底计数', () => {
    setRows([
      { shot_no: '1', duration: '3', visual_description: '有运动提示词', video_motion_prompt: '运镜一' },
      { shot_no: '2', duration: '3', visual_description: '只有画面描述' },
    ]);
    seedStoryboardImages();
    const plan = planScriptShotVideos(SCRIPT_ID);
    expect(plan.ok).toBe(true);
    if (!plan.ok) return;
    expect(plan.fallbackPromptCount).toBe(1);

    const result = scatterVideos();
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    const byKey = videoByRowKey();
    expect(byKey.get('shot:1')?.data.prompt).toContain(scriptShotVideoPrompt({ video_motion_prompt: '运镜一' }).prompt);
    // 兜底用的是画面提示词（`shot_prompt` → `visual_description`）。
    expect(byKey.get('shot:2')?.data.prompt).toContain(scriptShotVideoPrompt({ visual_description: '只有画面描述' }).prompt);
  });

  it('提示词也拼不出来的行不落节点', () => {
    setRows([
      { shot_no: '1', duration: '3' },
      { shot_no: '2', duration: '3', visual_description: '有描述就够出片' },
    ]);
    seedStoryboardImages();
    const result = scatterVideos();
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.nodeIds).toHaveLength(1);
    expect(result.skippedNoPrompt).toBe(1);
    expect(String(videoNodes()[0].data[SCRIPT_SHOT_VIDEO_ROW_FIELD])).toBe('shot:2');
  });

  it('空时长回退默认值，长镜头保留导演秒数供模型准入检查', () => {
    setRows([
      { shot_no: '1', duration: '', visual_description: '没有时长' },
      { shot_no: '2', duration: '99', visual_description: '超上限' },
    ]);
    seedStoryboardImages();
    const result = scatterVideos({ defaultDurationSec: 6 });
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    const byKey = videoByRowKey();
    expect(byKey.get('shot:1')?.data.durationSec).toBe(6);
    expect(byKey.get('shot:2')?.data.durationSec).toBe(99);
    expect(byKey.get('shot:2')?.data[SCRIPT_SHOT_VIDEO_DURATION_FIELD]).toBe(99);
  });

  it('短镜和小数时长在派生时不会被取整或拉长', () => {
    setRows([
      { shot_no: '1', duration: '0.5', visual_description: '闪现' },
      { shot_no: '2', duration: '3.4', visual_description: '停留' },
    ]);
    seedStoryboardImages();
    expect(scatterVideos().ok).toBe(true);
    expect(videoByRowKey().get('shot:1')?.data.durationSec).toBe(0.5);
    expect(videoByRowKey().get('shot:2')?.data.durationSec).toBe(3.4);
    expect(videoByRowKey().get('shot:2')?.data[SCRIPT_SHOT_VIDEO_DURATION_FIELD]).toBe(3.4);
  });

  it.each(['', '-2秒', '3-5s'])('时长不明确时拒绝自动批次且不修改画布：%s', (duration) => {
    setRows([
      { shot_no: '1', duration, visual_description: '需要确定切点' },
      { shot_no: '2', duration: '5', visual_description: '第二镜' },
    ]);
    seedStoryboardImages();
    const beforeNodes = structuredClone(useCanvasStore.getState().nodes);
    const beforeEdges = structuredClone(useCanvasStore.getState().edges);
    const plan = planScriptShotVideos(SCRIPT_ID);
    expect(plan.ok).toBe(true);
    if (!plan.ok) throw new Error(plan.reason);
    expect(plan.rows[0].issues).toContain('invalidDuration');
    const result = scatterVideos({ generateVideos: true });
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.reason).toContain('没有明确的正数秒时长');
    expect(useCanvasStore.getState().nodes).toEqual(beforeNodes);
    expect(useCanvasStore.getState().edges).toEqual(beforeEdges);
    expect(scatterVideos({ generateVideos: false }).ok).toBe(true);
  });

  it.each([
    { durationOptions: [2, 3] },
    { durationOptions: [], durationParameterEnabled: false },
    { durationOptions: [4, 5], nativeAudio: 'unsupported' as const },
  ])('所选模型不能执行镜头时，审核与提交同步拒绝', (modelCapabilities) => {
    setRows(rows().map(row => ({ ...row, sound: '原生环境声' })));
    seedStoryboardImages();
    const before = structuredClone(useCanvasStore.getState().nodes);
    const plan = planScriptShotVideos(SCRIPT_ID, undefined, { modelCapabilities });
    expect(plan.ok).toBe(true);
    if (!plan.ok) throw new Error(plan.reason);
    expect(plan.rows[0].issues).toContain('modelUnsupported');
    expect(scatterVideos({ generateVideos: true, modelCapabilities }).ok).toBe(false);
    expect(useCanvasStore.getState().nodes).toEqual(before);
    expect(scatterVideos({ generateVideos: false, modelCapabilities }).ok).toBe(true);
  });

  it('短镜头按模型生成足够长素材，取用节奏、费用与重试账一致', () => {
    setRows(rows().map(row => ({ ...row, duration: '2', video_motion_prompt: '[运镜：快速推进] + [时长：2s]' })));
    seedStoryboardImages();
    const before = structuredClone(rows());
    const modelCapabilities = { durationOptions: [5, 10] };
    const plan = planScriptShotVideos(SCRIPT_ID, undefined, { modelCapabilities });
    if (!plan.ok) throw new Error(plan.reason);
    expect(plan.rows.every(row => row.durationSec === 2 && row.generationDurationSec === 5 && !row.issues.includes('modelUnsupported'))).toBe(true);
    expect(plan.plannedSeconds).toBe(10);
    expect(scatterVideos({ generateVideos: true, modelCapabilities }).ok).toBe(true);
    for (const node of videoNodes()) {
      expect(node.data.durationSec).toBe(5);
      expect(node.data.scriptShotGenerationDurationSec).toBe(5);
      expect(node.data.scriptShotRowDurationSec).toBe(2);
      expect(node.data.prompt).toContain('不要求在前 2s 提前演完');
      expect(node.data.prompt).toContain('默认完整保留并顺序拼接');
      useCanvasStore.getState().updateNodeData(node.id, { ...generatedVideoPatch(node.id, '/ready.mp4'), canvas_auto_generate_once: false });
    }
    expect(rows()).toEqual(before);
    const ready = planScriptShotVideos(SCRIPT_ID, undefined, { modelCapabilities });
    if (!ready.ok) throw new Error(ready.reason);
    expect(ready.pendingCount).toBe(0);
    expect(ready.plannedSeconds).toBe(0);
    const changed = { durationOptions: [10, 15] };
    const changedPlan = planScriptShotVideos(SCRIPT_ID, undefined, { modelCapabilities: changed });
    if (!changedPlan.ok) throw new Error(changedPlan.reason);
    expect(changedPlan.pendingCount).toBe(2);
    expect(changedPlan.plannedSeconds).toBe(20);
    expect(scatterVideos({ generateVideos: true, modelCapabilities: changed }).ok).toBe(true);
    expect(videoNodes().every(node => node.data.durationSec === 10 && node.data.scriptShotRowDurationSec === 2)).toBe(true);
  });

  it('模型更换支持档位后审核恢复，显式关闭声音可用无声模型', () => {
    seedStoryboardImages();
    expect(scatterVideos({ modelCapabilities: { durationOptions: [4, 5] } }).ok).toBe(true);
    for (const node of videoNodes()) useCanvasStore.getState().updateNodeData(node.id, { generateAudioUserSet: true, generateAudio: false });
    const modelCapabilities = { durationOptions: [4, 5], nativeAudio: 'unsupported' as const };
    const plan = planScriptShotVideos(SCRIPT_ID, undefined, { modelCapabilities });
    if (!plan.ok) throw new Error(plan.reason);
    expect(plan.rows.every(row => !row.issues.includes('modelUnsupported'))).toBe(true);
    expect(scatterVideos({ generateVideos: true, modelCapabilities }).ok).toBe(true);
  });

  it('generateVideos 为真时给每条打上一次性提交标志，armed 等于条数', () => {
    seedStoryboardImages();
    const result = scatterVideos({ generateVideos: true });
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.armed).toBe(2);
    expect(videoNodes().every((node) => node.data.canvas_auto_generate_once === true)).toBe(true);
  });

  it('重复点不会重复派生（幂等），只按需重新排队', () => {
    seedStoryboardImages();
    const first = scatterVideos();
    expect(first.ok).toBe(true);
    if (!first.ok) return;

    first.nodeIds.forEach((nodeId, index) => {
      completeShot(nodeId, `v-${index}.mp4`);
    });

    const second = scatterVideos({ generateVideos: true });
    expect(second.ok).toBe(true);
    if (!second.ok) return;
    expect(second.mode).toBe('rearmed');
    expect([...second.nodeIds].sort()).toEqual([...first.nodeIds].sort());
    // 都出齐了 → 一条都不该重新排队（不给付费接口白跑）。
    expect(second.armed).toBe(0);
    expect(videoNodes()).toHaveLength(2);
  });

  it('没出片的那些会被重新排队，已出片的不动', () => {
    seedStoryboardImages();
    const first = scatterVideos();
    expect(first.ok).toBe(true);
    if (!first.ok) return;
    completeShot(first.nodeIds[0], 'v-0.mp4');

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
    seedStoryboardImages();
    const first = scatterVideos();
    expect(first.ok).toBe(true);
    if (!first.ok) return;
    first.nodeIds.forEach((nodeId, index) => {
      completeShot(nodeId, `v-${index}.mp4`);
    });

    const edited = rows();
    edited[1] = { ...edited[1], video_motion_prompt: '改过的运镜' };
    setRows(edited);

    const plan = planScriptShotVideos(SCRIPT_ID);
    expect(plan.ok).toBe(true);
    if (!plan.ok) return;
    expect(plan.mode).toBe('regenerate');
    expect(plan.pendingCount).toBe(1);
    expect(plan.plannedSeconds).toBe(5);
  });

  it('画面提示词改过但运动提示词没改 → 行指纹仍让那一镜重新排队', () => {
    seedStoryboardImages();
    const first = scatterVideos();
    expect(first.ok).toBe(true);
    if (!first.ok) return;
    first.nodeIds.forEach((nodeId, index) => {
      completeShot(nodeId, `v-${index}.mp4`);
    });

    const edited = rows();
    edited[1] = { ...edited[1], shot_prompt: '改成阿雀推门后停住' };
    setRows(edited);

    expect(planScriptShotVideos(SCRIPT_ID).ok).toBe(false);
    const groupId = useCanvasStore.getState().nodes.find(node => node.id === SCRIPT_ID)?.data.linkedImageGroupId as string;
    expect(regenerateStoryboardGroupImages(groupId).ok).toBe(true);
    for (const node of imageNodes().filter(node => node.data.canvas_auto_generate_once)) useCanvasStore.getState().updateNodeData(node.id, { imageUrl: `updated-${node.id}.png`, canvas_auto_generate_once: false });

    const plan = planScriptShotVideos(SCRIPT_ID);
    expect(plan.ok).toBe(true);
    if (!plan.ok) return;
    expect(plan.mode).toBe('regenerate');
    expect(plan.pendingCount).toBe(1);

    const second = scatterVideos({ generateVideos: true });
    expect(second.ok).toBe(true);
    if (!second.ok) return;
    expect(second.armed).toBe(1);
    const changed = videoByRowKey().get('shot:2');
    expect(changed?.data[SCRIPT_SHOT_VIDEO_ROW_FINGERPRINT_FIELD]).toBe(
      scriptRowFingerprint(edited[1], 1),
    );
    expect(changed?.data[SCRIPT_SHOT_VIDEO_ASSET_SNAPSHOT_FIELD]).toBeNull();
  });

  it('表里的时长改过 → 那一镜重新排队，不沿用旧时长', () => {
    seedStoryboardImages();
    const first = scatterVideos();
    expect(first.ok).toBe(true);
    if (!first.ok) return;
    first.nodeIds.forEach((nodeId, index) => {
      completeShot(nodeId, `v-${index}.mp4`);
    });

    const edited = rows();
    edited[1] = { ...edited[1], duration: '9' };
    setRows(edited);

    const plan = planScriptShotVideos(SCRIPT_ID);
    expect(plan.ok).toBe(true);
    if (!plan.ok) return;
    expect(plan.mode).toBe('regenerate');
    expect(plan.pendingCount).toBe(1);
    expect(plan.plannedSeconds).toBe(9);

    const second = scatterVideos({ generateVideos: true });
    expect(second.ok).toBe(true);
    if (!second.ok) return;
    expect(second.armed).toBe(1);
    const changed = videoByRowKey().get('shot:2');
    expect(changed?.data.durationSec).toBe(9);
    expect(changed?.data[SCRIPT_SHOT_VIDEO_DURATION_FIELD]).toBe(9);
  });

  it('分镜图原地重出 → 首帧 URL 变了，那一镜重新排队', () => {
    const imageIds = seedStoryboardImages();
    const first = scatterVideos();
    expect(first.ok).toBe(true);
    if (!first.ok) return;
    first.nodeIds.forEach((nodeId, index) => {
      completeShot(nodeId, `v-${index}.mp4`);
    });

    useCanvasStore.getState().updateNodeData(imageIds[1], {
      imageUrl: 'frame-regenerated.png',
    });

    const plan = planScriptShotVideos(SCRIPT_ID);
    expect(plan.ok).toBe(true);
    if (!plan.ok) return;
    expect(plan.pendingCount).toBe(1);

    const second = scatterVideos({ generateVideos: true });
    expect(second.ok).toBe(true);
    if (!second.ok) return;
    expect(second.armed).toBe(1);
    const changed = videoByRowKey().get('shot:2');
    expect(changed?.data[SCRIPT_SHOT_VIDEO_FIRST_FRAME_FIELD]).toBe(
      'frame-regenerated.png',
    );
    expect(firstFrameEdge(changed?.id ?? '')?.source).toBe(imageIds[1]);
  });

  it('上次失败的镜头也算未落地，会重新排队', () => {
    seedStoryboardImages();
    const first = scatterVideos();
    expect(first.ok).toBe(true);
    if (!first.ok) return;
    first.nodeIds.forEach((nodeId, index) => {
      useCanvasStore.getState().updateNodeData(nodeId, {
        ...generatedVideoPatch(nodeId, `v-${index}.mp4`),
        generationError: index === 0 ? '上游超时' : null,
      });
    });
    const plan = planScriptShotVideos(SCRIPT_ID);
    expect(plan.ok).toBe(true);
    if (!plan.ok) return;
    expect(plan.pendingCount).toBe(1);
  });

  it('行标识整批换过（数量相同）→ 重建，不把新提示词写到旧行号上', () => {
    seedStoryboardImages();
    const first = scatterVideos();
    expect(first.ok).toBe(true);
    if (!first.ok) return;
    const firstIds = [...first.nodeIds];

    setRows(renamedRows());
    rebuildStoryboardImages(renamedRows());

    const second = scatterVideos();
    expect(second.ok).toBe(true);
    if (!second.ok) return;
    expect(second.mode).toBe('rebuilt');
    const state = useCanvasStore.getState();
    firstIds.forEach((nodeId) => {
      expect(state.nodes.some((node) => node.id === nodeId)).toBe(false);
    });
    const byKey = videoByRowKey();
    expect([...byKey.keys()].sort()).toEqual(['shot:11', 'shot:12']);
    expect(byKey.get('shot:11')?.data.prompt).toContain(scriptShotVideoPrompt(renamedRows()[0]).prompt);
  });

  it('稳定 shot_id 下只改镜号，不重建也不丢原有视频节点', () => {
    const stableRows: FreezoneStoryScriptRow[] = rows().map((row, index) => ({
      ...row,
      shot_id: `shot_stable_${index + 1}`,
      shot_order: index + 1,
      display_shot_no: String(index + 1),
    }));
    setRows(stableRows);
    seedStoryboardImages({ rows: stableRows });
    const first = scatterVideos();
    expect(first.ok).toBe(true);
    if (!first.ok) return;
    first.nodeIds.forEach((nodeId, index) => {
      completeShot(nodeId, `v-${index}.mp4`);
    });

    const renamed = stableRows.map((row, index) => ({
      ...row,
      shot_no: String(11 + index),
      display_shot_no: String(11 + index),
    }));
    setRows(renamed);

    const second = scatterVideos({ generateVideos: true });
    expect(second.ok).toBe(true);
    if (!second.ok) return;
    expect(second.mode).toBe('rearmed');
    expect(second.nodeIds).toEqual(first.nodeIds);
    expect(second.armed).toBe(0);
    expect(videoNodes().map((node) => node.data.scriptShotId)).toEqual([
      'shot_stable_1',
      'shot_stable_2',
    ]);
  });

  it('分镜图重建过（图没了）→ 边跟着指向新的那张，身份字段同步更新', () => {
    seedStoryboardImages();
    const first = scatterVideos();
    expect(first.ok).toBe(true);
    if (!first.ok) return;

    const oldImages = imageNodes().map((node) => node.id);
    useCanvasStore.getState().deleteNodes(oldImages);
    expect(imageNodes()).toHaveLength(0);
    // 视频节点本身还在（分镜图被删不连带删视频）。
    expect(videoNodes()).toHaveLength(first.nodeIds.length);
    expect(
      useCanvasStore.getState().edges.filter(
        (edge) => edge.data?.role === SCRIPT_SHOT_VIDEO_EDGE_ROLE,
      ),
    ).toHaveLength(0);

    const newImages = seedStoryboardImages();
    const rearm = scatterVideos();
    expect(rearm.ok).toBe(true);
    if (!rearm.ok) return;
    expect(rearm.mode).toBe('rearmed');
    const frame = firstFrameEdge(first.nodeIds[0]);
    expect(newImages).toContain(frame?.source);
    expect(
      videoNodes().find((node) => node.id === first.nodeIds[0])
        ?.data[SCRIPT_SHOT_VIDEO_IMAGE_FIELD],
    ).toBe(frame?.source);
  });

  it('用户手删了首帧边 → 下次进来会补回来（身份存在节点上，不依赖边）', () => {
    seedStoryboardImages();
    const first = scatterVideos();
    expect(first.ok).toBe(true);
    if (!first.ok) return;
    const edge = firstFrameEdge(first.nodeIds[0]);
    expect(edge).toBeTruthy();
    if (!edge) return;
    useCanvasStore.getState().deleteEdge(edge.id);
    expect(firstFrameEdge(first.nodeIds[0])).toBeFalsy();

    const plan = planScriptShotVideos(SCRIPT_ID);
    // 边丢了也不能被当成「一个都没派过」——否则重复点击会再派生一批付费节点。
    expect(plan.ok).toBe(true);
    if (!plan.ok) return;
    expect(plan.mode).toBe('regenerate');
    expect(plan.derivedCount).toBe(2);

    const again = scatterVideos();
    expect(again.ok).toBe(true);
    if (!again.ok) return;
    expect(videoNodes()).toHaveLength(2);
    expect(firstFrameEdge(first.nodeIds[0])).toBeTruthy();
  });

  it('linkedImageGroupId 还没写回（出图散开的中间态）时靠血缘边认出这批图', () => {
    // 真实时序：分镜图出图要几分钟，而组 id 是**出完再收拢**才写回的。所以出图期间
    // `linkedImageGroupId` 一直是 null，那批图只有 `role: storyboard` 的边认得出。
    // （单测里出图是瞬时的，收拢会跟着落定，所以这里显式还原那个中间态。）
    seedStoryboardImages();
    useCanvasStore.getState().updateNodeData(SCRIPT_ID, { linkedImageGroupId: null });
    expect(
      useCanvasStore.getState().nodes.find((node) => node.id === SCRIPT_ID)?.data
        .linkedImageGroupId,
    ).toBeFalsy();
    expect(
      useCanvasStore.getState().edges.filter((edge) => edge.data?.role === STORYBOARD_EDGE_ROLE),
    ).toHaveLength(2);

    const result = scatterVideos();
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.nodeIds).toHaveLength(2);
  });

  it('出图散开时订阅链也带上分镜图，图片落定才能触发按钮状态刷新', () => {
    const imageIds = seedStoryboardImages({ scatter: true });
    useCanvasStore.getState().updateNodeData(SCRIPT_ID, { linkedImageGroupId: null });
    const state = useCanvasStore.getState();

    const chainIds = new Set(
      scriptShotVideoChain(SCRIPT_ID, state.nodes, state.edges)
        .filter((item) => 'position' in item)
        .map((item) => item.id),
    );
    expect(chainIds).toEqual(new Set([SCRIPT_ID, ...imageIds]));
  });

  it('成组之后（linkedImageGroupId 已写）同样认得出来', () => {
    const imageIds = seedStoryboardImages();
    collapseStoryboardMembers({
      scriptNodeId: SCRIPT_ID,
      memberIds: imageIds,
      groupLabel: '分镜图 · 天空之跃',
      aspectKey: '16:9',
    });
    const scriptNode = useCanvasStore.getState().nodes.find((node) => node.id === SCRIPT_ID);
    expect(typeof scriptNode?.data.linkedImageGroupId).toBe('string');

    const result = scatterVideos();
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.nodeIds).toHaveLength(2);
  });

  it('视频节点散在分镜图那块右侧，且行序 = 宫格读取顺序', () => {
    seedStoryboardImages();
    const result = scatterVideos();
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
    expect(a?.position.y).toBe(b?.position.y);
    expect(b?.position.x).toBeGreaterThan(a?.position.x ?? 0);
  });

  it('没选模型也可以只建节点，但不该把空模型写进节点覆盖默认绑定', () => {
    seedStoryboardImages();
    const result = scatterVideos({ model: '' });
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.nodeIds).toHaveLength(2);
    expect(videoNodes().every((node) => !node.data.model)).toBe(true);
  });

  it('脚本节点不存在 / 表里没内容时给出可读原因', () => {
    const missing = scatterScriptShotVideos({ scriptNodeId: 'nope', model: VIDEO_MODEL });
    expect(missing.ok).toBe(false);

    setRows([]);
    const empty = scatterVideos();
    expect(empty.ok).toBe(false);
    if (!empty.ok) expect(empty.reason).toContain('分镜行');
    expect(videoNodes()).toHaveLength(0);
  });

  it('行序取表序而不是落位 y —— 合成的时间线顺序靠它', () => {
    seedStoryboardImages();
    const result = scatterVideos();
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    const ordered = scriptShotVideoNodesInRowOrder(SCRIPT_ID);
    expect(ordered.map((node) => String(node.data[SCRIPT_SHOT_VIDEO_ROW_FIELD]))).toEqual([
      'shot:1',
      'shot:2',
    ]);
  });
});

describe('成片 · 把镜头视频按行序接进合成节点', () => {
  beforeEach(() => {
    resetStoryboardSettle();
    seedScriptNode();
  });

  /** 铺到「两镜都出好片」的状态。 */
  function seedShotVideos() {
    seedStoryboardImages();
    const result = scatterVideos();
    if (!result.ok) throw new Error(`前置出片失败：${result.reason}`);
    result.nodeIds.forEach((nodeId, index) => {
      completeShot(nodeId, `clip-${index}.mp4`);
    });
    return result;
  }

  it('改成另一版生成后再改回原文，不能冒认旧产物；来源未知或换URL也拒绝', () => {
    const result = seedShotVideos();
    const nodeId = result.nodeIds[0];
    const bound = videoNodes()[0].data.shotContractFacts!.executionPrompt!;
    useCanvasStore.getState().updateNodeData(nodeId, { prompt: `${bound}改走北门` });
    completeShot(nodeId, '/other-version.mp4');
    useCanvasStore.getState().updateNodeData(nodeId, { prompt: bound });
    expect(scriptVideoExecutionPromptMatches(videoNodes()[0].data)).toBe(true);
    const resultFilm = assembleScriptFilm({ scriptNodeId: SCRIPT_ID });
    expect(resultFilm.ok).toBe(false);
    if (!resultFilm.ok) expect(resultFilm.reason).toContain('生成记录');
    expect(assembleScriptFilm({ scriptNodeId: SCRIPT_ID, preview: true }).ok).toBe(true);
    completeShot(nodeId, '/correct-version.mp4');
    expect(assembleScriptFilm({ scriptNodeId: SCRIPT_ID }).ok).toBe(true);
    useCanvasStore.getState().updateNodeData(nodeId, { videoUrl: '/unrelated.mp4' });
    expect(assembleScriptFilm({ scriptNodeId: SCRIPT_ID }).ok).toBe(false);
    useCanvasStore.getState().updateNodeData(nodeId, { videoUrl: '/correct-version.mp4', videoGenerationSource: null });
    expect(assembleScriptFilm({ scriptNodeId: SCRIPT_ID }).ok).toBe(false);
    const plan = planScriptShotVideos(SCRIPT_ID);
    expect(plan.ok && plan.pendingCount).toBe(1);
    const rerun = scatterVideos({ generateVideos: true });
    expect(rerun.ok && rerun.armed).toBe(1);
    expect(videoNodes()[0].data.videoUrl).toBe('/correct-version.mp4');
  });

  it.each([
    '人物改为停在红门前',
    '[运镜轨迹：固定机位] + [主体动作：人物停住] + [时长：4s]',
    '[camera: locked off] + [subject action: stay still] + [duration: 4s]',
    '完整优化稿'.repeat(250) + '最后向左离开',
  ])('手改或翻译优化正文拒绝旧事实正式成片，预览与正文保留：%s', prompt => {
    const result = seedShotVideos();
    const node = videoNodes()[0];
    const bound = node.data.shotContractFacts!.executionPrompt;
    useCanvasStore.getState().updateNodeData(result.nodeIds[0], { prompt });
    expect(scriptVideoExecutionPromptMatches(videoNodes()[0].data)).toBe(false);
    const film = assembleScriptFilm({ scriptNodeId: SCRIPT_ID });
    expect(film.ok).toBe(false);
    if (!film.ok) expect(film.reason).toContain('正文');
    expect(assembleScriptFilm({ scriptNodeId: SCRIPT_ID, preview: true }).ok).toBe(true);
    expect(videoNodes()[0].data.prompt).toBe(prompt);
    expect(videoNodes()[0].data.videoUrl).toBe('clip-0.mp4');
    useCanvasStore.getState().updateNodeData(result.nodeIds[0], { prompt: bound! });
    expect(assembleScriptFilm({ scriptNodeId: SCRIPT_ID }).ok).toBe(true);
  });

  it('参考职责重排、旧引用块和空白不改变正文版本，未闭合正文仍拒绝', () => {
    const result = seedShotVideos();
    const node = videoNodes()[0];
    const bound = node.data.shotContractFacts!.executionPrompt!;
    useCanvasStore.getState().updateNodeData(result.nodeIds[0], {
      prompt: `[视频资产引用: @图片3锁定[A + B]]\n[视频参考用途：@图片2用于动作[落点 + 支撑]]\n ${bound.replace(/ /g, '  ')}\n`,
    });
    expect(scriptVideoExecutionPromptMatches(videoNodes()[0].data)).toBe(true);
    expect(assembleScriptFilm({ scriptNodeId: SCRIPT_ID }).ok).toBe(true);
    useCanvasStore.getState().updateNodeData(result.nodeIds[0], { prompt: `${bound}\n[视频参考用途：未闭合` });
    expect(scriptVideoExecutionPromptMatches(videoNodes()[0].data)).toBe(false);
    expect(assembleScriptFilm({ scriptNodeId: SCRIPT_ID }).ok).toBe(false);
  });

  it('旧脚本节点缺正文绑定须显式重做，已有视频保留，重做完成前不得正式成片', () => {
    const result = seedShotVideos();
    const node = videoNodes()[0];
    useCanvasStore.getState().updateNodeData(result.nodeIds[0], {
      shotContractFacts: { ...node.data.shotContractFacts!, executionPrompt: undefined },
    });
    const film = assembleScriptFilm({ scriptNodeId: SCRIPT_ID });
    expect(film.ok).toBe(false);
    if (!film.ok) expect(film.reason).toContain('正文执行版本');
    expect(scatterVideos().ok).toBe(true);
    expect(videoNodes()[0].data.shotContractFacts!.executionPrompt).toBeUndefined();
    const rebuilt = scatterVideos({ generateVideos: true });
    expect(rebuilt.ok && rebuilt.armed).toBe(1);
    expect(scriptVideoExecutionPromptMatches(videoNodes()[0].data)).toBe(true);
    expect(videoNodes()[0].data.videoUrl).toBe('clip-0.mp4');
    expect(assembleScriptFilm({ scriptNodeId: SCRIPT_ID }).ok).toBe(false);
    useCanvasStore.getState().updateNodeData(result.nodeIds[0], { canvas_auto_generate_once: false, isGenerating: true });
    expect(assembleScriptFilm({ scriptNodeId: SCRIPT_ID }).ok).toBe(false);
    useCanvasStore.getState().updateNodeData(result.nodeIds[0], { isGenerating: false });
    completeShot(result.nodeIds[0], '/new.mp4');
    expect(assembleScriptFilm({ scriptNodeId: SCRIPT_ID }).ok).toBe(true);
  });

  it('规格不一致也能预览已有镜头，严格成片仍拒绝', () => {
    const seeded = seedShotVideos();
    useCanvasStore.getState().updateNodeData(seeded.nodeIds[0], { deliverySpec: { width: 1280, height: 720, aspectRatio: '16:9', fps: 24 } });
    expect(assembleScriptFilm({ scriptNodeId: SCRIPT_ID }).ok).toBe(false);
    const beforeVideos = structuredClone(videoNodes());
    const result = assembleScriptFilm({ scriptNodeId: SCRIPT_ID, preview: true });
    if (!result.ok) throw new Error(result.reason);
    expect(result.clipCount).toBe(2);
    expect(result.warning).toContain('规格');
    const compose = useCanvasStore.getState().nodes.find(node => node.id === result.composeNodeId)!;
    expect(compose.data.scriptFilmPreview).toBe(true);
    expect(compose.data.scriptClipNodeIds).toEqual(seeded.nodeIds);
    expect(compose.data.scriptEditDurationMsByShotId).toEqual({ 'shot:1': 4000, 'shot:2': 5000 });
    expect(videoNodes()).toEqual(beforeVideos);
    expect(assembleScriptFilm({ scriptNodeId: SCRIPT_ID }).ok).toBe(false);
  });

  it('部分镜头预览可更新，清除不再属于剧本的旧连接', () => {
    const seeded = seedShotVideos();
    useCanvasStore.getState().updateNodeData(seeded.nodeIds[1], { videoUrl: '' });
    const first = assembleScriptFilm({ scriptNodeId: SCRIPT_ID, preview: true });
    if (!first.ok) throw new Error(first.reason);
    expect(first.clipCount).toBe(1);
    expect(first.skippedNoVideo).toBe(1);
    useCanvasStore.getState().updateNodeData(seeded.nodeIds[1], { videoUrl: '/second.mp4' });
    const second = assembleScriptFilm({ scriptNodeId: SCRIPT_ID, preview: true });
    if (!second.ok) throw new Error(second.reason);
    expect(second.composeNodeId).toBe(first.composeNodeId);
    expect(second.clipCount).toBe(2);
    setRows([rows()[0]]);
    const third = assembleScriptFilm({ scriptNodeId: SCRIPT_ID, preview: true });
    if (!third.ok) throw new Error(third.reason);
    expect(third.clipCount).toBe(1);
    expect(useCanvasStore.getState().edges.some(edge => edge.source === seeded.nodeIds[1] && edge.target === third.composeNodeId)).toBe(false);
    expect(videoNodes()).toHaveLength(2);
  });

  it('旧脚本视频可明确作为预览，未同步规划仍禁止正式交付', () => {
    seedShotVideos();
    setRows(rows().map(row => ({ ...row, end_state: '新的结束状态' })));
    useCanvasStore.getState().updateNodeData(SCRIPT_ID, { scriptDirectorPlanNeedsSync: true });
    expect(assembleScriptFilm({ scriptNodeId: SCRIPT_ID }).ok).toBe(false);
    const preview = assembleScriptFilm({ scriptNodeId: SCRIPT_ID, preview: true });
    if (!preview.ok) throw new Error(preview.reason);
    expect(preview.clipCount).toBe(2);
    expect(preview.warning).toContain('预览');
    expect(useCanvasStore.getState().nodes.find(node => node.id === SCRIPT_ID)?.data.scriptDirectorPlanNeedsSync).toBe(true);
  });

  it('拒绝把按旧脚本生成的视频作为修改后的正式成片', () => {
    seedShotVideos();
    const changed = rows();
    changed[1].end_state = '阿雀停在门内，回头望向雨巷';
    setRows(changed);
    const before = useCanvasStore.getState().nodes.length;
    const result = assembleScriptFilm({ scriptNodeId: SCRIPT_ID });
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.reason).toContain('第 2 镜脚本已修改');
    expect(useCanvasStore.getState().nodes).toHaveLength(before);
  });

  it.each(['视频重做', '尾帧改图', '删除承接边', '源视频失败'])('拒绝尾帧来源%s后的旧视频进入成片', (change) => {
    const result = seedShotVideos();
    const sourceId = result.nodeIds[0];
    const targetId = result.nodeIds[1];
    const tailId = useCanvasStore.getState().addNode(CANVAS_NODE_TYPES.upload, { x: 0, y: 0 }, {
      imageUrl: '/tail.png', captureMetadata: { source_kind: 'video_frame_capture', source_node_id: sourceId, source_video_url: 'clip-0.mp4', capture_mode: 'last' },
    })!;
    useCanvasStore.getState().addEdgeWithData(tailId, targetId, { role: SCRIPT_SHOT_CONTINUITY_EDGE_ROLE });
    useCanvasStore.getState().updateNodeData(targetId, { scriptShotTailReferenceUrl: '/tail.png' });
    const current = useCanvasStore.getState();
    expect(scriptTailReferenceStaleReason(current.nodes.find(node => node.id === targetId)!, current)).toBeNull();
    expect(assembleScriptFilm({ scriptNodeId: SCRIPT_ID }).ok).toBe(true);
    if (change === '视频重做') completeShot(sourceId, '/new.mp4');
    if (change === '尾帧改图') useCanvasStore.getState().updateNodeData(tailId, { imageUrl: '/changed.png' });
    if (change === '删除承接边') {
      const edge = useCanvasStore.getState().edges.find(edge => edge.source === tailId && edge.target === targetId)!;
      useCanvasStore.getState().deleteEdge(edge.id);
    }
    if (change === '源视频失败') useCanvasStore.getState().updateNodeData(sourceId, { generationError: 'failed' });
    const before = useCanvasStore.getState();
    expect(scriptTailReferenceStaleReason(before.nodes.find(node => node.id === targetId)!, before)).toBeTruthy();
    expect(assembleScriptFilm({ scriptNodeId: SCRIPT_ID }).ok).toBe(false);
    expect(useCanvasStore.getState().nodes).toBe(before.nodes);
    expect(useCanvasStore.getState().edges).toBe(before.edges);
  });

  it('建合成节点并按行序连边，幂等复用', () => {
    seedShotVideos();
    const first = assembleScriptFilm({ scriptNodeId: SCRIPT_ID });
    expect(first.ok).toBe(true);
    if (!first.ok) return;
    expect(first.mode).toBe('created');
    expect(first.clipCount).toBe(2);
    expect(first.skippedNoVideo).toBe(0);

    const compose = useCanvasStore
      .getState()
      .nodes.find((node) => node.id === first.composeNodeId);
    expect(compose && isVideoComposeNode(compose)).toBe(true);
    expect(compose?.data[SCRIPT_COMPOSE_SOURCE_FIELD]).toBe(SCRIPT_ID);

    // 边按行序建：合成台初始化时间线按上游边序遍历，顺序错了成片就散了。
    const inbound = useCanvasStore
      .getState()
      .edges.filter((edge) => edge.target === first.composeNodeId)
      .map((edge) => edge.source);
    const rowOrder = scriptShotVideoNodesInRowOrder(SCRIPT_ID).map((node) => node.id);
    expect(inbound).toEqual(rowOrder);

    const second = assembleScriptFilm({ scriptNodeId: SCRIPT_ID });
    expect(second.ok).toBe(true);
    if (!second.ok) return;
    expect(second.mode).toBe('reused');
    expect(second.composeNodeId).toBe(first.composeNodeId);
    // 不重复建节点、不重复连边。
    expect(
      useCanvasStore.getState().nodes.filter(isVideoComposeNode),
    ).toHaveLength(1);
    expect(
      useCanvasStore.getState().edges.filter((edge) => edge.target === first.composeNodeId),
    ).toHaveLength(2);
  });

  it('缺任一镜时拒绝创建正式成片，不改动画布', () => {
    seedStoryboardImages();
    const scattered = scatterVideos();
    expect(scattered.ok).toBe(true);
    if (!scattered.ok) return;
    completeShot(scattered.nodeIds[0], 'clip-0.mp4');

    const result = assembleScriptFilm({ scriptNodeId: SCRIPT_ID });
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.reason).toContain('正式成片');
    expect(useCanvasStore.getState().nodes.filter(isVideoComposeNode)).toHaveLength(0);
  });

  it('新出片的镜再点一次会补上（更新成片）', () => {
    const scattered = seedShotVideos();
    const first = assembleScriptFilm({ scriptNodeId: SCRIPT_ID });
    expect(first.ok).toBe(true);
    if (!first.ok) return;

    // 脚本行从 2 行变 3 行 → 分镜图整批重建（旧图删掉、新图落盘），成片要补边。
    setRows(threeRows());
    rebuildStoryboardImages(threeRows());
    const again = scatterVideos();
    expect(again.ok).toBe(true);
    if (!again.ok) return;
    // 旧行仍然是当前行的子集 → 保留旧视频，只把新增的第三镜补进来。
    expect(again.mode).toBe('rearmed');
    expect(again.addedCount).toBe(1);
    expect(scattered.nodeIds.every((nodeId) => again.nodeIds.includes(nodeId))).toBe(true);
    again.nodeIds.forEach((nodeId, index) => {
      completeShot(nodeId, `clip-new-${index}.mp4`);
    });

    const status = scriptFilmStatus(SCRIPT_ID);
    expect(status.clipCount).toBe(3);
    expect(status.composeMissingClips).toBe(true);

    const second = assembleScriptFilm({ scriptNodeId: SCRIPT_ID });
    expect(second.ok).toBe(true);
    if (!second.ok) return;
    expect(second.mode).toBe('reused');
    expect(second.clipCount).toBe(3);
    expect(scriptFilmStatus(SCRIPT_ID).composeMissingClips).toBe(false);
  });

  it('一镜都没出片时拒绝成片，并给出可读原因', () => {
    seedStoryboardImages();
    const scattered = scatterVideos();
    expect(scattered.ok).toBe(true);
    const result = assembleScriptFilm({ scriptNodeId: SCRIPT_ID });
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.reason).toContain('出好片');
    expect(useCanvasStore.getState().nodes.filter(isVideoComposeNode)).toHaveLength(0);
  });

  it('一条镜头视频都没派生时给出「先逐镜出视频」', () => {
    seedStoryboardImages();
    const result = assembleScriptFilm({ scriptNodeId: SCRIPT_ID });
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.reason).toContain('逐镜出视频');
  });

  it('多镜交付规格不一致时拒绝正式成片', () => {
    const scattered = seedShotVideos();
    useCanvasStore.getState().updateNodeData(scattered.nodeIds[1], {
      deliverySpec: {
        width: 1080,
        height: 1920,
        aspectRatio: '9:16',
        fps: 30,
        safeArea: { top: 0.05, right: 0.05, bottom: 0.1, left: 0.05 },
      },
    });

    const result = assembleScriptFilm({ scriptNodeId: SCRIPT_ID });
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.reason).toContain('规格');
    expect(useCanvasStore.getState().nodes.filter(isVideoComposeNode)).toHaveLength(0);
  });

  it('多镜缺镜头合同事实时拒绝正式成片', () => {
    const scattered = seedShotVideos();
    useCanvasStore.getState().updateNodeData(scattered.nodeIds[1], {
      shotContractFacts: null,
    });

    const result = assembleScriptFilm({ scriptNodeId: SCRIPT_ID });
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.reason).toContain('镜头合同');
    expect(useCanvasStore.getState().nodes.filter(isVideoComposeNode)).toHaveLength(0);
  });

  it('全片规格变化会让旧视频失效并重新排队', () => {
    seedStoryboardImages();
    const first = scatterVideos();
    expect(first.ok).toBe(true);
    if (!first.ok) return;
    first.nodeIds.forEach((nodeId, index) => {
      useCanvasStore.getState().updateNodeData(nodeId, { videoUrl: `clip-${index}.mp4` });
    });

    const second = scatterVideos({ aspectRatio: '9:16', generateVideos: true });
    expect(second.ok).toBe(true);
    if (!second.ok) return;
    expect(second.mode).toBe('rearmed');
    expect(second.armed).toBe(2);
    expect(
      videoNodes().every((node) => node.data.deliverySpec?.aspectRatio === '9:16'),
    ).toBe(true);
  });

  it('单镜旧画布没有交付规格和镜头合同时仍可兼容成片', () => {
    const single = [rows()[0]];
    setRows(single);
    seedStoryboardImages({ rows: single });
    const scattered = scatterVideos({ model: null });
    expect(scattered.ok).toBe(true);
    if (!scattered.ok) return;
    useCanvasStore.getState().updateNodeData(scattered.nodeIds[0], {
      videoUrl: 'legacy-single.mp4',
      deliverySpec: null,
      shotContractFacts: null,
    });

    const result = assembleScriptFilm({ scriptNodeId: SCRIPT_ID });
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.clipCount).toBe(1);
  });
});

describe('逐镜出视频 · 计划账与订阅选择器', () => {
  beforeEach(() => {
    resetStoryboardSettle();
    seedScriptNode();
  });

  it('planScriptShotVideos 与真正落盘的口径一致', () => {
    seedStoryboardImages();
    const plan = planScriptShotVideos(SCRIPT_ID);
    expect(plan.ok).toBe(true);
    if (!plan.ok) return;
    expect(plan.mode).toBe('create');
    expect(plan.shotCount).toBe(2);
    expect(plan.pendingCount).toBe(2);
    expect(plan.willRebuild).toBe(false);
    expect(plan.derivedCount).toBe(0);
    // 4 + 5 秒
    expect(plan.plannedSeconds).toBe(9);

    const result = scatterVideos();
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.nodeIds).toHaveLength(plan.shotCount);
  });

  it('规格换算：行键 / 镜号 / 景别 / 提示词来源', () => {
    const specs = buildScriptShotVideoSpecs(rows());
    expect(specs.map((spec) => spec.rowKey)).toEqual(['shot:1', 'shot:2']);
    expect(specs[0].shotSize).toBe('特写');
    expect(specs[0].promptSource).toBe('motion');
    expect(specs[0].durationSec).toBe(4);
  });
});

/**
 * T-153 · 同场景相邻镜的「上一镜承接」参考边。
 *
 * 锁三件事：
 * - 接的**条件**（同场景、非显式硬切、上一镜已出图、模型声明吃得下第二张图）；
 * - 接的**位置**（首帧边必须仍在边序第一张 —— 提交时第一张就是首帧）；
 * - 接的**代价**（只补边，不因为上一镜的图变了就重跑这一镜的付费视频）。
 */
describe('逐镜出视频 · 同场景相邻镜的上一镜承接边', () => {
  beforeEach(() => {
    resetStoryboardSettle();
  });

  /** 三镜：前两镜同场景，第三镜换场。 */
  function sceneRows(): FreezoneStoryScriptRow[] {
    return [
      {
        shot_no: '1',
        duration: '4',
        shot: '中景',
        scene_tags: '祠堂',
        transition_plan: 'continuous_action',
        visual_description: '阿雀推门',
        video_motion_prompt: '门被推开',
      },
      {
        shot_no: '2',
        duration: '4',
        shot: '近景',
        scene_tags: '祠堂',
        visual_description: '阿雀抬眼',
        video_motion_prompt: '镜头推近阿雀',
      },
      {
        shot_no: '3',
        duration: '4',
        shot: '全景',
        scene_tags: '山道',
        visual_description: '山道上雨落',
        video_motion_prompt: '镜头拉远',
      },
    ];
  }

  function chainEdge(videoNodeId: string) {
    return useCanvasStore
      .getState()
      .edges.find(
        (edge) =>
          edge.target === videoNodeId && edge.data?.role === SCRIPT_SHOT_CONTINUITY_EDGE_ROLE,
      );
  }

  /** 提交时真正取图的顺序：按边序（`upstreamNodesInEdgeOrder` 同一口径）。 */
  function upstreamSourceIds(videoNodeId: string): string[] {
    const seen = new Set<string>();
    const ordered: string[] = [];
    useCanvasStore
      .getState()
      .edges.forEach((edge) => {
        if (edge.target !== videoNodeId || seen.has(edge.source)) return;
        seen.add(edge.source);
        ordered.push(edge.source);
      });
    return ordered;
  }

  /** 脚本节点与分镜图必须用**同一批行**：只铺分镜图不换行，脚本还在读旧表。 */
  function seedScene(
    sceneRowsValue: FreezoneStoryScriptRow[],
    options: { generated?: boolean } = {},
  ) {
    seedScriptNode(sceneRowsValue);
    return seedStoryboardImages({ rows: sceneRowsValue, ...options });
  }

  it('承接参考默认支持全能参考，明确模式和数量限制仍生效', () => {
    expect(shotVideoAcceptsChainReference(undefined)).toBe(true);
    expect(shotVideoAcceptsChainReference(null)).toBe(true);
    expect(shotVideoAcceptsChainReference({})).toBe(true);
    expect(shotVideoAcceptsChainReference({ referenceLimits: { allReference: { image: 1 } } })).toBe(false);
    expect(shotVideoAcceptsChainReference({ supportedModes: [] })).toBe(false);
    // 只有单图模式 → 不放行。
    expect(shotVideoAcceptsChainReference({ supportedModes: ['imageToVideo'] })).toBe(false);
    // 声明了多图模式、但合同说只收 1 张 → 不放行。
    expect(
      shotVideoAcceptsChainReference({
        supportedModes: ['imageToVideo', 'allReference'],
        referenceLimits: { allReference: { image: 1 } },
      }),
    ).toBe(false);
    // 真能多图（H3 就是这一档：allReference 收 9 张）→ 放行。
    expect(
      shotVideoAcceptsChainReference({
        supportedModes: ['imageToVideo', 'allReference'],
        referenceLimits: { allReference: { image: 9 } },
      }),
    ).toBe(true);
    expect(
      shotVideoAcceptsChainReference({
        supportedModes: ['imageReference'],
        referenceLimits: { imageReference: { image: 9 } },
      }),
    ).toBe(true);
  });

  it('没有上一镜真实尾帧时不伪造承接边，首帧仍可独立生成', () => {
    seedScene(sceneRows());
    const result = scatterScriptShotVideos({
      scriptNodeId: SCRIPT_ID,
      model: VIDEO_MODEL,
      continuityReference: true,
    });
    expect(result.ok).toBe(true);
    if (!result.ok) return;

    const byKey = videoByRowKey();
    const first = byKey.get('shot:1');
    const second = byKey.get('shot:2');
    const third = byKey.get('shot:3');
    expect(first && second && third).toBeTruthy();
    if (!first || !second || !third) return;

    // 第 1 镜没有上一镜；第 2 镜虽同场景但没有真实尾帧 → 不接；第 3 镜换场景 → 不接。
    expect(chainEdge(first.id)).toBeUndefined();
    const edge = chainEdge(second.id);
    expect(edge).toBeUndefined();
    expect(chainEdge(third.id)).toBeUndefined();

    // 顺序：首帧在前、承接在后。第一张是首帧是提交约定，颠倒了就是拿上一镜去当首帧。
    const order = upstreamSourceIds(second.id);
    expect(order).toHaveLength(1);
    expect(order[0]).toBe(second.data[SCRIPT_SHOT_VIDEO_IMAGE_FIELD]);
    // 承接边不能被人读成「首帧来自上一镜」。
    expect(firstFrameEdge(second.id)?.source).toBe(
      second.data[SCRIPT_SHOT_VIDEO_IMAGE_FIELD],
    );
    expect(firstFrameEdge(second.id)?.data?.role).toBe(SCRIPT_SHOT_VIDEO_EDGE_ROLE);
  });

  it('显式写了硬切的接缝不接，只有明确动作衔接才接', () => {
    const cutRows = sceneRows();
    cutRows[0] = { ...cutRows[0], transition_plan: 'direct_cut' };
    seedScene(cutRows);
    const result = scatterScriptShotVideos({
      scriptNodeId: SCRIPT_ID,
      model: VIDEO_MODEL,
      continuityReference: true,
    });
    expect(result.ok).toBe(true);
    if (!result.ok) return;

    const byKey = videoByRowKey();
    expect(chainEdge(byKey.get('shot:2')?.id ?? '')).toBeUndefined();
    // 换场景那一段本来就不接，这里再确认一次别被 transition 的分支带偏。
    expect(chainEdge(byKey.get('shot:3')?.id ?? '')).toBeUndefined();
  });

  it('一镜的状态关键画面挂到同一个视频节点，不拆成多个视频镜头', () => {
    const geography = '坡沿位于红门东侧，坡沿外两米是落地平台';
    const planned = sceneRows().map((row, index) => index === 0 ? {
      ...row,
      scene_tags: '坡道', scene_descriptions: { 坡道: geography, 地下室: '不相关场景' },
      keyframe_plan: [
        { role: 'contact_state', state: '滑板前轮压上坡沿，双脚仍保持支撑', purpose: '锁住接触关系', required: true },
        { role: 'spatial_reveal', generation_strategy: 'independent' as const, framing: '侧面全景，板与落地平台同框',
          state: '滑板腾空越过坡沿，身体重心前倾', purpose: '锁住切点状态', required: false },
      ],
    } : row);
    seedScene(planned);
    const result = scatterScriptShotVideos({ scriptNodeId: SCRIPT_ID, model: VIDEO_MODEL });
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    const firstVideo = videoByRowKey().get('shot:1');
    expect(firstVideo).toBeTruthy();
    expect(videoNodes()).toHaveLength(3);
    const keyframeEdges = useCanvasStore.getState().edges.filter(edge => edge.target === firstVideo?.id && edge.data?.role === SCRIPT_SHOT_KEYFRAME_EDGE_ROLE);
    expect(keyframeEdges).toHaveLength(2);
    expect(firstVideo?.data.prompt).toContain('本镜状态画面计划');
    expect(useCanvasStore.getState().nodes.filter(node => node.data.scriptShotKeyframeRowKey === 'shot:1')).toHaveLength(2);

    const originalKeyframes = useCanvasStore.getState().nodes.filter(node => node.data.scriptShotKeyframeRowKey === 'shot:1');
    expect(originalKeyframes[0].data.prompt).toContain('可见状态：滑板前轮压上坡沿');
    for (const node of [firstVideo, ...originalKeyframes]) {
      expect(node?.data.prompt).toContain(geography);
      expect(node?.data.prompt).not.toContain('不相关场景');
    }
    expect(originalKeyframes[0].data.prompt).toContain('锁住接触关系');
    expect(originalKeyframes[1].data.prompt).toContain('锁住切点状态');
    const openingUrl = useCanvasStore.getState().nodes.find(node => node.id === firstVideo?.data.scriptShotImageNodeId)?.data.imageUrl;
    expect(openingUrl).toBeTruthy();
    expect(originalKeyframes[1].data.referenceImageUrls).not.toContain(openingUrl);
    expect(originalKeyframes[1].data.prompt).toContain('本张构图：侧面全景');
    expect(originalKeyframes[1].data.prompt).not.toContain('先看@图片1');
    expect(originalKeyframes[0].data.scriptCreativeHandoff).toMatchObject({ sceneDescriptions: { 坡道: geography } });
    expect(originalKeyframes[0].data.prompt).toContain('先看@图片1中实际可见的姿态、接触与支撑');
    expect(originalKeyframes[0].data.prompt).not.toContain('首帧契约');
    expect(originalKeyframes[0].data.prompt).not.toContain(planned[0].shot_prompt);
    const duplicateId = useCanvasStore.getState().addNode(CANVAS_NODE_TYPES.imageGen, { x: 0, y: 0 }, originalKeyframes[0].data);
    expect(duplicateId).toBeTruthy();
    expect(useCanvasStore.getState().nodes.filter(node => node.data.scriptShotKeyframeRowKey === 'shot:1')).toHaveLength(3);
    scatterScriptShotVideos({ scriptNodeId: SCRIPT_ID, model: VIDEO_MODEL });
    const deduped = useCanvasStore.getState().nodes.filter(node => node.data.scriptShotKeyframeRowKey === 'shot:1');
    expect(deduped).toHaveLength(2);
    expect(new Set(deduped.map(node => node.data.scriptShotKeyframeIndex))).toEqual(new Set([0, 1]));

    deduped.forEach((node, index) => useCanvasStore.getState().updateNodeData(node.id, { imageUrl: `/state-${index}.png` }));
    scatterScriptShotVideos({ scriptNodeId: SCRIPT_ID, model: VIDEO_MODEL });
    expect(videoByRowKey().get('shot:1')?.data.prompt).toContain('本镜状态关键帧');
    expect(videoByRowKey().get('shot:1')?.data.prompt).toContain('提供独立视点');
    expect(videoByRowKey().get('shot:1')?.data.prompt).toContain('侧面全景，板与落地平台同框');
    expect(videoByRowKey().get('shot:1')?.data.prompt).not.toContain('@图片0');

    const shortened = planned.map((row, index) => index === 0 ? { ...row, keyframe_plan: row.keyframe_plan?.slice(0, 1) } : row);
    useCanvasStore.getState().updateNodeData(SCRIPT_ID, { scriptResult: { title: '测试', rows: shortened } });
    scatterScriptShotVideos({ scriptNodeId: SCRIPT_ID, model: VIDEO_MODEL });
    const remaining = useCanvasStore.getState().nodes.filter(node => node.data.scriptShotKeyframeRowKey === 'shot:1');
    expect(remaining).toHaveLength(1);
    expect(remaining[0].id).toBe(originalKeyframes[0].id);
    expect(remaining[0].data.imageUrl).toBeNull();
    expect(useCanvasStore.getState().edges.some(edge => edge.source === originalKeyframes[1].id)).toBe(false);
    useCanvasStore.getState().updateNodeData(SCRIPT_ID, { scriptResult: { title: '测试', rows: shortened.map(row => ({ ...row, keyframe_plan: [] })) } });
    scatterScriptShotVideos({ scriptNodeId: SCRIPT_ID, model: VIDEO_MODEL });
    expect(useCanvasStore.getState().nodes.filter(node => node.data.scriptShotKeyframeRowKey === 'shot:1')).toHaveLength(0);
  });

  it('计划补图尚未生成时也先检查全部参考数量，不为不支持多图的模型先生成补图', () => {
    const planned = sceneRows().map((row, index) => index === 0 ? { ...row, keyframe_plan: [
      { role: 'spatial_reveal', generation_strategy: 'independent' as const, framing: '侧面全景',
        state: '腾空越过坡沿', purpose: '看清落点距离', required: true },
      { role: 'ending_state', state: '双脚落稳平台', purpose: '看清落地支撑', required: true },
    ] } : row);
    seedScene(planned);
    const result = scatterScriptShotVideos({ scriptNodeId: SCRIPT_ID, model: VIDEO_MODEL,
      generateVideos: true, modelCapabilities: { supportedModes: ['allReference'], referenceLimits: { allReference: { image: 2 } } } });
    expect(result.ok).toBe(false);
    expect(useCanvasStore.getState().nodes.filter(node => node.data.scriptShotKeyframeSourceNodeId)).toHaveLength(0);
  });

  it('读取脚本的衔接方式与推荐生成方式并留档', () => {
    const scriptRows = sceneRows().map((row, index) => ({
      ...row,
      transition_plan: index === 0 ? 'continuous_action' : 'match_cut',
      generation_mode: index === 1 ? 'first_last_frame' : 'image_to_video',
    }));
    const specs = buildScriptShotVideoSpecs(scriptRows);
    expect(specs[0].transition).toBe('continuous_action');
    expect(specs[0].generationMode).toBe('imageToVideo');
    expect(specs[1].transition).toBe('match_cut');
    expect(specs[1].generationMode).toBe('firstLastFrame');
  });

  it('只有明确连续动作才从前镜终点补本镜缺失起点', () => {
    const source = sceneRows().slice(0, 2).map((row, index) => ({
      ...row, start_state: '', end_state: index === 0 ? '室内手握扇子' : '山顶望远',
      transition_plan: index === 0 ? '椭圆省略' : 'continuous_action',
    }));
    let specs = buildScriptShotVideoSpecs(source);
    expect(specs[1].continuityIn.frame).toBe('本镜分镜图首帧');
    expect(specs[1].continuityIn.seam).toBe('椭圆省略');
    expect(specs[1].continuityIn.action_state).toBeUndefined();
    source[0].transition_plan = 'continuous_action';
    specs = buildScriptShotVideoSpecs(source);
    expect(specs[1].continuityIn.action_state).toBe('室内手握扇子');
    source[1].start_state = '本镜明确起点';
    specs = buildScriptShotVideoSpecs(source);
    expect(specs[1].continuityIn.action_state).toBe('本镜明确起点');
  });

  it('连续动作只用当前视频的真实尾帧，旧版本尾帧失效后不回退起始图', () => {
    seedScene(sceneRows());
    scatterScriptShotVideos({ scriptNodeId: SCRIPT_ID, model: VIDEO_MODEL, continuityReference: true });
    const first = videoByRowKey().get('shot:1')!;
    const second = videoByRowKey().get('shot:2')!;
    useCanvasStore.getState().updateNodeData(first.id, { videoUrl: '/current.mp4' });
    const tailId = useCanvasStore.getState().addNode(CANVAS_NODE_TYPES.upload, { x: 0, y: 0 }, {
      imageUrl: '/tail.png', captureMetadata: { source_kind: 'video_frame_capture', source_node_id: first.id, source_video_url: '/current.mp4', capture_mode: 'last' },
    })!;
    const beforeConnect = useCanvasStore.getState();
    const capturedGraph = graphSliceOf(scriptShotVideoChain(SCRIPT_ID, beforeConnect.nodes, beforeConnect.edges));
    expect(capturedGraph.nodes.some(node => node.id === tailId)).toBe(true);
    scatterScriptShotVideos({ scriptNodeId: SCRIPT_ID, model: VIDEO_MODEL, continuityReference: true });
    expect(chainEdge(second.id)?.source).toBe(tailId);
    expect(upstreamSourceIds(second.id)).toHaveLength(2);
    useCanvasStore.getState().updateNodeData(first.id, { videoUrl: '/new.mp4' });
    scatterScriptShotVideos({ scriptNodeId: SCRIPT_ID, model: VIDEO_MODEL, continuityReference: true });
    expect(chainEdge(second.id)).toBeUndefined();
    expect(upstreamSourceIds(second.id)).not.toContain(tailId);
  });

  it('推荐首尾帧的批次不能静默以单首帧自动生成，但允许仅建节点', () => {
    const scriptRows = sceneRows().map((row, index) => ({ ...row, generation_mode: index === 1 ? 'first_last_frame' : 'image_to_video' }));
    seedScene(scriptRows);
    const plan = planScriptShotVideos(SCRIPT_ID, undefined, { model: VIDEO_MODEL });
    expect(plan.ok).toBe(true);
    if (!plan.ok) return;
    expect(plan.rows[1].issues).toContain('missingLastFrame');
    const before = useCanvasStore.getState();
    const result = scatterScriptShotVideos({ scriptNodeId: SCRIPT_ID, model: VIDEO_MODEL, generateVideos: true });
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.reason).toContain('本镜尾帧');
    expect(useCanvasStore.getState().nodes).toBe(before.nodes);
    expect(useCanvasStore.getState().edges).toBe(before.edges);
    const created = scatterScriptShotVideos({ scriptNodeId: SCRIPT_ID, model: VIDEO_MODEL, generateVideos: false });
    expect(created.ok).toBe(true);
    const node = videoByRowKey().get('shot:2');
    expect(node?.data.generationModeRecommendation).toBe('firstLastFrame');
    expect(node?.data.canvas_auto_generate_once).toBe(false);
  });

  it('手动补齐本镜尾图后允许批次生成，并禁止用补时长错置尾图', () => {
    const scriptRows = sceneRows().map((row, index) => ({ ...row, generation_mode: index === 1 ? 'first_last_frame' : 'image_to_video' }));
    seedScene(scriptRows);
    scatterScriptShotVideos({ scriptNodeId: SCRIPT_ID, model: VIDEO_MODEL });
    const second = videoByRowKey().get('shot:2')!;
    const tail = useCanvasStore.getState().addNode(CANVAS_NODE_TYPES.upload, { x: 0, y: 0 }, { imageUrl: '/ending.png' })!;
    useCanvasStore.getState().addEdgeWithData(tail, second.id, { edgeKind: 'mainline_data', propagates: true }, { sourceHandle: 'source', targetHandle: 'target' });
    useCanvasStore.getState().updateNodeData(second.id, { genMode: 'firstLastFrame' });
    const graph = useCanvasStore.getState();
    const pair = scriptShotKeyframes(graph.nodes.find(node => node.id === second.id), graph);
    expect(pair.ok ? null : pair.reason).toBeNull();
    const modelCapabilities = { supportedModes: ['imageToVideo', 'firstLastFrame'], durationOptions: [4, 5, 6, 8, 10, 15] };
    const plan = planScriptShotVideos(SCRIPT_ID, undefined, { model: VIDEO_MODEL, modelCapabilities });
    expect(plan.ok && plan.rows[1].issues).not.toContain('missingLastFrame');
    const slicedGraph = graphSliceOf(scriptShotVideoChain(SCRIPT_ID, graph.nodes, graph.edges));
    expect(slicedGraph.nodes.some(node => node.id === tail)).toBe(true);
    const slicedPlan = planScriptShotVideos(SCRIPT_ID, slicedGraph, { model: VIDEO_MODEL, modelCapabilities });
    expect(slicedPlan).toEqual(plan);
    const blocked = scatterScriptShotVideos({ scriptNodeId: SCRIPT_ID, model: VIDEO_MODEL, generateVideos: true, modelCapabilities: { ...modelCapabilities, durationOptions: [15] } });
    expect(blocked.ok).toBe(false);
    const result = scatterScriptShotVideos({ scriptNodeId: SCRIPT_ID, model: VIDEO_MODEL, generateVideos: true, modelCapabilities });
    expect(result.ok).toBe(true);
    expect(useCanvasStore.getState().nodes.find(node => node.id === second.id)?.data).toMatchObject({ genMode: 'firstLastFrame', scriptShotLastFrameUrl: '/ending.png', canvas_auto_generate_once: true });
    for (const video of videoByRowKey().values()) useCanvasStore.getState().updateNodeData(video.id, { ...generatedVideoPatch(video.id, '/done.mp4'), canvas_auto_generate_once: false });
    const unchanged = planScriptShotVideos(SCRIPT_ID, undefined, { model: VIDEO_MODEL, modelCapabilities });
    expect(unchanged.ok && unchanged.pendingCount).toBe(0);
    const completed = useCanvasStore.getState();
    const savedGraph = structuredClone({ nodes: completed.nodes, edges: completed.edges });
    useCanvasStore.getState().updateNodeData(tail, { imageUrl: '/changed-ending.png' });
    const changed = planScriptShotVideos(SCRIPT_ID, undefined, { model: VIDEO_MODEL, modelCapabilities });
    expect(changed.ok && changed.pendingCount).toBe(1);
    expect(planScriptShotVideos(SCRIPT_ID, savedGraph, { model: VIDEO_MODEL, modelCapabilities })).toEqual(unchanged);
    const current = useCanvasStore.getState();
    const updatedGraph = graphSliceOf(scriptShotVideoChain(SCRIPT_ID, current.nodes, current.edges));
    expect(updatedGraph.nodes.find(node => node.id === tail)?.data.imageUrl).toBe('/changed-ending.png');
    expect(planScriptShotVideos(SCRIPT_ID, updatedGraph, { model: VIDEO_MODEL, modelCapabilities })).toEqual(changed);
    useCanvasStore.getState().updateNodeData(second.id, { generationError: '新的运行失败' });
    expect(planScriptShotVideos(SCRIPT_ID, savedGraph, { model: VIDEO_MODEL, modelCapabilities })).toEqual(unchanged);
  });


  it('衔接方式从连续动作改成切镜后移除旧承接边', () => {
    const scriptRows = sceneRows();
    seedScene(scriptRows);
    const first = scatterScriptShotVideos({
      scriptNodeId: SCRIPT_ID,
      model: VIDEO_MODEL,
      continuityReference: true,
    });
    expect(first.ok).toBe(true);
    if (!first.ok) return;
    const secondId = videoByRowKey().get('shot:2')?.id ?? '';
    const firstId = videoByRowKey().get('shot:1')?.id ?? '';
    useCanvasStore.getState().updateNodeData(firstId, { videoUrl: '/first.mp4' });
    useCanvasStore.getState().addNode(CANVAS_NODE_TYPES.upload, { x: 0, y: 0 }, {
      imageUrl: '/first-tail.png',
      captureMetadata: { source_kind: 'video_frame_capture', source_node_id: firstId, source_video_url: '/first.mp4', capture_mode: 'last' },
    });
    scatterScriptShotVideos({ scriptNodeId: SCRIPT_ID, model: VIDEO_MODEL, continuityReference: true });
    expect(chainEdge(secondId)).toBeTruthy();

    setRows(scriptRows.map((row, index) => index === 0 ? { ...row, transition_plan: 'direct_cut' } : row));
    const rebuilt = scatterScriptShotVideos({
      scriptNodeId: SCRIPT_ID,
      model: VIDEO_MODEL,
      continuityReference: true,
    });
    expect(rebuilt.ok).toBe(true);
    expect(chainEdge(secondId)).toBeUndefined();
  });

  it('模型吃不下第二张图时一条承接边都不接', () => {
    seedScene(sceneRows());
    const result = scatterScriptShotVideos({
      scriptNodeId: SCRIPT_ID,
      model: VIDEO_MODEL,
      continuityReference: false,
    });
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    videoNodes().forEach((node) => expect(chainEdge(node.id)).toBeUndefined());
    // 首帧边一条不少 —— 不接承接边不等于这一镜没得生成。
    videoNodes().forEach((node) => expect(firstFrameEdge(node.id)).toBeTruthy());
  });

  it('上一镜还没出图时不接（接不了没有画面的图）', () => {
    const imageIds = seedScene(sceneRows(), { generated: false });
    // 只让第 2 镜出图：第 1 镜没图，第 2 镜就没有可承接的画面来源。
    useCanvasStore.getState().updateNodeData(imageIds[1], { imageUrl: 'frame-1.png' });

    const result = scatterScriptShotVideos({
      scriptNodeId: SCRIPT_ID,
      model: VIDEO_MODEL,
      continuityReference: true,
    });
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.nodeIds).toHaveLength(1);
    expect(chainEdge(result.nodeIds[0])).toBeUndefined();
  });

  it('计划账如实报出会接承接边的镜数', () => {
    seedScene(sceneRows());
    const blocked = planScriptShotVideos(SCRIPT_ID, undefined, {
      continuityReference: false,
    });
    expect(blocked.ok).toBe(true);
    if (!blocked.ok) return;
    expect(blocked.continuityCount).toBe(0);

    const allowed = planScriptShotVideos(SCRIPT_ID, undefined, {
      continuityReference: true,
    });
    expect(allowed.ok).toBe(true);
    if (!allowed.ok) return;
    expect(allowed.continuityCount).toBe(0);
  });

  it('重复点只补边不重跑：上一镜的图变了不把这一镜重新排队', () => {
    seedScene(sceneRows());
    const first = scatterScriptShotVideos({
      scriptNodeId: SCRIPT_ID,
      model: VIDEO_MODEL,
      continuityReference: true,
    });
    expect(first.ok).toBe(true);
    if (!first.ok) return;

    const byKey = videoByRowKey();
    // 先把第 2、3 镜标成已出片：派生时快照已经写好，只差一个 videoUrl。
    ['shot:2', 'shot:3'].forEach((key) => {
      completeShot(String(byKey.get(key)?.id), `${key}.mp4`);
    });

    // 第 1 镜分镜图原地重出（URL 变了）：第 2 镜的**首帧**没变，就不该重新付费。
    // 承接边让第 2 镜多参考了一张图，但那是上一镜的画面锚，不是主导输入。
    useCanvasStore
      .getState()
      .updateNodeData(String(byKey.get('shot:1')?.data[SCRIPT_SHOT_VIDEO_IMAGE_FIELD]), {
        imageUrl: 'frame-0-regenerated.png',
      });

    const plan = planScriptShotVideos(SCRIPT_ID, undefined, {
      continuityReference: true,
    });
    expect(plan.ok).toBe(true);
    if (!plan.ok) return;
    expect(plan.mode).toBe('regenerate');
    expect(plan.pendingCount).toBe(1);

    const rearm = scatterScriptShotVideos({
      scriptNodeId: SCRIPT_ID,
      model: VIDEO_MODEL,
      generateVideos: true,
      continuityReference: true,
    });
    expect(rearm.ok).toBe(true);
    if (!rearm.ok) return;
    expect(rearm.armed).toBe(1);
    // 承接边还在，而且指向的仍是那个实时节点（提交时读实时上游）。
    expect(chainEdge(byKey.get('shot:2')?.id ?? '')).toBeUndefined();
  });
});

/**
 * T-154 · 逐镜派生时的音频口径与逐镜体检表。
 *
 * 此前这里写死 `generateAudio: false`：脚本的 `sound` 列在画布这条路上没有任何消费方，
 * 有音效设计的镜头全被提交成静音，有台词的镜头也没走外部配音。现在按行事实路由
 * （口径对齐后端 `freezone_videos._shot_audio_contract`，见 T-152）：
 * 有台词→外部配音、有音效无台词→模型原生声音、两者皆无→静音。
 */
function audioRows(): FreezoneStoryScriptRow[] {
  return [
    {
      shot_no: '1',
      duration: '4',
      shot: '特写',
      visual_description: '阿雀停在门外',
      shot_prompt: '特写阿雀停在门外',
      video_motion_prompt: '[运镜轨迹] 固定机位 + [时长：4.0s]',
      sound: '雨声、门轴吱呀声',
      dialogue: '无',
    },
    {
      shot_no: '2',
      duration: '3',
      shot: '中景',
      visual_description: '阿雀抬头看向门内',
      shot_prompt: '中景阿雀抬头',
      video_motion_prompt: '[运镜轨迹] 镜头前推 + [时长：3.0s]',
      sound: '无',
      dialogue: '你到底来不来？我等到天亮。',
    },
    {
      shot_no: '3',
      duration: '5',
      shot: '远景',
      visual_description: '雨里的空巷',
      shot_prompt: '远景空巷',
      video_motion_prompt: '[运镜轨迹] 镜头拉远 + [时长：12.0s]',
      sound: '无台词',
      dialogue: '无台词',
    },
  ];
}

describe('逐镜出视频 · 音频合同与逐镜体检表', () => {
  beforeEach(() => {
    resetStoryboardSettle();
    seedScriptNode(audioRows());
  });

  it('有音效没台词的镜排模型原生声音，台词列写「无」不算台词', () => {
    seedStoryboardImages({ rows: audioRows() });
    const result = scatterVideos();
    expect(result.ok).toBe(true);
    if (!result.ok) return;

    const data = videoByRowKey().get('shot:1')?.data;
    expect(data?.generateAudio).toBe(true);
    expect(data?.nativeAudioStrategy).toBe('native');
    expect(data?.dialogueText).toBeUndefined();
  });

  it('有台词的镜走原生声音，整句进 dialogueText、拆句进 spokenDialogue', () => {
    seedStoryboardImages({ rows: audioRows() });
    const result = scatterVideos();
    expect(result.ok).toBe(true);
    if (!result.ok) return;

    const data = videoByRowKey().get('shot:2')?.data;
    expect(data?.generateAudio).toBe(true);
    expect(data?.audioType).toBe('dialogue');
    expect(data?.nativeAudioStrategy).toBe('native');
    expect(data?.dialogueText).toBe('你到底来不来？我等到天亮。');
    expect(data?.spokenDialogue).toEqual(['你到底来不来', '我等到天亮']);
  });

  it('既没音效也没台词的镜仍默认开启原生声音', () => {
    seedStoryboardImages({ rows: audioRows() });
    const result = scatterVideos();
    expect(result.ok).toBe(true);
    if (!result.ok) return;

    const data = videoByRowKey().get('shot:3')?.data;
    expect(data?.generateAudio).toBe(true);
    expect(data?.audioType).toBeUndefined();
    expect(data?.nativeAudioStrategy).toBe('native');
  });

  it('派生出来的节点不再带 cameraMovement 字段（运镜只在提示词里）', () => {
    seedStoryboardImages({ rows: audioRows() });
    const result = scatterVideos();
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    videoNodes().forEach((node) => {
      expect(node.data).not.toHaveProperty('cameraMovement');
    });
  });

  it('体检表逐镜摊开首帧、运镜、提示词来源与音轨', () => {
    seedStoryboardImages({ rows: audioRows() });
    const plan = planScriptShotVideos(SCRIPT_ID);
    expect(plan.ok).toBe(true);
    if (!plan.ok) return;

    expect(plan.rows.map((row) => row.shotNumber)).toEqual(['1', '2', '3']);
    expect(plan.rows.map((row) => row.audioRoute)).toEqual(['native', 'native', 'native']);
    expect(plan.rows.map((row) => row.promptSource)).toEqual(['motion', 'motion', 'motion']);
    expect(plan.rows[0].camera).toBe('固定机位');
    expect(plan.rows[1].camera).toBe('镜头前推');
    expect(plan.rows[1].dialogue).toBe('你到底来不来？我等到天亮。');
    expect(plan.rows.every((row) => row.firstFrameUrl.length > 0)).toBe(true);
    // 第 3 镜运动稿写 12s、时长列写 5s：照实标出来，但不改用户数据。
    expect(plan.rows[2].issues).toEqual(['durationMismatch']);
  });

  it('缺首帧的镜在体检表里标出来，而不是等点完再报', () => {
    // 只让前两镜出图：第三镜这一批进不去，表里要如实标「缺首帧」。
    const imageIds = seedStoryboardImages({ rows: audioRows(), generated: false });
    imageIds.slice(0, 2).forEach((nodeId, index) => {
      useCanvasStore.getState().updateNodeData(nodeId, { imageUrl: `frame-${index}.png` });
    });

    const plan = planScriptShotVideos(SCRIPT_ID);
    expect(plan.ok).toBe(true);
    if (!plan.ok) return;
    expect(plan.shotCount).toBe(2);
    expect(plan.rows.map((row) => row.issues.includes('noImage'))).toEqual([false, false, true]);
  });

  it('没有摄影段时标为未识别摄影安排，不猜摄影结果', () => {
    setRows([
      {
        shot_no: '1',
        duration: '4',
        visual_description: '雨打门环',
        shot_prompt: '特写门环',
        video_motion_prompt: '[主体极其具体的物理动作：雨珠砸在门环上]',
      },
    ]);
    seedStoryboardImages({ rows: [
      {
        shot_no: '1',
        duration: '4',
        visual_description: '雨打门环',
        shot_prompt: '特写门环',
        video_motion_prompt: '[主体极其具体的物理动作：雨珠砸在门环上]',
      },
    ] });

    const plan = planScriptShotVideos(SCRIPT_ID);
    expect(plan.ok).toBe(true);
    if (!plan.ok) return;
    expect(plan.rows[0].camera).toBe('');
    expect(plan.rows[0].issues).toContain('noCamera');
    expect(plan.shotCount).toBe(1);
  });

  it('无标签摄影进入逐镜审查，矛盾提醒不自动改稿，明确覆盖仍能替换和还原', () => {
    const camera = '固定的中景背影机位，镜头始终跟随跑者奔跑，跑者起跑后镜头不再移动';
    const rows = [{ shot_no: '1', duration: '4', visual_description: '跑者奔跑', shot_prompt: '中景背影',
      video_motion_prompt: `[${camera}] + [主体动作：跑者奔跑] + [环境物理动态：风吹草] + [音效：脚步声] + [对话台词：无] + [时长：4s]` }];
    setRows(rows);
    seedStoryboardImages({ rows });
    const before = useCanvasStore.getState().nodes;
    const plan = planScriptShotVideos(SCRIPT_ID);
    expect(plan.ok).toBe(true);
    if (!plan.ok) return;
    expect(plan.rows[0].camera).toBe(camera);
    expect(plan.rows[0].issues).toContain('cameraNeedsReview');
    expect(plan.rows[0].issues).not.toContain('noCamera');
    expect(useCanvasStore.getState().nodes).toBe(before);
    expect(scriptShotVideoPrompt({ ...rows[0], camera_movement: '镜头先跟随跑者，跑者站定后机位停止' }).prompt)
      .not.toContain('始终跟随');
    expect(scriptShotVideoPrompt(rows[0]).prompt).toContain(camera);
  });
});
