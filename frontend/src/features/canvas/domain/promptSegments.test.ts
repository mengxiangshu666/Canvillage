// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from 'vitest';

import {
  joinPromptSegmentChunks,
  parsePromptSegment,
  segmentBody,
  segmentLabel,
  splitPromptSegmentChunks,
  stripSegmentBrackets,
} from './promptSegments';

/**
 * 段级解析的两种写法都要认。
 *
 * 服务端产的规范形态是 `[标签：正文]`，但**提示词是用户可以随手改的文本**，写了几轮
 * 之后就会出现 `[运镜轨迹] 正文` 这种「方括号当标签」的写法。解析只服务于显示与整段
 * 替换，认不出来就什么都显示不出来 —— 这正是最需要说清楚的时候。
 */
describe('splitPromptSegmentChunks', () => {
  it('只在至少一侧带空白的加号处切段', () => {
    expect(splitPromptSegmentChunks('[画面构图] 门 + [技术参数] 35mm')).toEqual([
      '[画面构图] 门',
      '[技术参数] 35mm',
    ]);
    expect(splitPromptSegmentChunks('[画面构图] 门 +[技术参数] 35mm')).toHaveLength(2);
    expect(splitPromptSegmentChunks('[画面构图] 门+ [技术参数] 35mm')).toHaveLength(2);
  });

  it('段内容里的「A+B」不当分隔符', () => {
    expect(splitPromptSegmentChunks('[光影几何：冷蓝A+B主调]')).toEqual(['[光影几何：冷蓝A+B主调]']);
  });

  it('多人角色卡和段内加号不拆成额外段落', () => {
    const characters = '[角色卡/主体描述：[阿波: 红夹克] + [小雀: 蓝背包]]';
    expect(splitPromptSegmentChunks(`[画面构图：双人中景] + ${characters} + [光影：暖主光 + 冷补光]`)).toEqual([
      '[画面构图：双人中景]', characters, '[光影：暖主光 + 冷补光]',
    ]);
    expect(parsePromptSegment(characters)).toEqual({ label: '角色卡/主体描述', body: '[阿波: 红夹克] + [小雀: 蓝背包]' });
  });

  it('空段不产生占位空串', () => {
    expect(splitPromptSegmentChunks('[画面构图] 门 +  + [技术参数] 35mm')).toHaveLength(2);
    expect(splitPromptSegmentChunks('   ')).toEqual([]);
    expect(splitPromptSegmentChunks(null)).toEqual([]);
  });
});

describe('stripSegmentBrackets', () => {
  it('只在两端成对时剥壳', () => {
    expect(stripSegmentBrackets('[运镜轨迹：固定机位]')).toBe('运镜轨迹：固定机位');
    expect(stripSegmentBrackets('[运镜轨迹] 固定机位')).toBe('[运镜轨迹] 固定机位');
    expect(stripSegmentBrackets('运镜轨迹] 固定机位')).toBe('运镜轨迹] 固定机位');
  });

  it('保留角色卡内层的方括号', () => {
    expect(stripSegmentBrackets('[[沈昭昭_现代: 28岁]]')).toBe('[沈昭昭_现代: 28岁]');
  });
});

describe('parsePromptSegment', () => {
  it('认 `[标签：正文]`：标签在方括号内、冒号之前', () => {
    expect(parsePromptSegment('[明确的摄影机运镜：极慢推进]')).toEqual({
      label: '明确的摄影机运镜',
      body: '极慢推进',
    });
  });

  it('认 `[标签] 正文`：方括号本身是标签', () => {
    expect(parsePromptSegment('[运镜轨迹] 固定机位，极慢微推')).toEqual({
      label: '运镜轨迹',
      body: '固定机位，极慢微推',
    });
  });

  it('认 `[标签]：正文`：括号后的冒号也吃掉', () => {
    expect(parsePromptSegment('[运镜轨迹]：固定机位')).toEqual({
      label: '运镜轨迹',
      body: '固定机位',
    });
  });

  it('认没壳的 `标签：正文` 与纯正文', () => {
    expect(parsePromptSegment('运镜：缓慢推近')).toEqual({ label: '运镜', body: '缓慢推近' });
    expect(parsePromptSegment('固定机位')).toEqual({ label: '', body: '固定机位' });
  });

  it('缺右括号时不抛错，整块当正文用', () => {
    expect(parsePromptSegment('[运镜轨迹 固定机位')).toEqual({
      label: '',
      body: '[运镜轨迹 固定机位',
    });
  });
});

describe('segmentLabel / segmentBody', () => {
  it('取标签与正文', () => {
    expect(segmentLabel('[运镜轨迹] 固定机位')).toBe('运镜轨迹');
    expect(segmentBody('[运镜轨迹] 固定机位')).toBe('固定机位');
  });
});

describe('joinPromptSegmentChunks', () => {
  it('用 ` + ` 重连并丢掉空块', () => {
    expect(joinPromptSegmentChunks([' [a] x ', '', '[b] y'])).toBe('[a] x + [b] y');
    expect(joinPromptSegmentChunks([])).toBe('');
  });
});
