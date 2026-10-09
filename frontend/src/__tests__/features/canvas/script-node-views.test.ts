// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from 'vitest';

import type { FreezoneStoryScriptRow } from '@/api/ops';
import { CANVAS_NODE_TYPES, type CanvasNode } from '@/features/canvas/domain/canvasNodes';
import { scriptRowFingerprint } from '@/features/canvas/nodes/script/scriptRowFingerprint';
import { SCRIPT_SHOT_VIDEO_ROW_FINGERPRINT_FIELD } from '@/features/canvas/nodes/script/scriptShotVideos';
import { buildScriptCsv, buildScriptCsvFileName } from '@/features/canvas/nodes/script/scriptCsv';
import { scriptFieldSequence, SCRIPT_TABLE_FIELDS } from '@/features/canvas/nodes/script/scriptFields';
import {
  buildScriptShotSpecs,
  buildScriptStoryboardPlan,
  findStoryboardBlockOrigin,
  storyboardGridCols,
  storyboardGridPositions,
  storyboardGroupLabel,
} from '@/features/canvas/nodes/script/scriptStoryboard';
import {
  buildScriptRowKeys,
  collectScriptCharacters,
  collectScriptProps,
  collectScriptScenes,
  isReusableSceneTag,
  maxOccupiedCharacterSlots,
  resolveScriptViewId,
  rowCharacters,
  rowImagePrompt,
  scriptRowKey,
  scriptRowShotNumber,
  splitScriptTags,
} from '@/features/canvas/nodes/script/scriptViews';

function row(overrides: Partial<FreezoneStoryScriptRow> = {}): FreezoneStoryScriptRow {
  return { shot_no: '1', duration: '3', visual_description: '开场', ...overrides };
}

it.each(['无', 'none', 'N/A', '-'])('图片占位稿%s回落到可见画面描述', marker => {
  expect(rowImagePrompt(row({ shot_prompt: marker, visual_description: '门环悬在雨中' }))).toBe('门环悬在雨中');
  expect(rowImagePrompt(row({ shot_prompt: marker, visual_description: marker }))).toBe('');
  expect(buildScriptShotSpecs([row({ shot_prompt: marker, visual_description: '门环悬在雨中' })])[0].basePrompt).toBe('门环悬在雨中');
  expect(buildScriptShotSpecs([row({ shot_prompt: marker, visual_description: marker })])[0].prompt).toBe('');
});

it('exports performance timing reasons using the existing editable field list', () => {
  const csv = buildScriptCsv([row({ duration: 15, duration_reason: '观察三秒，讲解十秒，停留两秒' })]);
  expect(csv).toContain('时长依据');
  expect(csv).toContain('观察三秒，讲解十秒，停留两秒');
  expect(scriptFieldSequence().find(field => field.key === 'duration_reason')?.label).toBe('时长依据');
});

describe('资产标签边界', () => {
  it('场景台账只保留可复用空间，镜头效果留在分镜提示词', () => {
    expect(isReusableSceneTag('极简静物空间')).toBe(true);
    expect(isReusableSceneTag('横向低角度光带')).toBe(false);
    expect(isReusableSceneTag('悬浮尘粒')).toBe(false);
    expect(isReusableSceneTag('微距材质空间')).toBe(false);
    expect(collectScriptScenes([
      row({ scene_tags: '极简静物空间、横向低角度光带、浅灰石台面、悬浮尘粒' }),
    ]).map((entry) => entry.name)).toEqual(['极简静物空间', '浅灰石台面']);
  });
});

describe('脚本节点字段表', () => {
  it('表格列 = 前段 + 2 组角色列 + 后段，角色三连列紧跟「画面描述」', () => {
    const keys = SCRIPT_TABLE_FIELDS.map((field) => field.key);
    expect(keys.slice(0, 3)).toEqual(['shot_no', 'duration', 'visual_description']);
    expect(keys.slice(3, 9)).toEqual([
      'character_1',
      'character_description_1',
      'character_image_1',
      'character_2',
      'character_description_2',
      'character_image_2',
    ]);
    expect(keys).toContain('reference');
    expect(keys).toContain('shot_prompt');
    expect(keys).toContain('video_motion_prompt');
  });

  it('按槽位数展开时列数递减（CSV 动态列用）', () => {
    expect(scriptFieldSequence(1).map((f) => f.key)).not.toContain('character_2');
    expect(scriptFieldSequence(2).map((f) => f.key)).toContain('character_2');
    // 少于 1 组角色列没有意义，至少保留 1 组。
    expect(scriptFieldSequence(0).map((f) => f.key)).toContain('character_1');
  });
});

