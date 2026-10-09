// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import {
  memo,
  useEffect,
  useState,
  useSyncExternalStore,
  type ComponentType,
  type CSSProperties,
} from 'react';
import { Handle, Position, useStore, type NodeProps } from '@xyflow/react';

import {
  LOD_SHELL_EXEMPT_TYPES,
  isCanvasGestureActive,
  isCanvasHydrateBurstActive,
  isLowDetailZoom,
  isNodeMediaActive,
  requestShellUpgrade,
} from '@/features/canvas/application/canvasLod';
import { resolveImageDisplayUrl } from '@/features/canvas/application/imageData';
import { useNodeBodyVariant } from '@/features/canvas/hooks/useNodeBodyVariantBudget';
import { getLodStill, requestLodStill, subscribeLodStills } from '@/features/canvas/application/videoFrameCapture';
import { withMediaVariant, type MediaVariant } from '@/lib/media-url';
import type { CanvasNodeType } from '@/features/canvas/domain/canvasNodes';
import { resolveKnownNodeEdge } from '@/features/canvas/domain/canvasNodes';
import { resolveScriptShellFallbackSize } from '@/features/canvas/nodes/script/scriptNodeLayout';
import {
  nodeHasSourceHandle,
  nodeHasTargetHandle,
} from '@/features/canvas/domain/nodeRegistry';
import { useCanvasStore } from '@/stores/canvasStore';
import { ReferencePickNodeOverlay } from '@/features/canvas/ui/ReferencePickNodeOverlay';

export const SHELL_FALLBACK_SIZES: Partial<Record<string, { width: number; height: number }>> = {
  uploadNode: { width: 320, height: 350 },
  imageNode: { width: 580, height: 360 },
  imageGenNode: { width: 580, height: 360 },
  exportImageNode: { width: 533, height: 300 },
  videoNode: { width: 580, height: 380 },
  textAnnotationNode: { width: 440, height: 320 },
  audioNode: { width: 480, height: 210 },
  videoStoryNode: { width: 720, height: 360 },
  videoComposeNode: { width: 240, height: 136 },
  pano360ViewerNode: { width: 900, height: 540 },
  threeDWorldNode: { width: 340, height: 210 },
  storyboardNode: { width: 800, height: 600 },
  storyboardGenNode: { width: 800, height: 600 },
  styleNode: { width: 220, height: 124 },
};

const DEFAULT_SHELL_SIZE = { width: 400, height: 300 };

type ShellData = {
  imageUrl?: string | null;
  previewImageUrl?: string | null;
  referenceImageUrl?: string | null;
  videoUrl?: string | null;
  isGenerating?: boolean;
  isUploading?: boolean;
};

/**
 * 外壳该按多大画。
 *
 * 脚本节点是唯一「默认尺寸取决于内容」的节点（有表格 800×400 / 没表格 480×320），
 * 所以它单独走 {@link resolveScriptShellFallbackSize}，不用表里的定值。
 */
function resolveShellFallbackSize(
  type: string,
  data: ShellData,
): { width: number; height: number } {
  if (type === 'scriptNode') return resolveScriptShellFallbackSize(data);
  return SHELL_FALLBACK_SIZES[type] ?? DEFAULT_SHELL_SIZE;
}

function resolveShellImage(type: string, data: ShellData, variant: MediaVariant): string | null {
  const pick = (...candidates: Array<string | null | undefined>) => {
    for (const candidate of candidates) {
      if (candidate) return withMediaVariant(resolveImageDisplayUrl(candidate), variant);
    }
    return null;
  };
  if (type === 'imageGenNode') return pick(data.imageUrl, data.previewImageUrl, data.referenceImageUrl);
  if (['uploadNode', 'imageNode', 'exportImageNode'].includes(type)) return pick(data.imageUrl, data.previewImageUrl);
  if (type === 'videoNode') return null;
  return pick(data.previewImageUrl);
}

