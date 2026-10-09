import { describe, expect, it } from 'vitest';
import { scriptGenerationDuration, scriptGenerationPrompt, type ScriptVideoModelCapabilities } from './scriptVideoDuration';

describe('script generation duration', () => {
  it('时长段含内层节奏说明时不把半截正文重复拼成尾注', () => {
    const prompt = '[运镜：固定] + [动作：阿波伸手 + 小雀接住] + [环境：暖主光 + 冷补光] + [音效：风声] + [台词：无] + [时长：2s，节奏[伸手 + 接住]，切点继续移动]\n可见状态接力：结束：双手接住';
    const result = scriptGenerationPrompt(prompt, 2, 5);
    expect(result).toContain('[时长：5s，节奏[伸手 + 接住]，切点继续移动');
    expect(result.match(/切点继续移动/g)).toHaveLength(1);
    expect(result).toContain('\n可见状态接力：结束：双手接住');
    expect(result).toContain('[动作：阿波伸手 + 小雀接住]');
  });
  it.each<[number, ScriptVideoModelCapabilities | null, number | null]>([
    [2, { durationOptions: [5, 10, 15] }, 5],
    [5.1, { durationOptions: [5, 10, 15] }, 10],
    [0.5, { minDuration: 4, maxDuration: 15, supportsCustomDuration: true }, 4],
    [3.4, { minDuration: 4, maxDuration: 15, supportsCustomDuration: true }, 4],
    [15.1, { minDuration: 4, maxDuration: 15 }, null],
    [16, { durationOptions: [5, 10, 15] }, null],
    [20, { durationOptions: [5, 15, 30] }, 30],
    [2, { durationOptions: [], durationParameterEnabled: false }, null],
    [2, null, 2],
  ])('resolves %s without shortening the shot', (seconds, model, expected) => {
    expect(scriptGenerationDuration(seconds, model)).toBe(expected);
  });

  it('changes only generated timing and preserves state and camera facts', () => {
    const prompt = '[运镜：快速横移] + [主体动作：跳过屋顶] + [时长：2s，24fps]\n可见状态接力：跳起 → 落地';
    const adapted = scriptGenerationPrompt(prompt, 2, 5);
    expect(adapted).toContain('[运镜：快速横移]');
    expect(adapted).toContain('[主体动作：跳过屋顶]');
    expect(adapted).toContain('5s，24fps');
    expect(adapted).toContain('跳起 → 落地');
    expect(adapted).toContain('默认完整保留并顺序拼接');
    expect(adapted).toContain('不要求在前 2s 提前演完');
    expect(adapted).toContain('不重启或重复原动作');
    expect(adapted).not.toContain('计划剪辑取用前');
    expect(adapted).not.toContain('保留剪辑余量');
    expect(adapted).not.toContain('保持结束状态并自然延续微动');
    expect(scriptGenerationPrompt(prompt, 2, 2)).toBe(prompt);
  });
});
