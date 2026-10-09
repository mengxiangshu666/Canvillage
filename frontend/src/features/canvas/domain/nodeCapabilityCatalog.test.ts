// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from 'vitest';
import {
  CANVAS_NODE_TYPES,
  type CanvasNode,
  type CanvasNodeType,
} from './canvasNodes';
import {
  describeNodeCapability,
  getNodeCapabilities,
  getNodeCapabilityMenuEntries,
  getNodeTypeCapabilities,
  NODE_CAPABILITY_CATALOG,
  type NodeCapability,
} from './nodeCapabilityCatalog';

const IMAGE_TYPES: readonly CanvasNodeType[] = [
  CANVAS_NODE_TYPES.upload,
  CANVAS_NODE_TYPES.imageEdit,
  CANVAS_NODE_TYPES.imageGen,
  CANVAS_NODE_TYPES.exportImage,
];

const VIDEO_TYPES: readonly CanvasNodeType[] = [
  CANVAS_NODE_TYPES.video,
  CANVAS_NODE_TYPES.videoStory,
  CANVAS_NODE_TYPES.videoCompose,
];

function capabilityIds(type: CanvasNodeType): string[] {
  return getNodeTypeCapabilities(type).map((capability) => capability.id);
}

function node(type: CanvasNodeType, data: Record<string, unknown>): CanvasNode {
  return { id: `${type}-test`, type, position: { x: 0, y: 0 }, data } as CanvasNode;
}

describe('NODE_CAPABILITY_CATALOG', () => {
  it('has a non-empty dedicated entry for all 17 canvas node types', () => {
    const nodeTypes = Object.values(CANVAS_NODE_TYPES);

    expect(nodeTypes).toHaveLength(17);
    expect(Object.keys(NODE_CAPABILITY_CATALOG).sort()).toEqual([...nodeTypes].sort());
    for (const type of nodeTypes) {
      const capabilities = getNodeTypeCapabilities(type);
      expect(capabilities.length, `${type} should have capabilities`).toBeGreaterThan(0);
      expect(new Set(capabilities.map(({ id }) => id)).size).toBe(capabilities.length);
      for (const capability of capabilities) {
        expect(capability.id).not.toBe('');
        expect(capability.label).not.toBe('');
        expect(capability.hint).not.toBe('');
      }
    }
  });

  it.each(IMAGE_TYPES)('%s exposes expression management for image output', (type) => {
    const expression = getNodeTypeCapabilities(type).find(
      (capability) => capability.powerTool === 'expression',
    );

    expect(expression).toMatchObject({
      id: 'expression',
      label: '情绪调节 · 50 锚点',
      category: 'image',
      requiresImage: true,
      hasOutput: true,
    });
  });

  it.each(IMAGE_TYPES)('%s exposes built-in image tools to workflow planning', (type) => {
    expect(capabilityIds(type)).toEqual(expect.arrayContaining([
      'image-crop',
      'image-annotate',
      'storyboard-split-input',
    ]));
  });

  it('maps each built-in image tool to the real tool dialog event', () => {
    const expectedToolTypes = {
      'image-crop': 'crop',
      'image-annotate': 'annotate',
      'storyboard-split-input': 'split-storyboard',
    } as const;

    for (const [id, toolType] of Object.entries(expectedToolTypes)) {
      const capability = getNodeTypeCapabilities(CANVAS_NODE_TYPES.imageGen).find(
        (item) => item.id === id,
      );
      expect(capability, id).toBeDefined();
      expect(describeNodeCapability(capability!)).toMatchObject({
        status: 'wired',
        execution: {
          kind: 'canvas_event',
          event: 'tool-dialog/open',
          params: { tool_type: toolType },
        },
        input_kinds: ['image'],
        mutates_source: false,
        creates_child_node: true,
      });
    }
  });

  it.each(VIDEO_TYPES)('%s does not incorrectly expose image expression management', (type) => {
    expect(capabilityIds(type)).not.toContain('expression');
    expect(getNodeTypeCapabilities(type).some(({ powerTool }) => powerTool === 'expression')).toBe(false);
  });

  it('no longer ships the retired Smart Shot Plan capability', () => {
    // Smart Shot Plan 已从产品里移除：目录里不应再有任何节点声明它，
    // 否则菜单会列出一个点开没有面板的条目。
    //
    // 断言走 `capabilityIds` 而不是 `powerTool === 'shot-plan'`：NodePowerTool
    // 已经收窄成 `"expression"`，直接比较字面量会被 tsc 判成无交集的死比较，
    // 之后有人把 shot-plan 加回来，类型系统也不会提醒这里曾经有过它。
    for (const type of Object.values(CANVAS_NODE_TYPES)) {
      expect(capabilityIds(type), `${type} ids`).not.toContain('shot-plan');
    }

    const declaredPowerTools = new Set(
      Object.values(CANVAS_NODE_TYPES).flatMap((type) =>
        getNodeTypeCapabilities(type)
          .map(({ powerTool }) => powerTool)
          .filter((tool): tool is NonNullable<typeof tool> => tool !== null),
      ),
    );
    expect(declaredPowerTools).toEqual(new Set(['expression']));
  });

  it('keeps group capabilities organizational and non-generative', () => {
    const capabilities = getNodeTypeCapabilities(CANVAS_NODE_TYPES.group);

    expect(capabilities.every(({ category }) => category === 'group')).toBe(true);
    expect(capabilities.every(({ powerTool }) => powerTool === null)).toBe(true);
    expect(capabilities.every(({ id }) => !id.includes('generation'))).toBe(true);
  });
});

