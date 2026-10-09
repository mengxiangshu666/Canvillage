// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from 'vitest';

import { CANVAS_NODE_TYPES, SCRIPT_NODE_SIZE, resolveKnownNodeEdge } from '@/features/canvas/domain/canvasNodes';
import {
  resolveScriptNodeDefaultSize,
  resolveScriptShellFallbackSize,
  resolveScriptNodeBox,
  scriptDataHasRows,
} from '@/features/canvas/nodes/script/scriptNodeLayout';
import { resolveCanvasFallbackNodeSize, useCanvasStore } from '@/stores/canvasStore';

/**
 * 脚本节点尺寸的若干声明点必须一致，而且**默认档必须可达**。
 *
 * 这个节点此前把尺寸写了三遍且互不相同：节点本体 480×320 / 800×400、LOD 外壳
 * 480×320、而 `canvasStore` 的 `FALLBACK_NODE_SIZES` 里**根本没有 scriptNode 条目**
 * （未测量时按 320×200 估算，被 groupNodes / fitGroupToChildren / 依赖落位读走）。
 *
 * 2026-09-13 真机又查出第二层：外壳那一档会**被测量并固化**。实测时间线
 * （无 width、8 行表格的节点）：
 *     props:undefined → 外壳 480×320 → props:480 → 真节点 480×320
 * 外壳是第一个被 ResizeObserver 量到的 DOM，量到的值进 `measured`，而
 * `getNodeDimensions` 里 measured 优先于 width ⇒ 800 档永远不可达。
 * 所以外壳必须和节点本体读**同一个**「按内容取档」的函数，下面钉住这一点。
 */

