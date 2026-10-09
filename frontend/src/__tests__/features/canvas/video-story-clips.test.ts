// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { beforeEach, describe, expect, it } from 'vitest';

import {
  CANVAS_NODE_TYPES,
  isVideoNode,
  type VideoStoryRow,
} from '@/features/canvas/domain/canvasNodes';
import { resetStoryboardSettle } from '@/features/canvas/nodes/script/storyboardSettle';
import {
  applyVideoStoryClipResults,
  buildVideoStoryClipSpecs,
  failVideoStoryClipNodes,
  planVideoStoryClips,
  scatterVideoStoryClips,
} from '@/features/canvas/nodes/videoStory/videoStoryShotClips';
import { useCanvasStore } from '@/stores/canvasStore';

/**
 * 「分镜表 → 逐镜切片段」落盘（T-016）。
 *
 * 这一层锁的是**生命周期与准入**，不是 ffmpeg 的活（真实的切段在后端
 * `tests/test_freezone_video_cut.py` 里跑 ffmpeg 验过）。所以这里关心的是：
 * - 准入门槛是「时间码能解析出两端点」，不是「已出图」—— 没图的镜照样能切；
 * - 只建节点、不打 `canvas_auto_generate_once`（切片段不该触发任何生成）；
 * - 重复点不重复派生；行集合换过就重建；区间改过只重切那一段；
 * - 结果按 index 对号入座回写，顺序错位不会串行；
 * - 两套派生互不串台（片段节点不进出视频的清点）。
 */

const SOURCE_ID = 'video-story-node';
const SOURCE_VIDEO = '/static/projects/p1/source.mp4';
const SOURCE_SIZE = { width: 720, height: 360 };

function rows(): VideoStoryRow[] {
  return [
    {
      shotNumber: 1,
      startTime: '00:00',
      endTime: '00:04',
      visualDescription: '雨夜祠堂外景',
      shotSize: '特写',
    },
    {
      shotNumber: 2,
      startTime: '00:04',
      endTime: '00:09',
      visualDescription: '阿雀推门而入',
      shotSize: '中景',
    },
  ];
}

/** 行数相同但行标识整批换过 —— 判定必须落到「重建」而不是「原地重切」。 */
function renamedRows(): VideoStoryRow[] {
  return [
    { shotNumber: 11, startTime: '00:00', endTime: '00:03' },
    { shotNumber: 12, startTime: '00:03', endTime: '00:07' },
  ];
}

function seedSourceNode(seedRows: VideoStoryRow[] = rows(), sourceVideoUrl: string | null = SOURCE_VIDEO) {
  useCanvasStore.getState().setCanvasData(
    [
      {
        id: SOURCE_ID,
        type: CANVAS_NODE_TYPES.videoStory,
        position: { x: 0, y: 0 },
        style: { width: SOURCE_SIZE.width, height: SOURCE_SIZE.height },
        data: { sourceVideoUrl, rows: seedRows },
      },
    ] as never,
    [],
  );
}

function videoNodes() {
  return useCanvasStore.getState().nodes.filter(isVideoNode);
}

function clipNodes() {
  return videoNodes().filter((node) => node.data.videoStoryClipSegment === true);
}

function clipForKey(rowKey: string) {
  return clipNodes().find((node) => node.data.videoStoryClipRowKey === rowKey);
}

function setRows(nextRows: VideoStoryRow[]) {
  useCanvasStore.getState().updateNodeData(SOURCE_ID, { rows: nextRows });
}

function scatter() {
  return scatterVideoStoryClips({ videoStoryNodeId: SOURCE_ID });
}

function plan() {
  return planVideoStoryClips(SOURCE_ID);
}

