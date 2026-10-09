// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from 'vitest';

import type { VideoStoryRow } from '@/features/canvas/domain/canvasNodes';
import {
  buildVideoStoryRowKeys,
  buildVideoStoryShotSpecs,
  scatterableShotSpecs,
  videoStoryRowDurationSeconds,
  videoStoryRowImagePrompt,
  videoStoryRowMotionPrompt,
  videoStoryRowRangeSeconds,
  videoStoryRowShotNumber,
  videoStoryRowTimeRange,
  videoStoryShotsGroupLabel,
} from '@/features/canvas/nodes/videoStory/videoStoryShots';

/**
 * 「分镜表 → 镜头节点」的纯换算（T-014 / B）。
 *
 * 这些用例锁住的是**口径**，不是实现：
 * - 行标识（rowKey）用镜号，缺列时回落下标 + 1 —— 与脚本分镜表同一套口径；
 * - 提示词优先「图像生成提示词」，其次「画面描述」；
 * - 关键帧进的是参考图，不是结果图；
 * - 拼不出提示词的行不落节点，但不影响同批其它行。
 */

function row(overrides: Partial<VideoStoryRow> = {}): VideoStoryRow {
  return { ...overrides };
}

describe('videoStoryRowShotNumber', () => {
  it('优先用镜号，缺列时回落下标 + 1', () => {
    expect(videoStoryRowShotNumber(row({ shotNumber: 7 }), 0)).toBe('7');
    expect(videoStoryRowShotNumber(row({ shotNumber: '3' }), 0)).toBe('3');
    expect(videoStoryRowShotNumber(row({}), 0)).toBe('1');
    expect(videoStoryRowShotNumber(row({ shotNumber: '' }), 4)).toBe('5');
    expect(videoStoryRowShotNumber(row({ shotNumber: '   ' }), 1)).toBe('2');
  });
});

describe('buildVideoStoryRowKeys', () => {
  it('重号补 #n 后缀，保证同一批里互不相同', () => {
    const keys = buildVideoStoryRowKeys([
      row({ shotNumber: 1 }),
      row({ shotNumber: 1 }),
      row({ shotNumber: 2 }),
      row({ shotNumber: 1 }),
    ]);
    expect(keys).toEqual(['1', '1#2', '2', '1#3']);
  });

  it('缺镜号的行按下标生成，不会互相撞车', () => {
    expect(buildVideoStoryRowKeys([row({}), row({}), row({})])).toEqual(['1', '2', '3']);
  });
});

describe('提示词取值口径', () => {
  it('图像生成提示词优先，缺失回落画面描述', () => {
    expect(videoStoryRowImagePrompt(row({ imagePrompt: '推近祠堂' }))).toBe('推近祠堂');
    expect(videoStoryRowImagePrompt(row({ visualDescription: '雨夜祠堂' }))).toBe('雨夜祠堂');
    expect(
      videoStoryRowImagePrompt(row({ imagePrompt: '推近祠堂', visualDescription: '雨夜祠堂' })),
    ).toBe('推近祠堂');
    expect(videoStoryRowImagePrompt(row({}))).toBe('');
  });

  it('运动提示词缺失时回落画面描述，避免空节点', () => {
    expect(videoStoryRowMotionPrompt(row({ videoMotionPrompt: '横移' }))).toBe('横移');
    expect(videoStoryRowMotionPrompt(row({ visualDescription: '雨夜祠堂' }))).toBe('雨夜祠堂');
    expect(videoStoryRowMotionPrompt(row({}))).toBe('');
  });

  it('空白单元格按空处理，不会把空格当提示词', () => {
    expect(videoStoryRowImagePrompt(row({ imagePrompt: '   ' }))).toBe('');
    expect(videoStoryRowImagePrompt(row({ imagePrompt: '  ', visualDescription: ' 画面 ' }))).toBe(
      '画面',
    );
  });

  it('数字型单元格转成字符串（镜号/时长常是数字）', () => {
    expect(videoStoryRowImagePrompt(row({ imagePrompt: 12 as never }))).toBe('12');
  });
});

describe('videoStoryRowTimeRange', () => {
  it('两端都有才是区间，单端照原样给', () => {
    expect(videoStoryRowTimeRange(row({ startTime: '00:03', endTime: '00:06' }))).toBe(
      '00:03 – 00:06',
    );
    expect(videoStoryRowTimeRange(row({ startTime: '00:03' }))).toBe('00:03');
    expect(videoStoryRowTimeRange(row({ endTime: '00:06' }))).toBe('00:06');
    expect(videoStoryRowTimeRange(row({}))).toBeNull();
  });
});

/**
 * 时长（一个长度，喂模型）与区间（两个端点，拿去切）是两回事，必须分开锁：
 * 表里只写了 `duration` 的行能出视频（给个默认长度），但**切不出来** —— 不知道
 * 从哪开始。把两者混成一件事就会从 0 秒开始瞎切一段。
 */
