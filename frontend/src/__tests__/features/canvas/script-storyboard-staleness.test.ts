// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { beforeEach, describe, expect, it } from 'vitest';

import type { FreezoneStoryScriptRow } from '@/api/ops';
import { CANVAS_NODE_TYPES, isImageGenNode } from '@/features/canvas/domain/canvasNodes';
import {
  generateScriptStoryboard,
  regenerateStoryboardGroupImages,
  storyboardGroupMembers,
  storyboardMemberIdsToRearm,
} from '@/features/canvas/nodes/script/generateScriptStoryboard';
import {
  buildScriptRowSnapshots,
  computeStoryboardStaleness,
  storyboardStalenessForGroup,
  storyboardStalenessForScript,
} from '@/features/canvas/nodes/script/scriptStaleness';
import { resetStoryboardSettle } from '@/features/canvas/nodes/script/storyboardSettle';
import { useCanvasStore } from '@/stores/canvasStore';
import { scriptPreflightForScript } from '@/features/canvas/nodes/script/scriptPreflight';
import { scriptPreviewFrames } from '@/features/canvas/nodes/script/scriptPreviewFrames';
import { planScriptShotVideos } from '@/features/canvas/nodes/script/scriptShotVideos';

/**
 * 脚本 → 分镜图 级联失效闸门（T-013 / A）。
 *
 * 这些用例证明的是「脚本表格改过之后，画布不再装作没事」：
 * 判过期、把失效的张算进重跑对象、重跑之后标记清掉。
 */

const SCRIPT_NODE_ID = 'script-node';
const SCRIPT_SIZE = { width: 800, height: 400 };
const MODEL = 'direct/image-mskqiqdq-ed3hhy';

function rows(): FreezoneStoryScriptRow[] {
  return [
    {
      shot_no: '1',
      visual_description: '开场',
      shot_prompt: '镜头推近祠堂',
      character_1: '阿雀',
      character_image_1: 'a.png',
    },
    { shot_no: '2', visual_description: '收尾' },
  ];
}

function seedScriptNode(rowList: FreezoneStoryScriptRow[] = rows()) {
  useCanvasStore.getState().setCanvasData(
    [
      {
        id: SCRIPT_NODE_ID,
        type: CANVAS_NODE_TYPES.script,
        position: { x: 0, y: 0 },
        style: { width: SCRIPT_SIZE.width, height: SCRIPT_SIZE.height },
        data: {
          scriptResult: { title: '天空之跃', rows: rowList },
          scriptTitle: '天空之跃',
        },
      },
    ] as never,
    [],
  );
}

function generate(rowList: FreezoneStoryScriptRow[] = rows()) {
  return generateScriptStoryboard({
    scriptNodeId: SCRIPT_NODE_ID,
    rows: rowList,
    scriptTitle: '天空之跃',
    scriptSize: SCRIPT_SIZE,
    config: { model: MODEL, aspectRatio: '16:9' },
    generateImages: false,
  });
}

/** 把某一镜标记成「出好图了」，用来模拟一次成功的生成。 */
function markGenerated(nodeId: string, url = 'shot.png') {
  useCanvasStore.getState().updateNodeData(nodeId, { imageUrl: url });
}

/** 改脚本表格里某一行的某一列（与 ScriptNode 的 handleCellCommit 同一口径）。 */
function editRow(index: number, colKey: string, nextValue: string, rowList = rows()) {
  const nextRows = rowList.map((row, i) => (i === index ? { ...row, [colKey]: nextValue } : row));
  useCanvasStore.getState().updateNodeData(SCRIPT_NODE_ID, {
    scriptResult: { title: '天空之跃', rows: nextRows },
  });
  return nextRows;
}