describe('逐镜切片段 · 按时间码从源视频派生片段节点', () => {
  beforeEach(() => {
    resetStoryboardSettle();
    seedSourceNode();
  });

  it('逐镜建片段节点，写下行身份与区间，且不打自动提交', () => {
    const result = scatter();
    expect(result.ok).toBe(true);
    if (!result.ok) return;

    expect(result.mode).toBe('created');
    expect(result.plan).toHaveLength(2);
    expect(result.skippedNoRange).toBe(0);

    const clips = clipNodes();
    expect(clips).toHaveLength(2);
    // 切片段不出片：没有提示词。
    expect(clips.every((node) => node.data.prompt === '')).toBe(true);
    // 也不带「出视频」那条路的过期判据快照 —— 两套派生刻意不共用字段。
    expect(clips.every((node) => node.data.videoStoryRowMotionPrompt === undefined)).toBe(true);
    // 结果靠回写，绝不能留下「挂载后自提交」的标记（那会白跑一次生成）。
    expect(clips.every((node) => node.data.canvas_auto_generate_once === undefined)).toBe(true);
    // 此时还没有片 —— 视频地址等任务回来才写。
    expect(clips.every((node) => !node.data.videoUrl)).toBe(true);

    // 行身份 + 区间（两端点，不是长度）。
    const first = clipForKey('1');
    expect(first?.data.videoStoryClipSourceNodeId).toBe(SOURCE_ID);
    expect(first?.data.videoStoryClipStartSec).toBe(0);
    expect(first?.data.videoStoryClipEndSec).toBe(4);
    // 时长是派生的展示值（毫秒），与区间一致。
    expect(first?.data.durationMs).toBe(4000);
    expect(first?.data.shot_size).toBe('特写');

    const second = clipForKey('2');
    expect(second?.data.videoStoryClipStartSec).toBe(4);
    expect(second?.data.videoStoryClipEndSec).toBe(9);

    // 片段节点与出视频那条路的节点绝不能互相被认领：身份字段不同、且带片段标记。
    expect(clips.every((node) => node.data.videoStoryClipSegment === true)).toBe(true);
    expect(clips.every((node) => node.data.videoStorySourceNodeId === undefined)).toBe(true);
  });

  it('不创建任何血缘边（切片段不需要首帧）', () => {
    const result = scatter();
    expect(result.ok).toBe(true);
    expect(useCanvasStore.getState().edges).toHaveLength(0);
  });

  it('准入只看时间码：没有镜头图的行照样能切', () => {
    // 一行有时间码 + 没有提示词 + 没有关键帧 —— 出视频那条路会跳过它。
    seedSourceNode([
      { shotNumber: 1, startTime: '00:02', endTime: '00:06', shotSize: '近景' },
    ]);
    const result = scatter();
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    // 切出来的是源片里那几秒，跟「有没有出过图」无关。
    expect(clipNodes()).toHaveLength(1);
    expect(clipForKey('1')?.data.videoStoryClipStartSec).toBe(2);
  });

  it('提交清单按时间排序，index 与 plan 同序（回写靠 index - 1 对号入座）', () => {
    // 刻意把表里的顺序与源上的时间顺序**反过来**写。
    seedSourceNode([
      { shotNumber: 1, startTime: '00:10', endTime: '00:14' },
      { shotNumber: 2, startTime: '00:00', endTime: '00:04' },
    ]);
    const result = scatter();
    expect(result.ok).toBe(true);
    if (!result.ok) return;

    // 提交清单：按时间升序，第二行（00:00）排在前。
    expect(result.segments).toEqual([
      { index: 2, start: 0, end: 4 },
      { index: 1, start: 10, end: 14 },
    ]);
    // plan 仍按表内行序：plan[0] 是行 1、plan[1] 是行 2。
    expect(result.plan[0].start).toBe(10);
    expect(result.plan[1].start).toBe(0);
    // 于是 index - 1 就是 plan 的下标 —— 这条不变式是回写正确的前提。
    result.segments.forEach((segment) => {
      expect(result.plan[segment.index - 1].start).toBe(segment.start);
      expect(result.plan[segment.index - 1].end).toBe(segment.end);
    });
  });

  it('重复点不重复派生；已切好的重切时只交未落地的那段', () => {
    const first = scatter();
    expect(first.ok).toBe(true);
    if (!first.ok) return;

    const again = scatter();
    expect(again.ok).toBe(true);
    if (!again.ok) return;
    expect(again.mode).toBe('rearmed');
    // 两段都还没切出来 → 两段都要重切。
    expect(again.plan).toHaveLength(2);
    expect(clipNodes()).toHaveLength(2);

    // 把第一段标记为已切好，再点一次：只剩第二段要切。
    const firstClip = clipForKey('1');
    useCanvasStore.getState().updateNodeData(firstClip!.id, { videoUrl: '/static/p1/a.mp4' });
    const third = scatter();
    expect(third.ok).toBe(true);
    if (!third.ok) return;
    expect(third.mode).toBe('rearmed');
    expect(third.plan).toHaveLength(1);
    expect(third.plan[0].rowKey).toBe('2');
    // 节点数不变（原地重切，不是又建一批）。
    expect(clipNodes()).toHaveLength(2);
  });

  it('区间改过 → 那一段要重切，其余不动', () => {
    const first = scatter();
    expect(first.ok).toBe(true);
    if (!first.ok) return;
    clipNodes().forEach((node) => {
      useCanvasStore.getState().updateNodeData(node.id, { videoUrl: '/static/p1/old.mp4' });
    });

    // 只动第一镜的结束时间。
    setRows([
      { shotNumber: 1, startTime: '00:00', endTime: '00:06', shotSize: '特写' },
      { shotNumber: 2, startTime: '00:04', endTime: '00:09', shotSize: '中景' },
    ]);
    const again = scatter();
    expect(again.ok).toBe(true);
    if (!again.ok) return;
    expect(again.plan).toHaveLength(1);
    expect(again.plan[0].rowKey).toBe('1');
    expect(again.plan[0].end).toBe(6);
  });

  it('行集合换过 → 重建：旧片段节点被删掉，不残留', () => {
    const first = scatter();
    expect(first.ok).toBe(true);
    if (!first.ok) return;
    const firstIds = first.plan.map((item) => item.nodeId);

    setRows(renamedRows());
    const rebuilt = scatter();
    expect(rebuilt.ok).toBe(true);
    if (!rebuilt.ok) return;
    expect(rebuilt.mode).toBe('rebuilt');
    expect(clipNodes()).toHaveLength(2);

    const liveIds = new Set(useCanvasStore.getState().nodes.map((node) => node.id));
    expect(firstIds.some((nodeId) => liveIds.has(nodeId))).toBe(false);
    expect(clipForKey('11')).toBeTruthy();
    expect(clipForKey('1')).toBeUndefined();
  });

  it('没有源视频 / 没有可解析的时间码 → 明确拒绝，不建节点', () => {
    seedSourceNode(rows(), null);
    const noSource = scatter();
    expect(noSource.ok).toBe(false);
    if (noSource.ok) return;
    expect(noSource.reason).toContain('源视频');

    seedSourceNode([
      { shotNumber: 1, duration: '4s', visualDescription: '只有时长列' },
      { shotNumber: 2, startTime: '00:04', endTime: '00:02', visualDescription: '结束早于开始' },
    ]);
    const noRange = scatter();
    expect(noRange.ok).toBe(false);
    if (noRange.ok) return;
    expect(noRange.reason).toContain('时间码');
    expect(clipNodes()).toHaveLength(0);
  });

  it('只有一部分行有时间码 → 能切的照切，跳过的进账', () => {
    seedSourceNode([
      { shotNumber: 1, startTime: '00:00', endTime: '00:04' },
      { shotNumber: 2, duration: '5s' },
      { shotNumber: 3, startTime: '00:09', endTime: '00:12' },
    ]);
    const result = scatter();
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.plan).toHaveLength(2);
    expect(result.skippedNoRange).toBe(1);
    expect(clipNodes()).toHaveLength(2);
    const accounting = plan();
    expect(accounting.ok).toBe(true);
    if (!accounting.ok) return;
    expect(accounting.pendingCount).toBe(2);
    expect(accounting.skippedNoRange).toBe(1);
  });
});

