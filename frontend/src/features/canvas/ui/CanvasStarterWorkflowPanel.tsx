// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import {
  BookOpenCheck,
  Bookmark,
  Clapperboard,
  Compass,
  Film,
  Grid3X3,
  Layers3,
  Loader2,
  Music,
  Sparkles,
  Trash2,
  type LucideIcon,
} from 'lucide-react';
import { useMemo, useState } from 'react';

import type { CanvasUserTemplateSummary } from '@/api/canvas';
import {
  CANVAS_STARTER_WORKFLOWS,
  type CanvasStarterWorkflowIcon,
  type CanvasStarterWorkflowId,
} from '@/features/canvas/application/starterWorkflows';
import {
  CANVAS_STARTER_WORKFLOW_CATEGORIES,
  starterWorkflowPresentation,
  type CanvasStarterWorkflowCategory,
} from '@/features/canvas/application/starterWorkflowCatalog';
import { CANVAS_TOOL_SURFACE_CLASS } from './canvas-node-menu-shared';

const ICON_BY_WORKFLOW: Record<CanvasStarterWorkflowIcon, LucideIcon> = {
  bookOpen: BookOpenCheck,
  film: Film,
  layers: Layers3,
  sparkles: Sparkles,
  grid: Grid3X3,
  clapperboard: Clapperboard,
  compass: Compass,
  music: Music,
};

