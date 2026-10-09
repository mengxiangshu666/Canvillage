// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useRouterState } from "@tanstack/react-router";
import { ArrowLeft, Check, ChevronDown, Frame, Workflow } from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  isProjectModeEntryActive,
  PROJECT_SECTION_ROUTES,
  projectModeFromPath,
  projectSectionFromPath,
  resolveProjectWorkflowEntryRoute,
  type ProjectMode,
} from "@/components/layout/project-navigation-routes";
import { normalizeLastEpisodeLocation, useEpisodeWorkbenchStore } from "@/stores/episode-workbench-store";
import { isRememberedSection, useProjectNavStore } from "@/stores/project-nav-store";
import { useAllProjectSummaries } from "@/lib/queries/projects";
import { getProjectCover } from "@/lib/project-cover";
import { canvasOnlyProduct } from "@/lib/product-mode";
import { cn } from "@/lib/utils";

const workflowMenuItems = [
  { labelKey: "nav.productionCenter", to: PROJECT_SECTION_ROUTES.production },
  { labelKey: "nav.storyLab", to: PROJECT_SECTION_ROUTES.storyLab },
  { labelKey: "nav.ingest", to: PROJECT_SECTION_ROUTES.ingest },
  { labelKey: "nav.assets", to: PROJECT_SECTION_ROUTES.characters },
  {
    labelKey: "nav.episodes",
    to: PROJECT_SECTION_ROUTES.episodes,
    rememberKey: "episodes",
  },
  { labelKey: "nav.aiAssistant", to: PROJECT_SECTION_ROUTES.assistant },
  { labelKey: "nav.styles", to: PROJECT_SECTION_ROUTES.styles },
] as const;
const villageCanvasWorkflowMenuItems = [
  { label: "总控", to: PROJECT_SECTION_ROUTES.production },
  {
    label: "故事",
    to: PROJECT_SECTION_ROUTES.storyLab,
    activeRoutes: [PROJECT_SECTION_ROUTES.storyLab, PROJECT_SECTION_ROUTES.ingest],
  },
  { label: "资产", to: PROJECT_SECTION_ROUTES.characters },
  {
    label: "分镜",
    to: PROJECT_SECTION_ROUTES.episodes,
    rememberKey: "episodes",
  },
  { label: "制作", to: PROJECT_SECTION_ROUTES.making },
] as const;
const visibleWorkflowMenuItems = canvasOnlyProduct
  ? villageCanvasWorkflowMenuItems
  : workflowMenuItems;

function ProjectAvatar({ name }: { name: string }) {
  const { gradient, initial } = useMemo(() => getProjectCover(name), [name]);
  return (
    <span
      className="flex size-5 shrink-0 items-center justify-center rounded text-xs font-bold text-white/95"
      style={{ background: gradient }}
    >
      {initial}
    </span>
  );
}

