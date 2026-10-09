// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ChevronRight, Sparkles } from 'lucide-react';

import type { CanvasNodeType } from '@/features/canvas/domain/canvasNodes';
import type { SkillDefinition, SkillProvider } from '@/features/freezone/context/skillRoles';
import {
  translateSkillDescription,
  translateSkillName,
} from '@/features/freezone/context/skillI18n';
import {
  CanvasAddNodeGrid,
  CanvasAddNodeSearch,
  CanvasMenuSectionHeader,
  CANVAS_MENU_DIVIDER_CLASS,
  CANVAS_MENU_ICON_CLASS,
  CANVAS_MENU_ROW_CLASS,
  CANVAS_TOOL_SURFACE_CLASS,
  useCanvasAddNodeSearch,
} from '@/features/canvas/ui/canvas-node-menu-shared';

const skillProviderLabels: Record<SkillProvider, string> = {
  freezone_mainline: '主线技能',
  agent: 'Agent 技能',
  tool: '工具技能',
  workflow: '工作流技能',
};

const skillProviderOrder: SkillProvider[] = ['freezone_mainline', 'agent', 'tool', 'workflow'];
const hiddenSkillIds = new Set(['agent.review_frame']);
const SKILL_PANEL_CLOSE_DELAY_MS = 40;

interface CanvasAddNodePanelProps {
  skillItems: SkillDefinition[];
  onSelectNode: (type: CanvasNodeType) => void;
  onSelectSkill: (skill: SkillDefinition) => void;
  onClose: () => void;
}

