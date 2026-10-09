"use client";

import { useState, useEffect, useRef } from "react";
import type { Spec, UIElement } from "./spec";
import type { RenderContext } from "./types";
import { SPEC_RENDERERS } from "./root-renderers";
import { DefaultRootRenderer } from "./core";
import { useSpecRendererContext } from "./context";

const URL_PROPS = ["src", "url", "poster"] as const;

function collectUrl(
  url: unknown,
  map: Map<string, string>,
  parseMediaUrl: (src: string) => string | null,
) {
  if (typeof url === "string" && parseMediaUrl(url)) {
    map.set(url, url);
  }
}

async function resolveSpecUrls(
  spec: Spec,
  resolveMediaUrl: (src: string) => Promise<string>,
  parseMediaUrl: (src: string) => string | null,
): Promise<Spec> {
  const urlsToResolve = new Map<string, string>();

  for (const el of Object.values(spec.elements)) {
    if (!el.props) continue;

    for (const prop of URL_PROPS) {
      collectUrl(el.props[prop], urlsToResolve, parseMediaUrl);
    }
    if (Array.isArray(el.props.images)) {
      for (const img of el.props.images as Array<Record<string, unknown>>) {
        if (img && typeof img === "object") collectUrl(img.src, urlsToResolve, parseMediaUrl);
      }
    }
    if (Array.isArray(el.props.candidates)) {
      for (const c of el.props.candidates as Array<Record<string, unknown>>) {
        if (c && typeof c === "object") collectUrl(c.src, urlsToResolve, parseMediaUrl);
      }
    }
  }

  if (urlsToResolve.size === 0) return spec;

  const entries = Array.from(urlsToResolve.keys());
  const results = await Promise.allSettled(entries.map((u) => resolveMediaUrl(u)));
  for (let i = 0; i < entries.length; i++) {
    const r = results[i];
    if (r.status === "fulfilled") {
      urlsToResolve.set(entries[i], r.value);
    }
  }

  const replaceUrl = (val: unknown): unknown => {
    if (typeof val !== "string") return val;
    const resolved = urlsToResolve.get(val);
    return resolved && resolved !== val ? resolved : val;
  };

  const newElements: Record<string, UIElement> = {};
  for (const [key, el] of Object.entries(spec.elements)) {
    if (!el.props) {
      newElements[key] = el;
      continue;
    }

    let changed = false;
    const newProps = { ...el.props };

    for (const prop of URL_PROPS) {
      const val = newProps[prop];
      const resolved = replaceUrl(val);
      if (resolved !== val) {
        newProps[prop] = resolved;
        changed = true;
      }
    }

    if (Array.isArray(newProps.images)) {
      const newImages = (newProps.images as Array<Record<string, unknown>>).map((img) => {
        if (!img || typeof img !== "object") return img;
        const resolved = replaceUrl(img.src);
        if (resolved !== img.src) {
          changed = true;
          return { ...img, src: resolved };
        }
        return img;
      });
      if (changed) newProps.images = newImages;
    }

    if (Array.isArray(newProps.candidates)) {
      const newCandidates = (newProps.candidates as Array<Record<string, unknown>>).map((c) => {
        if (!c || typeof c !== "object") return c;
        const resolved = replaceUrl(c.src);
        if (resolved !== c.src) {
          changed = true;
          return { ...c, src: resolved };
        }
        return c;
      });
      if (changed) newProps.candidates = newCandidates;
    }

    newElements[key] = changed ? { ...el, props: newProps } : el;
  }

  return { ...spec, elements: newElements };
}

function detectSpecType(spec: Spec): string | undefined {
  const elements = Object.values(spec.elements);
  const rootEl = spec.elements[spec.root];
  if (!rootEl) return undefined;

  const typeCounts: Record<string, number> = {};
  for (const el of elements) {
    typeCounts[el.type] = (typeCounts[el.type] || 0) + 1;
  }
  const imageCount = typeCounts["Image"] ?? 0;
  const videoCount = typeCounts["Video"] ?? 0;
  const textCount = typeCounts["Text"] ?? 0;
  const listCount = typeCounts["List"] ?? 0;
  const cardCount = typeCounts["Card"] ?? 0;

  if (videoCount >= 1) return "keyframe_video";

  if (imageCount >= 2) {
    if (rootEl.type === "Stack") return "character_showcase";
    const hasOverlays = elements.some(
      (el) =>
        el.type === "Image" && (el.props?.overlayTitle || el.props?.overlayDescription),
    );
    if (hasOverlays) return "character_showcase";
    if (cardCount >= 2) return "sketch_gallery";
  }

  if (listCount > 0 && cardCount >= 1) return "episode_breakdown";
  if (cardCount >= 3) return "script_overview";
  if (textCount >= 3 && imageCount === 0 && videoCount === 0) return "longform_story";

  return undefined;
}

export function SpecRenderer({
  spec,
  mediaMaxWidth,
}: {
  spec: Spec;
  mediaMaxWidth?: number;
}) {
  const { resolveMediaUrl, parseMediaUrl } = useSpecRendererContext();
  const [resolvedSpec, setResolvedSpec] = useState<Spec>(spec);
  const specRef = useRef(spec);

  useEffect(() => {
    specRef.current = spec;

    if (!resolveMediaUrl || !parseMediaUrl) {
      setResolvedSpec(spec);
      return;
    }

    let cancelled = false;
    resolveSpecUrls(spec, resolveMediaUrl, parseMediaUrl).then((result) => {
      if (!cancelled && specRef.current === spec) {
        setResolvedSpec(result);
      }
    });
    return () => {
      cancelled = true;
    };
  }, [spec, resolveMediaUrl, parseMediaUrl]);

  const style =
    mediaMaxWidth && mediaMaxWidth > 0
      ? ({ "--jr-media-max-width": `${mediaMaxWidth}px` } as React.CSSProperties)
      : undefined;

  let rendererKey = resolvedSpec.type ?? resolvedSpec.root;
  if (rendererKey === "analysis_report") return null;

  if (!SPEC_RENDERERS[rendererKey]) {
    const detected = detectSpecType(resolvedSpec);
    if (detected) rendererKey = detected;
  }

  const context: RenderContext = {
    spec: resolvedSpec,
    rendererKey,
  };

  const Renderer = SPEC_RENDERERS[rendererKey] ?? DefaultRootRenderer;

  return (
    <div className="jr-spec" style={style}>
      <Renderer spec={resolvedSpec} context={context} />
    </div>
  );
}
