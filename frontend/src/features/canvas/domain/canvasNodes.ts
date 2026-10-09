// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import type { Edge, Node, XYPosition } from '@xyflow/react';
import type {
  DirectorControlFrameBundle,
  DirectorObjectLayer,
  DirectorWorldSource,
} from '@/features/viewer-kit/three-d/directorManifest';
import type { CanvasResourceMeta } from './canvasResourceMeta';
import type { ScriptContractReport } from '@/api/scriptContract';
import type { FreezoneVideoGenerationSource } from '@/api/freezoneGenerationHistory';

export const CANVAS_NODE_TYPES = {
  upload: 'uploadNode',
  imageEdit: 'imageNode',
  imageGen: 'imageGenNode',
  exportImage: 'exportImageNode',
  beatContext: 'beatContextNode',
  textAnnotation: 'textAnnotationNode',
  group: 'groupNode',
  storyboardSplit: 'storyboardNode',
  storyboardGen: 'storyboardGenNode',
  video: 'videoNode',
  audio: 'audioNode',
  videoStory: 'videoStoryNode',
  videoCompose: 'videoComposeNode',
  script: 'scriptNode',
  pano360Viewer: 'pano360ViewerNode',
  threeDWorld: 'threeDWorldNode',
  skill: 'skillNode',
} as const;

export type CanvasNodeType = (typeof CANVAS_NODE_TYPES)[keyof typeof CANVAS_NODE_TYPES];

export const DEFAULT_ASPECT_RATIO = '1:1';
export const AUTO_REQUEST_ASPECT_RATIO = 'auto';
export const DEFAULT_NODE_WIDTH = 320;
export const EXPORT_RESULT_NODE_DEFAULT_WIDTH = 480;
export const EXPORT_RESULT_NODE_LAYOUT_HEIGHT = 360;
export const EXPORT_RESULT_NODE_MIN_WIDTH = 300;
export const EXPORT_RESULT_NODE_MIN_HEIGHT = 300;
// 缩放下限刻意小于创建/紧凑尺寸，否则节点缩不到比初始更小（配合 keepAspectRatio
// 时短边为绑定约束，按比例换算后宽屏/竖屏都能缩到一个一致的小框）。
export const EXPORT_RESULT_NODE_RESIZE_MIN_EDGE = 140;

/**
 * 脚本节点尺寸的**唯一口径**：空态一档、出表后一档，外加缩放下限。
 *
 * 这个节点此前把尺寸写了三遍且互不相同 —— 节点本体 480×320 / 800×400，LOD 外壳
 * 480×320，而 `FALLBACK_NODE_SIZES` 里根本没有 scriptNode 条目（未测量时按
 * 320×200 算，被 `groupNodes` / `fitGroupToChildren` / 依赖落位等二十多处读走）。
 * 现在三处都读这里，`canvas-node-size-parity` 用例钉住一致性。
 */
export const SCRIPT_NODE_SIZE = {
  /** 还没有脚本表时的默认尺寸。 */
  empty: { width: 480, height: 320 },
  /** 出现表格后的默认尺寸（对齐 LibTV 脚本节点的 800×400）。 */
  withResult: { width: 800, height: 400 },
  min: { width: 360, height: 240 },
  max: { width: 1600, height: 1200 },
} as const;

/**
 * React Flow 传进来的节点宽高到底算不算「量到了」。
 *
 * 判据只有一条：**有限且大于 0**。`undefined`、`0`、负数、`NaN` 一律当「不知道」。
 *
 * 为什么 `0` 必须也当「不知道」——`@xyflow/system@0.0.76` 的 `getNodeDimensions` 自己
 * 就用 0 当兜底，节点只要没有 `measured` / `width` / `initialWidth`，它返回的就是
 * **数字 0，不是 undefined**：
 *     width: node.measured?.width ?? node.width ?? node.initialWidth ?? 0
 * 而 React Flow 的节点包装器把这个结果原样展开进组件 props（`...nodeDimensions`）。
 * 于是 `width ?? fallback` 和 `typeof width === 'number' ? width : fallback` 这两种
 * 写法都会把 0 收成有效值，默认尺寸那一档永远不可达；脚本节点被钉在 360×240（= min
 * 档）就是这么来的 —— 0 被 `Math.max(min, 0)` 抬成 min，React Flow 再把 360 量回去写进
 * `measured`，而 `getNodeDimensions` 优先读 `measured`，从此连刷新都带着它。
 *
 * 另外落盘数据里也真的出现过 `width: 0` 的节点，所以这条判据同时盖住「上游给了 0」。
 */
export function resolveKnownNodeEdge(value: number | null | undefined): number | null {
  return typeof value === 'number' && Number.isFinite(value) && value > 0 ? value : null;
}

export const IMAGE_SIZES = ['0.5K', '1K', '2K', '4K'] as const;
export const IMAGE_ASPECT_RATIOS = [
  '1:1',
  '16:9',
  '9:16',
  '4:3',
  '3:4',
  '21:9',
] as const;

export type ImageSize = (typeof IMAGE_SIZES)[number] | (string & {});

export interface ProductionMetadata {
  schema: 'production_metadata.v1';
  production_layer: 'evidence' | 'constraints' | 'anchors' | 'expansion' | 'execution' | 'results';
  creation_stage: 'source' | 'assets' | 'storyboard' | 'prompt' | 'media' | 'assembly' | 'delivery';
  approval_status: 'draft' | 'ready' | 'approved' | 'blocked' | 'succeeded' | 'rejected';
  source_evidence: Array<Record<string, unknown>>;
  depends_on: string[];
  artifact_refs: string[];
  updated_by: { actor: 'agent' | 'user' | 'workflow'; turn_id: string };
}

/** Image quality preset, only honored by image2 models (gpt-image-2). */
export type ImageQuality = 'low' | 'medium' | 'high' | 'auto';

export interface NodeDisplayData {
  displayName?: string;
  productionMetadata?: ProductionMetadata;
  /**
   * Facts about the produced file (bytes, container type, real dimensions and
   * length), written by the backend that committed the media. Absent on nodes
   * whose producer predates it and on user uploads, and explicitly null when a
   * commit replaced the media without being able to prove anything about the
   * replacement — so consumers must treat every field as optional.
   */
  resourceMeta?: CanvasResourceMeta | null;
  [key: string]: unknown;
}

export interface NodeImageData extends NodeDisplayData {
  imageUrl: string | null;
  previewImageUrl?: string | null;
  aspectRatio: string;
  isSizeManuallyAdjusted?: boolean;
  candidate_origin?: Record<string, unknown>;
  output_role?: string;
  committed_at?: string | null;
  committed_slot_url?: string | null;
  director_control_bundle?: DirectorControlFrameBundle;
  media_kind?: string;
  [key: string]: unknown;
}

export interface UploadImageNodeData extends NodeImageData {
  sourceFileName?: string | null;
  isUploading?: boolean;
  uploadError?: string | null;
  imageOnly?: boolean;
}

export type VideoGenMode =
  | 'textToVideo'
  | 'allReference'
  | 'imageToVideo'
  | 'firstLastFrame'
  | 'imageReference'
  | 'videoEdit';

export type VideoGenQuality =
  | '480P'
  | '720P'
  | '768P'
  | '1080P'
  | '2K'
  | '4K'
  | (string & {});
export type VideoGenCount = 1 | 2 | 4 | 6 | 8 | 12;
export type Seedance2SceneOptimize = 'anime' | 'realistic';

