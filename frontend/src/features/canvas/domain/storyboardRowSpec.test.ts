// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from 'vitest';

import type { VideoStoryRow } from '@/features/canvas/domain/canvasNodes';
import {
  STORYBOARD_MAX_DURATION_SECONDS,
  STORYBOARD_MIN_DURATION_SECONDS,
  clampStoryboardDurationSeconds,
  parseStoryboardCharactersText,
  storyboardCharactersText,
} from '@/features/canvas/domain/storyboardRowSpec';

describe('clampStoryboardDurationSeconds', () => {
  it('accepts numbers and "3.4s" style strings', () => {
    expect(clampStoryboardDurationSeconds(3.4)).toBe(3.4);
    expect(clampStoryboardDurationSeconds('3.4s')).toBe(3.4);
    expect(clampStoryboardDurationSeconds(' 2 ')).toBe(2);
  });

  it('clamps to the 1–15 second band', () => {
    expect(clampStoryboardDurationSeconds(0.2)).toBe(STORYBOARD_MIN_DURATION_SECONDS);
    expect(clampStoryboardDurationSeconds(90)).toBe(STORYBOARD_MAX_DURATION_SECONDS);
  });

  it('returns null for anything unparseable instead of guessing', () => {
    expect(clampStoryboardDurationSeconds('')).toBeNull();
    expect(clampStoryboardDurationSeconds('约三秒')).toBeNull();
    expect(clampStoryboardDurationSeconds(null)).toBeNull();
    expect(clampStoryboardDurationSeconds(Number.NaN)).toBeNull();
  });
});

describe('characters column helpers', () => {
  it('joins arrays into a readable cell and reads back a string column', () => {
    const row = { characters: ['村长', '小满'] } as VideoStoryRow;
    expect(storyboardCharactersText(row)).toBe('村长、小满');
    expect(storyboardCharactersText({ characters: '村长' } as VideoStoryRow)).toBe('村长');
    expect(storyboardCharactersText({} as VideoStoryRow)).toBe('');
  });

  it('parses edits back into a de-duplicated array', () => {
    expect(parseStoryboardCharactersText('村长、小满, 村长；阿黄')).toEqual(['村长', '小满', '阿黄']);
    expect(parseStoryboardCharactersText('  ')).toEqual([]);
  });
});