export function ProjectSwitcher({ current }: { current: string }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { data: summaries } = useAllProjectSummaries();
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  const targetSection = projectSectionFromPath(pathname) ?? "freezone";
  const [open, setOpen] = useState(false);
  const closeTimerRef = useRef<number | null>(null);
  const projects = useMemo(
    () =>
      (summaries ?? [])
        .filter((project) => project.status === "active")
        .map((project) => ({ id: project.id || project.name, name: project.name })),
    [summaries],
  );
  const currentSummary = useMemo(
    () =>
      projects.find((project) => project.id === current) ??
      projects.find((project) => project.name === current),
    [current, projects],
  );
  const currentName = currentSummary?.name ?? current;

  const cancelClose = () => {
    if (closeTimerRef.current === null) return;
    window.clearTimeout(closeTimerRef.current);
    closeTimerRef.current = null;
  };
  const openMenu = () => {
    cancelClose();
    setOpen(true);
  };
  const scheduleClose = () => {
    cancelClose();
    closeTimerRef.current = window.setTimeout(() => {
      setOpen(false);
      closeTimerRef.current = null;
    }, 120);
  };

  useEffect(() => cancelClose, []);

  return (
    <DropdownMenu open={open} onOpenChange={setOpen}>
      <DropdownMenuTrigger
        onMouseEnter={openMenu}
        onMouseLeave={scheduleClose}
        className="inline-flex h-8 max-w-[156px] cursor-pointer items-center gap-1.5 bg-transparent px-1 text-left text-[13px] leading-none text-sidebar-foreground/90 transition-colors hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sidebar-ring"
      >
        <span className="min-w-0 truncate leading-none">{currentName}</span>
        <ChevronDown className="size-3.5 shrink-0 translate-y-px text-muted-foreground" />
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="start"
        sideOffset={8}
        onMouseEnter={openMenu}
        onMouseLeave={scheduleClose}
        className="w-56 rounded-md border border-white/10 bg-popover p-1 shadow-xl shadow-black/20 ring-0"
      >
        <DropdownMenuGroup>
          <DropdownMenuItem
            onClick={() => navigate({ to: "/" })}
            className="min-h-8 gap-2 rounded-sm px-2 py-1.5 text-xs focus:bg-white/8 focus:text-current"
          >
            <ArrowLeft className="size-3.5" />
            {t("project.dashboardReturn")}
          </DropdownMenuItem>
        </DropdownMenuGroup>
        <DropdownMenuSeparator />
        <DropdownMenuGroup>
          <DropdownMenuLabel className="px-2 py-1.5 text-xs font-medium text-muted-foreground">
            {t("nav.switchProject")}
          </DropdownMenuLabel>
          {projects.map((project) => (
            <DropdownMenuItem
              key={project.id}
              onClick={() =>
                navigate({
                  to: PROJECT_SECTION_ROUTES[targetSection],
                  params: { project: project.id },
                })
              }
              className="min-h-8 gap-2 rounded-sm px-2 py-1.5 text-xs focus:bg-white/8 focus:text-current"
            >
              <ProjectAvatar name={project.name} />
              <span className="flex-1 truncate">{project.name}</span>
              {project.id === current ? (
                <Check className="size-3.5 text-primary" aria-hidden />
              ) : null}
            </DropdownMenuItem>
          ))}
        </DropdownMenuGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

/**
 * 「村长工作流」子页菜单，作为 header 的第二行渲染 —— 它必须在文档流里占真实高度，
 * 而不是浮在内容之上：内容区是独立滚动容器，任何浮层都会被滚上来的内容穿过。
 */
export function ProjectWorkflowMenu({ project }: { project: string }) {
  const { t } = useTranslation();
  const pathname = useRouterState({ select: (state) => state.location.pathname });
  const rememberedEpisodeLocation = useEpisodeWorkbenchStore(
    (state) => state.lastEpisodeLocationByProject[project],
  );

  if (projectModeFromPath(pathname) !== "workflow") return null;

  return (
    <div className="village-workflow-section-strip flex justify-center px-4 pb-2">
      <nav
        aria-label={t("nav.workflowMenu")}
        className="village-workflow-section-navigation flex items-center gap-1 whitespace-nowrap px-1.5 py-1 text-sidebar-foreground"
      >
        {visibleWorkflowMenuItems.map((item) => {
          const target =
            "rememberKey" in item && rememberedEpisodeLocation
              ? normalizeLastEpisodeLocation(project, rememberedEpisodeLocation) ?? item.to
              : item.to;
          // 高亮按栏目自身的路由判断：target 可能是带 ?query 的剧集深链，
          // 拿它比 pathname 永远不相等（剧集深链里就不会高亮）。
          const activeRoutes =
            "activeRoutes" in item ? item.activeRoutes : [item.to];
          const active = activeRoutes.some((route) => {
            const sectionPath = route.replace("$project", encodeURIComponent(project));
            return pathname === sectionPath || pathname.startsWith(`${sectionPath}/`);
          });
          return (
            <Link
              key={item.to}
              to={target}
              params={{ project }}
              className={cn(
                "village-workflow-section-navigation__link flex h-7 items-center px-2.5 text-xs font-semibold transition-[color,background-color,border-color,box-shadow,transform] duration-150 ease-[var(--ease-out-quint)]",
                active
                  ? "is-active text-foreground"
                  : "text-muted-foreground hover:text-foreground",
              )}
              aria-current={active ? "page" : undefined}
            >
              {"label" in item ? item.label : t(item.labelKey)}
            </Link>
          );
        })}
      </nav>
    </div>
  );
}

export function ProjectHeaderNavigation({ project }: { project: string }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const pathname = useRouterState({ select: (state) => state.location.pathname });
  const activeMode = projectModeFromPath(pathname);
  const rememberedEpisodeLocation = useEpisodeWorkbenchStore(
    (state) => state.lastEpisodeLocationByProject[project],
  );
  const setLastEpisodeLocation = useEpisodeWorkbenchStore((state) => state.setLastEpisodeLocation);
  const clearLastEpisodeLocation = useEpisodeWorkbenchStore((state) => state.clearLastEpisodeLocation);
  const rememberSection = useProjectNavStore((state) => state.rememberSection);
  const lastWorkflowSection = useProjectNavStore(
    (state) => state.lastWorkflowSectionByProject[project],
  );

  // 记住当前停留的区块（画布 / 工作流子页），切换模式时恢复上次现场。
  useEffect(() => {
    const section = projectSectionFromPath(pathname);
    if (isRememberedSection(section)) {
      rememberSection(project, section);
    }
  }, [pathname, project, rememberSection]);

  useEffect(() => {
    const episodesRoot = `/projects/${encodeURIComponent(project)}/episodes`;
    if (pathname === episodesRoot) {
      clearLastEpisodeLocation(project);
      return;
    }
    const match = pathname.match(/^\/projects\/([^/]+)\/episodes\/(\d+)(?:\/|$)/);
    if (!match || decodeURIComponent(match[1]) !== project) return;
    setLastEpisodeLocation(project, `${pathname}${window.location.search}`);
  }, [clearLastEpisodeLocation, pathname, project, setLastEpisodeLocation]);

  const changeMode = (mode: ProjectMode) => {
    if (isProjectModeEntryActive(pathname, mode, canvasOnlyProduct)) return;
    if (mode === "canvas") {
      navigate({ to: PROJECT_SECTION_ROUTES.freezone, params: { project } });
      return;
    }
    // 独立产品从画布进入生产总控；兼容产品继续恢复上次生产子页。
    let target: string = resolveProjectWorkflowEntryRoute(
      canvasOnlyProduct,
      lastWorkflowSection,
    );
    if (!canvasOnlyProduct && lastWorkflowSection === "episodes" && rememberedEpisodeLocation) {
      target =
        normalizeLastEpisodeLocation(project, rememberedEpisodeLocation) ?? target;
    }
    navigate({ to: target, params: { project } });
  };

  return (
    <nav
      aria-label={t("nav.creationMode")}
      className="village-project-mode-navigation absolute left-1/2 top-1/2 z-30 flex -translate-x-1/2 -translate-y-1/2 items-center"
    >
      <div
        className="village-project-mode-switcher relative flex items-center"
        data-active-mode={activeMode}
      >
        <span
          aria-hidden="true"
          className="village-project-mode-switcher__indicator"
        />
        <button
          type="button"
          onClick={() => changeMode("canvas")}
          className={cn(
            "village-project-mode-switcher__button relative z-10 inline-flex items-center justify-center",
            activeMode === "canvas" && "is-active",
          )}
          aria-pressed={activeMode === "canvas"}
          data-active={activeMode === "canvas" ? "true" : "false"}
        >
          <Frame className="size-3.5" />
          {canvasOnlyProduct ? "画布" : t("nav.freezone")}
        </button>
        <button
          type="button"
          onClick={() => changeMode("workflow")}
          className={cn(
            "village-project-mode-switcher__button relative z-10 inline-flex items-center justify-center",
            activeMode === "workflow" && "is-active",
          )}
          aria-pressed={activeMode === "workflow"}
          data-active={activeMode === "workflow" ? "true" : "false"}
        >
          <Workflow className="size-3.5" />
          {canvasOnlyProduct ? "工作流" : t("nav.workflow")}
        </button>
      </div>
    </nav>
  );
}