/** Auditable provenance written only after the user accepts an AI optimization. */
export interface PromptOptimizerProvenance {
  prompt_optimizer_original_prompt?: string;
  prompt_optimizer_model?: string;
  prompt_optimizer_profile?: string;
  prompt_optimizer_revision?: string;
  prompt_optimizer_changes?: string[];
  prompt_optimizer_applied_rules?: string[];
  prompt_optimizer_warnings?: string[];
  prompt_optimizer_preserved_intent?: string;
  prompt_optimizer_output_language?: 'zh' | 'en' | 'mixed';
  prompt_optimizer_knowledge_profiles?: string[];
  prompt_optimizer_knowledge_sources?: string[];
  prompt_optimizer_knowledge_live_sources?: string[];
  prompt_optimizer_knowledge_retrieval_mode?: string;
  prompt_optimizer_knowledge_profile_details?: Array<Record<string, string>>;
  prompt_optimizer_strategy_contract?: Record<string, unknown>;
  prompt_optimizer_research_mode?: 'quick' | 'standard' | 'expert';
  prompt_optimizer_research_status?: string;
  prompt_optimizer_research_used?: boolean;
  prompt_optimizer_research_sources?: Array<Record<string, unknown>>;
  prompt_optimizer_research_assessment?: Record<string, unknown>;
  prompt_optimizer_critic_passed?: boolean;
  prompt_optimizer_critic_issues?: string[];
  prompt_optimizer_critic_repairs?: string[];
  prompt_optimizer_director_recipe_ids?: string[];
  prompt_optimizer_director_recipe_receipt?: Record<string, unknown>;
  prompt_optimizer_model_capability_snapshot?: Record<string, unknown>;
  prompt_optimizer_reference_manifest?: Record<string, unknown>;
  prompt_optimizer_target_model_id?: string;
  prompt_optimizer_target_api_model?: string;
  prompt_optimizer_target_model_label?: string;
  prompt_optimizer_target_mode?: unknown;
  prompt_optimizer_request_snapshot?: Record<string, unknown>;
  prompt_optimizer_job_id?: string;
  prompt_optimizer_task_key?: string;
  prompt_optimized_at?: string;
}

/**
 * 全片交付画框的安全区（归一化到 0..1）。
 *
 * 这是“最终成片”的规格，不是某个节点卡片的画布尺寸。分镜图与镜头视频都必须按
 * 同一个画框生成，后续合成只做容器对齐，不能把内容再次裁切到看不见主体。
 */
export interface VideoSafeArea {
  top: number;
  right: number;
  bottom: number;
  left: number;
}

/**
 * 一部片子只允许有一个最终交付规格。
 *
 * 资产概念图不受它约束（角色、场景、道具可以各自使用方图或竖图）；只有进入
 * 分镜、逐镜视频与最终合成的素材必须服从它。
 */
export interface VideoDeliverySpec {
  width: number;
  height: number;
  aspectRatio: string;
  fps: number;
  safeArea: VideoSafeArea;
}

/**
 * 前端向前端写入的镜头原始事实。
 *
 * 这里刻意不包含 Python `shot_contract` 的 hash 或 schema；合同由后端根据这些事实
 * 统一编译、校验和冻结。前端复制哈希会把“事实”和“派生结果”混成同一个真相源。
 */
export interface VideoShotContractFacts {
  shotId: string;
  shotSize?: string | null;
  subject: string;
  action: string;
  cameraMovement: string;
  firstFrame?: string | null;
  lastFrame?: string | null;
  continuityIn: Record<string, unknown>;
  continuityOut: Record<string, unknown>;
  transition?: string | null;
  generationMode?: 'imageToVideo' | 'firstLastFrame' | 'textToVideo' | null;
  referenceBindings: Record<string, string[]>;
  /** 正式提交时采用的正文；手改后旧事实不可继续冒充当前版本。 */
  executionPrompt?: string | null;
  /** 导演交接包：当前镜头执行目的与切镜后交接分开保存。 */
  creativeHandoff?: VideoCreativeHandoff | null;
}

export interface VideoCreativeHandoff {
  shotPurpose?: string;
  cutReason?: string;
  filmLanguage?: string;
  contentIntent?: string;
  sequenceIds?: string[];
  /** Fixed geography for this shot's named scenes; poses and routes belong to shot states. */
  sceneDescriptions?: Record<string, string>;
  /** Relevant snapshot of the user-authored whole-film director plan. */
  directorContext?: {
    storyPromise?: string;
    protagonistGoal?: string;
    coreConflict?: string;
    endingChange?: string;
    visualBible?: {
      visualStyle?: string;
      texture?: string;
      colorProgression?: string;
      lighting?: string;
      cameraLanguage?: string;
    };
    rhythmCurve?: string;
    soundPlan?: string;
    sequences: Array<{
      sequenceId: string;
      title?: string;
      dramaticGoal?: string;
      resistance?: string;
      escalation?: string;
      turn?: string;
      release?: string;
      stagingPlan?: string;
      performancePlan?: string;
      shotNos: number[];
    }>;
  };
  keyframePlan?: Array<{
    role: string;
    generation_strategy?: 'independent' | 'state_edit';
    framing?: string;
    state: string;
    purpose: string;
    required: boolean;
  }>;
  referenceResponsibilities?: Array<{
    scope: 'storyboard' | 'keyframe' | 'video';
    imageNumber: number;
    role: 'character' | 'scene' | 'prop' | 'reference' | 'frame_design' | 'opening_frame' | 'state_frame' | 'end_frame' | 'continuity_frame' | 'motion' | 'style';
    name: string;
    sourceNodeId?: string;
    responsibility: string;
    prohibited: string;
  }>;
}

