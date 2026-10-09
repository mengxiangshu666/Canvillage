// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useEffect, useRef, useState } from 'react';
import { Check, Columns3 } from 'lucide-react';

import { NODE_FLOATING_PANEL_SURFACE_CLASS } from '@/features/canvas/ui/nodeControlStyles';
import type { ScriptFieldDef } from './scriptFields';
import {
  SCRIPT_COLUMN_MODES,
  resolveScriptColumnMode,
  scriptColumnSummary,
  type ScriptColumnMode,
} from './scriptColumns';

/**
 * 分镜表列显隐。
 *
 * 20 列 × 2540px 的宽表塞进 800px 的节点里，一屏只能看三分之一 —— 这个菜单是
 * 唯一的「把表收成能读完的宽度」入口。三档：
 * 默认（按节点宽度自动选列，核心列优先）/ 全部列 / 自定义（逐列勾）。
 *
 * 与脚本节点自己的视图切换器（`ScriptViewSwitcher`）同构：收起态只显示当前档位，
 * 展开后逐项带勾，点空白或 Esc 收起 —— 少一套外观要维护。
 */

export interface ScriptColumnMenuProps {
  mode: ScriptColumnMode;
  visibleFields: ScriptFieldDef[];
  allFields: ScriptFieldDef[];
  onModeChange: (next: ScriptColumnMode) => void;
  onToggleColumn: (key: string) => void;
}

const MODE_LABELS: Record<ScriptColumnMode, string> = {
  default: '默认列',
  all: '全部列',
  manual: '自定义',
};

const BUTTON_CLASS =
  'nodrag inline-flex h-7 items-center gap-1 rounded-[8px] px-2 text-[12px] text-text-dark/85 transition-colors hover:bg-white/[0.08] hover:text-text-dark';

export function ScriptColumnMenu({
  mode,
  visibleFields,
  allFields,
  onModeChange,
  onToggleColumn,
}: ScriptColumnMenuProps) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const resolvedMode = resolveScriptColumnMode(mode);
  const visibleKeys = new Set(visibleFields.map((field) => field.key));

  useEffect(() => {
    if (!open) return;
    const handlePointerDown = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false);
    };
    window.addEventListener('mousedown', handlePointerDown);
    window.addEventListener('keydown', handleKey);
    return () => {
      window.removeEventListener('mousedown', handlePointerDown);
      window.removeEventListener('keydown', handleKey);
    };
  }, [open]);

  return (
    <div className="nodrag relative" ref={rootRef}>
      <button
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        className={BUTTON_CLASS}
        title={`列显隐（当前 ${scriptColumnSummary(visibleFields.length, allFields.length)}）`}
        onClick={(event) => {
          event.stopPropagation();
          setOpen((previous) => !previous);
        }}
      >
        <Columns3 className="h-3.5 w-3.5" />
        {scriptColumnSummary(visibleFields.length, allFields.length)}
      </button>
      {open && (
        <div
          role="menu"
          className={`absolute right-0 top-full z-30 mt-1 flex max-h-[320px] w-[176px] flex-col overflow-y-auto p-1 ui-scrollbar ${NODE_FLOATING_PANEL_SURFACE_CLASS}`}
          onClick={(event) => event.stopPropagation()}
        >
          <div className="flex flex-wrap gap-0.5 px-1 pb-1">
            {SCRIPT_COLUMN_MODES.map((option) => (
              <button
                key={option}
                type="button"
                aria-pressed={option === resolvedMode}
                className={`h-6 rounded-[6px] px-1.5 text-[11px] transition-colors ${
                  option === resolvedMode
                    ? 'bg-white/[0.14] text-text-dark'
                    : 'text-text-muted hover:bg-white/[0.06] hover:text-text-dark'
                }`}
                onClick={() => onModeChange(option)}
              >
                {MODE_LABELS[option]}
              </button>
            ))}
          </div>
          <div className="my-0.5 h-px bg-white/[0.08]" aria-hidden="true" />
          {allFields.map((field) => {
            const checked = visibleKeys.has(field.key);
            return (
              <button
                key={field.key}
                type="button"
                role="menuitemcheckbox"
                aria-checked={checked}
                // 逐列点选一律落到「自定义」档 —— 对应的是 LibTV `viewMode` 之外的
                // `tableConfig.columnVisibility[e.key] = !(visibility[e.key] ?? true)`，
                // 同一个「缺省可见」语义。
                onClick={() => onToggleColumn(field.key)}
                className="flex items-center gap-2 rounded px-2 py-1 text-left text-[11px] text-text-dark hover:bg-white/[0.08]"
              >
                <span className="flex h-3 w-3 shrink-0 items-center justify-center">
                  {checked && <Check className="h-3 w-3" />}
                </span>
                <span className="min-w-0 flex-1 truncate">{field.label}</span>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
