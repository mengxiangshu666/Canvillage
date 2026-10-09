// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab

export type CanvasInteractionKind =
  | 'agent-move'
  | 'agent-resize'
  | 'canvas-pan'
  | 'canvas-zoom'
  | 'companion-drag'
  | 'node-drag'
  | 'node-resize'
  | 'selection-drag';

const activeInteractions = new Map<symbol, CanvasInteractionKind>();
const pausedPreviewVideos = new Set<HTMLVideoElement>();
let resumeFrame: number | null = null;

function canvasPreviewVideos(): HTMLVideoElement[] {
  if (typeof document === 'undefined') return [];
  return Array.from(
    document.querySelectorAll<HTMLVideoElement>(
      '.village-canvas-stage .react-flow__node video',
    ),
  );
}

function pauseCanvasPreviews(): void {
  if (resumeFrame !== null) {
    window.cancelAnimationFrame(resumeFrame);
    resumeFrame = null;
  }
  for (const video of canvasPreviewVideos()) {
    if (video.paused || video.ended) continue;
    pausedPreviewVideos.add(video);
    video.pause();
  }
}

function resumeCanvasPreviews(): void {
  if (typeof window === 'undefined') return;
  resumeFrame = window.requestAnimationFrame(() => {
    resumeFrame = null;
    for (const video of pausedPreviewVideos) {
      if (!document.contains(video) || video.ended) continue;
      void video.play().catch(() => undefined);
    }
    pausedPreviewVideos.clear();
  });
}

function syncInteractionMode(): void {
  if (typeof document === 'undefined') return;
  const root = document.documentElement;
  const interactionKinds = Array.from(activeInteractions.values());
  const latestKind = interactionKinds[interactionKinds.length - 1];
  if (latestKind) {
    root.dataset.villageInteracting = 'true';
    root.dataset.villageInteractionKind = latestKind;
    pauseCanvasPreviews();
    return;
  }
  delete root.dataset.villageInteracting;
  delete root.dataset.villageInteractionKind;
  resumeCanvasPreviews();
}

/**
 * 开启一次高频交互。返回的结束函数可重复调用，多个交互重叠时只在最后一个结束后恢复画质。
 */
export function beginCanvasInteraction(kind: CanvasInteractionKind): () => void {
  const token = Symbol(kind);
  activeInteractions.set(token, kind);
  syncInteractionMode();
  let ended = false;
  return () => {
    if (ended) return;
    ended = true;
    activeInteractions.delete(token);
    syncInteractionMode();
  };
}

export function resetCanvasInteractionPerformanceForTests(): void {
  activeInteractions.clear();
  pausedPreviewVideos.clear();
  if (resumeFrame !== null && typeof window !== 'undefined') {
    window.cancelAnimationFrame(resumeFrame);
    resumeFrame = null;
  }
  if (typeof document !== 'undefined') {
    delete document.documentElement.dataset.villageInteracting;
    delete document.documentElement.dataset.villageInteractionKind;
  }
}