export interface VideoNodeData extends NodeDisplayData, PromptOptimizerProvenance {
  videoUrl: string | null;
  /** Server receipt for this artifact, never inferred from the current prompt. */
  videoGenerationSource?: FreezoneVideoGenerationSource | null;
  generationBatchSources?: Record<string, FreezoneVideoGenerationSource> | null;
  previewImageUrl?: string | null;
  /**
   * 节点被作为「上游视频引用素材」使用（例如脚本节点 spawn 出来的）。
   * 此时只显示视频本体 + 顶部 toolbar（剪辑/高清/解析/...），
   * 不渲染底部生成操作面板（Mode tabs / prompt / 提交）。
   */
  referenceOnly?: boolean;
  aspectRatio: string;
  /** Fixed pixel size slot (e.g. "992x432") chosen for size-slot models. */
  sizeSlot?: string | null;
  isSizeManuallyAdjusted?: boolean;
  sourceFileName?: string | null;
  widthPx?: number | null;
  heightPx?: number | null;
  /** Dimensions and ratio observed from the completed media, not a new request preset. */
  actualWidth?: number | null;
  actualHeight?: number | null;
  actualAspectRatio?: string | null;
  aspectRatioMismatch?: boolean | null;
  /** 全片唯一交付规格；脚本派生节点与最终成片都读它。 */
  deliverySpec?: VideoDeliverySpec | null;
  /** 镜头原始事实；后端据此编译并冻结 canonical shot contract。 */
  shotContractFacts?: VideoShotContractFacts | null;
  /** 后端编译并回写的 canonical shot contract（含 hash）；前端不自行生成。 */
  shotContract?: Record<string, unknown> | null;
  durationMs?: number | null;
  isUploading?: boolean;
  isAnalyzing?: boolean;
  analysisResult?: string | null;
  analysisError?: string | null;
  isSeparatingAv?: boolean;
  // clip editor (libtv-style) ------------------------------------------------
  isClipMode?: boolean;
  clipStartMs?: number | null;
  clipEndMs?: number | null;
  // subtitle erase (libtv-style 智能去字幕) ----------------------------------
  /** `smart` = auto-estimate bottom subtitle band; `box` = user-drawn region. */
  subtitleEraseMode?: 'smart' | 'box' | null;
  /** Box coords normalized 0..1 against the source frame. Only set in 'box' mode. */
  subtitleEraseBox?: { x: number; y: number; width: number; height: number } | null;
  // generation fields (libtv-style operation panel)
  prompt?: string;
  /**
   * 生成数量 > 1 时一次生成的全部结果 URL（含主视频）。节点收拢时渲染成
   * 叠卡画册（同图片节点），videoUrl 始终等于其中被选为主视频的那条。
   * 单条生成时为空。
   */
  generationBatch?: string[] | null;
  genMode?: VideoGenMode;
  model?: string;
  quality?: VideoGenQuality;
  /**
   * Last video generation request resolution (480p/720p/768p/1080p/2k). Frozen at
   * submit for the resolution honesty badge (requested tier vs actual pixels).
   */
  lastRequestedResolution?: string | null;
  /** Last video generation request aspect ratio, kept separate from observed media ratio. */
  lastRequestedAspectRatio?: string | null;
  /**
   * Last video generation request duration in seconds, frozen at submit for the
   * duration honesty badge (requested vs the artifact's measured length). Absent
   * on nodes whose submit predates it — the badge then stays silent rather than
   * attributing today's config to an older render.
   */
  lastRequestedDurationSeconds?: number | null;
  durationSec?: number;
  generateAudio?: boolean;
  /**
   * Legacy structured speech fields. Audio is authored by AudioNode/workflow;
   * these remain optional so existing canvases can still be loaded and submitted.
   */
  dialogueText?: string;
  spokenDialogue?: string[];
  audioType?: 'silence' | 'narration' | 'dialogue';
  speaker?: string;
  nativeAudioStrategy?: 'external' | 'native';
  audioAssetRef?: string | null;
  /**
   * 台词自动配音结果（TTS 音频静态地址）。提示词里抽出的台词经 `/freezone/audio/speech`
   * 合成后缓存在这里，提交时作为音频参考喂给视频模型做口型同步——模型不再自己「念」
   * 夹在画面描述里的台词，因此不会再念错。
   */
  dialogueAudioUrl?: string | null;
  /** 上述配音对应的台词/声线缓存键；键变了才重新合成，避免重复扣费。 */
  dialogueAudioCacheKey?: string | null;
  /** Provider-specific controls declared by the selected model capability envelope. */
  parameters?: Record<string, unknown>;
  /** True only after the user or Agent explicitly changes the audio switch. */
  generateAudioUserSet?: boolean;
  /** Legacy persisted field. Canvas video generation now enables provider human review by default. */
  humanReview?: boolean;
  sceneOptimize?: Seedance2SceneOptimize;
  count?: VideoGenCount;
  /**
   * 运镜不再存在节点字段里：它归口到提示词的运镜段（见
   * `domain/promptCamera.ts`）。旧画布上的 `cameraMovement` 在
   * `canvasStore.normalizeNodes` 水合时折进提示词后删除。
   */
  /**
   * 用户手动拖拽调整后的上游引用顺序(上游节点 id 列表)。决定参考 chips 的展示顺序、
   * 「图片N / 音频N」编号,以及提交给后端的 reference/首尾帧 顺序。未列入的上游节点
   * (如新接入的)排在其后,按节点 y 坐标兜底。
   */
  referenceOrder?: string[];
  isGenerating?: boolean;
  generationStartedAt?: number | null;
  generationError?: string | null;
  generationErrorDetails?: string | null;
  generationErrorRequestId?: string | null;
  generationErrorStage?: string | null;
  generationErrorSuggestedAction?: string | null;
  generationErrorCode?: string | null;
  generationErrorRetryable?: boolean | null;
  /** Last accepted upstream video task that can be queried without resubmitting. */
  generationRecoveryJobId?: string | null;
  generationRecoveryTaskType?: string | null;
  // 视频高清（upscale）标记 ----------------------------------------------------
  // 视频节点「高清」操作 spawn 的视频节点带这些字段：复用 video 节点的播放器 / 角标 /
  // 尺寸（与普通视频节点一致），但用 isUpscaleNode 抑制底部生成面板，改走选中时常驻的
  // VideoUpscaleEditorOverlay（提交 submitFreezoneVideoUpscale，高清结果回写到本节点
  // videoUrl）。
  /** 标记此视频节点是「视频高清」节点（参数面板 + upscale 提交，而非常规生成）。 */
  isUpscaleNode?: boolean;
  /** 待高清的上游视频静态地址。 */
  upscaleSourceUrl?: string;
  /** 目标清晰度档位。 */
  upscaleResolution?: '1080p' | '2k' | '4k';
  /** 降噪强度。 */
  upscaleDenoise?: 'none' | '1x' | '2x';
  // 「分镜表 → 逐镜视频」派生身份 ------------------------------------------------
  // 这些节点由 `nodes/videoStory/videoStoryShotVideos.ts` 从「镜头表」的每一镜派生，
  // 首帧靠一条镜头图 → 本节点的血缘边供给（i2v 只认上游节点的图）。边会被人删、
  // 也会在镜头图重组时被连带删掉，所以「这条视频是哪一行」以节点自带的这几个字段
  // 为准，不由边决定。
  /** 派生它的视频故事节点 id。 */
  videoStorySourceNodeId?: string;
  /** 行标识（与镜头图节点同一套行键，行不变则键不变）。 */
  videoStoryRowKey?: string;
  /** 派生时的运动提示词快照：表里改过之后靠它判这一镜的视频是否已过期。 */
  videoStoryRowMotionPrompt?: string;
  /** 供首帧的镜头图节点 id；边丢了靠它补回来。 */
  videoStoryShotImageNodeId?: string;
  // 「逐镜切片段」派生身份 ------------------------------------------------------
  // 这一组与「逐镜出视频」的区别：切片段是从**源视频**上原样切下来的（纯 ffmpeg，
  // 不出模型、不花钱），所以没有首帧、没有提示词，身份靠「哪一行 + 源上的哪一段」。
  /** 该片段取自源视频的第几行（与上组同一套行键，用于清点与三态分类）。 */
  videoStoryClipRowKey?: string;
  /** 该片段在源视频上的区间（秒）—— 重跑时靠它判「这一镜的区间改过没有」。 */
  videoStoryClipStartSec?: number;
  videoStoryClipEndSec?: number;
  /** 片段节点派生于哪个视频故事节点（切片段与出视频共用这一个归属键）。 */
  videoStoryClipSourceNodeId?: string;
  /** 切片段派生的视频节点标记：这些节点不参与出片，只是源片的一截副本。 */
  videoStoryClipSegment?: boolean;
  [key: string]: unknown;
}

/**
 * 「视频合成」节点。把 ≥2 个上游视频节点（可选音频节点）连进来后，打开 libtv 风格
 * 的时间线剪辑器（{@link VideoComposeModal}）编排导出。最终合成走后端
 * `submitFreezoneVideoCompose`，导出地址回写到 `resultVideoUrl`。
 */
export interface VideoComposeNodeData extends NodeDisplayData {
  /** 最近一次合成导出的视频 url。 */
  resultVideoUrl?: string | null;
  /** 结果视频的封面（暂未生成时为空）。 */
  previewImageUrl?: string | null;
  /** 上次使用的导出分辨率。 */
  resolution?: '720p' | '1080p';
  /**
   * 合成编辑器的草稿时间线（关闭弹窗时写回，重开/刷新后恢复）。结构为
   * `ComposeTimelineState`，这里存 unknown 以免领域层反向依赖 compose 特性层。
   */
  draftTimeline?: unknown;
  [key: string]: unknown;
}

export type ExportImageNodeResultKind =
  | 'generic'
  | 'storyboardGenOutput'
  | 'storyboardSplitExport'
  | 'storyboardFrameEdit'
  | 'matte'
  | 'upscale';

export interface ExportImageNodeData extends NodeImageData {
  resultKind?: ExportImageNodeResultKind;
}

