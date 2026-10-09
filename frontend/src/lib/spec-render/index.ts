export { SpecRenderer } from "./SpecRenderer";
export {
  SpecRendererProvider,
  useSpecRendererContext,
  LOADING_VIDEO_URL,
} from "./context";
export type { SpecRendererContextValue } from "./context";

export type {
  Spec,
  UIElement,
  ContentSegment,
} from "./spec";
export {
  normalizeSpecType,
  isValidSpec,
  isNestedSpec,
  normalizeSpec,
  flattenNestedSpec,
} from "./spec";

export type { RenderContext, RootRendererFn, ComponentFn, Props } from "./types";
export { p, coerceText } from "./types";

export { DefaultRootRenderer, renderElement, renderRootWithOverrides, renderElementWithOverrides } from "./core";
export { SPEC_RENDERERS } from "./root-renderers";

export type { ImageCandidate } from "./modals/image-detail-modal";
export { ImageDetailModal } from "./modals/image-detail-modal";
export type { VideoDetailSection } from "./modals/video-detail-modal";
export { VideoDetailModal } from "./modals/video-detail-modal";
