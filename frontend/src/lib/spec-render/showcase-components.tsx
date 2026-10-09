"use client";

import { useEffect, useState } from "react";
import { Play } from "lucide-react";
import { ImageDetailModal } from "./modals/image-detail-modal";
import type { ImageCandidate } from "./modals/image-detail-modal";
import { VideoDetailModal } from "./modals/video-detail-modal";
import type { ComponentFn, RenderContext, RootRendererFn } from "./types";
import { p, coerceText } from "./types";
import type { Spec } from "./spec";
import { RenderNode, DefaultRootRenderer } from "./core";
import { useSpecRendererContext } from "./context";
import {
  MediaTransitionShell,
  extractCandidates,
  useSendCandidateSelection,
} from "./media-utils";
import { TiltCard } from "./tilt-card";
import type {
  SketchGalleryItem,
  KeyframeGalleryItem,
} from "./spec-helpers";

function useIsUnresolved(src: string): boolean {
  const { parseMediaUrl } = useSpecRendererContext();
  if (!parseMediaUrl) return false;
  return Boolean(parseMediaUrl(src)) && !src.startsWith("http");
}

function showcaseCardSize(props: Record<string, unknown>): {
  width: number;
  height: number;
} {
  const ratio = props.aspectRatio as string | undefined;
  const maxW = props.maxWidth as number | undefined;
  if (ratio) {
    const parts = ratio.split("/").map(Number);
    if (parts.length === 2 && parts[0] > 0 && parts[1] > 0) {
      const w = maxW ?? (parts[0] >= parts[1] ? 280 : 148);
      const h = Math.round((w * parts[1]) / parts[0]);
      return { width: w, height: h };
    }
  }
  return { width: 148, height: 198 };
}

function LoadingTile({ className }: { className?: string }) {
  const { loadingVideoUrl } = useSpecRendererContext();
  if (loadingVideoUrl) {
    return (
      <video
        className={className ?? "block w-full h-full object-cover"}
        src={loadingVideoUrl}
        autoPlay
        loop
        muted
        playsInline
      />
    );
  }
  return (
    <div className={`${className ?? "block w-full h-full"} animate-pulse bg-muted-foreground/10`} />
  );
}

