// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { beforeEach, describe, expect, it } from 'vitest';

import type { FreezoneStoryScriptRow } from '@/api/ops';
import { CANVAS_NODE_TYPES, isImageGenNode } from '@/features/canvas/domain/canvasNodes';
import { generateScriptStoryboard } from '@/features/canvas/nodes/script/generateScriptStoryboard';
import {
  computeScriptPreflight,
  describeShotDefects,
  describeShotState,
  formatShotNumbers,
  scriptPreflightForScript,
  type ScriptPreflightMember,
} from '@/features/canvas/nodes/script/scriptPreflight';
import { resetStoryboardSettle } from '@/features/canvas/nodes/script/storyboardSettle';
import { buildScriptRowSnapshots } from '@/features/canvas/nodes/script/scriptStaleness';
import { useCanvasStore } from '@/stores/canvasStore';

/**
 * 开拍前逐镜清点。
 *
 * 这一层要证明的是「开拍前能一次看清每一镜能不能出、缺什么」：
 * - 四态（missing / pending / stale / synced）按行判，不是按张数；
 * - 「缺图片提示词」是硬阻塞（会拦住出图），「角色没图」是软降级（照出但少参考）；
 * - 价格与「会出几张」用的是同一个数，缺提示词的镜不会被算进去。
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
    { shot_no: '2', visual_description: '收尾', shot_prompt: '拉远收尾' },
  ];
}

function snapshotOf(rowKey: string, prompt: string, reference: string | null = null) {
  return { nodeId: `n-${rowKey}`, rowKey, prompt, reference };
}

function member(
  rowKey: string,
  options: {
    prompt?: string | null;
    reference?: string | null;
    hasImage?: boolean;
    hasError?: boolean;
  } = {},
): ScriptPreflightMember {
  const prompt = options.prompt ?? 'x';
  const reference = options.reference ?? null;
  return {
    nodeId: `n-${rowKey}`,
    rowKey,
    snapshot: snapshotOf(rowKey, prompt, reference),
    hasImage: options.hasImage ?? false,
    hasError: options.hasError ?? false,
  };
}

describe('开拍前逐镜清点 · 纯函数', () => {
  it('没有派生节点时每一镜都是 missing，且能照常指出缺什么', () => {
    const result = computeScriptPreflight({ members: [], rows: rows() });
    expect(result.entries.map((entry) => entry.state)).toEqual(['missing', 'missing']);
    expect(result.counts.missing).toBe(2);
    // 没有分镜组时「会出几张」就是全部行 —— 首次生成前正是要这个数。
    expect(result.generatableShotCount).toBe(2);
    expect(result.hasBlockers).toBe(false);
  });

  it('四态按行判：已出图且一致 = synced，出过图但脚本改过 = stale', () => {
    const rowList = rows();
    const snapshots = buildScriptRowSnapshots(rowList);
    const result = computeScriptPreflight({
      members: [
        member('shot:1', {
          prompt: snapshots[0].prompt,
          reference: snapshots[0].reference,
          hasImage: true,
        }),
        // 第 2 镜留着**旧**快照（脚本改过之后没重跑）。
        member('shot:2', { prompt: '拉远收尾（旧）', hasImage: true }),
      ],
      rows: rowList,
    });
    expect(result.entries[0].state).toBe('synced');
    expect(result.entries[1].state).toBe('stale');
    expect(result.entries[1].reasons).toEqual(['prompt-changed']);
    expect(result.staleShotNumbers).toEqual(['2']);
    // synced 的不重出，stale 的要重出。
    expect(result.generatableShotCount).toBe(1);
  });

  it('有节点但还没有自己的图 = pending（不是 stale —— 它本来就还没出）', () => {
    const rowList = rows();
    const snapshots = buildScriptRowSnapshots(rowList);
    const result = computeScriptPreflight({
      members: [
        member('shot:1', { prompt: snapshots[0].prompt, reference: snapshots[0].reference }),
        member('shot:2', { prompt: snapshots[1].prompt, hasImage: true }),
      ],
      rows: rowList,
    });
    // 第 1 镜快照完全一致，但没出图 —— 判 pending 而不是 synced，否则它会不进重跑队列。
    expect(result.entries[0].state).toBe('pending');
    expect(result.entries[1].state).toBe('synced');
    expect(result.pendingShotNumbers).toEqual(['1']);
    expect(result.counts).toEqual({ missing: 0, pending: 1, stale: 0, synced: 1 });
  });

  it('上次重跑失败的那一张仍然算 pending：否则点数少报一张', () => {
    const rowList = rows();
    const snapshots = buildScriptRowSnapshots(rowList);
    const result = computeScriptPreflight({
      members: [
        member('shot:1', {
          prompt: snapshots[0].prompt,
          reference: snapshots[0].reference,
          hasImage: true,
          hasError: true,
        }),
        member('shot:2', { prompt: snapshots[1].prompt, hasImage: true }),
      ],
      rows: rowList,
    });
    // 与 `storyboardMemberIdsToRearm` 同口径：它会进重跑队列，所以清点也必须算进去。
    expect(result.entries[0].state).toBe('pending');
    expect(result.generatableShotCount).toBe(1);
  });

  it('脚本改过又重跑失败：报 stale（横幅门槛）而不是 pending', () => {
    const rowList = rows().slice(0, 1);
    const result = computeScriptPreflight({
      members: [member('shot:1', { prompt: '改之前的旧提示词', hasImage: true, hasError: true })],
      rows: rowList,
    });
    expect(result.entries[0].state).toBe('stale');
    // 两条路都算重出，张数不受影响。
    expect(result.generatableShotCount).toBe(1);
  });

  it('缺图片提示词是硬阻塞：不计入「会出几张」，镜号单列出来', () => {
    const rowList: FreezoneStoryScriptRow[] = [
      { shot_no: '1', shot_prompt: '推近' },
      // 画面描述与分镜提示词都空 —— 派生出来的图片节点没有可提交的内容。
      { shot_no: '2', visual_description: '', shot_prompt: '' },
      { shot_no: '3', visual_description: '回落用画面描述' },
    ];
    const result = computeScriptPreflight({ members: [], rows: rowList });
    expect(result.blockedShotNumbers).toEqual(['2']);
    expect(result.hasBlockers).toBe(true);
    expect(result.entries[1].blocked).toBe(true);
    expect(result.entries[1].defects).toEqual(['image-prompt-missing']);
    // 第 3 镜只有画面描述 —— 不算缺（rowImagePrompt 会回落）。
    expect(result.entries[2].defects).toEqual([]);
    expect(result.generatableShotCount).toBe(2);
  });

  it('角色没图是软降级：照常出图，只是单列出来提醒', () => {
    const rowList: FreezoneStoryScriptRow[] = [
      { shot_no: '1', shot_prompt: '推近', character_1: '阿雀' },
      { shot_no: '2', shot_prompt: '拉远', character_1: '老树', character_image_1: 'tree.png' },
    ];
    const result = computeScriptPreflight({ members: [], rows: rowList });
    expect(result.degradedShotNumbers).toEqual(['1']);
    expect(result.blockedShotNumbers).toEqual([]);
    // 降级不拦出图：两镜都算得进去。
    expect(result.generatableShotCount).toBe(2);
  });

  it('byRowKey 让表格按行键 O(1) 取标记', () => {
    const result = computeScriptPreflight({ members: [], rows: rows() });
    expect(result.byRowKey.get('shot:1')?.shotNumber).toBe('1');
    expect(result.byRowKey.get('shot:9')).toBeUndefined();
  });

  it('镜号列表与人话文案', () => {
    expect(formatShotNumbers([])).toBe('');
    expect(formatShotNumbers(['1'])).toBe('第 1 镜');
    expect(formatShotNumbers(['1', '2', '3'])).toBe('第 1 镜、第 2 镜、第 3 镜');
    expect(formatShotNumbers(['1', '2', '3', '4', '5'])).toBe('第 1 镜、第 2 镜、第 3 镜、第 4 镜 等 5 镜');
    expect(formatShotNumbers(['1', '2', '3', '4', '5'], 2)).toBe('第 1 镜、第 2 镜 等 5 镜');

    expect(describeShotDefects(['image-prompt-missing'])).toContain('缺图片提示词');
    expect(describeShotDefects(['character-image-missing'])).toContain('角色没有角色图');
    expect(describeShotDefects([])).toBe('');

    expect(describeShotState('missing')).toBe('还没有分镜图');
    expect(describeShotState('pending')).toBe('还没出图');
    expect(describeShotState('stale')).toBe('脚本已变更，图需重出');
    expect(describeShotState('synced')).toBe('已出图且与脚本一致');
  });
});

describe('开拍前逐镜清点 · 接画布', () => {
  beforeEach(() => {
    resetStoryboardSettle();
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
  });

  it('生成分镜之后：没出图的判 pending，出好图的判 synced', () => {
    const result = generateScriptStoryboard({
      scriptNodeId: SCRIPT_NODE_ID,
      rows: rows(),
      scriptTitle: '天空之跃',
      scriptSize: SCRIPT_SIZE,
      config: { model: MODEL, aspectRatio: '16:9' },
      generateImages: false,
    });
    expect(result.ok).toBe(true);

    const afterCreate = scriptPreflightForScript(SCRIPT_NODE_ID);
    expect(afterCreate.entries.map((entry) => entry.state)).toEqual(['pending', 'pending']);

    const shots = useCanvasStore.getState().nodes.filter(isImageGenNode);
    shots.forEach((shot) => useCanvasStore.getState().updateNodeData(shot.id, { imageUrl: 'x.png' }));
    expect(scriptPreflightForScript(SCRIPT_NODE_ID).entries.map((e) => e.state)).toEqual([
      'synced',
      'synced',
    ]);
  });

  it('单镜脚本没有分镜组时，仍按血缘边识别已派生节点并同步追踪状态', () => {
    const singleRow = rows().slice(0, 1);
    useCanvasStore.getState().updateNodeData(SCRIPT_NODE_ID, {
      scriptResult: { title: '天空之跃', rows: singleRow },
    });
    const result = generateScriptStoryboard({
      scriptNodeId: SCRIPT_NODE_ID,
      rows: singleRow,
      scriptTitle: '天空之跃',
      scriptSize: SCRIPT_SIZE,
      config: { model: MODEL, aspectRatio: '16:9' },
      generateImages: false,
    });
    if (!result.ok) throw new Error(result.reason);
    expect(result.groupId).toBeNull();

    const shots = useCanvasStore.getState().nodes.filter(isImageGenNode);
    expect(shots).toHaveLength(1);
    expect(scriptPreflightForScript(SCRIPT_NODE_ID).entries).toHaveLength(1);
    expect(scriptPreflightForScript(SCRIPT_NODE_ID).entries[0].state).toBe('pending');

    useCanvasStore.getState().updateNodeData(shots[0].id, { imageUrl: 'single.png' });
    expect(scriptPreflightForScript(SCRIPT_NODE_ID).entries[0].state).toBe('synced');

    useCanvasStore.getState().updateNodeData(SCRIPT_NODE_ID, {
      scriptResult: {
        title: '天空之跃',
        rows: [{ ...singleRow[0], shot_prompt: '镜头推近祠堂（改过）' }],
      },
    });
    expect(scriptPreflightForScript(SCRIPT_NODE_ID).entries[0].state).toBe('stale');
  });

  it('改了脚本行之后：只有那一镜判 stale，横幅能说出是第几镜', () => {
    generateScriptStoryboard({
      scriptNodeId: SCRIPT_NODE_ID,
      rows: rows(),
      scriptTitle: '天空之跃',
      scriptSize: SCRIPT_SIZE,
      config: { model: MODEL, aspectRatio: '16:9' },
      generateImages: false,
    });
    const shots = useCanvasStore.getState().nodes.filter(isImageGenNode);
    shots.forEach((shot) => useCanvasStore.getState().updateNodeData(shot.id, { imageUrl: 'x.png' }));

    useCanvasStore.getState().updateNodeData(SCRIPT_NODE_ID, {
      scriptResult: {
        title: '天空之跃',
        rows: [
          rows()[0],
          { ...rows()[1], visual_description: '收尾（导演改过）', shot_prompt: '拉远收尾（改过）' },
        ],
      },
    });

    const preflight = scriptPreflightForScript(SCRIPT_NODE_ID);
    expect(preflight.staleShotNumbers).toEqual(['2']);
    // 报的是镜号而不是节点 id —— 这正是「横幅说清第几镜」缺的那一半。
    expect(formatShotNumbers(preflight.staleShotNumbers)).toBe('第 2 镜');
    // 没被改过的第 1 镜不跟着重出。
    expect(preflight.generatableShotCount).toBe(1);
  });
});
