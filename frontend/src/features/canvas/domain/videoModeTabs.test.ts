// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from 'vitest';

import { listVisibleVideoModeTabs, resolveActiveVideoModeTab } from './videoModeTabs';

describe('video mode tab presentation', () => {
  it('keeps the only v2v mode visible for Kacang Kling V3 Omni', () => {
    const tabs = listVisibleVideoModeTabs({
      family: 'kacang-kling-v2v',
      supportedModes: ['videoEdit'],
      upstreamVideoCount: 0,
    });

    expect(tabs).toEqual([
      { key: 'videoEdit', labelKey: 'node.videoNode.tabs.videoEdit' },
    ]);
    expect(resolveActiveVideoModeTab({ value: 'textToVideo', tabs })).toEqual(tabs[0]);
  });

  it('keeps legacy family tabs when the catalog omitted the mode field', () => {
    const tabs = listVisibleVideoModeTabs({
      family: 'generic',
      upstreamVideoCount: 0,
    });

    expect(resolveActiveVideoModeTab({ value: 'textToVideo', tabs }).labelKey).toBe(
      'node.videoNode.tabs.textToVideo',
    );
  });

  it('shows no mode tabs when the upstream explicitly declares an empty list', () => {
    expect(listVisibleVideoModeTabs({
      family: 'seedance-2',
      supportedModes: [],
      upstreamVideoCount: 0,
    })).toEqual([]);
  });
});
