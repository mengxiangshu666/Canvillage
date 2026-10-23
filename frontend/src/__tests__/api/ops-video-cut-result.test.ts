// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { apiCall } from '@/api/client';
import { fetchFreezoneVideoCutResult } from '@/api/ops';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('@/api/client', () => ({
  apiCall: vi.fn(),
}));

describe('切段结果读取', () => {
  beforeEach(() => {
    vi.mocked(apiCall).mockReset();
  });

  it('apiCall 已经拆过 envelope，不能再次读 .data', async () => {
    vi.mocked(apiCall).mockResolvedValue({
      segments: [{
        index: 0,
        start: 2.4,
        end: 3,
        url: '/cut/segment_000.mp4',
        size: 100,
        duration_seconds: 0.6,
      }],
      segment_count: 1,
      source_duration_seconds: 3,
    });
    const result = await fetchFreezoneVideoCutResult('project-1', 'job-1');
    expect(result.segment_count).toBe(1);
    expect(result.segments[0]?.url).toBe('/cut/segment_000.mp4');
    expect(result.segments[0]?.duration_seconds).toBe(0.6);
  });

  it('未知字段保持为空，不拿请求区间冒充时长', async () => {
    vi.mocked(apiCall).mockResolvedValue({ segments: [] });
    const result = await fetchFreezoneVideoCutResult('project-1', 'job-2');
    expect(result.segments).toEqual([]);
    expect(result.segment_count).toBe(0);
    expect(result.source_duration_seconds).toBeNull();
  });
});
