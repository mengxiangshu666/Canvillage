// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { create } from 'zustand';

const STORAGE_KEY = 'canvas.viewSettings.v1';

export const CANVAS_THEME_PRESETS = [
  { id: 'deepNight', label: '深夜黑', background: '#121214', grid: '#4b4b53' },
  { id: 'graphite', label: '石墨灰', background: '#19191c', grid: '#575760' },
  { id: 'softGray', label: '柔雾灰', background: '#222226', grid: '#62626b' },
] as const;

export const CANVAS_EDGE_COLOR_PRESETS = [
  { label: '雾银', value: '#b0b0b7' },
  { label: '明亮', value: '#e5e7eb' },
  { label: '宝蓝', value: '#60a5fa' },
  { label: '极光青', value: '#22d3ee' },
  { label: '翠绿', value: '#4ade80' },
  { label: '日光黄', value: '#facc15' },
  { label: '暖橙', value: '#fb923c' },
  { label: '珊瑚红', value: '#f87171' },
  { label: '樱粉', value: '#f472b6' },
  { label: '藤紫', value: '#a78bfa' },
] as const;

export type CanvasThemeId = (typeof CANVAS_THEME_PRESETS)[number]['id'] | 'custom';

function normalizeHexColor(color: unknown, fallback: string): string {
  return typeof color === 'string' && /^#[0-9a-f]{6}$/i.test(color)
    ? color.toLowerCase()
    : fallback;
}

function mixHexColor(color: string, target: string, amount: number): string {
  const normalizedColor = normalizeHexColor(color, '#121214');
  const normalizedTarget = normalizeHexColor(target, '#ffffff');
  const ratio = Math.min(1, Math.max(0, amount));
  const channels = [1, 3, 5].map((offset) => {
    const start = Number.parseInt(normalizedColor.slice(offset, offset + 2), 16);
    const end = Number.parseInt(normalizedTarget.slice(offset, offset + 2), 16);
    return Math.round(start + (end - start) * ratio).toString(16).padStart(2, '0');
  });
  return `#${channels.join('')}`;
}

export function resolveCanvasTheme(themeId: CanvasThemeId, customCanvasColor = '#121214') {
  if (themeId === 'custom') {
    const background = normalizeHexColor(customCanvasColor, '#121214');
    return {
      id: 'custom' as const,
      label: '自定义',
      background,
      grid: mixHexColor(background, '#ffffff', 0.28),
    };
  }
  return CANVAS_THEME_PRESETS.find((theme) => theme.id === themeId) ?? CANVAS_THEME_PRESETS[0];
}

export function colorWithAlpha(color: string, alpha: number): string {
  const normalized = /^#[0-9a-f]{6}$/i.test(color) ? color : '#b0b0b7';
  const red = Number.parseInt(normalized.slice(1, 3), 16);
  const green = Number.parseInt(normalized.slice(3, 5), 16);
  const blue = Number.parseInt(normalized.slice(5, 7), 16);
  return `rgba(${red}, ${green}, ${blue}, ${Math.min(1, Math.max(0, alpha))})`;
}

export function resolveCanvasGridRenderMetrics(
  gridGap: number,
  gridDotSize: number,
  zoom: number,
): { gap: number; dotSize: number } {
  const safeZoom = Math.min(8, Math.max(0.1, Number.isFinite(zoom) ? zoom : 1));
  const baseScreenGap = gridGap * safeZoom;
  const gapMultiplier = baseScreenGap < 16
    ? 2 ** Math.ceil(Math.log2(16 / Math.max(1, baseScreenGap)))
    : 1;

  return {
    // Show every Nth logical grid point at low zoom. The visible points still
    // align with the snap grid while avoiding a dense, noisy dot field.
    gap: gridGap * gapMultiplier,
    // XYFlow scales dot size with zoom, so compensate to keep the dot diameter
    // visually stable and readable across the full zoom range.
    dotSize: gridDotSize / safeZoom,
  };
}

export interface CanvasViewSettings {
  canvasTheme: CanvasThemeId;
  customCanvasColor: string;
  edgeColor: string;
  showGrid: boolean;
  gridGap: number;
  gridDotSize: number;
  snapToGrid: boolean;
  minimapPinned: boolean;
  performanceHudEnabled: boolean;
}

export const DEFAULT_CANVAS_VIEW_SETTINGS: CanvasViewSettings = {
  canvasTheme: 'deepNight',
  customCanvasColor: '#121214',
  edgeColor: '#b0b0b7',
  showGrid: true,
  gridGap: 20,
  gridDotSize: 2.4,
  snapToGrid: false,
  minimapPinned: false,
  performanceHudEnabled: false,
};

