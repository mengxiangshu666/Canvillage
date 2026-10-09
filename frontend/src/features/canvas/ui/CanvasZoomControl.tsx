// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useCallback, useEffect, useRef, useState } from 'react';
import {
  Magnet,
  Map,
  Maximize2,
  Search,
  Wand2,
  Waypoints,
} from 'lucide-react';
import { useReactFlow, useStore } from '@xyflow/react';
import { useTranslation } from 'react-i18next';

import { isImmersiveViewerActive } from '@/features/viewer-kit/useViewerImmersiveBody';
import { MOD_KEY_LABEL } from '@/lib/platform';
import { useSnapAlignStore } from '../snap-align/snapAlignStore';
import { useEdgeVisibilityStore } from './edgeVisibilityStore';

const ZOOM_STEP = 1.2;
const ZOOM_MIN = 0.1;
const ZOOM_MAX = 8;
const ZOOM_PRESETS = [50, 100, 200, 800];

function isTypingTarget(target: EventTarget | null): boolean {
  const element = target as HTMLElement | null;
  if (!element) return false;
  const tagName = element.tagName.toLowerCase();
  return (
    tagName === 'input' ||
    tagName === 'textarea' ||
    tagName === 'select' ||
    element.isContentEditable ||
    Boolean(element.closest('[role="textbox"]'))
  );
}

interface CanvasZoomControlProps {
  minimapOpen: boolean;
  onFitView: () => void;
  onOrganize: () => void;
  onToggleMinimap: () => void;
  placement?: 'bottom-left' | 'bottom-right' | 'top-left' | 'top-right';
}

