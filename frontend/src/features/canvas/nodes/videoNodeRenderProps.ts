// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import type { VideoNodeData } from "@/features/canvas/domain/canvasNodes";

export type VideoNodeRenderProps = {
  id: string;
  data: VideoNodeData;
  selected?: boolean;
  width?: number;
  height?: number;
  positionAbsoluteX?: number;
  positionAbsoluteY?: number;
  dragging?: boolean;
};

export type CanvasNodeRenderProps = {
  id: string;
  data: unknown;
  selected?: boolean;
  width?: number;
  height?: number;
  positionAbsoluteX?: number;
  positionAbsoluteY?: number;
  dragging?: boolean;
};

export function areCanvasNodePropsEqual(
  previous: CanvasNodeRenderProps,
  next: CanvasNodeRenderProps,
): boolean {
  return previous.id === next.id
    && previous.data === next.data
    && previous.selected === next.selected
    && previous.width === next.width
    && previous.height === next.height;
}

export function videoNodePreload(selected?: boolean): "metadata" | "none" {
  return selected ? "metadata" : "none";
}

/**
 * React Flow moves the node wrapper during pan/drag without changing the
 * contents rendered inside it. Ignore positional and interaction-only props so
 * a node's editor is not rebuilt for every pointer frame.
 */
export function areVideoNodePropsEqual(
  previous: VideoNodeRenderProps,
  next: VideoNodeRenderProps,
): boolean {
  return areCanvasNodePropsEqual(previous, next);
}
