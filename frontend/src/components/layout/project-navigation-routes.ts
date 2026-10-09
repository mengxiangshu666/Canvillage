// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab

export const PROJECT_SECTION_ROUTES = {
  freezone: "/projects/$project/freezone",
  production: "/projects/$project/production",
  storyLab: "/projects/$project/story-lab",
  ingest: "/projects/$project/ingest",
  characters: "/projects/$project/characters",
  episodes: "/projects/$project/episodes",
  making: "/projects/$project/making",
  styles: "/projects/$project/styles",
  tasks: "/projects/$project/tasks",
  assistant: "/projects/$project/assistant",
} as const;

export type ProjectSection = keyof typeof PROJECT_SECTION_ROUTES;
export type ProjectMode = "canvas" | "workflow";

export function projectSectionFromPath(pathname: string): ProjectSection | null {
  const segment = pathname.match(/^\/projects\/[^/]+\/([^/]+)/)?.[1];
  if (segment === "story-lab") return "storyLab";
  return segment && segment in PROJECT_SECTION_ROUTES
    ? (segment as ProjectSection)
    : null;
}

export function projectModeFromPath(pathname: string): ProjectMode {
  return projectSectionFromPath(pathname) === "freezone" ? "canvas" : "workflow";
}

/**
 * 顶部一级入口是否已经处在对应产品模式。
 * 工作流下的总控和六个专业生产栏目都属于同一个模式，切换按钮不应把用户带离当前栏目。
 */
export function isProjectModeEntryActive(
  pathname: string,
  mode: ProjectMode,
  _isCanvasOnlyProduct: boolean,
): boolean {
  const section = projectSectionFromPath(pathname);
  if (mode === "canvas") return section === "freezone";
  return section !== null && section !== "freezone";
}

/** 村长无限画布固定进入生产总控；兼容产品仍恢复上次生产子页。 */
export function resolveProjectWorkflowEntryRoute(
  isCanvasOnlyProduct: boolean,
  lastWorkflowSection?: ProjectSection | null,
): (typeof PROJECT_SECTION_ROUTES)[ProjectSection] {
  if (isCanvasOnlyProduct) return PROJECT_SECTION_ROUTES.production;
  if (
    lastWorkflowSection &&
    lastWorkflowSection !== "freezone" &&
    lastWorkflowSection !== "tasks"
  ) {
    return PROJECT_SECTION_ROUTES[lastWorkflowSection];
  }
  return PROJECT_SECTION_ROUTES.production;
}
