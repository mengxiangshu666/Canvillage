// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { createFileRoute, useNavigate, useRouter } from "@tanstack/react-router";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { motion } from "framer-motion";
import {
  ArchiveRestore,
  ArrowLeft,
  ArrowUp,
  BookOpen,
  Brush,
  Clapperboard,
  Film,
  FolderOpen,
  LayoutGrid,
  LayoutTemplate,
  List as ListIcon,
  Loader2,
  Mic,
  MoreHorizontal,
  Paperclip,
  Plus,
  ScanLine,
  Search,
  Share2,
  Sparkles,
  Trash2,
  Undo2,
  Users,
  WandSparkles,
  XIcon,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { fileToImageAttachment } from "@/features/superchat/canvas-agent-references";
import type { ChatAttachment } from "@/features/superchat/types";
import { SkillStoreDialog } from "@/features/superchat/SkillStoreDialog";
import { saveCanvasAgentRunMode } from "@/features/superchat/canvas-agent-run-mode";
import {
  consumePendingHomeView,
  HOME_VIEW_EVENT,
  type HomeViewIntent,
} from "@/components/layout/app-sidebar";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  fetchProjectSummaries,
  useAllProjectSummaries,
  useArchiveProject,
  useCreateProject,
  useProjectCounts,
  usePurgeProject,
  useRestoreProject,
  useSoftDeleteProject,
  useUnarchiveProject,
} from "@/lib/queries/projects";
import { ShareProjectDialog } from "@/components/projects/share-project-dialog";
import { getProjectCover } from "@/lib/project-cover";
import { openFreezoneProject } from "@/lib/freezone-url";
import { formatRelativeTime } from "@/lib/relative-time";
import {
  canDeleteProject,
  canManageProjectGrants,
  isSharedProject,
  projectRole,
} from "@/lib/project-permissions";
import { useAppStore } from "@/stores/app-store";
import { cn } from "@/lib/utils";
import type { DashboardView } from "@/stores/app-store";
import type { ProjectStatus, ProjectSummary } from "@/types/project";
import { PROJECT_SECTION_ROUTES } from "@/components/layout/project-navigation-routes";
import {
  normalizeLastEpisodeLocation,
  useEpisodeWorkbenchStore,
} from "@/stores/episode-workbench-store";
import { useProjectNavStore } from "@/stores/project-nav-store";
import {
  clearHomeCanvasHandoff,
  setHomeCanvasHandoff,
} from "@/lib/home-handoff";

type PendingAction =
  | { kind: "archive"; project: string; name: string }
  | { kind: "delete"; project: string; name: string }
  | { kind: "purge"; project: string; name: string };

type SortKey = "updated-desc" | "updated-asc" | "name-asc" | "name-desc";

const SORT_OPTIONS: { value: SortKey; labelKey: string }[] = [
  { value: "updated-desc", labelKey: "project.sort.updatedDesc" },
  { value: "updated-asc", labelKey: "project.sort.updatedAsc" },
  { value: "name-asc", labelKey: "project.sort.nameAsc" },
  { value: "name-desc", labelKey: "project.sort.nameDesc" },
];

const PROJECT_NAME_PATTERN = /^[a-zA-Z0-9_]+$/;

const PROJECT_CARD_MIN_HEIGHT_CLASS = "min-h-[12.75rem]";
const RECENTLY_CREATED_PROJECT_KEY = "village-dashboard-recent-created-project";
const HOME_CAPABILITY_CARDS: readonly {
  icon: LucideIcon;
  title: string;
  detail: string;
  prompt: string;
}[] = [
  { icon: Clapperboard, title: "原创短片", detail: "一句话到竖屏成片", prompt: "做一个原创竖屏短片：主人公在城市停电的夜里，发现了一段被所有人遗忘的记忆。" },
  { icon: ScanLine, title: "分镜规划", detail: "先定镜头，再生成", prompt: "请先把这个故事拆成电影分镜，再根据镜头表安排关键帧和视频生成。" },
  { icon: Users, title: "角色设定", detail: "锁定人物一致性", prompt: "创建一个可以跨镜头复用的主角设定，包括正面全身、面部特写和三视图。" },
  { icon: Film, title: "产品广告", detail: "15 秒商业展示", prompt: "为一件科技产品做 15 秒商业广告：先出广告创意和分镜，再生成关键画面。" },
  { icon: WandSparkles, title: "解析再创作", detail: "参考视频拆解重做", prompt: "我要上传一段参考视频，请先拆解镜头节奏、运镜和画面语言，再帮我新做一版。" },
  { icon: LayoutTemplate, title: "工作流起步", detail: "按制作路线搭画布", prompt: "先根据我的目标推荐一条最适合的画布工作流，并把节点关系搭好等我确认。" },
] as const;

function readRecentlyCreatedProject(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(RECENTLY_CREATED_PROJECT_KEY);
}

function prioritizeRecentlyCreatedProject(
  list: ProjectSummary[],
  name: string | null,
): ProjectSummary[] {
  if (!name) return list;
  const index = list.findIndex((p) => p.name === name);
  if (index <= 0) return list;
  const next = [...list];
  const [project] = next.splice(index, 1);
  next.unshift(project);
  return next;
}

function getUpdatedAt(s: ProjectSummary): string {
  return s.updatedAt ?? s.archivedAt ?? s.deletedAt ?? "";
}

function projectRouteParam(summary: ProjectSummary): string {
  return summary.id;
}

function sortSummaries(list: ProjectSummary[], key: SortKey): ProjectSummary[] {
  const arr = [...list];
  switch (key) {
    case "updated-desc":
      return arr.sort((a, b) => {
        const at = getUpdatedAt(a);
        const bt = getUpdatedAt(b);
        if (at !== bt) return at < bt ? 1 : -1;
        return a.name.localeCompare(b.name);
      });
    case "updated-asc":
      return arr.sort((a, b) => {
        const at = getUpdatedAt(a);
        const bt = getUpdatedAt(b);
        if (at !== bt) return at > bt ? 1 : -1;
        return a.name.localeCompare(b.name);
      });
    case "name-asc":
      return arr.sort((a, b) => a.name.localeCompare(b.name));
    case "name-desc":
      return arr.sort((a, b) => b.name.localeCompare(a.name));
  }
}

function ProjectSelectionCheckbox({
  name,
  checked,
  onChange,
}: {
  name: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
}) {
  const { t } = useTranslation();
  return (
    <Checkbox
      aria-label={t("project.bulk.selectProject", { name })}
      checked={checked}
      onCheckedChange={(value) => onChange(value === true)}
      onClick={(event) => event.stopPropagation()}
      onKeyDown={(event) => event.stopPropagation()}
    />
  );
}

