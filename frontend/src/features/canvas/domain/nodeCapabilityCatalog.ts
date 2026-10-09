// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import type { NodePowerTool } from '@/features/freezone/nodePowerHubStore';
import {
  CANVAS_NODE_TYPES,
  isImageEditNode,
  type CanvasNode,
  type CanvasNodeType,
} from './canvasNodes';

export type NodeCapabilityCategory =
  | 'image'
  | 'video'
  | 'audio'
  | 'text'
  | 'storyboard'
  | 'spatial'
  | 'skill'
  | 'group';

export type NodeCapabilityExecution =
  | { kind: 'powerhub'; tool: NodePowerTool }
  | { kind: 'canvas_event'; event: string; params?: Record<string, unknown> }
  | { kind: 'browser_ui'; action: string }
  | { kind: 'node_action'; action: string }
  | { kind: 'async_task'; taskType: string }
  | { kind: 'catalog_only'; reason: string }
  | { kind: 'unwired'; reason: string };

export type NodeCapabilityStatus = 'wired' | 'catalog_only' | 'not_wired';

/** A user-facing capability that is meaningful for one canvas-node type. */
export interface NodeCapability {
  id: string;
  label: string;
  hint: string;
  category: NodeCapabilityCategory;
  /** Existing personal-enhancement panel that can execute this capability. */
  powerTool: NodePowerTool | null;
  /** When true, the node must currently contain the corresponding media. */
  requiresImage?: boolean;
  requiresVideo?: boolean;
  requiresAudio?: boolean;
  /** When true, an empty/configuration-only node is not yet eligible. */
  hasOutput?: boolean;
  /**
   * 画布上已经为这个动作渲染了另一个可见入口 —— 工具栏按钮，或视频节点内的播放器控件。
   * 标了它，「节点能力」菜单就不再重复列一行：同一个动作两个入口的代价不是占地，是两份
   * 实现各改各的（视频下载的文件名兜底、剪辑开关只开不关都是这么漂出来的）。
   *
   * 只影响菜单是否列出，不影响执行通道与 Agent 清单。
   */
  toolbarEntry?: boolean;
  /** Deterministic execution mapping shared by the node UI and Agent manifest. */
  execution?: NodeCapabilityExecution;
  /** Explicitly marks a discovered capability that still has no executable handler. */
  status?: NodeCapabilityStatus;
  inputKinds?: readonly string[];
  outputKinds?: readonly string[];
  mutatesSource?: boolean;
  createsChildNode?: boolean;
}

const imagePowerCapabilities: readonly NodeCapability[] = [
  {
    id: 'expression',
    label: '情绪调节 · 50 锚点',
    hint: '定位画面人物，用50个专属锚点与连续空间精确控制表情',
    category: 'image',
    powerTool: 'expression',
    requiresImage: true,
    hasOutput: true,
    toolbarEntry: true,
  },
];

const imageToolCapabilities: readonly NodeCapability[] = [
  {
    id: 'image-crop',
    label: '裁剪构图',
    hint: '从已有图片截取目标比例或局部主体，输出可继续生成的图片节点',
    category: 'image',
    powerTool: null,
    requiresImage: true,
    hasOutput: true,
    toolbarEntry: true,
    execution: { kind: 'canvas_event', event: 'tool-dialog/open', params: { tool_type: 'crop' } },
    inputKinds: ['image'],
    outputKinds: ['image'],
    mutatesSource: false,
    createsChildNode: true,
  },
  {
    id: 'image-annotate',
    label: '图片标注',
    hint: '在图片上添加局部标记，用于沟通构图、主体、区域或动作重点',
    category: 'image',
    powerTool: null,
    requiresImage: true,
    hasOutput: true,
    toolbarEntry: true,
    execution: { kind: 'canvas_event', event: 'tool-dialog/open', params: { tool_type: 'annotate' } },
    inputKinds: ['image'],
    outputKinds: ['image'],
    mutatesSource: false,
    createsChildNode: true,
  },
  {
    id: 'storyboard-split-input',
    label: '宫格分镜抽取',
    hint: '把九宫格、四宫格或多格图片拆成独立分镜帧继续编辑',
    category: 'storyboard',
    powerTool: null,
    requiresImage: true,
    hasOutput: true,
    toolbarEntry: true,
    execution: { kind: 'canvas_event', event: 'tool-dialog/open', params: { tool_type: 'split-storyboard' } },
    inputKinds: ['image'],
    outputKinds: ['storyboard'],
    mutatesSource: false,
    createsChildNode: true,
  },
];

