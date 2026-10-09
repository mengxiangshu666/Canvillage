// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useEffect, useRef, useState } from 'react';
import { Check, ChevronDown } from 'lucide-react';

import { SCRIPT_VIEWS, scriptViewLabel, type ScriptViewId } from './scriptViews';

/**
 * 脚本节点视图切换（脚本视图 / 创意视图 / 资产视图）。
 *
 * 取代原先点不动的占位下拉：视图选择写回节点数据，节点内与全屏共用同一份值。
 */
export interface ScriptViewSwitcherProps {
  value: ScriptViewId;
  onChange: (next: ScriptViewId) => void;
}

const BUTTON_CLASS =
  'nodrag inline-flex h-7 items-center gap-1 rounded-[8px] px-2 text-[12px] text-text-dark/85 transition-colors hover:bg-white/[0.08] hover:text-text-dark';

export function ScriptViewSwitcher({ value, onChange }: ScriptViewSwitcherProps) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

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
        onClick={(event) => {
          event.stopPropagation();
          setOpen((previous) => !previous);
        }}
      >
        {scriptViewLabel(value)}
        <ChevronDown className="h-3 w-3" />
      </button>
      {open && (
        <div
          role="menu"
          className="absolute right-0 top-full z-30 mt-1 flex min-w-[112px] flex-col rounded border border-[rgba(255,255,255,0.16)] bg-bg-dark/95 p-1 shadow-lg backdrop-blur"
          onClick={(event) => event.stopPropagation()}
        >
          {SCRIPT_VIEWS.map((view) => (
            <button
              key={view.id}
              type="button"
              role="menuitemradio"
              aria-checked={view.id === value}
              className="flex items-center gap-2 rounded px-2 py-1 text-left text-[11px] text-text-dark hover:bg-white/[0.08]"
              onClick={() => {
                onChange(view.id);
                setOpen(false);
              }}
            >
              <span className="flex-1">{view.label}</span>
              {view.id === value && <Check className="h-3 w-3" />}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