export function CanvasAddNodePanel({
  skillItems,
  onSelectNode,
  onSelectSkill,
  onClose,
}: CanvasAddNodePanelProps) {
  const { t } = useTranslation();
  const [activeSkillProvider, setActiveSkillProvider] = useState<SkillProvider | null>(null);
  const { query: nodeQuery, setQuery: setNodeQuery, visibleTypes: visibleNodeTypes } =
    useCanvasAddNodeSearch();
  const panelRootRef = useRef<HTMLDivElement>(null);
  const skillRowsRef = useRef<HTMLDivElement>(null);
  const skillPanelRef = useRef<HTMLDivElement>(null);
  const skillPanelCloseTimerRef = useRef<number | null>(null);

  const skillGroups = useMemo(() => {
    if (!skillItems || skillItems.length === 0) {
      return [];
    }
    const byProvider = new Map<SkillProvider, SkillDefinition[]>();
    for (const provider of skillProviderOrder) {
      byProvider.set(provider, []);
    }
    for (const skill of skillItems) {
      if (hiddenSkillIds.has(skill.id)) {
        continue;
      }
      byProvider.get(skill.provider)?.push(skill);
    }
    return skillProviderOrder
      .map((provider) => ({ provider, items: byProvider.get(provider) ?? [] }))
      .filter((group) => group.items.length > 0);
  }, [skillItems]);

  const activeSkillGroup = useMemo(() => {
    if (!activeSkillProvider) {
      return null;
    }
    return skillGroups.find((group) => group.provider === activeSkillProvider) ?? null;
  }, [activeSkillProvider, skillGroups]);

  const handlePickNode = (type: CanvasNodeType) => {
    onSelectNode(type);
    onClose();
  };

  const handlePickSkill = (skill: SkillDefinition) => {
    onSelectSkill(skill);
    onClose();
  };

  const cancelSkillPanelClose = useCallback(() => {
    if (skillPanelCloseTimerRef.current !== null) {
      window.clearTimeout(skillPanelCloseTimerRef.current);
      skillPanelCloseTimerRef.current = null;
    }
  }, []);

  const scheduleSkillPanelClose = useCallback(() => {
    cancelSkillPanelClose();
    skillPanelCloseTimerRef.current = window.setTimeout(() => {
      setActiveSkillProvider(null);
      skillPanelCloseTimerRef.current = null;
    }, SKILL_PANEL_CLOSE_DELAY_MS);
  }, [cancelSkillPanelClose]);

  useEffect(() => {
    return () => {
      cancelSkillPanelClose();
    };
  }, [cancelSkillPanelClose]);

  useEffect(() => {
    if (!activeSkillProvider) {
      return;
    }

    const isPointInside = (element: HTMLElement | null, x: number, y: number) => {
      if (!element) {
        return false;
      }
      const rect = element.getBoundingClientRect();
      return x >= rect.left && x <= rect.right && y >= rect.top && y <= rect.bottom;
    };

    const handlePointerMove = (event: PointerEvent) => {
      // 只有停在技能行列表或右侧弹窗上才保持打开；滑到主面板里的其它菜单项
      // (节点网格等)应当关闭弹窗，而不是因为「还在主面板内」就一直挂着。
      const insideSkillRows = isPointInside(skillRowsRef.current, event.clientX, event.clientY);
      const insideSkillPanel = isPointInside(skillPanelRef.current, event.clientX, event.clientY);
      if (insideSkillRows || insideSkillPanel) {
        cancelSkillPanelClose();
        return;
      }
      scheduleSkillPanelClose();
    };

    document.addEventListener('pointermove', handlePointerMove, true);
    return () => {
      document.removeEventListener('pointermove', handlePointerMove, true);
    };
  }, [activeSkillProvider, cancelSkillPanelClose, scheduleSkillPanelClose]);

  return (
    <div
      ref={panelRootRef}
      className="relative"
      onPointerEnter={cancelSkillPanelClose}
      onPointerLeave={scheduleSkillPanelClose}
    >
      <div
        className={`${CANVAS_TOOL_SURFACE_CLASS} w-[240px] max-w-[calc(100vw-24px)] overflow-hidden`}
      >
        <div className="ui-scrollbar max-h-[min(510px,70vh)] overflow-y-auto p-2.5 [scrollbar-gutter:stable]">
          <CanvasAddNodeSearch value={nodeQuery} onChange={setNodeQuery} />
          <CanvasMenuSectionHeader label={t('node.menu.sectionAddNode')} className="px-2 pb-2 pt-1" />
          <CanvasAddNodeGrid
            onSelectNode={handlePickNode}
            visibleTypes={visibleNodeTypes}
            emptyLabel={t('node.menu.searchEmpty')}
          />

          {skillGroups.length > 0 && (
            <>
              <div className={`${CANVAS_MENU_DIVIDER_CLASS} my-2`} />
              <div ref={skillRowsRef} className="flex flex-col gap-0.5">
                {skillGroups.map((group) => (
                  <div key={group.provider}>
                    <button
                      type="button"
                      className={`${CANVAS_MENU_ROW_CLASS} ${
                        activeSkillProvider === group.provider ? 'canvas-tool-row-active' : ''
                      }`}
                      onMouseEnter={() => {
                        cancelSkillPanelClose();
                        setActiveSkillProvider(group.provider);
                      }}
                      onFocus={() => {
                        cancelSkillPanelClose();
                        setActiveSkillProvider(group.provider);
                      }}
                      onClick={() => setActiveSkillProvider(group.provider)}
                    >
                      <Sparkles className={CANVAS_MENU_ICON_CLASS} aria-hidden="true" />
                      <span className="min-w-0 flex-1 truncate">{skillProviderLabels[group.provider]}</span>
                      <span className="canvas-tool-meta">{group.items.length}</span>
                      <ChevronRight
                        className="h-3.5 w-3.5 shrink-0 text-white/30"
                      />
                    </button>
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
      </div>
      {activeSkillGroup && (
        <div
          ref={skillPanelRef}
          className={`${CANVAS_TOOL_SURFACE_CLASS} absolute left-[calc(100%+8px)] top-0 w-[336px] max-w-[calc(100vw-24px)] overflow-hidden`}
          onPointerEnter={cancelSkillPanelClose}
          onPointerLeave={scheduleSkillPanelClose}
        >
          <div className="canvas-tool-section-label px-3 pb-2 pt-3">
            {skillProviderLabels[activeSkillGroup.provider]}
          </div>
          <div className="ui-scrollbar max-h-[440px] overflow-y-auto px-2 pb-2 [scrollbar-gutter:stable]">
            {activeSkillGroup.items.map((skill) => (
              <button
                key={skill.id}
                type="button"
                className={`${CANVAS_MENU_ROW_CLASS} min-h-[52px] items-start py-2`}
                onClick={() => handlePickSkill(skill)}
              >
                <Sparkles className={`${CANVAS_MENU_ICON_CLASS} mt-0.5`} aria-hidden="true" />
                <div className="min-w-0 flex-1">
                  <div className="truncate text-[13px] leading-5 text-white/88">
                    {translateSkillName(skill, t)}
                  </div>
                  <div className="line-clamp-2 text-[11px] leading-4 text-white/38">
                    {translateSkillDescription(skill, t) || skill.id}
                  </div>
                </div>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