export const NODE_CAPABILITY_CATALOG = {
  [CANVAS_NODE_TYPES.upload]: [
    ...imagePowerCapabilities,
    ...imageToolCapabilities,
    {
      id: 'source-image',
      label: '原始图片素材',
      hint: '作为不经生成的参考图、首尾帧或图像处理输入',
      category: 'image',
      powerTool: null,
      requiresImage: true,
    },
  ],
  [CANVAS_NODE_TYPES.imageEdit]: [
    ...imagePowerCapabilities,
    ...imageToolCapabilities,
    {
      id: 'image-edit',
      label: '指令改图',
      hint: '结合上游图片与编辑指令生成新的画面版本',
      category: 'image',
      powerTool: null,
    },
  ],
  [CANVAS_NODE_TYPES.imageGen]: [
    ...imagePowerCapabilities,
    ...imageToolCapabilities,
    {
      id: 'image-generation',
      label: '图片生成',
      hint: '按提示词、参考图、构图与模型参数生成图片候选',
      category: 'image',
      powerTool: null,
    },
  ],
  [CANVAS_NODE_TYPES.exportImage]: [
    ...imagePowerCapabilities,
    ...imageToolCapabilities,
    {
      id: 'image-export',
      label: '结果图片交付',
      hint: '预览、下载或继续连接已产出的图片结果',
      category: 'image',
      powerTool: null,
      requiresImage: true,
      hasOutput: true,
    },
  ],
  [CANVAS_NODE_TYPES.beatContext]: [
    {
      id: 'beat-context',
      label: '镜头上下文',
      hint: '维护场景、人物、道具、时间与叙事片段的镜头级约束',
      category: 'text',
      powerTool: null,
    },
  ],
  [CANVAS_NODE_TYPES.textAnnotation]: [
    {
      id: 'text-editing',
      label: '文本编辑与转换',
      hint: '编写说明，并按模式转换为提示词、视频描述、语音或音乐输入',
      category: 'text',
      powerTool: null,
    },
  ],
  [CANVAS_NODE_TYPES.group]: [
    {
      id: 'group-members',
      label: '节点编组',
      hint: '统一组织、移动和命名成员节点，不触发任何媒体生成',
      category: 'group',
      powerTool: null,
    },
    {
      id: 'group-storyboard-layout',
      label: '分镜组排版',
      hint: '把已有成员按分镜宫格排列并维护格序与比例',
      category: 'group',
      powerTool: null,
    },
  ],
  [CANVAS_NODE_TYPES.storyboardSplit]: [
    {
      id: 'storyboard-split',
      label: '分格抽取',
      hint: '从宫格素材识别并导出独立分镜帧，保留格序与画幅',
      category: 'storyboard',
      powerTool: null,
      hasOutput: true,
    },
    {
      id: 'storyboard-frame-export',
      label: '分镜帧交付',
      hint: '逐格选择、预览并导出可继续编辑的分镜图片',
      category: 'storyboard',
      powerTool: null,
      hasOutput: true,
    },
  ],
  [CANVAS_NODE_TYPES.storyboardGen]: [
    {
      id: 'storyboard-grid-generation',
      label: '多版本宫格生成',
      hint: '按每格描述、参考索引和统一画幅生成整组分镜方案',
      category: 'storyboard',
      powerTool: null,
    },
    {
      id: 'storyboard-grid-design',
      label: '宫格结构设计',
      hint: '编辑行列、格序、单格描述与整体或单格画幅控制',
      category: 'storyboard',
      powerTool: null,
    },
  ],
  [CANVAS_NODE_TYPES.video]: [
    {
      id: 'video-generation',
      label: '视频生成',
      hint: '使用文本、图片、首尾帧或多参考素材生成动态镜头',
      category: 'video',
      powerTool: null,
      execution: { kind: 'node_action', action: 'generate' },
      inputKinds: ['text', 'image', 'video', 'audio'],
      outputKinds: ['video'],
    },
    {
      id: 'video-clip',
      label: '视频剪辑',
      hint: '对已有视频设置入点、出点并处理画面片段',
      category: 'video',
      powerTool: null,
      toolbarEntry: true,
      requiresVideo: true,
      hasOutput: true,
      execution: {
        kind: 'canvas_event',
        event: 'video-node/set-operation',
        params: { operation: 'clip' },
      },
      inputKinds: ['video'],
      outputKinds: ['video'],
      createsChildNode: true,
    },
    {
      id: 'video-story-analysis',
      label: '逐帧拉片 / 故事解析',
      hint: '按时间拆解参考视频的分镜、动作与声音线索，输出可继续规划的故事节点',
      category: 'video',
      powerTool: null,
      toolbarEntry: true,
      requiresVideo: true,
      execution: { kind: 'async_task', taskType: 'freezone_video_story' },
      inputKinds: ['video'],
      outputKinds: ['storyboard', 'text'],
      createsChildNode: true,
    },
    {
      id: 'video-subtitle-erase-smart',
      label: '智能去字幕',
      hint: '自动估计字幕区域并提交视频去字幕处理',
      category: 'video',
      powerTool: null,
      toolbarEntry: true,
      requiresVideo: true,
      execution: {
        kind: 'canvas_event',
        event: 'video-node/set-operation',
        params: { operation: 'subtitle-smart' },
      },
      inputKinds: ['video'],
      outputKinds: ['video'],
      mutatesSource: false,
      createsChildNode: true,
    },
    {
      id: 'video-subtitle-erase-box',
      label: '框选去字幕',
      hint: '指定画面区域并提交视频去字幕处理',
      category: 'video',
      powerTool: null,
      toolbarEntry: true,
      requiresVideo: true,
      execution: {
        kind: 'canvas_event',
        event: 'video-node/set-operation',
        params: { operation: 'subtitle-box' },
      },
      inputKinds: ['video', 'region'],
      outputKinds: ['video'],
      mutatesSource: false,
      createsChildNode: true,
    },
    {
      id: 'video-upscale',
      label: '视频高清',
      hint: '对已有视频执行分辨率提升与降噪处理',
      category: 'video',
      powerTool: null,
      toolbarEntry: true,
      requiresVideo: true,
      hasOutput: true,
      execution: { kind: 'async_task', taskType: 'freezone_video_upscale' },
      inputKinds: ['video'],
      outputKinds: ['video'],
      createsChildNode: true,
    },
    {
      id: 'video-audio-separate',
      label: '音视频分离',
      hint: '从已有视频提取独立声音轨与无声视频，分别连接到后续流程',
      category: 'video',
      powerTool: null,
      toolbarEntry: true,
      requiresVideo: true,
      hasOutput: true,
      execution: { kind: 'async_task', taskType: 'freezone_audio_separate' },
      inputKinds: ['video'],
      outputKinds: ['audio', 'video'],
      createsChildNode: true,
    },
    {
      id: 'video-capture-first-frame',
      label: '截取首帧',
      hint: '从视频首帧提取图片节点并自动连接回视频源',
      category: 'video',
      powerTool: null,
      toolbarEntry: true,
      requiresVideo: true,
      hasOutput: true,
      execution: { kind: 'canvas_event', event: 'video-node/capture-frame', params: { mode: 'first' } },
      inputKinds: ['video'],
      outputKinds: ['image'],
      createsChildNode: true,
    },
    {
      id: 'video-capture-last-frame',
      label: '截取尾帧',
      hint: '从视频尾帧提取图片节点并自动连接回视频源',
      category: 'video',
      powerTool: null,
      toolbarEntry: true,
      requiresVideo: true,
      hasOutput: true,
      execution: { kind: 'canvas_event', event: 'video-node/capture-frame', params: { mode: 'last' } },
      inputKinds: ['video'],
      outputKinds: ['image'],
      createsChildNode: true,
    },
    {
      id: 'video-capture-current-frame',
      label: '截取当前帧',
      hint: '从视频当前播放位置提取图片节点并自动连接回视频源',
      category: 'video',
      powerTool: null,
      toolbarEntry: true,
      requiresVideo: true,
      hasOutput: true,
      execution: { kind: 'canvas_event', event: 'video-node/capture-frame', params: { mode: 'current' } },
      inputKinds: ['video'],
      outputKinds: ['image'],
      createsChildNode: true,
    },
    {
      id: 'video-download',
      label: '下载视频',
      hint: '下载当前视频结果，不改变画布节点或上游资产',
      category: 'video',
      powerTool: null,
      toolbarEntry: true,
      requiresVideo: true,
      hasOutput: true,
      execution: { kind: 'browser_ui', action: 'video_download' },
      inputKinds: ['video'],
      outputKinds: [],
    },
    {
      id: 'video-fullscreen',
      label: '全屏查看',
      hint: '在画布查看器中打开当前视频，不改变画布数据',
      category: 'video',
      powerTool: null,
      toolbarEntry: true,
      requiresVideo: true,
      hasOutput: true,
      execution: { kind: 'browser_ui', action: 'video_fullscreen' },
      inputKinds: ['video'],
      outputKinds: [],
    },
    {
      id: 'depth-motion-capture',
      label: '深度动作捕捉',
      hint: '从视频提取深度与动作信息，作为后续运镜或动作参考',
      category: 'video',
      powerTool: null,
      requiresVideo: true,
      execution: {
        kind: 'unwired',
        reason: '尚未接入真实深度模型、任务 runner、产物 schema 和消费节点',
      },
      status: 'not_wired',
      inputKinds: ['video'],
      outputKinds: ['motion_reference', 'depth_sequence'],
    },
  ],
  [CANVAS_NODE_TYPES.audio]: [
    {
      id: 'audio-generation',
      label: '语音与音乐生成',
      hint: '按文本、声线、情绪或音乐参数生成音频',
      category: 'audio',
      powerTool: null,
    },
    {
      id: 'audio-preview',
      label: '波形试听',
      hint: '试听已有音频并查看时长与波形内容',
      category: 'audio',
      powerTool: null,
      requiresAudio: true,
      hasOutput: true,
    },
    {
      id: 'audio-separation',
      label: '音画分离输入',
      hint: '作为独立声音轨继续连接到视频分析或合成流程',
      category: 'audio',
      powerTool: null,
      requiresAudio: true,
      hasOutput: true,
    },
  ],
  [CANVAS_NODE_TYPES.videoStory]: [
    {
      id: 'video-story-analysis',
      label: '视频故事解析',
      hint: '将源视频解析为按时间排列的画面、动作与叙事段落',
      category: 'video',
      powerTool: null,
      requiresVideo: true,
    },
  ],
  [CANVAS_NODE_TYPES.videoCompose]: [
    {
      id: 'video-timeline-compose',
      label: '时间线合成',
      hint: '编排多个上游视频与可选音频轨，调整顺序和片段范围',
      category: 'video',
      powerTool: null,
    },
    {
      id: 'video-compose-export',
      label: '合成导出',
      hint: '按目标分辨率渲染并交付时间线成片',
      category: 'video',
      powerTool: null,
    },
  ],
  [CANVAS_NODE_TYPES.script]: [
    {
      id: 'script-generation',
      label: '结构化脚本生成',
      hint: '从剧情、视频参考或角色要求生成标题与脚本行',
      category: 'text',
      powerTool: null,
    },
  ],
  [CANVAS_NODE_TYPES.pano360Viewer]: [
    {
      id: 'pano360-inspection',
      label: '360° 全景查看',
      hint: '校正球面贴图、正前方与默认视场角并沉浸式检查场景',
      category: 'spatial',
      powerTool: null,
      requiresImage: true,
      hasOutput: true,
    },
    {
      id: 'pano360-view-export',
      label: '全景视角导出',
      hint: '从球面场景选定观察方向并导出可复用视角数据',
      category: 'spatial',
      powerTool: null,
      requiresImage: true,
      hasOutput: true,
    },
  ],
  [CANVAS_NODE_TYPES.threeDWorld]: [
    {
      id: 'three-d-world-generation',
      label: '3D 世界生成',
      hint: '从普通图片、全景图或文本创建可取景的空间世界资源',
      category: 'spatial',
      powerTool: null,
    },
    {
      id: 'three-d-world-directing',
      label: '空间导演取景',
      hint: '管理世界来源、相机快照和对象图层，输出导演控制帧',
      category: 'spatial',
      powerTool: null,
      hasOutput: true,
    },
  ],
  [CANVAS_NODE_TYPES.skill]: [
    {
      id: 'skill-parameters',
      label: 'Skill 参数配置',
      hint: '按技能 schema 配置结构化输入并保留版本信息',
      category: 'skill',
      powerTool: null,
    },
    {
      id: 'skill-run',
      label: 'Skill 执行',
      hint: '以幂等任务运行专用技能，并记录运行、任务和错误状态',
      category: 'skill',
      powerTool: null,
    },
  ],
} satisfies Record<CanvasNodeType, readonly NodeCapability[]>;

