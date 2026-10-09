// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab

import {
  BadgeCheck,
  Check,
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
  type SkillStoreMaturity,
  type SkillStoreItem,
} from "@/api/skill-store";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { backendErrorToastMessage } from "@/lib/api-errors";
import { cn } from "@/lib/utils";

type StoreFilter = "all" | "installed" | "custom";

const FILTERS: readonly { id: StoreFilter; label: string }[] = [
  { id: "all", label: "全部技能" },
  { id: "installed", label: "已启用" },
  { id: "custom", label: "我的技能" },
];

const MATURITY_META: Record<SkillStoreMaturity, { label: string; className: string }> = {
  production_ready: {
    label: "可执行",
    className: "border-emerald-300/20 bg-emerald-400/[0.08] text-emerald-100/75",
  },
  workflow_ready: {
    label: "可编排",
    className: "border-cyan-300/20 bg-cyan-400/[0.08] text-cyan-100/75",
  },
  reference_only: {
    label: "待完善",
    className: "border-amber-300/20 bg-amber-400/[0.08] text-amber-100/75",
  },
};

function skillMaturityMeta(item: SkillStoreItem) {
  return MATURITY_META[item.contract?.maturity ?? "reference_only"];
}

function skillAdmissionMeta(item: SkillStoreItem) {
  const admission = item.admission;
  if (!admission || admission.status === "admitted") {
    return { label: "已通过", className: "border-emerald-300/15 bg-emerald-400/[0.05] text-emerald-100/60" };
  }
  if (admission.status === "review_required") {
    return { label: "需复核", className: "border-amber-300/15 bg-amber-400/[0.05] text-amber-100/65" };
  }
  return { label: "已阻断", className: "border-red-300/15 bg-red-400/[0.05] text-red-100/65" };
}

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
  const [filter, setFilter] = useState<StoreFilter>("all");
  const [category, setCategory] = useState("all");

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

  const visibleItems = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase();
    return (catalog?.items ?? []).filter((item) => {
      if (filter === "installed" && !item.installed) return false;
      if (filter === "custom" && item.source !== "custom") return false;
      if (category !== "all" && item.category !== category) return false;
      if (!needle) return true;
      return [item.name, item.description, item.skill_key, item.category, ...item.tags]
        .some((value) => value.toLocaleLowerCase().includes(needle));
    });
  }, [catalog?.items, category, filter, query]);

  const runMutation = useCallback(async (
    skillId: string,
    mutation: () => Promise<unknown>,
    success: string,
  ) => {
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
  }, [loadCatalog, t]);

  const handleUpload = useCallback(async (file: File | null) => {
    if (!file) return;
    setBusyId("__upload__");
    try {
      const result = await importSkillStoreFile(file);
      toast.success(`已导入 ${result.imported} 个技能`);
      setFilter("custom");
      await loadCatalog();
    } catch (error) {
      toast.error(backendErrorToastMessage(error, t));
    } finally {
      setBusyId(null);
      if (uploadRef.current) uploadRef.current.value = "";
    }
  }, [loadCatalog, t]);

  const mounted = useCallback(
    (item: SkillStoreItem) => mountedSkillIds.includes(skillStoreAgentId(item.id)),
    [mountedSkillIds],
  );

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        showCloseButton={false}
        data-agent-dialog="v4"
        className="village-agent-dialog-v4 village-agent-dialog-v4--skills skill-store-dialog flex h-[min(88vh,820px)] w-[min(94vw,1120px)] max-w-none flex-col gap-0 overflow-hidden rounded-[22px] border border-white/[0.10] bg-[#101012]/98 p-0 text-white shadow-[0_30px_100px_rgba(0,0,0,0.62)] backdrop-blur-2xl sm:max-w-none"
      >
        <header className="flex shrink-0 items-start justify-between gap-5 border-b border-white/[0.07] px-5 py-4">
          <div className="flex min-w-0 items-start gap-3">
            <span className="flex size-10 shrink-0 items-center justify-center rounded-[13px] border border-violet-300/20 bg-violet-400/[0.11] text-violet-100">
              <Store className="size-5" aria-hidden />
            </span>
            <div className="min-w-0">
              <DialogTitle className="text-base font-semibold tracking-[-0.01em] text-white/94">技能商店</DialogTitle>
              <p className="mt-1 text-[12px] leading-5 text-white/42">
                Agent 会从已启用技能中自动匹配；手动指定只用于强制优先。
              </p>
              <div className="mt-2 flex flex-wrap items-center gap-1.5 text-[10px] text-white/45">
                <span className="rounded-full border border-white/[0.08] bg-white/[0.035] px-2 py-0.5">{catalog?.total ?? 0} 个技能</span>
                <span className="rounded-full border border-cyan-300/15 bg-cyan-400/[0.06] px-2 py-0.5 text-cyan-100/70">{catalog?.installed ?? 0} 已启用</span>
                <span className="rounded-full border border-white/[0.08] bg-white/[0.035] px-2 py-0.5">{catalog?.custom ?? 0} 自定义</span>
              </div>
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <input
              ref={uploadRef}
              type="file"
              className="hidden"
              accept=".md,.markdown,.json,.zip"
              onChange={(event) => void handleUpload(event.target.files?.[0] ?? null)}
            />
            <button
              type="button"
              onClick={() => uploadRef.current?.click()}
              disabled={busyId !== null}
              className="inline-flex h-9 items-center gap-2 rounded-full border border-white/[0.10] bg-white/[0.045] px-3.5 text-[12px] font-medium text-white/72 transition hover:border-white/[0.20] hover:bg-white/[0.08] hover:text-white disabled:opacity-45"
            >
              {busyId === "__upload__" ? <Loader2 className="size-4 animate-spin" /> : <Upload className="size-4" />}
              上传技能
            </button>
            <button
              type="button"
              onClick={() => onOpenChange(false)}
              className="flex size-9 items-center justify-center rounded-full text-white/42 transition hover:bg-white/[0.07] hover:text-white/80"
              aria-label="关闭技能商店"
            >
              <X className="size-4" />
            </button>
          </div>
        </header>

        <div className="flex min-h-0 flex-1">
          <aside className="hidden w-44 shrink-0 border-r border-white/[0.06] p-3 md:block">
            <nav className="space-y-1" aria-label="技能商店分类">
              {FILTERS.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  onClick={() => setFilter(item.id)}
                  className={cn(
                    "flex h-9 w-full items-center justify-between rounded-[10px] px-3 text-left text-[12px] transition",
                    filter === item.id
                      ? "bg-white/[0.09] font-medium text-white"
                      : "text-white/48 hover:bg-white/[0.045] hover:text-white/75",
                  )}
                >
                  <span>{item.label}</span>
                  {item.id === "installed" && <span className="text-[10px] text-white/32">{catalog?.installed ?? 0}</span>}
                  {item.id === "custom" && <span className="text-[10px] text-white/32">{catalog?.custom ?? 0}</span>}
                </button>
              ))}
            </nav>
            <div className="mt-4 border-t border-white/[0.06] pt-3">
              <p className="px-3 text-[10px] font-medium uppercase tracking-[0.12em] text-white/25">创作分类</p>
              <div className="mt-1.5 max-h-[42vh] space-y-0.5 overflow-y-auto">
                <button
                  type="button"
                  onClick={() => setCategory("all")}
                  className={cn("w-full rounded-lg px-3 py-1.5 text-left text-[11px]", category === "all" ? "text-white/90" : "text-white/38 hover:text-white/65")}
                >
                  全部分类
                </button>
                {(catalog?.categories ?? []).map((item) => (
                  <button
                    key={item}
                    type="button"
                    onClick={() => setCategory(item)}
                    className={cn("w-full rounded-lg px-3 py-1.5 text-left text-[11px]", category === item ? "bg-white/[0.055] text-white/90" : "text-white/38 hover:text-white/65")}
                  >
                    {item}
                  </button>
                ))}
              </div>
            </div>
          </aside>

          <main className="flex min-w-0 flex-1 flex-col">
            <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-white/[0.055] px-4 py-3">
              <label className="flex h-9 min-w-[220px] flex-1 items-center gap-2 rounded-full border border-white/[0.08] bg-white/[0.035] px-3 text-white/45 focus-within:border-white/[0.18] focus-within:bg-white/[0.055]">
                <Search className="size-3.5 shrink-0" aria-hidden />
                <span className="sr-only">搜索技能</span>
                <input
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  className="min-w-0 flex-1 bg-transparent text-[12px] text-white/86 outline-none placeholder:text-white/28"
                  placeholder="搜索技能、用途、分类"
                  aria-label="搜索技能商店"
                />
              </label>
              <div className="flex gap-1 md:hidden">
                {FILTERS.map((item) => (
                  <button
                    key={item.id}
                    type="button"
                    onClick={() => setFilter(item.id)}
                    className={cn("rounded-full px-2.5 py-1.5 text-[10px]", filter === item.id ? "bg-white/[0.10] text-white" : "text-white/42")}
                  >
                    {item.label}
                  </button>
                ))}
              </div>
              <span className="text-[11px] text-white/30">{visibleItems.length} 项</span>
            </div>

            <div className="min-h-0 flex-1 overflow-y-auto p-4 [scrollbar-width:thin]">
              {loading && !catalog ? (
                <div className="flex h-full min-h-64 items-center justify-center gap-2 text-sm text-white/45">
                  <Loader2 className="size-4 animate-spin" />
                  正在读取本地技能目录
                </div>
              ) : visibleItems.length === 0 ? (
                <div className="flex h-full min-h-64 flex-col items-center justify-center text-center">
                  <PackageOpen className="size-8 text-white/20" />
                  <p className="mt-3 text-sm text-white/65">没有匹配的技能</p>
                  <p className="mt-1 text-[11px] text-white/32">换个关键词，或者上传自己的 SKILL.md。</p>
                </div>
              ) : (
                <div className="grid grid-cols-1 gap-3 lg:grid-cols-2 xl:grid-cols-3">
                  {visibleItems.map((item) => {
                    const isMounted = mounted(item);
                    const isBusy = busyId === item.id;
                    return (
                      <article
                        key={item.id}
                        className={cn(
                          "group flex min-h-[210px] flex-col rounded-[16px] border bg-white/[0.022] p-4 transition",
                          item.installed ? "border-cyan-300/[0.18]" : "border-white/[0.075] hover:border-white/[0.15] hover:bg-white/[0.035]",
                        )}
                      >
                        {item.cover_image && (
                          <div className="mb-3 aspect-[16/8] overflow-hidden rounded-[12px] border border-white/[0.07] bg-black/20">
                            <img
                              src={item.cover_image}
                              alt={`${item.name} 技能示意图`}
                              loading="lazy"
                              className="h-full w-full object-cover transition duration-300 group-hover:scale-[1.02]"
                              onError={(event) => {
                                event.currentTarget.hidden = true;
                              }}
                            />
                          </div>
                        )}
                        <div className="flex items-start justify-between gap-3">
                          <span className="flex size-9 shrink-0 items-center justify-center rounded-[12px] border border-white/[0.08] bg-gradient-to-br from-violet-400/20 to-cyan-400/[0.06] text-white/80">
                            {item.source === "custom" ? <Sparkles className="size-4" /> : <Store className="size-4" />}
                          </span>
                          <div className="flex items-center gap-1.5 text-[9px]">
                            <span className="rounded-full border border-white/[0.07] px-1.5 py-0.5 text-white/35">{item.source_label}</span>
                            <span
                              className={cn("rounded-full border px-1.5 py-0.5", skillMaturityMeta(item).className)}
                              title={item.contract?.readiness_issues?.join("；") || "输入、流程、输出和验收条件已具备"}
                            >
                              {skillMaturityMeta(item).label} {item.contract?.readiness_score ?? 0}
                            </span>
                            <span
                              className={cn("rounded-full border px-1.5 py-0.5", skillAdmissionMeta(item).className)}
                              title={item.admission?.issues?.map((issue) => issue.message).join("；") || "技能准入检查通过"}
                            >
                              {skillAdmissionMeta(item).label}
                            </span>
                            {item.installed && <span className="inline-flex items-center gap-1 rounded-full border border-cyan-300/15 bg-cyan-400/[0.07] px-1.5 py-0.5 text-cyan-100/65"><Check className="size-2.5" />已启用</span>}
                          </div>
                        </div>
                        <h3 className="mt-3 line-clamp-1 text-[13px] font-semibold text-white/88" title={item.name}>{item.name}</h3>
                        <p className="mt-1 line-clamp-3 min-h-[54px] text-[11px] leading-[18px] text-white/42">{item.description}</p>
                        {item.contract?.output_contract && (
                          <p className="mt-2 line-clamp-1 text-[10px] leading-4 text-white/32" title={item.contract.output_contract}>
                            输出：{item.contract.output_contract}
                          </p>
                        )}
                        <div className="mt-3 flex flex-wrap gap-1">
                          <span className="rounded-full bg-white/[0.045] px-2 py-0.5 text-[9px] text-white/36">{item.category}</span>
                          {item.tags.filter((tag) => tag !== item.category).slice(0, 2).map((tag) => (
                            <span key={tag} className="rounded-full bg-white/[0.03] px-2 py-0.5 text-[9px] text-white/28">{tag}</span>
                          ))}
                        </div>
                        <div className="mt-auto flex items-center gap-2 pt-4">
                          {!item.installed ? (
                            <button
                              type="button"
                              disabled={isBusy || item.admission?.can_install === false}
                              onClick={() => void runMutation(item.id, () => installSkillStoreItem(item.id), `已启用 ${item.name}`)}
                              title={item.admission?.can_install === false ? "准入检查未通过，修复来源、执行合同或引用后才能启用" : "启用技能"}
                              className="inline-flex h-8 flex-1 items-center justify-center gap-1.5 rounded-full bg-white text-[11px] font-semibold text-black transition hover:bg-white/88 disabled:cursor-not-allowed disabled:opacity-45"
                            >
                              {isBusy ? <Loader2 className="size-3.5 animate-spin" /> : <Power className="size-3.5" />}
                              {item.admission?.can_install === false ? "需复核" : "启用"}
                            </button>
                          ) : (
                            <>
                              <button
                                type="button"
                                disabled={isBusy}
                                onClick={() => onToggleMount(skillStoreAgentId(item.id))}
                                className={cn(
                                  "inline-flex h-8 flex-1 items-center justify-center gap-1.5 rounded-full border text-[11px] font-semibold transition disabled:opacity-45",
                                  isMounted
                                    ? "border-cyan-300/25 bg-cyan-400/[0.10] text-cyan-50"
                                    : "border-white/[0.12] bg-white/[0.055] text-white/75 hover:bg-white/[0.09]",
                                )}
                              >
                                {isMounted ? <BadgeCheck className="size-3.5" /> : <Sparkles className="size-3.5" />}
                                {isMounted ? "已指定" : "手动指定"}
                              </button>
                              <button
                                type="button"
                                disabled={isBusy}
                                onClick={() => {
                                  if (isMounted) onToggleMount(skillStoreAgentId(item.id));
                                  void runMutation(item.id, () => uninstallSkillStoreItem(item.id), `已停用 ${item.name}`);
                                }}
                                className="flex size-8 shrink-0 items-center justify-center rounded-full border border-white/[0.09] text-white/38 transition hover:bg-white/[0.06] hover:text-white/70 disabled:opacity-40"
                                aria-label={`停用 ${item.name}`}
                                title="停用"
                              >
                                {isBusy ? <Loader2 className="size-3.5 animate-spin" /> : <PowerOff className="size-3.5" />}
                              </button>
                            </>
                          )}
                          {item.removable && (
                            <button
                              type="button"
                              disabled={isBusy}
                              onClick={() => {
                                if (isMounted) onToggleMount(skillStoreAgentId(item.id));
                                void runMutation(item.id, () => deleteSkillStoreItem(item.id), `已删除 ${item.name}`);
                              }}
                              className="flex size-8 shrink-0 items-center justify-center rounded-full border border-white/[0.09] text-white/30 transition hover:border-red-300/20 hover:bg-red-400/[0.07] hover:text-red-100/70 disabled:opacity-40"
                              aria-label={`删除 ${item.name}`}
                              title="删除自定义技能"
                            >
                              <Trash2 className="size-3.5" />
                            </button>
                          )}
                        </div>
                      </article>
                    );
                  })}
                </div>
              )}
            </div>
          </main>
        </div>
      </DialogContent>
    </Dialog>
  );
}