export function CanvasZoomControl({
  minimapOpen,
  onFitView,
  onOrganize,
  onToggleMinimap,
  placement = 'bottom-left',
}: CanvasZoomControlProps) {
  const { zoomTo, getZoom } = useReactFlow();
  // Subscribe to zoom only. Panning no longer re-renders the control dock every frame.
  const zoom = useStore((state) => state.transform[2]);
  const { t } = useTranslation();
  const snapEnabled = useSnapAlignStore((state) => state.enabled);
  const toggleSnap = useSnapAlignStore((state) => state.toggle);
  const edgesHidden = useEdgeVisibilityStore((state) => state.hidden);
  const toggleEdgesHidden = useEdgeVisibilityStore((state) => state.toggle);

  const percent = Math.round(zoom * 100);
  const [menuOpen, setMenuOpen] = useState(false);
  const [draft, setDraft] = useState('');
  const rootRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);

  const handleZoomIn = useCallback(() => {
    zoomTo(Math.min(getZoom() * ZOOM_STEP, ZOOM_MAX), { duration: 120 });
  }, [getZoom, zoomTo]);

  const handleZoomOut = useCallback(() => {
    zoomTo(Math.max(getZoom() / ZOOM_STEP, ZOOM_MIN), { duration: 120 });
  }, [getZoom, zoomTo]);

  const handleFitView = useCallback(() => {
    onFitView();
  }, [onFitView]);

  const handleZoomToPercent = useCallback(
    (value: number) => {
      const clamped = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, value / 100));
      zoomTo(clamped, { duration: 160 });
    },
    [zoomTo],
  );

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (!(event.metaKey || event.ctrlKey) || event.altKey || event.shiftKey) return;
      if (isTypingTarget(event.target) || isImmersiveViewerActive()) return;
      if (event.key === '=' || event.key === '+') {
        event.preventDefault();
        handleZoomIn();
      } else if (event.key === '-') {
        event.preventDefault();
        handleZoomOut();
      } else if (event.key === '0') {
        event.preventDefault();
        handleFitView();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [handleFitView, handleZoomIn, handleZoomOut]);

  useEffect(() => {
    if (!menuOpen) return;
    const handlePointerDown = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setMenuOpen(false);
    };
    window.addEventListener('pointerdown', handlePointerDown);
    return () => window.removeEventListener('pointerdown', handlePointerDown);
  }, [menuOpen]);

  const openMenu = () => {
    setDraft(String(percent));
    setMenuOpen(true);
    window.requestAnimationFrame(() => inputRef.current?.select());
  };

  const commitDraft = () => {
    const value = Number.parseFloat(draft);
    if (Number.isFinite(value) && value > 0) handleZoomToPercent(value);
  };

  const runAndClose = (action: () => void) => {
    action();
    setMenuOpen(false);
  };

  const isTop = placement.startsWith('top');
  const isLeft = placement.endsWith('left');
  const positionClass = `${isTop ? 'top-3' : 'bottom-3'} ${isLeft ? 'left-3' : 'right-3'}`;
  const menuPositionClass = `${isTop ? 'top-full mt-2' : 'bottom-full mb-2'} ${isLeft ? 'left-0' : 'right-0'}`;
  const surfaceClass = 'canvas-dock-surface canvas-navigation-group';
  const iconButtonClass =
    'canvas-dock-button canvas-navigation-button group relative flex items-center justify-center';
  const menuItemClass = 'canvas-dock-menu-item flex w-full items-center justify-between';

  return (
    <div
      ref={rootRef}
      className={`canvas-navigation-dock nopan nowheel pointer-events-auto absolute z-[42] flex items-center gap-2 ${positionClass}`}
      data-placement={placement}
      onPointerDown={(event) => event.stopPropagation()}
    >
      <div className={`flex items-center ${surfaceClass}`}>
        {/* 网格显隐只在「画布设置 → 网格与显示 → 网格底纹」维护，这里不再放第二份入口。 */}
        <button
          type="button"
          onClick={onToggleMinimap}
          className={iconButtonClass}
          data-active={minimapOpen ? 'true' : 'false'}
          aria-label={minimapOpen ? '关闭任务视图' : '打开任务视图'}
          aria-pressed={minimapOpen}
          title={minimapOpen ? '关闭任务视图' : '打开任务视图'}
        >
          <Map className="h-[18px] w-[18px]" />
        </button>
        <span className="mx-1 h-5 w-px bg-white/[0.09]" aria-hidden />
        <button
          type="button"
          onClick={() => (menuOpen ? setMenuOpen(false) : openMenu())}
          className="canvas-dock-button canvas-navigation-action flex items-center justify-center"
          aria-haspopup="menu"
          aria-expanded={menuOpen}
          aria-label={t('canvas.zoom.menuLabel')}
        >
          <Search className="h-4 w-4 text-white/58" />
          <span className="text-[15px] tabular-nums tracking-[-0.02em]">{percent}%</span>
        </button>
      </div>

      <div className={`flex items-center ${surfaceClass}`}>
        <button
          type="button"
          onClick={onOrganize}
          className="canvas-dock-button canvas-navigation-action flex items-center"
          aria-label={t('canvas.toolbar.organize')}
          title={t('canvas.toolbar.organize')}
        >
          <Wand2 className="h-[17px] w-[17px]" />
          <span className="text-[12px] font-medium">{t('canvas.toolbar.tidy')}</span>
        </button>
        <button
          type="button"
          onClick={handleFitView}
          className="canvas-dock-button canvas-navigation-action flex items-center"
          aria-label={t('canvas.zoom.fitView')}
          title={t('canvas.zoom.fitView')}
        >
          <Maximize2 className="h-[17px] w-[17px]" />
          <span className="text-[12px] font-medium">{t('canvas.zoom.locate')}</span>
        </button>
        <button
          type="button"
          onClick={toggleSnap}
          className="canvas-dock-button canvas-navigation-action flex items-center"
          data-active={snapEnabled ? 'true' : 'false'}
          aria-label={snapEnabled ? '关闭对齐吸附' : '开启对齐吸附'}
          aria-pressed={snapEnabled}
          title={snapEnabled ? '关闭对齐吸附' : '开启对齐吸附'}
        >
          <Magnet className="h-[17px] w-[17px]" />
          <span className="text-[12px] font-medium">{t('canvas.toolbar.snap')}</span>
        </button>
      </div>

      {menuOpen && (
        <div
          role="menu"
          className={`canvas-dock-menu absolute z-50 w-[218px] rounded-2xl border border-white/10 bg-[#202023]/96 p-1.5 shadow-[0_18px_44px_rgba(0,0,0,0.46)] backdrop-blur-2xl ${menuPositionClass}`}
        >
          <div className="mb-1 flex items-center rounded-xl bg-white/[0.07] px-3 py-2">
            <input
              ref={inputRef}
              value={draft}
              onChange={(event) => setDraft(event.target.value.replace(/[^\d.]/g, ''))}
              onKeyDown={(event) => {
                event.stopPropagation();
                if (event.key === 'Enter') {
                  commitDraft();
                  setMenuOpen(false);
                } else if (event.key === 'Escape') {
                  setMenuOpen(false);
                }
              }}
              onBlur={commitDraft}
              inputMode="decimal"
              className="canvas-dock-zoom-input w-full bg-transparent text-[13px] tabular-nums text-white outline-none"
              aria-label={t('canvas.zoom.inputLabel')}
            />
            <span className="text-[13px] text-white/42">%</span>
          </div>
          <button type="button" role="menuitem" className={menuItemClass} onClick={() => runAndClose(handleZoomIn)}>
            <span>{t('canvas.zoom.zoomIn')}</span>
            <span className="text-[12px] text-white/38">{MOD_KEY_LABEL} +</span>
          </button>
          <button type="button" role="menuitem" className={menuItemClass} onClick={() => runAndClose(handleZoomOut)}>
            <span>{t('canvas.zoom.zoomOut')}</span>
            <span className="text-[12px] text-white/38">{MOD_KEY_LABEL} -</span>
          </button>
          <button type="button" role="menuitem" className={menuItemClass} onClick={() => runAndClose(handleFitView)}>
            <span>{t('canvas.zoom.fitView')}</span>
            <span className="text-[12px] text-white/38">{MOD_KEY_LABEL} 0</span>
          </button>
          <button type="button" role="menuitem" className={menuItemClass} onClick={() => runAndClose(toggleEdgesHidden)}>
            <span className="flex items-center gap-2"><Waypoints className="h-3.5 w-3.5" />{edgesHidden ? t('canvas.toolbar.showEdges') : t('canvas.toolbar.hideEdges')}</span>
          </button>
          <div className="mx-1 my-1 h-px bg-white/[0.08]" aria-hidden />
          {ZOOM_PRESETS.map((preset) => (
            <button
              key={preset}
              type="button"
              role="menuitem"
              className={menuItemClass}
              onClick={() => runAndClose(() => handleZoomToPercent(preset))}
            >
              <span>{t('canvas.zoom.zoomToPercent', { percent: preset })}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
