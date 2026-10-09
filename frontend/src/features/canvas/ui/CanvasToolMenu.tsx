// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { Hand, MousePointer2 } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import { useCanvasToolStore, type CanvasTool } from './canvasToolStore';
import {
  CANVAS_MENU_ICON_CLASS,
  CANVAS_MENU_ROW_CLASS,
  CANVAS_TOOL_SURFACE_CLASS,
} from './canvas-node-menu-shared';

export const CANVAS_TOOL_ITEMS: ReadonlyArray<{
  tool: CanvasTool;
  Icon: typeof Hand;
  labelKey: string;
  shortcut: string;
}> = [
  { tool: 'move', Icon: MousePointer2, labelKey: 'canvas.toolbar.toolMove', shortcut: 'V' },
  { tool: 'hand', Icon: Hand, labelKey: 'canvas.toolbar.toolHand', shortcut: 'H' },
];

interface CanvasToolMenuProps {
  onSelect?: () => void;
}

/** 画布快捷栏里的移动 / 抓手工具菜单。 */
export function CanvasToolMenu({ onSelect }: CanvasToolMenuProps) {
  const { t } = useTranslation();
  const tool = useCanvasToolStore((state) => state.tool);
  const setTool = useCanvasToolStore((state) => state.setTool);

  return (
    <div
      role="menu"
      aria-label={t('canvas.toolbar.toolGroupLabel')}
      className={`${CANVAS_TOOL_SURFACE_CLASS} w-[196px] p-1.5`}
    >
      {CANVAS_TOOL_ITEMS.map(({ tool: value, Icon, labelKey, shortcut }) => {
        const active = tool === value;
        return (
          <button
            key={value}
            type="button"
            role="menuitemradio"
            aria-checked={active}
            onClick={() => {
              setTool(value);
              onSelect?.();
            }}
            className={`${CANVAS_MENU_ROW_CLASS} ${
              active
                ? 'canvas-tool-row-active'
                : ''
            }`}
          >
            <Icon className={CANVAS_MENU_ICON_CLASS} />
            <span className="flex-1">{t(labelKey)}</span>
            <span className="text-[12px] tabular-nums text-white/38">{shortcut}</span>
          </button>
        );
      })}
    </div>
  );
}
