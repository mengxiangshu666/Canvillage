// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useEffect, useRef, useState, type ComponentType } from 'react';
import { useTranslation } from 'react-i18next';
import { Clock, Hand, Keyboard, MousePointer2, Plus, Settings2, Sparkles, WandSparkles } from 'lucide-react';

import type { CanvasNodeType } from '@/features/canvas/domain/canvasNodes';
import type { CanvasAsset } from '@/features/canvas/domain/canvasAssets';
import type { SkillDefinition } from '@/features/freezone/context/skillRoles';
import type { CanvasUserTemplateSummary } from '@/api/canvas';
import type { CanvasStarterWorkflowId } from '@/features/canvas/application/starterWorkflows';

import { CanvasAddNodePanel } from './CanvasAddNodePanel';
import { CanvasShortcutsPanel } from './CanvasShortcutsPanel';
import { CanvasHistoryAssetsModal } from './CanvasHistoryAssetsModal';
import { CanvasToolMenu } from './CanvasToolMenu';
import { CanvasStarterWorkflowPanel } from './CanvasStarterWorkflowPanel';
import { CanvasSkillQuickPickPanel } from './CanvasSkillQuickPickPanel';
import { CanvasSettingsPanel } from './CanvasSettingsPanel';
import { useCanvasToolStore } from './canvasToolStore';

type QuickPanel = 'add' | 'skills' | 'starter' | 'tool' | 'history' | 'shortcuts' | 'settings';

// 悬停即开 / 离开延迟关闭的轻量 popover 面板（区别于 history 那种 modal）。
const HOVER_POPOVER_PANELS: ReadonlySet<QuickPanel> = new Set(['add']);
const ANCHORED_POPOVER_PANELS: ReadonlySet<QuickPanel> = new Set([
  'add',
  'skills',
  'starter',
  'tool',
  'shortcuts',
  'settings',
]);

interface CanvasQuickActionBarProps {
  placement?: 'bottom-right' | 'top-right';
  skillItems: SkillDefinition[];
  onAddNode: (type: CanvasNodeType) => void;
  onAddSkill: (skill: SkillDefinition) => void;
  onUseStarterWorkflow: (workflowId: CanvasStarterWorkflowId) => void;
  userTemplates?: readonly CanvasUserTemplateSummary[];
  userTemplatesLoading?: boolean;
  onUseUserTemplate?: (templateId: string) => void;
  onDeleteUserTemplate?: (templateId: string) => void;
  onUseAsset: (asset: CanvasAsset) => void;
  onDeleteNode: (nodeId: string) => void;
}

interface QuickActionDef {
  key: QuickPanel;
  icon: ComponentType<{ className?: string }>;
  labelKey?: string;
  tooltipKey?: string;
  /** Rendered as the always-filled white primary button (libtv "+" style). */
  primary?: boolean;
}

const ACTIONS: QuickActionDef[] = [
  { key: 'add', icon: Plus, labelKey: 'canvas.quickbar.addNode', primary: true },
  { key: 'skills', icon: Sparkles, labelKey: 'canvas.quickbar.skills', tooltipKey: 'canvas.quickbar.skillsTooltip' },
  { key: 'starter', icon: WandSparkles, labelKey: 'canvas.quickbar.starter', tooltipKey: 'canvas.quickbar.starterTooltip' },
  { key: 'tool', icon: MousePointer2, labelKey: 'canvas.toolbar.toolGroupLabel' },
  {
    key: 'history',
    icon: Clock,
    labelKey: 'canvas.quickbar.history',
    tooltipKey: 'canvas.quickbar.history',
  },
  {
    key: 'shortcuts',
    icon: Keyboard,
    labelKey: 'canvas.quickbar.shortcuts',
    tooltipKey: 'canvas.quickbar.shortcuts',
  },
  {
    key: 'settings',
    icon: Settings2,
    labelKey: 'canvas.quickbar.settings',
    tooltipKey: 'canvas.quickbar.settingsTooltip',
  },
];

