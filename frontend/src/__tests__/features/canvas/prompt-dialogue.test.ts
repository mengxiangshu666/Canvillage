// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from 'vitest';

import {
  dialogueDubbingCacheKey,
  extractPromptDialogue,
} from '@/features/canvas/domain/promptDialogue';

describe('extractPromptDialogue', () => {
  it('extracts the four quoted forms the backend also recognizes', () => {
    expect(extractPromptDialogue('她说：“你终于来了。”')).toEqual(['你终于来了。']);
    expect(extractPromptDialogue('他说：「我们走吧」')).toEqual(['我们走吧']);
    expect(extractPromptDialogue('他喊：『站住』')).toEqual(['站住']);
    expect(extractPromptDialogue('她低声说："别动"')).toEqual(['别动']);
  });

  it('extracts the bracket slot taught by the text-node template', () => {
    expect(
      extractPromptDialogue('[对话台词与语气：你终于来了，带着疲惫] + [画面构图：近景]'),
    ).toEqual(['你终于来了，带着疲惫']);
    expect(extractPromptDialogue('[台词：我们走吧]')).toEqual(['我们走吧']);
    expect(extractPromptDialogue('[对白：站住]')).toEqual(['站住']);
  });

  it('extracts unquoted clause forms up to the first punctuation', () => {
    expect(extractPromptDialogue('台词：你终于来了，她抬头看向门口')).toEqual(['你终于来了']);
    expect(extractPromptDialogue('她说道：我们走吧。镜头推近')).toEqual(['我们走吧']);
  });

  it('keeps source order across mixed forms and de-duplicates repeats', () => {
    const prompt = [
      '女孩看向门口，用普通话说：“你终于来了。”',
      '男孩回答：“我们走吧。”',
      '女孩重复：“我们走吧。”',
    ].join('\n');
    expect(extractPromptDialogue(prompt)).toEqual(['你终于来了。', '我们走吧。']);
  });

  it('treats explicit no-dialogue placeholders as empty', () => {
    expect(extractPromptDialogue('[对话台词与语气：无] + [画面构图：近景]')).toEqual([]);
    expect(extractPromptDialogue('台词：没有')).toEqual([]);
    expect(extractPromptDialogue('')).toEqual([]);
    expect(extractPromptDialogue(null)).toEqual([]);
    expect(extractPromptDialogue(undefined)).toEqual([]);
  });

  it('does not treat ordinary visual prose as dialogue', () => {
    expect(
      extractPromptDialogue('[画面构图：近景特写] + [主体动作：她走向门口] + [时长：4.0s]'),
    ).toEqual([]);
  });

  it('strips wrapping quotes that appear inside a slot', () => {
    expect(extractPromptDialogue('[台词：“你终于来了”]')).toEqual(['你终于来了']);
  });

  it('builds a cache key from lines and voice', () => {
    expect(dialogueDubbingCacheKey(['a', 'b'], 'project_narrator')).toBe(
      dialogueDubbingCacheKey(['a', 'b'], 'project_narrator'),
    );
    expect(dialogueDubbingCacheKey(['a'], 'project_narrator')).not.toBe(
      dialogueDubbingCacheKey(['a'], 'user_custom:fv_1'),
    );
  });
});