function ProjectCard({
  summary,
  size = "md",
  onOpen,
  onPreload,
  onShare,
  onAction,
  selectable = false,
  selected = false,
  onSelectedChange,
}: {
  summary: ProjectSummary;
  size?: "md" | "sm";
  onOpen: () => void;
  onPreload?: () => void;
  onShare: () => void;
  onAction: (
    action: "archive" | "unarchive" | "delete" | "restore" | "purge",
  ) => void;
  selectable?: boolean;
  selected?: boolean;
  onSelectedChange?: (checked: boolean) => void;
}) {
  const { t } = useTranslation();
  const { initial } = useMemo(
    () => getProjectCover(summary.name),
    [summary.name],
  );
  const isActive = summary.status === "active";
  const isArchived = summary.status === "archived";
  const isDeleted = summary.status === "deleted";
  const canManageGrants = canManageProjectGrants(summary);
  const canLifecycle = canDeleteProject(summary);
  const isShared = isSharedProject(summary);
  const roleLabel = t(`project.roleLabel.${projectRole(summary)}`);
  const sourceLabel = isShared
    ? t("project.ownership.from", {
        owner: summary.ownerUsername || t("project.ownership.unknownOwner"),
      })
    : t("project.ownership.mine");
  const ownershipMetaLabel = `${sourceLabel} / ${roleLabel}`;
  const visibleOwnershipLabel = isShared ? ownershipMetaLabel : sourceLabel;

  const relativeEdited = summary.updatedAt
    ? formatRelativeTime(summary.updatedAt, t)
    : null;
  const relativeDeleted = summary.deletedAt
    ? formatRelativeTime(summary.deletedAt, t)
    : null;

  const clickable = !isDeleted;
  const containerClick = (e: React.MouseEvent) => {
    const target = e.target as HTMLElement;
    if (target.closest("[data-project-menu], [data-project-select]")) return;
    if (clickable) onOpen();
  };

  const sm = size === "sm";

  return (
    <div
      onFocus={() => {
        if (clickable) onPreload?.();
      }}
      onMouseEnter={() => {
        if (clickable) onPreload?.();
      }}
      onClick={containerClick}
      onKeyDown={(e) => {
        if (!clickable) return;
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onOpen();
        }
      }}
      role={clickable ? "button" : undefined}
      tabIndex={clickable ? 0 : undefined}
      className={cn(
        "village-project-card group relative flex h-full flex-col rounded-[14px] border border-border/80 bg-card transition-[border-color,background-color,transform] duration-200 ease-out",
        PROJECT_CARD_MIN_HEIGHT_CLASS,
        sm ? "p-2 pt-4" : "p-3 pt-5",
        clickable &&
          "cursor-pointer hover:border-foreground/15 hover:bg-card/65 hover:shadow-lg hover:shadow-black/10",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/60",
        isArchived && "opacity-70",
        isDeleted && "opacity-50",
      )}
    >
      {selectable && canLifecycle && onSelectedChange && (
        <div
          data-project-select
          className="absolute left-2 top-2 z-10"
          onClick={(event) => event.stopPropagation()}
          onKeyDown={(event) => event.stopPropagation()}
        >
          <ProjectSelectionCheckbox
            name={summary.name}
            checked={selected}
            onChange={onSelectedChange}
          />
        </div>
      )}
      <div className="mx-auto flex w-full flex-col">
        <div
          className={cn(
            "project-cover relative mx-auto mb-3 aspect-[16/10] w-full overflow-hidden rounded-[11px] bg-muted",
            isDeleted && "grayscale",
          )}
        >
          <span className="village-project-cover__mark" aria-hidden="true">
            <FolderOpen className="size-7" strokeWidth={1.5} />
          </span>
          <span className="village-project-cover__initial" aria-hidden="true">
            {initial}
          </span>
          {isArchived && (
            <div className="absolute bottom-2 right-2 rounded-[6px] bg-[#292929] px-2 py-0.5 text-[10px] font-medium text-foreground">
              {t("project.archivedBadge")}
            </div>
          )}
        </div>

        <div className="flex w-full items-start justify-between gap-1">
          <h3
            className="ml-[5%] min-w-0 truncate text-sm font-semibold text-foreground"
            title={summary.name}
          >
            {summary.name}
          </h3>
          <div
            data-project-menu
            onClick={(e) => e.stopPropagation()}
            onKeyDown={(e) => e.stopPropagation()}
          >
            <DropdownMenu>
              <DropdownMenuTrigger
                render={
                  <button
                    type="button"
                    className="shrink-0 p-0.5 text-muted-foreground opacity-0 transition-colors hover:text-foreground focus-visible:opacity-100 group-hover:opacity-100"
                    aria-label={t("project.actionsLabel")}
                  />
                }
              >
                <MoreHorizontal className="size-4" />
              </DropdownMenuTrigger>
              <DropdownMenuContent
                side="right"
                align="start"
                sideOffset={8}
                className="w-32 rounded-md p-1 shadow-xl shadow-black/20 [&_[data-slot=dropdown-menu-item]]:min-h-8 [&_[data-slot=dropdown-menu-item]]:gap-2 [&_[data-slot=dropdown-menu-item]]:rounded-sm [&_[data-slot=dropdown-menu-item]]:px-2 [&_[data-slot=dropdown-menu-item]]:py-1.5 [&_[data-slot=dropdown-menu-item]]:text-xs [&_[data-slot=dropdown-menu-item]:focus]:bg-white/8 [&_[data-slot=dropdown-menu-item]:focus]:text-current [&_[data-slot=dropdown-menu-item][data-variant=destructive]:focus]:bg-white/8 [&_[data-slot=dropdown-menu-item][data-variant=destructive]:focus]:text-destructive [&_[data-slot=dropdown-menu-item]_svg]:size-3.5"
              >
                <DropdownMenuGroup>
                  {isActive && (
                    <>
                      <DropdownMenuItem onClick={onOpen}>
                        <FolderOpen className="size-4" />
                        {t("project.actions.open")}
                      </DropdownMenuItem>
                      <DropdownMenuItem
                        onClick={() => openFreezoneProject(projectRouteParam(summary))}
                      >
                        <Brush className="size-4" />
                        {t("project.actions.openFreezone")}
                      </DropdownMenuItem>
                      {canLifecycle && (
                        <>
                          <DropdownMenuItem onClick={() => onAction("archive")}>
                            <ArchiveRestore className="size-4" />
                            {t("project.actions.archive")}
                          </DropdownMenuItem>
                          <DropdownMenuItem
                            onClick={() => onAction("delete")}
                            variant="destructive"
                          >
                            <Trash2 className="size-4" />
                            {t("project.actions.delete")}
                          </DropdownMenuItem>
                        </>
                      )}
                    </>
                  )}
                  {isArchived && (
                    <>
                      <DropdownMenuItem onClick={onOpen}>
                        <FolderOpen className="size-4" />
                        {t("project.actions.open")}
                      </DropdownMenuItem>
                      <DropdownMenuItem
                        onClick={() => openFreezoneProject(projectRouteParam(summary))}
                      >
                        <Brush className="size-4" />
                        {t("project.actions.openFreezone")}
                      </DropdownMenuItem>
                      {canManageGrants && (
                        <DropdownMenuItem onClick={onShare}>
                          <Share2 className="size-4" />
                          {t("project.actions.share")}
                        </DropdownMenuItem>
                      )}
                      {canLifecycle && (
                        <>
                          <DropdownMenuItem onClick={() => onAction("unarchive")}>
                            <ArchiveRestore className="size-4" />
                            {t("project.actions.unarchive")}
                          </DropdownMenuItem>
                          <DropdownMenuItem
                            onClick={() => onAction("delete")}
                            variant="destructive"
                          >
                            <Trash2 className="size-4" />
                            {t("project.actions.delete")}
                          </DropdownMenuItem>
                        </>
                      )}
                    </>
                  )}
                  {isDeleted && (
                    <>
                      {canLifecycle && (
                        <>
                          <DropdownMenuItem onClick={() => onAction("restore")}>
                            <Undo2 className="size-4" />
                            {t("project.actions.restore")}
                          </DropdownMenuItem>
                          <DropdownMenuItem
                            onClick={() => onAction("purge")}
                            variant="destructive"
                          >
                            <Trash2 className="size-4" />
                            {t("project.actions.purge")}
                          </DropdownMenuItem>
                        </>
                      )}
                    </>
                  )}
                </DropdownMenuGroup>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </div>

        {!isDeleted && (
          <div className="mx-auto mt-2 flex w-[90%] min-w-0 items-center gap-1.5 text-xs font-medium text-muted-foreground tabular-nums">
            <span className="tabular-nums">
              {t("project.card.episodes", { count: summary.episodeCount ?? 0 })}
            </span>
            {relativeEdited ? (
              <>
                <span className="text-muted-foreground/45" aria-hidden>
                  ·
                </span>
                <span className="min-w-0 truncate">
                  {t("project.card.editedAgo", { time: relativeEdited })}
                </span>
              </>
            ) : null}
          </div>
        )}

        {isDeleted && (
          <div className="mt-1 text-[11px] tabular-nums text-muted-foreground/80">
            <span className="font-medium text-destructive/80">
              {relativeDeleted
                ? t("project.card.deletedAgo", { time: relativeDeleted })
                : t("project.archivedBadge")}
            </span>
          </div>
        )}

        {!isDeleted && (
          <div className="mt-3.5 flex min-w-0 items-center justify-between gap-1.5 text-[10px] leading-none text-muted-foreground/80">
            <div className="ml-[5%] flex min-w-0 items-center gap-1.5" title={ownershipMetaLabel}>
              <span className="min-w-0 truncate">{visibleOwnershipLabel}</span>
              {!isShared ? <span className="sr-only">{roleLabel}</span> : null}
            </div>
            {isActive && canManageGrants ? (
              <button
                type="button"
                data-project-menu
                onClick={(e) => {
                  e.stopPropagation();
                  onShare();
                }}
                className="inline-flex shrink-0 items-center gap-0.5 text-[10px] font-medium text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/60"
                aria-label={t("project.actions.share")}
              >
                <Share2 className="size-3" />
                <span>{t("project.actions.share")}</span>
              </button>
            ) : null}
          </div>
        )}
      </div>
    </div>
  );
}

function CreateProjectCard({ onCreate }: { onCreate: () => void }) {
  const { t } = useTranslation();
  return (
    <button
      type="button"
      onClick={onCreate}
      className={cn(
        "group flex h-full w-full flex-col items-center justify-center rounded-[14px] border border-dashed border-border/80 bg-card/35 p-3 text-center text-muted-foreground transition-[border-color,background-color,transform] duration-200 ease-out",
        PROJECT_CARD_MIN_HEIGHT_CLASS,
        "hover:border-white/15 hover:bg-white/[0.03] hover:shadow-lg hover:shadow-black/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/60",
      )}
    >
      <Plus
        className="mb-3 size-7 stroke-[1px] text-white/70 transition-colors group-hover:text-foreground"
        aria-hidden="true"
      />
      <span className="text-sm font-normal text-white/70 transition-colors group-hover:text-foreground">
        {t("project.createCard")}
      </span>
    </button>
  );
}

function DashboardTabStrip({
  current,
  counts,
  onChange,
}: {
  current: ProjectStatus;
  counts: Record<ProjectStatus, number>;
  onChange: (v: ProjectStatus) => void;
}) {
  const { t } = useTranslation();
  const tabs: { value: ProjectStatus; label: string }[] = [
    { value: "active", label: t("project.statusActive") },
    { value: "archived", label: t("project.statusArchived") },
    { value: "deleted", label: t("project.statusDeleted") },
  ];
  return (
    <div className="village-library-status-tabs flex h-10 items-center gap-5 text-xs">
      {tabs.map((tab) => {
        const active = current === tab.value;
        return (
          <button
            key={tab.value}
            type="button"
            aria-pressed={active}
            onClick={() => onChange(tab.value)}
            className={cn(
              "relative inline-flex h-10 items-center gap-1.5 border-b-2 px-0.5 font-normal transition-colors duration-160",
              active
                ? "border-primary text-foreground"
                : "border-transparent text-muted-foreground hover:text-foreground",
            )}
          >
            <span>{tab.label}</span>
            <span
              className={cn(
                "px-0.5 text-[11px] tabular-nums",
                active
                  ? "text-primary"
                  : "text-muted-foreground/70",
              )}
            >
              {counts[tab.value]}
            </span>
          </button>
        );
      })}
    </div>
  );
}

