// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
interface CanvasNodeFrameOptions {
  mainline?: boolean;
  dashed?: boolean;
}

export const CANVAS_NODE_PANEL_SURFACE_CLASS =
  "canvas-node-panel-surface bg-[#262626] shadow-[inset_0_1px_0_rgba(255,255,255,0.035)]";
export const CANVAS_NODE_INPUT_SURFACE_CLASS =
  "canvas-node-media-surface bg-[#262626] shadow-none";
export const CANVAS_NODE_INPUT_BODY_FRAME_CLASS =
  "border-[#363636] hover:border-white/15 focus-within:border-[#09caf5]/55";
export const CANVAS_NODE_INPUT_FRAME_CLASS =
  "border-[#363636] hover:border-white/15 focus-within:border-[#09caf5]/55";
export const CANVAS_NODE_INPUT_PLACEHOLDER_CLASS =
  "canvas-node-input-placeholder placeholder:text-[var(--canvas-node-input-placeholder)]";

// Chrome for a node's floating operation area — the prompt / controls panel that
// floats below (or, when expanded, replaces) a selected node. Single source of
// truth so every node's 操作区 shares one neutral panel surface + border. Keep
// it separate from CANVAS_NODE_INPUT_SURFACE_CLASS: media previews have their
// own depth/empty-state styling and must never leak decoration over controls.
export const CANVAS_NODE_OPS_PANEL_CLASS = `border ${CANVAS_NODE_PANEL_SURFACE_CLASS} ${CANVAS_NODE_INPUT_FRAME_CLASS}`;
export const CANVAS_NODE_TOOLBAR_SURFACE_CLASS = CANVAS_NODE_OPS_PANEL_CLASS;
export const CANVAS_NODE_TOOLBAR_PILL_CLASS =
  `rounded-[10px] ${CANVAS_NODE_TOOLBAR_SURFACE_CLASS} p-1`;
export const CANVAS_NODE_TOOLBAR_CARD_CLASS =
  `rounded-[12px] ${CANVAS_NODE_TOOLBAR_SURFACE_CLASS}`;

export function canvasNodeFrameClass({
  mainline = false,
  dashed = false,
}: CanvasNodeFrameOptions = {}): string {
  const borderStyle = dashed ? "border-dashed" : "border-solid";
  const transition = "transition-colors duration-200 ease-out";
  return mainline
    ? `${borderStyle} ${transition} border-white/13 hover:border-white/23`
    : `${borderStyle} ${transition} border-white/10 hover:border-white/19`;
}
