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
  /** The project library is the library work surface on the home route. */
  projectLibraryOpen: boolean;
  taskPanelOpen: boolean;
  taskPanelHeight: number;
  aiAssistantOpen: boolean;
  aiAssistantWidth: number;
  toggleAiAssistant: () => void;
  setLanguage: (lang: string) => void;
  setTheme: (theme: Theme) => void;
  setDashboardTab: (tab: ProjectStatus) => void;
  setDashboardView: (view: DashboardView) => void;
  setProjectLibraryOpen: (open: boolean) => void;
  setTaskPanelOpen: (open: boolean) => void;
  setTaskPanelHeight: (h: number) => void;
  setAiAssistantOpen: (open: boolean) => void;
  setAiAssistantWidth: (w: number) => void;
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

export const useAppStore = create<AppState>()(
  persist(
    (set) => ({
      language: "zh",
      theme: "dark",
      dashboardTab: "active",
      dashboardView: "list",
      projectLibraryOpen: false,
      aiAssistantOpen: false,
      aiAssistantWidth: AI_ASSISTANT_WIDTH_DEFAULT,
      toggleAiAssistant: () =>
        set((s) => ({ aiAssistantOpen: !s.aiAssistantOpen })),
      setLanguage: (lang) => set({ language: lang }),
      setTheme: (theme) => set({ theme }),
      setDashboardTab: (tab) => set({ dashboardTab: tab }),
      setDashboardView: (view) => set({ dashboardView: view }),
      setProjectLibraryOpen: (open) => set({ projectLibraryOpen: open }),
      taskPanelOpen: false,
      taskPanelHeight: TASK_PANEL_HEIGHT_DEFAULT,
      setTaskPanelOpen: (open) => set({ taskPanelOpen: open }),
      setAiAssistantOpen: (open) => set({ aiAssistantOpen: open }),
      setAiAssistantWidth: (w) => set({ aiAssistantWidth: clampAiAssistantWidth(w) }),
      setTaskPanelHeight: (h) => set({ taskPanelHeight: clampTaskPanelHeight(h) }),
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
        if (fromVersion < 8) {
          // 搭子系统已整体移除，旧宠物字段不再使用；顺手清掉 Piko 遗留字段。
          delete base.pikoAccessory;
          delete base.companionKind;
          delete base.companionPet;
          delete base.companionXPercent;
          delete base.companionYPercent;
          delete base.companionHidden;
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
        const { projectLibraryOpen: _library, ...persisted } = state;
        return persisted;
      },
    },
  ),
);
