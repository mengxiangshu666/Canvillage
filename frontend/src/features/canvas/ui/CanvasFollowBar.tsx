// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { X } from 'lucide-react';

import { useCanvasFollowStore } from './canvasFollowStore';

/**
 * 跟随模式的顶部黄条。
 *
 * 跟随是一条「持续生效且改变视口行为」的状态 —— 不显式告诉用户在跟随谁、
 * 怎么退出，用户会觉得画布坏了（拖到哪都弹回同一个节点）。所以这条必须常驻，
 * 而且要能按 ESC 退出。
 */
export function CanvasFollowBar() {
  const { t } = useTranslation();
  const followedLabel = useCanvasFollowStore((state) => state.followedLabel);
  const followedNodeId = useCanvasFollowStore((state) => state.followedNodeId);
  const stopFollow = useCanvasFollowStore((state) => state.stopFollow);

  useEffect(() => {
    if (!followedNodeId) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      // 正在输入框里按 Esc（比如在搜索框里想清空）不该顺手退出跟随。
      if (event.target instanceof HTMLElement && event.target.closest('input, textarea, [contenteditable="true"]')) {
        return;
      }
      event.preventDefault();
      stopFollow();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [followedNodeId, stopFollow]);

  if (!followedNodeId) return null;

  return (
    <div className="pointer-events-auto absolute left-1/2 top-3 z-[130] flex h-9 -translate-x-1/2 items-center gap-2 rounded-[8px] border border-amber-300/25 bg-[#25221d]/97 px-3 text-[11px] font-medium text-amber-100 shadow-[0_8px_22px_rgba(0,0,0,0.3)]">
      <span className="relative flex h-1.5 w-1.5 shrink-0">
        <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-amber-900/50 motion-reduce:hidden" />
        <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-amber-300" />
      </span>
      <span className="max-w-[280px] truncate">
        {t('canvas.follow.banner', { name: followedLabel ?? '' })}
      </span>
      <button
        type="button"
        onClick={stopFollow}
        className="ml-0.5 inline-flex h-7 items-center rounded-[5px] bg-amber-200/10 px-2 text-[11px] font-semibold text-amber-100 transition-colors hover:bg-amber-200/16 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-200/40"
      >
        {t('canvas.follow.cancel')}
      </button>
      <button
        type="button"
        onClick={stopFollow}
        aria-label={t('canvas.follow.cancel')}
        className="flex h-7 w-7 items-center justify-center rounded-[5px] text-amber-100/70 transition-colors hover:bg-amber-200/12 hover:text-amber-50"
      >
        <X className="h-3 w-3" />
      </button>
    </div>
  );
}
