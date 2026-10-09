// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { beforeEach, describe, expect, it, vi } from 'vitest';

const publish = vi.fn((..._args: unknown[]) => undefined);
const downloadUrlAsFile = vi.fn(
  async (_url: string, _filename: string): Promise<void> => undefined,
);

vi.mock('./canvasServices', () => ({
  canvasEventBus: { publish: (...args: unknown[]) => publish(...args) },
}));
vi.mock('@/lib/browserDownload', () => ({
  downloadUrlAsFile: (url: string, filename: string) =>
    downloadUrlAsFile(url, filename),
}));

import {
  CANVAS_NODE_TYPES,
  type CanvasNode,
  type CanvasNodeType,
} from '@/features/canvas/domain/canvasNodes';
import { getNodeTypeCapabilities } from '@/features/canvas/domain/nodeCapabilityCatalog';
import { runNodeCapability } from './nodeCapabilityDispatch';

function node(
  type: CanvasNodeType,
  data: Record<string, unknown> = {},
  id = `${type}-instance`,
): CanvasNode {
  return { id, type, position: { x: 0, y: 0 }, data } as CanvasNode;
}

const VIDEO_NODE = () =>
  node(CANVAS_NODE_TYPES.video, {
    videoUrl: 'https://example.test/shot.mp4',
    displayName: 'shot',
    durationMs: 4000,
  });

function capabilityOf(type: CanvasNodeType, capabilityId: string) {
  const capability = getNodeTypeCapabilities(type).find(
    (item) => item.id === capabilityId,
  );
  if (!capability) throw new Error(`missing capability ${type}/${capabilityId}`);
  return capability;
}

beforeEach(() => {
  publish.mockClear();
  downloadUrlAsFile.mockClear();
});

