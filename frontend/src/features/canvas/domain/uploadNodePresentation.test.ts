// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from 'vitest';

import {
  isOpaqueMediaFilename,
  shouldShowUploadNodeHeader,
} from '@/features/canvas/domain/uploadNodePresentation';

describe('uploadNodePresentation', () => {
  it('识别服务端 hash 媒体名', () => {
    expect(isOpaqueMediaFilename('9238722740ae54629cb917a7d2e7adac.jpg')).toBe(true);
    expect(isOpaqueMediaFilename('assets/9238722740ae54629cb917a7d2e7adac.jpg')).toBe(true);
    expect(isOpaqueMediaFilename('img_v3_02146_03e7bb26-1045-4fae-ad5e-e9fe020ed89g.jpg')).toBe(true);
    expect(isOpaqueMediaFilename('image.png')).toBe(true);
    expect(isOpaqueMediaFilename('草莓英雄.jpg')).toBe(false);
  });

  it('已上传图片的 hash 标题不占据缩略图上方', () => {
    expect(
      shouldShowUploadNodeHeader({
        hasMediaContent: true,
        imageOnly: false,
        title: '9238722740ae54629cb917a7d2e7adac.jpg',
      }),
    ).toBe(false);
  });

  it('没有人工命名的图片节点只显示缩略图', () => {
    expect(
      shouldShowUploadNodeHeader({
        hasMediaContent: true,
        imageOnly: true,
        title: '上传图片',
      }),
    ).toBe(false);
  });

  it('保留人工命名与空节点的可读标签', () => {
    expect(
      shouldShowUploadNodeHeader({
        hasMediaContent: true,
        imageOnly: true,
        title: '草莓主角参考',
      }),
    ).toBe(true);
    expect(
      shouldShowUploadNodeHeader({
        hasMediaContent: false,
        imageOnly: true,
        title: '上传图片',
      }),
    ).toBe(true);
  });
});