export function CanvasQuickActionBar({
  placement = 'bottom-right',
  skillItems,
  onAddNode,
  onAddSkill,
  onUseStarterWorkflow,
  userTemplates = [],
  userTemplatesLoading = false,
  onUseUserTemplate,
  onDeleteUserTemplate,
  onUseAsset,
  onDeleteNode,
}: CanvasQuickActionBarProps) {
  const { t } = useTranslation();
  const [openPanel, setOpenPanel] = useState<QuickPanel | null>(null);
  const popoverCloseTimerRef = useRef<number | null>(null);
  const isTop = placement === 'top-right';
  const handToolActive = useCanvasToolStore((state) => state.tool === 'hand');

  const cancelPopoverClose = () => {
    if (popoverCloseTimerRef.current !== null) {
      window.clearTimeout(popoverCloseTimerRef.current);
      popoverCloseTimerRef.current = null;
    }
  };

  const schedulePopoverClose = () => {
    cancelPopoverClose();
    popoverCloseTimerRef.current = window.setTimeout(() => {
      setOpenPanel((current) => (current && HOVER_POPOVER_PANELS.has(current) ? null : current));
      popoverCloseTimerRef.current = null;
    }, 120);
  };

  useEffect(() => {
    return () => cancelPopoverClose();
  }, []);

  const toggle = (panel: QuickPanel) => {
    setOpenPanel((current) => (current === panel ? null : panel));
  };

  const handleActionClick = (action: QuickActionDef) => {
    const panel = action.key;
    if (HOVER_POPOVER_PANELS.has(panel)) {
      cancelPopoverClose();
      setOpenPanel(panel);
      return;
    }
    toggle(panel);
  };

  const handleActionHover = (panel: QuickPanel) => {
    if (!HOVER_POPOVER_PANELS.has(panel)) {
      return;
    }
    cancelPopoverClose();
    setOpenPanel(panel);
  };

  const hasPopover = openPanel != null && ANCHORED_POPOVER_PANELS.has(openPanel);
  // Anchor the popovers above the bar (or below it when the chrome lives at the
  // top), opening upward toward the canvas.
  const popoverAnchorClass = isTop ? 'top-full mt-3' : 'bottom-full mb-3';
  const popoverEnterClass = `animate-in fade-in-0 zoom-in-95 duration-150 ease-out motion-reduce:animate-none ${
    isTop ? 'slide-in-from-top-1' : 'slide-in-from-bottom-1'
  }`;

  return (
    <>
      {/*
        Click-away layer for the anchored popovers. Kept OUT of the centered bar
        wrapper below: that wrapper would carry no transform, but the shortcuts
        popover uses `-translate-x-1/2`, and a `fixed` backdrop nested under any
        transformed ancestor is positioned relative to it instead of the
        viewport — so the backdrop lives here at the fragment root.
      */}
      {hasPopover && (
        <div className="fixed inset-0 z-[40]" onClick={() => setOpenPanel(null)} />
      )}

      <div
        className={`canvas-quick-action-dock pointer-events-none absolute inset-x-0 z-[41] flex justify-center ${
          isTop ? 'top-3' : 'bottom-3'
        }`}
      >
        <div
          className="nopan nowheel pointer-events-auto relative"
          onPointerEnter={cancelPopoverClose}
          onPointerLeave={() => {
            if (openPanel != null && HOVER_POPOVER_PANELS.has(openPanel)) {
              schedulePopoverClose();
            }
          }}
          onPointerDown={(event) => event.stopPropagation()}
        >
          {openPanel === 'add' && (
            <div
              className={`absolute left-0 ${popoverAnchorClass}`}
              onPointerEnter={cancelPopoverClose}
              onPointerLeave={schedulePopoverClose}
            >
              <div className={popoverEnterClass}>
                <CanvasAddNodePanel
                  skillItems={skillItems}
                  onSelectNode={onAddNode}
                  onSelectSkill={onAddSkill}
                  onClose={() => setOpenPanel(null)}
                />
              </div>
            </div>
          )}

          {openPanel === 'starter' && (
            <div className={`absolute left-1/2 -translate-x-1/2 ${popoverAnchorClass}`}>
              <div className={popoverEnterClass}>
                <CanvasStarterWorkflowPanel
                  onSelect={(workflowId) => {
                    onUseStarterWorkflow(workflowId);
                    setOpenPanel(null);
                  }}
                  userTemplates={userTemplates}
                  userTemplatesLoading={userTemplatesLoading}
                  onSelectUserTemplate={(templateId) => {
                    onUseUserTemplate?.(templateId);
                    setOpenPanel(null);
                  }}
                  onDeleteUserTemplate={onDeleteUserTemplate}
                  onClose={() => setOpenPanel(null)}
                />
              </div>
            </div>
          )}

          {openPanel === 'skills' && (
            <div className={`absolute left-1/2 -translate-x-1/2 ${popoverAnchorClass}`}>
              <div className={popoverEnterClass}>
                <CanvasSkillQuickPickPanel
                  skillItems={skillItems}
                  onSelect={(skill) => {
                    onAddSkill(skill);
                    setOpenPanel(null);
                  }}
                  onClose={() => setOpenPanel(null)}
                />
              </div>
            </div>
          )}

          {openPanel === 'shortcuts' && (
            <div className={`absolute left-1/2 -translate-x-1/2 ${popoverAnchorClass}`}>
              <div className={popoverEnterClass}>
                <CanvasShortcutsPanel onClose={() => setOpenPanel(null)} />
              </div>
            </div>
          )}

          {openPanel === 'settings' && (
            <div className={`absolute right-0 ${popoverAnchorClass}`}>
              <div className={popoverEnterClass}>
                <CanvasSettingsPanel onClose={() => setOpenPanel(null)} />
              </div>
            </div>
          )}

          <div
            className="neo-canvas-quickbar canvas-dock-surface canvas-quick-action-surface flex items-center"
            data-placement={placement}
          >
            {ACTIONS.map((action) => {
              const { key, labelKey, tooltipKey, primary } = action;
              const active = openPanel === key;
              const Icon = key === 'tool' ? (handToolActive ? Hand : MousePointer2) : action.icon;
              const actionLabel = labelKey ? t(labelKey) : key;
              const tooltip =
                key === 'tool'
                  ? handToolActive
                    ? `${t('canvas.toolbar.toolHand')} H`
                    : `${t('canvas.toolbar.toolMove')} V`
                  : tooltipKey ? t(tooltipKey) : undefined;
              const filled = active || (key === 'tool' && handToolActive);
              return (
                <span
                  key={key}
                  className="canvas-dock-item group relative inline-flex"
                  data-panel={key}
                  data-active={filled ? 'true' : 'false'}
                  data-primary={primary ? 'true' : 'false'}
                >
                  <button
                    type="button"
                    onMouseEnter={() => handleActionHover(key)}
                    onFocus={() => handleActionHover(key)}
                    onClick={() => handleActionClick(action)}
                    aria-label={actionLabel}
                    aria-pressed={filled}
                    data-active={filled ? 'true' : 'false'}
                    data-primary={primary ? 'true' : 'false'}
                    className="canvas-dock-button flex items-center justify-center"
                  >
                    <Icon
                      className={`h-[18px] w-[18px] ${
                        primary
                          ? 'transition-transform duration-150 ease-out motion-reduce:transition-none group-hover:scale-110 motion-reduce:group-hover:scale-100'
                          : ''
                      }`}
                    />
                  </button>
                  {tooltip && !active && (
                    <span
                      className={`canvas-tool-surface pointer-events-none absolute left-1/2 z-20 -translate-x-1/2 whitespace-nowrap rounded-[6px] border px-2 py-1 text-[11px] leading-none text-white/78 opacity-0 transition-opacity duration-150 group-hover:opacity-100 group-focus-within:opacity-100 ${
                        isTop ? 'top-full mt-2' : 'bottom-full mb-2'
                      }`}
                    >
                      {tooltip}
                    </span>
                  )}
                  {key === 'tool' && active && (
                    <div className={`absolute left-0 z-20 ${popoverAnchorClass}`}>
                      <div className={popoverEnterClass}>
                        <CanvasToolMenu onSelect={() => setOpenPanel(null)} />
                      </div>
                    </div>
                  )}
                </span>
              );
            })}
          </div>
        </div>
      </div>

      {openPanel === 'history' && (
        <CanvasHistoryAssetsModal
          onClose={() => setOpenPanel(null)}
          onUseAsset={onUseAsset}
          onDeleteNode={onDeleteNode}
        />
      )}
    </>
  );
}