describe('runNodeCapability', () => {
  it('dispatches tool-dialog capabilities onto the canvas event bus', async () => {
    const receipt = await runNodeCapability(
      node(CANVAS_NODE_TYPES.upload, { imageUrl: 'https://example.test/a.png' }),
      capabilityOf(CANVAS_NODE_TYPES.upload, 'image-crop'),
    );

    expect(receipt).toMatchObject({ ok: true, capabilityId: 'image-crop' });
    expect(publish).toHaveBeenCalledWith('tool-dialog/open', {
      nodeId: 'uploadNode-instance',
      toolType: 'crop',
    });
  });

  it('refuses a capability whose declared tool type is not a real canvas tool', async () => {
    const receipt = await runNodeCapability(
      VIDEO_NODE(),
      {
        id: 'bogus-tool',
        label: 'bogus',
        hint: 'bogus',
        category: 'image',
        powerTool: null,
        execution: { kind: 'canvas_event', event: 'tool-dialog/open', params: { tool_type: 'nope' } },
      },
    );

    expect(receipt.ok).toBe(false);
    expect(receipt.reason).toContain('工具类型');
    expect(publish).not.toHaveBeenCalled();
  });

  it('refuses an event nobody on the canvas subscribes to', async () => {
    const receipt = await runNodeCapability(VIDEO_NODE(), {
      id: 'ghost-event',
      label: 'ghost',
      hint: 'ghost',
      category: 'video',
      powerTool: null,
      execution: { kind: 'canvas_event', event: 'video-node/not-a-real-event' },
    });

    expect(receipt.ok).toBe(false);
    expect(receipt.reason).toContain('没有订阅');
    expect(publish).not.toHaveBeenCalled();
  });

  it('dispatches each video frame capture mode verbatim', async () => {
    for (const [capabilityId, mode] of [
      ['video-capture-first-frame', 'first'],
      ['video-capture-last-frame', 'last'],
      ['video-capture-current-frame', 'current'],
    ] as const) {
      publish.mockClear();
      const receipt = await runNodeCapability(
        VIDEO_NODE(),
        capabilityOf(CANVAS_NODE_TYPES.video, capabilityId),
      );
      expect(receipt.ok, capabilityId).toBe(true);
      expect(publish).toHaveBeenCalledWith('video-node/capture-frame', {
        nodeId: 'videoNode-instance',
        mode,
      });
    }
  });

  it('runs the injected task handler for async capabilities and reports its refusal', async () => {
    const handler = vi.fn();
    const ok = await runNodeCapability(
      VIDEO_NODE(),
      capabilityOf(CANVAS_NODE_TYPES.video, 'video-story-analysis'),
      { taskHandlers: { 'video-story-analysis': handler } },
    );
    expect(ok).toMatchObject({ ok: true, channel: 'async_task:freezone_video_story' });
    expect(handler).toHaveBeenCalledTimes(1);

    const refused = await runNodeCapability(
      VIDEO_NODE(),
      capabilityOf(CANVAS_NODE_TYPES.video, 'video-upscale'),
      {
        taskHandlers: {
          'video-upscale': (run) => run.reject('请先上传视频'),
        },
      },
    );
    expect(refused.ok).toBe(false);
    expect(refused.reason).toBe('请先上传视频');
  });

  it('refuses an async capability when the context has no handler for it', async () => {
    const receipt = await runNodeCapability(
      VIDEO_NODE(),
      capabilityOf(CANVAS_NODE_TYPES.video, 'video-audio-separate'),
    );

    expect(receipt.ok).toBe(false);
    expect(receipt.reason).toContain('没有这个任务的执行者');
  });

  it('downloads a video through the browser channel', async () => {
    const receipt = await runNodeCapability(
      VIDEO_NODE(),
      capabilityOf(CANVAS_NODE_TYPES.video, 'video-download'),
    );

    expect(receipt).toMatchObject({ ok: true, channel: 'browser_ui:video_download' });
    expect(downloadUrlAsFile).toHaveBeenCalledTimes(1);
    expect(downloadUrlAsFile.mock.calls[0][1]).toBe('shot.mp4');
  });

  it('refuses download and fullscreen when the node has no video yet', async () => {
    const empty = node(CANVAS_NODE_TYPES.video, {});

    for (const capabilityId of ['video-download', 'video-fullscreen']) {
      const receipt = await runNodeCapability(
        empty,
        capabilityOf(CANVAS_NODE_TYPES.video, capabilityId),
      );
      expect(receipt.ok, capabilityId).toBe(false);
      expect(receipt.reason).toContain('视频');
    }
    expect(downloadUrlAsFile).not.toHaveBeenCalled();
  });

  it('routes fullscreen to the canvas viewer', async () => {
    const receipt = await runNodeCapability(
      VIDEO_NODE(),
      capabilityOf(CANVAS_NODE_TYPES.video, 'video-fullscreen'),
    );

    expect(receipt.ok).toBe(true);
    expect(publish).toHaveBeenCalledWith('video-viewer/open', {
      videoUrl: 'https://example.test/shot.mp4',
      title: 'shot',
    });
  });

  it('refuses node_action capabilities: the node submit button owns them', async () => {
    const receipt = await runNodeCapability(
      VIDEO_NODE(),
      capabilityOf(CANVAS_NODE_TYPES.video, 'video-generation'),
    );

    expect(receipt.ok).toBe(false);
    expect(receipt.reason).toContain('提交按钮');
    expect(publish).not.toHaveBeenCalled();
  });

  it('refuses unwired and catalog_only capabilities with the catalog reason', async () => {
    const unwired = await runNodeCapability(
      VIDEO_NODE(),
      capabilityOf(CANVAS_NODE_TYPES.video, 'depth-motion-capture'),
    );
    expect(unwired.ok).toBe(false);
    expect(unwired.reason).toContain('尚未接入真实深度模型');

    const catalogOnly = await runNodeCapability(
      node(CANVAS_NODE_TYPES.audio, { audioUrl: 'https://example.test/a.wav' }),
      capabilityOf(CANVAS_NODE_TYPES.audio, 'audio-preview'),
    );
    expect(catalogOnly.ok).toBe(false);
    expect(catalogOnly.channel).toBe('unwired');
  });

  it('refuses powerhub capabilities and points the caller at the panel', async () => {
    const receipt = await runNodeCapability(
      node(CANVAS_NODE_TYPES.imageGen, { imageUrl: 'https://example.test/a.png' }),
      capabilityOf(CANVAS_NODE_TYPES.imageGen, 'expression'),
    );

    expect(receipt.ok).toBe(false);
    expect(receipt.channel).toBe('powerhub');
    expect(receipt.reason).toContain('PowerHub');
  });
});
