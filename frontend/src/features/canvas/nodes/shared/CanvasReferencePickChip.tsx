// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { MousePointerClick } from 'lucide-react';
import { toast } from 'sonner';

import { collectReferencePickTargets } from '@/features/canvas/application/referencePick';
import { useReferencePickStore } from '@/features/canvas/application/referencePickStore';
import type { CanvasNodeType } from '@/features/canvas/domain/canvasNodes';
import {
  NODE_TEXT_CONTROL_ICON_CLASS,
  NODE_TEXT_CONTROL_TRIGGER_CLASS,
} from '@/features/canvas/ui/nodeControlStyles';
import { useCanvasStore } from '@/stores/canvasStore';

export function CanvasReferencePickChip({
  nodeId,
  nodeType,
}: {
  nodeId: string;
  nodeType: CanvasNodeType;
}) {
  const active = useReferencePickStore((state) => state.request?.targetNodeId === nodeId);

  return (
    <button
      type="button"
      onClick={(event) => {
        event.stopPropagation();
        if (active) {
          useReferencePickStore.getState().stop();
          return;
        }
        const { nodes, currentViewport } = useCanvasStore.getState();
        const targets = collectReferencePickTargets(nodes, nodeId, nodeType);
        if (targets.candidates.size === 0) {
          toast.info('画布上还没有可以作为参考的节点');
          return;
        }
        useReferencePickStore.getState().start({
          targetNodeId: nodeId,
          targetNodeType: nodeType,
          originViewport: currentViewport ?? null,
          ...targets,
        });
      }}
      className={`${NODE_TEXT_CONTROL_TRIGGER_CLASS} group/canvas-ref shrink-0 px-1.5 ${
        active ? 'text-[rgb(var(--accent-rgb))]' : ''
      }`}
      title={active ? '退出画布参考选择' : '直接从画布选择已有节点作为参考'}
    >
      <MousePointerClick
        className={`${NODE_TEXT_CONTROL_ICON_CLASS} ${
          active ? 'text-[rgb(var(--accent-rgb))]' : 'group-hover/canvas-ref:text-text-dark'
        }`}
      />
      <span>画布参考</span>
    </button>
  );
}