export interface GroupNodeData extends NodeDisplayData {
  label: string;
  /** 组背景色（基础 hex，见 groupColors.ts）。空/缺省走默认底色。 */
  backgroundColor?: string | null;
  /**
   * Marks a group created via "合并分镜组" — its members are laid out as an
   * ordered shot (分镜) grid (宫格). Plain "打组" groups leave this unset.
   */
  storyboardGroup?: boolean;
  /** Cell aspect ratio key for the storyboard grid, e.g. "16:9". */
  storyboardAspect?: string;
  /** Column count of the storyboard grid. */
  storyboardCols?: number;
  /**
   * Row count of the storyboard grid. The column count stays authoritative for
   * layout, so this is the "N rows" way of naming the same grid: picking rows in
   * the toolbar rewrites `storyboardCols` to match and records the result here.
   */
  storyboardRows?: number;
  /** Show a 1-based index badge on each cell. */
  storyboardShowIndex?: boolean;
  /** Largest member content box at merge time — the cell-size floor for re-layout. */
  storyboardBaseWidth?: number;
  storyboardBaseHeight?: number;
  [key: string]: unknown;
}

export type TextNodeMode =
  | 'writing'
  | 'textToVideo'
  | 'imageToPrompt'
  // textToMusic: 历史命名,实为「克隆音频」(语音克隆 TTS),派生语音音频节点。
  | 'textToMusic'
  // textToMusicGen: 「文字生成音乐」,派生 audioKind='music' 的音频节点(走 /freezone/audio/music)。
  | 'textToMusicGen';

export type TextPrepareMode = 'faithful' | 'creative';

export interface TextPreparePreview {
  preparedText: string;
  sourceText: string;
  sourceHash: string;
  mode: TextPrepareMode;
  model: string;
  jobId: string;
  taskKey: string;
  changeSummary: string[];
  unresolved: string[];
  warnings: string[];
  createdAt: number;
}

export interface TextAnnotationNodeData extends NodeDisplayData {
  content: string;
  /**
   * 节点被作为「上游引用素材」使用（例如脚本节点 spawn 出来的）。
   * 此时只显示编辑卡片，不渲染 mode 列表 / 模型选择 / 提交按钮。
   */
  referenceOnly?: boolean;
  /**
   * Steering description shown in the ops textarea for `imageToPrompt` mode.
   * Kept separate from `content` so the API result can overwrite the preview
   * card without erasing the user's instruction.
   */
  instruction?: string;
  mode?: TextNodeMode;
  model?: string;
  /** 用户选择的整理档位；默认忠实整理。 */
  textPrepareMode?: TextPrepareMode;
  /** 文本整理任务结果；存在时 UI 显示预览，不自动覆盖 content。 */
  textPreparePreview?: TextPreparePreview | null;
  textPrepareError?: string | null;
  /**
   * 用户已从能力 picker 选过一次（如「文字生成音乐」派生了下游音频节点）。
   * 置真后该文本节点恒为纯文本编辑区，空内容时也不再退回显示「试试」picker。
   */
  pickerDismissed?: boolean;
  extraParams?: Record<string, unknown>;
  isGenerating?: boolean;
  /** 反推提示词等异步任务的开始时间戳，喂给生成中 loading 覆盖层模拟进度。 */
  generationStartedAt?: number | null;
  [key: string]: unknown;
}