function nonEmptyString(value: unknown): boolean {
  return typeof value === 'string' && value.trim().length > 0;
}

function hasImage(node: CanvasNode): boolean {
  return nonEmptyString(node.data.imageUrl) || nonEmptyString(node.data.previewImageUrl);
}

function hasVideo(node: CanvasNode): boolean {
  return (
    nonEmptyString(node.data.videoUrl) ||
    nonEmptyString(node.data.resultVideoUrl) ||
    nonEmptyString(node.data.sourceVideoUrl)
  );
}

function hasAudio(node: CanvasNode): boolean {
  return nonEmptyString(node.data.audioUrl);
}

function nodeHasOutput(node: CanvasNode): boolean {
  const data = node.data;
  return (
    hasImage(node) ||
    hasVideo(node) ||
    hasAudio(node) ||
    nonEmptyString(data.content) ||
    nonEmptyString(data.plyUrl) ||
    nonEmptyString(data.panoUrl) ||
    data.scriptResult != null ||
    (Array.isArray(data.rows) && data.rows.length > 0) ||
    (Array.isArray(data.frames) && data.frames.length > 0)
  );
}

export function getNodeTypeCapabilities(type: CanvasNodeType): readonly NodeCapability[] {
  return NODE_CAPABILITY_CATALOG[type];
}

