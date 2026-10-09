// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { create } from 'zustand';

// 吸附对齐：节点拖动时显示蓝色虚线，指示当前位置与其它节点的边/中线对齐。
// 状态独立成一个轻量 store，避免和 canvas 内容 store 混在一起，订阅它的
// 组件（按钮、引导线 overlay）也不会因 canvas 节点变动而重渲染。

const STORAGE_KEY = 'canvas.snapAlign.enabled';

function readPersistedEnabled(): boolean {
  if (typeof window === 'undefined') return false;
  try {
    return window.localStorage.getItem(STORAGE_KEY) === '1';
  } catch {
    return false;
  }
}

function persistEnabled(value: boolean): void {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(STORAGE_KEY, value ? '1' : '0');
  } catch {
    // localStorage 写不进去就算了，下次进来从默认值开始。
  }
}

export interface SnapAlignGuides {
  /** Flow 坐标下的垂直引导线（即固定 x 的竖线）。 */
  vertical: number[];
  /** Flow 坐标下的水平引导线（即固定 y 的横线）。 */
  horizontal: number[];
}

const EMPTY_GUIDES: SnapAlignGuides = { vertical: [], horizontal: [] };

function guidesEqual(left: SnapAlignGuides, right: SnapAlignGuides): boolean {
  if (left === right) return true;
  if (
    left.vertical.length !== right.vertical.length
    || left.horizontal.length !== right.horizontal.length
  ) {
    return false;
  }
  return (
    left.vertical.every((value, index) => value === right.vertical[index])
    && left.horizontal.every((value, index) => value === right.horizontal[index])
  );
}

interface SnapAlignState {
  enabled: boolean;
  guides: SnapAlignGuides;
  toggle: () => void;
  setGuides: (guides: SnapAlignGuides) => void;
  clearGuides: () => void;
}

export const useSnapAlignStore = create<SnapAlignState>((set, get) => ({
  enabled: readPersistedEnabled(),
  guides: EMPTY_GUIDES,
  toggle: () => {
    const next = !get().enabled;
    persistEnabled(next);
    set({ enabled: next, guides: EMPTY_GUIDES });
  },
  setGuides: (guides) => {
    // 吸附计算在拖动期间每帧运行；相同引导线不再触发订阅组件重渲染。
    if (guidesEqual(get().guides, guides)) return;
    set({ guides });
  },
  clearGuides: () => {
    const cur = get().guides;
    if (cur.vertical.length === 0 && cur.horizontal.length === 0) return;
    set({ guides: EMPTY_GUIDES });
  },
}));