describe('videoStoryRowDurationSeconds / videoStoryRowRangeSeconds', () => {
  it('时长优先取开始→结束的差值，其次时长列', () => {
    expect(videoStoryRowDurationSeconds(row({ startTime: '00:00', endTime: '00:04' }))).toBe(4);
    expect(videoStoryRowDurationSeconds(row({ duration: '1.2s' }))).toBe(1);
    expect(videoStoryRowDurationSeconds(row({}))).toBeNull();
  });

  it('表格里改过的时长数值优先于解析出来的开始→结束', () => {
    // 用户在分镜表里把 4 秒改成 6 秒，就必须是 6 —— 否则编辑等于没改。
    expect(
      videoStoryRowDurationSeconds(
        row({ startTime: '00:00', endTime: '00:04', durationSeconds: 6 }),
      ),
    ).toBe(6);
    // 数值列本身也走 1–15 夹取。
    expect(videoStoryRowDurationSeconds(row({ durationSeconds: 99 }))).toBe(15);
    expect(videoStoryRowDurationSeconds(row({ durationSeconds: 0.4 }))).toBe(1);
  });

  it('区间只认开始与结束这一对，且不取整（半秒不四舍五入）', () => {
    expect(videoStoryRowRangeSeconds(row({ startTime: '00:00', endTime: '00:04' }))).toEqual({
      start: 0,
      end: 4,
    });
    expect(videoStoryRowRangeSeconds(row({ startTime: '00:03.5', endTime: '00:07.5' }))).toEqual({
      start: 3.5,
      end: 7.5,
    });
    // 全角冒号与 HH:MM:SS 都认（与时长那条同一套解析）。
    expect(videoStoryRowRangeSeconds(row({ startTime: '0:00:10', endTime: '0：00：12' }))).toEqual({
      start: 10,
      end: 12,
    });
  });

  it('只有时长列 / 只有单端 / 区间非法 → 切不出来（返回 null，绝不从 0 开始）', () => {
    expect(videoStoryRowRangeSeconds(row({ duration: '4s' }))).toBeNull();
    expect(videoStoryRowRangeSeconds(row({ startTime: '00:03' }))).toBeNull();
    expect(videoStoryRowRangeSeconds(row({ endTime: '00:06' }))).toBeNull();
    expect(videoStoryRowRangeSeconds(row({ startTime: '00:06', endTime: '00:06' }))).toBeNull();
    expect(videoStoryRowRangeSeconds(row({ startTime: '00:06', endTime: '00:03' }))).toBeNull();
    // 认不出来的写法不能被当成 0 秒。
    expect(videoStoryRowRangeSeconds(row({ startTime: '特写', endTime: '00:06' }))).toBeNull();
  });
});

describe('buildVideoStoryShotSpecs', () => {
  it('逐行出规格：显示名带景别、关键帧进参考图、镜头语言字段留档', () => {
    const specs = buildVideoStoryShotSpecs([
      row({
        shotNumber: 3,
        startTime: '00:06',
        endTime: '00:09',
        visualDescription: '雨夜祠堂',
        imagePrompt: '推近祠堂门环',
        videoMotionPrompt: '缓慢推进',
        shotSize: '特写',
        cameraAngle: '俯拍',
        cameraMovement: '推',
        keyframeUrl: '/static/projects/p1/frames/f3.jpg',
      }),
    ]);
    expect(specs).toHaveLength(1);
    const spec = specs[0];
    expect(spec.rowKey).toBe('3');
    expect(spec.shotNumber).toBe('3');
    expect(spec.name).toBe('镜头 3 · 特写');
    expect(spec.imagePrompt).toBe('推近祠堂门环');
    expect(spec.motionPrompt).toBe('缓慢推进');
    expect(spec.referenceImageUrl).toBe('/static/projects/p1/frames/f3.jpg');
    expect(spec.shotSize).toBe('特写');
    expect(spec.cameraAngle).toBe('俯拍');
    expect(spec.cameraMovement).toBe('推');
    expect(spec.timeRange).toBe('00:06 – 00:09');
    expect(spec.hasPrompt).toBe(true);
  });

  it('没有景别时显示名退化成「镜头 N」', () => {
    const specs = buildVideoStoryShotSpecs([
      row({ shotNumber: 2, imagePrompt: '收尾' }),
    ]);
    expect(specs[0].name).toBe('镜头 2');
  });

  it('没有关键帧时参考图为 null（不是空串）', () => {
    const specs = buildVideoStoryShotSpecs([row({ imagePrompt: 'x' })]);
    expect(specs[0].referenceImageUrl).toBeNull();
  });

  it('行数一致：提示词拼不出来的行仍留在清单里（只是标 hasPrompt=false）', () => {
    const specs = buildVideoStoryShotSpecs([
      row({ shotNumber: 1, imagePrompt: '有提示词' }),
      row({ shotNumber: 2 }),
    ]);
    expect(specs).toHaveLength(2);
    expect(specs.map((spec) => spec.hasPrompt)).toEqual([true, false]);
  });
});

describe('scatterableShotSpecs', () => {
  it('只跳过空行，不让一行没填拖垮整批', () => {
    const specs = buildVideoStoryShotSpecs([
      row({ shotNumber: 1 }),
      row({ shotNumber: 2, visualDescription: '有画面描述就够了' }),
      row({ shotNumber: 3, imagePrompt: '有提示词' }),
    ]);
    const scatterable = scatterableShotSpecs(specs);
    expect(scatterable.map((spec) => spec.shotNumber)).toEqual(['2', '3']);
  });

  it('整批都没提示词时返回空（调用方据此给出提示而不是散一排空节点）', () => {
    const specs = buildVideoStoryShotSpecs([row({ shotNumber: 1 }), row({ shotNumber: 2 })]);
    expect(scatterableShotSpecs(specs)).toHaveLength(0);
  });
});

describe('videoStoryShotsGroupLabel', () => {
  it('组标签带镜数', () => {
    expect(videoStoryShotsGroupLabel(0)).toBe('镜头表 · 0 镜');
    expect(videoStoryShotsGroupLabel(12)).toBe('镜头表 · 12 镜');
  });
});