describe('脚本节点尺寸口径', () => {
  it('空态与出表后各一档，且下限小于空态', () => {
    expect(SCRIPT_NODE_SIZE.empty).toEqual({ width: 480, height: 320 });
    expect(SCRIPT_NODE_SIZE.withResult).toEqual({ width: 800, height: 400 });
    expect(SCRIPT_NODE_SIZE.min.width).toBeLessThan(SCRIPT_NODE_SIZE.empty.width);
    expect(SCRIPT_NODE_SIZE.min.height).toBeLessThan(SCRIPT_NODE_SIZE.empty.height);
    // 上限必须大于出表后那一档，否则 resize 把手会在默认尺寸上就顶住。
    expect(SCRIPT_NODE_SIZE.max.width).toBeGreaterThan(SCRIPT_NODE_SIZE.withResult.width);
    expect(SCRIPT_NODE_SIZE.max.height).toBeGreaterThan(SCRIPT_NODE_SIZE.withResult.height);
  });

  it('未测量时按「有没有表格」取档，与共享常量逐字段相等', () => {
    const empty = resolveScriptNodeBox({ width: null, height: null, hasResult: false });
    expect(empty).toEqual(SCRIPT_NODE_SIZE.empty);

    const withResult = resolveScriptNodeBox({ width: null, height: null, hasResult: true });
    expect(withResult).toEqual(SCRIPT_NODE_SIZE.withResult);
  });

  it('React Flow 传进来的 0 要当「没量到」——默认档在此之前永远不可达', () => {
    // 回归钉（真机实测的口径）：`getNodeDimensions` 对没有 measured/width/initialWidth
    // 的节点返回数字 0：
    //    width: node.measured?.width ?? node.width ?? node.initialWidth ?? 0
    // 此前守卫写的是 `typeof params.width === 'number'`，0 也是 number ⇒ 收下 0 ⇒
    // `Math.max(360, 0)` = 360。结果是 480 / 800 两档都没生效，节点被钉在 360×240。
    expect(resolveScriptNodeBox({ width: 0, height: 0, hasResult: false })).toEqual(
      SCRIPT_NODE_SIZE.empty,
    );
    expect(resolveScriptNodeBox({ width: 0, height: 0, hasResult: true })).toEqual(
      SCRIPT_NODE_SIZE.withResult,
    );
    // 半个 0（宽是真值、高是 0）也要各自判定，不能整对一起丢。
    expect(resolveScriptNodeBox({ width: 0, height: 500, hasResult: true })).toEqual({
      width: SCRIPT_NODE_SIZE.withResult.width,
      height: 500,
    });
  });

  it('哨兵值判定只认「有限且大于 0」，其余一律当没量到', () => {
    expect(resolveKnownNodeEdge(0)).toBeNull();
    expect(resolveKnownNodeEdge(-1)).toBeNull();
    expect(resolveKnownNodeEdge(Number.NaN)).toBeNull();
    expect(resolveKnownNodeEdge(Number.POSITIVE_INFINITY)).toBeNull();
    expect(resolveKnownNodeEdge(null)).toBeNull();
    expect(resolveKnownNodeEdge(undefined)).toBeNull();
    expect(resolveKnownNodeEdge(1)).toBe(1);
    expect(resolveKnownNodeEdge(480.4)).toBe(480.4);
  });

  it('已测量的尺寸原样透传（只抬下限，不夹上限）', () => {
    expect(resolveScriptNodeBox({ width: 900, height: 500, hasResult: true })).toEqual({
      width: 900,
      height: 500,
    });
    // 上限刻意不在这里夹：React Flow 记的 node.width 才是权威，再夹一次会分叉。
    expect(
      resolveScriptNodeBox({ width: 5000, height: 5000, hasResult: true }),
    ).toEqual({ width: 5000, height: 5000 });
    // 下限要抬。
    expect(resolveScriptNodeBox({ width: 10, height: 10, hasResult: false })).toEqual({
      width: SCRIPT_NODE_SIZE.min.width,
      height: SCRIPT_NODE_SIZE.min.height,
    });
  });

  it('canvasStore 的 fallback 表能拿到脚本节点这一档 —— 未测量时不再低估成 320×200', () => {
    // 读真实的读取路径（`getNodeSize` 用的就是这个函数），而不是复述常量值：
    // 表里没有 scriptNode 条目时这里返回 undefined，而 undefined 会落到
    // DEFAULT_NODE_WIDTH / 200 那个兜底 —— 正是分组边界算小的原因。
    expect(CANVAS_NODE_TYPES.script).toBe('scriptNode');
    expect(resolveCanvasFallbackNodeSize(CANVAS_NODE_TYPES.script)).toEqual(
      SCRIPT_NODE_SIZE.empty,
    );
  });

  it('LOD 外壳按内容取档，与节点本体同一档', () => {
    const noRows = { scriptResult: { rows: [] } };
    const withRows = { scriptResult: { rows: [{ shot_no: 1 }] } };
    expect(resolveScriptShellFallbackSize(noRows)).toEqual(SCRIPT_NODE_SIZE.empty);
    expect(resolveScriptShellFallbackSize(withRows)).toEqual(SCRIPT_NODE_SIZE.withResult);
    // 与节点本体同源：同输入必须同输出。
    expect(resolveScriptShellFallbackSize(withRows)).toEqual(
      resolveScriptNodeDefaultSize(true),
    );
    expect(resolveScriptShellFallbackSize(null)).toEqual(SCRIPT_NODE_SIZE.empty);
    expect(resolveScriptShellFallbackSize({})).toEqual(SCRIPT_NODE_SIZE.empty);
    // 形状不对（不是数组）也算「没有表」，不能抛。
    expect(resolveScriptShellFallbackSize({ scriptResult: { rows: 'nope' } })).toEqual(
      SCRIPT_NODE_SIZE.empty,
    );
  });

  it('scriptDataHasRows 只认「数组且非空」', () => {
    expect(scriptDataHasRows({ scriptResult: { rows: [] } })).toBe(false);
    expect(scriptDataHasRows({ scriptResult: { rows: [{}] } })).toBe(true);
    expect(scriptDataHasRows({ scriptResult: null })).toBe(false);
    expect(scriptDataHasRows(null)).toBe(false);
    expect(scriptDataHasRows(undefined)).toBe(false);
    expect(scriptDataHasRows('scriptNode')).toBe(false);
  });
});

/**
 * `measured` 是 react-flow 的 DOM 测量缓存，会随画布一起落盘。脚本节点在 2026-09
 * 之前被旧 LOD 外壳的 0 宽渲染量成了 min 档（360×240），于是「默认档」再没出现过。
 * 下面钉住重新载入时的清理条件 —— 只在「事故特征」齐全时清，不碰用户意图。
 */
