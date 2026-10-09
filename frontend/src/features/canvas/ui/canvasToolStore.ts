// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { create } from 'zustand';

/**
 * 画布指针工具。
 * - `move`：默认模式，左键框选 / 拖节点，中键或空格拖动平移画布。
 * - `hand`：抓手，左键按住直接拖动画布；节点不可拖、不可框选。
 *
 * 不持久化：抓手属于临时手势；刷新一律回到可直接编辑节点的移动工具。
 */
export type CanvasTool = 'move' | 'hand';

interface CanvasToolState {
  tool: CanvasTool;
  setTool: (tool: CanvasTool) => void;
}

export const useCanvasToolStore = create<CanvasToolState>((set) => ({
  tool: 'move',
  setTool: (tool) => set({ tool }),
}));