describe('脚本 → 分镜图 级联失效 · 纯判定', () => {
  it('行内容一致时不判过期（否则徽标会恒亮）', () => {
    const snapshots = buildScriptRowSnapshots(rows());
    // 行里带角色图 ⇒ 提示词里带资产图锚定块；节点上留下的快照就是这句（含块）。
    expect(snapshots[0].prompt).toContain('角色 阿雀 的参考图是 图片1');
    expect(snapshots[0].prompt).toContain('\n\n镜头推近祠堂');
    const result = computeStoryboardStaleness({
      members: [
        { nodeId: 'n1', rowKey: 'shot:1', prompt: snapshots[0].prompt, reference: 'a.png' },
        { nodeId: 'n2', rowKey: 'shot:2', prompt: snapshots[1].prompt, reference: null },
      ],
      rows: snapshots,
    });
    expect(result.staleNodeIds).toEqual([]);
    expect(result.rowCountChanged).toBe(false);
  });

  it('提示词改了 ⇒ 只那一张判过期，原因是 prompt-changed', () => {
    const base = buildScriptRowSnapshots(rows());
    const snapshots = buildScriptRowSnapshots(
      rows().map((row, index) =>
        index === 1 ? { ...row, visual_description: '收尾（改过）' } : row,
      ),
    );
    const result = computeStoryboardStaleness({
      members: [
        { nodeId: 'n1', rowKey: 'shot:1', prompt: base[0].prompt, reference: 'a.png' },
        { nodeId: 'n2', rowKey: 'shot:2', prompt: base[1].prompt, reference: null },
      ],
      rows: snapshots,
    });
    expect(result.staleNodeIds).toEqual(['n2']);
    expect(result.reasons.get('n2')).toEqual(['prompt-changed']);
  });

  it('参考图换了 ⇒ reference-changed', () => {
    const base = buildScriptRowSnapshots(rows());
    const snapshots = buildScriptRowSnapshots(
      rows().map((row, index) => (index === 0 ? { ...row, character_image_1: 'b.png' } : row)),
    );
    const result = computeStoryboardStaleness({
      members: [
        { nodeId: 'n1', rowKey: 'shot:1', prompt: base[0].prompt, reference: 'a.png' },
      ],
      rows: snapshots,
    });
    expect(result.staleNodeIds).toEqual(['n1']);
    expect(result.reasons.get('n1')).toEqual(['reference-changed']);
  });

  it('资产 revision 变化只让真正引用它的镜头过期', () => {
    const baseRows: FreezoneStoryScriptRow[] = [
      {
        shot_no: '1',
        shot_prompt: '阿雀抬头',
        character_1: '阿雀',
        character_asset_id_1: 'char_aque',
        character_description_1: '短发',
        character_image_1: 'a.png',
      },
      {
        shot_no: '2',
        shot_prompt: '老树落下',
        character_1: '老树',
        character_asset_id_1: 'char_tree',
        character_description_1: '枯木',
        character_image_1: 'b.png',
      },
    ];
    const base = buildScriptRowSnapshots(baseRows);
    const changed = buildScriptRowSnapshots([
      { ...baseRows[0], character_description_1: '长发' },
      baseRows[1],
    ]);
    const result = computeStoryboardStaleness({
      members: [
        {
          nodeId: 'n1',
          rowKey: base[0].rowKey,
          prompt: base[0].prompt,
          reference: base[0].reference,
          assetRevision: base[0].assetRevision,
        },
        {
          nodeId: 'n2',
          rowKey: base[1].rowKey,
          prompt: base[1].prompt,
          reference: base[1].reference,
          assetRevision: base[1].assetRevision,
        },
      ],
      rows: changed,
    });

    expect(result.staleNodeIds).toEqual(['n1']);
    expect(result.reasons.get('n1')).toEqual(['asset-revision-changed']);
    expect(result.reasons.has('n2')).toBe(false);
  });

  it('镜号被改（行键对不上）⇒ row-missing', () => {
    const snapshots = buildScriptRowSnapshots([
      { shot_no: '1', shot_prompt: '镜头推近祠堂' },
      { shot_no: '9', shot_prompt: '收尾' },
    ]);
    const result = computeStoryboardStaleness({
      members: [{ nodeId: 'n2', rowKey: 'shot:2', prompt: '收尾', reference: null }],
      rows: snapshots,
    });
    expect(result.staleNodeIds).toEqual(['n2']);
    expect(result.reasons.get('n2')).toEqual(['row-missing']);
  });

  it('有稳定 shot_id 时改展示镜号不算内容变更', () => {
    const base = buildScriptRowSnapshots([
      { shot_id: 'shot_a', shot_no: '1', shot_prompt: '镜头推近祠堂' },
    ]);
    const renamed = buildScriptRowSnapshots([
      { shot_id: 'shot_a', shot_no: '11', shot_prompt: '镜头推近祠堂' },
    ]);

    expect(base[0].rowKey).toBe('shot_a');
    expect(renamed[0].rowKey).toBe('shot_a');
    const result = computeStoryboardStaleness({
      members: [{ nodeId: 'n1', rowKey: 'shot_a', prompt: undefined, reference: undefined }],
      rows: renamed,
    });
    expect(result.staleNodeIds).toEqual([]);
  });

  it('老节点没有快照字段（undefined）⇒ 不因缺快照被误判，但镜号变化仍能发现', () => {
    const snapshots = buildScriptRowSnapshots(rows());
    const unchanged = computeStoryboardStaleness({
      members: [{ nodeId: 'n1', rowKey: 'shot:1', prompt: undefined, reference: undefined }],
      rows: snapshots,
    });
    expect(unchanged.staleNodeIds).toEqual([]);

    const removed = computeStoryboardStaleness({
      members: [{ nodeId: 'n1', rowKey: 'shot:2', prompt: undefined, reference: undefined }],
      rows: [
        // 只剩 shot:1 了
        ...snapshots.filter((row) => row.rowKey === 'shot:1'),
      ],
    });
    expect(removed.staleNodeIds).toEqual(['n1']);
    expect(removed.reasons.get('n1')).toEqual(['row-missing']);
  });

  it('张数与行数不一致 ⇒ rowCountChanged（这种只能回脚本节点重建）', () => {
    const result = computeStoryboardStaleness({
      members: [{ nodeId: 'n1', rowKey: 'shot:1', prompt: 'x', reference: null }],
      rows: buildScriptRowSnapshots(rows()),
    });
    expect(result.rowCountChanged).toBe(true);
  });
});

