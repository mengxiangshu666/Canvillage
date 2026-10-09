// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from 'vitest';

import { normalizeVideoStoryRows } from '@/features/canvas/application/videoStoryNormalizer';

/**
 * 分镜行的解析口径。锁两件事：
 * - LibTV 那批新列（角色 / 微表情 / 场景标签 / 台词 / 剧情描述）能从后端原文认出来；
 * - 时长数值列一定落在 1–15，且老 payload 只有 `duration` 时数值列也不为空。
 */
describe('normalizeVideoStoryRows', () => {
  it('解析 LibTV 新增列与中文别名', () => {
    const rows = normalizeVideoStoryRows({
      video_story: {
        shots: [
          {
            shot: 1,
            时长: '3.5s',
            剧情描述: '村长第一次走进画布',
            角色: ['村长', '枝枝'],
            动作: '推门而入',
            微表情: '强装镇定',
            场景: '黄昏的院子里',
            台词: '这画布，得有无限大。',
          },
        ],
      },
    });

    expect(rows).toHaveLength(1);
    expect(rows[0].durationSeconds).toBe(3.5);
    expect(rows[0].plotDescription).toBe('村长第一次走进画布');
    expect(rows[0].characters).toEqual(['村长', '枝枝']);
    expect(rows[0].characterAction).toBe('推门而入');
    expect(rows[0].emotion).toBe('强装镇定');
    expect(rows[0].sceneTags).toBe('黄昏的院子里');
    expect(rows[0].dialogue).toBe('这画布，得有无限大。');
  });

  it('时长数值列被夹到 1–15，且角色是单字符串时也收进数组', () => {
    const rows = normalizeVideoStoryRows({
      shots: [
        { durationSeconds: 40, characters: '村长' },
        { duration_seconds: 0.2 },
      ],
    });

    expect(rows[0].durationSeconds).toBe(15);
    expect(rows[0].characters).toEqual(['村长']);
    expect(rows[1].durationSeconds).toBe(1);
  });

  it('老 payload 只有时长原文时，数值列不空', () => {
    const rows = normalizeVideoStoryRows({ shots: [{ duration: '4.0s' }] });
    expect(rows[0].duration).toBe('4.0s');
    expect(rows[0].durationSeconds).toBe(4);
  });
});
