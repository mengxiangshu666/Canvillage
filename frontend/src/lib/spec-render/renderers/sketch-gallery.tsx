"use client";

import type { RootRendererFn } from "../types";
import { p } from "../types";
import { renderRootWithOverrides } from "../core";
import { extractSketchGalleryItems, type SketchTag } from "../spec-helpers";
import { SketchImage, SketchGalleryCard } from "../showcase-components";
import { PreviewableImageFigure } from "../media-utils";
import { AlertList } from "./shared";

export const sketchGalleryRenderer: RootRendererFn = ({ spec, context }) => {
  const rootElement = spec.elements[spec.root];
  const rootProps = rootElement ? p(rootElement) : {};
  let title = rootProps.title as string | undefined;
  let description = rootProps.description as string | undefined;

  if (!title) {
    for (const el of Object.values(spec.elements)) {
      if (el.type === "Card" && el.props?.title) {
        title = el.props.title as string;
        description =
          description ?? (el.props.description as string | undefined);
        break;
      }
    }
  }

  const items = extractSketchGalleryItems(spec);

  const galleryImages: Array<{ src: string; alt?: string }> = [];
  const alerts: Array<{
    variant?: string;
    title?: string;
    message?: string;
  }> = [];
  const kvItems: Array<{ key: string; value: string }> = [];
  for (const el of Object.values(spec.elements)) {
    if (el.type === "Gallery" && Array.isArray(el.props?.images)) {
      for (const img of el.props.images as Array<{
        src?: string;
        alt?: string;
      }>) {
        if (img?.src) galleryImages.push({ src: img.src, alt: img.alt });
      }
    }
    if (el.type === "Alert") {
      alerts.push({
        variant: el.props?.variant as string | undefined,
        title: el.props?.title as string | undefined,
        message: el.props?.message as string | undefined,
      });
    }
    if (el.type === "KeyValue" && Array.isArray(el.props?.items)) {
      for (const item of el.props.items as Array<{
        key?: string;
        value?: string;
      }>) {
        if (item?.key)
          kvItems.push({ key: item.key, value: item.value ?? "" });
      }
    }
  }

  if (items.length === 0 && galleryImages.length === 0) {
    return (
      <div className="max-w-full overflow-x-auto rounded-3xl bg-muted/50 p-3">
        {renderRootWithOverrides(spec, context, { Image: SketchImage })}
      </div>
    );
  }

  return (
    <div className="max-w-full rounded-[28px] bg-transparent px-5 py-4 text-foreground">
      <div className="mb-4 min-w-0">
        <div className="text-base font-medium">{description || title}</div>
        {title && description && (
          <div className="mt-1 truncate text-xs text-muted-foreground">
            {title}
          </div>
        )}
      </div>

      {kvItems.length > 0 && (
        <div className="mb-4 flex flex-wrap gap-x-6 gap-y-1 text-sm">
          {kvItems.map((item, i) => (
            <span key={i} className="text-muted-foreground">
              <span className="text-muted-foreground/70">{item.key}：</span>
              <span className="text-foreground/80">{item.value}</span>
            </span>
          ))}
        </div>
      )}

      <AlertList alerts={alerts} />

      {items.length > 0 &&
        (() => {
          const seen = new Map<string, SketchTag>();
          for (const item of items) {
            for (const tag of item.tags ?? []) {
              if (!seen.has(tag.label)) seen.set(tag.label, tag);
            }
          }
          const legend = Array.from(seen.values());
          if (legend.length === 0) return null;
          return (
            <div className="mb-3 flex flex-wrap gap-2">
              {legend.map((tag, i) => (
                <span
                  key={i}
                  className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px] font-medium"
                  style={{
                    backgroundColor: tag.color
                      ? `${tag.color}18`
                      : "var(--muted)",
                    color: tag.color ?? "var(--muted-foreground)",
                  }}
                >
                  <span
                    className="inline-block size-2 rounded-full"
                    style={{
                      backgroundColor:
                        tag.color ?? "var(--muted-foreground)",
                    }}
                  />
                  {tag.label}
                </span>
              ))}
            </div>
          );
        })()}

      {items.length > 0 && (
        <div className="jr-sketch-gallery-grid flex max-w-full flex-wrap gap-3">
          {items.map((item) => (
            <SketchGalleryCard key={item.id} item={item} />
          ))}
        </div>
      )}

      {galleryImages.length > 0 && (
        <div className="jr-sketch-gallery-grid flex max-w-full flex-wrap gap-3">
          {galleryImages.map((img, i) => (
            <PreviewableImageFigure
              key={i}
              src={img.src}
              alt={img.alt}
              overlayTitle={img.alt}
              figureClassName="w-[148px] h-[198px] shrink-0 overflow-hidden rounded-[18px]"
            />
          ))}
        </div>
      )}
    </div>
  );
};