function ProjectRow({
  summary,
  onOpen,
  onPreload,
  onShare,
  onAction,
  selectable = false,
  selected = false,
  onSelectedChange,
}: {
  summary: ProjectSummary;
  onOpen: () => void;
  onPreload?: () => void;
  onShare: () => void;
  onAction: (
    action: "archive" | "unarchive" | "delete" | "restore" | "purge",
  ) => void;
  selectable?: boolean;
  selected?: boolean;
  onSelectedChange?: (checked: boolean) => void;
}) {
  const { t } = useTranslation();
  const { initial, gradient } = useMemo(
    () => getProjectCover(summary.name),
    [summary.name],
  );
  const isActive = summary.status === "active";
  const isArchived = summary.status === "archived";
  const isDeleted = summary.status === "deleted";
  const canManageGrants = canManageProjectGrants(summary);
  const canLifecycle = canDeleteProject(summary);
  const roleLabel = t(`project.roleLabel.${projectRole(summary)}`);
  const sourceLabel = isSharedProject(summary)
    ? t("project.ownership.from", {
        owner: summary.ownerUsername || t("project.ownership.unknownOwner"),
      })
    : t("project.ownership.mine");

  const relativeEdited = summary.updatedAt
    ? formatRelativeTime(summary.updatedAt, t)
    : null;
  const relativeDeleted = summary.deletedAt
    ? formatRelativeTime(summary.deletedAt, t)
    : null;

  const clickable = !isDeleted;
  const rowClick = (e: React.MouseEvent) => {
    const target = e.target as HTMLElement;
    if (target.closest("[data-project-menu], [data-project-select]")) return;
    if (clickable) onOpen();
  };

  return (
    <div
      onFocus={() => {
        if (clickable) onPreload?.();
      }}
      onMouseEnter={() => {
        if (clickable) onPreload?.();
      }}
      onClick={rowClick}
      onKeyDown={(e) => {
        if (!clickable) return;
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onOpen();
        }
      }}
      role={clickable ? "button" : undefined}
      tabIndex={clickable ? 0 : undefined}
      className={cn(
        "village-project-row group flex min-h-[60px] items-center gap-3 rounded-[11px] border border-border/80 bg-card px-4 py-3 transition-[border-color,background-color,transform] duration-160 ease-out",
        clickable &&
          "cursor-pointer hover:border-foreground/15 hover:bg-card/65",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/60",
        isArchived && "opacity-70",
        isDeleted && "opacity-50",
      )}
    >
      {selectable && canLifecycle && onSelectedChange && (
        <div
          data-project-select
          className="flex shrink-0 items-center"
          onClick={(event) => event.stopPropagation()}
          onKeyDown={(event) => event.stopPropagation()}
        >
          <ProjectSelectionCheckbox
            name={summary.name}
            checked={selected}
            onChange={onSelectedChange}
          />
        </div>
      )}
      <div className={cn("relative size-11 shrink-0 overflow-hidden rounded-[9px]", isDeleted && "grayscale")}>
        <span aria-hidden="true" className="absolute inset-0" style={{ background: gradient }} />
        <div className="absolute inset-0 bg-black/28" />
        <span className="absolute inset-0 flex items-center justify-center text-sm font-semibold text-white drop-shadow-[0_1px_6px_rgba(0,0,0,0.4)]">
          {initial}
        </span>
      </div>

      <div className="flex min-w-0 flex-1 items-center gap-3">
        <h3
          className="min-w-0 flex-1 truncate text-sm font-semibold text-foreground"
          title={summary.name}
        >
          {summary.name}
        </h3>
        <span className="hidden shrink-0 text-[11px] font-medium text-muted-foreground sm:inline">
          {sourceLabel}
        </span>
        <span className="hidden shrink-0 text-[11px] font-medium text-primary sm:inline">
          {roleLabel}
        </span>
        {isArchived && (
          <span className="hidden shrink-0 text-[11px] font-medium text-muted-foreground sm:inline">
            {t("project.archivedBadge")}
          </span>
        )}
        {!isDeleted && summary.episodeCount != null && (
          <div className="hidden shrink-0 items-center gap-1.5 text-[11px] tabular-nums text-muted-foreground/80 md:flex">
            <span>
              {t("project.card.episodes", {
                count: summary.episodeCount ?? 0,
              })}
            </span>
          </div>
        )}
        <div className="hidden shrink-0 text-xs tabular-nums text-muted-foreground sm:block">
          {isDeleted
            ? relativeDeleted
              ? t("project.card.deletedAgo", { time: relativeDeleted })
              : t("project.archivedBadge")
            : relativeEdited
              ? t("project.card.editedAgo", { time: relativeEdited })
              : null}
        </div>
      </div>

      {isActive && canManageGrants && (
        <button
          type="button"
          data-project-menu
          onClick={(e) => {
            e.stopPropagation();
            onShare();
          }}
          className="flex h-8 shrink-0 items-center gap-1 rounded-[7px] px-2 text-[11px] font-medium text-muted-foreground transition-colors hover:bg-accent hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/60"
          aria-label={t("project.actions.share")}
        >
          <Share2 className="size-3.5" />
          <span className="hidden sm:inline">{t("project.actions.share")}</span>
        </button>
      )}

      <div
        data-project-menu
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => e.stopPropagation()}
      >
        <DropdownMenu>
          <DropdownMenuTrigger
            render={
              <button
                type="button"
                className="shrink-0 rounded p-1 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
                aria-label={t("project.actionsLabel")}
              />
            }
          >
            <MoreHorizontal className="size-4" />
          </DropdownMenuTrigger>
          <DropdownMenuContent
            align="end"
            className="w-32 rounded-md p-1 shadow-xl shadow-black/20 [&_[data-slot=dropdown-menu-item]]:min-h-8 [&_[data-slot=dropdown-menu-item]]:gap-2 [&_[data-slot=dropdown-menu-item]]:rounded-sm [&_[data-slot=dropdown-menu-item]]:px-2 [&_[data-slot=dropdown-menu-item]]:py-1.5 [&_[data-slot=dropdown-menu-item]]:text-xs [&_[data-slot=dropdown-menu-item]:focus]:bg-white/8 [&_[data-slot=dropdown-menu-item]:focus]:text-current [&_[data-slot=dropdown-menu-item][data-variant=destructive]:focus]:bg-white/8 [&_[data-slot=dropdown-menu-item][data-variant=destructive]:focus]:text-destructive [&_[data-slot=dropdown-menu-item]_svg]:size-3.5"
          >
            <DropdownMenuGroup>
              {isActive && (
                <>
                  <DropdownMenuItem onClick={onOpen}>
                    <FolderOpen className="size-4" />
                    {t("project.actions.open")}
                  </DropdownMenuItem>
                  <DropdownMenuItem
                    onClick={() => openFreezoneProject(projectRouteParam(summary))}
                  >
                    <Brush className="size-4" />
                    {t("project.actions.openFreezone")}
                  </DropdownMenuItem>
                  {canLifecycle && (
                    <>
                      <DropdownMenuItem onClick={() => onAction("archive")}>
                        <ArchiveRestore className="size-4" />
                        {t("project.actions.archive")}
                      </DropdownMenuItem>
                      <DropdownMenuItem
                        onClick={() => onAction("delete")}
                        variant="destructive"
                      >
                        <Trash2 className="size-4" />
                        {t("project.actions.delete")}
                      </DropdownMenuItem>
                    </>
                  )}
                </>
              )}
              {isArchived && (
                <>
                  <DropdownMenuItem onClick={onOpen}>
                    <FolderOpen className="size-4" />
                    {t("project.actions.open")}
                  </DropdownMenuItem>
                  <DropdownMenuItem
                    onClick={() => openFreezoneProject(projectRouteParam(summary))}
                  >
                    <Brush className="size-4" />
                    {t("project.actions.openFreezone")}
                  </DropdownMenuItem>
                  {canManageGrants && (
                    <DropdownMenuItem onClick={onShare}>
                      <Share2 className="size-4" />
                      {t("project.actions.share")}
                    </DropdownMenuItem>
                  )}
                  {canLifecycle && (
                    <>
                      <DropdownMenuItem onClick={() => onAction("unarchive")}>
                        <ArchiveRestore className="size-4" />
                        {t("project.actions.unarchive")}
                      </DropdownMenuItem>
                      <DropdownMenuItem
                        onClick={() => onAction("delete")}
                        variant="destructive"
                      >
                        <Trash2 className="size-4" />
                        {t("project.actions.delete")}
                      </DropdownMenuItem>
                    </>
                  )}
                </>
              )}
              {isDeleted && (
                <>
                  {canLifecycle && (
                    <>
                      <DropdownMenuItem onClick={() => onAction("restore")}>
                        <Undo2 className="size-4" />
                        {t("project.actions.restore")}
                      </DropdownMenuItem>
                      <DropdownMenuItem
                        onClick={() => onAction("purge")}
                        variant="destructive"
                      >
                        <Trash2 className="size-4" />
                        {t("project.actions.purge")}
                      </DropdownMenuItem>
                    </>
                  )}
                </>
              )}
            </DropdownMenuGroup>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </div>
  );
}

function ViewToggle({
  value,
  onChange,
}: {
  value: DashboardView;
  onChange: (v: DashboardView) => void;
}) {
  const { t } = useTranslation();
  const options: {
    value: DashboardView;
    labelKey: string;
    Icon: typeof LayoutGrid;
  }[] = [
    { value: "card", labelKey: "project.view.card", Icon: LayoutGrid },
    { value: "list", labelKey: "project.view.list", Icon: ListIcon },
  ];
  return (
    <div
      role="tablist"
      aria-label={t("project.view.toggle")}
      className="inline-flex h-9 items-center rounded-[8px] border border-border bg-card p-0.5 text-xs"
    >
      {options.map(({ value: v, labelKey, Icon }) => {
        const active = value === v;
        return (
          <button
            key={v}
            type="button"
            role="tab"
            aria-selected={active}
            aria-label={t(labelKey)}
            title={t(labelKey)}
            onClick={() => onChange(v)}
            className={cn(
              "inline-flex h-8 items-center justify-center rounded-[6px] px-2.5 font-normal transition-[background-color,color] duration-160",
              active
                ? "bg-primary/12 text-primary"
                : "text-muted-foreground hover:bg-accent hover:text-foreground",
            )}
          >
            <Icon className="size-4" />
          </button>
        );
      })}
    </div>
  );
}