describe('脚本视图', () => {
  it('视图 id 收敛：未知值回落脚本视图', () => {
    expect(resolveScriptViewId('creative')).toBe('creative');
    expect(resolveScriptViewId('asset')).toBe('asset');
    expect(resolveScriptViewId('table')).toBe('table');
    expect(resolveScriptViewId('卡片视图')).toBe('table');
    expect(resolveScriptViewId(undefined)).toBe('table');
  });

  it('行标识优先镜号，其次关键帧，最后下标', () => {
    expect(scriptRowKey(row({ keyframe_index: 7, shot_no: '3' }), 0)).toBe('shot:3');
    expect(scriptRowKey(row({ keyframe_index: 7, shot_no: '' }), 0)).toBe('kf:7');
    expect(scriptRowKey(row({ shot_no: null }), 4)).toBe('idx:4');
  });

  it('整表行标识保证唯一：镜号重复 / 都缺镜号同属一张关键帧时补序号', () => {
    // 真实场景：脚本由关键帧派生，后端每行 keyframe_index 都是 0，镜号才是唯一值。
    const keyed = buildScriptRowKeys([
      row({ shot_no: '1', keyframe_index: 0 }),
      row({ shot_no: '2', keyframe_index: 0 }),
      row({ shot_no: '3', keyframe_index: 0 }),
    ]);
    expect(keyed).toEqual(['shot:1', 'shot:2', 'shot:3']);
    expect(new Set(keyed).size).toBe(3);

    const duplicated = buildScriptRowKeys([
      row({ shot_no: '', keyframe_index: 0 }),
      row({ shot_no: '', keyframe_index: 0 }),
      row({ shot_no: '' }),
    ]);
    expect(duplicated).toEqual(['kf:0', 'kf:0#2', 'idx:2']);
    expect(new Set(duplicated).size).toBe(3);
  });

  it('镜号缺列时回落下标 + 1', () => {
    expect(scriptRowShotNumber(row({ shot_no: '12' }), 0)).toBe('12');
    expect(scriptRowShotNumber(row({ shot_no: '' }), 4)).toBe('5');
  });

  it('只收非空角色槽，最多两槽', () => {
    expect(rowCharacters(row())).toHaveLength(0);
    const two = rowCharacters(
      row({ character_1: '阿雀', character_image_1: 'a.png', character_2: '老树' }),
    );
    expect(two.map((character) => character.slot)).toEqual([1, 2]);
    expect(two[0].imageUrl).toBe('a.png');
    expect(maxOccupiedCharacterSlots([row({ character_2: '老树' })])).toBe(2);
    expect(maxOccupiedCharacterSlots([row({ character_1: '阿雀' })])).toBe(1);
    expect(maxOccupiedCharacterSlots([row()])).toBe(0);
  });

  it('分镜提示词缺失时回落画面描述（对齐 LibTV imageGenerationPrompt || plotDescription）', () => {
    expect(rowImagePrompt(row({ shot_prompt: '镜头推近', visual_description: '开场' }))).toBe(
      '镜头推近',
    );
    expect(rowImagePrompt(row({ shot_prompt: '  ', visual_description: '开场' }))).toBe('开场');
  });

  it('资产视图按角色聚合出场镜次，按场景标签聚合场景', () => {
    const rows = [
      row({ shot_no: '1', character_1: '阿雀', character_image_1: 'a.png', scene_tags: '祠堂、黄昏' }),
      row({ shot_no: '2', character_1: '阿雀', character_description_1: '红衣', scene_tags: '祠堂' }),
      row({ shot_no: '3', character_2: '老树' }),
    ];
    const characters = collectScriptCharacters(rows);
    expect(characters.map((entry) => entry.name)).toEqual(['阿雀', '老树']);
    expect(characters[0].shotNumbers).toEqual(['1', '2']);
    expect(characters[0].description).toBe('红衣');
    const scenes = collectScriptScenes(rows);
    expect(scenes.map((entry) => entry.name)).toEqual(['祠堂', '黄昏']);
    expect(scenes[0].shotNumbers).toEqual(['1', '2']);
  });

  // 道具是 LibTV 资产台账的第三族（`props`）—— 此前我们的表结构里根本没有这一列，
  // 于是资产视图也只有角色与场景两族。
  it('资产视图按道具标签聚合，且不像场景那样借参考帧当预览', () => {
    const rows = [
      row({ shot_no: '1', prop_tags: '玉佩、青铜钥匙', reference: 'frame-1.png' }),
      row({ shot_no: '2', prop_tags: '玉佩', reference: 'frame-2.png' }),
    ];
    const props = collectScriptProps(rows);
    expect(props.map((entry) => entry.name)).toEqual(['玉佩', '青铜钥匙']);
    expect(props[0].shotNumbers).toEqual(['1', '2']);
    // 参考帧是整镜构图，不能冒充「玉佩长什么样」。
    expect(props.every((entry) => entry.imageUrl === null)).toBe(true);
  });

  it('标签拆分把「无」排掉 —— 它是「本镜没有」的占位，不是标签', () => {
    expect(splitScriptTags('玉佩、无、青铜钥匙')).toEqual(['玉佩', '青铜钥匙']);
    expect(splitScriptTags('无')).toEqual([]);
    expect(splitScriptTags('')).toEqual([]);
    expect(splitScriptTags(' 祠堂 , 黄昏 ')).toEqual(['祠堂', '黄昏']);
  });
});

