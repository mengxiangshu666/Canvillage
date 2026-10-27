// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "@tanstack/react-router";
import { ExternalLink, Loader2, Search, X } from "lucide-react";
import { toast } from "sonner";

import { useStyles } from "@/lib/queries/styles";
import { stylePreviewUrl } from "@/lib/style-preview-url";
import { PROJECT_SECTION_ROUTES } from "@/components/layout/project-navigation-routes";
import { isImageGenNode, isVideoNode } from "@/features/canvas/domain/canvasNodes";
import { selectCanvasNodeById } from "@/features/canvas/application/canvasNodeIndex";
import { useCanvasStore } from "@/stores/canvasStore";
import type { Style } from "@/types/style";

type StyleTab = "all" | "preset" | "libtv" | "custom";

const STYLE_TABS: ReadonlyArray<{ id: StyleTab; label: string }> = [
  { id: "all", label: "全部" },
  { id: "preset", label: "系统预设" },
  { id: "libtv", label: "风格广场" },
  { id: "custom", label: "我的" },
];

function isLibtvStyle(style: Style): boolean {
  return style.id.startsWith("libtv_");
}

function styleSource(style: Style): Exclude<StyleTab, "all"> {
  if (isLibtvStyle(style)) return "libtv";
  if (style.type === "custom") return "custom";
  return "preset";
}

function sourceLabel(style: Style): string {
  const source = styleSource(style);
  if (source === "libtv") return "风格广场";
  if (source === "custom") return "自定义";
  return "预设";
}

function styleCategoryTag(style: Style): string {
  if (isLibtvStyle(style)) {
    return (
      (style.tags ?? []).find((tag) => tag !== style.name && tag !== "libtv") ?? ""
    );
  }
  return style.style_tag ?? "";
}

/**
 * 画布里的风格库：列出项目可用的预设/自定义风格，点一张就套到当前选中的
 * 图片或视频节点上（写 styleTemplateId，与节点参数面板的「风格」同一个字段）。
 */
