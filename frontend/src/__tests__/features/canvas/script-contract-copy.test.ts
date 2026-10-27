// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from 'vitest';

import { scriptContractRuleTitle } from '@/features/canvas/nodes/script/scriptContractCopy';

describe('scriptContractRuleTitle', () => {
  it('translates the Dream-derived coverage and anchor rules', () => {
    expect(scriptContractRuleTitle('script.prompt.sound_coverage.v1')).toBe(
      '音效没有写进视频提示词',
    );
    expect(scriptContractRuleTitle('script.prompt.state_coverage.v1')).toBe(
      '道具状态没有写进视频提示词',
    );
    expect(scriptContractRuleTitle('script.continuity.static_anchor.v1')).toBe(
      '接力锚点疑似静止',
    );
  });

  it('keeps unknown rule ids visible for diagnosis', () => {
    expect(scriptContractRuleTitle('script.future.rule.v1')).toBe('script.future.rule.v1');
  });
});
