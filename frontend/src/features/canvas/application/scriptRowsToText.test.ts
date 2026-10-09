// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from 'vitest';

import { extractUpstreamContent, joinUpstreamText } from './graphContentResolver';
import { scriptResultToUpstreamText } from './scriptRowsToText';
import type { CanvasNode } from '../domain/canvasNodes';

const node = (id: string, type: string, data: Record<string, unknown>): CanvasNode =>
  ({ id, type, position: { x: 0, y: 0 }, data }) as unknown as CanvasNode;

const SCRIPT_RESULT = {
  title: '苍穹对拳',
  rows: [
    {
      shot_no: 1,
      duration: '3s',
      visual_description: '擂台特写，主角抬手格挡',
      shot: '近景',
      character_action: '抬手格挡',
      emotion: '紧绷',
      scene_tags: '夜雨擂台',
      dialogue: '代号二：“你就这么想死在我拳下？”',
      shot_prompt: '雨夜擂台近景，主角抬手格挡',
      video_motion_prompt: '镜头缓慢推近',
    },
    {
      shot_no: 2,
      visual_description: '对手侧身避开',
      dialogue: '无',
    },
  ],
};

/**
 * 这一组测试钉住「脚本节点把自己的生成指令当成内容传给下游」这个断点。
 *
 * 现象：H3 出片的音轨是逐字朗读节点提示词框里的那句「帮我生成肯定不会被安全
 * 拦截的脚本。一定要好看。」—— 那句话经 `extractUpstreamContent` 的 `text`
 * 字段、`joinUpstreamText` 的拼接，进了视频节点的提交提示词。视频模型分不清
 * 「创作要求」和「画面内容」，就当台词念了。
 */
describe('脚本节点的上游内容导出', () => {  it('导出的是分镜表正文，不是节点自己的生成指令', () => {
    const scriptNode = node('script', 'scriptNode', {
      prompt: '帮我生成肯定不会被安全拦截的脚本。细致仔细推理。一定要好看。画风一定要去噪点。',
      scriptResult: SCRIPT_RESULT,
    });

    const content = extractUpstreamContent(scriptNode);

    expect(content.text).toBeDefined();
    // 指令一个字都不能漏出去 —— 这是被模型朗读的那段文本。
    expect(content.text).not.toContain('不会被安全拦截');
    expect(content.text).not.toContain('一定要好看');
    expect(content.text).not.toContain('去噪点');
    // 表里的可拍内容必须在。
    expect(content.text).toContain('擂台特写，主角抬手格挡');
    expect(content.text).toContain('角色动作 抬手格挡');
    expect(content.text).toContain('场景标签 夜雨擂台');
  });

  it('对白保留在正文里，占位「无」不渲染', () => {
    const content = extractUpstreamContent(
      node('script', 'scriptNode', { prompt: '指令', scriptResult: SCRIPT_RESULT }),
    );

    expect(content.text).toContain('你就这么想死在我拳下？');
    expect(content.text).not.toContain('对白 无');
  });

  /**
   * 后端只认三种「这句是要说出来的台词」：引号、`说：` 标记、结构化字段。
   * 裸文本会掉进「没有任何说话来源」那一类，被追加「本镜头没有必须说出的台词」——
   * 而正文里明明有台词行。真机 871 条非空对白里 63 条是裸文本（如「退下！」），
   * 所以渲染时补一层引号，不改用户数据。
   */
  it('裸文本对白补引号，已经带引号或标记的不重复加工', () => {
    const bare = extractUpstreamContent(
      node('script', 'scriptNode', {
        scriptResult: { rows: [{ shot_no: 1, dialogue: '退下！' }] },
      }),
    );
    expect(bare.text).toBe('镜号 1｜对白 “退下！”');

    const quoted = extractUpstreamContent(
      node('script', 'scriptNode', {
        scriptResult: { rows: [{ shot_no: 1, dialogue: '她说：“你终于来了。”' }] },
      }),
    );
    expect(quoted.text).toBe('镜号 1｜对白 她说：“你终于来了。”');

    const marked = extractUpstreamContent(
      node('script', 'scriptNode', {
        scriptResult: { rows: [{ shot_no: 2, dialogue: '女孩说：别过来' }] },
      }),
    );
    expect(marked.text).toBe('镜号 2｜对白 女孩说：别过来');
  });

  it('表还没生成时宁可不传 text，也不把指令当内容', () => {
    const content = extractUpstreamContent(
      node('script', 'scriptNode', { prompt: '帮我生成脚本' }),
    );

    expect(content.text).toBeUndefined();
    expect(joinUpstreamText([content])).toBe('');
  });

  it('分镜表经 joinUpstreamText 拼进下游提示词时不带指令', () => {
    const joined = joinUpstreamText([
      extractUpstreamContent(
        node('script', 'scriptNode', { prompt: '帮我生成脚本。一定要好看。', scriptResult: SCRIPT_RESULT }),
      ),
      extractUpstreamContent(node('brief', 'textAnnotationNode', { content: '导演总纲' })),
    ]);

    expect(joined).toContain('导演总纲');
    expect(joined).toContain('镜头缓慢推近');
    expect(joined).not.toContain('一定要好看');
  });

  it('没有 rows / rows 不是数组时返回空', () => {
    expect(scriptResultToUpstreamText(null)).toBeUndefined();
    expect(scriptResultToUpstreamText({ title: '只有标题' })).toBeUndefined();
    expect(scriptResultToUpstreamText({ rows: '不是数组' })).toBeUndefined();
    expect(scriptResultToUpstreamText({ rows: [] })).toBeUndefined();
  });
});

