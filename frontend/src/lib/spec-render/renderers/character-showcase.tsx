"use client";

import type { Spec } from "../spec";
import type { RootRendererFn } from "../types";
import { ShowcaseImage, buildFlowRenderer } from "../showcase-components";

function expandGalleryToImages(spec: Spec): Spec {
  if (Object.values(spec.elements).some((el) => el.type === "Image")) return spec;

  const newElements = { ...spec.elements };
  let counter = 0;
  const imageIds: string[] = [];

  for (const el of Object.values(spec.elements)) {
    if (el.type !== "Gallery" || !Array.isArray(el.props?.images)) continue;
    for (const img of el.props.images as Array<{ src?: string; alt?: string }>) {
      if (!img?.src) continue;
      const fullAlt = img.alt ?? "";
      const name = fullAlt.replace(/[（(][^）)]*[）)]/g, "").trim();
      const role = fullAlt.match(/[（(]([^）)]*)[）)]/)?.[1] ?? "";
      const id = `_gi${counter++}`;
      newElements[id] = {
        type: "Image",
        props: {
          src: img.src,
          alt: fullAlt,
          overlayTitle: name || undefined,
          overlayDescription: role || undefined,
          fit: "cover",
        },
      };
      imageIds.push(id);
    }
  }

  if (imageIds.length === 0) return spec;

  const stackId = "_showcase_stack";
  newElements[stackId] = {
    type: "Stack",
    props: { direction: "row", gap: 12 },
    children: imageIds,
  };

  return { ...spec, root: stackId, elements: newElements };
}

const flowRenderer = buildFlowRenderer({ Image: ShowcaseImage });

export const characterShowcaseRenderer: RootRendererFn = (props) => {
  const expandedSpec = expandGalleryToImages(props.spec);
  if (expandedSpec !== props.spec) {
    return flowRenderer({
      spec: expandedSpec,
      context: { ...props.context, spec: expandedSpec },
    });
  }
  return flowRenderer(props);
};
