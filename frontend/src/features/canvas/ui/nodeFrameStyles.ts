// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
interface CanvasNodeFrameOptions {
  mainline?: boolean;
  dashed?: boolean;
}

export const CANVAS_NODE_PANEL_SURFACE_CLASS =
  "canvas-node-panel-surface bg-[#212121]/96 shadow-[0_16px_42px_rgba(0,0,0,0.34),inset_0_1px_0_rgba(255,255,255,0.035)]";
export const CANVAS_NODE_INPUT_SURFACE_CLASS =
  "canvas-node-media-surface bg-[#212121] shadow-[0_16px_42px_rgba(0,0,0,0.32),inset_0_1px_0_rgba(255,255,255,0.03)]";
export const CANVAS_NODE_INPUT_BODY_FRAME_CLASS =
  "border-white/12 shadow-[0_8px_20px_rgba(0,0,0,0.24)] hover:border-white/18 focus-within:border-white/24";
export const CANVAS_NODE_INPUT_FRAME_CLASS =
  "border-white/8 shadow-[0_10px_24px_rgba(0,0,0,0.28)] hover:border-white/14 focus-within:border-white/18";
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
  `rounded-full ${CANVAS_NODE_TOOLBAR_SURFACE_CLASS} p-1.5`;
export const CANVAS_NODE_TOOLBAR_CARD_CLASS =
  `rounded-2xl ${CANVAS_NODE_TOOLBAR_SURFACE_CLASS}`;

export function canvasNodeFrameClass({
  mainline = false,
  dashed = false,
}: CanvasNodeFrameOptions = {}): string {
  const borderStyle = dashed ? "border-dashed" : "border-solid";
  const transition = "transition-colors duration-200 ease-out";
  return mainline
    ? `${borderStyle} ${transition} border-white/15 hover:border-white/24`
    : `${borderStyle} ${transition} border-white/11 hover:border-white/20`;
}