describe('逐镜切片段 · 计划账（弹层用，不产生副作用）', () => {
  beforeEach(() => {
    resetStoryboardSettle();
    seedSourceNode();
  });

  it('首次：create，段数＝可切行数，秒数合计＝各段长度之和', () => {
    const before = useCanvasStore.getState().nodes.length;
    const result = plan();
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.mode).toBe('create');
    expect(result.clipCount).toBe(2);
    expect(result.pendingCount).toBe(2);
    expect(result.willRebuild).toBe(false);
    expect(result.skippedNoRange).toBe(0);
    expect(result.derivedCount).toBe(0);
    expect(result.plannedSeconds).toBe(9);
    // 算账不能有副作用。
    expect(useCanvasStore.getState().nodes.length).toBe(before);
  });

  it('已派过：regenerate，派生的段全部落地后待切数为 0', () => {
    scatter();
    const pending = plan();
    expect(pending.ok).toBe(true);
    if (!pending.ok) return;
    expect(pending.mode).toBe('regenerate');
    expect(pending.derivedCount).toBe(2);
    expect(pending.pendingCount).toBe(2);

    clipNodes().forEach((node) => {
      useCanvasStore.getState().updateNodeData(node.id, { videoUrl: '/static/p1/a.mp4' });
    });
    const allDone = plan();
    expect(allDone.ok).toBe(true);
    if (!allDone.ok) return;
    expect(allDone.pendingCount).toBe(0);
    expect(allDone.plannedSeconds).toBe(0);
  });

  it('上次失败的那段会被算进待切（失败不能被当成已完成）', () => {
    const scattered = scatter();
    expect(scattered.ok).toBe(true);
    if (!scattered.ok) return;
    clipNodes().forEach((node) => {
      useCanvasStore.getState().updateNodeData(node.id, { videoUrl: '/static/p1/a.mp4' });
    });
    const failed = clipForKey('2');
    useCanvasStore.getState().updateNodeData(failed!.id, { generationError: '源视频读不出来' });
    const result = plan();
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.pendingCount).toBe(1);
  });
});

