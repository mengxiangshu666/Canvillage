import { Braces, ChevronDown, Copy, Download, File, Image, ListTree, Maximize2, MoreHorizontal, Pin, PinOff, Play, RefreshCw, Search, Volume2, X } from "lucide-react";
import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import { SpecRenderer, SpecRendererProvider, VideoDetailModal } from "@/lib/spec-render";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuRadioGroup, DropdownMenuRadioItem, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Dialog, DialogClose, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { useSuperChat } from "@/features/superchat/use-superchat";
import { cn } from "@/lib/utils";
import { resolveMediaUrl } from "@/lib/media-url";
import { canvasOnlyProduct } from "@/lib/product-mode";
import { extractStructuredBlocks, isUiSpec, looksLikeStructuredRenderText, type StructuredBlock, type UiSpec } from "@/features/superchat/spec-extract";
import { knowledgeReceiptFromMessage, knowledgeReceiptUsageLabel } from "@/features/superchat/knowledge-receipt";
import { calculateTimelineContextDelta } from "@/features/superchat/timeline-scroll";
import { MessageText, HighlightedCompletionText, HighlightedErrorText, DotsIndicator, ChatAvatarFrame, isAssistantCompletionNotice, isAssistantErrorReply } from "./superchat-message-rendering";
import type { ChatAttachment, ChatMessage } from "@/features/superchat/types";

type SuperChatPanelVariant = "default" | "freezone";
function parseSpecMediaUrl(src: string): string | null {
  return src.startsWith("st-unresolved:") ? src : null;
}

function resolveSpecMediaUrl(src: string): Promise<string> {
  if (src.startsWith("st-unresolved:")) return Promise.resolve(src);
  return Promise.resolve(resolveMediaUrl(src) ?? src);
}
export type SpecMediaDetailSection = {
  title: string;
  body?: string;
  items?: string[];
};

export type SpecMediaDetail = {
  kind: "image" | "video";
  src: string;
  poster?: string;
  title?: string;
  description?: string;
  tags?: Array<{ label: string; color?: string }>;
  sections?: SpecMediaDetailSection[];
  candidates?: Array<{ id?: string; src: string; label?: string }>;
};


export function isToolMessage(message: ChatMessage): boolean {
  if (message.role === "tool") return true;
  if (!message.raw || typeof message.raw !== "object") return false;
  const raw = message.raw as Record<string, unknown>;
  const role = raw.role;
  const type = raw.type;
  return (
    role === "trace"
    || role === "tool"
    || role === "tool_result"
    || role === "toolResult"
    || type === "tool.result"
    || type === "tool_update"
  );
}

function isHistoricalToolMessage(message: ChatMessage): boolean {
  const raw = message.raw && typeof message.raw === "object"
    ? (message.raw as Record<string, unknown>)
    : {};
  return raw.role === "trace";
}

function renderJsonScalar(value: unknown): string {
  if (value === null) return "null";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(value);
}

function triggerDownload(url: string) {
  const link = document.createElement("a");
  link.href = url;
  link.download = "";
  link.rel = "noopener noreferrer";
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
}


type KeyframeVideoPreviewItem = {
  id: string;
  title: string;
  description?: string;
  poster?: string;
  videoSrc?: string;
  status?: string;
  progress?: number;
};

type UnifiedMediaKind = "image" | "video" | "audio";

type UnifiedMediaItem = {
  id: string;
  kind: UnifiedMediaKind;
  title: string;
  description?: string;
  src: string;
  poster?: string;
};

function elementProps(element: unknown): Record<string, unknown> {
  if (!element || typeof element !== "object") return {};
  const props = (element as Record<string, unknown>).props;
  return props && typeof props === "object" && !Array.isArray(props)
    ? props as Record<string, unknown>
    : {};
}

