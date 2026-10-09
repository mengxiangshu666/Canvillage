// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
/**
 * app-store: cross-region UX chrome.
 *
 * This store is NOT purged on region switch. Keep it strictly region-agnostic:
 * theme, language, dashboard filters, etc. Do NOT add region IDs,
 * project IDs, episode IDs, or any region-specific content here — it will bleed
 * across region switches and cause data confusion. For region-scoped state, add
 * a new store and include it in `src/lib/reset-region-state.ts`.
 */
import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";
import { quotaSafeStateStorage } from "@/lib/localStorageQuota";
import { DEFAULT_BUILTIN_PET_SLUG } from "@/features/companion/petdex/petdex-pets";
import type { ProjectStatus } from "@/types/project";

export const TASK_PANEL_HEIGHT_MIN = 200;
export const TASK_PANEL_HEIGHT_DEFAULT = 400;
export const TASK_PANEL_HEIGHT_MAX_VH = 0.7;
export const AI_ASSISTANT_WIDTH_MIN = 440;
export const AI_ASSISTANT_WIDTH_DEFAULT = 640;
export const AI_ASSISTANT_WIDTH_MAX = 760;

export type Theme = "light" | "gray" | "dark" | "system";
export type DashboardView = "card" | "list";

interface AppState {
  language: string;
  theme: Theme;
  dashboardTab: ProjectStatus;
  dashboardView: DashboardView;
  taskPanelOpen: boolean;
  taskPanelHeight: number;
  aiAssistantOpen: boolean;
  aiAssistantWidth: number;
  /**
   * 顶部陪伴形象：某只 petdex 宠物的 slug（内置目录与用户导入共用这一套）。
   * 属全局 UI 偏好（region-agnostic），随 village-app 持久化、不随区域切换清除。
   */
  companionKind: string;
  /**
   * 选中的宠物（来自 manifest，含同源精灵图地址）。导入宠物的 spritesheetUrl 是会话级
   * blob，刷新后按 slug 重解析；内置宠物以目录 pets.json 为准。
   */
  companionPet: {
    slug: string;
    displayName: string;
    spritesheetUrl: string;
    submittedBy?: string;
    cols?: number;
    rows?: number;
    /** 导入宠物（IndexedDB）。spritesheetUrl 是会话级 blob，刷新后按 slug 重解析。 */
    imported?: boolean;
  } | null;
  /**
   * 陪伴形象在视口中的位置（占视口宽/高的百分比 0–100），可在整页任意拖动。
   * null = 未拖动过，用默认落点。拖动后写入并持久化。属全局 UI 偏好。
   */
  companionXPercent: number | null;
  companionYPercent: number | null;
  /** 是否隐藏陪伴形象（workbuddy）。true = 全局隐藏。属全局 UI 偏好，持久化。 */
  companionHidden: boolean;
  toggleAiAssistant: () => void;
  setLanguage: (lang: string) => void;
  setTheme: (theme: Theme) => void;
  setDashboardTab: (tab: ProjectStatus) => void;
  setDashboardView: (view: DashboardView) => void;
  setTaskPanelOpen: (open: boolean) => void;
  setTaskPanelHeight: (h: number) => void;
  setAiAssistantOpen: (open: boolean) => void;
  setAiAssistantWidth: (w: number) => void;
  setCompanion: (kind: string, pet: AppState["companionPet"]) => void;
  setCompanionPosition: (xPercent: number, yPercent: number) => void;
  setCompanionHidden: (hidden: boolean) => void;
  /**
   * Re-clamp viewport-relative panel dimensions against the current window size.
   * Call this on window `resize` so a panel sized on a large screen (or restored
   * from persisted state) doesn't exceed the viewport after the window shrinks.
   */
  clampDimensionsToViewport: () => void;
}

function clampAiAssistantWidth(width: number): number {
  const viewportMax =
    typeof window !== "undefined"
      ? Math.max(AI_ASSISTANT_WIDTH_MIN, window.innerWidth - 320)
      : AI_ASSISTANT_WIDTH_MAX;
  const max = Math.min(AI_ASSISTANT_WIDTH_MAX, viewportMax);
  return Math.min(max, Math.max(AI_ASSISTANT_WIDTH_MIN, Math.round(width)));
}

function clampTaskPanelHeight(height: number): number {
  const viewport = typeof window !== "undefined" ? window.innerHeight : 1000;
  const max = Math.floor(viewport * TASK_PANEL_HEIGHT_MAX_VH);
  return Math.min(max, Math.max(TASK_PANEL_HEIGHT_MIN, Math.round(height)));
}

