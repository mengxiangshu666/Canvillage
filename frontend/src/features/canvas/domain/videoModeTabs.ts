// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import type { VideoGenMode } from './canvasNodes';
import type { VideoModelFamily } from './videoCapabilityCompiler';

export interface VideoModeTab {
  key: VideoGenMode;
  labelKey: string;
}
export const VIDEO_MODE_TABS: readonly VideoModeTab[] = [
  { key: 'textToVideo', labelKey: 'node.videoNode.tabs.textToVideo' },
  { key: 'allReference', labelKey: 'node.videoNode.tabs.allReference' },
  { key: 'imageToVideo', labelKey: 'node.videoNode.tabs.imageToVideo' },
  { key: 'firstLastFrame', labelKey: 'node.videoNode.tabs.firstLastFrame' },
  { key: 'imageReference', labelKey: 'node.videoNode.tabs.imageReference' },
  { key: 'videoEdit', labelKey: 'node.videoNode.tabs.videoEdit' },
];

const HAPPYHORSE_TAB_ORDER: readonly VideoGenMode[] = [
  'textToVideo',
  'imageToVideo',
  'imageReference',
  'videoEdit',
];

function isDeclaredSupported(
  mode: VideoGenMode,
  supportedModes: readonly VideoGenMode[] | undefined,
): boolean {
  return supportedModes === undefined || supportedModes.includes(mode);
}

/**
 * The displayed mode list is a presentation of the actual capability contract.
 * In particular, a v2v-only provider must retain its sole `videoEdit` mode;
 * filtering it out creates an empty selector and a downstream render crash.
 */
export function listVisibleVideoModeTabs(input: {
  family: VideoModelFamily;
  supportedModes?: readonly VideoGenMode[];
  upstreamVideoCount: number;
}): VideoModeTab[] {
  const { family, supportedModes, upstreamVideoCount } = input;
  if (family === 'happyhorse') {
    const order = upstreamVideoCount > 0
      ? (['textToVideo', 'videoEdit'] as const)
      : HAPPYHORSE_TAB_ORDER;
    return order
      .filter((mode) => isDeclaredSupported(mode, supportedModes))
      .map((mode) => VIDEO_MODE_TABS.find((tab) => tab.key === mode))
      .filter((tab): tab is VideoModeTab => Boolean(tab))
      .map((tab) => tab.key === 'imageToVideo'
        ? { ...tab, labelKey: 'node.videoNode.tabs.firstFrame' }
        : tab);
  }

  // Do not hard-code one provider here: any explicitly declared video-edit
  // capability needs an affordance, including the Kacang Kling V3 v2v route.
  const exposesVideoEdit = supportedModes?.includes('videoEdit') === true;
  return VIDEO_MODE_TABS.filter(
    (tab) => (tab.key !== 'videoEdit' || exposesVideoEdit)
      && isDeclaredSupported(tab.key, supportedModes),
  );
}

/** Always provides a label-bearing fallback, even if a malformed gateway contract lists no modes. */
export function resolveActiveVideoModeTab(input: {
  value: VideoGenMode;
  tabs: readonly VideoModeTab[];
}): VideoModeTab {
  return input.tabs.find((tab) => tab.key === input.value)
    ?? input.tabs[0]
    ?? VIDEO_MODE_TABS[0];
}