function textProp(...values: unknown[]): string {
  for (const value of values) {
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return "";
}

function numberProp(value: unknown): number | undefined {
  const parsed = typeof value === "number"
    ? value
    : typeof value === "string"
      ? Number.parseFloat(value)
      : Number.NaN;
  return Number.isFinite(parsed) ? Math.min(Math.max(parsed, 0), 100) : undefined;
}

function specElementOrder(spec: UiSpec): string[] {
  const root = spec.elements[spec.root];
  const children = root && typeof root === "object"
    ? (root as Record<string, unknown>).children
    : undefined;
  const ordered = Array.isArray(children)
    ? children.filter((child): child is string => typeof child === "string")
    : [];
  const orderedSet = new Set(ordered);
  return [
    ...ordered,
    ...Object.keys(spec.elements).filter((key) => key !== spec.root && !orderedSet.has(key)),
  ];
}

function extractUnifiedMediaItems(spec: UiSpec): UnifiedMediaItem[] {
  const mediaSpecTypes = new Set([
    "character_showcase",
    "sketch_gallery",
    "keyframe_video",
    "audio_list",
    "media_bundle",
  ]);
  if (spec.type && !mediaSpecTypes.has(spec.type)) return [];

  const items: UnifiedMediaItem[] = [];
  for (const id of specElementOrder(spec)) {
    const element = spec.elements[id];
    if (!element || typeof element !== "object") continue;
    const record = element as Record<string, unknown>;
    const props = elementProps(record);
    const type = typeof record.type === "string" ? record.type : "";
    const src = textProp(props.src, props.url);
    if (!src) continue;

    if (type === "Image") {
      items.push({
        id,
        kind: "image",
        title: textProp(props.overlayTitle, props.title, props.caption, props.alt, id),
        description: textProp(props.overlayDescription, props.description),
        src,
        poster: textProp(props.poster, props.thumbnail),
      });
      continue;
    }

    if (type === "Video") {
      items.push({
        id,
        kind: "video",
        title: textProp(props.overlayTitle, props.title, props.caption, props.alt, id),
        description: textProp(props.overlayDescription, props.description),
        src,
        poster: textProp(props.poster, props.thumbnail),
      });
      continue;
    }

    if (type === "Audio") {
      items.push({
        id,
        kind: "audio",
        title: textProp(props.overlayTitle, props.title, props.caption, props.alt, id),
        description: textProp(props.overlayDescription, props.description),
        src,
        poster: textProp(props.poster, props.thumbnail),
      });
    }
  }
  return items;
}

function extractKeyframeVideoPreviewItems(spec: UiSpec): KeyframeVideoPreviewItem[] {
  return Object.entries(spec.elements)
    .flatMap(([id, element]) => {
      if (!element || typeof element !== "object") return [];
      const record = element as Record<string, unknown>;
      if (record.type !== "Video") return [];

      const props = elementProps(record);
      const videoSrc = textProp(props.src, props.url);
      if (!videoSrc) return [];

      return [{
        id,
        title: textProp(props.overlayTitle, props.caption, props.alt, id),
        description: textProp(props.overlayDescription, props.description),
        poster: textProp(props.poster),
        videoSrc,
      }];
    });
}

function useResolvedSpecUrl(src?: string): string | undefined {
  const [resolved, setResolved] = useState(src);

  useEffect(() => {
    let cancelled = false;
    if (!src) {
      setResolved(undefined);
      return undefined;
    }

    resolveSpecMediaUrl(src).then((url) => {
      if (!cancelled) setResolved(url);
    });

    return () => {
      cancelled = true;
    };
  }, [src]);

  return resolved;
}

function useVideoFirstFrame(src?: string, explicitPoster?: string): string | undefined {
  const [poster, setPoster] = useState(explicitPoster);

  useEffect(() => {
    if (explicitPoster) {
      setPoster(explicitPoster);
      return undefined;
    }

    setPoster(undefined);
    if (!src) return undefined;

    let cancelled = false;
    const video = document.createElement("video");
    video.muted = true;
    video.playsInline = true;
    video.preload = "auto";
    video.src = src;

    const capture = () => {
      if (cancelled || video.videoWidth <= 0 || video.videoHeight <= 0) return;
      try {
        const canvas = document.createElement("canvas");
        canvas.width = video.videoWidth;
        canvas.height = video.videoHeight;
        const ctx = canvas.getContext("2d");
        if (!ctx) return;
        ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
        setPoster(canvas.toDataURL("image/jpeg", 0.82));
      } catch {
        setPoster(undefined);
      }
    };

    const seekToFirstFrame = () => {
      if (cancelled) return;
      const target = Number.isFinite(video.duration) && video.duration > 0
        ? Math.min(0.12, Math.max(video.duration / 100, 0.01))
        : 0.01;
      try {
        video.currentTime = target;
      } catch {
        capture();
      }
    };

    video.addEventListener("loadeddata", seekToFirstFrame, { once: true });
    video.addEventListener("seeked", capture, { once: true });
    video.load();

    return () => {
      cancelled = true;
      video.removeAttribute("src");
      video.load();
    };
  }, [src, explicitPoster]);

  return poster;
}

function KeyframeVideoPreviewCard({ item }: { item: KeyframeVideoPreviewItem }) {
  const [open, setOpen] = useState(false);
  const poster = useResolvedSpecUrl(item.poster);
  const videoSrc = useResolvedSpecUrl(item.videoSrc);
  const previewPoster = useVideoFirstFrame(videoSrc, poster);
  const playable = Boolean(videoSrc);
  const cardStyle = { width: "158px", height: "211px" };

  return (
    <>
      <div style={{ perspective: 800, ...cardStyle }} className="shrink-0">
        <div className="relative h-full w-full overflow-hidden rounded-2xl bg-white/5 p-[1.5px]">
          <div className="relative z-10 h-full w-full overflow-hidden rounded-[14px] bg-zinc-950">
            <button
              type="button"
              className={cn("relative h-full w-full cursor-pointer text-left", !playable && "cursor-default")}
              onClick={() => {
                if (playable) setOpen(true);
              }}
              aria-label={item.title}
            >
              {previewPoster ? (
                <img
                  className="block h-full w-full select-none object-cover"
                  src={previewPoster}
                  alt={item.title}
                  loading="lazy"
                  draggable={false}
                />
              ) : (
                <span className="st-keyframe-video-placeholder block h-full w-full" />
              )}
              {playable && (
                <span className="st-keyframe-video-play">
                  <Play className="size-5 fill-white text-white" />
                </span>
              )}
              <span className="absolute inset-x-0 bottom-0 z-10 flex flex-col gap-1 bg-gradient-to-t from-black/85 via-black/35 to-transparent px-3 pb-3 pt-8 text-white">
                <span className="truncate text-sm font-semibold">{item.title}</span>
                {item.description && (
                  <span className="line-clamp-2 text-[11px] leading-4 text-white/80">
                    {item.description}
                  </span>
                )}
                {item.status && (
                  <span className="st-keyframe-video-status">{item.status}</span>
                )}
                {item.progress !== undefined && (
                  <span className="st-keyframe-video-progress">
                    <span style={{ width: `${item.progress}%` }} />
                  </span>
                )}
              </span>
            </button>
          </div>
        </div>
      </div>
      {playable && (
        <VideoDetailModal
          src={videoSrc}
          poster={poster}
          title={item.title}
          description={item.description}
          open={open}
          setOpen={setOpen}
        />
      )}
    </>
  );
}

function UnifiedMediaCard({
  item,
  onOpenMedia,
}: {
  item: UnifiedMediaItem;
  onOpenMedia?: (detail: SpecMediaDetail) => void;
}) {
  const [videoOpen, setVideoOpen] = useState(false);
  const src = useResolvedSpecUrl(item.src);
  const poster = useResolvedSpecUrl(item.poster);
  const previewPoster = useVideoFirstFrame(item.kind === "video" ? src : undefined, poster);
  const imageSrc = item.kind === "video" ? previewPoster : item.kind === "image" ? src : poster;
  const playable = Boolean(src);

  const openPreview = () => {
    if (!src) return;
    if (item.kind === "video") {
      setVideoOpen(true);
      return;
    }
    if (item.kind === "image") {
      onOpenMedia?.({
        kind: "image",
        src,
        poster,
        title: item.title,
        description: item.description,
      });
    }
  };

  return (
    <>
      <div className="st-unified-media-card">
        <div className="relative h-full w-full overflow-hidden rounded-2xl bg-white/5 p-[1.5px]">
          <div className="relative z-10 h-full w-full overflow-hidden rounded-[14px] bg-zinc-950">
            {item.kind === "audio" ? (
              <div className="relative flex h-full w-full flex-col justify-center gap-4 px-3 pb-16 pt-5">
                <span className="mx-auto flex size-14 items-center justify-center rounded-full border border-white/15 bg-white/10 text-white shadow-[0_12px_30px_rgba(0,0,0,0.3)]">
                  <Volume2 className="size-7" />
                </span>
                {src && (
                  <audio
                    className="st-unified-media-audio w-full"
                    src={src}
                    controls
                    preload="metadata"
                  />
                )}
                {!src && <span className="st-keyframe-video-placeholder absolute inset-0" />}
                <span className="pointer-events-none absolute inset-x-0 bottom-0 z-10 flex flex-col gap-1 bg-gradient-to-t from-black/85 via-black/35 to-transparent px-3 pb-3 pt-8 text-white">
                  <span className="truncate text-sm font-semibold">{item.title}</span>
                  {item.description && (
                    <span className="line-clamp-2 text-[11px] leading-4 text-white/80">
                      {item.description}
                    </span>
                  )}
                </span>
              </div>
            ) : (
              <button
                type="button"
                className={cn("relative h-full w-full text-left", playable ? "cursor-pointer" : "cursor-default")}
                onClick={openPreview}
                aria-label={item.title}
              >
                {imageSrc ? (
                  <img
                    className="block h-full w-full select-none object-cover"
                    src={imageSrc}
                    alt={item.title}
                    loading="lazy"
                    draggable={false}
                  />
                ) : (
                  <span className="st-keyframe-video-placeholder block h-full w-full" />
                )}
                {item.kind === "video" && playable && (
                  <span className="st-keyframe-video-play">
                    <Play className="size-5 fill-white text-white" />
                  </span>
                )}
                <span className="absolute inset-x-0 bottom-0 z-10 flex flex-col gap-1 bg-gradient-to-t from-black/85 via-black/35 to-transparent px-3 pb-3 pt-8 text-white">
                  <span className="truncate text-sm font-semibold">{item.title}</span>
                  {item.description && (
                    <span className="line-clamp-2 text-[11px] leading-4 text-white/80">
                      {item.description}
                    </span>
                  )}
                </span>
              </button>
            )}
          </div>
        </div>
      </div>
      {item.kind === "video" && src && (
        <VideoDetailModal
          src={src}
          poster={poster}
          title={item.title}
          description={item.description}
          open={videoOpen}
          setOpen={setVideoOpen}
        />
      )}
    </>
  );
}

function UnifiedMediaGrid({
  spec,
  onOpenMedia,
}: {
  spec: UiSpec;
  onOpenMedia?: (detail: SpecMediaDetail) => void;
}) {
  const items = extractUnifiedMediaItems(spec);
  if (items.length === 0) return null;

  return (
    <div className="st-unified-media-grid">
      {items.map((item) => (
        <UnifiedMediaCard key={item.id} item={item} onOpenMedia={onOpenMedia} />
      ))}
    </div>
  );
}

function extractPendingKeyframeVideoItem(spec: UiSpec): KeyframeVideoPreviewItem | null {
  const root = spec.elements[spec.root];
  const rootProps = elementProps(root);
  const title = textProp(rootProps.title, rootProps.description, spec.type);
  const description = textProp(rootProps.description);
  let status = "";
  let progress: number | undefined;

  for (const element of Object.values(spec.elements)) {
    if (!element || typeof element !== "object") continue;
    const record = element as Record<string, unknown>;
    const props = elementProps(record);
    if (record.type === "Badge" && !status) {
      status = textProp(props.label, props.text);
    }
    if (record.type === "Progress" && progress === undefined) {
      progress = numberProp(props.value);
    }
  }

  if (!title && !status && progress === undefined) return null;

  return {
    id: "pending",
    title,
    description,
    status,
    progress,
  };
}

function KeyframeVideoPreview({ spec }: { spec: UiSpec }) {
  const videoItems = extractKeyframeVideoPreviewItems(spec);
  const pendingItem = videoItems.length === 0 ? extractPendingKeyframeVideoItem(spec) : null;
  const items = videoItems.length > 0 ? videoItems : pendingItem ? [pendingItem] : [];

  if (items.length === 0) {
    return <SpecRenderer spec={spec} />;
  }

  return (
    <div className="st-keyframe-video-preview">
      <div className="st-keyframe-video-grid">
        {items.map((item) => (
          <KeyframeVideoPreviewCard key={item.id} item={item} />
        ))}
      </div>
    </div>
  );
}

function UiSpecRenderer({
  spec,
  onOpenMedia,
}: {
  spec: UiSpec;
  onOpenMedia?: (detail: SpecMediaDetail) => void;
}) {
  const mediaItems = extractUnifiedMediaItems(spec);
  // Keep this wrapper aligned with SuperChat so media specs inherit the same
  // renderer sizing and do not get an extra local card frame.
  return (
    <div
      className="chat-spec-renderer w-full min-w-0 max-w-full overflow-visible [contain:layout]"
      data-spec-type={spec.type ?? "auto"}
    >
      <SpecRendererProvider
        resolveMediaUrl={resolveSpecMediaUrl}
        parseMediaUrl={parseSpecMediaUrl}
        loadingVideoUrl="/video/loading.mp4"
      >
        {mediaItems.length > 0 ? (
          <UnifiedMediaGrid spec={spec} onOpenMedia={onOpenMedia} />
        ) : spec.type === "keyframe_video" ? (
          <KeyframeVideoPreview spec={spec} />
        ) : (
          <SpecRenderer spec={spec} />
        )}
      </SpecRendererProvider>
    </div>
  );
}

function JsonNode({
  name,
  value,
  depth = 0,
}: {
  name?: string;
  value: unknown;
  depth?: number;
}) {
  if (Array.isArray(value)) {
    return (
      <div className={cn("space-y-1", depth > 0 && "pl-3")}>
        {name && <div className="text-xs font-medium text-muted-foreground">{name}</div>}
        {value.map((item, index) => (
          <JsonNode key={index} name={`#${index + 1}`} value={item} depth={depth + 1} />
        ))}
      </div>
    );
  }

  if (value && typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>);
    const objectTitle =
      typeof (value as Record<string, unknown>).title === "string"
        ? String((value as Record<string, unknown>).title)
        : name;
    return (
      <div className={cn("rounded-md border border-border/70 bg-background/45 p-2", depth > 0 && "ml-2")}>
        {objectTitle && <div className="mb-1 text-xs font-semibold text-foreground">{objectTitle}</div>}
        <div className="space-y-1">
          {entries.map(([key, item]) => (
            <JsonNode key={key} name={key} value={item} depth={depth + 1} />
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className={cn("grid grid-cols-[88px_minmax(0,1fr)] gap-2 text-xs", depth > 0 && "pl-2")}>
      {name && <span className="truncate text-muted-foreground">{name}</span>}
      <span className="min-w-0 break-words font-mono text-foreground/90">{renderJsonScalar(value)}</span>
    </div>
  );
}

function StructuredRenderer({
  blocks,
  onOpenMedia,
}: {
  blocks: StructuredBlock[];
  onOpenMedia?: (detail: SpecMediaDetail) => void;
}) {
  if (blocks.length === 0) return null;
  return (
    <div className="mt-3 flex w-full min-w-0 max-w-full flex-col items-stretch gap-3">
      {blocks.map((block) => {
        if (isUiSpec(block.value)) {
          return (
            <section
              key={block.id}
              className="w-full min-w-0 max-w-full flex-none overflow-visible [contain:layout]"
            >
              <UiSpecRenderer spec={block.value} onOpenMedia={onOpenMedia} />
            </section>
          );
        }
        return (
          <section
            key={block.id}
            className="w-full min-w-0 max-w-full rounded-lg border border-border/70 bg-background/35 p-2 [contain:layout]"
          >
            <div className="mb-2 flex items-center justify-between gap-2">
              <Badge variant="outline" className="h-5 rounded-md px-1.5 text-[10px] uppercase">
                {block.label}
              </Badge>
              <Button
                variant="ghost"
                size="icon-xs"
                onClick={() => navigator.clipboard?.writeText(JSON.stringify(block.value, null, 2)).catch(() => undefined)}
                aria-label="Copy JSON"
              >
                <Copy className="size-3" />
              </Button>
            </div>
            <JsonNode value={block.value} />
          </section>
        );
      })}
    </div>
  );
}

export function SpecMediaDetailModal({
  detail,
  onClose,
  onOpenMedia,
}: {
  detail: SpecMediaDetail | null;
  onClose: () => void;
  onOpenMedia: (detail: SpecMediaDetail) => void;
}) {
  const { t } = useTranslation();
  const open = Boolean(detail);
  const src = detail?.src ?? "";
  const poster = detail?.poster || src;
  const downloadSrc = detail?.kind === "video" ? src || poster : src;
  const sections =
    detail?.sections && detail.sections.length > 0
      ? detail.sections
      : detail?.description
        ? [{ title: t("aiAssistant.mediaDescription"), body: detail.description }]
        : [];

  return (
    <Dialog open={open} onOpenChange={(nextOpen) => {
      if (!nextOpen) onClose();
    }}>
      <DialogContent
        showCloseButton={false}
        className="fixed inset-0 left-0 top-0 flex h-screen w-screen max-w-none translate-x-0 translate-y-0 items-center justify-center rounded-none border-none bg-black/25 p-0 text-white backdrop-blur-xl sm:max-w-none"
      >
        <DialogTitle className="sr-only">{detail?.title || t("aiAssistant.mediaDetail")}</DialogTitle>
        <div className="absolute right-6 top-5 z-50 flex items-center gap-5">
          <button
            type="button"
            className="text-white/45 transition hover:text-white"
            onClick={() => {
              if (downloadSrc) triggerDownload(downloadSrc);
            }}
            aria-label={t("aiAssistant.download")}
            title={t("aiAssistant.download")}
          >
            <Download className="size-6" />
          </button>
          <DialogClose className="text-white/45 outline-none transition hover:text-white" aria-label={t("aiAssistant.closeDetail")}>
            <X className="size-7" />
          </DialogClose>
        </div>

        {detail && (
          <div className="flex h-full w-full max-w-7xl items-center justify-center p-6">
            <div className="grid h-full w-full grid-cols-1 items-center gap-8 lg:grid-cols-[minmax(0,1fr)_360px] lg:gap-10">
              <div className="relative mx-auto flex max-h-[82vh] max-w-full items-center justify-center overflow-hidden rounded-[28px] bg-black/45 shadow-[0_30px_80px_rgba(0,0,0,0.45)]">
                {detail.kind === "video" ? (
                  <video
                    className="block max-h-[82vh] max-w-full object-contain"
                    src={src}
                    poster={poster || undefined}
                    controls
                    playsInline
                  />
                ) : (
                  <img
                    className="block max-h-[82vh] max-w-full object-contain"
                    src={src}
                    alt={detail.title || "image"}
                  />
                )}
              </div>

              <div className="flex min-w-0 flex-col justify-center self-center">
                {detail.title && (
                  <h2 className="text-[34px] font-semibold tracking-tight text-white/95">
                    {detail.title}
                  </h2>
                )}
                {detail.tags && detail.tags.length > 0 && (
                  <div className="mt-4 flex flex-wrap gap-1.5">
                    {detail.tags.map((tag) => (
                      <span
                        key={`${tag.label}:${tag.color ?? ""}`}
                        className="rounded border border-white/20 px-2 py-1 text-xs text-white/70"
                        style={tag.color ? { borderColor: tag.color, color: tag.color } : undefined}
                      >
                        {tag.label}
                      </span>
                    ))}
                  </div>
                )}

                <div className="mt-6 space-y-0">
                  {sections.map((section, index) => (
                    <section key={`${section.title}-${index}`} className="border-t border-white/10 py-7 first:border-t">
                      {section.title && (
                        <h3 className="mb-5 text-[15px] font-medium text-white/55">
                          {section.title}
                        </h3>
                      )}
                      {section.items && section.items.length > 0 && (
                        <ul className="space-y-5 text-[16px] leading-8 text-white/88">
                          {section.items.map((item, itemIndex) => (
                            <li key={`${section.title}-${itemIndex}`} className="flex gap-3">
                              <span className="mt-[11px] size-1.5 shrink-0 rounded-full bg-white/65" />
                              <span>{item}</span>
                            </li>
                          ))}
                        </ul>
                      )}
                      {section.body && (
                        <p className="whitespace-pre-wrap text-[16px] leading-8 text-white/88">
                          {section.body}
                        </p>
                      )}
                    </section>
                  ))}
                </div>

                {detail.candidates && detail.candidates.length > 0 && (
                  <div className="mt-2 border-t border-white/10 pt-5">
                    <div className="mb-3 text-[15px] font-medium text-white/55">
                      {t("aiAssistant.mediaCandidates")}
                    </div>
                    <div className="flex gap-2 overflow-x-auto pb-1">
                      {detail.candidates.map((candidate, index) => (
                        <button
                          key={candidate.id || index}
                          type="button"
                          onClick={() => onOpenMedia({
                            ...detail,
                            kind: "image",
                            src: candidate.src,
                            title: candidate.label || detail.title,
                          })}
                          className="block w-16 shrink-0 overflow-hidden rounded-lg border border-white/15 bg-black"
                          title={candidate.label}
                        >
                          <img src={candidate.src} alt={candidate.label || "candidate"} className="aspect-[3/4] w-full object-cover" />
                        </button>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

function MessageActionsMenu({
  message,
  isUser,
  pinned,
  onOpenDetail,
  onDelete,
  onTogglePin,
  onCopy,
  onSpeak,
}: {
  message: ChatMessage;
  isUser: boolean;
  pinned: boolean;
  onOpenDetail: (message: ChatMessage) => void;
  onDelete: (id: string) => void;
  onTogglePin: (id: string) => void;
  onCopy: () => void;
  onSpeak: () => void;
}) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={
          <Button
            type="button"
            variant="ghost"
            size="icon-xs"
            className={cn(
              "z-10 size-7 rounded-md border border-white/[0.08] bg-background/72 text-muted-foreground/70 opacity-0 shadow-sm backdrop-blur transition-[background-color,color,opacity] hover:bg-background hover:text-foreground hover:opacity-100 focus-visible:opacity-100 [@media(pointer:coarse)]:opacity-60",
              isUser
                ? "absolute right-[calc(100%+6px)] top-1/2 -translate-y-1/2 group-hover/message-actions:opacity-100 group-focus-within/message-actions:opacity-100"
                : "absolute right-1.5 top-1.5 group-hover:opacity-100 group-focus-within:opacity-100",
            )}
            aria-label="消息操作"
            title="消息操作"
          />
        }
      >
        <MoreHorizontal className="size-3.5" aria-hidden />
      </DropdownMenuTrigger>
      <DropdownMenuContent
        side="bottom"
        align={isUser ? "end" : "start"}
        sideOffset={5}
        className="w-40"
      >
        <DropdownMenuItem closeOnClick onClick={onCopy}>
          <Copy className="size-3.5" aria-hidden />
          复制
        </DropdownMenuItem>
        <DropdownMenuItem closeOnClick onClick={onSpeak}>
          <Volume2 className="size-3.5" aria-hidden />
          朗读
        </DropdownMenuItem>
        <DropdownMenuItem closeOnClick onClick={() => onOpenDetail(message)}>
          <Maximize2 className="size-3.5" aria-hidden />
          查看详情
        </DropdownMenuItem>
        <DropdownMenuItem closeOnClick onClick={() => onTogglePin(message.id)}>
          {pinned ? <PinOff className="size-3.5" aria-hidden /> : <Pin className="size-3.5" aria-hidden />}
          {pinned ? "取消置顶" : "置顶消息"}
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem variant="destructive" closeOnClick onClick={() => onDelete(message.id)}>
          <X className="size-3.5" aria-hidden />
          删除消息
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

export const MessageBubble = memo(function MessageBubble({
  message,
  variant = "default",
  onOpenDetail,
  onOpenMedia,
  pinned,
  onDelete,
  onTogglePin,
  deferStructuredRender = false,
  streaming = false,
}: {
  message: ChatMessage;
  variant?: SuperChatPanelVariant;
  onOpenDetail: (message: ChatMessage) => void;
  onOpenMedia: (detail: SpecMediaDetail) => void;
  pinned: boolean;
  onDelete: (id: string) => void;
  onTogglePin: (id: string) => void;
  deferStructuredRender?: boolean;
  streaming?: boolean;
}) {
  const isUser = message.role === "user";
  const isTool = isToolMessage(message);
  const isHistoricalTool = isTool && isHistoricalToolMessage(message);
  const isFreezoneLayout = variant === "freezone";
  const isErrorReply = isAssistantErrorReply(message);
  const isCompletionNotice = isAssistantCompletionNotice(message);
  const knowledgeReceipt = !isUser && !isTool ? knowledgeReceiptFromMessage(message) : null;
  const { t } = useTranslation();
  const shouldWaitForStructuredRender =
    deferStructuredRender && !isUser && !isTool && looksLikeStructuredRenderText(message.text);
  const { displayText, blocks } = extractStructuredBlocks(message);
  const copyText = async () => {
    await navigator.clipboard?.writeText(message.text).catch(() => undefined);
  };
  const speak = () => {
    if (!("speechSynthesis" in window)) return;
    window.speechSynthesis.cancel();
    window.speechSynthesis.speak(new SpeechSynthesisUtterance(message.text));
  };
  const actions = (
    <MessageActionsMenu
      message={message}
      isUser={isUser}
      pinned={pinned}
      onOpenDetail={onOpenDetail}
      onDelete={onDelete}
      onTogglePin={onTogglePin}
      onCopy={copyText}
      onSpeak={speak}
    />
  );

  if (isUser) {
    return (
      <div className={cn("flex justify-end", canvasOnlyProduct && isFreezoneLayout && "village-agent-message-row village-agent-message-row--user")}>
        <article
          className={cn(
            "max-w-[72%]",
            isFreezoneLayout && "max-w-[82%]",
            canvasOnlyProduct && isFreezoneLayout && "village-agent-message-bubble village-agent-message-bubble--user",
          )}
          data-message-role="user"
          data-message-kind="user"
        >
          <div className="group/message-actions">
            <div
              className={cn(
                canvasOnlyProduct && isFreezoneLayout
                  ? "relative !rounded-[15px] !border !border-white/[0.045] !bg-[#303033] !px-[13px] !py-[9px] text-sm leading-6 text-foreground shadow-none"
                  : "relative rounded-[14px] border-0 bg-white/[0.12] px-4 py-2.5 text-sm leading-6 text-foreground shadow-none",
              )}
            >
              {actions}
              <AttachmentList attachments={message.attachments} align="end" showMedia />
              {displayText && (
                <div className="whitespace-pre-wrap break-words">{displayText}</div>
              )}
              <StructuredRenderer blocks={blocks} />
            </div>
          </div>
        </article>
      </div>
    );
  }

  return (
    <div
      className={cn(
        "flex items-start gap-3",
        isUser ? "justify-end" : "justify-start",
        canvasOnlyProduct && isFreezoneLayout && "village-agent-message-row",
        canvasOnlyProduct && isFreezoneLayout && !isUser && "village-agent-message-row--assistant",
        canvasOnlyProduct && isFreezoneLayout && isTool && "village-agent-message-row--tool",
      )}
    >
      {!isUser && (
        <ChatAvatarFrame
          role={message.role}
          label={message.displayName || t("aiAssistant.title")}
          streaming={streaming}
        />
      )}
      <div className={cn("flex min-w-0 flex-1", isUser ? "justify-end" : "justify-start")}>
        <article
          className={cn(
            "group relative text-sm leading-6 shadow-none",
            blocks.length > 0 && !isUser && !isTool
              ? "w-full min-w-0 overflow-visible"
              : "w-fit overflow-hidden",
            isTool
              ? "max-w-[86%] rounded-[14px] border border-amber-500/20 bg-amber-500/8 px-4 pb-3 pt-2 text-card-foreground"
              : isUser
                ? "max-w-[86%] rounded-[14px] bg-muted px-4 pb-3 pt-2 text-foreground"
                : canvasOnlyProduct && isFreezoneLayout
                  ? "max-w-full !rounded-none !border-0 !bg-transparent !p-0 !pr-10 text-foreground"
                  : "max-w-full rounded-[14px] border border-white/[0.08] bg-transparent px-4 pb-3 pt-2 text-foreground",
            canvasOnlyProduct && isFreezoneLayout && "village-agent-message-bubble",
            canvasOnlyProduct && isFreezoneLayout && isTool && "village-agent-message-bubble--tool",
            canvasOnlyProduct && isFreezoneLayout && !isTool && "village-agent-message-bubble--assistant",
            "pr-10",
          )}
          data-message-role={message.role}
          data-message-kind={isTool ? "tool" : "assistant"}
        >
        {actions}
        {(isTool || (message.displayName && !isUser)) && (
          <div className="mb-1 flex items-center gap-2 pr-9">
            {isTool ? (
              <Badge variant="outline" className="h-5 rounded-md px-1.5 text-[10px] uppercase">
                {isHistoricalTool ? t("aiAssistant.historyTool") : t("aiAssistant.tool")}
              </Badge>
            ) : message.displayName && !isUser ? (
              <div className="text-[11px] font-medium text-muted-foreground">
                {message.displayName}
              </div>
            ) : null}
          </div>
        )}
        <AttachmentList attachments={message.attachments} />
        {shouldWaitForStructuredRender ? (
          <div className="flex items-center gap-2 py-1 text-sm text-muted-foreground" aria-live="polite">
            <span>{t("aiAssistant.waitingStructuredRender")}</span>
            <DotsIndicator />
          </div>
        ) : (
          <>
            {displayText && (
              isErrorReply && !isUser && !isTool
                ? <HighlightedErrorText text={displayText} />
                : isCompletionNotice && !isUser && !isTool
                  ? <HighlightedCompletionText text={displayText} />
                  : <MessageText text={displayText} markdown={!isUser && !isTool} />
            )}
            <StructuredRenderer blocks={blocks} onOpenMedia={onOpenMedia} />
            {knowledgeReceipt && (
              <details
                className="village-agent-knowledge-receipt mt-2 border-t border-white/[0.06] pt-2 text-[10px] leading-4 text-white/35"
              >
                <summary
                  className="cursor-pointer list-none select-none text-white/35 transition hover:text-white/65 [&::-webkit-details-marker]:hidden"
                  title={`项目 ${knowledgeReceipt.projectCount} · 长期偏好 ${knowledgeReceipt.userCount} · 专业经验 ${knowledgeReceipt.professionalCount}`}
                >
                  {knowledgeReceipt.legacySummary
                    ? `本轮使用了 ${knowledgeReceipt.usedCount} 条项目与长期经验`
                    : `本轮参考了 ${knowledgeReceipt.shownCount} 条 · 采用 ${knowledgeReceipt.usedCount} 条 · 已验证 ${knowledgeReceipt.verifiedCount} 条`}
                  <span className="ml-1 text-white/20">· 点击查看依据</span>
                </summary>
                <div className="mt-2 space-y-1.5 rounded-lg border border-white/[0.06] bg-white/[0.02] p-2">
                  {knowledgeReceipt.items.length === 0 ? (
                    <div className="text-white/25">本轮参考了记忆，详细来源不可用。</div>
                  ) : knowledgeReceipt.items.map((item) => {
                    const statusLabel = item.status === "confirmed"
                      ? "已确认"
                      : item.status === "validated"
                        ? "已验证"
                        : item.status === "candidate"
                          ? "候选"
                          : item.status || "已记录";
                    const scopeLabel = item.scope_kind === "project"
                      ? "项目"
                      : item.scope_kind === "user"
                        ? "用户偏好"
                        : item.scope_kind === "professional"
                          ? "专业经验"
                          : item.scope_kind || "经验";
                    return (
                      <div key={item.memory_id} className="rounded-md border border-white/[0.05] px-2 py-1.5">
                        <div className="flex flex-wrap items-center gap-x-1.5 gap-y-0.5 text-white/55">
                          <span className="font-medium text-white/70">{item.title}</span>
                          <span className="text-white/20">·</span>
                          <span>{scopeLabel}</span>
                          <span className="text-white/20">·</span>
                          <span>{statusLabel}</span>
                          <span className="text-white/20">·</span>
                          <span>{knowledgeReceiptUsageLabel(item.usage_status)}</span>
                          {item.candidate_recall && <span className="text-amber-200/65">待验证</span>}
                        </div>
                        {item.summary && <div className="mt-0.5 line-clamp-2 text-white/30">{item.summary}</div>}
                        <div className="mt-1 flex flex-wrap gap-x-2 gap-y-0.5 text-white/22">
                          <span>置信度 {Math.round(item.confidence * 100)}%</span>
                          <span>证据 {item.evidence_count}</span>
                          <span>采用 {item.applied_count}</span>
                          <span>成功 {item.positive_count}</span>
                          {item.negative_count > 0 && <span>失败 {item.negative_count}</span>}
                          {item.influence === "execution_rule" && (
                            <span className="text-emerald-200/55">
                              {knowledgeReceipt.legacySummary
                                ? "执行规则已注入"
                                : `执行规则${knowledgeReceiptUsageLabel(item.usage_status)}`}
                            </span>
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>
              </details>
            )}
          </>
        )}
        </article>
      </div>
      {isUser && (
        <ChatAvatarFrame
          role="user"
          label={message.displayName}
        />
      )}
    </div>
  );
});

type TimelineTurn = {
  id: string;
  index: number;
  preview: string;
  timestamp: number;
  hasAttachment: boolean;
  hasImage: boolean;
};

function buildTimelineTurns(messages: ChatMessage[]): TimelineTurn[] {
  return messages
    .filter((message) => message.role === "user")
    .map((message, index) => {
      const attachments = message.attachments ?? [];
      const hasImage = attachments.some((attachment) => attachment.mimeType?.startsWith("image/"));
      const hasAttachment = attachments.length > 0;
      const preview = message.text.trim().slice(0, 60) || (hasImage ? "Image" : hasAttachment ? "File" : "...");
      return {
        id: message.id,
        index,
        preview,
        timestamp: message.timestamp,
        hasAttachment,
        hasImage,
      };
    });
}

export function ChatTimeline({
  messages,
  scrollRef,
}: {
  messages: ChatMessage[];
  scrollRef: React.RefObject<HTMLDivElement | null>;
}) {
  const turns = useMemo(() => buildTimelineTurns(messages), [messages]);
  const [activeIndex, setActiveIndex] = useState(-1);
  const [hoveredTurn, setHoveredTurn] = useState<{
    index: number;
    top: number;
    right: number;
  } | null>(null);
  const activeButtonRef = useRef<HTMLButtonElement | null>(null);
  const timelineListRef = useRef<HTMLDivElement | null>(null);
  const [scrollEdges, setScrollEdges] = useState({ up: false, down: false });

  const updateScrollEdges = useCallback(() => {
    const list = timelineListRef.current;
    if (!list) return;
    const next = {
      up: list.scrollTop > 1,
      down: list.scrollTop + list.clientHeight < list.scrollHeight - 1,
    };
    setScrollEdges((current) => current.up === next.up && current.down === next.down ? current : next);
  }, []);

  useEffect(() => {
    const container = scrollRef.current;
    if (!container || turns.length < 2) return;

    const handleScroll = () => {
      const containerRect = container.getBoundingClientRect();
      const targetY = containerRect.top + containerRect.height / 3;
      let closest = -1;
      let closestDistance = Infinity;

      for (let index = turns.length - 1; index >= 0; index -= 1) {
        const element = container.querySelector(`[data-turn-id="${CSS.escape(turns[index].id)}"]`);
        if (!element) continue;
        const rect = element.getBoundingClientRect();
        const distance = Math.abs(rect.top - targetY);
        if (distance < closestDistance) {
          closestDistance = distance;
          closest = index;
        }
      }
      setActiveIndex(closest);
    };

    container.addEventListener("scroll", handleScroll, { passive: true });
    handleScroll();
    return () => container.removeEventListener("scroll", handleScroll);
  }, [scrollRef, turns]);

  useEffect(() => {
    const list = timelineListRef.current;
    const button = activeButtonRef.current;
    if (!list || !button) return;
    const listRect = list.getBoundingClientRect();
    const buttonRect = button.getBoundingClientRect();
    const edgePadding = 8;
    if (buttonRect.top < listRect.top + edgePadding) {
      list.scrollBy({ top: buttonRect.top - listRect.top - edgePadding, behavior: "auto" });
    } else if (buttonRect.bottom > listRect.bottom - edgePadding) {
      list.scrollBy({ top: buttonRect.bottom - listRect.bottom + edgePadding, behavior: "auto" });
    }
  }, [activeIndex]);

  useEffect(() => {
    const list = timelineListRef.current;
    if (!list) return;
    updateScrollEdges();
    list.addEventListener("scroll", updateScrollEdges, { passive: true });
    const resizeObserver = typeof ResizeObserver === "undefined"
      ? null
      : new ResizeObserver(updateScrollEdges);
    resizeObserver?.observe(list);
    return () => {
      list.removeEventListener("scroll", updateScrollEdges);
      resizeObserver?.disconnect();
    };
  }, [turns.length, updateScrollEdges]);

  const scrollToTurn = useCallback((turn: TimelineTurn) => {
    const container = scrollRef.current;
    if (!container) return;
    const element = container.querySelector(`[data-turn-id="${CSS.escape(turn.id)}"]`);
    element?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [scrollRef]);

  const revealTimelineContext = useCallback((button: HTMLButtonElement) => {
    const list = timelineListRef.current;
    if (!list) return;
    const listRect = list.getBoundingClientRect();
    const buttonRect = button.getBoundingClientRect();
    const delta = calculateTimelineContextDelta({
      viewportHeight: list.clientHeight,
      nodeCenter: buttonRect.top - listRect.top + buttonRect.height / 2,
      scrollTop: list.scrollTop,
      scrollHeight: list.scrollHeight,
    });
    if (Math.abs(delta) < 1) return;
    list.scrollTo({ top: list.scrollTop + delta, behavior: "smooth" });
  }, []);

  if (turns.length < 2) return null;

  return (
    <div className="pointer-events-none absolute bottom-4 right-1 top-4 z-20 hidden w-9 select-none lg:flex">
      <div className="pointer-events-auto relative flex h-full w-full justify-center">
        <div className="absolute inset-y-2 left-1/2 w-px -translate-x-1/2 bg-border/70" />
        <div
          ref={timelineListRef}
          className="flex max-h-full flex-col items-center gap-2 overflow-y-auto px-2 py-2 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
        >
          {turns.map((turn, index) => (
            <button
              key={turn.id}
              ref={index === activeIndex ? activeButtonRef : null}
              type="button"
              className="group/timeline-dot relative z-10 flex h-6 w-4 shrink-0 items-center justify-center"
              onClick={(event) => {
                revealTimelineContext(event.currentTarget);
                scrollToTurn(turn);
              }}
              onMouseEnter={(event) => {
                const rect = event.currentTarget.getBoundingClientRect();
                setHoveredTurn({
                  index,
                  top: rect.top + rect.height / 2,
                  right: window.innerWidth - rect.left + 12,
                });
              }}
              onMouseLeave={() => setHoveredTurn(null)}
              aria-label={`Turn ${index + 1}: ${turn.preview}`}
            >
              <span
                aria-hidden="true"
                className={cn(
                  "rounded-full border transition-[width,height,background-color,border-color] duration-150",
                  index === activeIndex
                    ? turns.length > 80
                      ? "size-2 border-primary bg-primary"
                      : turns.length > 40
                        ? "size-2.5 border-primary bg-primary"
                        : "size-3 border-primary bg-primary"
                    : cn(
                        "border-muted-foreground/40 bg-background group-hover/timeline-dot:border-primary group-hover/timeline-dot:bg-primary/20",
                        turns.length > 80 ? "size-1.5" : turns.length > 40 ? "size-2" : "size-2.5",
                      ),
                )}
              />
            </button>
          ))}
        </div>
        <div
          className={cn(
            "pointer-events-none absolute inset-x-0 top-0 z-20 h-10 bg-gradient-to-b from-background via-background/55 to-transparent transition-opacity duration-200",
            scrollEdges.up ? "opacity-75" : "opacity-0",
          )}
          aria-hidden="true"
        />
        <div
          className={cn(
            "pointer-events-none absolute inset-x-0 bottom-0 z-20 h-10 bg-gradient-to-t from-background via-background/55 to-transparent transition-opacity duration-200",
            scrollEdges.down ? "opacity-75" : "opacity-0",
          )}
          aria-hidden="true"
        />
      </div>
      {hoveredTurn && turns[hoveredTurn.index] && createPortal(
        <div
          className="pointer-events-none fixed z-[80] -translate-y-1/2"
          style={{ top: hoveredTurn.top, right: hoveredTurn.right }}
        >
          <div className="max-w-[240px] rounded-lg border border-border bg-popover px-3 py-2 text-xs text-popover-foreground shadow-lg">
            <div className="flex items-center gap-1 font-medium">
              {turns[hoveredTurn.index].hasImage && <Image className="size-3 shrink-0 text-muted-foreground" />}
              {turns[hoveredTurn.index].hasAttachment && !turns[hoveredTurn.index].hasImage && <File className="size-3 shrink-0 text-muted-foreground" />}
              <span className="line-clamp-3 whitespace-normal break-words">{turns[hoveredTurn.index].preview}</span>
            </div>
            <div className="mt-1 text-muted-foreground">
              {new Date(turns[hoveredTurn.index].timestamp).toLocaleTimeString([], {
                hour: "numeric",
                minute: "2-digit",
              })}
            </div>
          </div>
        </div>,
        document.body,
      )}
    </div>
  );
}

export function AttachmentList({
  attachments,
  align = "start",
  showMedia = false,
}: {
  attachments?: ChatAttachment[];
  align?: "start" | "end";
  showMedia?: boolean;
}) {
  const visibleAttachments = attachments?.filter((attachment) => shouldRenderAttachmentChip(attachment, showMedia)) ?? [];
  if (visibleAttachments.length === 0) return null;

  return (
    <div className={cn("mb-2 flex flex-wrap gap-1.5", showMedia && "village-agent-message-attachments", align === "end" && "justify-end")}>
      {visibleAttachments.map((attachment) => (
        <AttachmentChip
          key={attachment.id || attachment.fileName || attachment.content}
          attachment={attachment}
          showMedia={showMedia}
        />
      ))}
    </div>
  );
}

function AttachmentChip({ attachment, showMedia = false }: { attachment: ChatAttachment; showMedia?: boolean }) {
  const isImage = isImageAttachment(attachment);
  const isVideo = isVideoAttachment(attachment);
  const inlineContent = attachment.content?.startsWith("data:") || attachment.content?.startsWith("http")
    ? attachment.content
    : null;
  const previewSrc = (inlineContent || attachment.url) ?? null;

  if (showMedia && previewSrc && (isImage || isVideo)) {
    return (
      <span
        className="village-agent-sent-reference-preview"
        aria-label={isVideo ? "本轮视频引用" : "本轮图片引用"}
      >
        {isVideo ? (
          <video src={previewSrc} muted playsInline preload="metadata" />
        ) : (
          <img src={previewSrc} alt="" loading="lazy" />
        )}
      </span>
    );
  }

  return (
    <span className="inline-flex max-w-56 items-center gap-1.5 rounded-md border border-border/70 bg-background/45 px-2 py-1 text-xs">
      {previewSrc ? (
        <img src={previewSrc} alt="" className="size-7 shrink-0 rounded object-cover" />
      ) : isImage ? (
        <Image className="size-3.5" />
      ) : (
        <File className="size-3.5" />
      )}
      <span className="min-w-0 truncate">{attachment.label || attachment.fileName || attachment.mimeType || "Attachment"}</span>
    </span>
  );
}

export function shouldRenderAttachmentChip(attachment: ChatAttachment, showMedia = false): boolean {
  if (showMedia) return true;
  return !isImageAttachment(attachment) && !isVideoAttachment(attachment);
}

function isImageAttachment(attachment: ChatAttachment): boolean {
  return (
    attachment.mimeType?.startsWith("image/")
    || attachment.type === "image"
    || attachment.type === "canvas_image"
    || attachment.kind === "image"
    || /\.(avif|gif|jpe?g|png|webp)$/i.test(attachment.fileName ?? "")
  );
}

function isVideoAttachment(attachment: ChatAttachment): boolean {
  return (
    attachment.mimeType?.startsWith("video/")
    || attachment.type === "video"
    || attachment.kind === "video"
    || /\.(m4v|mov|mp4|webm)$/i.test(attachment.fileName ?? "")
  );
}


export function ControlBar({
  chat,
  compact = false,
  searchOpen,
  onToggleSearch,
  hideProviderControls = false,
}: {
  chat: ReturnType<typeof useSuperChat>;
  compact?: boolean;
  searchOpen: boolean;
  onToggleSearch: () => void;
  hideProviderControls?: boolean;
}) {
  const { t } = useTranslation();
  const hasInstances = chat.relayInstances.length > 0;
  const hasModels = chat.models.length > 0;
  const selectedModel = chat.models.find((model) => model.id === chat.activeModel) ?? null;
  const transportStatus =
    chat.connected
      ? "connected"
      : chat.connecting || chat.busy
        ? "reconnecting"
        : "disconnected";
  const transportLabel =
    transportStatus === "connected"
      ? t("aiAssistant.connected")
      : transportStatus === "reconnecting"
        ? t("aiAssistant.reconnecting")
        : t("aiAssistant.disconnected");
  return (
    <div
      className={cn(
        "flex min-w-0 shrink items-center gap-2",
        !compact && "flex-wrap border-b border-border/65 px-3 py-2",
      )}
    >
      {!compact && (
        <div className="flex shrink-0 items-center gap-1.5 text-xs text-muted-foreground" title={chat.error || transportLabel}>
          <span>{transportLabel}</span>
          <span>{t("aiAssistant.backendTransport")}</span>
        </div>
      )}
      {hasInstances && !hideProviderControls && (
        <select
          value={chat.selectedInstanceId}
          onChange={(event) => chat.selectRelayInstance(event.target.value)}
          className={cn(
            "h-7 min-w-0 rounded-md border border-border bg-background px-2 text-xs outline-none disabled:opacity-50",
            compact ? "w-28" : "flex-1",
          )}
          title={t("aiAssistant.instance")}
        >
          {chat.relayInstances.map((instance) => (
            <option key={instance.instanceId} value={instance.instanceId}>
              {instance.instanceName || instance.instanceId}{instance.busy ? " *" : ""}
            </option>
          ))}
        </select>
      )}
      {hasModels && !hideProviderControls && (
        <div className={cn("flex min-w-0 items-center gap-1", compact ? "w-28" : "flex-1")}>
          <select
            value={chat.activeModel ?? ""}
            onChange={(event) => chat.switchModel(event.target.value)}
            disabled={chat.modelsLoading || chat.busy || !chat.connected}
            className="h-7 min-w-0 flex-1 rounded-md border border-border bg-background px-2 text-xs outline-none disabled:opacity-50"
            title={chat.busy
              ? "当前任务执行中，模型将在本轮结束后可切换"
              : selectedModel?.description || t("aiAssistant.model")}
            aria-label={t("aiAssistant.model")}
          >
            {chat.models.map((model) => (
              <option
                key={model.id}
                value={model.id}
                title={model.description}
                disabled={model.stale === true || model.disabled === true || model.enabled === false}
              >
                {model.label || model.id}{model.reasoning ? " · 深度" : ""}
              </option>
            ))}
          </select>
          <Button
            variant="ghost"
            size="icon-sm"
            onClick={chat.refreshModels}
            disabled={chat.modelsLoading || chat.busy}
            aria-label="刷新小树模型列表"
            title="刷新小树模型列表"
            className="shrink-0 text-muted-foreground"
          >
            <RefreshCw className={cn("size-3.5", chat.modelsLoading && "animate-spin")} />
          </Button>
        </div>
      )}
      <Button
        variant="ghost"
        size="icon-sm"
        onClick={onToggleSearch}
        aria-label={t("aiAssistant.search")}
        title={t("aiAssistant.search")}
        className={searchOpen ? "text-primary" : "text-muted-foreground"}
      >
        <Search className="size-4" />
      </Button>
      {!(compact && hideProviderControls) && (
        <Button
          variant="ghost"
          size="icon-sm"
          onClick={() => chat.setSettings({ showToolEvents: !chat.settings.showToolEvents })}
          aria-pressed={chat.settings.showToolEvents}
          aria-label={t("aiAssistant.showToolEvents")}
          title={t("aiAssistant.showToolEvents")}
          className={chat.settings.showToolEvents ? "text-primary" : "text-muted-foreground"}
        >
          <ListTree className="size-4" />
        </Button>
      )}
      {!compact && (
        <>
          <Button
            variant="ghost"
            size="icon-sm"
            onClick={() => chat.setSettings({
              showStructuredSourceWhileStreaming: !chat.settings.showStructuredSourceWhileStreaming,
            })}
            aria-pressed={chat.settings.showStructuredSourceWhileStreaming}
            aria-label={t("aiAssistant.showStructuredSourceWhileStreaming")}
            title={t("aiAssistant.showStructuredSourceWhileStreaming")}
            className={chat.settings.showStructuredSourceWhileStreaming ? "text-primary" : "text-muted-foreground"}
          >
            <Braces className="size-4" />
          </Button>
        </>
      )}
    </div>
  );
}

type VillageAgentChoice = {
  value: string;
  label: string;
  description?: string;
  disabled?: boolean;
};

export function VillageAgentSelect({
  value,
  options,
  onValueChange,
  ariaLabel,
  title,
  className,
  triggerClassName,
  disabled = false,
  testId,
}: {
  value: string;
  options: readonly VillageAgentChoice[];
  onValueChange: (value: string) => void;
  ariaLabel: string;
  title: string;
  className?: string;
  triggerClassName?: string;
  disabled?: boolean;
  testId: string;
}) {
  const selected = options.find((option) => option.value === value) ?? options[0];
  if (!selected) return null;
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={
          <button
            type="button"
            className={cn(
              "village-agent-select-trigger inline-flex h-8 min-w-0 items-center gap-1 rounded-full px-2 text-left text-[12px] font-medium outline-none transition-colors disabled:pointer-events-none disabled:opacity-45",
              triggerClassName,
            )}
            aria-label={ariaLabel}
            title={title}
            disabled={disabled}
            data-agent-select={testId}
          />
        }
      >
          <span className="min-w-0 flex-1 truncate">{selected.label}</span>
          <ChevronDown className="size-3.5 shrink-0 opacity-55" aria-hidden />
      </DropdownMenuTrigger>
      <DropdownMenuContent
        side="top"
        align="start"
        sideOffset={8}
        className={cn("village-agent-select-menu min-w-52", className)}
      >
        <DropdownMenuRadioGroup value={value} onValueChange={onValueChange}>
          {options.map((option) => (
            <DropdownMenuRadioItem
              key={option.value}
              value={option.value}
              disabled={option.disabled}
              closeOnClick
              className="village-agent-select-option min-h-9 px-2.5 py-1.5 pr-9"
            >
              <span className="min-w-0">
                <span className="block truncate text-[12px] font-medium">{option.label}</span>
                {option.description && (
                  <span className="mt-0.5 block truncate text-[10px] text-muted-foreground">
                    {option.description}
                  </span>
                )}
              </span>
            </DropdownMenuRadioItem>
          ))}
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

export function HeaderControlPortal({
  chat,
  searchOpen,
  onToggleSearch,
}: {
  chat: ReturnType<typeof useSuperChat>;
  searchOpen: boolean;
  onToggleSearch: () => void;
}) {
  const [target, setTarget] = useState<HTMLElement | null>(null);

  useEffect(() => {
    setTarget(document.getElementById("superchat-header-controls"));
  }, []);

  if (!target) return null;
  return createPortal(
    <ControlBar
      chat={chat}
      compact
      searchOpen={searchOpen}
      onToggleSearch={onToggleSearch}
    />,
    target,
  );
}

export function SearchBar({
  query,
  onChange,
  onClose,
}: {
  query: string;
  onChange: (query: string) => void;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const inputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  return (
    <div className="village-agent-search-bar flex items-center gap-2 border-b border-border bg-muted/30 px-4 py-2">
      <Search className="size-4 shrink-0 text-muted-foreground" />
      <Input
        ref={inputRef}
        value={query}
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Escape") onClose();
        }}
        placeholder={t("aiAssistant.search")}
        className="h-7 border-0 bg-transparent text-sm shadow-none focus-visible:ring-0"
      />
      {query && (
        <Button
          variant="ghost"
          size="icon"
          className="size-6"
          onClick={() => onChange("")}
          aria-label="清空消息搜索"
          title="清空消息搜索"
        >
          <X className="size-3" />
        </Button>
      )}
      <Button
        variant="ghost"
        size="icon"
        className="size-6"
        onClick={onClose}
        aria-label="关闭消息搜索"
        title="关闭消息搜索"
      >
        <X className="size-4" />
      </Button>
    </div>
  );
}

export function PinnedPanel({
  messages,
  onClear,
  onTogglePin,
}: {
  messages: ChatMessage[];
  onClear: () => void;
  onTogglePin: (id: string) => void;
}) {
  const { t } = useTranslation();
  if (messages.length === 0) return null;

  return (
    <div className="border-b border-border/65 bg-muted/20 px-3 py-2">
      <div className="mb-2 flex items-center justify-between gap-2">
        <div className="flex items-center gap-1.5 text-xs font-medium">
          <Pin className="size-3.5" />
          {t("aiAssistant.pinned")}
        </div>
        <Button variant="ghost" size="xs" onClick={onClear}>
          {t("aiAssistant.clearPinned")}
        </Button>
      </div>
      <div className="flex gap-2 overflow-x-auto pb-1">
        {messages.map((message) => (
          <button
            key={message.id}
            type="button"
            onClick={() => onTogglePin(message.id)}
            className="min-w-44 max-w-56 rounded-md border border-border/70 bg-background/70 px-2 py-1.5 text-left text-xs text-muted-foreground hover:text-foreground"
          >
            <div className="line-clamp-2">{message.text}</div>
          </button>
        ))}
      </div>
    </div>
  );
}

export function MessageDetailPanel({
  message,
  onClose,
  onOpenMedia,
}: {
  message: ChatMessage | null;
  onClose: () => void;
  onOpenMedia: (detail: SpecMediaDetail) => void;
}) {
  const { t } = useTranslation();
  if (!message) return null;
  const { displayText, blocks } = extractStructuredBlocks(message);

  return (
    <aside className="hidden h-full w-72 shrink-0 flex-col border-l border-border/65 bg-background xl:flex">
      <div className="flex h-11 shrink-0 items-center justify-between border-b border-border/65 px-3">
        <div className="text-sm font-medium">{t("aiAssistant.messageDetail")}</div>
        <Button variant="ghost" size="icon-sm" onClick={onClose} aria-label={t("aiAssistant.closeDetail")}>
          <X className="size-4" />
        </Button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-3">
        <div className="mb-3 flex items-center gap-2">
          <Badge variant="outline" className="rounded-md uppercase">
            {message.role}
          </Badge>
          <span className="text-xs text-muted-foreground">
            {new Date(message.timestamp).toLocaleString()}
          </span>
        </div>
        {displayText && (
          <pre className="mb-3 whitespace-pre-wrap break-words rounded-md border border-border/70 bg-muted/30 p-2 text-xs leading-5">
            {displayText}
          </pre>
        )}
        <StructuredRenderer blocks={blocks} onOpenMedia={onOpenMedia} />
        {message.raw !== undefined && (
          <details className="mt-3">
            <summary className="cursor-pointer text-xs text-muted-foreground">{t("aiAssistant.raw")}</summary>
            <pre className="mt-2 whitespace-pre-wrap break-words rounded-md border border-border/70 bg-muted/30 p-2 text-[11px] leading-5">
              {JSON.stringify(message.raw, null, 2)}
            </pre>
          </details>
        )}
      </div>
    </aside>
  );
}