export const ShowcaseImage: ComponentFn = ({ element }) => {
  const props = p(element);
  const src = (props.src ?? props.url ?? "") as string;
  const alt = (props.alt ?? "") as string;
  const overlayTitle = props.overlayTitle as string | undefined;
  const overlayDescription = props.overlayDescription as string | undefined;
  const detailType = props.detailType as string | undefined;
  const detailTags = Array.isArray(props.detailTags)
    ? props.detailTags.filter(
        (tag): tag is string =>
          typeof tag === "string" && tag.trim().length > 0,
      )
    : undefined;
  const detailSections = Array.isArray(props.detailSections)
    ? (props.detailSections as Array<{ label: string; value: string }>)
    : undefined;
  const candidates = extractCandidates(props);
  const [modalOpen, setModalOpen] = useState(false);
  const [displaySrc, setDisplaySrc] = useState(src);
  useEffect(() => {
    setDisplaySrc(src);
  }, [src]);
  const sendSelection = useSendCandidateSelection(setModalOpen);

  const handleSelectCandidate = candidates
    ? (candidate: ImageCandidate) => {
        setDisplaySrc(candidate.src);
        sendSelection(candidate);
      }
    : undefined;

  const cardSize = showcaseCardSize(props);
  const cardStyle = {
    width: `${cardSize.width}px`,
    height: `${cardSize.height}px`,
  };
  const isUnresolved = useIsUnresolved(src);

  if (!src) return null;

  if (isUnresolved) {
    return (
      <TiltCard className="shrink-0" style={cardStyle}>
        <LoadingTile />
      </TiltCard>
    );
  }

  return (
    <>
      <TiltCard className="shrink-0" style={cardStyle}>
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
          <MediaTransitionShell
            className="absolute inset-0"
            loadingClassName="h-full w-full object-cover"
          >
            {({ setReady }) => (
              <img
                className="block w-full h-full object-cover select-none"
                src={displaySrc}
                alt={alt}
                loading="lazy"
                draggable={false}
                onLoad={() => setReady(true)}
              />
            )}
          </MediaTransitionShell>
          {(overlayTitle || overlayDescription) && (
            <figcaption className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/85 via-black/35 to-transparent px-3 pb-3 pt-8 text-white z-10">
              {overlayTitle && (
                <div className="truncate text-sm font-semibold">
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
        hasOverlay={Boolean(overlayTitle || overlayDescription)}
        overlayDescription={overlayDescription}
        overlayTitle={overlayTitle}
        detailType={detailType}
        detailTags={detailTags}
        detailSections={detailSections}
        candidates={candidates}
        onSelectCandidate={candidates ? handleSelectCandidate : undefined}
        open={modalOpen}
        setOpen={setModalOpen}
      />
    </>
  );
};

export const SketchImage: ComponentFn = ({ element }) => {
  const props = p(element);
  const src = (props.src ?? props.url ?? "") as string;
  const alt = (props.alt ?? "") as string;
  const detailType = props.detailType as string | undefined;
  const [modalOpen, setModalOpen] = useState(false);

  if (!src) return null;

  return (
    <>
      <figure className="group w-[180px] shrink-0 overflow-hidden rounded-2xl bg-zinc-950/90">
        <MediaTransitionShell
          className="h-[240px] w-[180px]"
          loadingClassName="h-[240px] w-[180px] rounded-2xl object-cover"
        >
          {({ setReady }) => (
            <img
              className="block h-[240px] w-[180px] cursor-pointer rounded-2xl object-cover transition-all duration-300 hover:scale-[1.02] hover:border-2 hover:border-white/25 hover:shadow-[0_0_18px_rgba(56,189,248,0.35)]"
              src={src}
              alt={alt}
              loading="lazy"
              onLoad={() => setReady(true)}
              onClick={() => setModalOpen(true)}
              role="button"
              tabIndex={0}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  setModalOpen(true);
                }
              }}
            />
          )}
        </MediaTransitionShell>
      </figure>
      <ImageDetailModal
        src={src}
        hasOverlay={false}
        detailType={detailType}
        open={modalOpen}
        setOpen={setModalOpen}
      />
    </>
  );
};

export function SketchGalleryCard({ item }: { item: SketchGalleryItem }) {
  const [modalOpen, setModalOpen] = useState(false);
  const [displaySrc, setDisplaySrc] = useState(item.src);
  useEffect(() => {
    setDisplaySrc(item.src);
  }, [item.src]);
  const sendSelection = useSendCandidateSelection(setModalOpen);

  const handleSelectCandidate = item.candidates?.length
    ? (candidate: ImageCandidate) => {
        setDisplaySrc(candidate.src);
        sendSelection(candidate);
      }
    : undefined;

  return (
    <>
      <TiltCard className="w-[146px] h-[190px] shrink-0">
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
          <MediaTransitionShell
            className="absolute inset-0"
            loadingClassName="h-full w-full object-cover"
          >
            {({ setReady }) => (
              <img
                className="block w-full h-full object-cover select-none"
                src={displaySrc}
                alt={item.alt}
                loading="lazy"
                draggable={false}
                onLoad={() => setReady(true)}
              />
            )}
          </MediaTransitionShell>
          <figcaption className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/90 via-black/45 to-transparent px-3 pb-3 pt-8 text-white z-10">
            <div className="truncate text-[14px] font-medium">{item.title}</div>
            {item.description && (
              <div className="mt-1 line-clamp-2 text-[11px] leading-4 text-white/70">
                {item.description}
              </div>
            )}
          </figcaption>
        </figure>
      </TiltCard>
      <ImageDetailModal
        src={displaySrc}
        hasOverlay={true}
        overlayTitle={item.title}
        detailType={item.type}
        overlayDescription={item.description}
        candidates={item.candidates}
        onSelectCandidate={handleSelectCandidate}
        open={modalOpen}
        setOpen={setModalOpen}
      />
    </>
  );
}

export function KeyframeGalleryCard({ item }: { item: KeyframeGalleryItem }) {
  const { parseMediaUrl } = useSpecRendererContext();
  const [modalOpen, setModalOpen] = useState(false);
  const isVideo = Boolean(item.videoSrc);
  const isUnresolved = (src?: string) =>
    src ? Boolean(parseMediaUrl?.(src)) && !src.startsWith("http") : false;

  const videoResolved =
    item.videoSrc && !isUnresolved(item.videoSrc) ? item.videoSrc : undefined;
  const imageResolved = !isUnresolved(item.imageSrc) ? item.imageSrc : undefined;

  return (
    <>
      <div className="w-[148px] shrink-0">
        <figure
          className="group relative overflow-hidden rounded-[18px] bg-zinc-950/95 shadow-[0_12px_32px_rgba(0,0,0,0.35)]"
          role="button"
          tabIndex={0}
          onClick={() =>
            videoResolved || imageResolved ? setModalOpen(true) : undefined
          }
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              if (videoResolved || imageResolved) setModalOpen(true);
            }
          }}
        >
          <MediaTransitionShell
            className="h-[198px] w-[148px]"
            loadingClassName="h-[198px] w-[148px] rounded-[18px] object-cover"
          >
            {({ setReady }) =>
              isVideo && videoResolved ? (
                <video
                  className="block h-[198px] w-[148px] rounded-[18px] object-cover transition-all duration-300 hover:scale-[1.03] hover:border-2 hover:border-white/25 hover:shadow-[0_0_18px_rgba(56,189,248,0.35)]"
                  src={videoResolved}
                  muted
                  playsInline
                  preload="metadata"
                  onLoadedData={() => setReady(true)}
                />
              ) : imageResolved ? (
                <img
                  className="block h-[198px] w-[148px] rounded-[18px] object-cover transition-all duration-300 hover:scale-[1.03] hover:border-2 hover:border-white/25 hover:shadow-[0_0_18px_rgba(56,189,248,0.35)]"
                  src={imageResolved}
                  alt={item.alt}
                  loading="lazy"
                  onLoad={() => setReady(true)}
                />
              ) : null
            }
          </MediaTransitionShell>
          <figcaption className="pointer-events-none absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/85 via-black/35 to-transparent px-3 pb-3 pt-8 text-white">
            <div className="flex items-center gap-1.5 truncate text-[13px] font-medium text-white/92">
              <Play className="size-3.5 shrink-0 fill-white text-white" />
              <span className="truncate">{item.title}</span>
            </div>
          </figcaption>
        </figure>
      </div>
      <VideoDetailModal
        src={videoResolved}
        poster={
          imageResolved && imageResolved !== videoResolved
            ? imageResolved
            : undefined
        }
        title={item.title}
        description={item.description}
        sections={
          item.description
            ? [{ title: "【视频描述】", body: item.description }]
            : []
        }
        open={modalOpen}
        setOpen={setModalOpen}
      />
    </>
  );
}

