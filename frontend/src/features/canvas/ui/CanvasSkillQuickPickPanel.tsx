// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Search, Sparkles, X } from 'lucide-react';

import type { SkillDefinition, SkillProvider } from '@/features/freezone/context/skillRoles';
import {
  translateSkillDescription,
  translateSkillName,
} from '@/features/freezone/context/skillI18n';
import { CANVAS_TOOL_SURFACE_CLASS } from './canvas-node-menu-shared';

const PROVIDER_LABELS: Record<SkillProvider, string> = {
  freezone_mainline: '画布能力',
  agent: 'Agent 技能',
  tool: '工具能力',
  workflow: '工作流能力',
};

const CREATIVE_TERMS = /(导演|剧本|分镜|镜头|画面|风格|提示词|角色|视频|图像|光影|场景)/i;

function searchText(skill: SkillDefinition): string {
  return `${skill.display_name} ${skill.description} ${skill.id}`.toLocaleLowerCase();
}

/**
 * Show the skills that can act on this canvas first, then retain a stable,
 * searchable list of every available local skill. The ranking is deliberately
 * local-only: no marketplace data, remote popularity score, or hidden model
 * call is needed for the panel to be useful.
 */
export function rankCanvasQuickPickSkills(skills: readonly SkillDefinition[]): SkillDefinition[] {
  return [...skills].sort((left, right) => {
    const score = (skill: SkillDefinition): number => {
      const text = `${skill.display_name} ${skill.description}`;
      return (
        (skill.capabilities?.can_apply_canvas_patch ? 80 : 0) +
        (skill.capabilities?.can_propose_canvas_patch ? 40 : 0) +
        (skill.capabilities?.can_read_canvas ? 20 : 0) +
        (CREATIVE_TERMS.test(text) ? 12 : 0) +
        (skill.provider === 'agent' ? 6 : 0)
      );
    };

    const difference = score(right) - score(left);
    if (difference !== 0) {
      return difference;
    }
    return left.display_name.localeCompare(right.display_name, 'zh-CN');
  });
}

export function CanvasSkillQuickPickPanel({
  skillItems,
  onSelect,
  onClose,
}: {
  skillItems: SkillDefinition[];
  onSelect: (skill: SkillDefinition) => void;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const [query, setQuery] = useState('');
  const normalizedQuery = query.trim().toLocaleLowerCase();
  const matchingSkills = useMemo(() => {
    const ranked = rankCanvasQuickPickSkills(skillItems);
    const localized = ranked.map((skill) => ({
      skill,
      displayName: translateSkillName(skill, t),
      description: translateSkillDescription(skill, t),
    }));
    if (!normalizedQuery) return localized;
    return localized.filter(({ skill, displayName, description }) =>
      `${displayName} ${description} ${searchText(skill)}`
        .toLocaleLowerCase()
        .includes(normalizedQuery),
    );
  }, [normalizedQuery, skillItems, t]);

  return (
    <section
      className={`${CANVAS_TOOL_SURFACE_CLASS} w-[min(92vw,620px)] overflow-hidden`}
      role="dialog"
      aria-label="创作技能"
    >
      <div className="flex items-start justify-between gap-4 border-b border-white/[0.07] px-4 py-3">
        <div className="flex min-w-0 items-start gap-2.5">
          <span className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-lg border border-white/[0.09] bg-white/[0.045] text-white/68">
            <Sparkles className="size-3.5" />
          </span>
          <div>
            <h2 className="text-sm font-semibold text-white/90">创作技能</h2>
            <p className="mt-0.5 text-[11px] leading-4 text-white/45">
              优先展示可直接作用于当前画布的本地能力；点击即可插入真实技能节点。
            </p>
          </div>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="关闭创作技能"
          className="rounded-md p-1 text-white/45 transition-colors hover:bg-white/[0.08] hover:text-white/80"
        >
          <X className="size-4" />
        </button>
      </div>

      <label className="relative mx-3 mt-3 flex items-center" htmlFor="canvas-skill-search">
        <Search className="pointer-events-none absolute left-3 size-3.5 text-white/35" />
        <input
          id="canvas-skill-search"
          aria-label="搜索画布技能"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="搜索技能、用途或能力"
          className="h-9 w-full rounded-lg border border-white/[0.08] bg-white/[0.04] pl-8 pr-3 text-xs text-white/85 outline-none placeholder:text-white/30 focus:border-white/[0.18] focus:bg-white/[0.065]"
        />
      </label>

      <ul className="max-h-[min(48vh,390px)] overflow-y-auto p-3" aria-label="可用创作技能">
        {matchingSkills.length > 0 ? (
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            {matchingSkills.map(({ skill, displayName, description }) => (
              <li key={skill.id}>
                <button
                  type="button"
                  aria-label={`插入技能：${displayName}`}
                  onClick={() => onSelect(skill)}
                  className="group min-h-[82px] w-full rounded-xl border border-white/[0.07] bg-white/[0.02] p-3 text-left transition-colors hover:border-white/[0.14] hover:bg-white/[0.06]"
                >
                  <span className="mb-1.5 flex items-center justify-between gap-2">
                    <span className="truncate text-xs font-medium text-white/88 group-hover:text-white">
                      {displayName}
                    </span>
                    <span className="shrink-0 rounded-full border border-white/[0.08] bg-black/15 px-1.5 py-0.5 text-[9px] text-white/45">
                      {PROVIDER_LABELS[skill.provider]}
                    </span>
                  </span>
                  <span className="line-clamp-2 block text-[11px] leading-4 text-white/45 group-hover:text-white/62">
                    {description || skill.id}
                  </span>
                </button>
              </li>
            ))}
          </div>
        ) : (
          <div className="flex min-h-28 flex-col items-center justify-center gap-1 rounded-xl border border-dashed border-white/[0.10] bg-white/[0.02] px-4 text-center">
            <span className="text-xs text-white/65">没有匹配的本地技能</span>
            <span className="text-[11px] text-white/35">换一个关键词，或从「添加节点」查看完整分类。</span>
          </div>
        )}
      </ul>
    </section>
  );
}
