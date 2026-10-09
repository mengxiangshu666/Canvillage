// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { memo, type MouseEvent, type SyntheticEvent } from 'react';
import { toast } from 'sonner';

import { useReferencePickStore } from '@/features/canvas/application/referencePickStore';
import { useCanvasStore } from '@/stores/canvasStore';

function stopEvent(event: SyntheticEvent) {
  event.stopPropagation();
}

function OverlayHint({ text, visible }: { text: string; visible: boolean }) {
  return (
    <span className={`pointer-events-none absolute left-1/2 top-1/2 max-w-[86%] -translate-x-1/2 -translate-y-1/2 truncate rounded-md bg-[#17181b]/95 px-2.5 py-1 text-[12px] text-white shadow-lg transition-opacity ${
      visible ? 'opacity-100' : 'opacity-0 group-hover/refpick:opacity-100'
    }`}>
      {text}
    </span>
  );
}

function SelectableOverlay({
  nodeId,
  targetNodeId,
  label,
}: {
  nodeId: string;
  targetNodeId: string;
  label: string;
}) {
  const connected = useCanvasStore((state) =>
    state.edges.some((edge) => edge.source === nodeId && edge.target === targetNodeId),
  );
  const handleClick = (event: MouseEvent) => {
    event.preventDefault();
    event.stopPropagation();
    const store = useCanvasStore.getState();
    if (connected) {
      store.edges
        .filter((edge) => edge.source === nodeId && edge.target === targetNodeId)
        .forEach((edge) => store.deleteEdge(edge.id));
      return;
    }
    if (!store.addEdge(nodeId, targetNodeId)) toast.error('这个节点不能作为参考');
  };
  return (
    <div
      role="button"
      tabIndex={-1}
      title={connected ? '取消选择' : `选择 ${label}`}
      className={`nodrag nopan group/refpick absolute inset-0 z-[45] cursor-pointer rounded-[6px] ring-2 ${
        connected
          ? 'bg-[rgb(var(--accent-rgb)/0.1)] ring-[rgb(var(--accent-rgb))]'
          : 'ring-transparent hover:bg-[rgb(var(--accent-rgb)/0.08)] hover:ring-[rgb(var(--accent-rgb))]'
      }`}
      onClick={handleClick}
      onPointerDown={stopEvent}
      onMouseDown={stopEvent}
      onDoubleClick={stopEvent}
      onContextMenu={stopEvent}
    >
      <OverlayHint text={connected ? '取消选择' : `选择 ${label}`} visible={connected} />
    </div>
  );
}

function BlockedOverlay({ reason }: { reason: string }) {
  return (
    <div
      title={reason}
      className="nodrag group/refpick absolute inset-0 z-[45] cursor-not-allowed rounded-[6px] bg-black/55"
      onClick={stopEvent}
      onDoubleClick={stopEvent}
      onContextMenu={stopEvent}
    >
      <OverlayHint text={reason} visible={false} />
    </div>
  );
}

function ReferencePickNodeOverlayImpl({ nodeId }: { nodeId: string }) {
  const targetNodeId = useReferencePickStore((state) =>
    state.request && state.request.targetNodeId !== nodeId
      ? state.request.targetNodeId
      : null,
  );
  const label = useReferencePickStore(
    (state) => state.request?.candidates.get(nodeId)?.label ?? null,
  );
  const rejection = useReferencePickStore(
    (state) => state.request?.rejections.get(nodeId) ?? null,
  );
  if (!targetNodeId) return null;
  if (label) return <SelectableOverlay nodeId={nodeId} targetNodeId={targetNodeId} label={label} />;
  if (rejection) return <BlockedOverlay reason={rejection} />;
  return null;
}

export const ReferencePickNodeOverlay = memo(ReferencePickNodeOverlayImpl);