export function StyleLibraryModal({
  open,
  project,
  onClose,
}: {
  open: boolean;
  project: string;
  onClose: () => void;
}) {
  const navigate = useNavigate();
  const stylesRes = useStyles(project);
  const styles = stylesRes.data?.data ?? [];
  const [query, setQuery] = useState("");
  const [tab, setTab] = useState<StyleTab>("all");
  const selectedNode = useCanvasStore((state) =>
    selectCanvasNodeById(state.nodes, state.selectedNodeId),
  );
  const canApply = Boolean(
    selectedNode && (isImageGenNode(selectedNode) || isVideoNode(selectedNode)),
  );

  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open, onClose]);

  const counts = useMemo(() => {
    const map: Record<StyleTab, number> = {
      all: styles.length,
      preset: 0,
      libtv: 0,
      custom: 0,
    };
    for (const style of styles) {
      map[styleSource(style)] += 1;
    }
    return map;
  }, [styles]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return styles.filter((style) => {
      if (tab !== "all" && styleSource(style) !== tab) return false;
      if (!q) return true;
      const haystack = [
        style.name,
        style.label ?? "",
        style.style_tag ?? "",
        style.family ?? "",
        style.era ?? "",
        style.style_family ?? "",
        ...(style.tags ?? []),
      ]
        .join(" ")
        .toLowerCase();
      return haystack.includes(q);
    });
  }, [query, styles, tab]);

  if (!open) return null;

  const applyStyle = (style: Style) => {
    if (!selectedNode || !canApply) {
      toast.info("先在画布上选中一个图片或视频节点，再套用风格");
      return;
    }
    useCanvasStore.getState().updateNodeData(selectedNode.id, {
      styleTemplateId: style.id,
    });
    toast.success(`已套用「${style.name}」到当前节点`);
  };

  return (
    <div
      className="fixed inset-0 z-[95] flex items-center justify-center bg-black/60 p-6 backdrop-blur-[2px]"
      role="dialog"
      aria-modal="true"
      aria-label="风格库"
      onClick={onClose}
    >
      <div
        className="flex h-[min(720px,84vh)] w-[min(1080px,94vw)] flex-col overflow-hidden rounded-[16px] border border-white/[0.12] bg-[#101114]/97 text-white shadow-[0_30px_90px_rgba(0,0,0,0.6)]"
        onClick={(event) => event.stopPropagation()}
      >
        <header className="flex shrink-0 items-center justify-between gap-4 border-b border-white/[0.07] px-6 py-4">
          <div>
            <h2 className="flex items-center gap-2 text-[17px] font-semibold">
              风格库
              <span className="rounded-full border border-white/10 bg-white/[0.04] px-2 py-0.5 text-[10px] font-normal text-white/45">
                {styles.length} 个风格 · {counts.libtv} 个来自风格广场
              </span>
            </h2>
            <p className="mt-0.5 text-[11.5px] text-white/40">
              {canApply
                ? `选中节点：${String(selectedNode?.data.displayName ?? selectedNode?.id)}`
                : "先在画布上选中一个图片或视频节点，再点风格套用"}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <div className="relative">
              <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-white/35" />
              <input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="搜索风格"
                aria-label="搜索风格"
                className="h-8 w-[180px] rounded-lg border border-white/10 bg-white/[0.04] pl-8 pr-2 text-[12px] text-white outline-none placeholder:text-white/30 focus:border-white/25"
              />
            </div>
            <button
              type="button"
              onClick={() => {
                onClose();
                void navigate({
                  to: PROJECT_SECTION_ROUTES.styles,
                  params: { project },
                });
              }}
              className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-white/12 px-3 text-[12px] text-white/70 transition-colors hover:bg-white/8 hover:text-white"
            >
              <ExternalLink className="size-3.5" />
              管理风格
            </button>
            <button
              type="button"
              aria-label="关闭"
              onClick={onClose}
              className="inline-flex size-8 items-center justify-center rounded-lg text-white/55 transition-colors hover:bg-white/10 hover:text-white"
            >
              <X className="size-4" />
            </button>
          </div>
        </header>

        <div className="ui-scrollbar flex shrink-0 items-center gap-1.5 overflow-x-auto border-b border-white/[0.06] px-6 py-2.5">
          {STYLE_TABS.map((item) => {
            const active = tab === item.id;
            return (
              <button
                key={item.id}
                type="button"
                onClick={() => setTab(item.id)}
                aria-pressed={active}
                className={`inline-flex shrink-0 items-center gap-1.5 rounded-full border px-3 py-1.5 text-[12px] transition-colors ${
                  active
                    ? "border-white/25 bg-white/[0.12] text-white"
                    : "border-transparent text-white/50 hover:bg-white/[0.06] hover:text-white/80"
                }`}
              >
                {item.label}
                <span className="text-[10px] text-white/35">{counts[item.id]}</span>
              </button>
            );
          })}
        </div>

        <div className="ui-scrollbar min-h-0 flex-1 overflow-y-auto px-6 py-5">
          {stylesRes.isLoading ? (
            <div className="flex items-center justify-center py-20 text-white/40">
              <Loader2 className="size-4 animate-spin" />
            </div>
          ) : filtered.length === 0 ? (
            <p className="py-20 text-center text-[12.5px] text-white/35">
              没有匹配的风格。去「管理风格」新建一个。
            </p>
          ) : (
            <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-4">
              {filtered.map((style) => (
                <button
                  key={style.id}
                  type="button"
                  onClick={() => applyStyle(style)}
                  className="group overflow-hidden rounded-[12px] border border-white/[0.08] bg-white/[0.03] text-left transition-colors hover:border-white/20 hover:bg-white/[0.05]"
                >
                  <div className="aspect-video overflow-hidden bg-gradient-to-br from-white/[0.06] to-white/[0.01]">
                    <img
                      src={stylePreviewUrl(style.id, project)}
                      alt=""
                      loading="lazy"
                      className="size-full object-cover transition-transform duration-300 group-hover:scale-[1.03]"
                      onError={(event) => {
                        event.currentTarget.style.display = "none";
                      }}
                    />
                  </div>
                  <div className="p-3">
                    <p className="truncate text-[13px] font-medium">
                      {style.name || style.label || style.id}
                    </p>
                    <p className="mt-0.5 flex items-center gap-1.5 text-[10.5px] text-white/35">
                      <span className="rounded-full border border-white/10 px-1.5 py-0.5">
                        {sourceLabel(style)}
                      </span>
                      {styleCategoryTag(style) ? (
                        <span className="truncate">{styleCategoryTag(style)}</span>
                      ) : null}
                    </p>
                  </div>
                </button>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