export const StoryText: ComponentFn = ({ element }) => {
  const props = p(element);
  const content = coerceText(props.content ?? props.text);
  return (
    <p className="text-[15px] leading-8 text-foreground/85 whitespace-pre-wrap">
      {content}
    </p>
  );
};

export const StoryHeading: ComponentFn = ({ element }) => {
  const props = p(element);
  const content = coerceText(props.content ?? props.text);
  const level = Math.min(Math.max(Number(props.level) || 3, 1), 6);
  const sizes: Record<number, string> = {
    1: "text-3xl",
    2: "text-2xl",
    3: "text-xl",
    4: "text-lg",
    5: "text-base",
    6: "text-sm",
  };
  return (
    <div
      className={`font-semibold tracking-tight text-foreground ${sizes[level] ?? "text-lg"}`}
      role="heading"
      aria-level={level}
    >
      {content}
    </div>
  );
};

// ─── buildFlowRenderer ─────────────────────────────────────────

type CharacterInfo = {
  imageId: string;
  name: string;
  description: string;
  badge: string | null;
  badgeVariant: string;
};

function findElementsByType(
  spec: Spec,
  elementId: string,
  targetTypes: Set<string>,
): string[] {
  const el = spec.elements[elementId];
  if (!el) return [];
  if (targetTypes.has(el.type)) return [elementId];
  return (el.children ?? []).flatMap((id) =>
    findElementsByType(spec, id, targetTypes),
  );
}

