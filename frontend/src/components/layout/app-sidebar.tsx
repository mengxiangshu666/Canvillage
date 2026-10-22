// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { Link, useNavigate, useParams, useRouterState } from "@tanstack/react-router";
import {
  Bot,
  BookOpenText,
  Clapperboard,
  Film,
  FolderOpen,
  Frame,
  LayoutDashboard,
  ListChecks,
  Plus,
  UsersRound,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  PROJECT_SECTION_ROUTES,
  projectSectionFromPath,
} from "@/components/layout/project-navigation-routes";
import { APP_VERSION } from "@/lib/app-version";
import { appBrandName } from "@/lib/product-mode";
import { useAppStore } from "@/stores/app-store";

/**
 * 侧栏与首页之间的解耦通道：首页持有「全部项目 / 新建项目」的真实状态，
 * 侧栏只发意图，不复制一份创建逻辑，避免两处对话框各自维护一份草稿。
 */
export type HomeViewIntent = "home" | "all" | "new";

export const HOME_VIEW_EVENT = "village:home-view";
export const COMPANION_MANAGER_EVENT = "village:open-companion-manager";

// 跨路由时首页还没挂载，事件会被丢掉；先把意图存成模块级待办，首页挂载后再取走。
let pendingHomeView: HomeViewIntent | null = null;

export function requestHomeView(intent: HomeViewIntent) {
  pendingHomeView = intent;
  window.dispatchEvent(new CustomEvent<HomeViewIntent>(HOME_VIEW_EVENT, { detail: intent }));
}

export function consumePendingHomeView(): HomeViewIntent | null {
  const intent = pendingHomeView;
  pendingHomeView = null;
  return intent;
}

const projectNavItems = [
  { id: "freezone", label: "画布", icon: Frame },
  { id: "production", label: "总控", icon: LayoutDashboard },
  { id: "storyLab", label: "故事", icon: BookOpenText },
  { id: "characters", label: "资产", icon: UsersRound },
  { id: "episodes", label: "分镜", icon: Clapperboard },
  { id: "making", label: "制作", icon: Film },
] as const;

export function AppSidebar() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const params = useParams({ strict: false }) as { project?: string };
  const pathname = useRouterState({ select: (state) => state.location.pathname });
  const project = params.project ?? null;
  const activeSection = projectSectionFromPath(pathname);
  const projectLibraryOpen = useAppStore((state) => state.projectLibraryOpen);
  const setProjectLibraryOpen = useAppStore((state) => state.setProjectLibraryOpen);
  const setTaskPanelOpen = useAppStore((state) => state.setTaskPanelOpen);
  const projectName = project && project.length <= 16 ? project : "当前制作项目";

  const openHomeView = (intent: HomeViewIntent) => {
    setProjectLibraryOpen(intent !== "home");
    requestHomeView(intent);
    if (pathname !== "/") {
      void navigate({ to: "/" });
    }
  };

  return (
    <aside className="village-sidebar flex min-h-0 flex-col" aria-label={t("nav.home", "首页")}>
      <div className="village-sidebar__brand flex h-[60px] shrink-0 items-center gap-2.5 px-4">
        <img
          src="/brand/village-canvas-mark-dark.png"
          alt=""
          aria-hidden="true"
          className="size-[22px] shrink-0 rounded-[6px] object-contain dark:hidden"
        />
        <img
          src="/brand/village-canvas-mark-light.png"
          alt=""
          aria-hidden="true"
          className="hidden size-[22px] shrink-0 rounded-[6px] object-contain dark:block"
        />
        <span className="truncate text-[14px] font-semibold tracking-[-0.015em] text-sidebar-foreground">
          {appBrandName}
        </span>
      </div>

      <nav
        className="village-scroll flex min-h-0 flex-1 flex-col overflow-y-auto px-3 pb-3 pt-2"
        aria-label={t("nav.home", "首页")}
      >
        <div className="village-nav-group-label pb-2 pt-1">工作台</div>
        <Link
          to="/"
          onClick={() => setProjectLibraryOpen(false)}
          data-active={pathname === "/" && !projectLibraryOpen ? "true" : "false"}
          className="village-nav-item"
        >
          <span className="flex size-4 items-center justify-center" aria-hidden="true">
            <svg viewBox="0 0 16 16" fill="none" className="size-4">
              <path
                d="M2.5 6.5 8 2.5l5.5 4v6a1 1 0 0 1-1 1h-9a1 1 0 0 1-1-1v-6Z"
                stroke="currentColor"
                strokeWidth="1.4"
                strokeLinejoin="round"
              />
            </svg>
          </span>
          {t("nav.home", "首页")}
        </Link>
        <button
          type="button"
          onClick={() => openHomeView("all")}
          data-active={pathname === "/" && projectLibraryOpen ? "true" : "false"}
          className="village-nav-item w-full text-left"
        >
          <FolderOpen className="size-4" aria-hidden="true" />
          {t("nav.projectLibrary", "项目库")}
        </button>
        <button
          type="button"
          onClick={() => setTaskPanelOpen(true)}
          className="village-nav-item w-full text-left"
        >
          <ListChecks className="size-4" aria-hidden="true" />
          {t("nav.taskHub", "任务")}
        </button>

        <div className="village-nav-group-label pb-2 pt-5">创作</div>
        <button
          type="button"
          onClick={() => openHomeView("new")}
          data-active="false"
          className="village-nav-item w-full text-left"
        >
          <Plus className="size-4" aria-hidden="true" />
          新建画布
        </button>
        <button
          type="button"
          onClick={() => window.dispatchEvent(new Event(COMPANION_MANAGER_EVENT))}
          data-active="false"
          className="village-nav-item w-full text-left"
        >
          <Bot className="size-4" aria-hidden="true" />
          管理搭子
        </button>

        {project ? (
          <>
            <div className="village-sidebar-project">
              <span className="village-sidebar-project__eyebrow">当前项目</span>
              <strong title={projectName}>{projectName}</strong>
            </div>
            <div className="village-nav-group-label village-nav-group-label--project">制作阶段</div>
            {projectNavItems.map((item) => (
              <Link key={item.id} to={PROJECT_SECTION_ROUTES[item.id]} params={{ project: project as string }} data-active={activeSection === item.id ? "true" : "false"} className="village-nav-item">
                <item.icon className="size-4" aria-hidden="true" />
                {item.label}
              </Link>
            ))}
            <Link to={PROJECT_SECTION_ROUTES.tasks} params={{ project }} data-active={activeSection === "tasks" ? "true" : "false"} className="village-nav-item mt-2">
              <ListChecks className="size-4" aria-hidden="true" />任务记录
            </Link>
          </>
        ) : null}
      </nav>

      <div className="village-sidebar__footer shrink-0 px-5 py-3.5">
        <p className="text-[10px] tabular-nums text-sidebar-foreground/32">
          {APP_VERSION}
        </p>
      </div>
    </aside>
  );
}