function LoadingList() {
  return (
    <div className="flex flex-col gap-2">
      {Array.from({ length: 8 }).map((_, i) => (
        <div
          key={i}
          className="flex animate-pulse items-center gap-3 rounded-lg border border-border/60 bg-card px-3 py-2"
        >
          <div className="size-10 rounded-md bg-white/[0.04]" />
          <div className="h-4 w-1/3 rounded bg-white/[0.05]" />
          <div className="ml-auto h-3 w-16 rounded bg-white/[0.04]" />
        </div>
      ))}
    </div>
  );
}

function LoadingGrid({ size = "md" }: { size?: "md" | "sm" }) {
  const sm = size === "sm";
  return (
    <div
      className={cn(
        "grid gap-4",
        sm
          ? "grid-cols-3 sm:grid-cols-4 md:grid-cols-5 lg:grid-cols-6 xl:grid-cols-8"
          : "grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-7",
      )}
    >
      {Array.from({ length: sm ? 8 : 6 }).map((_, i) => (
        <div
          key={i}
          className={cn(
            "flex animate-pulse flex-col rounded-xl border border-border/60 bg-card",
            sm ? "p-2" : "p-3",
          )}
        >
          <div className="mb-3 aspect-[16/10] rounded-lg bg-white/[0.04]" />
          <div className="h-4 w-2/3 rounded bg-white/[0.05]" />
          <div className="mt-1 h-3 w-1/3 rounded bg-white/[0.04]" />
        </div>
      ))}
    </div>
  );
}

function FirstTimeEmpty({ onCreate }: { onCreate: () => void }) {
  const { t } = useTranslation();
  return (
    <div className="flex flex-col items-center justify-center pt-12 text-center">
      <div className="village-empty-stack mb-7" aria-hidden="true">
        <span />
        <span />
        <span><BookOpen className="size-6" /></span>
      </div>
      <h3 className="mb-5 text-xl font-bold tracking-tight text-foreground">
        {t("project.heroTitle")}
      </h3>
      <p className="mb-8 text-sm text-muted-foreground">
        {t("project.heroDescription")}
      </p>
      <Button
        onClick={onCreate}
        size="lg"
        className="gap-2 rounded-[10px] px-6"
      >
        <Plus className="size-4" />
        {t("project.heroButton")}
      </Button>
    </div>
  );
}

function TabEmpty({
  title,
  description,
}: {
  title: string;
  description?: string;
}) {
  return (
    <div className="village-library-empty flex flex-col items-center justify-center rounded-[12px] border border-dashed border-border/80 bg-card/55 px-6 py-14 text-center">
      <h3 className="mb-1 text-base font-semibold text-foreground">{title}</h3>
      {description && (
        <p className="max-w-md text-sm text-muted-foreground">{description}</p>
      )}
    </div>
  );
}

function ActiveEmptyWithCreate({
  onCreate,
}: {
  onCreate: () => void;
}) {
  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
      <div>
        <CreateProjectCard onCreate={onCreate} />
      </div>
    </div>
  );
}