/** Returns only capabilities whose declared media/output requirements the node currently meets. */
export function getNodeCapabilities(node: CanvasNode): readonly NodeCapability[] {
  return getNodeTypeCapabilities(node.type).filter((capability) => {
    if (capability.requiresImage && !hasImage(node)) return false;
    if (capability.requiresVideo && !hasVideo(node)) return false;
    if (capability.requiresAudio && !hasAudio(node)) return false;
    if (capability.hasOutput && !nodeHasOutput(node)) return false;
    return true;
  });
}

export interface NodeCapabilityContract {
  id: string;
  label: string;
  hint: string;
  category: NodeCapabilityCategory;
  status: NodeCapabilityStatus;
  execution: NodeCapabilityExecution;
  input_kinds: string[];
  output_kinds: string[];
  requires: { image: boolean; video: boolean; audio: boolean; output: boolean };
  mutates_source: boolean;
  creates_child_node: boolean;
}

/** Serialize one capability without exposing a React/UI implementation detail. */
export function describeNodeCapability(capability: NodeCapability): NodeCapabilityContract {
  const execution = capability.execution
    ?? (capability.powerTool
      ? { kind: 'powerhub' as const, tool: capability.powerTool }
      : { kind: 'catalog_only' as const, reason: 'catalogued without a direct executor' });
  const status = capability.status
    ?? (execution.kind === 'unwired'
      ? 'not_wired'
      : execution.kind === 'catalog_only'
        ? 'catalog_only'
        : 'wired');
  return {
    id: capability.id,
    label: capability.label,
    hint: capability.hint,
    category: capability.category,
    status,
    execution,
    input_kinds: [...(capability.inputKinds ?? [])],
    output_kinds: [...(capability.outputKinds ?? [])],
    requires: {
      image: Boolean(capability.requiresImage),
      video: Boolean(capability.requiresVideo),
      audio: Boolean(capability.requiresAudio),
      output: Boolean(capability.hasOutput),
    },
    mutates_source: Boolean(capability.mutatesSource),
    creates_child_node: Boolean(capability.createsChildNode),
  };
}