describe('分镜表导出 CSV', () => {
  function video(shotId: string, url: string, snapshot?: string): CanvasNode {
    return { id: `video-${shotId}`, type: CANVAS_NODE_TYPES.video, position: { x: 0, y: 0 },
      data: { scriptShotId: shotId, videoUrl: url, [SCRIPT_SHOT_VIDEO_ROW_FINGERPRINT_FIELD]: snapshot } };
  }

  it('交接信息按片序和稳定身份匹配视频，不按镜号或节点顺序猜', () => {
    const ordered = [row({ shot_id: 'B', shot_no: 9, display_shot_no: '补拍', shot_order: 2 }), row({ shot_id: 'A', shot_no: 1 })];
    const csv = buildScriptCsv(ordered, {
      directorPlan: { sequences: [{ sequence_id: 'S1', title: '起跳', shot_nos: [9] }] },
      videos: [video('A', '/A.mp4', scriptRowFingerprint(ordered[1], 1)), video('B', '/B.mp4', scriptRowFingerprint(ordered[0], 0))],
    });
    const lines = csv.split('\r\n');
    expect(lines[0]).toContain('片序,镜头身份,显示镜号,所属段落,视频节点,视频状态,视频地址');
    expect(lines[1]).toContain('1,B,补拍,S1 起跳,video-B,内容对应当前脚本，观感未验,/B.mp4');
    expect(lines[2]).toContain('2,A,1,,video-A,内容对应当前脚本，观感未验,/A.mp4');
  });

  it.each(['过期', '失败', '重复', '未同步'])('不把%s素材地址当成可交接结果', (problem) => {
    const current = row({ shot_id: 'A' });
    const clip = video('A', '/wrong.mp4', problem === '过期' ? 'old' : scriptRowFingerprint(current));
    if (problem === '失败') clip.data.generationError = 'failed';
    const csv = buildScriptCsv([current], { videos: problem === '重复' ? [clip, { ...clip, id: 'duplicate' }] : [clip], directorPlanPending: problem === '未同步' });
    expect(csv).not.toContain('/wrong.mp4');
    expect(csv).toContain(problem === '过期' ? '视频过期' : problem === '失败' ? '生成失败' : problem === '重复' ? '存在重复视频' : '导演规划未同步');
  });

  it('没有指纹的历史视频与未出片分开说明，不伪装为验收通过', () => {
    const csv = buildScriptCsv([row({ shot_id: 'A' }), row({ shot_id: 'B', shot_no: 2 })], { videos: [video('A', '/legacy.mp4')] });
    expect(csv).toContain('已有视频，内容对应未核验,/legacy.mp4');
    expect(csv).toContain('未出片');
  });

  it('交接表不把丢失尾帧参考的旧视频列成可用素材', () => {
    const current = row({ shot_id: 'A' });
    const clip = video('A', '/old.mp4', scriptRowFingerprint(current));
    clip.data.scriptShotTailReferenceUrl = '/tail.png';
    const csv = buildScriptCsv([current], { videos: [clip], graph: { nodes: [clip], edges: [] } });
    expect(csv).toContain('尾帧参考已变化，视频过期');
    expect(csv).not.toContain('/old.mp4');
  });

  it('角色列按各行实际占用槽位数的最大值展开', () => {
    const csv = buildScriptCsv([
      row({ character_1: '阿雀' }),
      row({ shot_no: '2', character_2: '老树' }),
    ]);
    const [header] = csv.replace('\uFEFF', '').split('\r\n');
    expect(header).toContain('角色1,角色描述1,角色图1,角色2,角色描述2,角色图2');
    // 只有第一槽时不该出现第二槽列。
    const single = buildScriptCsv([row({ character_1: '阿雀' })]);
    expect(single).not.toContain('角色2');
  });

  it('含逗号 / 引号 / 换行的单元格按 RFC 4180 转义', () => {
    const csv = buildScriptCsv([
      row({ visual_description: '他说,"走"', dialogue: '第一句\n第二句' }),
    ]);
    expect(csv).toContain('"他说,""走"""');
    expect(csv).toContain('"第一句\n第二句"');
  });

  it('「参考」列取参考帧图，并带 UTF-8 BOM', () => {
    const csv = buildScriptCsv([row({ reference: 'ref.png' })]);
    expect(csv.startsWith('\uFEFF')).toBe(true);
    const [, firstDataRow] = csv.split('\r\n');
    expect(firstDataRow).toContain('ref.png');
  });

  // 导出与表格共用一份字段定义 —— 加了列却忘了 CSV 的话，用户在表里填好道具、
  // 导出给别人时道具整列消失（这类「新列只在表格里存在」的漂移此前有过）。
  it('道具列随字段定义一起进 CSV', () => {
    const csv = buildScriptCsv([row({ prop_tags: '玉佩、青铜钥匙' })]);
    const [header, firstDataRow] = csv.replace('\uFEFF', '').split('\r\n');
    expect(header).toContain('道具标签');
    expect(firstDataRow).toContain('玉佩、青铜钥匙');
  });

  it('文件名用标题派生并清掉非法字符', () => {
    expect(buildScriptCsvFileName('县衙惊堂·状纸诉冤')).toBe('县衙惊堂·状纸诉冤-分镜脚本.csv');
    expect(buildScriptCsvFileName('a/b:c')).toBe('a_b_c-分镜脚本.csv');
    expect(buildScriptCsvFileName('')).toBe('分镜脚本-分镜脚本.csv');
  });
});