function PurgeDialog({
  name,
  count,
  busy = false,
  open,
  onOpenChange,
  onConfirm,
}: {
  name?: string;
  count?: number;
  busy?: boolean;
  open: boolean;
  onOpenChange: (v: boolean) => void;
  onConfirm: () => void | Promise<void>;
}) {
  const { t } = useTranslation();
  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>
            {count == null
              ? t("project.purgeDialog.title", { name })
              : t("project.bulk.purgeDialog.title", { count })}
          </AlertDialogTitle>
          <AlertDialogDescription>
            {count == null
              ? t("project.purgeDialog.description")
              : t("project.bulk.purgeDialog.description", { count })}
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>{t("common.cancel")}</AlertDialogCancel>
          <AlertDialogAction
            variant="destructive"
            className="min-w-24"
            disabled={busy}
            onClick={() => {
              void Promise.resolve(onConfirm()).finally(() =>
                onOpenChange(false),
              );
            }}
          >
            {t("project.purgeDialog.confirm")}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

function ArchiveDialog({
  name,
  open,
  onOpenChange,
  onConfirm,
}: {
  name: string;
  open: boolean;
  onOpenChange: (v: boolean) => void;
  onConfirm: () => void;
}) {
  const { t } = useTranslation();
  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>
            {t("project.archiveDialog.title", { name })}
          </AlertDialogTitle>
          <AlertDialogDescription>
            {t("project.archiveDialog.description")}
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>{t("common.cancel")}</AlertDialogCancel>
          <AlertDialogAction
            onClick={() => {
              onConfirm();
              onOpenChange(false);
            }}
          >
            {t("project.archiveDialog.confirm")}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

function DeleteDialog({
  name,
  open,
  onOpenChange,
  onConfirm,
}: {
  name: string;
  open: boolean;
  onOpenChange: (v: boolean) => void;
  onConfirm: () => void;
}) {
  const { t } = useTranslation();
  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>
            {t("project.deleteDialog.title", { name })}
          </AlertDialogTitle>
          <AlertDialogDescription>
            {t("project.deleteDialog.description")}
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>{t("common.cancel")}</AlertDialogCancel>
          <AlertDialogAction
            variant="destructive"
            onClick={() => {
              onConfirm();
              onOpenChange(false);
            }}
          >
            {t("project.deleteDialog.confirm")}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

function projectNameSlug(seed: string, fallback = "canvas"): string {
  const slug = seed
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 28);
  return slug || fallback;
}

function nextProjectName(seed: string): string {
  const suffix =
    Date.now().toString(36).slice(-6) +
    Math.random().toString(36).slice(2, 4);
  return `${projectNameSlug(seed)}_${suffix}`.slice(0, 48);
}

type HomeSpeech = {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  start: () => void;
  stop: () => void;
  onresult: ((event: { results: ArrayLike<ArrayLike<{ transcript: string }>> }) => void) | null;
  onend: (() => void) | null;
  onerror: (() => void) | null;
};

function HomeHero({
  prompt,
  onPromptChange,
  onSubmit,
  busy,
}: {
  prompt: string;
  onPromptChange: (value: string) => void;
  onSubmit: (options: { attachments: ChatAttachment[]; mode: "plan" | "direct"; skillIds: string[] }) => void;
  busy: boolean;
}) {
  const { t } = useTranslation();
  const [mode, setMode] = useState<"plan" | "direct">("plan");
  const [recording, setRecording] = useState(false);
  const [attachments, setAttachments] = useState<ChatAttachment[]>([]);
  const [readingFile, setReadingFile] = useState(false);
  const [skillsOpen, setSkillsOpen] = useState(false);
  const [skillIds, setSkillIds] = useState<string[]>([]);
  const [previewImage, setPreviewImage] = useState<ChatAttachment | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const speechRef = useRef<HomeSpeech | null>(null);
  useEffect(() => () => speechRef.current?.stop(), []);
  const submit = () => {
    if (busy || readingFile || (!prompt.trim() && !attachments.length)) return;
    speechRef.current?.stop();
    saveCanvasAgentRunMode(mode === "plan" ? "draft" : "auto");
    onSubmit({ attachments, mode, skillIds });
  };
  const addFiles = async (files: File[]) => {
    setReadingFile(true);
    try {
      const next: ChatAttachment[] = [];
      for (const file of files) {
        if (file.size > 1024 * 1024 || file.size === 0) {
          toast.error("首页参考素材限 1MB，请压缩后添加");
          continue;
        }
        if (file.type.startsWith("image/")) {
          const image = await fileToImageAttachment(file);
          if (image) next.push(image);
        } else if (/\.(txt|md)$/i.test(file.name)) {
          next.push({ id: crypto.randomUUID(), type: "file", kind: "file", fileName: file.name, mimeType: "text/plain", content: await file.text(), source: "upload" });
        } else toast.error("首页支持图片、TXT 和 Markdown，视频音频请在画布中添加");
      }
      setAttachments((current) => [...current, ...next].slice(0, 2));
    } catch { toast.error("素材读取失败，请重试"); }
    finally { setReadingFile(false); }
  };

  const toggleVoice = () => {
    if (recording) {
      speechRef.current?.stop();
      setRecording(false);
      return;
    }
    const speechWindow = window as unknown as { SpeechRecognition?: new () => HomeSpeech; webkitSpeechRecognition?: new () => HomeSpeech };
    const Speech = speechWindow.SpeechRecognition ?? speechWindow.webkitSpeechRecognition;
    if (!Speech) {
      toast.error("当前浏览器不支持语音输入");
      return;
    }
    const recognition = new Speech();
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.lang = "zh-CN";
    const previousText = prompt;
    recognition.onresult = (event) => {
      let text = "";
      for (let i = 0; i < event.results.length; i += 1) text += event.results[i][0]?.transcript ?? "";
      onPromptChange(previousText + text);
    };
    recognition.onend = () => setRecording(false);
    recognition.onerror = () => { setRecording(false); toast.error("语音输入失败，请检查麦克风权限"); };
    speechRef.current = recognition;
    setRecording(true);
    try { recognition.start(); } catch { setRecording(false); toast.error("无法启动语音输入"); }
  };

  return (
    <section className="village-home-hero village-home-composer-wrap">
      <form className="village-home-single-input" onSubmit={(event) => {
        event.preventDefault();
        submit();
      }}>
        <div className="village-composer p-4" onDragOver={(event) => { if (event.dataTransfer.types.includes("Files")) event.preventDefault(); }} onDrop={(event) => {
          if (!event.dataTransfer.files.length) return;
          event.preventDefault();
          void addFiles(Array.from(event.dataTransfer.files));
        }}>
          <input ref={fileRef} type="file" accept="image/*,.txt,.md" multiple className="hidden" aria-label="选择参考素材" onChange={(event) => { void addFiles(Array.from(event.target.files ?? [])); event.target.value = ""; }} />
          <div className="village-composer__head">
            <span className="village-composer__mark" aria-hidden="true">
              <img className="village-home-title-mark__dark" src="/brand/village-canvas-mark-light.png" alt="" />
              <img className="village-home-title-mark__light" src="/brand/village-canvas-mark-dark.png" alt="" />
            </span>
            <div className="min-w-0">
              <h2>说出你的创意</h2>
              <p>导演搭子会把它拆成脚本、分镜、资产与可执行的画布节点。</p>
            </div>
          </div>
          <textarea
            aria-label={t("home.placeholder")}
            value={prompt}
            onChange={(event) => onPromptChange(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
                event.preventDefault();
                submit();
              }
            }}
            rows={2}
            onPaste={(event) => {
              const files = Array.from(event.clipboardData.files);
              if (files.length) { event.preventDefault(); void addFiles(files); }
            }}
            placeholder={t("home.placeholder")}
            className="min-h-[68px] w-full resize-none bg-transparent px-1 py-1 text-sm leading-6 text-foreground outline-none placeholder:text-muted-foreground/70"
          />
          {attachments.map((attachment) => <div key={attachment.id} className="village-composer__attachment">
            {attachment.mimeType?.startsWith("image/") && attachment.content ? <button type="button" onClick={() => setPreviewImage(attachment)} aria-label={`预览 ${attachment.fileName}`}><img src={attachment.content} alt={attachment.fileName ?? "参考素材"} /></button> : <Paperclip className="size-3" />}
            <span>{attachment.fileName}</span>
            <button type="button" onClick={() => setAttachments((current) => current.filter((item) => item.id !== attachment.id))} aria-label={`移除 ${attachment.fileName}`}>×</button>
          </div>)}
          <div className="village-composer__tools">
            <button type="button" className="home-glass-tool" onClick={() => fileRef.current?.click()} title="添加参考素材"><Paperclip className="size-4" /><span>添加素材</span></button>
            <button type="button" className={cn("home-glass-tool", recording && "is-active")} onClick={toggleVoice} title="语音输入"><Mic className="size-4" /><span>{recording ? "正在听" : "语音"}</span></button>
            <div className="home-glass-modes" role="group" aria-label="执行方式">
              <button type="button" aria-pressed={mode === "plan"} className={cn("home-glass-mode", mode === "plan" && "is-active")} onClick={() => setMode("plan")}><Sparkles className="size-3" />先规划</button>
              <button type="button" aria-pressed={mode === "direct"} className={cn("home-glass-mode", mode === "direct" && "is-active")} onClick={() => setMode("direct")}>自动执行</button>
            </div>
            <div className="village-composer__shortcuts" aria-label="创作快捷入口">
              {[["视频预演", "做一个视频预演"], ["深度影视", "进入深度影视创作"], ["角色设定", "创建角色设定"]].map(([label, value]) => (
                <button key={label} type="button" className="home-glass-shortcut" onClick={() => onPromptChange(prompt.trim() ? `${prompt.trim()}\n${value}` : value)}>{label}</button>
              ))}
              <button type="button" className="home-glass-shortcut" onClick={() => setSkillsOpen(true)}>技能{skillIds.length ? ` · ${skillIds.length}` : ""}</button>
            </div>
            <button
              type="submit"
              title={t("home.submit")}
              aria-label={t("home.submit")}
              disabled={busy || readingFile || (!prompt.trim() && !attachments.length)}
              className="home-glass-action home-glass-action--primary inline-flex size-10 items-center justify-center disabled:cursor-not-allowed disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              {busy ? <Loader2 className="size-4 animate-spin" aria-hidden="true" /> : <ArrowUp className="size-4" aria-hidden="true" />}
            </button>
          </div>
        </div>
      </form>
      <Dialog open={Boolean(previewImage)} onOpenChange={(open) => { if (!open) setPreviewImage(null); }}>
        <DialogContent><DialogTitle>{previewImage?.fileName ?? "参考素材"}</DialogTitle><img src={previewImage?.content} alt={previewImage?.fileName ?? "参考素材"} className="max-h-[70dvh] w-full object-contain" /></DialogContent>
      </Dialog>
      <SkillStoreDialog open={skillsOpen} onOpenChange={setSkillsOpen} mountedSkillIds={skillIds} onToggleMount={(id) => {
        setSkillIds((current) => current.includes(id) ? current.filter((item) => item !== id) : [...current, id]);
      }} />
    </section>
  );
}

function ProjectDashboard() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const router = useRouter();
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<SortKey>("updated-desc");
  const [createOpen, setCreateOpen] = useState(false);
  const [newName, setNewName] = useState("");
  const [homePrompt, setHomePrompt] = useState("");
  const [recentlyCreatedProject, setRecentlyCreatedProject] = useState<
    string | null
  >(() => readRecentlyCreatedProject());
  const [pending, setPending] = useState<PendingAction | null>(null);
  const [selectedProjectIds, setSelectedProjectIds] = useState<Set<string>>(
    () => new Set(),
  );
  const [bulkPurgeOpen, setBulkPurgeOpen] = useState(false);
  const [bulkPurging, setBulkPurging] = useState(false);
  // Project pending the "open in Freezone?" prompt after creation.
  const [shareProject, setShareProject] = useState<ProjectSummary | null>(null);
  const searchInputRef = useRef<HTMLInputElement>(null);

  const statusCounts = useProjectCounts();
  const allSummaries = useAllProjectSummaries();
  // Only animate the grid entry on a cold load (data wasn't cached). When the
  // user navigates back from a project page, the Sidebar has already warmed
  // the query — render instantly instead of flashing an empty grid.
  const [wasColdOnMount] = useState(() => allSummaries.isLoading);
  const currentTab = useAppStore((s) => s.dashboardTab);
  const setCurrentTab = useAppStore((s) => s.setDashboardTab);
  const view = useAppStore((s) => s.dashboardView);
  const setView = useAppStore((s) => s.setDashboardView);
  const projectLibraryOpen = useAppStore((s) => s.projectLibraryOpen) ?? false;
  const setProjectLibraryOpen = useAppStore((s) => s.setProjectLibraryOpen);
  const [showAllProjects, setShowAllProjectsState] = useState(projectLibraryOpen);
  const openLibrary = (open: boolean) => {
    setShowAllProjectsState(open);
    if (typeof setProjectLibraryOpen === "function") setProjectLibraryOpen(open);
  };
  const createProject = useCreateProject();
  const archive = useArchiveProject();
  const unarchive = useUnarchiveProject();
  const softDelete = useSoftDeleteProject();
  const restore = useRestoreProject();
  const purge = usePurgeProject();

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key !== "/") return;
      const tag = (e.target as HTMLElement | null)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA") return;
      const contentEditable = (e.target as HTMLElement | null)
        ?.isContentEditable;
      if (contentEditable) return;
      e.preventDefault();
      searchInputRef.current?.focus();
      searchInputRef.current?.select();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);

  const totalProjects =
    statusCounts.active + statusCounts.archived + statusCounts.deleted;

  // TECH-DEBT TD-003: keep local filtering until the registry exposes a
  // paginated, searchable summaries contract.
  const matchesSearch = useMemo(() => {
    const q = search.trim().toLowerCase();
    return (p: ProjectSummary) => (q ? p.name.toLowerCase().includes(q) : true);
  }, [search]);

  const all = allSummaries.data ?? [];
  const activeList = useMemo(
    () => {
      const sorted = sortSummaries(
        all.filter((p) => p.status === "active" && matchesSearch(p)),
        sort,
      );
      return prioritizeRecentlyCreatedProject(sorted, recentlyCreatedProject);
    },
    [all, matchesSearch, recentlyCreatedProject, sort],
  );
  const recentProjects = useMemo(
    () => sortSummaries(all.filter((project) => project.status === "active"), "updated-desc").slice(0, 4),
    [all],
  );
  const archivedList = useMemo(
    () =>
      sortSummaries(
        all.filter((p) => p.status === "archived" && matchesSearch(p)),
        sort,
      ),
    [all, matchesSearch, sort],
  );
  const deletedList = useMemo(
    () =>
      sortSummaries(
        all.filter((p) => p.status === "deleted" && matchesSearch(p)),
        sort,
      ),
    [all, matchesSearch, sort],
  );
  const deletableDeletedProjects = useMemo(
    () => deletedList.filter(canDeleteProject),
    [deletedList],
  );
  const selectedDeletedProjects = useMemo(
    () =>
      deletableDeletedProjects.filter((project) =>
        selectedProjectIds.has(project.id),
      ),
    [deletableDeletedProjects, selectedProjectIds],
  );
  const allDeletedSelected =
    deletableDeletedProjects.length > 0 &&
    selectedDeletedProjects.length === deletableDeletedProjects.length;

  useEffect(() => {
    setSelectedProjectIds(new Set());
  }, [currentTab, search, sort]);

  useEffect(() => {
    const visibleIds = new Set(
      deletableDeletedProjects.map((project) => project.id),
    );
    setSelectedProjectIds((current) => {
      const next = new Set(
        [...current].filter((projectId) => visibleIds.has(projectId)),
      );
      return next.size === current.size ? current : next;
    });
  }, [deletableDeletedProjects]);
  const trimmedNewName = newName.trim();
  const existingProject = useMemo(
    () => (trimmedNewName ? all.find((p) => p.name === trimmedNewName) : null),
    [all, trimmedNewName],
  );
  const hasInvalidProjectName = !!trimmedNewName && !PROJECT_NAME_PATTERN.test(trimmedNewName);
  const createNameError = hasInvalidProjectName
    ? t("project.nameInvalid")
    : existingProject?.status === "active"
      ? t("project.nameExistsActive")
      : existingProject?.status === "archived"
        ? t("project.nameExistsArchived")
        : existingProject?.status === "deleted"
          ? t("project.nameExistsDeleted")
          : null;
  const handleCreate = async () => {
    const name = trimmedNewName;
    if (!name || createNameError) return;
    try {
      const res = await createProject.mutateAsync(name);
      const createdName = res.data.name || name;
      let projectId = res.data.id ?? res.data.project_id ?? null;
      if (!projectId) {
        try {
          const summaries = await fetchProjectSummaries();
          projectId =
            summaries.find((project) => project.name === createdName)?.id ??
            null;
        } catch {
          projectId = null;
        }
      }
      setRecentlyCreatedProject(createdName);
      window.localStorage.setItem(RECENTLY_CREATED_PROJECT_KEY, createdName);
      setNewName("");
      setCreateOpen(false);
      if (projectId) {
        navigate({
          to: PROJECT_SECTION_ROUTES.freezone,
          params: { project: projectId },
        });
      }
    } catch {
      toast.error(t("project.toasts.createFailed"));
    }
  };

  const createCanvasFromHome = async ({
    prompt = "",
    starterWorkflowId,
    attachments = [],
    skillIds = [],
  }: {
    prompt?: string;
    starterWorkflowId?: string;
    attachments?: ChatAttachment[];
    skillIds?: string[];
  }) => {
    const draft = prompt.trim();
    const name = nextProjectName(
      starterWorkflowId ?? (draft || "canvas"),
    );
    try {
      const res = await createProject.mutateAsync(name);
      const createdName = res.data.name || name;
      setRecentlyCreatedProject(createdName);
      window.localStorage.setItem(RECENTLY_CREATED_PROJECT_KEY, createdName);

      let projectId = res.data.id ?? res.data.project_id ?? null;
      if (!projectId) {
        const summaries = await fetchProjectSummaries();
        projectId =
          summaries.find((project) => project.name === createdName)?.id ??
          null;
      }
      if (!projectId) throw new Error("created project id missing");

      setHomeCanvasHandoff({
        projectId,
        draft,
        starterWorkflowId: starterWorkflowId ?? null,
        autoSend: true,
        ...(attachments.length ? { attachments } : {}),
        ...(skillIds.length ? { skillIds } : {}),
      });
      navigate({
        to: PROJECT_SECTION_ROUTES.freezone,
        params: { project: projectId },
      });
    } catch {
      clearHomeCanvasHandoff();
      toast.error(t("home.createFailed", "画布创建失败，请稍后重试"));
    }
  };

  const handleHomeSubmit = ({ attachments, mode, skillIds }: { attachments: ChatAttachment[]; mode: "plan" | "direct"; skillIds: string[] }) => {
    if (createProject.isPending) return;
    if (!homePrompt.trim() && !attachments.length) {
      toast.error(t("home.promptRequired", "先说一句你想做什么"));
      return;
    }
    const draft = homePrompt.trim() || "请分析这些参考素材";
    const instruction = mode === "plan" ? "请先给出创作方案，等我确认后再执行生成。" : "请根据我的要求开始创作，按实战模式执行生成。";
    void createCanvasFromHome({ prompt: `${draft}\n\n${instruction}`, attachments, skillIds });
  };

  const scrollMainToTop = () => {
    requestAnimationFrame(() => {
      document
        .getElementById("main-content")
        ?.scrollTo({ top: 0, behavior: "smooth" });
    });
  };

  const returnToHome = () => {
    openLibrary(false);
    scrollMainToTop();
  };

  // 侧栏只发意图：它不复制一份项目对话框状态，避免两处各持一份草稿。
  // 处理函数放 ref，监听器只装一次，既不会漏掉最新状态也不会每次渲染重装。
  const homeViewHandlerRef = useRef<(intent: HomeViewIntent) => void>(() => {});
  useEffect(() => {
    homeViewHandlerRef.current = (intent) => {
      if (intent === "all") {
        openLibrary(true);
      } else if (intent === "new") {
        openLibrary(true);
        setCreateOpen(true);
      } else {
        openLibrary(false);
      }
      scrollMainToTop();
    };
  });
  useEffect(() => {
    const handler = (event: Event) => {
      // 首页已挂载时事件会被当场处理，清掉待办，避免下次回首页重复触发。
      consumePendingHomeView();
      homeViewHandlerRef.current((event as CustomEvent<HomeViewIntent>).detail);
    };
    window.addEventListener(HOME_VIEW_EVENT, handler);
    const pendingIntent = consumePendingHomeView();
    if (pendingIntent) {
      homeViewHandlerRef.current(pendingIntent);
    }
    return () => window.removeEventListener(HOME_VIEW_EVENT, handler);
  }, []);

  // 进项目恢复上次停留的区块（村长画布 / 镜头工艺子页，默认村长画布）；上次在剧集页且
  // 有剧集深链则直达该集。
  const resolveProjectEntry = useCallback((project: string): string => {
    const section =
      useProjectNavStore.getState().lastSectionByProject[project] ?? "freezone";
    if (section === "episodes") {
      const remembered =
        useEpisodeWorkbenchStore.getState().lastEpisodeLocationByProject[project];
      if (remembered) {
        const normalized = normalizeLastEpisodeLocation(project, remembered);
        if (normalized) return normalized;
      }
    }
    return PROJECT_SECTION_ROUTES[section] ?? PROJECT_SECTION_ROUTES.freezone;
  }, []);

  const openProject = (project: string) =>
    navigate({ to: resolveProjectEntry(project), params: { project } });
  const openProduction = (project: string) =>
    navigate({ to: PROJECT_SECTION_ROUTES.production, params: { project } });
  const preloadProject = useCallback(
    (project: string) => {
      void router
        .preloadRoute({ to: resolveProjectEntry(project), params: { project } })
        .catch(() => undefined);
    },
    [resolveProjectEntry, router],
  );
  const useHomePrompt = (prompt: string) => {
    openLibrary(false);
    setHomePrompt(prompt);
    requestAnimationFrame(() => {
      const composer = document.querySelector<HTMLTextAreaElement>(".village-home-composer-wrap textarea");
      composer?.scrollIntoView({ behavior: "smooth", block: "center" });
      composer?.focus();
    });
  };

  const runWithUndo = (
    name: string,
    forward: () => void,
    undo: () => void,
    toastKey: string,
  ) => {
    forward();
    toast.success(t(toastKey, { name }), {
      action: {
        label: t("project.toasts.undo"),
        onClick: undo,
      },
    });
  };

  const setProjectSelected = (projectId: string, checked: boolean) => {
    setSelectedProjectIds((current) => {
      const next = new Set(current);
      if (checked) next.add(projectId);
      else next.delete(projectId);
      return next;
    });
  };

  const onAction = (
    summary: ProjectSummary,
    action: "archive" | "unarchive" | "delete" | "restore" | "purge",
  ) => {
    const project = projectRouteParam(summary);
    const { name } = summary;
    if (action === "archive") return setPending({ kind: "archive", project, name });
    if (action === "delete") return setPending({ kind: "delete", project, name });
    if (action === "purge") return setPending({ kind: "purge", project, name });
    if (action === "unarchive") {
      return runWithUndo(
        name,
        () => unarchive.mutate(project),
        () => archive.mutate(project),
        "project.toasts.unarchived",
      );
    }
    if (action === "restore") {
      return runWithUndo(
        name,
        () => restore.mutate(project),
        () => softDelete.mutate(project),
        "project.toasts.restored",
      );
    }
  };

  const confirmPending = () => {
    if (!pending) return;
    const { kind, project, name } = pending;
    if (kind === "archive") {
      runWithUndo(
        name,
        () => archive.mutate(project),
        () => unarchive.mutate(project),
        "project.toasts.archived",
      );
    } else if (kind === "delete") {
      runWithUndo(
        name,
        () => softDelete.mutate(project),
        () => restore.mutate(project),
        "project.toasts.deleted",
      );
    } else if (kind === "purge") {
      purge.mutate(project);
      toast.success(t("project.toasts.purged", { name }));
    }
    setPending(null);
  };

  const confirmBulkPurge = async () => {
    const targets = [...selectedDeletedProjects];
    if (targets.length === 0 || bulkPurging) return;

    setBulkPurging(true);
    const failed: ProjectSummary[] = [];
    for (const project of targets) {
      try {
        await purge.mutateAsync(projectRouteParam(project));
      } catch {
        failed.push(project);
      }
    }
    setSelectedProjectIds(new Set(failed.map((project) => project.id)));
    setBulkPurging(false);

    if (failed.length > 0) {
      toast.error(
        t("project.toasts.bulkPurgePartial", {
          purged: targets.length - failed.length,
          failed: failed.length,
        }),
      );
    } else {
      toast.success(
        t("project.toasts.bulkPurged", { count: targets.length }),
      );
    }
  };

  return (
    <div className="village-home w-full">
      <nav aria-label="项目导航" className="village-mobile-nav flex justify-end gap-1 px-4 py-1.5 lg:hidden">
        <Button variant="ghost" aria-label={t("nav.allProjects", "所有项目")} onClick={() => openLibrary(true)}><FolderOpen className="size-4" />项目库</Button>
        <Button variant="ghost" onClick={() => { openLibrary(true); setCreateOpen(true); }}><Plus className="size-4" />创建项目</Button>
      </nav>
      <div className={cn("village-home-workbench village-studio-home", showAllProjects && "!hidden")}>
        <div className="village-studio-content">
          <header className="village-home-heading">
            <div className="village-home-heading__copy">
              <span className="village-home-heading__context">工作台 / 创作室</span>
              <h1 id="studio-heading">把想法推进到成片</h1>
              <p>从故事、资产和分镜开始，所有制作环节留在同一个项目里。</p>
            </div>
            <div className="village-home-heading__actions">
              {recentProjects[0] ? (
                <Button type="button" variant="outline" className="village-banner-secondary" onClick={() => openProduction(projectRouteParam(recentProjects[0]))}>
                  <Film className="size-4" aria-hidden="true" />继续制作
                </Button>
              ) : null}
              <Button type="button" className="village-banner-primary" onClick={() => setCreateOpen(true)}>
                <Plus className="size-4" aria-hidden="true" />新建作品
              </Button>
            </div>
          </header>

          <section className="village-home-command-grid" aria-label="开始或继续创作">
            <div className="village-home-continue">
              <div className="village-home-panel-head">
                <div><span>继续最近项目</span><h2>{recentProjects[0]?.name ?? "你的第一个作品"}</h2></div>
                {recentProjects[0] ? <Button type="button" variant="ghost" onClick={() => openProduction(projectRouteParam(recentProjects[0]))}>打开项目</Button> : null}
              </div>
              {recentProjects[0] ? (
                <button type="button" className="village-home-project-stage" onClick={() => openProduction(projectRouteParam(recentProjects[0]))} onMouseEnter={() => preloadProject(projectRouteParam(recentProjects[0]))}>
                  <span className="village-home-project-stage__index" aria-hidden="true">{recentProjects[0].name.slice(0, 1)}</span>
                  <span className="village-home-project-stage__meta"><small>制作进度</small><strong>{recentProjects[0].episodeCount != null ? `${recentProjects[0].episodeCount} 集已建立` : "等待导入故事"}</strong><span>{recentProjects[0].beatCount != null ? `${recentProjects[0].beatCount} 个镜头片段` : "从故事开始，建立第一条制作记录"}</span></span>
                  <span className="village-home-project-stage__next"><small>下一步</small><strong>进入制作总控</strong><span>查看当前阶段与待处理任务</span></span>
                </button>
              ) : (
                <button type="button" className="village-home-project-stage village-home-project-stage--empty" onClick={() => setCreateOpen(true)}>
                  <span className="village-home-project-stage__index"><Plus className="size-5" /></span><span className="village-home-project-stage__meta"><strong>从一句想法开始</strong><span>空白画布、故事导入或现成模板都可以。</span></span>
                </button>
              )}
            </div>
            <HomeHero prompt={homePrompt} onPromptChange={setHomePrompt} onSubmit={handleHomeSubmit} busy={createProject.isPending} />
          </section>

          <section className="village-home-starters" aria-label="创作起手式">
            <div className="village-studio-section-head"><div><h2>常用起手式</h2><span>把一句话带进一条成熟制作路线</span></div><button type="button" onClick={() => openLibrary(true)}>浏览全部模板 <span aria-hidden="true">↗</span></button></div>
            <div className="village-capability-grid">
              <button type="button" className="village-capability-card village-capability-card--create" onClick={() => setCreateOpen(true)}><span className="village-capability-card__icon"><LayoutGrid className="size-[18px]" aria-hidden="true" /></span><span className="village-capability-card__copy"><strong>空白画布</strong><span>自己搭制作流程</span></span><span className="village-capability-card__arrow" aria-hidden="true">↗</span></button>
              {HOME_CAPABILITY_CARDS.slice(0, 5).map(({ icon: Icon, title, detail, prompt }) => <button key={title} type="button" className="village-capability-card" onClick={() => useHomePrompt(prompt)}><span className="village-capability-card__icon"><Icon className="size-[18px]" aria-hidden="true" /></span><span className="village-capability-card__copy"><strong>{title}</strong><span>{detail}</span></span><span className="village-capability-card__arrow" aria-hidden="true">↗</span></button>)}
            </div>
          </section>

          <section className="village-recent-projects" aria-labelledby="recent-projects-heading">
            <div className="village-studio-section-head"><div><h2 id="recent-projects-heading">{t("home.recentProjects", "最近项目")}</h2><span>{t("home.activeCount", "{{count}} 个进行中", { count: statusCounts.active })}</span></div><button type="button" onClick={() => openLibrary(true)}>查看全部 <span aria-hidden="true">↗</span></button></div>
            {allSummaries.isLoading ? <div className="village-recent-project-skeleton" aria-label={t("home.loadingProjects", "正在整理项目…")} /> : recentProjects.length === 0 ? (
              <button type="button" className="village-recent-project-empty" onClick={() => setCreateOpen(true)}><span className="village-recent-project-empty__plus"><Plus className="size-5" /></span><span><strong>{t("home.noRecentProjects", "还没有正在进行的项目")}</strong><small>{t("home.noRecentProjectsHint", "写下第一句，工作会从这里开始。")}</small></span></button>
            ) : (
              <div className="village-recent-project-track village-recent-project-track--wide">{recentProjects.map((project) => <button key={project.id} type="button" className="village-recent-project-card" onClick={() => openProject(projectRouteParam(project))} onMouseEnter={() => preloadProject(projectRouteParam(project))}><span className="village-recent-project-card__cover" aria-hidden="true"><FolderOpen className="size-[18px]" /></span><span className="village-recent-project-card__copy"><strong>{project.name}</strong><small>{project.episodeCount != null ? t("project.card.episodes", { count: project.episodeCount }) : "等待故事"}{project.updatedAt ? ` · ${formatRelativeTime(project.updatedAt, t)}` : ""}</small></span><span className="village-recent-project-card__open" aria-hidden="true">→</span></button>)}</div>
            )}
          </section>
        </div>
      </div>

      <div
        className={cn(
          "village-project-library mx-auto w-full max-w-[1360px] px-5 pb-16 pt-5 md:px-8",
          !showAllProjects && "!hidden",
        )}
        data-project-library
      >
        <section className="scroll-mt-6">
          <button
            type="button"
            onClick={returnToHome}
            className="mb-6 inline-flex items-center gap-1.5 text-xs font-medium text-muted-foreground transition hover:text-foreground"
          >
            <ArrowLeft className="size-3.5" aria-hidden="true" />
            {t("home.back", "返回首页")}
          </button>
          {/* Header strip */}
          <div className="village-library-head mb-5 flex flex-col gap-5 md:mb-7 md:flex-row md:items-start md:justify-between">
        <div className="min-w-0">
          <h1 className="text-[28px] font-semibold leading-tight tracking-[-0.025em] text-foreground">
            {t("project.dashboardTitle")}
          </h1>
          <p className="mt-[12px] text-[13px] font-medium text-muted-foreground">
            {t("project.dashboardSubtitle")}
          </p>
        </div>
        <div className="village-library-search-row flex flex-wrap items-center justify-end gap-2">
          {totalProjects > 0 && (
            <div className="relative">
              <Search className="pointer-events-none absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
              <Input
                ref={searchInputRef}
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder={t("project.searchPlaceholder")}
                aria-label={t("project.searchPlaceholder")}
                className="h-10 w-[min(17rem,calc(100vw-3rem))] rounded-[9px] border-border bg-card pl-8 focus-visible:border-primary/45 focus-visible:ring-2 focus-visible:ring-primary/15 md:text-xs"
              />
            </div>
          )}
          <Button
            type="button"
            onClick={() => setCreateOpen(true)}
            className="h-10 rounded-[9px] bg-primary px-4 text-xs font-semibold text-primary-foreground shadow-none hover:bg-primary/90"
          >
            <Plus className="size-4" aria-hidden="true" />
            {t("project.create")}
          </Button>
          <Dialog open={createOpen} onOpenChange={setCreateOpen}>
            <DialogContent className="village-glass-popover gap-4 overflow-hidden rounded-[16px] p-7 shadow-none sm:max-w-md">
              <DialogHeader className="gap-2">
                <DialogTitle className="text-lg font-medium tracking-tight">

                  <span>{t("project.create")}</span>
                </DialogTitle>
                <p className="text-xs leading-5 text-muted-foreground">
                  {t("project.emptyDescription")}
                </p>
              </DialogHeader>
              <div className="mt-2 flex flex-col gap-2">
                <div className="relative">
                  <Input
                    id="project-name"
                    value={newName}
                    onChange={(e) => setNewName(e.target.value)}
                    placeholder={t("project.namePlaceholder")}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") {
                        e.preventDefault();
                        handleCreate();
                      }
                    }}
                    aria-invalid={!!createNameError || undefined}
                    aria-describedby={createNameError ? "project-name-error" : undefined}
                    autoFocus
                    className="h-11 rounded-[8px] border-border bg-card px-3 pr-10 text-sm placeholder:text-muted-foreground/70 focus-visible:border-primary/45 focus-visible:ring-2 focus-visible:ring-primary/15"
                  />
                  {newName && (
                    <button
                      type="button"
                      onClick={() => setNewName("")}
                      className="absolute right-2 top-1/2 inline-flex size-7 -translate-y-1/2 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-white/[0.08] hover:text-foreground"
                      aria-label={t("project.clearName")}
                    >
                      <XIcon className="size-3.5" aria-hidden="true" />
                    </button>
                  )}
                </div>
                {createNameError && (
                  <p
                    id="project-name-error"
                    className="text-xs text-destructive"
                  >
                    {createNameError}
                  </p>
                )}
              </div>
              <DialogFooter className="-mx-7 -mb-7 border-t-0 bg-transparent p-7 pt-3 sm:flex-row sm:justify-end">
                <Button
                  variant="outline"
                  onClick={() => setCreateOpen(false)}
                  className="h-10 w-18 rounded-[8px] border-border bg-card px-0 text-sm font-normal text-foreground/80 hover:border-foreground/20 hover:bg-accent hover:text-foreground"
                >
                  {t("common.cancel")}
                </Button>
                <Button
                  onClick={handleCreate}
                  disabled={
                    createProject.isPending || !trimmedNewName || !!createNameError
                  }
                  className="h-10 w-18 rounded-[8px] bg-primary px-0 text-sm font-normal text-primary-foreground shadow-none hover:bg-primary/90"
                >
                  {createProject.isPending && (
                    <Loader2
                      className="size-4 animate-spin"
                      aria-hidden="true"
                    />
                  )}
                  {t("common.confirm")}
                </Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>
        </div>
      </div>

      {/* Tab strip + sort */}
      <div className="village-library-toolbar mb-7 flex flex-wrap items-center gap-x-3 gap-y-2">
        <div className="flex min-w-0 flex-wrap items-center gap-x-2.5 gap-y-2">
          <DashboardTabStrip
            current={currentTab}
            counts={statusCounts}
            onChange={setCurrentTab}
          />
          <Select value={sort} onValueChange={(v) => setSort(v as SortKey)}>
            <SelectTrigger
              size="sm"
              className="h-9 gap-1 rounded-[8px] border-border bg-card px-3 text-xs text-muted-foreground hover:bg-accent data-[size=sm]:h-9 data-[size=sm]:rounded-[8px]"
            >
              <SelectValue>
                {(value: string) => {
                  const opt = SORT_OPTIONS.find((o) => o.value === value);
                  return opt ? t(opt.labelKey) : value;
                }}
              </SelectValue>
            </SelectTrigger>
            <SelectContent
              align="end"
              sideOffset={8}
              alignItemWithTrigger={false}
              className="w-40 rounded-md p-1 shadow-xl shadow-black/20 data-[align-trigger=true]:animate-in [&_[data-slot=select-item]]:min-h-8 [&_[data-slot=select-item]]:rounded-sm [&_[data-slot=select-item]]:px-2 [&_[data-slot=select-item]]:py-1.5 [&_[data-slot=select-item]]:text-xs [&_[data-slot=select-item]:focus]:bg-white/8 [&_[data-slot=select-item]:focus]:text-current [&_[data-slot=select-item]_svg]:size-3.5"
            >
              {SORT_OPTIONS.map((opt) => (
                <SelectItem key={opt.value} value={opt.value}>
                  {t(opt.labelKey)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        {currentTab === "deleted" && deletedList.length > 0 && (
          <p className="text-xs text-muted-foreground">
            {t("project.trashRetentionHint")}
          </p>
        )}
        {currentTab === "deleted" && deletableDeletedProjects.length > 0 && (
          <div className="village-glass-batchbar flex min-w-0 basis-full flex-wrap items-center gap-2 rounded-[10px] px-2.5 py-1.5 sm:basis-auto">
            <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <Checkbox
                aria-label={t("project.bulk.selectAllLabel")}
                checked={allDeletedSelected}
                indeterminate={
                  !allDeletedSelected && selectedDeletedProjects.length > 0
                }
                disabled={bulkPurging}
                onCheckedChange={(checked) =>
                  setSelectedProjectIds(
                    checked
                      ? new Set(
                          deletableDeletedProjects.map((project) => project.id),
                        )
                      : new Set(),
                  )
                }
              />
              {t("project.bulk.selectAll")}
            </div>
            <span className="text-xs tabular-nums text-muted-foreground">
              {t("project.bulk.selectedCount", {
                count: selectedDeletedProjects.length,
              })}
            </span>
            <Button
              type="button"
              size="sm"
              variant="destructive"
              className="h-8 rounded-[7px] px-3 text-xs"
              disabled={selectedDeletedProjects.length === 0 || bulkPurging}
              onClick={() => setBulkPurgeOpen(true)}
            >
              {bulkPurging && <Loader2 className="size-3.5 animate-spin" />}
              <Trash2 className="size-3.5" />
              {t("project.bulk.deleteSelected")}
            </Button>
          </div>
        )}
        <div className="ml-auto flex items-center gap-2">
          <ViewToggle value={view} onChange={setView} />
        </div>
      </div>

      {/* Grid / list / empty states */}
      {allSummaries.isLoading ? (
        view === "list" ? (
          <LoadingList />
        ) : (
          <LoadingGrid size="md" />
        )
      ) : allSummaries.isError && !allSummaries.data ? (
        <div role="alert" className="py-12 text-center">
          <p>项目加载失败，请重试</p>
          <Button variant="ghost" disabled={allSummaries.isFetching} onClick={() => void allSummaries.refetch()}>重试</Button>
        </div>
      ) : (
        (() => {
          const list =
            currentTab === "active"
              ? activeList
              : currentTab === "archived"
                ? archivedList
                : deletedList;

          if (list.length === 0) {
            if (search.trim()) return (
              <div className="village-library-empty py-14 text-center" role="status">
                <p className="font-medium text-foreground">没有找到「{search.trim()}」</p>
                <p className="mt-1.5 text-xs text-muted-foreground">换个关键词，或清除当前搜索。</p>
                <Button variant="ghost" onClick={() => setSearch("")}>清除搜索</Button>
              </div>
            );
            if (currentTab === "active") {
              const realProjects = statusCounts.active + statusCounts.archived;
              return realProjects === 0 ? (
                <FirstTimeEmpty onCreate={() => setCreateOpen(true)} />
              ) : (
                <ActiveEmptyWithCreate
                  onCreate={() => setCreateOpen(true)}
                />
              );
            }
            if (currentTab === "archived") {
              return (
                <TabEmpty
                  title={t("project.emptyArchived")}
                  description={t("project.emptyArchivedDescription")}
                />
              );
            }
            return (
              <TabEmpty
                title={t("project.emptyTrash")}
                description={t("project.emptyTrashDescription")}
              />
            );
          }

          return (
            <motion.div
              initial={wasColdOnMount ? "hidden" : false}
              animate="visible"
              variants={{
                hidden: {},
                visible: { transition: { staggerChildren: 0.03 } },
              }}
              className={cn(
                view === "list"
                  ? "village-project-rows flex flex-col gap-2"
                  : "village-project-cards grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4",
              )}
            >
              {currentTab === "active" && view === "card" && (
                <motion.div
                  className="h-full w-full"
                  variants={{
                    hidden: { opacity: 0.6 },
                    visible: {
                      opacity: 1,
                      transition: { duration: 0.12, ease: "easeOut" },
                    },
                  }}
                >
                  <CreateProjectCard onCreate={() => setCreateOpen(true)} />
                </motion.div>
              )}
              {list.slice(0, 20).map((summary) => (
                <motion.div
                  key={projectRouteParam(summary)}
                  variants={{
                    hidden: { opacity: 0.6 },
                    visible: {
                      opacity: 1,
                      transition: { duration: 0.12, ease: "easeOut" },
                    },
                  }}
                >
                  {view === "list" ? (
                    <ProjectRow
                      summary={summary}
                      selectable={currentTab === "deleted"}
                      selected={selectedProjectIds.has(summary.id)}
                      onSelectedChange={(checked) =>
                        setProjectSelected(summary.id, checked)
                      }
                      onOpen={() => openProject(projectRouteParam(summary))}
                      onPreload={() => preloadProject(projectRouteParam(summary))}
                      onShare={() => setShareProject(summary)}
                      onAction={(action) => onAction(summary, action)}
                    />
                  ) : (
                    <ProjectCard
                      summary={summary}
                      selectable={currentTab === "deleted"}
                      selected={selectedProjectIds.has(summary.id)}
                      onSelectedChange={(checked) =>
                        setProjectSelected(summary.id, checked)
                      }
                      size="md"
                      onOpen={() => openProject(projectRouteParam(summary))}
                      onPreload={() => preloadProject(projectRouteParam(summary))}
                      onShare={() => setShareProject(summary)}
                      onAction={(action) => onAction(summary, action)}
                    />
                  )}
                </motion.div>
              ))}
              {list
                .slice(20)
                .map((summary) =>
                  view === "list" ? (
                    <ProjectRow
                      key={projectRouteParam(summary)}
                      summary={summary}
                      selectable={currentTab === "deleted"}
                      selected={selectedProjectIds.has(summary.id)}
                      onSelectedChange={(checked) =>
                        setProjectSelected(summary.id, checked)
                      }
                      onOpen={() => openProject(projectRouteParam(summary))}
                      onPreload={() => preloadProject(projectRouteParam(summary))}
                      onShare={() => setShareProject(summary)}
                      onAction={(action) => onAction(summary, action)}
                    />
                  ) : (
                    <ProjectCard
                      key={projectRouteParam(summary)}
                      summary={summary}
                      selectable={currentTab === "deleted"}
                      selected={selectedProjectIds.has(summary.id)}
                      onSelectedChange={(checked) =>
                        setProjectSelected(summary.id, checked)
                      }
                      size="md"
                      onOpen={() => openProject(projectRouteParam(summary))}
                      onPreload={() => preloadProject(projectRouteParam(summary))}
                      onShare={() => setShareProject(summary)}
                      onAction={(action) => onAction(summary, action)}
                    />
                  ),
                )}
            </motion.div>
          );
        })()
      )}
        </section>
      </div>

      {/* Dialogs */}
      <ArchiveDialog
        name={pending?.kind === "archive" ? pending.name : ""}
        open={pending?.kind === "archive"}
        onOpenChange={(v) => !v && setPending(null)}
        onConfirm={confirmPending}
      />
      <DeleteDialog
        name={pending?.kind === "delete" ? pending.name : ""}
        open={pending?.kind === "delete"}
        onOpenChange={(v) => !v && setPending(null)}
        onConfirm={confirmPending}
      />
      <PurgeDialog
        name={pending?.kind === "purge" ? pending.name : ""}
        open={pending?.kind === "purge"}
        onOpenChange={(v) => !v && setPending(null)}
        onConfirm={confirmPending}
      />
      <PurgeDialog
        count={selectedDeletedProjects.length}
        busy={bulkPurging}
        open={bulkPurgeOpen}
        onOpenChange={(open) => {
          if (!open && !bulkPurging) setBulkPurgeOpen(false);
        }}
        onConfirm={confirmBulkPurge}
      />
      <ShareProjectDialog
        project={shareProject}
        open={!!shareProject}
        onOpenChange={(open) => {
          if (!open) setShareProject(null);
        }}
      />
    </div>
  );
}

export { ProjectDashboard };

export const Route = createFileRoute("/_app/")({
  component: ProjectDashboard,
});
