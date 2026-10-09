// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab

export const LOW_DETAIL_ZOOM_THRESHOLD = 0.35;

export const LOD_SHELL_EXEMPT_TYPES: ReadonlySet<string> = new Set([
  'skillNode',
  'groupNode',
  'beatContextNode',
]);

export const CANVAS_LOW_DETAIL_CLASS = 'dc-canvas--low-detail';
export const LOD_RELEASE_DELAY_MS = 80;

// LOD reduces the cost of each mounted node; it must not disable React Flow's
// viewport virtualization. Turning virtualization off at low zoom makes a
// large graph mount every shell and edge, which blocks the main thread during
// pan/zoom. Node state is persisted outside the component, so visibility
// remounts remain safe.
export const CANVAS_ONLY_RENDER_VISIBLE_ELEMENTS = true;

export function isLowDetailZoom(zoom: number): boolean {
  return Number.isFinite(zoom) && zoom < LOW_DETAIL_ZOOM_THRESHOLD;
}

let gestureActive = false;
let lowDetailActive = false;
let hydrateBurstActive = false;

// Video playback is an explicit user intent. Keep those nodes out of the
// low-detail shell and retain the last playhead long enough to survive a
// React Flow visibility remount during a zoom gesture.
type ActiveMediaState = { currentTime: number };
const mediaActiveNodes = new Map<string, ActiveMediaState>();

export function setNodeMediaActive(
  nodeId: string,
  active: boolean,
  currentTime?: number,
): void {
  if (!active) {
    mediaActiveNodes.delete(nodeId);
    return;
  }
  const previous = mediaActiveNodes.get(nodeId);
  mediaActiveNodes.set(nodeId, {
    currentTime:
      Number.isFinite(currentTime) && (currentTime as number) >= 0
        ? (currentTime as number)
        : previous?.currentTime ?? 0,
  });
}

export function isNodeMediaActive(nodeId: string): boolean {
  return mediaActiveNodes.has(nodeId);
}

export function getNodeMediaCurrentTime(nodeId: string): number | null {
  const currentTime = mediaActiveNodes.get(nodeId)?.currentTime;
  return Number.isFinite(currentTime) ? currentTime ?? null : null;
}

const measurementResumeListeners = new Set<() => void>();
// 手势/低清是模块级可变状态，没有 store 可订阅。边这种「只在特定状态下才渲染昂贵
// 特效」的组件需要知道状态何时翻转，否则只能靠父级重渲染顺带刷，漏帧不可避免。
const motionStateListeners = new Set<() => void>();

function notifyMeasurementResume(wasDeferred: boolean): void {
  if (wasDeferred && !isCanvasMeasurementDeferred()) {
    for (const listener of measurementResumeListeners) listener();
  }
}

function notifyMotionState(): void {
  for (const listener of motionStateListeners) listener();
}

/**
 * 订阅手势/低清状态翻转，供 `useSyncExternalStore` 使用。
 *
 * 回调必须幂等且不读状态：`setCanvasLowDetail` 先赋值再广播，重复通知会
 * 让订阅者多跑一次快照比较，除此之外没有副作用。
 */
export function subscribeCanvasMotionState(listener: () => void): () => void {
  motionStateListeners.add(listener);
  return () => {
    motionStateListeners.delete(listener);
  };
}

/**
 * 当前是否应压住画布上的装饰性无限动画。
 *
 * 平移/缩放/拖节点期间每帧都要重排，低清态下这些动画根本看不清 —— 两种情况下
 * 保留它们只有成本没有收益。
 */
export function isCanvasMotionSuppressed(): boolean {
  return gestureActive || lowDetailActive;
}

export function setCanvasGestureActive(active: boolean): void {
  if (gestureActive === active) return;
  const wasDeferred = isCanvasMeasurementDeferred();
  gestureActive = active;
  notifyMotionState();
  notifyMeasurementResume(wasDeferred);
}

export function isCanvasGestureActive(): boolean {
  return gestureActive;
}

export function setCanvasLowDetail(active: boolean): void {
  if (lowDetailActive === active) return;
  const wasDeferred = isCanvasMeasurementDeferred();
  lowDetailActive = active;
  notifyMotionState();
  notifyMeasurementResume(wasDeferred);
}

export function isCanvasLowDetail(): boolean {
  return lowDetailActive;
}

/**
 * 返回当前是否应跳过会触发同步布局的测量。
 *
 * 手势和低缩放阶段节点正在持续移动或不可读，读取布局只会把回流成本加入
 * 每一帧；调用方应在恢复通知后再执行一次测量。
 */
export function isCanvasMeasurementDeferred(): boolean {
  return gestureActive || lowDetailActive;
}

/** 订阅画布从延迟测量状态恢复的通知。 */
export function onCanvasMeasurementResume(listener: () => void): () => void {
  measurementResumeListeners.add(listener);
  return () => {
    measurementResumeListeners.delete(listener);
  };
}

export function isCanvasHydrateBurstActive(): boolean {
  return hydrateBurstActive;
}

/** Mount a hydrated graph as shells first, then restore full nodes in small batches. */
export function beginCanvasHydrateBurst(): void {
  hydrateBurstActive = true;
  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      hydrateBurstActive = false;
    });
  });
}

const hydrateViewportListeners = new Set<(zoom: number) => void>();

export function onCanvasHydrateViewport(listener: (zoom: number) => void): () => void {
  hydrateViewportListeners.add(listener);
  return () => {
    hydrateViewportListeners.delete(listener);
  };
}

export function notifyCanvasHydrateViewport(zoom: number): void {
  for (const listener of hydrateViewportListeners) listener(zoom);
}

const UPGRADES_PER_FRAME = 3;
type UpgradeGrant = () => void;
const upgradeQueue: UpgradeGrant[] = [];
let upgradePumpScheduled = false;

function scheduleUpgradePump(): void {
  if (upgradePumpScheduled) return;
  upgradePumpScheduled = true;
  requestAnimationFrame(pumpUpgrades);
}

function pumpUpgrades(): void {
  upgradePumpScheduled = false;
  if (upgradeQueue.length === 0) return;
  if (!gestureActive) {
    for (const grant of upgradeQueue.splice(0, UPGRADES_PER_FRAME)) grant();
  }
  if (upgradeQueue.length > 0) scheduleUpgradePump();
}

export function requestShellUpgrade(grant: UpgradeGrant): () => void {
  upgradeQueue.push(grant);
  scheduleUpgradePump();
  return () => {
    const index = upgradeQueue.indexOf(grant);
    if (index >= 0) upgradeQueue.splice(index, 1);
  };
}