describe('生成分镜规划', () => {
  it('宫格列数取 ceil(sqrt(n))（与 LibTV 一致）', () => {
    expect(storyboardGridCols(0)).toBe(1);
    expect(storyboardGridCols(1)).toBe(1);
    expect(storyboardGridCols(4)).toBe(2);
    expect(storyboardGridCols(5)).toBe(3);
    expect(storyboardGridCols(16)).toBe(4);
  });

  it('分组命名对齐 LibTV 的「分镜图 · <脚本名>」', () => {
    expect(storyboardGroupLabel('天空之跃')).toBe('分镜图 · 天空之跃');
    expect(storyboardGroupLabel('  ')).toBe('分镜图 · 脚本');
  });

  it('逐行派生镜头规格：提示词、参考图、参考帧兜底就位', () => {
    const shots = buildScriptShotSpecs([
      row({ shot_no: '1', shot_prompt: '推近', character_1: '阿雀', character_image_1: 'a.png', reference: 'r.png' }),
      row({ shot_no: '2', visual_description: '收尾' }),
    ]);
    expect(shots.map((shot) => shot.name)).toEqual(['分镜 #1', '分镜 #2']);
    // 行原文进 basePrompt；带出去的那句前面挂资产图锚定块（名字 ↔ 图片编号）。
    expect(shots[0].basePrompt).toBe('推近');
    expect(shots[0].prompt).toContain('资产图锚定：\n角色 阿雀 的参考图是 图片1；');
    expect(shots[0].prompt).toContain('不让参考图覆盖本镜状态');
    expect(shots[0].prompt).toContain('设定板中的多个角度属于同一个对象');
    expect(shots[0].prompt).toContain('\n\n推近');
    expect(shots[0].referenceUrls).toEqual(['a.png']);
    // 角色图来自行本身，没有台账也进锚定块；场景/道具没有台账时不参与。
    expect(shots[0].references.map((entry) => entry.assetId)).toEqual(['character:阿雀']);
    // 没有角色图时保留原文和质量要求，不建角色锚定块。
    expect(shots[1].prompt).toContain('收尾');
    expect(shots[1].prompt).not.toContain('资产图锚定');
    expect(shots[1].referenceImageUrl).toBeNull();
  });

  // 参考帧（行「参考」列）只在**没有任何带身份的角色图**时才上场：分镜图节点自身的
  // `@图片1` 编号基线挂在第 1 张上，多塞一张会让已有节点里写好的 `@图片1` 全部错位。
  it('参考帧只做兜底：有角色图时它不占槽位', () => {
    const shots = buildScriptShotSpecs([
      row({ shot_no: '1', character_1: '阿雀', character_image_1: 'a.png', reference: 'r.png' }),
      row({ shot_no: '2', reference: 'r.png' }),
    ]);
    expect(shots[0].referenceUrls).toEqual(['a.png']);
    expect(shots[1].referenceUrls).toEqual(['r.png']);
    // 兜底那条没有身份（assetId 为 null），不会进锚定块。
    expect(shots[1].references[0].assetId).toBeNull();
  });

  it('宫格从左到右、从上到下排布，顺序与分镜行一致', () => {
    const positions = storyboardGridPositions({
      origin: { x: 100, y: 200 },
      count: 5,
      cellWidth: 580,
      cellHeight: 360,
      gapX: 48,
      gapY: 32,
    });
    expect(positions).toHaveLength(5);
    expect(positions[0]).toEqual({ x: 100, y: 200 });
    expect(positions[1].x).toBeGreaterThan(positions[0].x);
    expect(positions[1].y).toBe(positions[0].y);
    // 第 4 个回到第二行行首。
    expect(positions[3]).toEqual({ x: 100, y: 200 + 360 + 32 });
  });

  it('没有分镜行时不产出计划，并给出可读原因', () => {
    const plan = buildScriptStoryboardPlan({
      rows: [],
      scriptTitle: '空脚本',
      origin: { x: 0, y: 0 },
      cellWidth: 580,
      cellHeight: 360,
    });
    expect(plan.ok).toBe(false);
    if (!plan.ok) expect(plan.reason).toContain('还没有分镜行');
  });

  it('整块落位对齐 LibTV：默认右侧顶部对齐，落点被下游压住才排到下游下方', () => {
    const script = { x: 0, y: 0, width: 800, height: 400 };
    const free = findStoryboardBlockOrigin({
      script,
      gridWidth: 580,
      gridHeight: 360,
    });
    expect(free).toEqual({ x: 856, y: 0 }); // 800 + 56，顶部对齐

    // 下游正好压在落点上 → 排到它下面。
    const blockedByDownstream = findStoryboardBlockOrigin({
      script,
      gridWidth: 580,
      gridHeight: 360,
      downstream: [{ x: 900, y: 0, width: 580, height: 360 }],
    });
    expect(blockedByDownstream.x).toBe(856);
    expect(blockedByDownstream.y).toBe(360 + 48);

    // 下游在很远处（不压落点）→ 仍然贴着脚本节点，不被甩飞。
    const farDownstream = findStoryboardBlockOrigin({
      script,
      gridWidth: 580,
      gridHeight: 360,
      downstream: [{ x: 900, y: 4200, width: 580, height: 360 }],
    });
    expect(farDownstream).toEqual({ x: 856, y: 0 });

    // 兜底：落点被无关节点占住时逐行下移。
    const blocked = findStoryboardBlockOrigin({
      script,
      gridWidth: 580,
      gridHeight: 360,
      occupied: [{ x: 856, y: 0, width: 600, height: 380 }],
    });
    expect(blocked.x).toBe(856);
    expect(blocked.y).toBeGreaterThan(0);
  });
});
