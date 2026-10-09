// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
/**
 * 端到端核对：真机那条 H3 提交的提示词，经修复后的解析链是否会再落到视频节点上。
 *
 * 这不是一个断言型单测，而是把 2026-09-14 的真实记录喂回解析链做一次回归复算。
 * 断言口径只有三条：
 *   ① 脚本节点导出的上游文本里**不再**出现节点自己的生成指令；
 *   ② 它导出的是分镜表里真正可拍的内容；
 *   ③ 表进下游后不再触发前后端同口径的时长闸门（真机 1899 行每行运动提示词
 *      都以 `[时长：3.0s]` 结尾，闸门会读成「15 秒的节点里写着 3 秒」）。
 */
import { describe, expect, it } from 'vitest';

import { extractUpstreamContent, joinUpstreamText } from './graphContentResolver';
import { videoPromptContractIssue } from '@/features/canvas/domain/videoCapabilityCompiler';
import type { CanvasNode } from '../domain/canvasNodes';

const SCRIPT_INSTRUCTION =
  '帮我生成肯定不会被安全拦截的脚本。细致仔细推理。一定要好看。画风一定要去噪点。';

const node = (id: string, type: string, data: Record<string, unknown>): CanvasNode =>
  ({ id, type, position: { x: 0, y: 0 }, data }) as unknown as CanvasNode;

describe('真机 H3 提示词污染的回归复算', () => {
  it('脚本指令不再出现在下游视频提示词里', () => {
    // 与画布 user_local_17cvc3s 的 ad053677 节点同形（含真实 scriptResult 形状）。
    const scriptNode = node('ad053677', 'scriptNode', {
      prompt: SCRIPT_INSTRUCTION,
      scriptResult: {
        title: '苍穹对拳',
        rows: [
          {
            shot_no: 1,
            duration: '15s',
            visual_description: '雨夜擂台，主角立于场中',
            shot: '全景',
            character_action: '缓缓抬拳',
            emotion: '肃杀',
            scene_tags: '雨夜擂台',
            shot_prompt: '雨夜擂台全景，主角缓缓抬拳',
            video_motion_prompt: '镜头缓慢推近',
          },
        ],
      },
    });
    const exportNode = node('6830cb84', 'exportImageNode', {
      imageUrl: 'http://x/shot1.png',
    });

    const joined = joinUpstreamText([
      extractUpstreamContent(scriptNode),
      extractUpstreamContent(exportNode),
    ]);

    expect(joined).not.toContain('安全拦截');
    expect(joined).not.toContain('去噪点');
    expect(joined).toContain('雨夜擂台全景，主角缓缓抬拳');
    // 视频节点自己那句仍是权威画面指令，不受影响。
    const videoPrompt = [joined, '@图片1 根据分镜脚本和分镜图生成一段15秒的视频']
      .filter((segment) => segment.length > 0)
      .join('\n\n');
    expect(videoPrompt).not.toContain('帮我生成');
    expect(videoPrompt).toContain('@图片1 根据分镜脚本和分镜图生成一段15秒的视频');
  });

  it('表进下游后不再携带逐行时长槽，且文案秒数不再挡提交', () => {
    const scriptNode = node('ad053677', 'scriptNode', {
      prompt: SCRIPT_INSTRUCTION,
      scriptResult: {
        rows: [
          {
            shot_no: 1,
            duration: 3,
            video_motion_prompt: '[明确的摄影机运镜轨迹与速度：极慢速推进] + [时长：3.0s]',
          },
          {
            shot_no: 2,
            duration: 4,
            video_motion_prompt: '[明确的摄影机运镜轨迹与速度：横向平移] + [时长：4.0s]',
          },
        ],
      },
    });

    const upstream = joinUpstreamText([extractUpstreamContent(scriptNode)]);
    const prompt = `${upstream}\n@图片1 根据分镜脚本和分镜图生成一段15秒的视频`;
    const counts = { images: 1, videos: 0, audios: 0 };

    expect(upstream).not.toContain('[时长：3.0s]');
    expect(upstream).not.toContain('[时长：4.0s]');
    expect(videoPromptContractIssue(prompt, counts)).toBeNull();
    // 即使槽位漏进下游，文案里的秒数也只作描述，不再禁用提交。
    const withSlot = `${prompt}\n[时长：3.0s]`;
    expect(videoPromptContractIssue(withSlot, counts)).toBeNull();
  });
});
