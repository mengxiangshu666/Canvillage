"use client";

import { useEffect, useState } from "react";
import type { RootRendererFn } from "../types";
import { p } from "../types";
import { DefaultRootRenderer } from "../core";
import {
  extractKeyframeGalleryItems,
  type KeyframeGalleryItem,
} from "../spec-helpers";
import { KeyframeGalleryCard } from "../showcase-components";
import { useSpecRendererContext } from "../context";

function useResolvedItems(
  items: KeyframeGalleryItem[],
): KeyframeGalleryItem[] {
  const { resolveMediaUrl, parseMediaUrl } = useSpecRendererContext();
  const itemsKey = items
    .map((i) => `${i.id}:${i.videoSrc}:${i.imageSrc}`)
    .join("|");
  const [resolved, setResolved] = useState(items);

  useEffect(() => {
    if (!resolveMediaUrl || !parseMediaUrl) {
      setResolved(items);
      return;
    }
    const needResolve = items.some(
      (item) =>
        parseMediaUrl(item.videoSrc ?? "") || parseMediaUrl(item.imageSrc),
    );
    if (!needResolve) {
      setResolved(items);
      return;
    }

    let cancelled = false;
    Promise.all(
      items.map(async (item) => {
        const [videoUrl, imageUrl] = await Promise.all([
          item.videoSrc ? resolveMediaUrl(item.videoSrc) : Promise.resolve(item.videoSrc),
          resolveMediaUrl(item.imageSrc),
        ]);
        return {
          ...item,
          videoSrc: videoUrl ?? item.videoSrc,
          imageSrc: imageUrl ?? item.imageSrc,
        };
      }),
    ).then((result) => {
      if (!cancelled) setResolved(result);
    });

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [itemsKey, resolveMediaUrl, parseMediaUrl]);

  return resolved;
}

export const keyframeVideoRenderer: RootRendererFn = ({ spec, context }) => {
  const rootElement = spec.elements[spec.root];
  const rootProps = rootElement ? p(rootElement) : {};
  const title = rootProps.title as string | undefined;
  const description = rootProps.description as string | undefined;
  const rawItems = extractKeyframeGalleryItems(spec);
  const items = useResolvedItems(rawItems);

  if (items.length === 0) {
    return <DefaultRootRenderer spec={spec} context={context} />;
  }

  return (
    <div className="max-w-full rounded-[28px] bg-transparent px-5 py-4 text-foreground">
      <div className="mb-4 text-base font-medium">
        {title || description}
      </div>
      {title && description && (
        <div className="mb-3 text-sm text-muted-foreground">{description}</div>
      )}
      <div className="flex max-w-full flex-wrap gap-3">
        {items.map((item) => (
          <KeyframeGalleryCard key={item.id} item={item} />
        ))}
      </div>
    </div>
  );
};
