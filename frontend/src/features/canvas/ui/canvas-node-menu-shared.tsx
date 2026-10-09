// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  FileText,
  Film,
  Globe,
  Image,
  LayoutGrid,
  Music,
  Orbit,
  Search,
  Sparkles,
  Type,
  Upload,
  Video,
  type LucideIcon,
} from "lucide-react";

import {
  CANVAS_NODE_TYPES,
  type CanvasNodeType,
} from "@/features/canvas/domain/canvasNodes";
import { nodeCatalog } from "@/features/canvas/application/nodeCatalog";
import type { MenuIconKey } from "@/features/canvas/domain/nodeRegistry";

export const canvasMenuIconMap: Record<MenuIconKey, LucideIcon> = {
  upload: Upload,
  sparkles: Sparkles,
  layout: LayoutGrid,
  text: Type,
  video: Video,
  audio: Music,
  script: FileText,
  pano360: Globe,
  threeDWorld: Orbit,
  videoCompose: Film,
};

export const CANVAS_ADD_NODE_TYPES: readonly CanvasNodeType[] = [
  CANVAS_NODE_TYPES.textAnnotation,
  CANVAS_NODE_TYPES.beatContext,
  CANVAS_NODE_TYPES.imageGen,
  CANVAS_NODE_TYPES.video,
  CANVAS_NODE_TYPES.videoCompose,
  CANVAS_NODE_TYPES.audio,
  CANVAS_NODE_TYPES.script,
  CANVAS_NODE_TYPES.upload,
  CANVAS_NODE_TYPES.pano360Viewer,
  CANVAS_NODE_TYPES.threeDWorld,
];

/**
 * 菜单里的徽章。只标「用户需要提前知道代价」的项：Beta 表示还在收敛，
 * `hd` 表示会走高清/超分计费链路 —— 与 libtv 的 NEW / Beta / 💎HD 同义。
 */
export const CANVAS_NODE_BADGES: Partial<Record<CanvasNodeType, 'beta' | 'hd' | 'new'>> = {
  [CANVAS_NODE_TYPES.threeDWorld]: 'beta',
  [CANVAS_NODE_TYPES.videoCompose]: 'beta',
};

export const CANVAS_TOOL_SURFACE_CLASS =
  "canvas-tool-surface rounded-[14px] border";
export const CANVAS_MENU_ROW_CLASS =
  "canvas-tool-row flex min-h-10 w-full items-center gap-3 rounded-[9px] px-2.5 py-2 text-left text-[13px] font-medium leading-5 transition-[background-color,color,transform] duration-150 ease-out";
export const CANVAS_MENU_ICON_CLASS = "canvas-tool-icon h-[18px] w-[18px] shrink-0";
export const CANVAS_MENU_DIVIDER_CLASS = "canvas-tool-divider h-px w-full";

interface CanvasMenuSectionHeaderProps {
  label: string;
  className?: string;
}

export function CanvasMenuSectionHeader({
  label,
  className = "",
}: CanvasMenuSectionHeaderProps) {
  return (
    <div className={`canvas-tool-section-label ${className}`}>
      {label}
    </div>
  );
}

/** 节点类型徽章：色块小标签，浮在菜单行文字后面。 */
export function CanvasMenuBadge({ kind }: { kind: 'beta' | 'hd' | 'new' }) {
  const { t } = useTranslation();
  const label =
    kind === 'hd'
      ? t('node.menu.hdBadge')
      : kind === 'new'
        ? t('node.menu.newBadge')
        : t('node.menu.betaTag');
  const tone =
    kind === 'new'
      ? 'canvas-tool-badge--new'
      : kind === 'hd'
        ? 'canvas-tool-badge--hd'
        : 'canvas-tool-badge--beta';
  return <span className={`canvas-tool-badge ${tone}`}>{label}</span>;
}

interface CanvasAddNodeSearchProps {
  value: string;
  onChange: (next: string) => void;
}

