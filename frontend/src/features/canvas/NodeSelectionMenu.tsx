// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useMemo, useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  ChevronRight,
  FileText,
  Globe,
  Image,
  Music,
  Orbit,
  Sparkles,
  Video,
  Type,
} from 'lucide-react';
import { UI_POPOVER_TRANSITION_MS } from '@/components/ui/motion';

import {
  CANVAS_NODE_TYPES,
  type CanvasNodeType,
} from '@/features/canvas/domain/canvasNodes';
import type { SkillDefinition, SkillProvider } from '@/features/freezone/context/skillRoles';
import {
  translateSkillDescription,
  translateSkillName,
} from '@/features/freezone/context/skillI18n';
import {
  CanvasAddNodeGrid,
  CanvasMenuSectionHeader,
  CANVAS_MENU_DIVIDER_CLASS,
  CANVAS_MENU_ICON_CLASS,
  CANVAS_MENU_ROW_CLASS,
  CANVAS_TOOL_SURFACE_CLASS,
} from '@/features/canvas/ui/canvas-node-menu-shared';

interface NodeSelectionMenuProps {
  position: { x: number; y: number };
  allowedTypes?: CanvasNodeType[];
  onSelect: (type: CanvasNodeType, clientPosition?: { x: number; y: number }) => void;
  skillItems?: SkillDefinition[];
  onSelectSkill?: (skill: SkillDefinition) => void;
  onClose: () => void;
}

const skillProviderLabels: Record<SkillProvider, string> = {
  freezone_mainline: '主线技能',
  agent: 'Agent 技能',
  tool: '工具技能',
  workflow: '工作流技能',
};

const skillProviderOrder: SkillProvider[] = ['freezone_mainline', 'agent', 'tool', 'workflow'];

const hiddenSkillIds = new Set(['agent.review_frame']);

const SKILL_PANEL_CLOSE_DELAY_MS = 40;
const MENU_VIEWPORT_MARGIN = 12;
const SKILL_PANEL_GAP = 8;

interface ReferenceGenerateAction {
  key: string;
  label: string;
  Icon: typeof Image;
  type?: CanvasNodeType;
  disabled?: boolean;
  beta?: boolean;
}

