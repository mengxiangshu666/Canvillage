import { describe, expect, it } from 'vitest';

import {
  framesToCaptures,
  resolveExtractableVideoUrl,
  resolveFrameAspectRatio,
  resolveFrameCaptureSize,
  videoFrameCaptureMetadata,
} from './extractedFrames';

describe('captured video frame provenance', () => {
  it('keeps source version and capture mode without inventing a measured timestamp', () => {
    expect(videoFrameCaptureMetadata('video-A', '/old.mp4', 'last', 3.95)).toEqual({
      source_kind: 'video_frame_capture', source_node_id: 'video-A', source_video_url: '/old.mp4',
      capture_mode: 'last', requested_seconds: 3.95,
    });
    expect(videoFrameCaptureMetadata('video-A', '/new.mp4', 'first', 0).source_video_url).toBe('/new.mp4');
    expect(videoFrameCaptureMetadata('video-A', '/old.mp4', 'last', Number.MAX_SAFE_INTEGER).requested_seconds).toBeNull();
    expect(videoFrameCaptureMetadata('video-A', '/old.mp4', 'current', Number.NaN).requested_seconds).toBeNull();
  });
});

describe('resolveFrameAspectRatio', () => {
  it('把视频像素尺寸约分成画幅档位', () => {
    expect(resolveFrameAspectRatio({ widthPx: 1920, heightPx: 1080 })).toBe('16:9');
    expect(resolveFrameAspectRatio({ widthPx: 1080, heightPx: 1920 })).toBe('9:16');
    expect(resolveFrameAspectRatio({ widthPx: 1280, heightPx: 720 })).toBe('16:9');
  });

  it('尺寸缺失时回落节点上的比例字段，并同样约分', () => {
    expect(
      resolveFrameAspectRatio({ widthPx: null, heightPx: null, fallback: '1920:1080' }),
    ).toBe('16:9');
  });

  it('尺寸不合法时忽略它，改看回落字段', () => {
    expect(
      resolveFrameAspectRatio({ widthPx: 0, heightPx: 1080, fallback: '4:3' }),
    ).toBe('4:3');
    expect(
      resolveFrameAspectRatio({ widthPx: Number.NaN, heightPx: 1080, fallback: '1:1' }),
    ).toBe('1:1');
  });

  it('回落字段不是 W:H 形式时不可信，最终回落 16:9', () => {
    expect(resolveFrameAspectRatio({})).toBe('16:9');
    expect(resolveFrameAspectRatio({ fallback: 'source' })).toBe('16:9');
    expect(resolveFrameAspectRatio({ fallback: '0:9' })).toBe('16:9');
  });
});

describe('framesToCaptures', () => {
  it('没有镜头分析时标题只用帧号（从 1 起）', () => {
    const captures = framesToCaptures([
      { url: '/static/a.png', index: 0 },
      { url: '/static/b.png', index: 6 },
    ]);
    expect(captures.map((capture) => capture.label)).toEqual(['帧 #1', '帧 #7']);
    expect(captures.map((capture) => capture.url)).toEqual(['/static/a.png', '/static/b.png']);
  });

  it('有分析时把景别与角度拼进标题，完整分析留给下游', () => {
    const captures = framesToCaptures([
      {
        url: '/static/a.png',
        index: 2,
        analysis: {
          shot_type: '特写',
          angle: '仰拍',
          camera_movement: '推',
          mood: '紧张',
          color_tone: '冷蓝',
          suggested_prompt: 'close-up, low angle, tense',
        },
      },
    ]);
    expect(captures[0].label).toBe('帧 #3 · 特写 / 仰拍');
    expect(captures[0].metadata).toMatchObject({
      source_kind: 'extracted_frame',
      frame_index: 2,
      suggested_prompt: 'close-up, low angle, tense',
    });
    expect(captures[0].metadata?.shot_analysis).toMatchObject({
      camera_movement: '推',
      mood: '紧张',
      color_tone: '冷蓝',
    });
  });

  it('只有其中一个字段也拼得出来，全空则退回纯帧号', () => {
    expect(
      framesToCaptures([{ url: '/a.png', index: 0, analysis: { shot_type: '全景' } }])[0].label,
    ).toBe('帧 #1 · 全景');
    expect(
      framesToCaptures([{ url: '/a.png', index: 0, analysis: { angle: '俯拍' } }])[0].label,
    ).toBe('帧 #1 · 俯拍');
    expect(
      framesToCaptures([{ url: '/a.png', index: 0, analysis: { shot_type: '  ', angle: '' } }])[0]
        .label,
    ).toBe('帧 #1');
  });

  it('丢弃没有地址的帧，避免落出一个空图节点', () => {
    const captures = framesToCaptures([
      { url: '', index: 0 },
      { url: '/static/ok.png', index: 1 },
    ]);
    expect(captures).toHaveLength(1);
    expect(captures[0].url).toBe('/static/ok.png');
    // 帧号仍按后端给的 index 算，不因过滤而错位。
    expect(captures[0].label).toBe('帧 #2');
  });
});

describe('resolveFrameCaptureSize', () => {
  it('以 1920 为长边，约分后与传入比例一致', () => {
    expect(resolveFrameCaptureSize('16:9')).toEqual({ width: 1920, height: 1080 });
    expect(resolveFrameCaptureSize('9:16')).toEqual({ width: 1080, height: 1920 });
    expect(resolveFrameCaptureSize('1:1')).toEqual({ width: 1920, height: 1920 });
  });

  it('比例串不可解析时回落 16:9', () => {
    expect(resolveFrameCaptureSize('')).toEqual({ width: 1920, height: 1080 });
    expect(resolveFrameCaptureSize('x:y')).toEqual({ width: 1920, height: 1080 });
  });
});

describe('resolveExtractableVideoUrl', () => {
  it('项目内地址原样交给后端（后端只解析 path，同源绝对地址折回 pathname）', () => {
    expect(resolveExtractableVideoUrl('/static/projects/p1/freezone/a.mp4')).toBe(
      '/static/projects/p1/freezone/a.mp4',
    );
    expect(
      resolveExtractableVideoUrl('/api/v1/projects/p1/media/freezone/a.mp4'),
    ).toBe('/api/v1/projects/p1/media/freezone/a.mp4');
    expect(resolveExtractableVideoUrl(`${window.location.origin}/static/a.mp4?v=1`)).toBe(
      '/static/a.mp4?v=1',
    );
  });

  it('跨源 / 非 http 地址不可抽，退回选文件（否则点了按钮才吃 400）', () => {
    expect(resolveExtractableVideoUrl('https://cdn.example.com/a.mp4')).toBeNull();
    expect(resolveExtractableVideoUrl('blob:http://localhost/abc')).toBeNull();
    expect(resolveExtractableVideoUrl('data:video/mp4;base64,AAAA')).toBeNull();
  });

  it('空值与非字符串一律视为没有源视频', () => {
    expect(resolveExtractableVideoUrl(null)).toBeNull();
    expect(resolveExtractableVideoUrl(undefined)).toBeNull();
    expect(resolveExtractableVideoUrl('   ')).toBeNull();
  });
});