/** 「添加节点」菜单顶部的过滤框。键盘 `Tab` 打开菜单后可以直接输入过滤。 */
export function CanvasAddNodeSearch({ value, onChange }: CanvasAddNodeSearchProps) {
  const { t } = useTranslation();
  return (
    <div className="canvas-tool-search relative px-2.5 pb-1 pt-1.5">
      <Search
        className="pointer-events-none absolute left-[18px] top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-white/35"
        aria-hidden="true"
      />
      <input
        type="text"
        value={value}
        autoFocus
        onChange={(event) => onChange(event.target.value)}
        placeholder={t('node.menu.searchPlaceholder')}
        aria-label={t('node.menu.searchPlaceholder')}
        className="canvas-tool-search__input w-full rounded-[9px] py-1.5 pl-7 pr-2 text-[12px] leading-5"
        onKeyDown={(event) => event.stopPropagation()}
      />
    </div>
  );
}

interface CanvasAddNodeGridProps {
  onSelectNode: (type: CanvasNodeType, clientPosition?: { x: number; y: number }) => void;
  onItemPointerEnter?: () => void;
  transitionDelayForIndex?: (index: number) => string | undefined;
  /** Optional type filter (from {@link CanvasAddNodeSearch}). */
  visibleTypes?: readonly CanvasNodeType[];
  /** Shown when the filter matches nothing. */
  emptyLabel?: string;
}

export function CanvasAddNodeGrid({
  onSelectNode,
  onItemPointerEnter,
  transitionDelayForIndex,
  visibleTypes,
  emptyLabel,
}: CanvasAddNodeGridProps) {
  const { t } = useTranslation();
  const types = visibleTypes ?? CANVAS_ADD_NODE_TYPES;

  if (types.length === 0 && emptyLabel) {
    return (
      <div className="px-2.5 py-3 text-[12px] leading-5 text-white/38">{emptyLabel}</div>
    );
  }

  return (
    <div className="flex flex-col gap-0.5">
      {types.map((type, index) => {
        const definition = nodeCatalog.getDefinition(type);
        if (!definition) return null;
        const Icon = canvasMenuIconMap[definition.menuIcon] ?? Image;
        const badge = CANVAS_NODE_BADGES[type];
        return (
          <button
            key={type}
            type="button"
            onMouseEnter={onItemPointerEnter}
            className={CANVAS_MENU_ROW_CLASS}
            style={{ transitionDelay: transitionDelayForIndex?.(index) }}
            onClick={(event) => onSelectNode(type, { x: event.clientX, y: event.clientY })}
          >
            <Icon className={CANVAS_MENU_ICON_CLASS} aria-hidden="true" />
            <span className="min-w-0 flex-1 truncate">
              {t(definition.menuLabelKey)}
            </span>
            {badge && <CanvasMenuBadge kind={badge} />}
          </button>
        );
      })}
    </div>
  );
}

/**
 * 按关键字过滤节点类型菜单项。
 *
 * 只按已翻译的菜单名和类型 key 匹配 —— 不搜 hint/描述，避免「输入图片却跳出
 * 一堆带图片两个字的能力项」这类噪声。空关键字返回全量（不复制数组，保持引用
 * 稳定以免每次渲染都触发子组件重排）。
 */
export function filterCanvasAddNodeTypes(
  types: readonly CanvasNodeType[],
  query: string,
  translate: (key: string) => string,
): readonly CanvasNodeType[] {
  const needle = query.trim().toLowerCase();
  if (needle.length === 0) return types;
  return types.filter((type) => {
    const definition = nodeCatalog.getDefinition(type);
    const label = definition ? translate(definition.menuLabelKey) : '';
    return (
      label.toLowerCase().includes(needle)
      || String(type).toLowerCase().includes(needle)
      || type.toLowerCase().includes(needle)
    );
  });
}

/** 给菜单组件用的受控搜索状态：输入框和过滤结果绑在一起，避免两处各写一遍。 */
export function useCanvasAddNodeSearch(types: readonly CanvasNodeType[] = CANVAS_ADD_NODE_TYPES) {
  const { t } = useTranslation();
  const [query, setQuery] = useState('');
  const visibleTypes = useMemo(
    () => filterCanvasAddNodeTypes(types, query, t),
    [types, query, t],
  );
  return { query, setQuery, visibleTypes };
}
