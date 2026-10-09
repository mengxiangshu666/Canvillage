// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useMemo, useState } from "react";

import { CANVAS_NODE_TYPES } from "@/features/canvas/domain/canvasNodes";
import { useCanvasStore } from "@/stores/canvasStore";

type StoryboardFilter = "all" | "image" | "video" | "audio";

const IMAGE_TYPES = new Set<string>([
  CANVAS_NODE_TYPES.upload,
  CANVAS_NODE_TYPES.imageEdit,
  CANVAS_NODE_TYPES.imageGen,
  CANVAS_NODE_TYPES.exportImage,
  CANVAS_NODE_TYPES.storyboardGen,
]);

function mediaKind(type: string | undefined): Exclude<StoryboardFilter, "all"> | "other" {
  if (type && IMAGE_TYPES.has(type)) return "image";
  if (type === CANVAS_NODE_TYPES.video || type === CANVAS_NODE_TYPES.videoCompose || type === CANVAS_NODE_TYPES.videoStory) return "video";
  if (type === CANVAS_NODE_TYPES.audio) return "audio";
  return "other";
}

function firstText(data: Record<string, unknown>, keys: string[], fallback: string): string {
  for (const key of keys) {
    const value = data[key];
    if (typeof value === "string" && value.trim()) return value;
  }
  return fallback;
}

function previewUrl(data: Record<string, unknown>, kind: ReturnType<typeof mediaKind>): string | null {
  const keys = kind === "video"
    ? ["previewImageUrl", "posterUrl", "thumbnailUrl"]
    : ["imageUrl", "previewImageUrl", "referenceImageUrl"];
  for (const key of keys) {
    const value = data[key];
    if (typeof value === "string" && value.trim()) return value;
  }
  return null;
}

function statusLabel(data: Record<string, unknown>): string {
  if (data.isGenerating || data.isUploading) return "处理中";
  if (data.generationError || data.uploadError) return "需要处理";
  if (data.videoUrl || data.imageUrl || data.audioUrl) return "已有结果";
  return "待确认后生成";
}

function modelLabel(data: Record<string, unknown>): string {
  const explicit = firstText(
    data,
    ["modelLabel", "modelDisplayName", "targetModelLabel", "modelName"],
    "",
  );
  if (explicit) return explicit;
  const raw = firstText(data, ["model", "apiModel", "modelId", "model_id"], "");
  if (!raw) return "未选择模型";
  if (/^direct_video-/i.test(raw)) return "直连视频模型";
  if (/^direct[-_]/i.test(raw)) return "直连模型";
  return raw;
}

export function CanvasStoryboardView({ onLocateNode }: { onLocateNode: (nodeId: string) => void }) {
  const nodes = useCanvasStore((state) => state.nodes);
  const [filter, setFilter] = useState<StoryboardFilter>("all");
  const cards = useMemo(() => nodes.map((node) => {
    const data = (node.data ?? {}) as Record<string, unknown>;
    const kind = mediaKind(node.type);
    return {
      id: node.id,
      kind,
      title: firstText(data, ["displayName", "label", "sourceFileName"], node.type ?? "节点"),
      model: modelLabel(data),
      status: statusLabel(data),
      preview: previewUrl(data, kind),
      hasReference: Boolean(data.referenceImageUrl || data.startImageUrl || data.endImageUrl || data.referenceNodeIds),
    };
  }).filter((card) => card.kind !== "other" && (filter === "all" || card.kind === filter)), [filter, nodes]);

  const counts = useMemo(() => ({
    all: nodes.filter((node) => mediaKind(node.type) !== "other").length,
    image: nodes.filter((node) => mediaKind(node.type) === "image").length,
    video: nodes.filter((node) => mediaKind(node.type) === "video").length,
    audio: nodes.filter((node) => mediaKind(node.type) === "audio").length,
  }), [nodes]);

  return (
    <section className="absolute inset-0 overflow-auto bg-[#151515] px-6 pb-20 pt-20 text-white">
      <div className="mx-auto max-w-[1500px]">
        <div className="mb-5 flex items-center justify-between gap-4">
          <div><h2 className="text-lg font-semibold">故事板</h2><p className="mt-1 text-xs text-[#777]">按媒体类型审阅模型、状态和参考素材；这里只读，不会触发生成。</p></div>
          <div className="flex rounded-xl border border-[#303030] bg-[#202020] p-1">
            {(["all", "image", "video", "audio"] as const).map((key) => <button key={key} type="button" onClick={() => setFilter(key)} className={`rounded-lg px-3 py-1.5 text-xs transition-colors ${filter === key ? "bg-[#3a3a3a] text-white" : "text-[#777] hover:text-[#bbb]"}`}>{key === "all" ? "全部" : key === "image" ? "图片" : key === "video" ? "视频" : "音频"} {counts[key]}</button>)}
          </div>
        </div>
        {cards.length === 0 ? <div className="flex h-64 flex-col items-center justify-center rounded-2xl border border-dashed border-[#353535] text-center"><div className="text-sm font-medium text-[#999]">{counts.all === 0 ? "画布里还没有媒体节点" : "当前分类暂无媒体节点"}</div><div className="mt-1 text-xs leading-5 text-[#666]">{counts.all === 0 ? "回到工作流添加图片、视频或音频节点，它们会自动汇总到这里。" : "切换上方分类可以继续浏览其他媒体。"}</div>{filter !== "all" && <button type="button" onClick={() => setFilter("all")} className="mt-3 rounded-lg border border-white/10 bg-white/[0.04] px-3 py-1.5 text-xs text-[#aaa] hover:bg-white/[0.08] hover:text-white">查看全部</button>}</div> : <div className="grid grid-cols-[repeat(auto-fill,minmax(230px,1fr))] gap-4">
          {cards.map((card) => <button key={card.id} type="button" onClick={() => onLocateNode(card.id)} className="group overflow-hidden rounded-2xl border border-[#303030] bg-[#202020] text-left shadow-[0_8px_28px_rgba(0,0,0,.18)] transition hover:-translate-y-0.5 hover:border-[#555] hover:bg-[#242424]">
            <div className="relative aspect-video overflow-hidden bg-[#292929]">{card.preview ? <img src={card.preview} alt="" className="h-full w-full object-cover transition duration-300 group-hover:scale-[1.02]"/> : <div className="grid h-full place-items-center text-2xl text-[#555]">{card.kind === "image" ? "▧" : card.kind === "video" ? "▶" : "♪"}</div>}<span className="absolute left-2 top-2 rounded-md bg-black/65 px-2 py-1 text-[9px] text-[#ddd] backdrop-blur">{card.kind === "image" ? "图片" : card.kind === "video" ? "视频" : "音频"}</span></div>
            <div className="p-3"><div className="truncate text-[13px] font-medium text-[#eee]">{card.title}</div><div className="mt-2 flex flex-wrap gap-1.5"><span className="rounded-md bg-[#303030] px-2 py-1 text-[9px] text-[#aaa]">{card.status}</span><span className="max-w-full truncate rounded-md bg-[#303030] px-2 py-1 text-[9px] text-[#aaa]">{card.model}</span>{card.hasReference && <span className="rounded-md bg-[#303030] px-2 py-1 text-[9px] text-[#aaa]">参考素材</span>}</div></div>
          </button>)}
        </div>}
      </div>
    </section>
  );
}
