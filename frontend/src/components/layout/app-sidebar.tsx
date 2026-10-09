// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { Link, useNavigate, useParams, useRouterState } from "@tanstack/react-router";
import {
  Frame,
  ListChecks,
  Plus,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  PROJECT_SECTION_ROUTES,
  projectSectionFromPath,
} from "@/components/layout/project-navigation-routes";
import { APP_VERSION } from "@/lib/app-version";
import { appBrandName } from "@/lib/product-mode";

/**
 * 侧栏与首页之间的解耦通道：首页持有「全部项目 / 新建项目」的真实状态，
 * 侧栏只发意图，不复制一份创建逻辑，避免两处对话框各自维护一份草稿。
 */
export type HomeViewIntent = "home" | "all" | "new";

export const HOME_VIEW_EVENT = "village:home-view";

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
  { id: "freezone", labelKey: "nav.freezone", fallback: "画布", icon: Frame },
  { id: "tasks", labelKey: "nav.tasks", fallback: "任务管理", icon: ListChecks },
] as const;

export function AppSidebar() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const params = useParams({ strict: false }) as { project?: string };
  const pathname = useRouterState({ select: (state) => state.location.pathname });
  const project = params.project ?? null;
  const activeSection = projectSectionFromPath(pathname);

  const openHomeView = (intent: HomeViewIntent) => {
    requestHomeView(intent);
    if (pathname !== "/") {
      void navigate({ to: "/" });
    }
  };

  return (
    <aside className="village-sidebar flex min-h-0 flex-col" aria-label={t("nav.home", "首页")}>
      <div className="flex h-[52px] shrink-0 items-center gap-2.5 px-4">
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
        <span className="truncate text-[13px] font-semibold tracking-[-0.01em] text-sidebar-foreground">
          {appBrandName}
        </span>
      </div>

      <div className="shrink-0 px-3 pb-3">
        <button
          type="button"
          onClick={() => openHomeView("new")}
          className="flex h-9 w-full items-center justify-center gap-1.5 rounded-lg bg-primary text-[13px] font-semibold text-primary-foreground transition-[filter] duration-150 hover:brightness-110 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sidebar-ring"
        >
          <Plus className="size-4" aria-hidden="true" />
          {t("project.create", "新建项目")}
        </button>
      </div>

      <nav
        className="village-scroll flex min-h-0 flex-1 flex-col gap-0.5 overflow-y-auto px-3 pb-3"
        aria-label={t("nav.home", "首页")}
      >
        <Link
          to="/"
          data-active={pathname === "/" ? "true" : "false"}
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
          className="village-nav-item w-full text-left"
        >
          <span className="flex size-4 items-center justify-center" aria-hidden="true">
            <svg viewBox="0 0 16 16" fill="none" className="size-4">
              <rect x="2.5" y="3" width="11" height="3.2" rx="1.1" stroke="currentColor" strokeWidth="1.4" />
              <rect x="2.5" y="9.8" width="11" height="3.2" rx="1.1" stroke="currentColor" strokeWidth="1.4" />
            </svg>
          </span>
          {t("nav.allProjects", "全部项目")}
        </button>

        {project ? (
          <>
            <div className="village-nav-group-label pb-1 pt-4">{t("nav.sectionProject", "项目")}</div>
            {projectNavItems.map((item) => {
              const active = activeSection === item.id;
              return (
                <Link
                  key={item.id}
                  to={PROJECT_SECTION_ROUTES[item.id]}
                  params={{ project }}
                  data-active={active ? "true" : "false"}
                  className="village-nav-item"
                >
                  <item.icon className="size-4" aria-hidden="true" />
                  {t(item.labelKey, item.fallback)}
                </Link>
              );
            })}
          </>
        ) : null}
      </nav>

      <div className="shrink-0 border-t border-sidebar-border p-3">
        <p className="px-2.5 pt-2 text-[11px] tabular-nums text-sidebar-foreground/32">
          {APP_VERSION}
        </p>
      </div>
    </aside>
  );
}