interface CanvasViewSettingsState extends CanvasViewSettings {
  setCanvasTheme: (canvasTheme: CanvasThemeId) => void;
  setCustomCanvasColor: (customCanvasColor: string) => void;
  setEdgeColor: (edgeColor: string) => void;
  setShowGrid: (showGrid: boolean) => void;
  setGridGap: (gridGap: number) => void;
  setGridDotSize: (gridDotSize: number) => void;
  setSnapToGrid: (snapToGrid: boolean) => void;
  setMinimapPinned: (minimapPinned: boolean) => void;
  setPerformanceHudEnabled: (performanceHudEnabled: boolean) => void;
  reset: () => void;
}

function clampNumber(value: unknown, fallback: number, min: number, max: number): number {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return fallback;
  return Math.min(max, Math.max(min, numeric));
}

function normalizeSettings(input: unknown): CanvasViewSettings {
  if (!input || typeof input !== 'object') return DEFAULT_CANVAS_VIEW_SETTINGS;
  const raw = input as Partial<CanvasViewSettings>;
  const canvasTheme = raw.canvasTheme === 'custom' || CANVAS_THEME_PRESETS.some((theme) => theme.id === raw.canvasTheme)
    ? (raw.canvasTheme as CanvasThemeId)
    : DEFAULT_CANVAS_VIEW_SETTINGS.canvasTheme;
  const customCanvasColor = normalizeHexColor(
    raw.customCanvasColor,
    DEFAULT_CANVAS_VIEW_SETTINGS.customCanvasColor,
  );
  const edgeColor = normalizeHexColor(raw.edgeColor, DEFAULT_CANVAS_VIEW_SETTINGS.edgeColor);
  const legacyDotSize = Number(raw.gridDotSize);
  const migratedDotSize = legacyDotSize === 1
    ? 1.5
    : legacyDotSize === 2
      ? 2.4
      : legacyDotSize === 3
        ? 3.2
        : legacyDotSize;
  return {
    canvasTheme,
    customCanvasColor,
    edgeColor,
    showGrid: typeof raw.showGrid === 'boolean' ? raw.showGrid : true,
    gridGap: clampNumber(raw.gridGap, 20, 8, 48),
    gridDotSize: clampNumber(migratedDotSize, 2.4, 1, 4),
    snapToGrid: typeof raw.snapToGrid === 'boolean' ? raw.snapToGrid : false,
    minimapPinned: typeof raw.minimapPinned === 'boolean' ? raw.minimapPinned : false,
    performanceHudEnabled:
      typeof raw.performanceHudEnabled === 'boolean' ? raw.performanceHudEnabled : false,
  };
}

function readPersistedSettings(): CanvasViewSettings {
  if (typeof window === 'undefined') return DEFAULT_CANVAS_VIEW_SETTINGS;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    return raw ? normalizeSettings(JSON.parse(raw)) : DEFAULT_CANVAS_VIEW_SETTINGS;
  } catch {
    return DEFAULT_CANVAS_VIEW_SETTINGS;
  }
}

function persistSettings(settings: CanvasViewSettings): void {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(settings));
  } catch {
    // A canvas preference must never interrupt editing when storage is unavailable.
  }
}

export const useCanvasViewSettingsStore = create<CanvasViewSettingsState>((set, get) => {
  const update = (patch: Partial<CanvasViewSettings>) => {
    const next = normalizeSettings({ ...get(), ...patch });
    persistSettings(next);
    set(next);
  };

  return {
    ...readPersistedSettings(),
    setCanvasTheme: (canvasTheme) => update({ canvasTheme }),
    setCustomCanvasColor: (customCanvasColor) => update({
      canvasTheme: 'custom',
      customCanvasColor,
    }),
    setEdgeColor: (edgeColor) => update({ edgeColor }),
    setShowGrid: (showGrid) => update({ showGrid }),
    setGridGap: (gridGap) => update({ gridGap }),
    setGridDotSize: (gridDotSize) => update({ gridDotSize }),
    setSnapToGrid: (snapToGrid) => update({ snapToGrid }),
    setMinimapPinned: (minimapPinned) => update({ minimapPinned }),
    setPerformanceHudEnabled: (performanceHudEnabled) => update({ performanceHudEnabled }),
    reset: () => {
      persistSettings(DEFAULT_CANVAS_VIEW_SETTINGS);
      set(DEFAULT_CANVAS_VIEW_SETTINGS);
    },
  };
});