export interface BeatContextNodeData extends NodeDisplayData {
  content?: string;
  projectId?: string;
  episode?: number;
  beat?: number;
  snapshot?: {
    visualDescription?: string;
    narrationSegment?: string;
    sceneId?: string;
    sceneVariantId?: string;
    timeOfDay?: string;
    detectedIdentities?: string[];
    detectedProps?: string[];
    sketchColors?: Record<string, string>;
    propMarkerColors?: Record<string, string>;
    selectedBackgroundExists?: boolean;
    currentSketchExists?: boolean;
    currentFrameExists?: boolean;
    [key: string]: unknown;
  };
  syncStatus?: 'fresh' | 'stale' | 'syncing' | 'error';
  errorMessage?: string;
  mainline_context?: unknown;
  beat_edit_fields?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface ImageEditNodeData extends NodeImageData {
  prompt: string;
  model: string;
  size: ImageSize;
  requestAspectRatio?: string;
  generationMode?: 'text_to_image' | 'image_to_image' | 'all_reference' | 'image_reference';
  extraParams?: Record<string, unknown>;
  capabilityId?: string;
  capabilityParams?: Record<string, unknown>;
  capabilityInputs?: Record<
    string,
    {
      nodeId?: string;
      role?: string;
      sourceUrl?: string;
      assetKind?: string;
    }
  >;
  capabilityOutputKind?: string;
  capabilityDefaultPushTarget?: Record<string, unknown>;
  compiledPromptPreview?: string;
  isGenerating?: boolean;
  generationStartedAt?: number | null;
}

export type ImageGenCount = 1 | 2 | 4 | 6 | 8 | 12;

export interface ImageGenFocusRegion {
  /** Normalized 0-1 coordinates against the upstream source image. */
  sourceUrl: string;
  x: number;
  y: number;
  width: number;
  height: number;
  aspectRatio?: string;
}

export interface ImageGenCameraSelection {
  cameraBodyId?: string | null;
  lensId?: string | null;
  focalLengthMm?: number | null;
  aperture?: string | null;
}

export interface ImageGenNodeData extends NodeImageData, PromptOptimizerProvenance {
  prompt: string;
  model: string;
  size: ImageSize;
  /**
   * 生成数量 > 1 时一次生成的全部结果 URL（含主图）。节点收拢时渲染成
   * 叠卡画册（卡片边缘从主图后探出），imageUrl 始终等于其中被选为主图
   * 的那张。单张生成时为空。
   */
  generationBatch?: string[] | null;
  /** Quality preset for image2 models; defaults to 'medium' when unset. */
  quality?: ImageQuality;
  /**
   * Last generation request size tier (1K/2K/4K). Frozen at submit so the
   * resolution honesty badge compares actual pixels against what was requested,
   * even if the user changes the size chip afterwards.
   */
  lastRequestedImageSize?: ImageSize | string | null;
  /** Last generation request quality (image2 only), when sent. */
  lastRequestedImageQuality?: ImageQuality | string | null;
  /** Last generation request aspect ratio (snapped), when sent. */
  lastRequestedAspectRatio?: string | null;
  requestAspectRatio?: string;
  /**
   * 模型能力合同声明的高级参数（MJ 系的 stylize / chaos / weird / personalisation 等），
   * 由参数面板写入，提交时以 `advanced_settings` 下发；后端按所选模型合同过滤，
   * 未知键不会透传上游。与 ImageEditNode / StoryboardGenNode 共用同一个 map 形状。
   */
  extraParams?: Record<string, unknown>;
  count?: ImageGenCount;
  styleTemplateId?: string | null;
  focusRegion?: ImageGenFocusRegion | null;
  cameraSelection?: ImageGenCameraSelection | null;
  /** User-uploaded identity/scene reference image, always reference image 1. */
  referenceImageUrl?: string | null;
  /**
   * 节点**自带**的整组参考图（有序、含首张）。对应 LibTV 的 `params.imageList` ——
   * 官方 CLI `syncScriptStoryboardFromNode` 把该行 `characters` 的**每一张**
   * `characterImageUrl` 都塞进这个数组，而不是只留第一张。
   *
   * 我们此前只把该行首张角色图写进 `referenceImageUrl`，多角色镜头里的第二个
   * 人物在提交时就整条丢了（实翻 4,475 份社区快照 / 241,010 个图片类节点：58.8%
   * 带 `imageList`，其中 **21.3% 带 ≥2 张**，多参考是常态）。`referenceImageUrl`
   * 仍是第 1 张、仍是 `@图片1` 的编号基线；本字段按序含全部，两者由
   * `orderedReferenceUrlsWithOwnFirst` 统一去重。
   */
  referenceImageUrls?: string[] | null;
  /** Uploaded render of the exact 3D expression preview, injected as geometry-only image 2. */
  expressionControlImageUrl?: string | null;
  /** Present/mainline workflow nodes can auto-commit their generated image to slot_target. */
  autoCommitOnGenerate?: boolean;
  /** Local-only marks/annotations placed on upstream image. */
  marks?: Array<{ id: string; label: string; x: number; y: number }>;
  /**
   * 脚本节点「生成分镜」派生的行关联键（等价 LibTV 的 `scriptRowHiddenUuid`），
   * 用于把这张分镜图和脚本分镜表里的某一行对齐（重算 / 回填都靠它）。
   */
  scriptShotId?: string | null;
  /** T-047 之前的兼容别名；新节点与 scriptShotId 同时写入。 */
  scriptRowKey?: string | null;
  /**
   * 派生这张分镜图时，该脚本行的图片提示词快照（`shot_prompt || visual_description`）。
   * 脚本表格里改了提示词之后，用它判定这张图是否已过期（LibTV 的
   * 「脚本已变更须重生成 storyboard」靠的就是这类比对）。
   * 老画布上的分镜图没有这个字段 ⇒ 一律按「无可比对」处理，不判过期。
   */
  scriptRowPrompt?: string | null;
  /** 派生时该行首张参考图（角色图 / 参考帧）快照，同 {@link scriptRowPrompt} 用于判过期。 */
  scriptRowReference?: string | null;
  /** 派生时该行引用资产的 id / revision / hash / locks 快照，用于资产级过期判定。 */
  scriptRowAssetSnapshot?: string | null;
  /** 脚本分镜图沿用的视频/关键帧导演交接包。 */
  scriptCreativeHandoff?: VideoCreativeHandoff | null;
  /** 该分镜图必须服从的全片交付规格。 */
  deliverySpec?: VideoDeliverySpec | null;
  /**
   * 脚本**资产**图节点的认领键 `role:名字`（等价 LibTV 的 `linkedNodeId` + `thumbnailUrl`）。
   *
   * 脚本节点从分镜行的标签推导出角色 / 场景 / 道具三族资产，每族一张概念图；这张图画好
   * 之后要被**认回**对应的那个资产（下游镜头用它当参考图）。LibTV 的做法是把生成的节点 id
   * 写回资产台账的 `linkedNodeId`，我们反过来：键写在图上，台账按它认领 ——
   * 台账本身是推导出来的（表一改就变），存节点 id 会立刻和表不一致。
   *
   * 有它 = 这个图片节点是「某个资产的资产图」，不是普通图片节点（分镜图、素材图都不带）。
   */
  scriptAssetId?: string | null;
  /** 资产图所属的脚本节点 —— 批量生成 / 认领时用来把图圈回原脚本，防止跨节点串台。 */
  scriptAssetOwnerId?: string | null;
  /** 生成这张资产图时冻结的资产正版本号。 */
  scriptAssetRevision?: number | null;
  /** 生成这张资产图时冻结的资产定义摘要；不是媒体产物 sha256。 */
  scriptAssetContentHash?: string | null;
  /** 生成这张资产图时冻结的身份锁。 */
  scriptAssetIdentityLocks?: string[] | null;
  /** 生成这张资产图时冻结的资产依赖。 */
  scriptAssetDependencies?: string[] | null;
  isGenerating?: boolean;
  generationStartedAt?: number | null;
  /** Last failure reason, kept on the node until the next submit. */
  generationError?: string | null;
  generationErrorDetails?: string | null;
  /** Gateway request id parsed from the last failure, for support tracing. */
  generationErrorRequestId?: string | null;
  generationErrorStage?: string | null;
  generationErrorSuggestedAction?: string | null;
}

export interface StoryboardFrameItem {
  id: string;
  imageUrl: string | null;
  previewImageUrl?: string | null;
  aspectRatio?: string;
  note: string;
  order: number;
}

export interface StoryboardExportOptions {
  showFrameIndex: boolean;
  showFrameNote: boolean;
  notePlacement: 'overlay' | 'bottom';
  imageFit: 'cover' | 'contain';
  frameIndexPrefix: string;
  cellGap: number;
  outerPadding: number;
  fontSize: number;
  backgroundColor: string;
  textColor: string;
}

export interface StoryboardSplitNodeData {
  displayName?: string;
  aspectRatio: string;
  frameAspectRatio?: string;
  gridRows: number;
  gridCols: number;
  frames: StoryboardFrameItem[];
  exportOptions?: StoryboardExportOptions;
  [key: string]: unknown;
}

export interface StoryboardGenFrameItem {
  id: string;
  description: string;
  referenceIndex: number | null;
}

export type StoryboardRatioControlMode = 'overall' | 'cell';

export interface StoryboardGenNodeData {
  displayName?: string;
  gridRows: number;
  gridCols: number;
  frames: StoryboardGenFrameItem[];
  ratioControlMode?: StoryboardRatioControlMode;
  model: string;
  size: ImageSize;
  requestAspectRatio: string;
  extraParams?: Record<string, unknown>;
  imageUrl: string | null;
  previewImageUrl?: string | null;
  aspectRatio: string;
  isGenerating?: boolean;
  generationStartedAt?: number | null;
  [key: string]: unknown;
}

export type AudioTextSegment =
  | { type: 'text'; value: string }
  | { type: 'pause'; durationSec: number }
  | { type: 'filler'; token: string };

/**
 * 后端 freezone-audio 声线引用：scope 必填，character_name / identity_id / slot
 * 视 scope 而定。与 ops.ts 中的 `FreezoneAudioVoiceRef` 同构，但这里保留前端
 * 自带的 camelCase 字段，避免节点数据被序列化为 snake_case。
 */
export interface AudioVoiceRef {
  scope:
    | 'project_narrator'
    | 'user_custom'
    | 'character_default'
    | 'character_age_group'
    | 'identity'
    | 'identity_resolved';
  characterName?: string;
  identityId?: string;
  slot?: string;
  /** scope=user_custom 时必填：账号级我的音色 ID（来自 /freezone/audio/voices）。 */
  voiceId?: string;
  /** 声线内容摘要，用于跨会话检测引用是否漂移。 */
  sha256?: string;
  /** 声线资产版本；后端未提供时保持缺省。 */
  revision?: number;
}

export interface AudioNodeData extends NodeDisplayData {
  audioUrl: string | null;
  sourceFileName?: string | null;
  durationMs?: number | null;
  isUploading?: boolean;
  /**
   * 音频节点的生成类型：
   * - 'speech'(默认/缺省)：克隆音频,文本转语音(/freezone/audio/speech),用 voiceRef/语气词。
   * - 'music'：文字生成音乐(/freezone/audio/eleven-music),用 text 作为音乐描述 prompt。
   */
  audioKind?: 'speech' | 'music';
  /** Direct audio-model registry ID. Empty uses the server-side default. */
  model?: string;
  /** music 模式：生成长度(毫秒),范围 3000–600000,缺省按后端默认 30000。 */
  musicLengthMs?: number;
  /** music 模式：是否强制纯音乐(force_instrumental),缺省 true。 */
  forceInstrumental?: boolean;
  /** music 模式：是否严格遵守音乐段落时长策略(respect_sections_durations),缺省 true。 */
  respectSectionsDurations?: boolean;
  // operations panel (TTS) ---------------------------------------------------
  /** 要合成的纯文本。新字段，未来主用。 */
  text?: string;
  /** 旧字段：分段编辑器留下的 segments；保留只为兼容老节点数据。 */
  segments?: AudioTextSegment[];
  /**
   * 语气词（情绪提示词）。用户自由输入，提交时映射到后端 `emotion_prompt`。
   * 示例："紧张、压低声音、带一点恐惧感"。留空时后端按项目解说风格走默认。
   */
  emotionPrompt?: string;
  /** 选中的声线引用（freezone-audio references 接口里的一条记录）。 */
  voiceRef?: AudioVoiceRef | null;
  /** 当前声线的展示名（缓存自 references 接口，避免每次选完都要重新拉列表）。 */
  voiceLabel?: string;
  /** 当前声线的语言标签（来自 references；可空）。 */
  voiceLanguage?: string;
  isGenerating?: boolean;
  generationStartedAt?: number | null;
  /**
   * 生成失败信息。持久化到节点数据（而非面板本地 state），这样画布虚拟化
   * 卸载/重挂后仍能展示错误 + 重试，不会因组件重建而丢失。成功/开始时清空。
   */
  generationError?: string | null;
  /** Transient: which format the download menu is currently transcoding to. */
  convertingAudioFormat?: 'mp3' | 'm4a' | 'wav' | null;
  [key: string]: unknown;
}

export interface VideoStoryRow {
  /** 镜号 — 1-based shot index, optional because some payloads only sequence rows by array order. */
  shotNumber?: number | string | null;
  /** 开始时间 (HH:MM:SS or seconds, kept as string for display flexibility). */
  startTime?: string | null;
  /** 结束时间. */
  endTime?: string | null;
  /** 时长 (e.g. "1.2s")。解析原文，供展示；下单口径见 `durationSeconds`。 */
  duration?: string | null;
  /**
   * 时长（秒，数值）。对齐 LibTV `StoryboardRowCli.durationSeconds`：写入前按
   * 1.0–15.0 夹取（见 `domain/storyboardRowSpec.ts`）。与 `duration` 并存 ——
   * 后者保留解析原文，本字段是下单用的口径。
   */
  durationSeconds?: number | null;
  /** 剧情描述（LibTV `plotDescription`）。 */
  plotDescription?: string | null;
  /** 本镜出场角色（LibTV `characters[]`；表格里以顿号分隔的文本列编辑）。 */
  characters?: string[] | string | null;
  /** 角色动作（LibTV `characterAction`）。 */
  characterAction?: string | null;
  /** 微表情心理（LibTV `emotion`）。 */
  emotion?: string | null;
  /** 场景标签（LibTV `sceneTags`）。 */
  sceneTags?: string | null;
  /** 台词（LibTV `dialogue`）。 */
  dialogue?: string | null;
  /** 画面描述. */
  visualDescription?: string | null;
  /** 叙事内容. */
  narrative?: string | null;
  /** 景别. */
  shotSize?: string | null;
  /** 摄影机角度. */
  cameraAngle?: string | null;
  /** 摄影机运动. */
  cameraMovement?: string | null;
  /** 焦距与景深. */
  focalAndDof?: string | null;
  /** 光线. */
  lighting?: string | null;
  /** 背景音乐. */
  backgroundMusic?: string | null;
  /** 人声/音效. */
  voiceAndSfx?: string | null;
  /** 图像生成提示词. */
  imagePrompt?: string | null;
  /** 视频运动提示词. */
  videoMotionPrompt?: string | null;
  /** 关键帧静态 URL. */
  keyframeUrl?: string | null;
  /** Raw backend row kept around so we can render fields we didn't normalize. */
  raw?: Record<string, unknown>;
}

export interface VideoStoryNodeData extends NodeDisplayData {
  /** Source video URL the rows were derived from (for audit / re-runs). */
  sourceVideoUrl?: string | null;
  rows: VideoStoryRow[];
  /** Last successful raw payload from the backend for debug/regen. */
  rawResult?: Record<string, unknown> | null;
  isAnalyzing?: boolean;
  /** 解析开始时间戳,用于 loading 遮罩的进度百分比模拟。 */
  analysisStartedAt?: number | null;
  analysisError?: string | null;
  /** 「逐镜出视频」用的视频模型与参数（对齐脚本节点的 `imageGenConfig`）。 */
  videoGenConfig?: StoryVideoGenConfig;
  [key: string]: unknown;
}

/**
 * 视频故事节点「逐镜出视频」的配置（写法对齐 `ScriptImageGenConfig`）：跑逐镜视频
 * 时用的视频模型与参数，由「逐镜出视频」写入，下次沿用。清晰度与时长不在这里存 ——
 * 时长由分镜行的时间码决定，清晰度由视频节点按所选模型能力自行收敛。
 */
export interface StoryVideoGenConfig {
  model?: string;
  aspectRatio?: string;
  /** 全片交付规格；无值时由 aspectRatio 通过稳定画框表解析。 */
  deliverySpec?: VideoDeliverySpec;
  settings?: Record<string, unknown>;
}

/**
 * 快捷动作的键空间（「剧本生成分镜脚本」/「视频参考生成分镜脚本」/「角色生成分镜脚本」）。
 *
 * 它曾经同时被写进 `data.lastAction` 当「最近一次点击」用，但全仓没有任何读取方 ——
 * 一个只写不读的持久化字段，每点一次快捷动作就产生一次无意义的节点补丁与操作日志条目，
 * 所以那个字段删了。类型留着：`ScriptActionDef` 靠它把 SCRIPT_ACTIONS 的 key 约束住。
 */
export type ScriptGenAction = 'fromScript' | 'fromVideoRef' | 'fromCharacter';

/**
 * 脚本节点的生成分镜配置（对齐 LibTV `imageGenConfig`）：跑分镜图时用的图片
 * 模型与参数，由「生成分镜」写入，下次沿用。
 *
 * 只有 `model` / `aspectRatio` 有读者；早先还带着 `count` / `settings`，那是照抄 LibTV
 * 结构留下的空位，从来没被读过却会被持久化进每个脚本节点。等真有「一次出几张」的需求时
 * 再按实际形状加回来，而不是留着一个假接口。
 */
export interface ScriptImageGenConfig {
  model?: string;
  aspectRatio?: string;
  /** 全片交付规格；缺席时按 aspectRatio 解析同一份稳定规格。 */
  deliverySpec?: VideoDeliverySpec;
}

/**
 * 角色资产图的提示词模板。
 *
 * 旧配置兼容：four_view 对应多视图，front_full_body 对应单视图。
 */
export type ScriptAssetCharacterPromptTemplate = 'four_view' | 'front_full_body';

export type ScriptAssetViewMode = 'multi_view' | 'single_view';

/** 资产图配置在通用脚本出图配置之上增加角色模板选择。 */
export interface ScriptAssetGenConfig extends ScriptImageGenConfig {
  /** 三类资产共用的视图规格，默认多视图。 */
  viewMode?: ScriptAssetViewMode;
  /** 旧画布的角色模板；新配置优先使用 viewMode。 */
  characterPromptTemplate?: ScriptAssetCharacterPromptTemplate;
}

export interface ScriptNodeData extends NodeDisplayData {
  /** 操作区输入的剧情/参考说明文本 */
  prompt?: string;
  /** 选中的脚本生成模型 id（前端写死，后端接口未提供） */
  model?: string;
  /** 生成结果（freezone story-script 接口返回的 { title, rows[] }）。 */
  scriptResult?: unknown;
  /**
   * 生成后由服务端跑的合同报告（`freezone.script-contract.v1`）。
   *
   * 与 `scriptResult.rows` 分开存：表格只渲染那张干净的表，这里单独说明
   * 「哪一镜不符合合同、服务端修了什么」——角色卡逐字一致与第 7/8 段全篇唯一
   * 一直是靠提示词约束的，报告是它们第一次变成可读的结果。
   */
  scriptContractReport?: ScriptContractReport | null;
  /** 最近一次生成的标题，便于在节点头展示。 */
  scriptTitle?: string | null;
  /**
   * 视图模式：table 脚本视图 / creative 创意视图 / asset 资产视图。
   * `activeViewId` 是 LibTV 的**同名字段**（不是同一个视图集 —— LibTV 两代节点的默认视图
   * 各只有两个，见 `nodes/script/scriptViews.ts` 顶部）。两者同时写入、读取时后者优先。
   */
  viewMode?: string;
  activeViewId?: string;
  /** 「生成分镜」用的图片模型与参数（对齐 LibTV `imageGenConfig`）。 */
  imageGenConfig?: ScriptImageGenConfig;
  /**
   * 「生成资产图」用的图片模型与参数（角色 / 场景 / 道具的概念图）。
   *
   * 与 {@link imageGenConfig} 分开存：资产图与分镜图想要的东西不一样（资产图多半要 1:1
   * 的方图，分镜图默认 16:9），共用一个配置会让用户在两处互相改。
   */
  assetGenConfig?: ScriptAssetGenConfig;
  /**
   * 「逐镜出视频」用的视频模型与比例（与「视频故事」节点的 `videoGenConfig` 同构）。
   *
   * 派生出来的每条视频节点都会带上这里的模型绑定，下次沿用。时长与清晰度不在这里存：
   * 时长由分镜行的时长列决定，清晰度由视频节点按所选模型的契约自行收敛 —— 在这里再放
   * 一份就是两套口径。见 `nodes/script/scriptShotVideos.ts`。
   */
  videoGenConfig?: StoryVideoGenConfig;
  /**
   * 分镜表列显隐（**机制**对齐 LibTV `views[].tableConfig.columnVisibility`，但我们是
   * 节点级单一列集，不按视图各记一套）：`default` 按节点宽度自动选列（核心列优先）/
   * `all` 全列 / `manual` 用 `hiddenColumns` 逐列勾。缺席时一律按 default 走，老画布无需迁移。
   * 见 `nodes/script/scriptColumns.ts`。
   *
   * 注意别跟 `columnFilters` 混了：LibTV 那个是同层的**行过滤**（contains/equals/gt/lt，
   * 见其 `filterRowsByColumnFilters`），我们没做；列集只由这里两个字段决定。
   */
  columnMode?: string;
  /** manual 模式下被收起的列键。存黑名单：新增列默认可见。 */
  hiddenColumns?: string[];
  /** 「生成分镜」派生的分镜图组 id（对齐 LibTV `linkedImageGroupId`，只读）。 */
  linkedImageGroupId?: string | null;
  isGenerating?: boolean;
  generationStartedAt?: number | null;
  /** 最近一次生成失败的错误信息，渲染在节点本体上（提交面板取消选中后仍可见）。 */
  generationError?: string | null;
  /**
   * 失败诊断（与图片 / 视频节点同一套字段名，见 `resolveGenerationErrorDiagnostics`）：
   * 后端随错误一起送来的原始报文、请求 ID、失败阶段与建议动作。脚本节点的错误卡靠它们
   * 才能说清「哪一步失败、拿什么去查、要不要重试」，而不是只回放上游那句机器原文。
   */
  generationErrorDetails?: string | null;
  generationErrorRequestId?: string | null;
  generationErrorStage?: string | null;
  generationErrorSuggestedAction?: string | null;
  generationErrorCode?: string | null;
  generationErrorRetryable?: boolean | null;
  [key: string]: unknown;
}

/**
 * 360° 全景查看器节点。
 *
 * 视图状态（viewYawDeg / viewPitchDeg）只是 Photo Sphere Viewer 渲染时的"当前
 * 视角"，不参与持久化（用户每次打开节点都会回到 yaw=0/pitch=0）。
 * 持久化的是"校正"参数：sphereCorrectionDeg 把贴图旋到水平，frontYawDeg 把
 * 渲染坐标系里的"正前方"对齐到场景的真正前方，fovDeg 是默认 FOV。
 */
export interface Pano360ViewerNodeData extends NodeDisplayData {
  imageUrl: string | null;
  previewImageUrl?: string | null;
  /** 上游连接节点 id，仅用作 audit。 */
  sourceNodeId?: string | null;
  /** 球面贴图校正（角度制，roll/pitch/yaw 都是 [-180, 180]，pitch 内部裁到 [-90, 90]）。 */
  sphereCorrectionDeg: { roll: number; pitch: number; yaw: number };
  /** 场景里"正前方"对应的 yaw（角度制，[-180, 180]）。 */
  frontYawDeg: number;
  /** 默认 FOV，单位°，范围 [FOV_MIN, FOV_MAX] = [5, 170]。 */
  fovDeg: number;
  /** 最后一次截图导出的 JSON，便于排错；非持久化生命周期。 */
  lastExportedEntry?: Record<string, unknown> | null;
  [key: string]: unknown;
}

/**
 * 3D 世界节点。图片上游走 freezone/image-to-3gs：普通图用 source_kind="master"，
 * 360 全景图用 source_kind="pano"。节点也可以直接把 360 图作为 pano360 source
 * 加入导演世界取景。生成完成后优先写 `sources`，`plyUrl`/`panoUrl` 继续作为
 * 节点本地快捷字段。
 */
export interface ThreeDWorldNodeData extends NodeDisplayData {
  /** 用户提示词（操作面板下方输入）。 */
  prompt?: string;
  /** 模型 id。模型当前未对接，前端写死为 'marble-1.1'。 */
  model?: string;
  /** image-to-3gs 异步任务的 task_key，用于 await + UI 状态显示。 */
  taskKey?: string | null;
  /** 后端 SHARP 跑完后返回的 3GS 包静态地址，优先 SOG，兼容旧 PLY。 */
  plyUrl?: string | null;
  /** 360 全景图作为世界 source 时使用；只能转向取景，不能做真实空间移动。 */
  panoUrl?: string | null;
  /** Director World sources, kept in manifest-native source shape. */
  sources?: DirectorWorldSource[];
  /** Active Director World source id, mapped to manifest.active_source_id. */
  activeSourceId?: string | null;
  /** Per-source director scene snapshots; keeps pano/SOG camera and placements independent. */
  scenesBySourceId?: Record<string, unknown>;
  /** Per-source object layers, mapped to manifest snake_case when supported. */
  layersBySourceId?: Record<string, DirectorObjectLayer>;
  /** 来源节点 id，仅用于 audit。 */
  sourceNodeId?: string | null;
  /** 来源类型：image / text — 决定提交走哪条 API。 */
  sourceKind?: 'image' | 'text' | null;
  /** image-to-3gs 的来源类型（后端 source_kind 字段）：
   * master/reverse 生成单面 3GS，pano 走 360 全景。默认 master。 */
  plyKind?: 'master' | 'reverse' | 'pano';
  isGenerating?: boolean;
  generationStartedAt?: number | null;
  /** 错误消息（提交或 task 失败时显示）。 */
  errorMessage?: string | null;
  /**
   * 节点缩略图。从素材库加入时灌入同场景的 scene image 作为封面；上游
   * 图片节点连上后优先用上游图，没上游则回落到 previewImageUrl，再没有
   * 才显示 Orbit 占位。
   */
  previewImageUrl?: string | null;
  /**
   * 3D viewer 内部编辑状态的快照（actors / props / staging + 相机视角）。
   * 用户按 viewer sidebar 的「保存场景编辑」按钮时写入；下次进入 viewer 自动恢复。
   * 结构是 viewer engine 自己定义的 `ThreeDSceneSnapshot`，对画布 store 透明。
   */
  scene?: unknown;
  [key: string]: unknown;
}

export interface SkillNodeData extends NodeDisplayData {
  skill_id: string;
  skill_schema_version?: string;
  isGenerating?: boolean;
  generationStartedAt?: number | null;
  generationError?: string | null;
  skillRunId?: string | null;
  skillInputSignature?: string | null;
  skillIdempotencyKey?: string | null;
  generationTaskKey?: string | null;
  generationTaskType?: string | null;
  generationTaskJobId?: string | null;
  parameters?: Record<string, unknown>;
  [key: string]: unknown;
}

export type CanvasNodeData =
  | UploadImageNodeData
  | ExportImageNodeData
  | BeatContextNodeData
  | TextAnnotationNodeData
  | GroupNodeData
  | ImageEditNodeData
  | ImageGenNodeData
  | StoryboardSplitNodeData
  | StoryboardGenNodeData
  | VideoNodeData
  | AudioNodeData
  | VideoStoryNodeData
  | VideoComposeNodeData
  | ScriptNodeData
  | Pano360ViewerNodeData
  | ThreeDWorldNodeData
  | SkillNodeData;

export type CanvasNode = Node<CanvasNodeData, CanvasNodeType>;
export type CanvasEdge = Edge;

export interface NodeCreationDto {
  type: CanvasNodeType;
  position: XYPosition;
  data?: Partial<CanvasNodeData>;
}

export interface StoryboardNodeCreationDto {
  position: XYPosition;
  rows: number;
  cols: number;
  frames: StoryboardFrameItem[];
}

export const NODE_TOOL_TYPES = {
  crop: 'crop',
  annotate: 'annotate',
  splitStoryboard: 'split-storyboard',
} as const;

export type NodeToolType = (typeof NODE_TOOL_TYPES)[keyof typeof NODE_TOOL_TYPES];

export interface ActiveToolDialog {
  nodeId: string;
  toolType: NodeToolType;
}

export function isUploadNode(
  node: CanvasNode | null | undefined
): node is Node<UploadImageNodeData, typeof CANVAS_NODE_TYPES.upload> {
  return node?.type === CANVAS_NODE_TYPES.upload;
}

export function isImageEditNode(
  node: CanvasNode | null | undefined
): node is Node<ImageEditNodeData, typeof CANVAS_NODE_TYPES.imageEdit> {
  return node?.type === CANVAS_NODE_TYPES.imageEdit;
}

export function isImageGenNode(
  node: CanvasNode | null | undefined
): node is Node<ImageGenNodeData, typeof CANVAS_NODE_TYPES.imageGen> {
  return node?.type === CANVAS_NODE_TYPES.imageGen;
}

export function isExportImageNode(
  node: CanvasNode | null | undefined
): node is Node<ExportImageNodeData, typeof CANVAS_NODE_TYPES.exportImage> {
  return node?.type === CANVAS_NODE_TYPES.exportImage;
}

export function isBeatContextNode(
  node: CanvasNode | null | undefined
): node is Node<BeatContextNodeData, typeof CANVAS_NODE_TYPES.beatContext> {
  return node?.type === CANVAS_NODE_TYPES.beatContext;
}

export function isGroupNode(
  node: CanvasNode | null | undefined
): node is Node<GroupNodeData, typeof CANVAS_NODE_TYPES.group> {
  return node?.type === CANVAS_NODE_TYPES.group;
}

export function isStoryboardGroupNode(
  node: CanvasNode | null | undefined
): node is Node<GroupNodeData, typeof CANVAS_NODE_TYPES.group> {
  return isGroupNode(node) && node.data.storyboardGroup === true;
}

export function isProtectedProjectionGroupNode(
  node: CanvasNode | null | undefined
): node is Node<GroupNodeData, typeof CANVAS_NODE_TYPES.group> {
  if (!isGroupNode(node)) {
    return false;
  }
  return (
    node.data.user_spawned !== true &&
    typeof node.data.projection_key === 'string' &&
    node.data.projection_key.trim().length > 0
  );
}

export function isTextAnnotationNode(
  node: CanvasNode | null | undefined
): node is Node<TextAnnotationNodeData, typeof CANVAS_NODE_TYPES.textAnnotation> {
  return node?.type === CANVAS_NODE_TYPES.textAnnotation;
}

export function isStoryboardSplitNode(
  node: CanvasNode | null | undefined
): node is Node<StoryboardSplitNodeData, typeof CANVAS_NODE_TYPES.storyboardSplit> {
  return node?.type === CANVAS_NODE_TYPES.storyboardSplit;
}

export function isStoryboardGenNode(
  node: CanvasNode | null | undefined
): node is Node<StoryboardGenNodeData, typeof CANVAS_NODE_TYPES.storyboardGen> {
  return node?.type === CANVAS_NODE_TYPES.storyboardGen;
}

export function isVideoNode(
  node: CanvasNode | null | undefined
): node is Node<VideoNodeData, typeof CANVAS_NODE_TYPES.video> {
  return node?.type === CANVAS_NODE_TYPES.video;
}

export function isAudioNode(
  node: CanvasNode | null | undefined
): node is Node<AudioNodeData, typeof CANVAS_NODE_TYPES.audio> {
  return node?.type === CANVAS_NODE_TYPES.audio;
}

export function isSkillNode(
  node: CanvasNode | null | undefined
): node is Node<SkillNodeData, typeof CANVAS_NODE_TYPES.skill> {
  return node?.type === CANVAS_NODE_TYPES.skill;
}

export function isVideoStoryNode(
  node: CanvasNode | null | undefined
): node is Node<VideoStoryNodeData, typeof CANVAS_NODE_TYPES.videoStory> {
  return node?.type === CANVAS_NODE_TYPES.videoStory;
}

export function isScriptNode(
  node: CanvasNode | null | undefined
): node is Node<ScriptNodeData, typeof CANVAS_NODE_TYPES.script> {
  return node?.type === CANVAS_NODE_TYPES.script;
}

export function isVideoComposeNode(
  node: CanvasNode | null | undefined
): node is Node<VideoComposeNodeData, typeof CANVAS_NODE_TYPES.videoCompose> {
  return node?.type === CANVAS_NODE_TYPES.videoCompose;
}

export function isPano360ViewerNode(
  node: CanvasNode | null | undefined
): node is Node<Pano360ViewerNodeData, typeof CANVAS_NODE_TYPES.pano360Viewer> {
  return node?.type === CANVAS_NODE_TYPES.pano360Viewer;
}

export function isThreeDWorldNode(
  node: CanvasNode | null | undefined
): node is Node<ThreeDWorldNodeData, typeof CANVAS_NODE_TYPES.threeDWorld> {
  return node?.type === CANVAS_NODE_TYPES.threeDWorld;
}

export function nodeHasImage(node: CanvasNode | null | undefined): boolean {
  if (!node) {
    return false;
  }

  if (isUploadNode(node) || isImageEditNode(node) || isExportImageNode(node)) {
    return Boolean(node.data.imageUrl);
  }

  if (isStoryboardSplitNode(node)) {
    return node.data.frames.some((frame) => Boolean(frame.imageUrl));
  }

  if (isStoryboardGenNode(node)) {
    return Boolean(node.data.imageUrl);
  }

  return false;
}

// The single image an image-bearing node's toolbar/tools should act on.
// imageGen has no generated result until the user hits 生成, but an uploaded
// 参考图 (referenceImageUrl) is still the image shown on the node — operations
// like 抠图 / 裁剪 / 分格抽取 should target it, matching ImageGenNode's previewUrl
// fallback order (imageUrl → previewImageUrl → referenceImageUrl).
export function resolveNodeSourceImageUrl(
  node: CanvasNode | null | undefined
): string | null {
  if (!node) {
    return null;
  }
  if (isImageGenNode(node)) {
    return node.data.imageUrl || node.data.previewImageUrl || node.data.referenceImageUrl || null;
  }
  if (isUploadNode(node) || isImageEditNode(node) || isExportImageNode(node)) {
    return node.data.imageUrl || node.data.previewImageUrl || null;
  }
  return null;
}