describe('逐镜切片段 · 结果回写', () => {
  beforeEach(() => {
    resetStoryboardSettle();
    seedSourceNode([
      { shotNumber: 1, startTime: '00:10', endTime: '00:14' },
      { shotNumber: 2, startTime: '00:00', endTime: '00:04' },
    ]);
  });

  it('按 index 对号入座：结果清单顺序与节点顺序不一致也不会串行', () => {
    const scattered = scatter();
    expect(scattered.ok).toBe(true);
    if (!scattered.ok) return;

    // 后端按时间排序输出，且第二段的文件先回来 —— 刻意打乱。
    const applied = applyVideoStoryClipResults(scattered.plan, [
      { index: 2, url: '/static/p1/seg_002.mp4', duration_seconds: 4 },
      { index: 1, url: '/static/p1/seg_001.mp4', duration_seconds: 3.9 },
    ]);
    expect(applied).toBe(2);
    // 行 1（源上 10-14s）拿到的是 index 1 的文件。
    expect(clipForKey('1')?.data.videoUrl).toBe('/static/p1/seg_001.mp4');
    expect(clipForKey('2')?.data.videoUrl).toBe('/static/p1/seg_002.mp4');
    // 时长以产物实测值为准（3.9 而不是请求区间的 4）。
    expect(clipForKey('1')?.data.durationMs).toBe(3900);
  });

  it('越界 / 找不到节点的条目丢掉，不误写', () => {
    const scattered = scatter();
    expect(scattered.ok).toBe(true);
    if (!scattered.ok) return;
    const applied = applyVideoStoryClipResults(scattered.plan, [
      { index: 3, url: '/static/p1/seg_003.mp4' },
      { index: 0, url: '/static/p1/seg_000.mp4' },
      { index: 1, url: '/static/p1/seg_001.mp4' },
    ]);
    expect(applied).toBe(1);
    expect(clipForKey('1')?.data.videoUrl).toBe('/static/p1/seg_001.mp4');
    expect(clipForKey('2')?.data.videoUrl ?? null).toBeNull();
  });

  it('失败信息落在承载它的节点上，且能被当作「未完成」重切', () => {
    const scattered = scatter();
    expect(scattered.ok).toBe(true);
    if (!scattered.ok) return;
    failVideoStoryClipNodes(scattered.plan, 'ffmpeg 退出码 1');
    expect(clipNodes().every((node) => node.data.generationError === 'ffmpeg 退出码 1')).toBe(true);
    const result = plan();
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.pendingCount).toBe(2);
  });
});

