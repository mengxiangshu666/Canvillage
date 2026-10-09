// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useCallback, useMemo, useState } from 'react';
import { useReactFlow } from '@xyflow/react';
import { Crosshair, Trash2, Unlink, X } from 'lucide-react';

import { useCanvasStore } from '@/stores/canvasStore';
import { findOrphanNodes } from '@/features/canvas/application/orphanNodes';
import {
  CANVAS_CONTROL_GLASS_CLASS,
  CANVAS_CONTROL_ICON_BUTTON_CLASS,
} from './canvasControlStyles';

/**
 * 孤儿节点面板：一键列出画布上「没有任何连线」的节点，支持逐个定位与批量清理。
 *
 * 对应规格 `libtv爬取/DISTILL/analysis/B3_CONVERGENCE.md` G8。
 * 折叠态只显示计数徽标；展开后列出节点类型 + 摘要，点击可居中查看。
 * 容器节点（分组）不计入孤儿。
 */
export function CanvasOrphanNodesPanel() {
  const nodes = useCanvasStore((state) => state.nodes);
  const edges = useCanvasStore((state) => state.edges);
  const deleteNodes = useCanvasStore((state) => state.deleteNodes);
  const { setCenter, getNode } = useReactFlow();

  const [open, setOpen] = useState(false);
  const [confirmingClear, setConfirmingClear] = useState(false);

  const orphans = useMemo(() => findOrphanNodes(nodes, edges), [nodes, edges]);
  const count = orphans.length;

  const focusNode = useCallback(
    (id: string) => {
      const node = getNode(id);
      const width = node?.measured?.width ?? 220;
      const height = node?.measured?.height ?? 160;
      const position = node?.position ?? { x: 0, y: 0 };
      void setCenter(position.x + width / 2, position.y + height / 2, {
        zoom: 1,
        duration: 420,
      });
    },
    [getNode, setCenter],
  );

  const removeOne = useCallback(
    (id: string) => {
      deleteNodes([id]);
    },
    [deleteNodes],
  );

  const clearAll = useCallback(() => {
    deleteNodes(orphans.map((item) => item.id));
    setConfirmingClear(false);
    setOpen(false);
  }, [deleteNodes, orphans]);

  // 没有孤儿时不占位，保持画布干净。
  if (count === 0) return null;

  if (!open) {
    return (
      <button
        type="button"
        className={`nopan nowheel absolute left-3 top-24 z-[10000] flex items-center gap-1.5 rounded-full px-2.5 py-1.5 text-[11px] text-text/85 transition hover:text-text ${CANVAS_CONTROL_GLASS_CLASS}`}
        onClick={() => setOpen(true)}
        title="查看没有连线的孤立节点"
      >
        <Unlink className="h-3.5 w-3.5" />
        <span>孤立节点 {count}</span>
      </button>
    );
  }

  return (
    <div
      className={`nopan nowheel absolute left-3 top-24 z-[10000] flex max-h-[60vh] w-72 flex-col overflow-hidden rounded-lg ${CANVAS_CONTROL_GLASS_CLASS}`}
    >
      <header className="flex items-center justify-between border-b border-[var(--ui-border-soft)] px-3 py-2">
        <div className="flex items-center gap-1.5 text-[12px] font-medium text-text">
          <Unlink className="h-3.5 w-3.5" />
          <span>孤立节点</span>
          <span className="rounded-full bg-white/10 px-1.5 text-[10px] text-text/70">
            {count}
          </span>
        </div>
        <button
          type="button"
          className={CANVAS_CONTROL_ICON_BUTTON_CLASS}
          onClick={() => {
            setOpen(false);
            setConfirmingClear(false);
          }}
          aria-label="关闭"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </header>

      <p className="px-3 pt-2 text-[10px] leading-relaxed text-text/55">
        这些节点没有任何连线，通常是改图后留下的废弃节点。可逐个定位确认，或整批清理。
      </p>

      <ul className="mt-1.5 flex-1 overflow-y-auto px-1.5 pb-1.5">
        {orphans.map((orphan) => (
          <li
            key={orphan.id}
            className="group flex items-center gap-2 rounded-md px-1.5 py-1.5 transition hover:bg-white/[0.06]"
          >
            <button
              type="button"
              className="flex min-w-0 flex-1 items-center gap-2 text-left"
              onClick={() => focusNode(orphan.id)}
              title="在画布中定位"
            >
              <Crosshair className="h-3.5 w-3.5 shrink-0 text-text/45" />
              <span className="min-w-0 flex-1">
                <span className="block truncate text-[11px] text-text/90">
                  {orphan.summary || '(无描述)'}
                </span>
                <span className="block text-[10px] text-text/45">
                  {orphan.typeLabel}
                </span>
              </span>
            </button>
            <button
              type="button"
              className={`${CANVAS_CONTROL_ICON_BUTTON_CLASS} shrink-0 opacity-0 transition group-hover:opacity-100`}
              onClick={() => removeOne(orphan.id)}
              aria-label="删除该节点"
              title="删除该节点"
            >
              <Trash2 className="h-3.5 w-3.5" />
            </button>
          </li>
        ))}
      </ul>

      <footer className="border-t border-[var(--ui-border-soft)] px-3 py-2">
        {confirmingClear ? (
          <div className="flex items-center justify-between gap-2">
            <span className="text-[11px] text-text/70">
              删除全部 {count} 个孤立节点？
            </span>
            <span className="flex items-center gap-1">
              <button
                type="button"
                className="rounded px-2 py-1 text-[11px] text-rose-300 transition hover:bg-rose-500/15"
                onClick={clearAll}
              >
                确认删除
              </button>
              <button
                type="button"
                className="rounded px-2 py-1 text-[11px] text-text/60 transition hover:bg-white/10"
                onClick={() => setConfirmingClear(false)}
              >
                取消
              </button>
            </span>
          </div>
        ) : (
          <button
            type="button"
            className="w-full rounded px-2 py-1 text-[11px] text-text/70 transition hover:bg-white/10 hover:text-text"
            onClick={() => setConfirmingClear(true)}
          >
            一键清理全部
          </button>
        )}
      </footer>
    </div>
  );
}