/**
 * Returns the capabilities the "节点能力" menu should offer.
 *
 * A capability qualifies when the catalog declares an executor for it. Two kinds
 * stay out:
 *
 *  - `catalog_only` / `unwired` have no executor at all. Listing them is exactly
 *    the fake action this filter exists to prevent.
 *  - `node_action` is a single action that the node's own primary control already
 *    performs — the submit button is on screen whenever the capability applies.
 *    It stays in the Agent manifest (`capability_contracts`) where "which node
 *    can generate" is a real fact, but as a menu entry it would duplicate a
 *    spend trigger.
 *
 * A third kind stays out for a different reason: `toolbarEntry` capabilities
 * already have a visible button on the canvas (the toolbar chip, or the video
 * player's own camera/fullscreen controls). Two entry points for one action is
 * not just clutter — the two implementations drift. `video-download` is the
 * standing example: the toolbar and the dispatcher disagreed about the `.mp4`
 * suffix, so the same click produced two different filenames. The rule for this
 * menu is therefore "what the toolbar cannot reach", which is why imageEdit
 * nodes still list the four image tools and video nodes list nothing at all.
 *
 * The earlier version of this filter kept only `powerTool !== null`, which also
 * hid every bus-dispatched capability: crop, frame capture, clip, subtitle
 * erase, download, fullscreen and the three video async tasks all had working
 * handlers and were invisible anyway.
 */
export function getNodeCapabilityMenuEntries(
  node: CanvasNode,
): readonly NodeCapability[] {
  return getNodeCapabilities(node)
    .filter(isNodeCapabilityMenuEntry)
    // 图片编辑节点有自己的节点内工作流，工具栏的图片按钮对它一律不渲染
    // （`!isImageEdit`），所以「工具栏已经有了」在它身上不成立，那几条必须留在菜单里
    // 作为唯一入口。
    .filter((capability) => !capability.toolbarEntry || isImageEditNode(node));
}

/** True when this capability is a real, selectable action in the node menu. */
export function isNodeCapabilityMenuEntry(capability: NodeCapability): boolean {
  if (capability.powerTool !== null) return true;
  const kind = capability.execution?.kind;
  return kind === 'canvas_event' || kind === 'async_task' || kind === 'browser_ui';
}