function persistedCompanionPet(pet: AppState["companionPet"]): AppState["companionPet"] {
  if (!pet) return null;
  const spritesheetUrl =
    pet.spritesheetUrl &&
    !pet.spritesheetUrl.startsWith("data:") &&
    !pet.spritesheetUrl.startsWith("blob:") &&
    pet.spritesheetUrl.length <= 2048
      ? pet.spritesheetUrl
      : "";
  return {
    slug: pet.slug,
    displayName: pet.displayName,
    spritesheetUrl,
    submittedBy: pet.submittedBy,
    cols: pet.cols,
    rows: pet.rows,
    imported: pet.imported,
  };
}

export const useAppStore = create<AppState>()(
  persist(
    (set) => ({
      language: "zh",
      theme: "dark",
      dashboardTab: "active",
      dashboardView: "card",
      aiAssistantOpen: false,
      aiAssistantWidth: AI_ASSISTANT_WIDTH_DEFAULT,
      companionKind: DEFAULT_BUILTIN_PET_SLUG,
      companionPet: null,
      companionXPercent: null,
      companionYPercent: null,
      companionHidden: false,
      toggleAiAssistant: () =>
        set((s) => ({ aiAssistantOpen: !s.aiAssistantOpen })),
      setLanguage: (lang) => set({ language: lang }),
      setTheme: (theme) => set({ theme }),
      setDashboardTab: (tab) => set({ dashboardTab: tab }),
      setDashboardView: (view) => set({ dashboardView: view }),
      taskPanelOpen: false,
      taskPanelHeight: TASK_PANEL_HEIGHT_DEFAULT,
      setTaskPanelOpen: (open) => set({ taskPanelOpen: open }),
      setAiAssistantOpen: (open) => set({ aiAssistantOpen: open }),
      setAiAssistantWidth: (w) => set({ aiAssistantWidth: clampAiAssistantWidth(w) }),
      setTaskPanelHeight: (h) => set({ taskPanelHeight: clampTaskPanelHeight(h) }),
      setCompanion: (kind, pet) => set({ companionKind: kind, companionPet: pet }),
      setCompanionPosition: (xPercent, yPercent) =>
        set({ companionXPercent: xPercent, companionYPercent: yPercent }),
      setCompanionHidden: (hidden) => set({ companionHidden: hidden }),
      clampDimensionsToViewport: () =>
        set((s) => {
          const aiAssistantWidth = clampAiAssistantWidth(s.aiAssistantWidth);
          const taskPanelHeight = clampTaskPanelHeight(s.taskPanelHeight);
          if (
            aiAssistantWidth === s.aiAssistantWidth &&
            taskPanelHeight === s.taskPanelHeight
          ) {
            return s;
          }
          return { aiAssistantWidth, taskPanelHeight };
        }),
    }),
    {
      name: "village-app",
      storage: createJSONStorage(() => quotaSafeStateStorage),
      version: 8,
      migrate: (persisted: unknown, fromVersion: number) => {
        const base = (persisted ?? {}) as Record<string, unknown>;
        delete base.sidebarCollapsed;
        delete base.sidebarWidth;
        if (fromVersion < 6) {
          base.companionXPercent = null;
          base.companionYPercent = null;
        }
        if (fromVersion < 4 && base.companionKind == null) {
          base.companionKind = DEFAULT_BUILTIN_PET_SLUG;
        }
        if (fromVersion < 8) {
          // Piko 与三只第三方宠物已下架：旧选择（含已不存在的 slug）一律回到默认搭子，
          // 免得留下一个解析不到的空形象。
          delete base.pikoAccessory;
          base.companionKind = DEFAULT_BUILTIN_PET_SLUG;
          base.companionPet = null;
        }
        if (fromVersion < 1) {
          return {
            ...base,
            taskPanelOpen: base.taskPanelOpen ?? false,
            taskPanelHeight: base.taskPanelHeight ?? TASK_PANEL_HEIGHT_DEFAULT,
            aiAssistantOpen: base.aiAssistantOpen ?? false,
            aiAssistantWidth: base.aiAssistantWidth ?? AI_ASSISTANT_WIDTH_DEFAULT,
          };
        }
        if (fromVersion < 2) {
          return {
            ...base,
            aiAssistantOpen: base.aiAssistantOpen ?? false,
            aiAssistantWidth: base.aiAssistantWidth ?? AI_ASSISTANT_WIDTH_DEFAULT,
          };
        }
        if (fromVersion < 3) {
          return {
            ...base,
            aiAssistantWidth: base.aiAssistantWidth ?? AI_ASSISTANT_WIDTH_DEFAULT,
          };
        }
        if (base.taskPanelHeight === 320) {
          return {
            ...base,
            taskPanelHeight: TASK_PANEL_HEIGHT_DEFAULT,
          };
        }
        return base;
      },
      partialize: (state) => {
        const { companionXPercent: _x, companionYPercent: _y, ...persisted } = state;
        return {
          ...persisted,
          companionPet: persistedCompanionPet(state.companionPet),
        };
      },
    },
  ),
);
