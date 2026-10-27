// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab

import {
  BadgeCheck,
  Check,
  ChevronDown,
  Loader2,
  PackageOpen,
  Power,
  PowerOff,
  Search,
  Sparkles,
  Store,
  Trash2,
  Upload,
  X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import {
  deleteSkillStoreItem,
  getSkillStoreCatalog,
  importSkillStoreFile,
  installSkillStoreItem,
  skillStoreAgentId,
  uninstallSkillStoreItem,
  type SkillStoreCatalog,
  type SkillStoreItem,
} from "@/api/skill-store";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { backendErrorToastMessage } from "@/lib/api-errors";
import { cn } from "@/lib/utils";

type StoreTab = "general" | "favorite" | "mine";

const TABS: readonly { id: StoreTab; label: string }[] = [
  { id: "general", label: "通用" },
  { id: "favorite", label: "收藏" },
  { id: "mine", label: "我的" },
];

type ScopeFilter = "all" | "installed" | "custom";

const SCOPE_LABELS: Record<ScopeFilter, string> = {
  all: "全部",
  installed: "已启用",
  custom: "我的技能",
};

function slugOf(item: SkillStoreItem): string {
  return `/${(item.skill_key || item.id).replace(/^village-canvas-/, "").slice(0, 26)}`;
}

const PREVIEW_WIDTH = 264;
const PREVIEW_HEIGHT = 268;
const PREVIEW_GAP = 14;
const VIEWPORT_MARGIN = 12;

interface SkillHoverPreview {
  item: SkillStoreItem;
  top: number;
  left: number;
}

/** Floating peek card: cover on top, then name and one-line description. */
function SkillHoverCard({ preview }: { preview: SkillHoverPreview }) {
  const { item } = preview;
  return (
    <div
      aria-hidden
      data-skill-hover-card={item.id}
      style={{ top: preview.top, left: preview.left, width: PREVIEW_WIDTH }}
      className="pointer-events-none fixed z-[70] animate-in fade-in-0 zoom-in-95 overflow-hidden rounded-[10px] border border-white/[0.10] bg-[#141414]/97 shadow-[0_18px_50px_rgba(0,0,0,0.66)] backdrop-blur-md duration-150 ease-out motion-reduce:animate-none"
    >
      <div className="relative h-[148px] w-full overflow-hidden bg-[#0e0e0e]">
        {item.cover_image ? (
          <img
            src={item.cover_image}
            alt=""
            className="size-full object-cover"
            onError={(event) => {
              event.currentTarget.style.visibility = "hidden";
            }}
          />
        ) : (
          <div className="flex size-full items-center justify-center bg-[radial-gradient(circle_at_30%_20%,rgba(148,233,255,0.16),transparent_60%),radial-gradient(circle_at_75%_75%,rgba(180,150,255,0.14),transparent_62%)]">
            <Sparkles className="size-7 text-white/22" />
          </div>
        )}
        <div className="pointer-events-none absolute inset-x-0 bottom-0 h-12 bg-gradient-to-t from-[#141414] to-transparent" />
      </div>
      <div className="px-3 pb-3 pt-2">
        <div className="flex items-center gap-1.5">
          <p className="truncate text-[13px] font-semibold text-white/94">{item.name}</p>
          {item.installed && <Check className="size-3 shrink-0 text-white/35" />}
        </div>
        <p className="mt-1 line-clamp-2 text-[11.5px] leading-[17px] text-white/45">
          {item.description}
        </p>
        <div className="mt-2 flex items-center gap-1.5 text-[9.5px] text-white/28">
          <span className="rounded-full border border-white/[0.08] px-1.5 py-0.5">
            {item.category}
          </span>
          {item.contract && (
            <span className="rounded-full border border-white/[0.08] px-1.5 py-0.5">
              {item.contract.maturity === "production_ready"
                ? "可执行"
                : item.contract.maturity === "workflow_ready"
                  ? "可编排"
                  : "待完善"}{" "}
              {item.contract.readiness_score}
            </span>
          )}
        </div>
      </div>
    </div>
  );
}

/**
 * 技能商店：按 LibTV Skill 列表的样式重做。
 *
 * 结构：右上「+ 添加 / 全部」两个下拉，左侧「收藏 / 最近」，右侧搜索框，
 * 下面是「名称 + /slug」的行式列表，悬停出「详情」。契约、准入、分类这些
 * 多出来的信息收进详情面板和两个下拉里，列表本身保持干净。
 */
export function SkillStoreDialog({
  open,
  onOpenChange,
  mountedSkillIds,
  onToggleMount,
  onCatalogChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  mountedSkillIds: readonly string[];
  onToggleMount: (agentSkillId: string) => void;
  onCatalogChange?: (catalog: SkillStoreCatalog) => void;
}) {
  const { t } = useTranslation();
  const uploadRef = useRef<HTMLInputElement | null>(null);
  const [catalog, setCatalog] = useState<SkillStoreCatalog | null>(null);
  const [loading, setLoading] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [tab, setTab] = useState<StoreTab>("general");
  const [scope, setScope] = useState<ScopeFilter>("all");
  const [category, setCategory] = useState("all");
  const [addMenuOpen, setAddMenuOpen] = useState(false);
  const [scopeMenuOpen, setScopeMenuOpen] = useState(false);
  const [openId, setOpenId] = useState<string | null>(null);
  const [hoverPreview, setHoverPreview] = useState<SkillHoverPreview | null>(null);
  const hoverTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(
    () => () => {
      if (hoverTimer.current) clearTimeout(hoverTimer.current);
    },
    [],
  );

  const schedulePreview = useCallback((item: SkillStoreItem, element: HTMLElement) => {
    if (hoverTimer.current) clearTimeout(hoverTimer.current);
    hoverTimer.current = setTimeout(() => {
      const rect = element.getBoundingClientRect();
      const dialog = element.closest('[role="dialog"]');
      const dialogRect = dialog?.getBoundingClientRect();
      const windowWidth = window.innerWidth;
      const windowHeight = window.innerHeight;
      // Sit in the gutter left of the panel; when the window is too narrow for
      // that, slide in as far as the viewport allows instead of overflowing.
      const panelLeft = dialogRect?.left ?? rect.left;
      const maxLeft = Math.max(VIEWPORT_MARGIN, windowWidth - PREVIEW_WIDTH - VIEWPORT_MARGIN);
      const left = Math.min(Math.max(VIEWPORT_MARGIN, panelLeft - PREVIEW_WIDTH - PREVIEW_GAP), maxLeft);
      const maxTop = windowHeight - PREVIEW_HEIGHT - VIEWPORT_MARGIN;
      const top = Math.min(
        Math.max(VIEWPORT_MARGIN, rect.top - 8),
        Math.max(VIEWPORT_MARGIN, maxTop),
      );
      setHoverPreview({ item, top, left });
    }, 220);
  }, []);

  const clearPreview = useCallback(() => {
    if (hoverTimer.current) clearTimeout(hoverTimer.current);
    hoverTimer.current = null;
    setHoverPreview(null);
  }, []);

  const loadCatalog = useCallback(async () => {
    setLoading(true);
    try {
      const next = await getSkillStoreCatalog();
      setCatalog(next);
      onCatalogChange?.(next);
    } catch (error) {
      toast.error(backendErrorToastMessage(error, t));
    } finally {
      setLoading(false);
    }
  }, [onCatalogChange, t]);

  useEffect(() => {
    if (!open) return;
    void loadCatalog();
  }, [loadCatalog, open]);

  const mounted = useCallback(
    (item: SkillStoreItem) => mountedSkillIds.includes(skillStoreAgentId(item.id)),
    [mountedSkillIds],
  );

  const visibleItems = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase();
    return (catalog?.items ?? []).filter((item) => {
      if (scope === "installed" && !item.installed) return false;
      if (scope === "custom" && item.source !== "custom") return false;
      if (category !== "all" && item.category !== category) return false;
      if (tab === "favorite" && !(item.installed || mounted(item))) return false;
      if (tab === "mine" && item.source !== "custom" && !mounted(item)) return false;
      if (!needle) return true;
      return [item.name, item.description, item.skill_key, item.category, ...item.tags].some(
        (value) => value.toLocaleLowerCase().includes(needle),
      );
    });
  }, [catalog?.items, category, mounted, query, scope, tab]);

  const runMutation = useCallback(
    async (skillId: string, mutation: () => Promise<unknown>, success: string) => {
      setBusyId(skillId);
      try {
        await mutation();
        toast.success(success);
        await loadCatalog();
      } catch (error) {
        toast.error(backendErrorToastMessage(error, t));
      } finally {
        setBusyId(null);
      }
    },
    [loadCatalog, t],
  );

  const handleUpload = useCallback(
    async (file: File | null) => {
      if (!file) return;
      setBusyId("__upload__");
      try {
        const result = await importSkillStoreFile(file);
        toast.success(`已导入 ${result.imported} 个技能`);
        setScope("custom");
        await loadCatalog();
      } catch (error) {
        toast.error(backendErrorToastMessage(error, t));
      } finally {
        setBusyId(null);
        if (uploadRef.current) uploadRef.current.value = "";
      }
    },
    [loadCatalog, t],
  );

  const categories = catalog?.categories ?? [];

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      {hoverPreview &&
        createPortal(<SkillHoverCard preview={hoverPreview} />, document.body)}
      <DialogContent
        showCloseButton={false}
        data-agent-dialog="v4"
        className="village-agent-dialog-v4 village-agent-dialog-v4--skills skill-store-dialog flex h-[min(88vh,760px)] w-[min(94vw,860px)] max-w-none flex-col gap-0 overflow-hidden rounded-[16px] border border-white/[0.09] bg-[#1a1a1a]/98 p-0 text-white shadow-[0_30px_100px_rgba(0,0,0,0.62)] backdrop-blur-2xl sm:max-w-none"
      >
        <DialogTitle className="sr-only">技能商店</DialogTitle>

        <div className="flex shrink-0 items-center gap-2 px-4 pb-1 pt-3">
          <span className="mr-auto text-[13px] font-semibold text-white/88">Skill</span>
          <input
            ref={uploadRef}
            type="file"
            className="hidden"
            accept=".md,.markdown,.json,.zip"
            onChange={(event) => void handleUpload(event.target.files?.[0] ?? null)}
          />
          <div className="relative">
            <button
              type="button"
              onClick={() => {
                setAddMenuOpen((value) => !value);
                setScopeMenuOpen(false);
              }}
              aria-expanded={addMenuOpen}
              aria-label="添加技能"
              className="inline-flex h-8 items-center gap-1.5 rounded-full border border-white/[0.14] bg-white/[0.06] px-3 text-[12px] font-medium text-white/85 transition hover:border-white/[0.26] hover:bg-white/[0.10]"
            >
              <span className="text-[14px] leading-none">+</span>
              创建
              <ChevronDown className="size-3 text-white/45" />
            </button>
            {addMenuOpen && (
              <div
                role="menu"
                className="absolute right-0 top-9 z-30 w-44 rounded-xl border border-white/[0.10] bg-[#232323] p-1 shadow-[0_18px_46px_rgba(0,0,0,0.5)]"
              >
                <button
                  type="button"
                  role="menuitem"
                  onClick={() => {
                    setAddMenuOpen(false);
                    uploadRef.current?.click();
                  }}
                  disabled={busyId !== null}
                  className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-[12px] text-white/78 transition hover:bg-white/[0.07] hover:text-white disabled:opacity-45"
                >
                  {busyId === "__upload__" ? (
                    <Loader2 className="size-3.5 animate-spin" />
                  ) : (
                    <Upload className="size-3.5" />
                  )}
                  上传技能
                </button>
                <button
                  type="button"
                  role="menuitem"
                  onClick={() => {
                    setAddMenuOpen(false);
                    void loadCatalog();
                  }}
                  disabled={loading}
                  className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-[12px] text-white/78 transition hover:bg-white/[0.07] hover:text-white disabled:opacity-45"
                >
                  {loading ? (
                    <Loader2 className="size-3.5 animate-spin" />
                  ) : (
                    <Sparkles className="size-3.5" />
                  )}
                  刷新目录
                </button>
              </div>
            )}
          </div>

          <div className="relative">
            <button
              type="button"
              onClick={() => {
                setScopeMenuOpen((value) => !value);
                setAddMenuOpen(false);
              }}
              aria-expanded={scopeMenuOpen}
              aria-label="筛选技能范围"
              className="inline-flex h-8 items-center gap-1.5 rounded-full border border-white/[0.14] bg-white/[0.06] px-3 text-[12px] font-medium text-white/85 transition hover:border-white/[0.26] hover:bg-white/[0.10]"
            >
              {SCOPE_LABELS[scope]}
              <ChevronDown className="size-3 text-white/45" />
            </button>
            {scopeMenuOpen && (
              <div
                role="menu"
                className="absolute right-0 top-9 z-30 w-52 rounded-xl border border-white/[0.10] bg-[#232323] p-1 shadow-[0_18px_46px_rgba(0,0,0,0.5)]"
              >
                {(Object.keys(SCOPE_LABELS) as ScopeFilter[]).map((item) => (
                  <button
                    key={item}
                    type="button"
                    role="menuitem"
                    onClick={() => {
                      setScope(item);
                      setScopeMenuOpen(false);
                    }}
                    className={cn(
                      "flex w-full items-center justify-between rounded-lg px-2.5 py-2 text-left text-[12px] transition hover:bg-white/[0.07]",
                      scope === item ? "text-white" : "text-white/70",
                    )}
                  >
                    {SCOPE_LABELS[item]}
                    <span className="text-[10px] text-white/30">
                      {item === "all"
                        ? catalog?.total ?? 0
                        : item === "installed"
                          ? catalog?.installed ?? 0
                          : catalog?.custom ?? 0}
                    </span>
                  </button>
                ))}
                {categories.length > 0 && (
                  <>
                    <div className="my-1 h-px bg-white/[0.07]" />
                    <p className="px-2.5 py-1 text-[10px] uppercase tracking-[0.1em] text-white/25">
                      创作分类
                    </p>
                    <div className="max-h-48 overflow-y-auto">
                      <button
                        type="button"
                        role="menuitem"
                        onClick={() => {
                          setCategory("all");
                          setScopeMenuOpen(false);
                        }}
                        className={cn(
                          "flex w-full rounded-lg px-2.5 py-1.5 text-left text-[11px] transition hover:bg-white/[0.07]",
                          category === "all" ? "text-white" : "text-white/55",
                        )}
                      >
                        全部分类
                      </button>
                      {categories.map((item) => (
                        <button
                          key={item}
                          type="button"
                          role="menuitem"
                          onClick={() => {
                            setCategory(item);
                            setScopeMenuOpen(false);
                          }}
                          className={cn(
                            "flex w-full rounded-lg px-2.5 py-1.5 text-left text-[11px] transition hover:bg-white/[0.07]",
                            category === item ? "text-white" : "text-white/55",
                          )}
                        >
                          {item}
                        </button>
                      ))}
                    </div>
                  </>
                )}
              </div>
            )}
          </div>

          <button
            type="button"
            onClick={() => onOpenChange(false)}
            className="flex size-8 items-center justify-center rounded-lg text-white/45 transition hover:bg-white/[0.07] hover:text-white/85"
            aria-label="关闭技能商店"
          >
            <X className="size-4" />
          </button>
        </div>

        <div className="flex shrink-0 items-center gap-3 px-4 py-2.5">
          <div className="flex items-center gap-4">
            {TABS.map((item) => (
              <button
                key={item.id}
                type="button"
                onClick={() => setTab(item.id)}
                aria-pressed={tab === item.id}
                className={cn(
                  "text-[12.5px] transition-colors",
                  tab === item.id ? "font-medium text-white" : "text-white/38 hover:text-white/70",
                )}
              >
                {item.label}
              </button>
            ))}
          </div>
          <label className="ml-auto flex h-8 w-[190px] items-center gap-2 rounded-full border border-white/[0.10] bg-white/[0.05] px-3 text-white/45 focus-within:border-white/[0.22]">
            <Search className="size-3.5 shrink-0" aria-hidden />
            <span className="sr-only">搜索技能</span>
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              className="min-w-0 flex-1 bg-transparent text-[12px] text-white/86 outline-none placeholder:text-white/28"
              placeholder="搜索 Skill"
              aria-label="搜索技能商店"
            />
          </label>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-3 [scrollbar-width:thin]" onScroll={clearPreview}>
          {loading && !catalog ? (
            <div className="flex h-full min-h-56 items-center justify-center gap-2 text-sm text-white/45">
              <Loader2 className="size-4 animate-spin" />
              正在读取本地技能目录
            </div>
          ) : visibleItems.length === 0 ? (
            <div className="flex h-full min-h-56 flex-col items-center justify-center text-center">
              <PackageOpen className="size-8 text-white/20" />
              <p className="mt-3 text-sm text-white/65">没有匹配的技能</p>
              <p className="mt-1 text-[11px] text-white/32">换个关键词，或从「+ 添加」上传 SKILL.md。</p>
            </div>
          ) : (
            <ul className="flex flex-col">
              {visibleItems.map((item) => {
                const isMounted = mounted(item);
                const isBusy = busyId === item.id;
                const expanded = openId === item.id;
                return (
                  <li key={item.id} className="group">
                    <div
                      role="button"
                      tabIndex={0}
                      onClick={() => {
                        clearPreview();
                        setOpenId(expanded ? null : item.id);
                      }}
                      onKeyDown={(event) => {
                        if (event.key !== "Enter" && event.key !== " ") return;
                        event.preventDefault();
                        clearPreview();
                        setOpenId(expanded ? null : item.id);
                      }}
                      onMouseEnter={(event) => schedulePreview(item, event.currentTarget)}
                      onMouseLeave={clearPreview}
                      onFocus={(event) => schedulePreview(item, event.currentTarget)}
                      onBlur={clearPreview}
                      className={cn(
                        "flex cursor-pointer items-start gap-2.5 rounded-lg px-2.5 py-2.5 transition-colors hover:bg-white/[0.05] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-white/20",
                        expanded && "bg-white/[0.05]",
                      )}
                    >
                      <button
                        type="button"
                        tabIndex={-1}
                        aria-label={`预览 ${item.name}`}
                        onClick={(event) => {
                          event.stopPropagation();
                          setOpenId(expanded ? null : item.id);
                        }}
                        className="size-9 shrink-0 overflow-hidden rounded-md border border-white/[0.08] bg-white/[0.04]"
                      >
                        {item.cover_image ? (
                          <img
                            src={item.cover_image}
                            alt=""
                            loading="lazy"
                            className="size-full object-cover"
                            onError={(event) => {
                              event.currentTarget.style.visibility = "hidden";
                            }}
                          />
                        ) : (
                          <span className="flex size-full items-center justify-center bg-[radial-gradient(circle_at_30%_20%,rgba(148,233,255,0.16),transparent_60%),radial-gradient(circle_at_75%_75%,rgba(180,150,255,0.14),transparent_62%)]">
                            <Sparkles className="size-3.5 text-white/28" />
                          </span>
                        )}
                      </button>
                      <div className="min-w-0 flex-1">
                        <div className="flex min-w-0 items-center gap-2">
                          <span className="truncate text-[13px] font-medium text-white/92">
                            {item.name}
                          </span>
                          <span className="shrink-0 rounded-[5px] bg-white/[0.07] px-1.5 py-0.5 text-[10px] text-white/40">
                            {slugOf(item)}
                          </span>
                          {item.installed && (
                            <Check className="size-3 shrink-0 text-white/35" aria-label="已启用" />
                          )}
                          {isMounted && (
                            <BadgeCheck className="size-3 shrink-0 text-cyan-200/80" aria-label="已指定" />
                          )}
                        </div>
                        <p className="mt-1 line-clamp-2 text-[11.5px] leading-[18px] text-white/38">
                          {item.description}
                        </p>
                      </div>
                      <button
                        type="button"
                        onClick={(event) => {
                          event.stopPropagation();
                          setOpenId(expanded ? null : item.id);
                        }}
                        className={cn(
                          "mt-0.5 shrink-0 rounded-md px-2 py-1 text-[11px] transition-colors",
                          expanded
                            ? "bg-white/[0.10] text-white/80"
                            : "text-white/0 group-hover:text-white/55 hover:!text-white/85",
                        )}
                      >
                        {expanded ? "收起" : "详情"}
                      </button>
                    </div>

                    {expanded && (
                      <div className="mx-2.5 mb-2 rounded-xl border border-white/[0.07] bg-black/25 p-3">
                        <div className="flex flex-wrap items-center gap-1.5 text-[10px] text-white/40">
                          <span className="rounded-full border border-white/[0.08] px-1.5 py-0.5">
                            {item.source_label}
                          </span>
                          <span className="rounded-full border border-white/[0.08] px-1.5 py-0.5">
                            {item.category}
                          </span>
                          {item.contract && (
                            <span className="rounded-full border border-white/[0.08] px-1.5 py-0.5">
                              {item.contract.maturity === "production_ready"
                                ? "可执行"
                                : item.contract.maturity === "workflow_ready"
                                  ? "可编排"
                                  : "待完善"}{" "}
                              {item.contract.readiness_score}
                            </span>
                          )}
                          {item.admission?.status === "review_required" && (
                            <span className="rounded-full border border-amber-300/20 px-1.5 py-0.5 text-amber-100/70">
                              需复核
                            </span>
                          )}
                          {item.admission?.can_install === false && (
                            <span className="rounded-full border border-red-300/20 px-1.5 py-0.5 text-red-100/70">
                              已阻断
                            </span>
                          )}
                        </div>
                        {item.contract?.output_contract && (
                          <p className="mt-2 text-[11px] leading-[18px] text-white/45">
                            输出：{item.contract.output_contract}
                          </p>
                        )}
                        {item.tags.length > 0 && (
                          <div className="mt-2 flex flex-wrap gap-1">
                            {item.tags.slice(0, 6).map((tag) => (
                              <span
                                key={tag}
                                className="rounded-full bg-white/[0.045] px-2 py-0.5 text-[9.5px] text-white/35"
                              >
                                {tag}
                              </span>
                            ))}
                          </div>
                        )}
                        <div className="mt-3 flex items-center gap-2">
                          {!item.installed ? (
                            <button
                              type="button"
                              disabled={isBusy || item.admission?.can_install === false}
                              onClick={() =>
                                void runMutation(
                                  item.id,
                                  () => installSkillStoreItem(item.id),
                                  `已启用 ${item.name}`,
                                )
                              }
                              className="inline-flex h-7 items-center gap-1.5 rounded-md bg-white px-2.5 text-[11px] font-semibold text-black transition hover:bg-white/90 disabled:cursor-not-allowed disabled:opacity-45"
                            >
                              {isBusy ? (
                                <Loader2 className="size-3 animate-spin" />
                              ) : (
                                <Power className="size-3" />
                              )}
                              {item.admission?.can_install === false ? "需复核" : "启用"}
                            </button>
                          ) : (
                            <>
                              <button
                                type="button"
                                disabled={isBusy}
                                onClick={() => onToggleMount(skillStoreAgentId(item.id))}
                                className={cn(
                                  "inline-flex h-7 items-center gap-1.5 rounded-md border px-2.5 text-[11px] font-semibold transition disabled:opacity-45",
                                  isMounted
                                    ? "border-cyan-300/25 bg-cyan-400/[0.12] text-cyan-50"
                                    : "border-white/[0.14] bg-white/[0.06] text-white/78 hover:bg-white/[0.10]",
                                )}
                              >
                                {isMounted ? (
                                  <BadgeCheck className="size-3" />
                                ) : (
                                  <Sparkles className="size-3" />
                                )}
                                {isMounted ? "已指定" : "手动指定"}
                              </button>
                              <button
                                type="button"
                                disabled={isBusy}
                                onClick={() => {
                                  if (isMounted) onToggleMount(skillStoreAgentId(item.id));
                                  void runMutation(
                                    item.id,
                                    () => uninstallSkillStoreItem(item.id),
                                    `已停用 ${item.name}`,
                                  );
                                }}
                                className="inline-flex h-7 items-center gap-1.5 rounded-md border border-white/[0.10] px-2.5 text-[11px] text-white/50 transition hover:bg-white/[0.06] hover:text-white/80 disabled:opacity-40"
                              >
                                {isBusy ? (
                                  <Loader2 className="size-3 animate-spin" />
                                ) : (
                                  <PowerOff className="size-3" />
                                )}
                                停用
                              </button>
                            </>
                          )}
                          {item.removable && (
                            <button
                              type="button"
                              disabled={isBusy}
                              onClick={() => {
                                if (isMounted) onToggleMount(skillStoreAgentId(item.id));
                                void runMutation(
                                  item.id,
                                  () => deleteSkillStoreItem(item.id),
                                  `已删除 ${item.name}`,
                                );
                              }}
                              className="ml-auto inline-flex size-7 items-center justify-center rounded-md border border-white/[0.10] text-white/32 transition hover:border-red-300/20 hover:bg-red-400/[0.07] hover:text-red-100/70 disabled:opacity-40"
                              aria-label={`删除 ${item.name}`}
                              title="删除自定义技能"
                            >
                              <Trash2 className="size-3" />
                            </button>
                          )}
                        </div>
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </div>

        <div className="flex shrink-0 items-center gap-2 border-t border-white/[0.06] px-4 py-2 text-[10px] text-white/28">
          <Store className="size-3" aria-hidden />
          {catalog?.total ?? 0} 个技能 · {catalog?.installed ?? 0} 已启用 ·{" "}
          {catalog?.custom ?? 0} 自定义
          <span className="ml-auto">Agent 会从已启用技能中自动匹配</span>
        </div>
      </DialogContent>
    </Dialog>
  );
}