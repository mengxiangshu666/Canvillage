// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from 'vitest';

import type { FreezoneStoryScriptRow } from '@/api/ops';
import { CANVAS_NODE_TYPES } from '@/features/canvas/domain/canvasNodes';
import {
  copyScriptShotRow,
  createScriptShotRow,
  deleteScriptShotRowAt,
  duplicateScriptShotRowAt,
  ensureScriptShotIdentities,
  insertScriptShotRowAfter,
  moveScriptShotRow,
  resequenceScriptShotRowsAfterEdit,
  resequenceScriptShotRows,
} from '@/features/canvas/domain/scriptShotIdentity';
import {
  buildScriptRowKeys,
  scriptRowKey,
  scriptRowShotNumber,
} from '@/features/canvas/nodes/script/scriptViews';
import { useCanvasStore } from '@/stores/canvasStore';

describe('稳定镜头身份', () => {
  it('旧行确定性迁移，刷新不会生成新的身份', () => {
    const rows: FreezoneStoryScriptRow[] = [
      { shot_no: '1', visual_description: '开场' },
      { shot_no: '2', visual_description: '收尾' },
    ];
    const first = ensureScriptShotIdentities(rows);
    const second = ensureScriptShotIdentities(first.rows);

    expect(first.changed).toBe(true);
    expect(first.rows.map((row) => row.shot_id)).toEqual(['shot:1', 'shot:2']);
    expect(second.changed).toBe(false);
    expect(second.rows.map((row) => row.shot_id)).toEqual(['shot:1', 'shot:2']);
    expect(second.rows.map((row) => row.shot_order)).toEqual([1, 2]);
    expect(second.rows.map((row) => row.display_shot_no)).toEqual(['1', '2']);
  });

  it('改展示镜号不改变 shot_id，显式身份优先于镜号', () => {
    const rows: FreezoneStoryScriptRow[] = [
      {
        shot_id: 'shot_stable_a',
        shot_order: 1,
        display_shot_no: '1',
        shot_no: '1',
        visual_description: '开场',
      },
    ];
    const changed = [{ ...rows[0], shot_no: '11', display_shot_no: '11' }];

    expect(scriptRowKey(changed[0], 0)).toBe('shot_stable_a');
    expect(buildScriptRowKeys(changed)).toEqual(['shot_stable_a']);
    expect(scriptRowShotNumber(changed[0], 0)).toBe('11');
  });

  it('复制生成新身份，重排只改片序', () => {
    const original = createScriptShotRow({ shot_no: '1' }, 1);
    const copy = copyScriptShotRow(original, 2);
    const reordered = resequenceScriptShotRows([copy, original]);

    expect(copy.shot_id).not.toBe(original.shot_id);
    expect(reordered.map((row) => row.shot_id)).toEqual([copy.shot_id, original.shot_id]);
    expect(reordered.map((row) => row.shot_order)).toEqual([1, 2]);
  });

  it('插行 / 删行后既有身份不变，自动编号跟随片序', () => {
    const seeded = ensureScriptShotIdentities([
      { shot_no: '1', visual_description: '开场' },
      { shot_no: '2', visual_description: '收尾' },
    ]).rows;
    const inserted = createScriptShotRow({ visual_description: '中段' }, 2);
    const afterInsert = resequenceScriptShotRowsAfterEdit(seeded, [
      seeded[0],
      inserted,
      seeded[1],
    ]);

    // 既有两行身份原样保留；插进来的行拿到的是新身份。
    expect(afterInsert.map((row) => row.shot_id)).toEqual([
      seeded[0].shot_id,
      inserted.shot_id,
      seeded[1].shot_id,
    ]);
    // 自动编号（原来是 1 / 2）跟着新片序重排；新行的编号也落到它的片序上。
    expect(afterInsert.map((row) => row.shot_order)).toEqual([1, 2, 3]);
    expect(afterInsert.map((row) => row.display_shot_no)).toEqual(['1', '2', '3']);
    expect(afterInsert.map((row) => row.shot_no)).toEqual(['1', '2', '3']);

    // 删掉中间一行：身份不变，剩下的自动编号回到 1 / 2。
    const afterDelete = resequenceScriptShotRowsAfterEdit(afterInsert, [
      afterInsert[0],
      afterInsert[2],
    ]);
    expect(afterDelete.map((row) => row.shot_id)).toEqual([
      seeded[0].shot_id,
      seeded[1].shot_id,
    ]);
    expect(afterDelete.map((row) => row.display_shot_no)).toEqual(['1', '2']);
  });

  it('用户 / 模型写过的镜号不被结构编辑覆盖', () => {
    const seeded = ensureScriptShotIdentities([
      { shot_no: '1A', visual_description: '开场' },
      { shot_no: '2', visual_description: '收尾' },
    ]).rows;
    const inserted = createScriptShotRow({ visual_description: '中段' }, 2);
    const afterInsert = resequenceScriptShotRowsAfterEdit(seeded, [
      seeded[0],
      inserted,
      seeded[1],
    ]);

    // '1A' 不等于旧片序 '1'，视为人工编号，保留原样。
    expect(afterInsert.map((row) => row.display_shot_no)).toEqual(['1A', '2', '3']);
    expect(afterInsert.map((row) => row.shot_order)).toEqual([1, 2, 3]);
  });

  it('行操作原语：插 / 复制 / 删 / 重排都不破坏既有身份', () => {
    const seeded = ensureScriptShotIdentities([
      { shot_no: '1', visual_description: '开场' },
      { shot_no: '2', visual_description: '收尾' },
    ]).rows;
    const [firstId, secondId] = seeded.map((row) => row.shot_id);

    const inserted = insertScriptShotRowAfter(seeded, 0);
    expect(inserted.map((row) => row.shot_id)).toEqual([
      firstId,
      inserted[1].shot_id,
      secondId,
    ]);
    expect(inserted[1].shot_id).not.toBe(firstId);
    expect(inserted[1].shot_id).not.toBe(secondId);

    const duplicated = duplicateScriptShotRowAt(seeded, 0);
    expect(duplicated.map((row) => row.shot_id)).toEqual([
      firstId,
      duplicated[1].shot_id,
      secondId,
    ]);
    expect(duplicated[1].shot_id).not.toBe(firstId);
    expect(duplicated[1].shot_id).not.toBe(secondId);

    const deleted = deleteScriptShotRowAt(seeded, 0);
    expect(deleted.map((row) => row.shot_id)).toEqual([secondId]);
    expect(deleted.map((row) => row.shot_order)).toEqual([1]);

    const moved = moveScriptShotRow(seeded, 1, -1);
    expect(moved.map((row) => row.shot_id)).toEqual([secondId, firstId]);
    expect(moved.map((row) => row.shot_order)).toEqual([1, 2]);

    // 越界移动与「删到空」都被挡住，身份一个都不丢。
    expect(moveScriptShotRow(seeded, 0, -1).map((row) => row.shot_id)).toEqual([
      firstId,
      secondId,
    ]);
    expect(deleteScriptShotRowAt([seeded[0]], 0).map((row) => row.shot_id)).toEqual([
      firstId,
    ]);
  });

  it('旧画布载入时写入确定性身份，重复载入不漂移', () => {
    const node = {
      id: 'script-node',
      type: CANVAS_NODE_TYPES.script,
      position: { x: 0, y: 0 },
      data: {
        scriptResult: {
          title: '旧脚本',
          rows: [{ shot_no: '1' }, { shot_no: '2' }],
        },
      },
    } as never;

    useCanvasStore.getState().setCanvasData([node], []);
    const first = (
      useCanvasStore.getState().nodes[0].data.scriptResult as {
        rows: FreezoneStoryScriptRow[];
      }
    ).rows.map((row) => row.shot_id);

    useCanvasStore.getState().setCanvasData([node], []);
    const second = (
      useCanvasStore.getState().nodes[0].data.scriptResult as {
        rows: FreezoneStoryScriptRow[];
      }
    ).rows.map((row) => row.shot_id);

    expect(first).toEqual(['shot:1', 'shot:2']);
    expect(second).toEqual(first);
  });
});