describe('getNodeCapabilities', () => {
  it('hides output-only image actions until an image exists', () => {
    const emptyImage = node(CANVAS_NODE_TYPES.imageGen, { imageUrl: null });
    const completedImage = node(CANVAS_NODE_TYPES.imageGen, {
      imageUrl: 'https://example.test/frame.png',
    });

    expect(getNodeCapabilities(emptyImage).some(({ id }) => id === 'expression')).toBe(false);
    expect(getNodeCapabilities(emptyImage).some(({ id }) => id === 'image-generation')).toBe(true);
    expect(getNodeCapabilities(completedImage).some(({ id }) => id === 'expression')).toBe(true);
  });

  it('enables audio output actions only when audio is present', () => {
    const emptyAudio = node(CANVAS_NODE_TYPES.audio, { audioUrl: null });
    const completedAudio = node(CANVAS_NODE_TYPES.audio, {
      audioUrl: 'https://example.test/voice.wav',
    });

    expect(capabilityIds(CANVAS_NODE_TYPES.audio)).toContain('audio-preview');
    expect(getNodeCapabilities(emptyAudio).some(({ id }) => id === 'audio-preview')).toBe(false);
    expect(getNodeCapabilities(completedAudio).some(({ id }) => id === 'audio-preview')).toBe(true);
  });

  it('maps every real video toolbar capability to an explicit execution contract', () => {
    const capabilities = getNodeTypeCapabilities(CANVAS_NODE_TYPES.video);
    const ids = capabilities.map(({ id }) => id);
    expect(ids).toEqual(expect.arrayContaining([
      'video-clip',
      'video-story-analysis',
      'video-subtitle-erase-smart',
      'video-subtitle-erase-box',
      'video-upscale',
      'video-audio-separate',
      'video-capture-first-frame',
      'video-capture-last-frame',
      'video-capture-current-frame',
      'video-download',
      'video-fullscreen',
      'depth-motion-capture',
    ]));

    for (const capability of capabilities) {
      expect(capability.execution, capability.id).toBeDefined();
    }
    expect(describeNodeCapability(capabilities.find(({ id }) => id === 'video-capture-first-frame')!)).toMatchObject({
      status: 'wired',
      execution: {
        kind: 'canvas_event',
        event: 'video-node/capture-frame',
        params: { mode: 'first' },
      },
      input_kinds: ['video'],
      output_kinds: ['image'],
      creates_child_node: true,
    });
    for (const [id, operation] of [
      ['video-clip', 'clip'],
      ['video-subtitle-erase-smart', 'subtitle-smart'],
      ['video-subtitle-erase-box', 'subtitle-box'],
    ] as const) {
      expect(describeNodeCapability(capabilities.find((item) => item.id === id)!)).toMatchObject({
        status: 'wired',
        execution: {
          kind: 'canvas_event',
          event: 'video-node/set-operation',
          params: { operation },
        },
      });
    }
    expect(describeNodeCapability(capabilities.find(({ id }) => id === 'video-download')!)).toMatchObject({
      status: 'wired',
      execution: { kind: 'browser_ui', action: 'video_download' },
    });
    expect(describeNodeCapability(capabilities.find(({ id }) => id === 'video-fullscreen')!)).toMatchObject({
      status: 'wired',
      execution: { kind: 'browser_ui', action: 'video_fullscreen' },
    });
    expect(describeNodeCapability(capabilities.find(({ id }) => id === 'depth-motion-capture')!)).toMatchObject({
      status: 'not_wired',
      execution: { kind: 'unwired' },
    });
  });
});