/**
 * 逐行时长槽 `[时长：4.0s]` 必须摘掉。
 *
 * 分镜表里每行的运动提示词都以 `[时长：3.0s]` 结尾（真机 1899 行全是这个形态、
 * 且与同行 `duration` 单元格逐行相等）。节点自己的时长才是提交参数，逐行时长槽
 * 不该混进下游的画面描述。
 */
describe('逐行时长槽', () => {
  it('运动提示词末尾的 [时长：3.0s] 不进下游文本', () => {
    const content = extractUpstreamContent(
      node('script', 'scriptNode', {
        scriptResult: {
          rows: [
            {
              shot_no: 1,
              duration: 3,
              video_motion_prompt:
                '[明确的摄影机运镜轨迹与速度：极慢速推进] + [环境物理动态：纸张边缘被风吹起] + [时长：3.0s]',
            },
          ],
        },
      }),
    );

    expect(content.text).not.toContain('[时长');
    expect(content.text).not.toContain('3.0s');
    // 结尾不能留下落单的 `+`。
    expect(content.text?.endsWith(']')).toBe(true);
    expect(content.text).not.toContain('+ ]');
    // 上半段一个字都不能少。
    expect(content.text).toContain('[环境物理动态：纸张边缘被风吹起]');
  });

  it('英文 duration 槽同样摘掉', () => {
    const content = extractUpstreamContent(
      node('script', 'scriptNode', {
        scriptResult: {
          rows: [{ shot_no: 1, video_motion_prompt: '[Camera: slow push in] + [Duration: 4.0s]' }],
        },
      }),
    );

    expect(content.text).toBe('镜号 1｜视频运动提示词 [Camera: slow push in]');
  });

  it('时长槽两侧多余分隔符被合并，正文里的加号不受影响', () => {
    const content = extractUpstreamContent(
      node('script', 'scriptNode', {
        scriptResult: {
          rows: [
            {
              shot_no: 1,
              shot_prompt: '[A：x] + [B：a+b] + [时长：4.0s]',
              video_motion_prompt: '[C：y] + [时长：4.0s]',
            },
          ],
        },
      }),
    );

    // 正文里的 `a+b` 保留，只清掉槽位留下的空分隔。
    expect(content.text).toContain('[B：a+b]');
    expect(content.text).not.toContain('[时长');
    expect(content.text).not.toMatch(/\+\s*\+/);
    expect(content.text).not.toMatch(/\+\s*$/);
  });

  it('渲染后的表不再触发前后端同口径的时长闸门', () => {
    const content = extractUpstreamContent(
      node('script', 'scriptNode', {
        scriptResult: {
          rows: [
            { shot_no: 1, duration: 3, video_motion_prompt: '[运镜：推近] + [时长：3.0s]' },
            { shot_no: 2, duration: 4, video_motion_prompt: '[运镜：拉远] + [时长：4.0s]' },
          ],
        },
      }),
    );

    // 闸门拿「时长」抓数字；表里摘干净了，就不该再出现任何「时长 + 数字 + 秒」。
    expect(content.text).not.toMatch(/时长[^\n]{0,12}?\d+(?:\.\d+)?\s*(?:秒|s)/i);
    expect(content.text).not.toMatch(/\[\s*时长\s*[:：]/);
  });
});
