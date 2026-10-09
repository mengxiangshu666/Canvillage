// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { beforeEach, describe, expect, it } from 'vitest';

import { CANVAS_NODE_TYPES } from '@/features/canvas/domain/canvasNodes';
import { useCanvasStore } from '@/stores/canvasStore';

/**
 * 打开旧画布时把废弃的 `data.cameraMovement` 折进提示词（T-154）。
 *
 * 真机证据：`项目资产/state/local/t113_l3_20260921t034442z` 那份画布上 12 个脚本
 * 派生的视频节点都带着自由文本 `cameraMovement`，提交时被当成 `camera_template_id`
 * 发出去，每一镜都撞 `unknown camera_template_id: [运镜轨迹] 固定机位，极慢微推，不切碎表演`。
 * 水合是唯一的兜底点：老画布不折一把，这批节点在用户点开之前还是坏的。
 */

function videoData(id: string): Record<string, unknown> {
  const node = useCanvasStore.getState().nodes.find((item) => item.id === id);
  if (!node) throw new Error(`节点不存在：${id}`);
  return node.data as Record<string, unknown>;
}

function seedVideo(data: Record<string, unknown>, id = 'video-1'): void {
  useCanvasStore.getState().setCanvasData(
    [
      {
        id,
        type: CANVAS_NODE_TYPES.video,
        position: { x: 0, y: 0 },
        data,
      },
    ] as never,
    [],
  );
}

describe('废弃的 cameraMovement 在水合时折进提示词', () => {
  beforeEach(() => {
    useCanvasStore.getState().setCanvasData([], []);
  });

  it('旧值是目录 id：按目录正文写进运镜段，字段本身删掉', () => {
    seedVideo({ prompt: '[画面构图] 祠堂正门', cameraMovement: 'dolly_in' });
    const data = videoData('video-1');
    expect(data.prompt).toBe('[运镜轨迹] 镜头前推 + [画面构图] 祠堂正门');
    expect(data).not.toHaveProperty('cameraMovement');
  });

  it('旧值是自由文本、提示词已有运镜段：提示词原样保留（不拿旧值覆盖）', () => {
    const prompt = '[运镜轨迹] 固定机位，极慢微推 + [画面构图] 祠堂正门';
    seedVideo({ prompt, cameraMovement: '[运镜轨迹] 固定机位，极慢微推，不切碎表演' });
    const data = videoData('video-1');
    expect(data.prompt).toBe(prompt);
    expect(data).not.toHaveProperty('cameraMovement');
  });

  it('旧值是自由文本、提示词没有运镜段：补在最前面', () => {
    seedVideo({ prompt: '[画面构图] 祠堂正门', cameraMovement: '固定机位，极慢微推' });
    const data = videoData('video-1');
    expect(data.prompt).toBe('[运镜轨迹] 固定机位，极慢微推 + [画面构图] 祠堂正门');
  });

  it('没有旧字段时不碰提示词', () => {
    seedVideo({ prompt: '[画面构图] 祠堂正门' });
    expect(videoData('video-1').prompt).toBe('[画面构图] 祠堂正门');
  });

  it('空字符串是「没写过」，不算运镜', () => {
    seedVideo({ prompt: '[画面构图] 祠堂正门', cameraMovement: '   ' });
    const data = videoData('video-1');
    expect(data.prompt).toBe('[画面构图] 祠堂正门');
    expect(data).not.toHaveProperty('cameraMovement');
  });
});
