// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { afterEach, describe, expect, it, vi } from 'vitest';

import { copyImageSourceToClipboard } from './image';

describe('copyImageSourceToClipboard', () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it('copies a canvas image URL as a native image clipboard item', async () => {
    const write = vi.fn().mockResolvedValue(undefined);
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(new Uint8Array([1, 2, 3]), {
        status: 200,
        headers: { 'content-type': 'image/png' },
      }),
    );
    class ClipboardItemMock {
      constructor(readonly items: Record<string, Blob>) {}
    }

    vi.stubGlobal('fetch', fetchMock);
    vi.stubGlobal('ClipboardItem', ClipboardItemMock);
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { write },
    });

    await copyImageSourceToClipboard('/static/projects/demo/freezone/frame.png');

    expect(fetchMock).toHaveBeenCalledWith('/static/projects/demo/freezone/frame.png', {
      credentials: 'same-origin',
    });
    const item = write.mock.calls[0][0][0] as ClipboardItemMock;
    expect(item.items['image/png']).toMatchObject({ type: 'image/png', size: 3 });
  });

  it('converts JPEG images to PNG before writing to the clipboard', async () => {
    const write = vi.fn().mockResolvedValue(undefined);
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(new Uint8Array([1, 2, 3]), {
        status: 200,
        headers: { 'content-type': 'image/jpeg' },
      }),
    );
    const close = vi.fn();
    const drawImage = vi.fn();
    const bitmap = { width: 1445, height: 1088, close };
    const createImageBitmap = vi.fn().mockResolvedValue(bitmap);
    const canvas = {
      width: 0,
      height: 0,
      getContext: vi.fn().mockReturnValue({ drawImage }),
      toBlob: (callback: BlobCallback, type?: string) =>
        callback(new Blob([new Uint8Array([9, 8, 7])], { type })),
    };
    class ClipboardItemMock {
      constructor(readonly items: Record<string, Blob>) {}
    }

    vi.stubGlobal('fetch', fetchMock);
    vi.stubGlobal('ClipboardItem', ClipboardItemMock);
    vi.stubGlobal('createImageBitmap', createImageBitmap);
    vi.spyOn(document, 'createElement').mockReturnValueOnce(canvas as never);
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { write },
    });

    await copyImageSourceToClipboard('/static/projects/demo/freezone/frame.jpg');

    expect(createImageBitmap).toHaveBeenCalledTimes(1);
    expect(canvas.width).toBe(1445);
    expect(canvas.height).toBe(1088);
    expect(drawImage).toHaveBeenCalledWith(bitmap, 0, 0);
    expect(close).toHaveBeenCalledTimes(1);
    const item = write.mock.calls[0][0][0] as ClipboardItemMock;
    expect(item.items['image/png']).toMatchObject({ type: 'image/png', size: 3 });
    expect(item.items['image/jpeg']).toBeUndefined();
  });
});
