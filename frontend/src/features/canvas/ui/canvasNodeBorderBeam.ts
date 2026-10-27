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

export type CanvasNodeBeamState = 'running' | 'selected';

export interface CanvasNodeBorderBeamManager {
  setSuspended(suspended: boolean): void;
  /**
   * Drive the beam from canvas state instead of watching the DOM. Callers pass
   * the nodes that should glow, keyed by node id; an empty map clears all
   * beams. When never called, the manager falls back to scanning node classes.
   */
  setActiveNodes(active: ReadonlyMap<string, CanvasNodeBeamState>): void;
  sync(): void;
  destroy(): void;
  activeCount(): number;
}

function activeNodeElements(stage: HTMLElement): Set<HTMLElement> {
  return new Set(
    Array.from(stage.querySelectorAll<HTMLElement>(ACTIVE_NODE_SELECTOR)),
  );
}

function nodeElementsById(stage: HTMLElement): Map<string, HTMLElement> {
  const byId = new Map<string, HTMLElement>();
  for (const element of stage.querySelectorAll<HTMLElement>(NODE_SELECTOR)) {
    const id = element.getAttribute('data-id');
    if (id) byId.set(id, element);
  }
  return byId;
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
  // Set by `setActiveNodes`. Null keeps the legacy class-scanning behaviour,
  // which the DOM-level tests rely on.
  let drivenNodes: ReadonlyMap<string, CanvasNodeBeamState> | null = null;

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

  const collectActive = (): Map<HTMLElement, CanvasNodeBeamState> => {
    const active = new Map<HTMLElement, CanvasNodeBeamState>();
    if (suspended) return active;
    if (drivenNodes === null) {
      for (const node of activeNodeElements(stage)) {
        active.set(
          node,
          node.classList.contains('canvas-node-generating') ? 'running' : 'selected',
        );
      }
      return active;
    }
    if (drivenNodes.size === 0) return active;
    const byId = nodeElementsById(stage);
    for (const [id, state] of drivenNodes) {
      const element = byId.get(id);
      if (element) active.set(element, state);
    }
    return active;
  };

  const sync = () => {
    if (destroyed) return;
    const activeNodes = collectActive();

    for (const [node, record] of records) {
      if (!node.isConnected || !stage.contains(node)) {
        deactivate(node, record, true);
        continue;
      }
      if (!activeNodes.has(node)) deactivate(node, record);
    }

    for (const [node, state] of activeNodes) {
      const record = ensureRecord(node);
      record.overlay.dataset.state = state;
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

  // Only node mount/unmount matters now: selection and generation state is
  // pushed in through `setActiveNodes`, so we no longer observe every class
  // change on every node (which fired on any status flip anywhere).
  const stageObserver = new MutationObserver((mutations) => {
    for (const mutation of mutations) {
      if (mutation.type !== 'childList') continue;
      for (const node of [...mutation.addedNodes, ...mutation.removedNodes]) {
        if (!(node instanceof HTMLElement)) continue;
        if (node.matches(NODE_SELECTOR) || node.querySelector(NODE_SELECTOR)) {
          queueSync();
          return;
        }
      }
    }
  });
  stageObserver.observe(stage, {
    subtree: true,
    childList: true,
  });

  sync();

  return {
    setSuspended(next) {
      if (destroyed || suspended === next) return;
      suspended = next;
      sync();
    },
    setActiveNodes(active) {
      if (destroyed) return;
      drivenNodes = active;
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