describe('逐镜切片段 · 规格换算', () => {
  it('时间码解析：MM:SS / HH:MM:SS / 全角冒号 / 裸秒数都认，半秒不取整', () => {
    const specs = buildVideoStoryClipSpecs([
      { shotNumber: 1, startTime: '00:00', endTime: '00:04' },
      { shotNumber: 2, startTime: '0:01:02', endTime: '0:01:07' },
      { shotNumber: 3, startTime: '00：10', endTime: '00：12.5' },
      { shotNumber: 4, startTime: '18', endTime: '22.5' },
    ]);
    expect(specs.map((spec) => [spec.startSec, spec.endSec])).toEqual([
      [0, 4],
      [62, 67],
      [10, 12.5],
      [18, 22.5],
    ]);
  });

  it('解析不出端点 / 区间非法的行：区间为 null，展示字段照留', () => {
    const specs = buildVideoStoryClipSpecs([
      { shotNumber: 1, duration: '4s' },
      { shotNumber: 2, startTime: '00:04', endTime: '00:04' },
      { shotNumber: 3, startTime: '特写', endTime: '00:09' },
      { shotNumber: 4, startTime: '00:09', endTime: '00:12', shotSize: '中景' },
    ]);
    expect(specs[0].startSec).toBeNull();
    expect(specs[0].endSec).toBeNull();
    // 只有时长列：切不出来（不知道从哪开始），不能从 0 秒开始瞎切；
    // 展示串与另两条路同源，没有开始/结束就是空。
    expect(specs[0].timeRange).toBeNull();
    // end == start 是非法区间。
    expect(specs[1].startSec).toBeNull();
    // 结束时间解析不出来。
    expect(specs[2].endSec).toBeNull();
    expect(specs[3].startSec).toBe(9);
    expect(specs[3].name).toBe('镜头 4 · 中景 · 片段');
    // 展示串保留原始写法，便于溯源。
    expect(specs[3].timeRange).toBe('00:09 – 00:12');
  });

  it('重号镜号补 #n 后缀，与出图 / 出视频两条路同一套行键', () => {
    const specs = buildVideoStoryClipSpecs([
      { shotNumber: 1, startTime: '00:00', endTime: '00:02' },
      { shotNumber: 1, startTime: '00:02', endTime: '00:04' },
    ]);
    expect(specs.map((spec) => spec.rowKey)).toEqual(['1', '1#2']);
  });
});

describe('逐镜切片段 · 两套派生互不串台', () => {
  beforeEach(() => {
    resetStoryboardSettle();
    seedSourceNode();
  });

  it('片段节点不会被算成「已派生的出片视频节点」', () => {
    const scattered = scatter();
    expect(scattered.ok).toBe(true);
    expect(clipNodes()).toHaveLength(2);
    // 片段节点带的是 videoStoryClipSourceNodeId，绝没有 videoStorySourceNodeId ——
    // 出视频那条路的清点只认后者，所以不会把它们当已派生的热点。
    const claimedByShotVideoPath = videoNodes().filter(
      (node) => node.data.videoStorySourceNodeId === SOURCE_ID,
    );
    expect(claimedByShotVideoPath).toHaveLength(0);
  });

  it('重建时不会把出视频那条路派生的普通视频节点误删', () => {
    // 同一个故事节点派生的**出片**视频节点：Owner 相同、但没有片段标记。
    // 重建只该清片段节点，这个必须留着。
    const shotVideo = useCanvasStore.getState().addNode(
      CANVAS_NODE_TYPES.video,
      { x: 2000, y: 2000 },
      { videoUrl: '/static/p1/shot-1.mp4', videoStorySourceNodeId: SOURCE_ID } as never,
    );
    const scattered = scatter();
    expect(scattered.ok).toBe(true);
    // 它没有被画成片段（清点靠片段标记，不靠 owner）。
    expect(clipNodes()).toHaveLength(2);
    expect(clipNodes().some((node) => node.id === shotVideo)).toBe(false);

    setRows(renamedRows());
    const rebuilt = scatter();
    expect(rebuilt.ok).toBe(true);
    if (!rebuilt.ok) return;
    expect(rebuilt.mode).toBe('rebuilt');
    const liveIds = new Set(useCanvasStore.getState().nodes.map((node) => node.id));
    expect(liveIds.has(shotVideo)).toBe(true);
  });
});