export function CanvasStarterWorkflowPanel({
  onSelect,
  onClose,
  userTemplates = [],
  userTemplatesLoading = false,
  onSelectUserTemplate,
  onDeleteUserTemplate,
}: {
  onSelect: (workflowId: CanvasStarterWorkflowId) => void;
  onClose: () => void;
  userTemplates?: readonly CanvasUserTemplateSummary[];
  userTemplatesLoading?: boolean;
  onSelectUserTemplate?: (templateId: string) => void;
  onDeleteUserTemplate?: (templateId: string) => void;
}) {
  const [category, setCategory] = useState<
    CanvasStarterWorkflowCategory | 'all' | 'mine'
  >('all');
  const workflows = useMemo(
    () => CANVAS_STARTER_WORKFLOWS.filter((workflow) => (
      category !== 'mine'
      && (category === 'all' || starterWorkflowPresentation(workflow.id).category === category)
    )),
    [category],
  );

  return (
    <section
      className={`neo-canvas-starter-panel ${CANVAS_TOOL_SURFACE_CLASS} w-[min(92vw,620px)] overflow-hidden`}
      role="dialog"
      aria-label="工作流起步器"
    >
      <div className="flex items-start justify-between gap-4 border-b border-white/[0.07] px-4 py-3">
        <div>
          <h2 className="text-sm font-semibold text-white/90">工作流起步器</h2>
          <p className="mt-0.5 text-[11px] leading-4 text-white/45">
            先看素材与模型契约，再插入本地可编辑节点图；不会直接生成。
          </p>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="rounded-md px-1.5 py-1 text-[11px] text-white/45 transition-colors hover:bg-white/[0.08] hover:text-white/80"
        >
          关闭
        </button>
      </div>
      <div className="flex gap-1.5 overflow-x-auto border-b border-white/[0.06] px-3 py-2 scrollbar-none">
        <button
          type="button"
          onClick={() => setCategory('mine')}
          className={`shrink-0 rounded-full px-2.5 py-1 text-[10px] transition-colors ${
            category === 'mine'
              ? 'bg-white/[0.13] text-white'
              : 'bg-white/[0.035] text-white/50 hover:bg-white/[0.075] hover:text-white/80'
          }`}
        >
          我的模板{userTemplates.length > 0 ? ` ${userTemplates.length}` : ''}
        </button>
        <button
          type="button"
          onClick={() => setCategory('all')}
          className={`shrink-0 rounded-full px-2.5 py-1 text-[10px] transition-colors ${
            category === 'all'
              ? 'bg-white/[0.13] text-white'
              : 'bg-white/[0.035] text-white/50 hover:bg-white/[0.075] hover:text-white/80'
          }`}
        >
          全部
        </button>
        {CANVAS_STARTER_WORKFLOW_CATEGORIES.map((item) => (
          <button
            key={item.id}
            type="button"
            onClick={() => setCategory(item.id)}
            className={`shrink-0 rounded-full px-2.5 py-1 text-[10px] transition-colors ${
              category === item.id
                ? 'bg-white/[0.13] text-white'
                : 'bg-white/[0.035] text-white/50 hover:bg-white/[0.075] hover:text-white/80'
            }`}
          >
            {item.label}
          </button>
        ))}
      </div>
      {category === 'mine' ? (
        <>
          {userTemplatesLoading ? (
            <div className="flex min-h-[180px] items-center justify-center gap-2 text-[11px] text-white/45">
              <Loader2 className="size-3.5 animate-spin" />
              正在读取我的模板
            </div>
          ) : userTemplates.length === 0 ? (
            <div className="flex min-h-[180px] flex-col items-center justify-center gap-2 px-4 text-center">
              <Bookmark className="size-5 text-white/28" />
              <div className="text-[12px] text-white/62">还没有保存的模板</div>
              <div className="max-w-[300px] text-[10px] leading-4 text-white/35">
                在画布上多选节点后，点工具栏里的「存为模板」。
              </div>
            </div>
          ) : (
            <div className="grid max-h-[min(60vh,520px)] grid-cols-1 gap-2 overflow-y-auto p-3 sm:grid-cols-2">
              {userTemplates.map((template) => (
                <div
                  key={template.id}
                  className="neo-canvas-starter-card group relative min-h-[86px] rounded-xl border border-white/[0.07] bg-white/[0.02] transition-colors hover:border-white/[0.14] hover:bg-white/[0.06]"
                >
                  <button
                    type="button"
                    onClick={() => onSelectUserTemplate?.(template.id)}
                    disabled={!onSelectUserTemplate}
                    className="flex h-full w-full items-start gap-3 p-3 pr-10 text-left disabled:cursor-not-allowed disabled:opacity-55"
                  >
                    <span className="neo-canvas-starter-icon flex size-8 shrink-0 items-center justify-center rounded-lg border border-white/[0.09] bg-white/[0.045] text-white/68">
                      <Bookmark className="size-4" />
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-[12px] font-medium text-white/88 group-hover:text-white">
                        {template.title}
                      </span>
                      <span className="mt-1 line-clamp-2 block text-[11px] leading-4 text-white/45 group-hover:text-white/60">
                        {template.description || '无说明'}
                      </span>
                      <span className="mt-2 block text-[10px] text-white/38">
                        {template.node_count} 个节点 · {template.edge_count} 条连线
                      </span>
                    </span>
                  </button>
                  {onDeleteUserTemplate ? (
                    <button
                      type="button"
                      onClick={() => onDeleteUserTemplate(template.id)}
                      className="absolute right-2 top-2 z-10 rounded-md p-1.5 text-white/35 opacity-0 transition-colors hover:bg-rose-400/[0.10] hover:text-rose-200 group-hover:opacity-100 focus:opacity-100"
                      aria-label={`删除模板 ${template.title}`}
                      title="删除模板"
                    >
                      <Trash2 className="size-3.5" />
                    </button>
                  ) : null}
                </div>
              ))}
            </div>
          )}
        </>
      ) : (
        <div className="grid max-h-[min(60vh,520px)] grid-cols-1 gap-2 overflow-y-auto p-3 sm:grid-cols-2">
          {workflows.map((workflow) => {
            const Icon = ICON_BY_WORKFLOW[workflow.icon];
            const presentation = starterWorkflowPresentation(workflow.id);
            return (
              <button
                key={workflow.id}
                type="button"
                onClick={() => onSelect(workflow.id)}
                className="neo-canvas-starter-card group flex min-h-[86px] items-start gap-3 rounded-xl border border-white/[0.07] bg-white/[0.02] p-3 text-left transition-colors hover:border-white/[0.14] hover:bg-white/[0.06]"
              >
                <span className="neo-canvas-starter-icon flex size-8 shrink-0 items-center justify-center rounded-lg border border-white/[0.09] bg-white/[0.045] text-white/68">
                  <Icon className="size-4" />
                </span>
                <span className="min-w-0 flex-1">
                  {/* 徽标（模型提示）长短不一：放不下时换到标题下一行，绝不许挤压标题——
                      标题一旦被压到 min-content（中文只有一个字宽）就会竖排成一条线。 */}
                  <span className="flex flex-wrap items-center justify-between gap-x-2 gap-y-1">
                    <span className="min-w-0 truncate text-[12px] font-medium text-white/88 group-hover:text-white">
                      {workflow.title}
                    </span>
                    <span className="max-w-full shrink-0 truncate rounded bg-white/[0.06] px-1.5 py-0.5 text-[9px] text-white/45">
                      {presentation.modelHint}
                    </span>
                  </span>
                  <span className="mt-1 block text-[11px] leading-4 text-white/45 group-hover:text-white/60">
                    {workflow.description}
                  </span>
                  <span className="mt-2 block truncate text-[10px] text-white/48">
                    需要：{presentation.inputSummary}
                  </span>
                </span>
              </button>
            );
          })}
        </div>
      )}
    </section>
  );
}
