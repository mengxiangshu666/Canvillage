// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useEffect } from 'react';
import { useReactFlow } from '@xyflow/react';
import { MousePointerClick, X } from 'lucide-react';

import { useReferencePickStore } from '@/features/canvas/application/referencePickStore';
import { useCanvasStore } from '@/stores/canvasStore';

export function ReferencePickBanner() {
  const request = useReferencePickStore((state) => state.request);
  const stop = useReferencePickStore((state) => state.stop);
  const reactFlow = useReactFlow();
  const targetNodeId = request?.targetNodeId ?? null;
  const targetMissing = useCanvasStore((state) =>
    targetNodeId ? !state.nodes.some((node) => node.id === targetNodeId) : false,
  );

  useEffect(() => {
    if (targetMissing) stop();
  }, [stop, targetMissing]);

  useEffect(() => {
    if (!targetNodeId) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') stop();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [stop, targetNodeId]);

  if (!request) return null;

  const handleReturn = () => {
    useCanvasStore.getState().setSelectedNode(request.targetNodeId);
    if (request.originViewport) {
      void reactFlow.setViewport(request.originViewport, { duration: 320 });
    }
    stop();
  };

  return (
    <div className="pointer-events-none absolute left-1/2 top-4 z-[140] -translate-x-1/2">
      <div className="pointer-events-auto flex items-center gap-2 rounded-md bg-[rgb(var(--accent-rgb))] py-1.5 pl-3 pr-1.5 text-[13px] text-white shadow-xl">
        <MousePointerClick className="h-4 w-4" aria-hidden />
        <span className="font-medium">从画布选择参考</span>
        <button
          type="button"
          onClick={handleReturn}
          className="ml-1 inline-flex h-7 items-center rounded-md bg-white/20 px-3 text-[12px] hover:bg-white/30"
        >
          完成并返回
        </button>
        <button
          type="button"
          onClick={stop}
          title="退出选择"
          aria-label="退出选择"
          className="inline-flex h-7 w-7 items-center justify-center rounded-md text-white/80 hover:bg-white/20 hover:text-white"
        >
          <X className="h-4 w-4" aria-hidden />
        </button>
      </div>
    </div>
  );
}