function LodShell({ type, id, data, selected, width, height }: {
  type: string;
  id: string;
  data: ShellData;
  selected: boolean | undefined;
  width: number | undefined;
  height: number | undefined;
}) {
  const fallback = resolveShellFallbackSize(type, data);
  // 0 不是尺寸，是 React Flow 的「还没量到」（`getNodeDimensions` 的 `?? 0` 兜底，
  // 由 `...nodeDimensions` 原样展开进 props）。`width ?? fallback` 会把它收下 ——
  // 外壳宽 0 就等于节点在低缩放下整个消失。
  const w = resolveKnownNodeEdge(width) ?? fallback.width;
  const h = resolveKnownNodeEdge(height) ?? fallback.height;
  const variant = useNodeBodyVariant({ width: w, height: h }) ?? 'thumb';
  const videoSource = type === 'videoNode' && data.videoUrl ? resolveImageDisplayUrl(data.videoUrl) : null;
  const lodStill = useSyncExternalStore(
    subscribeLodStills,
    () => getLodStill(videoSource),
    () => null,
  );
  useEffect(() => {
    requestLodStill(videoSource);
  }, [videoSource]);
  const style: CSSProperties = {
    width: w,
    height: h,
  };
  const imageSrc = type === 'videoNode'
    ? lodStill ?? (data.previewImageUrl ? withMediaVariant(resolveImageDisplayUrl(data.previewImageUrl), variant) : null)
    : resolveShellImage(type, data, variant);
  const busy = Boolean(data.isGenerating || data.isUploading);

  return (
    <div className={`dc-lod-shell${selected ? ' dc-lod-shell--selected' : ''}`} style={style}>
      {nodeHasTargetHandle(type as CanvasNodeType) && (
        <Handle type="target" position={Position.Left} id="target" />
      )}
      {nodeHasSourceHandle(type as CanvasNodeType) && (
        <Handle type="source" position={Position.Right} id="source" />
      )}
      {imageSrc && <img src={imageSrc} alt="" draggable={false} className="dc-lod-shell__thumb" />}
      {busy && <span className="dc-lod-shell__busy" data-node-id={id} />}
    </div>
  );
}

const lowDetailSelector = (state: { transform: [number, number, number] }) =>
  isLowDetailZoom(state.transform[2]);

// Node components intentionally narrow their props independently. React Flow's
// NodeTypes contract is the common runtime boundary used by this wrapper.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AnyNodeComponent = ComponentType<any>;

export function withLodShell(type: string, Component: AnyNodeComponent): ComponentType<NodeProps> {
  const exempt = LOD_SHELL_EXEMPT_TYPES.has(type);
  const Wrapped = (props: NodeProps) => {
    const lowDetail = useStore(lowDetailSelector);
    const isActiveSelection = useCanvasStore(
      (state) => state.selectedNodeId === props.id,
    );
    // VideoNode owns its own low-detail swap so it can observe the real player
    // state synchronously. Keeping the component mounted avoids a shell race
    // that would otherwise reset an actively playing video's playhead.
    const wantShell = lowDetail && type !== 'videoNode' && !exempt && !isActiveSelection && !isNodeMediaActive(props.id);
    const [heldShell, setHeldShell] = useState(
      () => wantShell || (type !== 'videoNode' && !exempt && !isActiveSelection && (
        isCanvasGestureActive() || isCanvasHydrateBurstActive()
      )),
    );

    useEffect(() => {
      if (wantShell && !heldShell) setHeldShell(true);
    }, [heldShell, wantShell]);

    useEffect(() => {
      if (!heldShell || wantShell) return;
      return requestShellUpgrade(() => setHeldShell(false));
    }, [heldShell, wantShell]);

    if (heldShell) {
      return (
        <>
          <LodShell
            type={type}
            id={props.id}
            data={props.data as ShellData}
            selected={props.selected}
            width={props.width}
            height={props.height}
          />
          <ReferencePickNodeOverlay nodeId={props.id} />
        </>
      );
    }
    return (
      <>
        <Component {...props} />
        <ReferencePickNodeOverlay nodeId={props.id} />
      </>
    );
  };
  Wrapped.displayName = `withLodShell(${type})`;
  return memo(Wrapped);
}
