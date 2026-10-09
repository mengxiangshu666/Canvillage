// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { SCRIPT_NODE_SIZE, resolveKnownNodeEdge } from '@/features/canvas/domain/canvasNodes';

/**
 * 脚本节点的版面常数与纯计算。
 *
 * 抽出来的原因有两个，都是实测踩过的：
 *
 * 1. **浮层 z 序此前是散落的魔法数**（预览贴片 400 / 全屏 220 / 分镜弹层 230）。
 *    预览贴片挂在节点自己的 portal 上，写在最上面时，打开全屏后节点里那张预览会
 *    画在蒙层之上。这里把三个层级并排列出，谁在上面一眼可见，改的时候不会只改一个。
 * 2. **输入面板的外溢是定值 60**，在最小宽度 360 下让面板比节点宽 120px、两侧悬空。
 *    改成「按节点宽度取外溢」：最小宽度时不外溢（面板与节点等宽），宽节点才给出余量。
 */

/** 脚本相关的浮层层级（越小越靠下）。 */
export const SCRIPT_NODE_Z = {
  /** 节点内参考图贴片的悬浮预览 —— 必须在全屏/弹层蒙层之下。 */
  chipPreview: 180,
  /** 节点自带的表格全屏。 */
  fullscreen: 220,
  /** 「生成分镜」确认弹层，压在全屏之上（从全屏里也能触发）。 */
  storyboardDialog: 230,
} as const;

/** 输入面板左右各外溢的上限（节点足够宽时）。 */
export const PANEL_OVERHANG_MAX_PX = 60;

/**
 * 输入面板相对节点左右各外溢多少像素。
 *
 * 最小宽度（360）下返回 0 —— 面板与节点等宽，不会两侧悬空；节点越宽外溢越多，
 * 到 {@link PANEL_OVERHANG_MAX_PX} 封顶。
 */
export function resolveScriptPanelOverhang(nodeWidth: number): number {
  const width = Number.isFinite(nodeWidth) ? nodeWidth : SCRIPT_NODE_SIZE.min.width;
  const slack = (width - SCRIPT_NODE_SIZE.min.width) / 2;
  return Math.round(Math.min(PANEL_OVERHANG_MAX_PX, Math.max(0, slack)));
}

/**
 * 脚本节点在没有显式尺寸时的默认档：有表格 800×400，没表格 480×320。
 *
 * 这是**三处共同的那个「默认尺寸」**——节点本体（{@link resolveScriptNodeBox}）、
 * LOD 外壳、`canvasStore` 的兜底表都读这里，别再各写一份。
 */
export function resolveScriptNodeDefaultSize(hasResult: boolean): { width: number; height: number } {
  return hasResult ? SCRIPT_NODE_SIZE.withResult : SCRIPT_NODE_SIZE.empty;
}

/** 节点数据里已经有一张脚本表了吗（只看行数，不看内容）。 */
export function scriptDataHasRows(data: unknown): boolean {
  const result = (data as { scriptResult?: unknown } | null | undefined)?.scriptResult;
  const rows = (result as { rows?: unknown } | null | undefined)?.rows;
  return Array.isArray(rows) && rows.length > 0;
}

/**
 * LOD 外壳给脚本节点画的框。
 *
 * 外壳是**第一个**被 ResizeObserver 量到的 DOM，量到的尺寸会写进 `measured`，而
 * `getNodeDimensions` 里 `measured` 优先于 `width` —— 所以外壳一旦画错档，节点就被
 * 永久钉在那一档。实测时间线（无 width、8 行表的节点）：
 *     props:0 → 外壳 480×320 → props:480 → 真节点 480×320
 * 即外壳按「空态 480×320」画了，800 那一档再也没机会出现。这里必须和节点本体读同一档。
 */
export function resolveScriptShellFallbackSize(data: unknown): { width: number; height: number } {
  return resolveScriptNodeDefaultSize(scriptDataHasRows(data));
}

/**
 * 节点实际渲染尺寸：优先用 React Flow 给的宽高，没有就按有没有表格取默认档，
 * 再抬到下限。
 *
 * 这里必须用 {@link resolveKnownNodeEdge} 而不是 `typeof width === 'number'`：
 * React Flow 对未测量节点传下来的是**数字 0**（见该函数的说明），收下它渲染宽就一律
 * 落到 `Math.max(360, 0)`，480 / 800 两档永远不可达。用户画布上那个 8 行表格被挤在
 * 最小宽度里的脚本节点就是这么来的。
 *
 * 只夹下线不夹上线：上限由 `NodeResizeHandle` 的 maxWidth/maxHeight 在拖动时就挡住，
 * 这里再夹一次会让渲染宽度与 React Flow 记的 `node.width` 分叉。
 */
export function resolveScriptNodeBox(params: {
  width?: number | null;
  height?: number | null;
  hasResult: boolean;
}): { width: number; height: number } {
  const fallback = resolveScriptNodeDefaultSize(params.hasResult);
  const width = resolveKnownNodeEdge(params.width) ?? fallback.width;
  const height = resolveKnownNodeEdge(params.height) ?? fallback.height;
  return {
    width: Math.max(SCRIPT_NODE_SIZE.min.width, Math.round(width)),
    height: Math.max(SCRIPT_NODE_SIZE.min.height, Math.round(height)),
  };
}
