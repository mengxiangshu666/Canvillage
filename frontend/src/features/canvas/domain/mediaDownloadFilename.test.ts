// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from 'vitest';
import { resolveMediaDownloadFilename } from './mediaDownloadFilename';

describe('resolveMediaDownloadFilename', () => {
  it('keeps a real source filename verbatim', () => {
    expect(
      resolveMediaDownloadFilename({
        sourceFileName: 'EP1-B03-take2.mp4',
        displayName: '镜头 3',
        fallback: 'video-abc',
        extension: '.mp4',
      }),
    ).toBe('EP1-B03-take2.mp4');
  });

  it('does not double the extension when the node was renamed with one', () => {
    // 这条是回归钉子：工具栏此前无条件给 `displayName` 追加 `.mp4`，用户把节点改名成
    // 「成片.mp4」就会存出「成片.mp4.mp4」，而分发器那条存出「成片.mp4」—— 同一个下载
    // 动作两个文件名。
    expect(
      resolveMediaDownloadFilename({
        displayName: '成片.mp4',
        fallback: 'video-abc',
        extension: '.mp4',
      }),
    ).toBe('成片.mp4');
  });

  it('appends the extension when it is missing', () => {
    expect(
      resolveMediaDownloadFilename({
        displayName: '成片',
        fallback: 'video-abc',
        extension: '.mp4',
      }),
    ).toBe('成片.mp4');
  });

  it('matches the extension case-insensitively', () => {
    expect(
      resolveMediaDownloadFilename({
        sourceFileName: 'TAKE.MP4',
        fallback: 'video-abc',
        extension: '.mp4',
      }),
    ).toBe('TAKE.MP4');
  });

  it('falls back when both name sources are blank', () => {
    for (const blank of [null, undefined, '', '   ', 42]) {
      expect(
        resolveMediaDownloadFilename({
          sourceFileName: blank,
          displayName: blank,
          fallback: 'video-abc',
          extension: '.mp4',
        }),
      ).toBe('video-abc.mp4');
    }
  });
});
