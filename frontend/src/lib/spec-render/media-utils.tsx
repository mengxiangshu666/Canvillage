"use client";

import { useCallback, useEffect, useState, type ReactNode } from "react";
import { cn } from "./utils";
import { ImageDetailModal } from "./modals/image-detail-modal";
import type { ImageCandidate } from "./modals/image-detail-modal";
import type { Props } from "./types";
import { useSpecRendererContext } from "./context";
import { TiltCard } from "./tilt-card";

export const MEDIA_SIZES: Record<string, string> = {
  sm: "200px",
  md: "400px",
  lg: "600px",
  full: "100%",
};

export function mediaStyle(props: Props): React.CSSProperties | undefined {
  const size = props.size as string | undefined;
  const maxWidth = props.maxWidth as string | number | undefined;
  const aspectRatio = props.aspectRatio as string | undefined;
  const mw = maxWidth
    ? typeof maxWidth === "number"
      ? `${maxWidth}px`
      : maxWidth
    : size
      ? MEDIA_SIZES[size]
      : undefined;
  if (!mw && !aspectRatio) return undefined;
  const style: React.CSSProperties = {};
  if (mw) style.maxWidth = `min(${mw}, 100%)`;
  if (aspectRatio) style.aspectRatio = aspectRatio;
  return style;
}

export function mediaWidth(props: Props): string | undefined {
  const size = props.size as string | undefined;
  const maxWidth = props.maxWidth as string | number | undefined;
  if (typeof maxWidth === "number") return `${maxWidth}px`;
  if (typeof maxWidth === "string") return `min(${maxWidth}, 100%)`;
  if (size && MEDIA_SIZES[size]) return `min(${MEDIA_SIZES[size]}, 100%)`;
  return undefined;
}

export function extractCandidates(
  props: Props,
): ImageCandidate[] | undefined {
  if (!Array.isArray(props.candidates)) return undefined;
  const out = (props.candidates as Array<Record<string, unknown>>)
    .filter(
      (c) =>
        c &&
        typeof c === "object" &&
        typeof c.id === "string" &&
        typeof c.src === "string",
    )
    .map((c) => ({
      id: c.id as string,
      src: c.src as string,
      label: typeof c.label === "string" ? c.label : undefined,
    }));
  return out.length > 0 ? out : undefined;
}

/** 用户选中候选图后通过 context 回调通知宿主，并关闭弹窗 */
export function useSendCandidateSelection(
  setModalOpen?: (open: boolean) => void,
) {
  const { onCandidateSelect } = useSpecRendererContext();
  return useCallback(
    (candidate: ImageCandidate) => {
      onCandidateSelect?.(candidate);
      setModalOpen?.(false);
    },
    [onCandidateSelect, setModalOpen],
  );
}

/** 媒体加载占位：有 loadingVideoUrl 用循环视频，否则纯 CSS 闪烁 */
function LoadingPlaceholder({ className }: { className: string }) {
  const { loadingVideoUrl } = useSpecRendererContext();
  if (loadingVideoUrl) {
    return (
      <video
        className={className}
        src={loadingVideoUrl}
        autoPlay
        loop
        muted
        playsInline
      />
    );
  }
  return (
    <div
      className={cn(className, "animate-pulse bg-muted-foreground/10")}
    />
  );
}

