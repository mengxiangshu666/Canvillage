// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from 'vitest';

import {
  hasEightSegments,
  isFrozenShotSegment,
  scriptRewriteFrozenFacts,
  MOTION_SEGMENT_PREFIXES,
  SHOT_SEGMENT_PREFIXES,
  shotPromptSegment,
  splitPromptSegments,
  splitShotPromptSegments,
} from '@/features/canvas/nodes/script/scriptPromptSegments';

/**
 * 段级读取。
 *
 * 前端这里**不复制服务端的判定与修复**（那是 `freezone/script_contract.py` 的活），
 * 只做只读解析：给用户看「哪几段改不动」。所以这一层要证明的是「解得开、解得稳」——
 * 格式有小瑕疵时也要解得出来，否则弹层在最需要说清楚的时候反而什么都不显示。
 */

const CARD = '[沈昭昭_现代: 28岁女性，面色苍白，神情疲惫，身穿现代简约职业装]';
const STYLE = '[视觉风格/质感：都市悬疑写实电影感]';
const TECH = '[技术参数：85mm镜头，f/1.8，浅景深]';

function shotPrompt(): string {
  return [
    '[画面构图：近景特写，平视机位]',
    `[角色卡/主体描述：${CARD}]`,
    '[主体/人物空间与互动关系：她独坐在办公桌前]',
    '[极具体的微表情：眼下发青，手指微颤]',
    '[明确的场景环境元素：深夜办公室、冷掉的咖啡杯]',
    '[光影几何与大气效果：冷蓝主调]',
    STYLE,
    TECH,
  ].join(' + ');
}

describe('splitPromptSegments', () => {
  it('按 ` + ` 切段并剥掉最外层方括号', () => {
    const segments = splitShotPromptSegments(shotPrompt());
    expect(segments).toHaveLength(8);
    expect(segments[0]).toBe('画面构图：近景特写，平视机位');
  });

  it('保留角色卡内层的方括号（角色 ID 的载体）', () => {
    const segments = splitShotPromptSegments(shotPrompt());
    expect(segments[1]).toBe(`角色卡/主体描述：${CARD}`);
    expect(segments[1]).toContain('沈昭昭_现代');
  });

  it('段内容里的「A+B」不当作分隔符', () => {
    expect(splitPromptSegments('[光影几何与大气效果：冷蓝A+B主调]')).toEqual([
      '光影几何与大气效果：冷蓝A+B主调',
    ]);
  });

  it('一侧带空白仍然算分隔（模型偶尔漏空格）', () => {
    expect(splitPromptSegments('[画面构图：近景] +[技术参数：85mm]')).toEqual([
      '画面构图：近景',
      '技术参数：85mm',
    ]);
  });

  it('容错缺失的外层括号：不抛错，也不静默丢段', () => {
    expect(splitPromptSegments('画面构图：近景 + 技术参数：85mm')).toEqual([
      '画面构图：近景',
      '技术参数：85mm',
    ]);
  });

  it('空输入与纯空白返回空数组', () => {
    expect(splitPromptSegments('')).toEqual([]);
    expect(splitPromptSegments('   ')).toEqual([]);
    expect(splitPromptSegments(null)).toEqual([]);
    expect(splitPromptSegments(undefined)).toEqual([]);
  });

  it('空段被丢掉，不产生占位空串', () => {
    expect(splitPromptSegments('[画面构图：近景] +  + [技术参数：85mm]')).toEqual([
      '画面构图：近景',
      '技术参数：85mm',
    ]);
  });
});

describe('shotPromptSegment', () => {
  it('按段序标签前缀取段', () => {
    expect(shotPromptSegment(shotPrompt(), '视觉风格')).toBe(STYLE.slice(1, -1));
    expect(shotPromptSegment(shotPrompt(), '技术参数')).toBe(TECH.slice(1, -1));
  });

  it('取不到时返回空串（不返回 undefined）', () => {
    expect(shotPromptSegment('[画面构图：近景]', '视觉风格')).toBe('');
    expect(shotPromptSegment('', '视觉风格')).toBe('');
  });
});

describe('isFrozenShotSegment', () => {
  it('角色卡和全片风格冻结，逐镜技术参数不冻结', () => {
    expect(isFrozenShotSegment(`角色卡/主体描述：${CARD}`)).toBe(true);
    expect(isFrozenShotSegment('视觉风格/质感：都市悬疑写实电影感')).toBe(true);
    expect(isFrozenShotSegment('技术参数：85mm镜头，f/1.8')).toBe(false);
  });

  it('逐镜可变的段不是冻结的', () => {
    expect(isFrozenShotSegment('画面构图：中景，略俯')).toBe(false);
    expect(isFrozenShotSegment('主体/人物空间与互动关系：她走向落地窗')).toBe(false);
    expect(isFrozenShotSegment('极具体的微表情：肩线绷紧')).toBe(false);
    expect(isFrozenShotSegment('明确的场景环境元素：深夜办公室')).toBe(false);
    expect(isFrozenShotSegment('光影几何与大气效果：冷蓝主调')).toBe(false);
  });

  it('一段 8 段式只冻结角色卡和风格', () => {
    const frozen = splitShotPromptSegments(shotPrompt()).filter(isFrozenShotSegment);
    expect(frozen).toHaveLength(2);
  });
  it('返工第二镜展示第二镜角色而非第一镜，整段返工仅展示公共风格', () => {
    const rows = [{ shot_prompt: shotPrompt() }, { shot_prompt: '[角色卡/主体描述：[小雀: 蓝背包]] + [技术参数：24mm] + [视觉风格/质感：不同的旧风格]' }];
    const facts = scriptRewriteFrozenFacts(rows, 1);
    expect(facts).toContain('角色卡/主体描述：[小雀: 蓝背包]');
    expect(facts.join('')).not.toContain(CARD);
    expect(facts.join('')).not.toContain('技术参数');
    expect(facts).toContain(STYLE.slice(1, -1));
    expect(scriptRewriteFrozenFacts(rows, null)).toEqual([STYLE.slice(1, -1)]);
  });
});

describe('段序常量与段数判定', () => {
  it('8 段式与 6 段式的段序标签齐备', () => {
    expect(SHOT_SEGMENT_PREFIXES).toHaveLength(8);
    expect(MOTION_SEGMENT_PREFIXES).toHaveLength(6);
  });

  it('hasEightSegments 认 8 段、不认 7 段', () => {
    expect(hasEightSegments(shotPrompt())).toBe(true);
    const segments = splitShotPromptSegments(shotPrompt()).slice(0, 7);
    expect(hasEightSegments(segments.map((s) => `[${s}]`).join(' + '))).toBe(false);
    expect(hasEightSegments('')).toBe(false);
  });
});
