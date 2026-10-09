// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab

import {
  attachBorderBeam,
  type BorderBeamController,
  type BorderBeamOptions,
} from 'border-beam-vanilla';

const ACTIVE_NODE_SELECTOR = [
  '.react-flow__node.selected',
  '.react-flow__node.canvas-node-generating',
].join(',');
const NODE_SELECTOR = '.react-flow__node';
const OVERLAY_CLASS = 'canvas-node-live-beam';
const FADE_OUT_MS = 540;

export const CANVAS_NODE_BEAM_MIN_ZOOM = 0.28;

type BeamAttacher = (
  element: HTMLElement,
  options: BorderBeamOptions,
) => BorderBeamController;

interface BeamRecord {
  controller: BorderBeamController;
  overlay: HTMLDivElement;
  removalTimer: number | null;
}

export interface CanvasNodeBorderBeamManager {
  setSuspended(suspended: boolean): void;
  sync(): void;
  destroy(): void;
  activeCount(): number;
}

function activeNodeElements(stage: HTMLElement): Set<HTMLElement> {
  return new Set(
    Array.from(stage.querySelectorAll<HTMLElement>(ACTIVE_NODE_SELECTOR)),
  );
}

function nodeBorderRadius(node: HTMLElement): number {
  const parsed = Number.parseFloat(getComputedStyle(node).borderTopLeftRadius);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : 16;
}

export function createCanvasNodeBorderBeamManager(
  stage: HTMLElement,
  attach: BeamAttacher = attachBorderBeam,
): CanvasNodeBorderBeamManager {
  const records = new Map<HTMLElement, BeamRecord>();
  let destroyed = false;
  let suspended = false;
  let syncQueued = false;

  const destroyRecord = (node: HTMLElement, record: BeamRecord) => {
    if (record.removalTimer !== null) {
      window.clearTimeout(record.removalTimer);
    }
    record.controller.destroy();
    record.overlay.remove();
    records.delete(node);
  };

  const ensureRecord = (node: HTMLElement): BeamRecord => {
    const current = records.get(node);
    if (current) {
      if (current.removalTimer !== null) {
        window.clearTimeout(current.removalTimer);
        current.removalTimer = null;
      }
      return current;
    }

    const overlay = document.createElement('div');
    overlay.className = OVERLAY_CLASS;
    overlay.setAttribute('aria-hidden', 'true');
    node.appendChild(overlay);
    const controller = attach(overlay, {
      size: 'md',
      colorVariant: 'colorful',
      theme: 'dark',
      active: false,
      borderRadius: nodeBorderRadius(node),
      strength: 0.9,
      duration: 1.96,
    });
    const record = { controller, overlay, removalTimer: null };
    records.set(node, record);
    return record;
  };

  const deactivate = (node: HTMLElement, record: BeamRecord, immediate = false) => {
    record.controller.setActive(false);
    record.overlay.dataset.state = 'fading';
    if (immediate || !node.isConnected) {
      destroyRecord(node, record);
      return;
    }
    if (record.removalTimer !== null) return;
    record.removalTimer = window.setTimeout(() => {
      if (records.get(node) === record) destroyRecord(node, record);
    }, FADE_OUT_MS);
  };

  const sync = () => {
    if (destroyed) return;
    const activeNodes = suspended ? new Set<HTMLElement>() : activeNodeElements(stage);

    for (const [node, record] of records) {
      if (!node.isConnected || !stage.contains(node)) {
        deactivate(node, record, true);
        continue;
      }
      if (!activeNodes.has(node)) deactivate(node, record);
    }

    for (const node of activeNodes) {
      const record = ensureRecord(node);
      record.overlay.dataset.state = node.classList.contains('canvas-node-generating')
        ? 'running'
        : 'selected';
      record.controller.setActive(true);
    }
  };

  const queueSync = () => {
    if (destroyed || syncQueued) return;
    syncQueued = true;
    queueMicrotask(() => {
      syncQueued = false;
      sync();
    });
  };

  const stageObserver = new MutationObserver((mutations) => {
    if (mutations.some((mutation) => (
      mutation.type === 'childList'
      || (
        mutation.type === 'attributes'
        && mutation.target instanceof Element
        && mutation.target.matches(NODE_SELECTOR)
      )
    ))) {
      queueSync();
    }
  });
  stageObserver.observe(stage, {
    subtree: true,
    childList: true,
    attributes: true,
    attributeFilter: ['class'],
  });

  sync();

  return {
    setSuspended(next) {
      if (destroyed || suspended === next) return;
      suspended = next;
      sync();
    },
    sync,
    activeCount: () => records.size,
    destroy() {
      if (destroyed) return;
      destroyed = true;
      stageObserver.disconnect();
      for (const [node, record] of [...records]) {
        destroyRecord(node, record);
      }
    },
  };
}

export function canvasNodeRuntimeIsActive(data: Record<string, unknown>): boolean {
  return [
    'isGenerating',
    'isUploading',
    'isAnalyzing',
    'isProcessing',
    'isRunning',
    'isSeparatingAv',
  ].some((key) => data[key] === true);
}
