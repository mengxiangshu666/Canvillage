// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useTranslation } from 'react-i18next';
import { MousePointerClick } from 'lucide-react';

/**
 * 空画布上的一行轻提示。
 *
 * 这里曾经是一张居中的「起步路线」大卡片（四条起手骨架 + 浏览全部），2026-09-30
 * 用户明确反馈「不要这个卡片，太影响体验了」—— 一张横在画布正中的卡片既挡视线、
 * 又占住用户想双击新建节点的位置。撤掉卡片后回到只教一次建节点方式的轻提示：
 * `pointer-events-none`，不拦截 Tab / 双击 / 框选。
 *
 * 起步路线本身没有消失：入口仍在右下角 dock 的起步器按钮上
 * （`CanvasQuickActionBar` → `CanvasStarterWorkflowPanel`），用户模板也走同一个面板，
 * 不再有第二处入口各画一份。
 */
export function EmptyCanvasStart() {
  const { t } = useTranslation();

  return (
    <div className="pointer-events-none absolute inset-0 z-[46] flex items-center justify-center">
      <div className="inline-flex h-9 items-center gap-2 rounded-[8px] border border-white/[0.09] bg-[#1d1d20]/96 px-3.5 shadow-[0_10px_28px_rgba(0,0,0,0.24)]">
        <MousePointerClick
          className="h-3.5 w-3.5 shrink-0 text-white/60"
          aria-hidden="true"
        />
        <span className="text-xs text-white/68">
          {t('canvas.emptyHintBeforeTab')}
          <span className="text-primary">Tab</span>
          {t('canvas.emptyHintAfterTab')}
        </span>
      </div>
    </div>
  );
}