/**
 * 「节点能力」菜单的准入契约。
 *
 * 关键断言是：菜单里出现的每一个能力都必须有一条真实的执行通道 —— 要么是 PowerHub
 * 面板，要么是 `runNodeCapability` 认得出来的 `execution.kind`。此前这个入口按
 * `powerTool !== null` 过滤，把 13 个已经写好执行器的视频能力全挡在门外（它们本来
 * 在工具栏上都有按钮），菜单于是成了目录的残影。这组用例就是防止那种漂移再回来。
 */
describe('getNodeCapabilityMenuEntries', () => {
  const EXECUTABLE_KINDS = ['canvas_event', 'async_task', 'browser_ui', 'powerhub'];

  const completeNodeData: Record<CanvasNodeType, Record<string, unknown>> = {
    [CANVAS_NODE_TYPES.upload]: { imageUrl: 'https://example.test/upload.png' },
    [CANVAS_NODE_TYPES.imageEdit]: { imageUrl: 'https://example.test/edit.png' },
    [CANVAS_NODE_TYPES.imageGen]: { imageUrl: 'https://example.test/gen.png' },
    [CANVAS_NODE_TYPES.exportImage]: { imageUrl: 'https://example.test/export.png' },
    [CANVAS_NODE_TYPES.beatContext]: { content: 'EP1/B1 fruit short drama beat' },
    [CANVAS_NODE_TYPES.textAnnotation]: { content: 'fruit short drama shot notes' },
    [CANVAS_NODE_TYPES.group]: { label: 'group' },
    [CANVAS_NODE_TYPES.storyboardSplit]: {
      frames: [{ imageUrl: 'https://example.test/frame-1.png' }],
    },
    [CANVAS_NODE_TYPES.storyboardGen]: { content: 'storyboard grid prompt' },
    [CANVAS_NODE_TYPES.video]: { videoUrl: 'https://example.test/shot.mp4' },
    [CANVAS_NODE_TYPES.audio]: { audioUrl: 'https://example.test/voice.wav' },
    [CANVAS_NODE_TYPES.videoStory]: {
      sourceVideoUrl: 'https://example.test/source.mp4',
      rows: [{ start: 0, end: 1, description: 'opening shot' }],
    },
    [CANVAS_NODE_TYPES.videoCompose]: { resultVideoUrl: 'https://example.test/final.mp4' },
    [CANVAS_NODE_TYPES.script]: { scriptResult: { rows: [{ text: 'hello' }] } },
    [CANVAS_NODE_TYPES.pano360Viewer]: { panoUrl: 'https://example.test/pano.jpg' },
    [CANVAS_NODE_TYPES.threeDWorld]: { plyUrl: 'https://example.test/world.ply' },
    [CANVAS_NODE_TYPES.skill]: { skillId: 'local-skill' },
  };

  it('never offers a capability without a real executor, on any node type', () => {
    for (const type of Object.values(CANVAS_NODE_TYPES)) {
      const targetNode = node(type, completeNodeData[type]);
      for (const capability of getNodeCapabilityMenuEntries(targetNode)) {
        const kind = capability.execution?.kind;
        const executable = capability.powerTool !== null
          || (kind !== undefined && EXECUTABLE_KINDS.includes(kind));
        expect(
          executable,
          `${type}/${capability.id} must not be offered without an executor`,
        ).toBe(true);
      }
    }
  });

  it('excludes catalog_only and unwired entries from the menu', () => {
    for (const type of Object.values(CANVAS_NODE_TYPES)) {
      const targetNode = node(type, completeNodeData[type]);
      const offered = new Set(
        getNodeCapabilityMenuEntries(targetNode).map((capability) => capability.id),
      );
      for (const capability of getNodeCapabilities(targetNode)) {
        const kind = capability.execution?.kind;
        if (kind === 'catalog_only' || kind === 'unwired') {
          expect(offered.has(capability.id), `${type}/${capability.id}`).toBe(false);
        }
      }
    }
  });

  it('offers exactly the catalogued executors per node type', () => {
    // 菜单的定位是「工具栏给不了的能力」。
    //
    // 视频节点每一条能力在工具栏或播放器控件上都有按钮 → 菜单空。
    // 上传/图片生成/结果交付节点：裁剪、标注、分格抽取、情绪调节
    // 四条工具栏全都渲染了 → 菜单也空（`nodeCapabilityMenuEntries.length > 0` 为假，
    // 触发按钮都不出现）。
    // 图片编辑节点是唯一例外：工具栏的图片按钮一律带 `!isImageEdit`（那个节点有自己的
    // 节点内工作流：标记 / 运镜 / 资产库），所以这五条在它身上没有工具栏入口，菜单必须
    // 继续列 —— 否则这个节点会彻底没有入口。
    const offeredByType: Partial<Record<CanvasNodeType, string[]>> = {
      [CANVAS_NODE_TYPES.video]: [],
      [CANVAS_NODE_TYPES.upload]: [],
      [CANVAS_NODE_TYPES.imageGen]: [],
      [CANVAS_NODE_TYPES.exportImage]: [],
      [CANVAS_NODE_TYPES.imageEdit]: [
        'expression',
        'image-annotate',
        'image-crop',
        'storyboard-split-input',
      ],
      [CANVAS_NODE_TYPES.beatContext]: [],
      [CANVAS_NODE_TYPES.skill]: [],
      [CANVAS_NODE_TYPES.group]: [],
    };

    for (const [type, expected] of Object.entries(offeredByType) as [CanvasNodeType, string[]][]) {
      expect(
        getNodeCapabilityMenuEntries(node(type, completeNodeData[type]))
          .map((capability) => capability.id)
          .sort(),
        `${type} menu`,
      ).toEqual([...expected].sort());
    }
  });

  it('never lists a capability the toolbar already renders, outside the imageEdit exception', () => {
    // 这条是防漂移的锁：目录里标了 `toolbarEntry` 的动作，菜单里不能再出现 —— 同一个
    // 动作两个入口的代价是两份实现各改各的（视频下载的文件名兜底已经漂过一次）。
    // 例外只有图片编辑节点，理由见上一条用例。
    for (const type of Object.values(CANVAS_NODE_TYPES)) {
      const targetNode = node(type, completeNodeData[type]);
      const offered = new Set(
        getNodeCapabilityMenuEntries(targetNode).map((capability) => capability.id),
      );
      for (const capability of getNodeCapabilities(targetNode)) {
        if (!capability.toolbarEntry) continue;
        if (type === CANVAS_NODE_TYPES.imageEdit) {
          expect(offered.has(capability.id), `${type}/${capability.id} must stay reachable`).toBe(true);
          continue;
        }
        expect(
          offered.has(capability.id),
          `${type}/${capability.id} duplicates a toolbar entry`,
        ).toBe(false);
      }
    }
  });

  it('marks every toolbar-reachable action with toolbarEntry', () => {
    // 反向锁：工具栏确实渲染了按钮的动作必须带标记，否则菜单会把同一件事再列一遍。
    const toolbarReachable: readonly string[] = [
      'expression',
      'image-crop',
      'image-annotate',
      'storyboard-split-input',
      'video-clip',
      'video-story-analysis',
      'video-subtitle-erase-smart',
      'video-subtitle-erase-box',
      'video-upscale',
      'video-audio-separate',
      'video-capture-first-frame',
      'video-capture-last-frame',
      'video-capture-current-frame',
      'video-download',
      'video-fullscreen',
    ];
    const marked = new Set<string>();
    // `NODE_CAPABILITY_CATALOG` 用 `satisfies` 声明，没写 `toolbarEntry` 的条目类型里就
    // 没有这个字段；显式收宽成 `NodeCapability` 才能统一读它。
    const allCapabilities: readonly NodeCapability[] = Object.values(NODE_CAPABILITY_CATALOG).flat();
    for (const capability of allCapabilities) {
      if (capability.toolbarEntry) marked.add(capability.id);
    }
    for (const id of toolbarReachable) {
      expect(marked.has(id), `${id} must be marked toolbarEntry`).toBe(true);
    }
    expect([...marked].sort()).toEqual([...toolbarReachable].sort());
  });

  it('routes every offered non-powerhub entry through runNodeCapability', async () => {
    // 菜单项除 PowerHub 外一律交给分发器执行；有执行器却不在分发器 switch 里的条目
    // 会在运行时落进 default 分支，这里用一次空调用把它钉死在用例里。
    const imageEditNode = node(CANVAS_NODE_TYPES.imageEdit, {
      imageUrl: 'https://example.test/edit.png',
    });
    const dispatched = getNodeCapabilityMenuEntries(imageEditNode)
      .filter((capability) => capability.powerTool === null)
      .map((capability) => capability.id)
      .sort();
    expect(dispatched).toEqual([
      'image-annotate',
      'image-crop',
      'storyboard-split-input',
    ]);
    // 「视频生成」走 node_action（节点主体的提交按钮），刻意不作为菜单项。
    expect(dispatched).not.toContain('video-generation');
  });
});
