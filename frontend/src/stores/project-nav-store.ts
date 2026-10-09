// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";
import { quotaSafeStateStorage } from "@/lib/localStorageQuota";

import type { ProjectSection } from "@/components/layout/project-navigation-routes";

/**
 * 记住每个项目的导航位置：
 * - lastSectionByProject：项目内最后停留的区块（画布 freezone 或工作流各子页），
 *   进入项目时恢复到这里，而不是固定落到画布。
 * - lastWorkflowSectionByProject：最后停留的工作流子页（总控/故事创作/剧本导入/项目资产/分镜/风格），
 *   顶部切到「工作流」时恢复到这里，而不是固定落到某个页面。
 */

/** 可被记忆的区块：画布 + 工作流核心子页（tasks 等其它路由不参与记忆）。 */
const REMEMBERED_SECTIONS = new Set<ProjectSection>([
  "freezone",
  "production",
  "storyLab",
  "ingest",
  "characters",
  "episodes",
  "making",
  "styles",
]);

export type WorkflowSection = Exclude<
  ProjectSection,
  "freezone" | "tasks" | "assistant"
>;

/** v2 之前持久化用的字段名（读旧写新）。 */
// identity-allow: 上一行的字段名必须逐字保留，否则老数据读不回来。
const LEGACY_WORKFLOW_SECTION_FIELD = "lastXiajiSectionByProject"; // identity-allow: 旧持久化键名

export function migrateLegacyProjectNavState(persisted: unknown): {
  lastSectionByProject: Record<string, ProjectSection>;
  lastWorkflowSectionByProject: Record<string, WorkflowSection>;
} {
  const base = (persisted ?? {}) as Partial<ProjectNavState> & Record<string, unknown>;
  const normalizeSection = (value: unknown): ProjectSection | null => {
    if (value === "assistant") return "production";
    if (typeof value !== "string") return null;
    const section = value as ProjectSection;
    return REMEMBERED_SECTIONS.has(section) ? section : null;
  };
  const lastSectionByProject: Record<string, ProjectSection> = {};
  for (const [project, value] of Object.entries(base.lastSectionByProject ?? {})) {
    const section = normalizeSection(value);
    if (section) lastSectionByProject[project] = section;
  }
  const persistedWorkflowSections =
    base.lastWorkflowSectionByProject ?? base[LEGACY_WORKFLOW_SECTION_FIELD] ?? {};
  const lastWorkflowSectionByProject: Record<string, WorkflowSection> = {};
  for (const [project, value] of Object.entries(
    persistedWorkflowSections as Record<string, unknown>,
  )) {
    const section = normalizeSection(value);
    if (section && section !== "freezone") {
      lastWorkflowSectionByProject[project] = section as WorkflowSection;
    }
  }
  return { lastSectionByProject, lastWorkflowSectionByProject };
}

export function isRememberedSection(
  section: ProjectSection | null,
): section is ProjectSection {
  return section !== null && REMEMBERED_SECTIONS.has(section);
}

interface ProjectNavState {
  lastSectionByProject: Record<string, ProjectSection>;
  lastWorkflowSectionByProject: Record<string, WorkflowSection>;
  rememberSection: (project: string, section: ProjectSection) => void;
  reset: () => void;
}

export const useProjectNavStore = create<ProjectNavState>()(
  persist(
    (set) => ({
      lastSectionByProject: {},
      lastWorkflowSectionByProject: {},
      rememberSection: (project, section) =>
        set((state) => {
          if (!project || !REMEMBERED_SECTIONS.has(section)) return state;
          const next: Partial<ProjectNavState> = {
            lastSectionByProject: {
              ...state.lastSectionByProject,
              [project]: section,
            },
          };
          if (section !== "freezone") {
            next.lastWorkflowSectionByProject = {
              ...state.lastWorkflowSectionByProject,
              [project]: section as WorkflowSection,
            };
          }
          return next as ProjectNavState;
        }),
      reset: () =>
        set({ lastSectionByProject: {}, lastWorkflowSectionByProject: {} }),
    }),
    {
      name: "village-project-nav",
      // identity-allow: v3 把 `lastXiajiSectionByProject` 改名成
      // `lastWorkflowSectionByProject`；升版本号才会对 v2 的老 store 触发 migrate。
      version: 3,
      storage: createJSONStorage(() => quotaSafeStateStorage),
      migrate: (persisted: unknown) => migrateLegacyProjectNavState(persisted),
    },
  ),
);
