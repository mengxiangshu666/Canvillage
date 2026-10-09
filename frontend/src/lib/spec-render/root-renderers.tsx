"use client";

import type { RootRendererFn } from "./types";
import { characterShowcaseRenderer } from "./renderers/character-showcase";
import { keyframeVideoRenderer } from "./renderers/keyframe-video";
import { sketchGalleryRenderer } from "./renderers/sketch-gallery";
import { episodeBreakdownRenderer } from "./renderers/episode-breakdown";
import { scriptOverviewRenderer } from "./renderers/script-overview";
import { longformStoryRenderer } from "./renderers/longform-story";

export const SPEC_RENDERERS: Record<string, RootRendererFn> = {
  character_showcase: characterShowcaseRenderer,
  keyframe_video: keyframeVideoRenderer,
  sketch_gallery: sketchGalleryRenderer,
  episode_breakdown: episodeBreakdownRenderer,
  script_overview: scriptOverviewRenderer,
  longform_story: longformStoryRenderer,
};