describe('脚本 → 分镜图 级联失效 · 落盘与重跑', () => {
  beforeEach(() => {
    resetStoryboardSettle();
    seedScriptNode();
  });

  it('固定空间改变使相应首图预览和视频准入失效，重跑采用新地标', () => {
    const source = rows().map((row, index): FreezoneStoryScriptRow => ({ ...row, video_motion_prompt: '观察门口', duration: 5,
      scene_tags: index === 0 ? '祠堂' : '庭院',
      scene_descriptions: index === 0 ? { 祠堂: '香案在入口北侧' } : { 庭院: '石桌位于南侧' },
    }));
    seedScriptNode(source);
    const result = generate(source);
    if (!result.ok || !result.groupId) throw new Error('missing storyboard');
    result.nodeIds.forEach(id => markGenerated(id, `${id}.png`));
    expect(planScriptShotVideos(SCRIPT_NODE_ID).ok).toBe(true);
    const changed = source.map((row, index) => index === 0 ? { ...row, scene_descriptions: { 祠堂: '香案在入口东侧' } } : row);
    useCanvasStore.getState().updateNodeData(SCRIPT_NODE_ID, { scriptResult: { rows: changed } });
    expect(storyboardStalenessForScript(SCRIPT_NODE_ID).staleNodeIds).toEqual([result.nodeIds[0]]);
    expect(scriptPreviewFrames(SCRIPT_NODE_ID, changed, useCanvasStore.getState())[0].url).toBeNull();
    expect(planScriptShotVideos(SCRIPT_NODE_ID)).toMatchObject({ ok: false, reason: expect.stringContaining('分镜已过期') });
    expect(regenerateStoryboardGroupImages(result.groupId)).toMatchObject({ ok: true });
    const refreshed = useCanvasStore.getState().nodes.find(node => node.id === result.nodeIds[0]);
    expect(refreshed?.data.prompt).toContain('香案在入口东侧');
    expect(refreshed?.data.prompt).not.toContain('香案在入口北侧');
    expect(refreshed?.data.scriptCreativeHandoff).toMatchObject({ sceneDescriptions: { 祠堂: '香案在入口东侧' } });
    resetStoryboardSettle();
  });

  it('导演视觉基准贯穿派生、清点、预览、视频准入与组重跑，保留手改正文', () => {
    const source = rows().map(row => ({ ...row, video_motion_prompt: '继续观察门口', duration: 5 }));
    const plan = { visual_bible: { texture: '纸纤维' }, sequences: [{ sequence_id: 'A', shot_nos: [1, 2], turn: '下一段离开' }] };
    useCanvasStore.getState().updateNodeData(SCRIPT_NODE_ID, { scriptResult: { rows: source, director_plan: plan } });
    const result = generate(source);
    if (!result.ok || !result.groupId) throw new Error('missing storyboard');
    result.nodeIds.forEach(id => markGenerated(id, `${id}.png`));
    expect(storyboardStalenessForScript(SCRIPT_NODE_ID).staleNodeIds).toEqual([]);
    expect(scriptPreflightForScript(SCRIPT_NODE_ID).counts.synced).toBe(2);
    expect(scriptPreviewFrames(SCRIPT_NODE_ID, source, useCanvasStore.getState())[0].url).not.toBeNull();
    expect(planScriptShotVideos(SCRIPT_NODE_ID).ok).toBe(true);
    const updatedPlan = { ...plan, visual_bible: { texture: '织物颗粒' } };
    useCanvasStore.getState().updateNodeData(SCRIPT_NODE_ID, { scriptResult: { rows: source, director_plan: updatedPlan } });
    expect(storyboardStalenessForScript(SCRIPT_NODE_ID).staleNodeIds).toEqual(result.nodeIds);
    expect(storyboardStalenessForGroup(result.groupId, SCRIPT_NODE_ID).staleNodeIds).toEqual(result.nodeIds);
    expect(scriptPreflightForScript(SCRIPT_NODE_ID).counts.stale).toBe(2);
    expect(scriptPreviewFrames(SCRIPT_NODE_ID, source, useCanvasStore.getState())[0].url).toBeNull();
    expect(planScriptShotVideos(SCRIPT_NODE_ID)).toMatchObject({ ok: false, reason: expect.stringContaining('分镜已过期') });
    useCanvasStore.getState().updateNodeData(result.nodeIds[0], { prompt: '用户指定的机位与织物颗粒' });
    expect(regenerateStoryboardGroupImages(result.groupId)).toMatchObject({ ok: true, armed: 2 });
    const members = result.nodeIds.map(id => useCanvasStore.getState().nodes.find(node => node.id === id));
    expect(members[0]?.data.prompt).toBe('用户指定的机位与织物颗粒');
    expect(members[1]?.data.prompt).toContain('织物颗粒');
    expect(members[1]?.data.prompt).not.toContain('下一段离开');
    expect(members[0]?.data.scriptCreativeHandoff).toMatchObject({ directorContext: { visualBible: { texture: '织物颗粒' } } });
    expect(storyboardStalenessForScript(SCRIPT_NODE_ID).staleNodeIds).toEqual([]);
    resetStoryboardSettle();
  });

  it('「生成分镜」把行内容快照写在分镜图上', () => {
    const result = generate();
    expect(result.ok).toBe(true);
    const shots = useCanvasStore.getState().nodes.filter(isImageGenNode);
    // 快照就是**最终提示词**（含资产图锚定块）—— 与判过期比的是同一句，不能剥块。
    const snapshots = buildScriptRowSnapshots(rows());
    expect(shots[0].data.scriptRowPrompt).toBe(snapshots[0].prompt);
    expect(String(shots[0].data.scriptRowPrompt)).toContain('资产图锚定：');
    expect(shots[0].data.scriptRowReference).toBe('a.png');
    expect(String(shots[0].data.scriptRowAssetSnapshot)).toContain('character:阿雀@1@');
    expect(shots[1].data.scriptRowPrompt).toBe(snapshots[1].prompt);
    expect(shots[1].data.scriptRowReference).toBeNull();
    expect(shots[1].data.scriptRowAssetSnapshot).toBeNull();
    // 生成完当场判定：没有任何一张过期。
    expect(storyboardStalenessForScript(SCRIPT_NODE_ID).staleNodeIds).toEqual([]);
  });

  it('改了脚本行的提示词后，脚本节点与分镜组都能判出过期', () => {
    const result = generate();
    if (!result.ok) return;
    const shots = useCanvasStore.getState().nodes.filter(isImageGenNode);
    shots.forEach((shot) => markGenerated(shot.id, `${shot.id}.png`));

    editRow(1, 'visual_description', '收尾（导演改过）');

    const scriptStaleness = storyboardStalenessForScript(SCRIPT_NODE_ID);
    expect(scriptStaleness.staleNodeIds).toEqual([shots[1].id]);

    const groupStaleness = storyboardStalenessForGroup(result.groupId as string, SCRIPT_NODE_ID);
    expect(groupStaleness.staleNodeIds).toEqual([shots[1].id]);
  });

  it('单镜脚本没有分镜组时，仍能判出过期并把该镜列入重跑对象', () => {
    const singleRow = rows().slice(0, 1);
    seedScriptNode(singleRow);
    const result = generate(singleRow);
    if (!result.ok) return;
    expect(result.groupId).toBeNull();

    const shots = useCanvasStore.getState().nodes.filter(isImageGenNode);
    expect(shots).toHaveLength(1);
    markGenerated(shots[0].id, 'single.png');

    const nextRows = editRow(0, 'shot_prompt', '镜头推近祠堂（改过）', singleRow);
    expect(storyboardStalenessForScript(SCRIPT_NODE_ID).staleNodeIds).toEqual([shots[0].id]);
    expect(storyboardMemberIdsToRearm(shots.map((shot) => shot.id), nextRows)).toEqual([
      shots[0].id,
    ]);
  });

  it('重跑对象＝未出图 + 已失效：出过图但脚本改过的那张也要重跑', () => {
    generate();
    const shots = useCanvasStore.getState().nodes.filter(isImageGenNode);
    // 两张都已出图 —— 按旧逻辑「重新生成」会一张都不动。
    shots.forEach((shot) => markGenerated(shot.id, `${shot.id}.png`));
    expect(storyboardMemberIdsToRearm(shots.map((shot) => shot.id), rows())).toEqual([]);

    const nextRows = editRow(0, 'shot_prompt', '镜头猛推祠堂（改）');
    expect(storyboardMemberIdsToRearm(shots.map((shot) => shot.id), nextRows)).toEqual([
      shots[0].id,
    ]);
  });

  it('重新生成后过期标记清空，且快照对齐到当前脚本行', () => {
    const result = generate();
    if (!result.ok) return;
    const groupId = result.groupId as string;
    const shots = useCanvasStore.getState().nodes.filter(isImageGenNode);
    shots.forEach((shot) => markGenerated(shot.id, `${shot.id}.png`));

    editRow(0, 'shot_prompt', '镜头猛推祠堂（改）');
    expect(storyboardStalenessForScript(SCRIPT_NODE_ID).staleNodeIds).toEqual([shots[0].id]);

    const regen = regenerateStoryboardGroupImages(groupId);
    expect(regen.ok).toBe(true);
    expect(regen.armed).toBe(1);
    expect(regen.stale).toBe(1);

    // 快照已对齐 ⇒ 不再判过期（否则用户会看到「点了没用」）。
    expect(storyboardStalenessForScript(SCRIPT_NODE_ID).staleNodeIds).toEqual([]);
    const reloaded = useCanvasStore.getState().nodes.find((node) => node.id === shots[0].id);
    expect(reloaded?.data.scriptRowPrompt).toContain(
      '\n\n镜头猛推祠堂（改）',
    );
    // 被重新排队的那张带着出图标记，没被碰的那张不带。
    expect(reloaded?.data.canvas_auto_generate_once).toBe(true);
    const untouched = useCanvasStore.getState().nodes.find((node) => node.id === shots[1].id);
    expect(untouched?.data.canvas_auto_generate_once).toBeFalsy();
  });

  it('分组即使散开（成员没有 parentId），按 id 传进来的判定仍然成立', () => {
    const result = generate();
    if (!result.ok) return;
    const groupId = result.groupId as string;
    const memberIds = storyboardGroupMembers(groupId).map((member) => member.id);
    useCanvasStore.getState().ungroupNode(groupId);
    // 散开后 storyboardGroupMembers 找不到它们了（这正是出图期间的中间态）。
    expect(storyboardGroupMembers(groupId)).toHaveLength(0);
    // 但重跑路径自己记着 memberIds，判定不该因此失效。
    const nextRows = editRow(0, 'shot_prompt', '改了');
    expect(storyboardMemberIdsToRearm(memberIds, nextRows)).toContain(memberIds[0]);
  });

  it('脚本换了角色图 → 重跑时节点的参考图组跟着换（否则仍拿旧角色图出图）', () => {
    const result = generate();
    if (!result.ok) return;
    const shots = useCanvasStore.getState().nodes.filter(isImageGenNode);
    shots.forEach((shot) => markGenerated(shot.id, `${shot.id}.png`));

    // editRow 直接改画布上的脚本节点数据；重跑路径从那里读当前行。
    editRow(0, 'character_image_1', 'a2.png');
    const regen = regenerateStoryboardGroupImages(result.groupId as string);
    expect(regen.ok).toBe(true);
    expect(regen.armed).toBe(1);

    const reloaded = useCanvasStore.getState().nodes.find((node) => node.id === shots[0].id);
    expect(reloaded?.data.referenceImageUrl).toBe('a2.png');
    expect(reloaded?.data.referenceImageUrls).toEqual(['a2.png']);
    expect(reloaded?.data.scriptRowReference).toBe('a2.png');
  });

  it('用户手传的参考图不被重跑顶掉（此时节点参考图已不归脚本所有）', () => {
    const result = generate();
    if (!result.ok) return;
    const shots = useCanvasStore.getState().nodes.filter(isImageGenNode);
    shots.forEach((shot) => markGenerated(shot.id, `${shot.id}.png`));

    // 用户在第 1 镜上手传了一张参考图（走 ImageGenNode 的上传路径）。
    useCanvasStore.getState().updateNodeData(shots[0].id, {
      referenceImageUrl: 'manual.png',
      referenceImageUrls: ['manual.png'],
    });
    // 只改提示词，脚本行的参考图没动。
    editRow(0, 'shot_prompt', '镜头改过');

    const regen = regenerateStoryboardGroupImages(result.groupId as string);
    expect(regen.ok).toBe(true);
    const reloaded = useCanvasStore.getState().nodes.find((node) => node.id === shots[0].id);
    expect(reloaded?.data.referenceImageUrls).toEqual(['manual.png']);
    expect(reloaded?.data.referenceImageUrl).toBe('manual.png');
    // 快照仍对齐当前行，否则过期标记会一直亮着。
    expect(reloaded?.data.scriptRowPrompt).toContain(
      '\n\n镜头改过',
    );
  });
});