function buildParentMap(spec: Spec): Map<string, string> {
  const map = new Map<string, string>();
  for (const [id, el] of Object.entries(spec.elements)) {
    for (const childId of el.children ?? []) {
      map.set(childId, id);
    }
  }
  return map;
}

function extractCharacterInfo(
  spec: Spec,
  imageId: string,
  parentMap: Map<string, string>,
): CharacterInfo {
  const imgEl = spec.elements[imageId];
  const imgProps = imgEl ? p(imgEl) : {};
  const name = (imgProps.overlayTitle ?? imgProps.alt ?? "") as string;

  let description = "";
  let badge: string | null = null;
  let badgeVariant = "info";

  let cardId: string | null = null;
  let current = imageId;
  while (parentMap.has(current)) {
    current = parentMap.get(current)!;
    const el = spec.elements[current];
    if (el?.type === "Card") {
      cardId = current;
      break;
    }
  }

  if (cardId) {
    const cardEl = spec.elements[cardId];
    const cardProps = cardEl ? p(cardEl) : {};

    if (!description && cardProps.description)
      description = cardProps.description as string;

    const queue = [...(cardEl?.children ?? [])];
    while (queue.length > 0) {
      const id = queue.shift()!;
      if (id === imageId) continue;
      const el = spec.elements[id];
      if (!el) continue;
      const elProps = p(el);
      if (el.type === "Text" && !description) {
        description = (elProps.content ?? elProps.text ?? "") as string;
      }
      if (el.type === "Badge" && !badge) {
        badge = (elProps.label ?? elProps.text ?? "") as string;
        badgeVariant = (elProps.variant ?? "info") as string;
      }
      queue.push(...(el.children ?? []));
    }
  }

  return { imageId, name, description, badge, badgeVariant };
}

export function buildFlowRenderer(
  overrides: Partial<Record<string, ComponentFn>> = {},
): RootRendererFn {
  return ({ spec, context }) => {
    const showcaseContext: RenderContext = {
      ...context,
      components: { ...context.components, ...overrides },
    };

    const targetTypes = new Set(Object.keys(overrides));
    const imageIds =
      targetTypes.size > 0
        ? findElementsByType(spec, spec.root, targetTypes)
        : [];

    if (imageIds.length === 0) {
      return <DefaultRootRenderer spec={spec} context={showcaseContext} />;
    }

    const parentMap = buildParentMap(spec);
    const characters = imageIds.map((id) =>
      extractCharacterInfo(spec, id, parentMap),
    );

    const enrichedElements = { ...spec.elements };
    for (const char of characters) {
      const el = enrichedElements[char.imageId];
      if (!el) continue;
      const existingProps = el.props ?? {};
      enrichedElements[char.imageId] = {
        ...el,
        props: {
          ...existingProps,
          overlayTitle: existingProps.overlayTitle ?? char.name,
          overlayDescription:
            existingProps.overlayDescription ?? char.description,
          detailTags:
            existingProps.detailTags ?? (char.badge ? [char.badge] : undefined),
        },
      };
    }
    const enrichedSpec = { ...spec, elements: enrichedElements };
    const enrichedContext: RenderContext = {
      ...showcaseContext,
      spec: enrichedSpec,
    };

    return (
      <div className="flex max-w-full flex-wrap items-start gap-3 pb-1">
        {characters.map((char) => (
          <div
            key={char.imageId}
            className="flex flex-col items-center shrink-0"
          >
            <RenderNode context={enrichedContext} elementId={char.imageId} />
            {char.badge && (
              <span
                className={`jr-badge jr-badge--${char.badgeVariant} mt-2 text-[10px]`}
              >
                {char.badge}
              </span>
            )}
          </div>
        ))}
      </div>
    );
  };
}