describe('脚本节点历史 measured 的清理（载入期）', () => {
  const rows = (n: number) => Array.from({ length: n }, (_, i) => ({ shot_no: i + 1 }));

  function hydrate(node: Record<string, unknown>) {
    useCanvasStore.getState().setCanvasData([{ position: { x: 0, y: 0 }, data: {}, ...node } as never], []);
    return useCanvasStore.getState().nodes[0];
  }

  it('8 行表格 + measured 360×240（事故产物）⇒ 丢掉 measured，让 React Flow 重新量', () => {
    const node = hydrate({
      id: 'stale',
      type: CANVAS_NODE_TYPES.script,
      measured: { width: 360, height: 240 },
      data: { scriptResult: { rows: rows(8) } },
    });
    expect(node).toBeTruthy();
    expect(node.measured).toBeUndefined();
    // 其余字段不能被动过
    expect(node.id).toBe('stale');
    expect((node.data as { scriptResult?: { rows?: unknown[] } }).scriptResult?.rows).toHaveLength(8);
  });

  it('用户亲手拉过的框不许动（isSizeManuallyAdjusted）', () => {
    const node = hydrate({
      id: 'manual',
      type: CANVAS_NODE_TYPES.script,
      measured: { width: 360, height: 240 },
      data: { scriptResult: { rows: rows(8) }, isSizeManuallyAdjusted: true },
    });
    expect(node.measured).toEqual({ width: 360, height: 240 });
  });

  it('有显式 width/height 的节点不许动（尺寸由它说了算）', () => {
    const node = hydrate({
      id: 'explicit',
      type: CANVAS_NODE_TYPES.script,
      width: 500,
      height: 300,
      measured: { width: 360, height: 240 },
      data: { scriptResult: { rows: rows(8) } },
    });
    expect(node.measured).toEqual({ width: 360, height: 240 });
    expect(node.width).toBe(500);
  });

  it('measured 与内容相符（8 行表格 = 800 档）时不动它', () => {
    const node = hydrate({
      id: 'match',
      type: CANVAS_NODE_TYPES.script,
      measured: { width: SCRIPT_NODE_SIZE.withResult.width, height: 612 },
      data: { scriptResult: { rows: rows(8) } },
    });
    expect(node.measured?.width).toBe(SCRIPT_NODE_SIZE.withResult.width);
    // 高度会随行数长，不是事故特征 ⇒ 只比宽度这一维
    expect(node.measured?.height).toBe(612);
  });

  it('空态节点量成 480 档时也不动它', () => {
    const node = hydrate({
      id: 'empty-ok',
      type: CANVAS_NODE_TYPES.script,
      measured: { width: SCRIPT_NODE_SIZE.empty.width, height: SCRIPT_NODE_SIZE.empty.height },
      data: { scriptResult: { rows: [] } },
    });
    expect(node.measured?.width).toBe(SCRIPT_NODE_SIZE.empty.width);
  });
});

describe('脚本结果出现时的外框同步', () => {
  it('空态先量成 480，出表后切到 800 并丢掉旧 measured', () => {
    useCanvasStore.getState().setCanvasData(
      [
        {
          id: 'live-result',
          type: CANVAS_NODE_TYPES.script,
          position: { x: 0, y: 0 },
          measured: {
            width: SCRIPT_NODE_SIZE.empty.width,
            height: SCRIPT_NODE_SIZE.empty.height,
          },
          data: { scriptResult: null },
        } as never,
      ],
      [],
    );

    useCanvasStore.getState().updateNodeData('live-result', {
      scriptResult: { rows: [{ shot_no: 1 }] },
    });

    const node = useCanvasStore.getState().nodes[0];
    expect(node.width).toBe(SCRIPT_NODE_SIZE.withResult.width);
    expect(node.height).toBe(SCRIPT_NODE_SIZE.withResult.height);
    expect(node.measured).toBeUndefined();
    expect(node.style).toMatchObject({
      width: SCRIPT_NODE_SIZE.withResult.width,
      height: SCRIPT_NODE_SIZE.withResult.height,
    });
  });

  it('用户已手动调过尺寸时不覆盖', () => {
    useCanvasStore.getState().setCanvasData(
      [
        {
          id: 'manual-result',
          type: CANVAS_NODE_TYPES.script,
          position: { x: 0, y: 0 },
          measured: {
            width: SCRIPT_NODE_SIZE.empty.width,
            height: SCRIPT_NODE_SIZE.empty.height,
          },
          data: {
            scriptResult: null,
            isSizeManuallyAdjusted: true,
          },
        } as never,
      ],
      [],
    );

    useCanvasStore.getState().updateNodeData('manual-result', {
      scriptResult: { rows: [{ shot_no: 1 }] },
    });

    const node = useCanvasStore.getState().nodes[0];
    expect(node.width).toBeUndefined();
    expect(node.height).toBeUndefined();
    expect(node.measured).toEqual({
      width: SCRIPT_NODE_SIZE.empty.width,
      height: SCRIPT_NODE_SIZE.empty.height,
    });
  });
});