export function MediaTransitionShell({
  className,
  loadingClassName,
  children,
}: {
  className?: string;
  loadingClassName: string;
  children: (props: {
    ready: boolean;
    setReady: (ready: boolean) => void;
  }) => ReactNode;
}) {
  const [ready, setReady] = useState(false);
  const [fakeProgress, setFakeProgress] = useState(0);

  useEffect(() => {
    if (ready) {
      setFakeProgress(100);
      return;
    }
    let frame: number;
    const start = Date.now();
    const tick = () => {
      const elapsed = Date.now() - start;
      let p: number;
      if (elapsed < 1500) p = (elapsed / 1500) * 60;
      else if (elapsed < 4000) p = 60 + ((elapsed - 1500) / 2500) * 28;
      else p = Math.min(88 + ((elapsed - 4000) / 5000) * 7, 95);
      setFakeProgress(Math.round(p));
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [ready]);

  return (
    <div className={cn("relative overflow-hidden", className)}>
      <div
        className={cn(
          "transition-all duration-500 ease-out",
          ready ? "scale-100 opacity-100" : "scale-[1.02] opacity-0",
        )}
      >
        {children({ ready, setReady })}
      </div>
      <div
        className={cn(
          "pointer-events-none absolute inset-0 transition-all duration-500 ease-out",
          ready ? "scale-95 opacity-0" : "scale-100 opacity-100",
        )}
        aria-hidden={ready}
      >
        <LoadingPlaceholder className={loadingClassName} />
        {!ready && (
          <div className="absolute left-2 top-2 z-10 rounded-md bg-black/50 px-2 py-1 text-xs text-white/90 backdrop-blur-sm">
            灵感即显 {fakeProgress}%
          </div>
        )}
      </div>
    </div>
  );
}

/** 未解析路径占位：内部路径还没有被 resolveMediaUrl 处理时用 */
export function UnresolvedMediaPlaceholder({
  width,
  className,
  style,
}: {
  width?: string;
  className?: string;
  style?: React.CSSProperties;
}) {
  const { loadingVideoUrl } = useSpecRendererContext();
  return (
    <figure
      className={cn("jr-figure jr-figure--overlay relative", className)}
      style={{ width: width ?? "min(220px, 100%)" }}
    >
      {loadingVideoUrl ? (
        <video
          className="block rounded-xl w-full"
          src={loadingVideoUrl}
          autoPlay
          loop
          muted
          playsInline
          style={{
            ...style,
            objectFit: "contain",
            width: "100%",
            height: "auto",
          }}
        />
      ) : (
        <div
          className="block rounded-xl w-full animate-pulse bg-muted-foreground/10"
          style={{ aspectRatio: "1 / 1", ...style }}
        />
      )}
    </figure>
  );
}

export function PreviewableImageFigure({
  src,
  alt,
  overlayTitle,
  overlayDescription,
  detailType,
  detailTags,
  detailSections,
  historyImages,
  candidates,
  figureClassName,
  imageClassName,
  imageStyle,
  loading = "lazy",
  onLoad,
}: {
  src: string;
  alt?: string;
  overlayTitle?: string;
  overlayDescription?: string;
  detailType?: string;
  detailTags?: string[];
  detailSections?: Array<{ label: string; value: string }>;
  historyImages?: string[];
  candidates?: ImageCandidate[];
  figureClassName?: string;
  glowClassName?: string;
  imageClassName?: string;
  imageStyle?: React.CSSProperties;
  loading?: "lazy" | "eager";
  onLoad?: () => void;
}) {
  const [modalOpen, setModalOpen] = useState(false);
  const [displaySrc, setDisplaySrc] = useState(src);
  useEffect(() => {
    setDisplaySrc(src);
  }, [src]);
  const hasOverlay = Boolean(overlayTitle || overlayDescription);
  const sendSelection = useSendCandidateSelection(setModalOpen);

  const handleSelectCandidate = candidates?.length
    ? (candidate: ImageCandidate) => {
        setDisplaySrc(candidate.src);
        sendSelection(candidate);
      }
    : undefined;

  return (
    <>
      <TiltCard className={figureClassName}>
        <figure
          className="relative w-full h-full cursor-pointer"
          onClick={() => setModalOpen(true)}
          role="button"
          tabIndex={0}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              setModalOpen(true);
            }
          }}
        >
          <img
            className={cn(
              "absolute inset-0 w-full h-full object-cover select-none",
              imageClassName,
            )}
            src={displaySrc}
            alt={alt ?? ""}
            loading={loading}
            style={imageStyle}
            draggable={false}
            onLoad={onLoad}
          />
          {hasOverlay && (
            <figcaption className="pointer-events-none absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/85 via-black/40 to-transparent px-3 pb-2.5 pt-6 text-white z-10">
              {overlayTitle && (
                <div className="truncate text-xs font-medium">
                  {overlayTitle}
                </div>
              )}
              {overlayDescription && (
                <div className="mt-1 line-clamp-2 text-[11px] leading-4 text-white/80">
                  {overlayDescription}
                </div>
              )}
            </figcaption>
          )}
        </figure>
      </TiltCard>
      <ImageDetailModal
        src={displaySrc}
        hasOverlay={hasOverlay}
        overlayTitle={overlayTitle}
        overlayDescription={overlayDescription}
        detailType={detailType}
        detailTags={detailTags}
        detailSections={detailSections}
        historyImages={historyImages}
        candidates={candidates}
        onSelectCandidate={
          candidates?.length ? handleSelectCandidate : undefined
        }
        open={modalOpen}
        setOpen={setModalOpen}
      />
    </>
  );
}
