// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useEffect, useState } from 'react';
import { Bookmark, Maximize2, Minimize2, X } from 'lucide-react';
import { MiniMap } from '@xyflow/react';

import { CanvasMinimapBookmarksOverlay } from './CanvasMinimapBookmarksOverlay';

interface CanvasMinimapPanelProps {
  expanded: boolean;
  open: boolean;
  suspended?: boolean;
  onClose: () => void;
  onExpandedChange: (expanded: boolean) => void;
}

export function CanvasMinimapPanel({
  expanded,
  open,
  suspended = false,
  onClose,
  onExpandedChange,
}: CanvasMinimapPanelProps) {
  const [bookmarksVisible, setBookmarksVisible] = useState(false);

  useEffect(() => {
    if (!open || !expanded) setBookmarksVisible(false);
  }, [expanded, open]);

  if (!open) return null;

  return (
    <div
      className="canvas-minimap-shell nopan nowheel pointer-events-none absolute z-[10000]"
      data-expanded={expanded ? 'true' : 'false'}
      data-suspended={suspended ? 'true' : 'false'}
      aria-label="任务视图"
    >
      {suspended ? (
        // MiniMap subscribes to the complete node list. Unmount it during a
        // drag so it no longer repaints on every pointer frame; it remounts with
        // the final node positions when the interaction ends.
        <div
          className="canvas-minimap canvas-minimap--task-view canvas-minimap--suspended"
          data-testid="canvas-minimap-suspended"
          aria-hidden="true"
        />
      ) : (
        <MiniMap
          position="bottom-left"
          className={`canvas-minimap canvas-minimap--task-view nopan nowheel ${
            expanded ? 'canvas-minimap--expanded' : 'canvas-minimap--compact'
          }`}
          nodeColor="rgba(184, 184, 190, 0.9)"
          nodeStrokeColor="rgba(255, 255, 255, 0.22)"
          nodeBorderRadius={4}
          nodeStrokeWidth={1}
          maskColor="rgba(6, 7, 9, 0.72)"
          maskStrokeColor="rgba(125, 249, 255, 0.72)"
          maskStrokeWidth={1.2}
          pannable
          zoomable
          zoomStep={0.65}
          offsetScale={6}
          ariaLabel="任务视图：拖动定位画布，滚轮缩放"
        />
      )}
      <div
        className="canvas-minimap-caption nopan nowheel pointer-events-none absolute z-[10002] flex items-center gap-1.5"
      >
        <span className="text-[11px] font-semibold text-white/82">任务视图</span>
        <span className="canvas-minimap-caption__hint text-[10px] text-white/38">拖动 · 滚轮缩放</span>
      </div>
      <div
        className="canvas-minimap-actions nopan nowheel pointer-events-auto absolute z-[10002] flex items-center gap-0.5 rounded-[10px] border border-white/[0.08] bg-black/48 p-0.5 shadow-[0_8px_24px_rgba(0,0,0,0.3)] backdrop-blur-xl"
        role="toolbar"
        aria-label="任务视图控制"
        onPointerDown={(event) => event.stopPropagation()}
      >
        {expanded ? (
          <button
            type="button"
            onClick={() => setBookmarksVisible((visible) => !visible)}
            className={`flex h-7 w-7 items-center justify-center rounded-lg transition hover:bg-white/[0.09] hover:text-white active:scale-95 ${
              bookmarksVisible ? 'bg-white/[0.1] text-white' : 'text-white/66'
            }`}
            aria-label={bookmarksVisible ? '收起视口书签' : '展开视口书签'}
            aria-pressed={bookmarksVisible}
            title={bookmarksVisible ? '收起视口书签' : '展开视口书签'}
          >
            <Bookmark className="h-3.5 w-3.5" />
          </button>
        ) : null}
        <button
          type="button"
          onClick={() => onExpandedChange(!expanded)}
          className="flex h-7 w-7 items-center justify-center rounded-lg text-white/66 transition hover:bg-white/[0.09] hover:text-white active:scale-95"
          aria-label={expanded ? '收起任务视图' : '放大任务视图'}
          title={expanded ? '收起任务视图' : '放大任务视图'}
        >
          {expanded ? <Minimize2 className="h-3.5 w-3.5" /> : <Maximize2 className="h-3.5 w-3.5" />}
        </button>
        <button
          type="button"
          onClick={onClose}
          className="flex h-7 w-7 items-center justify-center rounded-lg text-white/66 transition hover:bg-white/[0.09] hover:text-white active:scale-95"
          aria-label="关闭任务视图"
          title="关闭任务视图"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>
      {expanded && bookmarksVisible ? <CanvasMinimapBookmarksOverlay /> : null}
    </div>
  );
}
