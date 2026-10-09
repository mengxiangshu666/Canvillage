// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { apiCall } from '@/api/client';
import { submitFreezoneStoryScript } from '@/api/ops';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('@/api/client', () => ({
  apiCall: vi.fn(),
}));

/**
 * 脚本生成的抽帧参数。
 *
 * 后端 `FreezoneStoryScriptGenerateRequest` 一直收 `max_frames` / `scene_threshold`，
 * runner 也一直读它们（`runners/freezone.py` 传给 `run_freezone_extract_frames_job`），
 * 兄弟接口 `submitFreezoneExtractFrames` 同样在发 —— **只有脚本这一条路两个字段都不发**，
 * 于是带视频参考生成时永远 20 帧 / 0.3，用户没有任何办法调整。
 *
 * 这一组用例钉住「字段真的进了报文」，并且口径与兄弟接口一致（缺省显式补 20 / 0.3，
 * 而不是靠后端兜底）。
 */
describe('脚本生成的抽帧参数', () => {
  beforeEach(() => {
    vi.mocked(apiCall).mockReset();
    vi.mocked(apiCall).mockResolvedValue({ job_id: 'job-1', task_key: 'task-1' });
  });

  function lastBody(): Record<string, unknown> {
    // 不用 `calls.at(-1)`：app 侧 tsconfig 的 lib 是 ES2020，`Array.prototype.at` 是
    // ES2022 —— 测试文件也在 `tsc -b` 的编译范围里，会把 `npm run build` 直接打红。
    const calls = vi.mocked(apiCall).mock.calls;
    const call = calls[calls.length - 1];
    return (call?.[1]?.json ?? {}) as Record<string, unknown>;
  }

  it('不传时显式补默认值，报文里看得见', async () => {
    await submitFreezoneStoryScript('58', { sourceText: '沈昭昭在深夜办公室醒来。' });
    expect(lastBody()).toMatchObject({ max_frames: 20, scene_threshold: 0.3 });
  });

  it('将所选视频模型送入脚本规划请求', async () => {
    await submitFreezoneStoryScript('58', { sourceText: '沿桥滑行', videoModel: 'direct_test_video' });
    expect(lastBody().video_model).toBe('direct_test_video');
  });

  it('传了就按传的发（这是此前完全做不到的事）', async () => {
    await submitFreezoneStoryScript('58', {
      videoUrl: '/static/admin/58/freezone/_uploads/ref.mp4',
      durationSec: 9,
      maxFrames: 32,
      sceneThreshold: 0.45,
    });
    expect(lastBody()).toMatchObject({
      video_url: '/static/admin/58/freezone/_uploads/ref.mp4',
      duration_sec: 9,
      max_frames: 32,
      scene_threshold: 0.45,
    });
  });

  it('0 是合法阈值，不能被 ?? 吃掉', async () => {
    // `sceneThreshold: 0` 语义是「不做场景切分，纯按间隔抽帧」——用 `||` 兜底会把它
    // 悄悄换成 0.3，所以实现里必须是 `??`。
    await submitFreezoneStoryScript('58', { sourceText: 'x', sceneThreshold: 0 });
    expect(lastBody().scene_threshold).toBe(0);
  });
});