export function NodeSelectionMenu({
  position,
  allowedTypes,
  onSelect,
  skillItems,
  onSelectSkill,
  onClose,
}: NodeSelectionMenuProps) {
  const { t } = useTranslation();
  const menuRef = useRef<HTMLDivElement>(null);
  const mainPanelRef = useRef<HTMLDivElement>(null);
  const skillPanelRef = useRef<HTMLDivElement>(null);
  const [isVisible, setIsVisible] = useState(false);
  const [isPositioned, setIsPositioned] = useState(false);
  const [panelPosition, setPanelPosition] = useState(position);
  const [skillPanelSide, setSkillPanelSide] = useState<'left' | 'right'>('right');
  const [activeSkillProvider, setActiveSkillProvider] = useState<SkillProvider | null>(null);
  const skillPanelCloseTimerRef = useRef<number | null>(null);

  const allowedTypeSet = useMemo(
    () => (allowedTypes ? new Set(allowedTypes) : null),
    [allowedTypes]
  );

  const referenceGenerateItems = useMemo<ReferenceGenerateAction[] | null>(() => {
    if (!allowedTypeSet) {
      return null;
    }

    const items: ReferenceGenerateAction[] = [
      {
        key: 'text',
        label: '文本',
        Icon: Type,
        type: allowedTypeSet.has(CANVAS_NODE_TYPES.textAnnotation)
          ? CANVAS_NODE_TYPES.textAnnotation
          : undefined,
        disabled: !allowedTypeSet.has(CANVAS_NODE_TYPES.textAnnotation),
      },
      {
        key: 'image',
        label: '图片',
        Icon: Image,
        // 创建顺序：imageGen（默认生成节点） → imageEdit（编辑节点） →
        // upload（纯上传节点，目标端创建参考图时用）。
        type: allowedTypeSet.has(CANVAS_NODE_TYPES.imageGen)
          ? CANVAS_NODE_TYPES.imageGen
          : allowedTypeSet.has(CANVAS_NODE_TYPES.imageEdit)
            ? CANVAS_NODE_TYPES.imageEdit
            : allowedTypeSet.has(CANVAS_NODE_TYPES.upload)
              ? CANVAS_NODE_TYPES.upload
              : undefined,
        disabled:
          !allowedTypeSet.has(CANVAS_NODE_TYPES.imageGen)
          && !allowedTypeSet.has(CANVAS_NODE_TYPES.imageEdit)
          && !allowedTypeSet.has(CANVAS_NODE_TYPES.upload),
      },
      {
        key: 'video',
        label: '视频',
        Icon: Video,
        type: allowedTypeSet.has(CANVAS_NODE_TYPES.video)
          ? CANVAS_NODE_TYPES.video
          : undefined,
        disabled: !allowedTypeSet.has(CANVAS_NODE_TYPES.video),
      },
      {
        key: 'audio',
        label: '音频',
        Icon: Music,
        type: allowedTypeSet.has(CANVAS_NODE_TYPES.audio)
          ? CANVAS_NODE_TYPES.audio
          : undefined,
        disabled: !allowedTypeSet.has(CANVAS_NODE_TYPES.audio),
      },
      {
        key: 'script',
        label: '脚本',
        Icon: FileText,
        type: allowedTypeSet.has(CANVAS_NODE_TYPES.script)
          ? CANVAS_NODE_TYPES.script
          : undefined,
        disabled: !allowedTypeSet.has(CANVAS_NODE_TYPES.script),
      },
      {
        key: 'pano360',
        label: '360° 全景',
        Icon: Globe,
        type: allowedTypeSet.has(CANVAS_NODE_TYPES.pano360Viewer)
          ? CANVAS_NODE_TYPES.pano360Viewer
          : undefined,
        disabled: !allowedTypeSet.has(CANVAS_NODE_TYPES.pano360Viewer),
      },
      {
        key: 'threeDWorld',
        label: '3D 世界',
        Icon: Orbit,
        type: allowedTypeSet.has(CANVAS_NODE_TYPES.threeDWorld)
          ? CANVAS_NODE_TYPES.threeDWorld
          : undefined,
        disabled: !allowedTypeSet.has(CANVAS_NODE_TYPES.threeDWorld),
        beta: true,
      },
    ];

    const enabled = items.filter((item) => !item.disabled && item.type);
    return enabled.length > 0 ? enabled : null;
  }, [allowedTypeSet]);

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

  useEffect(() => {
    if (skillGroups.length === 0) {
      setActiveSkillProvider(null);
      return;
    }
    // Drop a stale selection (the provider list changed and the previously
    // active one is no longer here), but do NOT auto-select the first group:
    // the menu should open with no skill group expanded — the right panel
    // only appears once the user hovers or clicks a group on the left.
    if (
      activeSkillProvider &&
      !skillGroups.some((group) => group.provider === activeSkillProvider)
    ) {
      setActiveSkillProvider(null);
    }
  }, [activeSkillProvider, skillGroups]);

  useEffect(() => {
    requestAnimationFrame(() => {
      setIsVisible(true);
    });
  }, []);

  useLayoutEffect(() => {
    const menuElement = menuRef.current;
    const mainPanelElement = mainPanelRef.current;
    const viewportElement = menuElement?.offsetParent as HTMLElement | null;
    if (!menuElement || !mainPanelElement || !viewportElement) {
      return;
    }

    const mainWidth = mainPanelElement.offsetWidth;
    const mainHeight = mainPanelElement.offsetHeight;
    const viewportWidth = viewportElement.clientWidth;
    const viewportHeight = viewportElement.clientHeight;
    const maxX = Math.max(MENU_VIEWPORT_MARGIN, viewportWidth - mainWidth - MENU_VIEWPORT_MARGIN);
    const maxY = Math.max(MENU_VIEWPORT_MARGIN, viewportHeight - mainHeight - MENU_VIEWPORT_MARGIN);
    const nextX = Math.min(Math.max(position.x, MENU_VIEWPORT_MARGIN), maxX);
    const nextY = Math.min(Math.max(position.y, MENU_VIEWPORT_MARGIN), maxY);
    const skillPanelWidth = skillPanelRef.current?.offsetWidth ?? 0;
    const hasSpaceOnRight =
      !activeSkillProvider ||
      nextX + mainWidth + SKILL_PANEL_GAP + skillPanelWidth <= viewportWidth - MENU_VIEWPORT_MARGIN;
    const hasSpaceOnLeft =
      activeSkillProvider &&
      nextX - SKILL_PANEL_GAP - skillPanelWidth >= MENU_VIEWPORT_MARGIN;

    setPanelPosition((current) => (
      current.x === nextX && current.y === nextY ? current : { x: nextX, y: nextY }
    ));
    setSkillPanelSide(hasSpaceOnRight || !hasSpaceOnLeft ? 'right' : 'left');
    setIsPositioned(true);
  }, [activeSkillProvider, position.x, position.y]);

  const handleClose = useCallback(() => {
    setIsVisible(false);
    setTimeout(onClose, UI_POPOVER_TRANSITION_MS);
  }, [onClose]);

  const handleSkillPick = useCallback(
    (skill: SkillDefinition) => {
      if (!onSelectSkill) {
        return;
      }
      onSelectSkill(skill);
      handleClose();
    },
    [handleClose, onSelectSkill],
  );

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
    const onPointerDown = (event: MouseEvent) => {
      if (menuRef.current?.contains(event.target as Node)) {
        return;
      }

      handleClose();
    };

    document.addEventListener('mousedown', onPointerDown, true);
    return () => {
      document.removeEventListener('mousedown', onPointerDown, true);
    };
  }, [handleClose]);

  useEffect(() => {
    if (!activeSkillProvider) {
      return;
    }

    const onPointerMove = (event: PointerEvent) => {
      if (menuRef.current?.contains(event.target as Node)) {
        return;
      }
      scheduleSkillPanelClose();
    };

    document.addEventListener('pointermove', onPointerMove, true);
    return () => {
      document.removeEventListener('pointermove', onPointerMove, true);
    };
  }, [activeSkillProvider, scheduleSkillPanelClose]);

  return (
    <div
      ref={menuRef}
      onPointerLeave={scheduleSkillPanelClose}
      onPointerEnter={cancelSkillPanelClose}
      onPointerDown={(event) => event.stopPropagation()}
      onMouseDown={(event) => event.stopPropagation()}
      onClick={(event) => event.stopPropagation()}
      className={`
        absolute z-50
        transition-opacity duration-150
        ${isVisible && isPositioned ? 'opacity-100' : 'opacity-0'}
      `}
      style={{ left: panelPosition.x, top: panelPosition.y }}
    >
      <div
        ref={mainPanelRef}
        className={`${CANVAS_TOOL_SURFACE_CLASS} w-[240px] max-w-[calc(100vw-24px)] overflow-hidden`}
      >
        {/*
          Inner scroll container. The outer wrapper keeps `overflow-hidden` so
          the rounded corners + border clip cleanly; without this inner div the
          menu just runs off the bottom of the viewport once 技能节点 / 添加资源
          push the height past the screen. `max-h-[70vh]` adapts to short
          screens; the right-side skill panel has its own independent cap.
        */}
        <div className="ui-scrollbar max-h-[min(510px,70vh)] overflow-y-auto p-2.5 [scrollbar-gutter:stable]">
          {referenceGenerateItems ? (
            <>
              <CanvasMenuSectionHeader label="引用该节点生成" className="px-2 pb-2 pt-1" />
              <div className="flex flex-col gap-0.5">
                {referenceGenerateItems.map((item, index) => {
                  const Icon = item.Icon;
                  return (
                    <button
                      key={item.key}
                      disabled={item.disabled}
                      onMouseEnter={scheduleSkillPanelClose}
                      className={`${CANVAS_MENU_ROW_CLASS} ${item.disabled ? 'cursor-not-allowed opacity-35' : ''}`}
                      style={{ transitionDelay: isVisible ? `${index * 30}ms` : '0ms' }}
                      onClick={(event) => {
                        const selectedType = item.type;
                        if (!selectedType || item.disabled) {
                          return;
                        }
                        const clientPosition = { x: event.clientX, y: event.clientY };
                        handleClose();
                        setTimeout(
                          () => onSelect(selectedType, clientPosition),
                          UI_POPOVER_TRANSITION_MS + 10,
                        );
                      }}
                    >
                      <Icon className={CANVAS_MENU_ICON_CLASS} aria-hidden="true" />
                      <span className="min-w-0 flex-1 truncate text-white/82">{item.label}</span>
                    </button>
                  );
                })}
              </div>
            </>
          ) : (
            <>
              <CanvasMenuSectionHeader
                label={t('node.menu.sectionAddNode')}
                className="px-2 pb-2 pt-1"
              />
              <CanvasAddNodeGrid
                onItemPointerEnter={scheduleSkillPanelClose}
                transitionDelayForIndex={(index) => (isVisible ? `${index * 30}ms` : '0ms')}
                onSelectNode={(type, clientPosition) => {
                  handleClose();
                  setTimeout(
                    () => onSelect(type, clientPosition),
                    UI_POPOVER_TRANSITION_MS + 10,
                  );
                }}
              />
              {onSelectSkill && skillGroups.length > 0 && (
                <>
                  <div className={`${CANVAS_MENU_DIVIDER_CLASS} my-2`} />
                  <div className="flex flex-col gap-0.5">
                    {skillGroups.map((group, index) => (
                      <button
                        key={group.provider}
                        type="button"
                        className={`${CANVAS_MENU_ROW_CLASS} ${
                          activeSkillProvider === group.provider ? 'canvas-tool-row-active' : ''
                        }`}
                        style={{ transitionDelay: isVisible ? `${(index + 10) * 30}ms` : '0ms' }}
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
                        <span className="min-w-0 flex-1 truncate">
                          {skillProviderLabels[group.provider]}
                        </span>
                        <span className="canvas-tool-meta">{group.items.length}</span>
                        <ChevronRight className="h-3.5 w-3.5 shrink-0 text-white/30" />
                      </button>
                    ))}
                  </div>
                </>
              )}
            </>
          )}
        </div>
      </div>
      {onSelectSkill && activeSkillGroup && (
        <div
          ref={skillPanelRef}
          className={`${CANVAS_TOOL_SURFACE_CLASS} absolute top-0 w-[336px] max-w-[calc(100vw-24px)] overflow-hidden ${
            skillPanelSide === 'right'
              ? 'left-[calc(100%+8px)]'
              : 'right-[calc(100%+8px)]'
          }`}
          onPointerEnter={cancelSkillPanelClose}
          onPointerLeave={scheduleSkillPanelClose}
        >
          <div className="canvas-tool-section-label px-3 pb-2 pt-3">
            {skillProviderLabels[activeSkillGroup.provider]}
          </div>
          <div className="ui-scrollbar max-h-[440px] overflow-y-auto px-2 pb-2 [scrollbar-gutter:stable]">
            {activeSkillGroup.items.map((skill, index) => (
              <button
                key={skill.id}
                type="button"
                className={`${CANVAS_MENU_ROW_CLASS} min-h-[52px] items-start py-2`}
                style={{ transitionDelay: isVisible ? `${index * 30}ms` : '0ms' }}
                onClick={(event) => {
                  event.preventDefault();
                  event.stopPropagation();
                  handleSkillPick(skill);
                }}
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
