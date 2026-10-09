// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import {
  CANVAS_NODE_TYPES,
  isVideoNode,
  type CanvasEdge,
  type CanvasNode,
  type VideoDeliverySpec,
  type VideoGenQuality,
  type VideoNodeData,
  type VideoCreativeHandoff,
  type VideoShotContractFacts,
} from '@/features/canvas/domain/canvasNodes';
import {
  readScriptShotId,
  SCRIPT_SHOT_ID_NODE_FIELD,
} from '@/features/canvas/domain/scriptShotIdentity';
import type { FreezoneStoryScriptRow } from '@/api/ops';
import type { FreezoneStoryDirectorPlan } from '@/api/scriptContract';
import { resolveAbsolutePosition, useCanvasStore } from '@/stores/canvasStore';
import {
  findStoryboardBlockOrigin,
  storyboardGridCols,
  storyboardGridPositions,
  STORYBOARD_NODE_GAP_X,
  STORYBOARD_NODE_GAP_Y,
  type StoryboardRect,
} from './scriptStoryboard';
import {
  buildScriptRowKeys,
  cellText,
  isScriptNoValue,
  isScriptNoDialogue,
  rowCharacters,
  rowImagePrompt,
  scriptRowShotNumber,
  splitScriptTags,
  scriptCharacterStateText,
} from './scriptViews';
import { parseDurationSeconds } from './scriptStats';
import { cameraDirectionText, cameraDirectionNeedsReview, setCameraDirection } from '@/features/canvas/domain/promptCamera';
import {
  parsePromptSegment,
  splitPromptSegmentChunks,
} from '@/features/canvas/domain/promptSegments';
import { storyboardImageNodesForScript } from './scriptStoryboardMembers';
import { buildScriptShotSpecs } from './scriptStoryboard';
import { collectScriptAssetLedger, scriptAssetImageNodes } from './scriptAssets';
import { withScriptVideoQuality } from './scriptRenderQuality';
import { scriptRowFingerprint } from './scriptRowFingerprint';
import { scriptGenerationDuration, scriptGenerationPrompt, type ScriptVideoModelCapabilities } from './scriptVideoDuration';
import { scriptShotKeyframes } from './scriptShotKeyframes';
import { buildScriptRowSnapshots, computeStoryboardStaleness, storyboardMemberSnapshot } from './scriptStaleness';
import { prepareScriptEndFrameDraft } from './scriptEndFrameDraft';
import type { ScriptShotRefEntry } from './scriptShotRefs';
import { ensureScriptVideoAssetReferences, scriptAssetVideoMode, scriptVideoExecutionPromptMatches } from './scriptShotVideoReferences';
import { videoGenerationSourceMatches } from '@/features/canvas/application/videoGenerationSource';
import { scriptKeyframePlan, scriptKeyframePlanPrompt, scriptKeyframeVisualContext, type ScriptKeyframePlanItem } from './scriptKeyframePlan';
import { buildScriptCreativeHandoff, scriptDirectorVisualContext, scriptSceneSpatialContext, scriptShotVisualContext } from './scriptCreativeHandoff';
import { ensureShotKeyframeNodes } from './scriptKeyframeImages';

function scriptVideoModelIssue(spec: ScriptShotVideoSpec, model?: ScriptVideoModelCapabilities | null, existing?: CanvasNode, graph: CanvasGraphSlice = liveGraph(), continuitySourceId?: string): string | null {
  const assetCount = spec.assetReferences.filter(reference => reference.assetId).length;
  const referenceSources = new Set(graph.edges.filter(edge => edge.target === existing?.id && edge.data?.role !== SCRIPT_SHOT_VIDEO_EDGE_ROLE && edge.data?.role !== 'scriptShotAssetReference' && edge.data?.role !== SCRIPT_SHOT_CONTINUITY_EDGE_ROLE && edge.data?.role !== SCRIPT_SHOT_KEYFRAME_EDGE_ROLE).map(edge => edge.source));
  if (continuitySourceId) referenceSources.add(continuitySourceId);
  const requiredImages = new Set(spec.assetReferences.map(reference => reference.imageUrl));
  for (const source of referenceSources) {
    const url = firstFrameUrl(graph.nodes.find(node => node.id === source));
    if (url) requiredImages.add(url);
  }
  const imageCount = requiredImages.size + 1 + spec.keyframePlan.length;
  if (assetCount && spec.generationMode === 'firstLastFrame') return '本镜需要资产多参考，首尾帧模式不能同时提交这些资产；请改为多参考生成或换用兼容的镜头方案';
  if (imageCount > 1 && spec.generationMode !== 'textToVideo' && spec.generationMode !== 'firstLastFrame' && !scriptAssetVideoMode(imageCount, model)) return `本镜需要 ${imageCount} 张参考图（含关键帧与资产），请选择支持该数量多参考图片的模型`;
  if (spec.generationMode === 'textToVideo' && isScriptNoValue(spec.startState ?? '')) return '文生视频需要明确的可见起始状态，请补充本镜起始状态';
  if (spec.generationMode === 'textToVideo' && spec.requiresAssetReference) return '本镜有需要锁定的资产，请使用带参考图的生成方式，避免文生丢失资产一致性';
  if (spec.generationMode === 'textToVideo' && !model?.supportedModes?.includes('textToVideo')) return '所选模型未声明支持文生视频，请换模型';
  if (spec.generationMode === 'textToVideo' && existing && graph.edges.some(edge => edge.target === existing.id && firstFrameUrl(graph.nodes.find(node => node.id === edge.source)))) return '文生视频节点仍连接图片，请选择参考图生成方式或明确移除图片连线';
  if (!model) return null;
  if (spec.generationMode === 'firstLastFrame') {
    if (!model.supportedModes?.includes('firstLastFrame')) return '所选模型未声明支持首尾帧，请换模型';
    if (scriptGenerationDuration(spec.durationSec, model) !== spec.durationSec) return '首尾帧终点对应生成末尾，请选择恰好支持本镜时长的模型或调整本镜时长';
  }
  if (spec.durationSec !== null) {
    if (scriptGenerationDuration(spec.durationSec, model) === null) return `计划用 ${spec.durationSec}s，所选模型没有足够长的可指定档位；请拆分长镜头或换模型`;
  }
  const needsAudio = existing?.data?.generateAudioUserSet === true
    ? existing.data.generateAudio === true : true;
  if (needsAudio && model.nativeAudio === 'unsupported') return '本镜需要原生声音，所选模型不支持；请修改声音方案或换模型';
  return null;
}

/**
 * 「脚本分镜图 → 逐镜视频节点」落盘：把**已经出好图**的每一镜派生成一个视频节点，
 * 首帧取那一镜的分镜图（靠一条真实血缘边表达），提示词取该行的**视频运动提示词**，
 * 时长取该行的时长列 —— 补上 LibTV 那条「脚本 → 分镜图 → 批量出视频」级联的最后一跳。
 *
 * 为什么需要它：本项目的最后一跳此前只存在于「视频故事」节点那条路上
 * （`nodes/videoStory/videoStoryShotVideos.ts`，源是**拉片**解析出来的行）。脚本节点自己
 * 那条路（资产图 → 分镜图）走到分镜图就断了 —— 用户在脚本里出完分镜图要出视频，只能
 * 一个镜头一个镜头地新建视频节点、手画首帧边、手抄运动提示词、手填时长。而脚本的后端
 * 提示词**一直在产出** `video_motion_prompt` 这一列（见 `src/novelvideo/freezone/text_node.py`
 * 的六段式运动提示词），全仓此前没有任何消费方：表里有、出片时没人用。
 *
 * 与「视频故事」那条路的三个同款约定（刻意保持一致，两条路读起来是同一件事）：
 *
 * 1. **不动 `VideoNode` 的提交逻辑**。派生的就是普通视频节点，首帧走节点既有的上游镜像
 *    取图（`collectUpstreamImageUrls` → `submittableImageUrl`），所以**必须有一条从分镜图
 *    节点连到视频节点的边**。没有边的 i2v 节点会以「请先连接参考图片」拒绝提交，绝不会
 *    悄悄改成文生视频。
 * 2. **只处理已经出图的镜**。首帧是这条链路的准入条件，没有图的镜跳过（弹层会说明数量）。
 * 3. **不入组**。分镜组在领域层是图片专用（`storyboardGroupMembers` 只收 `isImageGenNode`），
 *    硬塞视频节点会得到一个组里画不出来的成员。
 * 4. **同场景的相邻镜再接一条「上一镜承接」边**（T-153，见下）。硬切、换场景、或模型
 *    接不下第二张图时不接。
 *
 * 视频参考沿用本镜的资产台账：关键帧在前，角色、场景、道具设定图在后。
 * 参考顺序与提示词编号一致；付费前校验模型支持的多参考模式和容量。
 * 对齐 oiioii §live_canvas/role_batch.json §roleMainImageGeneratePreview（角色设定图当
 * 下游的锚）与 libtv §DISTILL/06_AGENT_BEHAVIOR_SPEC.md §5（`@scene/@character`
 * 引用 + 分镜表驱动出片）。
 *
 * T-153 补的第二条边解决的是另一个问题：三级接力只管「角色长什么样」，不管「镜头之间
 * 接不接得上」。只有首帧一张图时，同场景的相邻两镜是各拍各的（用户实测观感是「每个视频
 * 都像独生视频」）。所以同场景的相邻镜再挂一条**上一镜分镜图**的边，让它当第二个画面锚。
 *
 * 三个刻意的取舍：
 *
 * - **上游全通，缺的只是这一张图**。`VideoNode` 的 i2v 分支本来就是「1 张 = 图生视频、
 *   2–9 张 = 图片参考视频」（`VideoNode.tsx` 的 `collectUpstreamImageUrls`），而
 *   `useVideoModeReconciliation.preferDeclaredMultiReferenceMode` 会在上游超过 1 张时
 *   自动把 `imageToVideo` 升级成 `allReference`。所以这里只负责把边接上，一个字都不用改
 *   提交逻辑。
 * - **首帧边必须排在边序第一张**。`collectUpstreamImageUrls` 按边序取图、第一张就是首帧，
 *   所以承接边必须在首帧边**之后**建（见 {@link createShotVideoNodes} 的建边顺序）。
 * - **只接同场景、非硬切**。硬切是刻意的画面断裂，换场景则上一镜的画面根本不是同一个空间，
 *   挂上去只会互相打架（Runway 官方口径「一图一主体，避免参考图里多个主体竞争」）。
 */

/** 血缘边标记：分镜图 → 派生视频节点。 */
export const SCRIPT_SHOT_VIDEO_EDGE_ROLE = 'scriptShotVideo';

/**
 * 承接边标记：**上一镜**的分镜图 → 本镜视频节点（T-153）。
 *
 * 与 {@link SCRIPT_SHOT_VIDEO_EDGE_ROLE} 分成两个角色是必须的：那个角色在别处被当成
 * 「谁供的首帧」来读（`derivedShotVideoNodeIds`、`hasShotVideoEdge` 都按它判），
 * 混进一条来路不同的边会被读成「这一镜的首帧来自上一镜」。
 */
export const SCRIPT_SHOT_CONTINUITY_EDGE_ROLE = 'scriptShotContinuity';

/** 本镜内部状态画面边；它们属于同一个视频节点，不会增加视频镜头数。 */
export const SCRIPT_SHOT_KEYFRAME_EDGE_ROLE = 'scriptShotKeyframe';

/** 该视频节点由哪个分镜图节点供首帧（边丢了靠它补回来）。 */
export const SCRIPT_SHOT_VIDEO_IMAGE_FIELD = 'scriptShotImageNodeId';

/** 派生它的脚本节点 id（行身份与归属判定的根）。 */
export const SCRIPT_SHOT_VIDEO_SOURCE_FIELD = 'scriptShotSourceNodeId';

/** 行标识（与分镜图节点同一套行键）。 */
export const SCRIPT_SHOT_VIDEO_ROW_FIELD = 'scriptShotRowKey';

/** T-047 稳定镜头身份；读取时优先它，旧节点回落到 {@link SCRIPT_SHOT_VIDEO_ROW_FIELD}。 */
export const SCRIPT_SHOT_VIDEO_SHOT_ID_FIELD = SCRIPT_SHOT_ID_NODE_FIELD;

/** 派生时的运动提示词快照：表里改过之后靠它判这一镜的视频是否已过期。 */
export const SCRIPT_SHOT_VIDEO_PROMPT_FIELD = 'scriptShotRowMotionPrompt';

/** 派生时的时长快照：表里的时长改过之后，旧视频同样应重新排队。 */
export const SCRIPT_SHOT_VIDEO_DURATION_FIELD = 'scriptShotRowDurationSec';
export const SCRIPT_SHOT_VIDEO_GENERATION_DURATION_FIELD = 'scriptShotGenerationDurationSec';

/** 派生时的首帧图片 URL 快照：分镜图原地重出后，视频首帧也已经变了。 */
export const SCRIPT_SHOT_VIDEO_FIRST_FRAME_FIELD = 'scriptShotFirstFrameUrl';

/** 派生时整行内容指纹：服务端用它证明视频对应的仍是当前脚本行。 */
export const SCRIPT_SHOT_VIDEO_ROW_FINGERPRINT_FIELD = 'scriptShotRowFingerprint';

/** 派生时资产 revision / 内容摘要快照；与分镜图节点上的快照必须一致。 */
export const SCRIPT_SHOT_VIDEO_ASSET_SNAPSHOT_FIELD = 'scriptShotAssetRevisionSnapshot';

/** 视频节点的设计尺寸（与 `canvasStore` 的 FALLBACK_NODE_SIZES / VideoNode 默认一致）。 */
export const SCRIPT_SHOT_VIDEO_CELL_WIDTH = 580;
export const SCRIPT_SHOT_VIDEO_CELL_HEIGHT = 380;

const DEFAULT_ASPECT_RATIO = '16:9';
const DEFAULT_DURATION_SEC = 5;
const DEFAULT_FPS = 30;
const DEFAULT_SAFE_AREA = Object.freeze({
  top: 0.05,
  right: 0.05,
  bottom: 0.1,
  left: 0.05,
});
const DELIVERY_PIXELS: Record<string, { width: number; height: number }> = {
  '16:9': { width: 1920, height: 1080 },
  '4:3': { width: 1440, height: 1080 },
  '1:1': { width: 1080, height: 1080 },
  '3:4': { width: 1080, height: 1440 },
  '9:16': { width: 1080, height: 1920 },
};

/**
 * 全片唯一交付规格。比例选择是用户入口，宽高、fps、安全区在派生第一帧之前就冻结，
 * 后续分镜图、逐镜视频、正式合成全部读这一份，不再各自从节点默认值猜。
 */
export function resolveScriptDeliverySpec(aspectRatio?: string | null): VideoDeliverySpec {
  const key = (aspectRatio ?? '').trim();
  const pixels = DELIVERY_PIXELS[key] ?? DELIVERY_PIXELS[DEFAULT_ASPECT_RATIO];
  return {
    width: pixels.width,
    height: pixels.height,
    aspectRatio: DELIVERY_PIXELS[key] ? key : DEFAULT_ASPECT_RATIO,
    fps: DEFAULT_FPS,
    safeArea: { ...DEFAULT_SAFE_AREA },
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/** 读取旧画布或持久化输入里的交付规格；字段不完整时拒绝，不用半份规格继续生成。 */
export function normalizeScriptDeliverySpec(value: unknown): VideoDeliverySpec | null {
  if (!isRecord(value)) return null;
  const width = Number(value.width);
  const height = Number(value.height);
  const fps = Number(value.fps);
  const aspectRatio = typeof value.aspectRatio === 'string' ? value.aspectRatio.trim() : '';
  const safeArea = isRecord(value.safeArea) ? value.safeArea : null;
  if (
    !Number.isFinite(width) ||
    !Number.isFinite(height) ||
    !Number.isFinite(fps) ||
    width <= 0 ||
    height <= 0 ||
    fps <= 0 ||
    aspectRatio.length === 0 ||
    !safeArea
  ) {
    return null;
  }
  const top = Number(safeArea.top);
  const right = Number(safeArea.right);
  const bottom = Number(safeArea.bottom);
  const left = Number(safeArea.left);
  if (
    ![top, right, bottom, left].every((item) => Number.isFinite(item) && item >= 0 && item < 1)
  ) {
    return null;
  }
  return {
    width: Math.round(width),
    height: Math.round(height),
    aspectRatio,
    fps: Math.round(fps),
    safeArea: { top, right, bottom, left },
  };
}

export function scriptDeliverySpecsEqual(
  left: VideoDeliverySpec | null | undefined,
  right: VideoDeliverySpec | null | undefined,
): boolean {
  if (!left || !right) return left === right;
  return (
    left.width === right.width &&
    left.height === right.height &&
    left.aspectRatio === right.aspectRatio &&
    left.fps === right.fps &&
    left.safeArea.top === right.safeArea.top &&
    left.safeArea.right === right.safeArea.right &&
    left.safeArea.bottom === right.safeArea.bottom &&
    left.safeArea.left === right.safeArea.left
  );
}

/** 原始事实快照只用于判断重跑；它不是后端合同 hash。 */
export function scriptShotContractFactsSnapshot(value: unknown): string {
  if (!isRecord(value)) return '';
  const facts = { ...value };
  if (isRecord(facts.creativeHandoff) && Array.isArray(facts.creativeHandoff.referenceResponsibilities)) {
    // Live video numbering is a submission fact, not a change to the authored shot.
    const handoff = { ...facts.creativeHandoff };
    const references = facts.creativeHandoff.referenceResponsibilities.filter(item => !isRecord(item) || item.scope !== 'video');
    if (references.length) handoff.referenceResponsibilities = references;
    else delete handoff.referenceResponsibilities;
    if (Object.keys(handoff).length) facts.creativeHandoff = handoff;
    else delete facts.creativeHandoff;
  }
  const stable = (input: unknown): unknown => {
    if (Array.isArray(input)) return input.map(stable);
    if (!isRecord(input)) return input;
    return Object.fromEntries(
      Object.keys(input)
        .sort()
        .map((key) => [key, stable(input[key])]),
    );
  };
  return JSON.stringify(stable(facts));
}

/**
 * 图上读数的入参：显式传 nodes / edges 而不是内部读 store。
 *
 * 这不是为了「纯函数」好看，是 React 侧的需要：脚本节点订阅整棵 `nodes` 数组会让一张
 * 大表格跟着画布上任何节点的拖动重渲染（见 `useUpstreamGraph` 的说明）。派生账依赖
 * 别人的节点，所以把「读哪张图」变成参数，订阅侧就能只选这条链上的节点与边、
 * 交给 `useShallow` 逐项比引用（见 {@link scriptShotVideoChain}）。
 */
export interface CanvasGraphSlice {
  nodes: CanvasNode[];
  edges: CanvasEdge[];
}

function liveGraph(): CanvasGraphSlice {
  const state = useCanvasStore.getState();
  return { nodes: state.nodes, edges: state.edges };
}

/**
 * 这一镜的出片提示词从哪来。
 *
 * `motion` ＝ 后端产的 `video_motion_prompt`（六段式：镜头运动 / 主体动作 / 环境变化 /
 * 音效 / 台词 / 时长），这是**正确的那一列**。`fallback` ＝ 该列为空时使用明确动作与运镜，
 * 没有动作才退回画面提示词，但必须在弹层里说清有几镜是兜底的 ——
 * 拿画面描述当运动提示词的片子运动感会弱，这是用户该知道的事，不该被静默吞掉。
 */
export type ScriptShotVideoPromptSource = 'motion' | 'fallback';

export interface ScriptShotVideoSpec {
  rowKey: string;
  /** 展示用镜号（缺失时回落行下标 + 1）。 */
  shotNumber: string;
  /** 景别（`shot` 列），仅作留档与显示名。 */
  shotSize: string | null;
  /** 最终写进视频节点的提示词。 */
  prompt: string;
  promptSource: ScriptShotVideoPromptSource;
  /** 该镜时长（秒）；解析不出时为 null，由调用方回退到兜底档。 */
  durationSec: number | null;
  hasPrompt: boolean;
  /** 全片唯一交付规格，分镜图、逐镜视频、正式合成共用。 */
  deliverySpec: VideoDeliverySpec;
  /** 后端编译 canonical shot contract 所需的原始事实。 */
  subject: string;
  action: string;
  cameraMovement: string;
  endState: string;
  startState?: string;
  requiresAssetReference: boolean;
  transition: string;
  /** 后端对本镜生成方式的建议；当前派生链仍以分镜图首帧为实际输入。 */
  generationMode: 'imageToVideo' | 'firstLastFrame' | 'textToVideo';
  /** 音效列（`sound`）：有设计、无台词的镜头由此得到模型原生音频。 */
  soundDesign: string;
  /** 对白列（`dialogue`）：原封给模型的 `dialogue_text`。 */
  dialogueLine: string;
  /** 对白拆句（一句一条），与后端 `extract_spoken_dialogue` 的分句口径一致。 */
  spokenDialogue: string[];
  referenceBindings: Record<string, string[]>;
  assetReferences: ScriptShotRefEntry[];
  continuityIn: Record<string, unknown>;
  continuityOut: Record<string, unknown>;
  rowFingerprint: string;
  assetRevisionSnapshot: string | null;
  /** 本镜内部的状态关键画面；仍只对应一个视频节点。 */
  keyframePlan: ScriptKeyframePlanItem[];
  keyframeContext: string;
  /** 同一份导演交接包：当前镜头提示词只消费 shotPurpose，完整包随合同留档。 */
  creativeHandoff: VideoCreativeHandoff;
}

/** 正文与执行事实共用实际采用的运动稿。 */
function scriptShotMotionPrompt(row: FreezoneStoryScriptRow): string {
  const motionValue = cellText(row, 'video_motion_prompt');
  const originalMotionValue = scriptCharacterStateText(row, 'start')
    ? motionValue.replace(/\s*<character_state>[\s\S]*?<\/character_state>/g, '') : motionValue;
  const originalMotion = isScriptNoValue(originalMotionValue) ? '' : originalMotionValue;
  const declaredCamera = cellText(row, 'camera_movement');
  return originalMotion && !isScriptNoValue(declaredCamera)
    ? (cameraDirectionText(originalMotion)
      ? setCameraDirection(originalMotion, declaredCamera)
      : `${originalMotion}\n摄影机安排：${declaredCamera}`)
    : originalMotion;
}

/** 缺运动稿才回落明确动作/摄影或画面稿，回落来源如实标注。 */
export function scriptShotVideoPrompt(
  row: FreezoneStoryScriptRow,
  handoff = buildScriptCreativeHandoff(row),
): {
  prompt: string;
  source: ScriptShotVideoPromptSource;
} {
  const motion = scriptShotMotionPrompt(row);
  const declaredCamera = cellText(row, 'camera_movement');
  const state = [['prop_state_start', '起始'], ['prop_state_change', '变化'], ['prop_state_end', '结束']]
    .map(([key, label]) => { const value = cellText(row, key); return isScriptNoValue(value) ? '' : `${label}：${value}`; }).filter(Boolean);
  const pose = [['start_state', '起始'], ['end_state', '结束']]
    .map(([key, label]) => { const value = cellText(row, key); return isScriptNoValue(value) ? '' : `${label}：${value}`; }).filter(Boolean);
  const characterStart = scriptCharacterStateText(row, 'start');
  const characterEnd = scriptCharacterStateText(row, 'end');
  const characterStates = characterStart && characterStart === characterEnd
    ? [`全程：${characterStart}`]
    : [characterStart ? `起始：${characterStart}` : '', characterEnd ? `结束：${characterEnd}` : ''].filter(Boolean);
  const stateClause = (state.length > 0 ? `\n道具连续性：${state.join(' → ')}` : '')
    + (pose.length > 0 ? `\n可见状态接力：${pose.join(' → ')}` : '')
    + (characterStates.length ? `\n本镜服装装备：${characterStates.join(' → ')}。当前状态优先于角色卡和参考图中的基准服装；保持人物身份，不无原因重置装备。` : '')
    + (state.length || pose.length || characterStates.length ? '\n状态推进：已明确发生的释放、转移、损坏或装备变化，在后续动作和结束状态中持续成立，除非剧本明确写出再次改变。保留本镜安排的反应、停顿与动作余势，用连续物理过程到达结束状态。' : '');
  const imagePrompt = rowImagePrompt(row);
  const declaredAction = cellText(row, 'character_action');
  const fallbackDirection = [
    !isScriptNoValue(declaredAction) ? `主体动作：${declaredAction}` : '',
    !isScriptNoValue(declaredCamera) ? `摄影机安排：${declaredCamera}` : '',
  ].filter(Boolean).join('\n');
  // 观看目的解释当前镜头要让观众看到什么；cut_reason 解释切走后下一视图增加什么，
  // 后者只进入结构化交接包，避免下一镜计划污染当前镜头的可见执行。
  const creativePurpose = handoff.shotPurpose ? `本镜观看目的：${handoff.shotPurpose}` : '';
  const directorContext = scriptDirectorVisualContext(handoff);
  const appendHandoff = (value: string) =>
    [value, directorContext, scriptShotVisualContext(row), scriptSceneSpatialContext(handoff), creativePurpose].filter(Boolean).join('\n');
  const keyframePlanClause = scriptKeyframePlanPrompt(row);
  if (shotGenerationMode(row) === 'textToVideo') {
    if (!imagePrompt) return { prompt: '', source: 'fallback' };
    return { prompt: withScriptVideoQuality(appendHandoff(`画面设计：${imagePrompt}\n${motion || fallbackDirection}${stateClause}${keyframePlanClause ? `\n${keyframePlanClause}` : ''}`)), source: motion ? 'motion' : 'fallback' };
  }
  if (motion.length > 0) return { prompt: withScriptVideoQuality(appendHandoff(`${motion}${stateClause}${keyframePlanClause ? `\n${keyframePlanClause}` : ''}`)), source: 'motion' };
  const fallback = !isScriptNoValue(declaredAction)
    ? fallbackDirection : [imagePrompt, imagePrompt ? fallbackDirection : ''].filter(Boolean).join('\n');
  return { prompt: fallback ? withScriptVideoQuality(appendHandoff(`${fallback}${stateClause}${keyframePlanClause ? `\n${keyframePlanClause}` : ''}`)) : '', source: 'fallback' };
}

/**
 * 保存导演安排的秒数，不按全局上限裁切或取整。
 * 模型提交档位若与此快照不同，由共享提交入口拒绝，不静默改切点。
 */
export function scriptShotVideoDurationSeconds(row: FreezoneStoryScriptRow): number | null {
  return parseDurationSeconds(row.duration);
}

function compactFact(value: string): string {
  return value.replace(/\s+/g, ' ').trim();
}

function shotSubject(row: FreezoneStoryScriptRow): string {
  const characters = rowCharacters(row)
    .map((character) => character.name.trim())
    .filter(Boolean);
  if (characters.length > 0) return compactFact(characters.join('、'));
  return compactFact(cellText(row, 'visual_description') || cellText(row, 'shot_prompt')) || '镜头主体';
}

function shotAction(row: FreezoneStoryScriptRow): string {
  const motion = scriptShotMotionPrompt(row);
  if (motion) {
    const actions = splitPromptSegmentChunks(motion).map(parsePromptSegment)
      .filter(segment => /^(主体|物理动作|状态变化)/.test(segment.label) && !isScriptNoValue(segment.body));
    // ponytail: labelled actions only; free-form motion stays whole until semantic review is needed.
    return compactFact(actions.length ? actions.map(segment => segment.body).join('；') : motion);
  }
  const action = cellText(row, 'character_action');
  return compactFact(
    (!isScriptNoValue(action) ? action : rowImagePrompt(row)) || '完成本镜的单一主要动作',
  );
}

function shotCameraMovement(row: FreezoneStoryScriptRow): string {
  const declared = cellText(row, 'camera_movement');
  if (!isScriptNoValue(declared)) return compactFact(declared);
  const motion = scriptShotMotionPrompt(row);
  if (!motion) return '保持构图稳定，仅跟随本镜主体动作';
  const camera = cameraDirectionText(motion);
  if (camera && !isScriptNoValue(camera)) return compactFact(camera);
  const sentences = motion
    .split(/[。！？\n]+/)
    .map((sentence) => sentence.trim())
    .filter(Boolean);
  const cameraSentence = sentences.find((sentence) =>
    /(镜头|运镜|camera|推近|推进|拉远|拉出|横移|平移|摇镜|环绕|升降|跟拍|固定)/i.test(sentence),
  );
  return compactFact(cameraSentence || sentences[0] || motion);
}

function shotEndState(row: FreezoneStoryScriptRow, action: string): string {
  return compactFact(
    cellText(row, 'end_state') || cellText(row, 'visual_description') || `本镜动作完成：${action}`,
  );
}

function shotTransition(row: FreezoneStoryScriptRow): string {
  return compactFact(cellText(row, 'transition_plan') || cellText(row, 'transition') || 'direct_cut');
}

function shotGenerationMode(row: FreezoneStoryScriptRow): ScriptShotVideoSpec['generationMode'] {
  const value = cellText(row, 'generation_mode').toLowerCase();
  if (value === 'first_last_frame' || value === 'firstlastframe') return 'firstLastFrame';
  if (value === 'text_to_video' || value === 'texttovideo') return 'textToVideo';
  return 'imageToVideo';
}

function transitionIsContinuous(value: string): boolean {
  return /(continuous_action|continuous-action|动作衔接|连续动作)/i.test(value);
}

/**
 * 对白拆句：`spoken_dialogue` 是「一句一条」的数组。
 *
 * 与后端 `freezone/video_request_contract.py` 的 `extract_spoken_dialogue` 同一口径——
 * 那里也是先按句读切开再逐句交给模型，整段塞进去会让模型把停顿和语气念成一条平线。
 * 「无」这类占位不算台词（后端字段规范要求「没有就写无」，见 `text_node.py`）。
 */
export function splitSpokenDialogue(
  text: string | null | undefined,
  limit = 8,
): string[] {
  const raw = typeof text === 'string' ? text.trim() : '';
  if (isScriptNoDialogue(raw)) return [];
  return raw
    .split(/[。！？；!?;\n]+/)
    .map((part) => part.trim())
    .filter((part) => part.length > 0)
    .slice(0, limit);
}

function shotReferenceBindings(
  references: ReturnType<typeof buildScriptShotSpecs>[number]['references'],
): Record<string, string[]> {
  const bindings: Record<string, string[]> = {};
  references.forEach((entry) => {
    const value = entry.assetId || entry.name;
    if (!value) return;
    const values = bindings[entry.role] ?? [];
    if (!values.includes(value)) values.push(value);
    bindings[entry.role] = values;
  });
  return bindings;
}

/** 逐行构造出片规格（没有提示词的行也保留，弹层要如实计数）。 */
export function buildScriptShotVideoSpecs(
  rows: readonly FreezoneStoryScriptRow[],
  aspectRatio?: string | null,
  ledger?: ReturnType<typeof collectScriptAssetLedger>,
  directorPlan?: FreezoneStoryDirectorPlan | null,
): ScriptShotVideoSpec[] {
  const rowKeys = buildScriptRowKeys([...rows]);
  const deliverySpec = resolveScriptDeliverySpec(aspectRatio);
  const storyboardSpecs = buildScriptShotSpecs([...rows], ledger, deliverySpec, directorPlan);
  const base = rows.map((row, index) => {
    const shotNumber = scriptRowShotNumber(row, index);
    const creativeHandoff = storyboardSpecs[index].creativeHandoff;
    const { prompt, source } = scriptShotVideoPrompt(row, creativeHandoff);
    const shotSize = cellText(row, 'shot');
    const subject = shotSubject(row);
    const action = shotAction(row);
    const cameraMovement = shotCameraMovement(row);
    const transition = shotTransition(row);
    const generationMode = shotGenerationMode(row);
    const soundCell = cellText(row, 'sound');
    // 「本镜没有音效」和「本镜没有说话的人」共用一套占位词（`无` / `无台词` / `none` …）：
    // 音效列里写「无台词」的脚本真实存在，不排掉会把它当成一条音效设计路由成原生声音。
    const soundDesign =
      isScriptNoValue(soundCell) || isScriptNoDialogue(soundCell) ? '' : soundCell;
    const dialogueCell = cellText(row, 'dialogue');
    const dialogueLine = isScriptNoDialogue(dialogueCell) ? '' : dialogueCell;
    return {
      rowKey: rowKeys[index],
      shotNumber,
      shotSize: shotSize.length > 0 ? shotSize : null,
      prompt,
      promptSource: source,
      durationSec: scriptShotVideoDurationSeconds(row),
      hasPrompt: prompt.length > 0,
      deliverySpec,
      subject,
      action,
      cameraMovement,
      endState: shotEndState(row, action),
      startState: isScriptNoValue(cellText(row, 'start_state')) ? '' : cellText(row, 'start_state'),
      requiresAssetReference: rowCharacters(row).length > 0 || ['scene_tags', 'prop_tags'].some(key => splitScriptTags(cellText(row, key)).some(tag => !isScriptNoValue(tag))),
      transition,
      generationMode,
      soundDesign,
      dialogueLine,
      spokenDialogue: splitSpokenDialogue(dialogueLine),
      referenceBindings: shotReferenceBindings(storyboardSpecs[index]?.references ?? []),
      assetReferences: (storyboardSpecs[index]?.references ?? []).filter(reference => reference.assetId),
      keyframePlan: storyboardSpecs[index]?.keyframePlan ?? scriptKeyframePlan(row),
      keyframeContext: scriptKeyframeVisualContext(row),
      creativeHandoff,
      rowFingerprint: scriptRowFingerprint(row, index),
      assetRevisionSnapshot: storyboardSpecs[index]?.assetRevisionSnapshot ?? null,
    };
  });
  return base.map((spec, index) => {
    const previous = index > 0 ? base[index - 1] : null;
    const seam = transitionIsContinuous(spec.transition) ? 'continuous' : 'cut';
    const incoming = spec.startState
      ? { subject: spec.subject, frame: spec.startState, action_state: spec.startState,
          seam: previous?.transition ?? seam }
      : previous && transitionIsContinuous(previous.transition)
      ? {
          subject: previous.subject,
          action_state: previous.endState,
          frame: previous.endState,
          seam: previous.transition,
        }
      : {
          subject: spec.subject,
          frame: '本镜分镜图首帧',
          seam: previous?.transition ?? 'cut',
        };
    return {
      ...spec,
      // 这些字段只作为事实传给后端；真正的 shot_contract 与 hash 由 Python 编译。
      continuityIn: incoming,
      continuityOut: {
        subject: spec.subject,
        action_state: spec.endState,
        frame: spec.endState,
        camera_endpoint: spec.cameraMovement,
        transition: spec.transition,
        seam,
      },
    };
  });
}

/** 派生视频节点的显示名：与分镜图同源，但明确标出这是视频。 */
function shotVideoLabel(spec: ScriptShotVideoSpec): string {
  return spec.shotSize
    ? `镜头 ${spec.shotNumber} · ${spec.shotSize} · 视频`
    : `镜头 ${spec.shotNumber} · 视频`;
}

/** 脚本节点的分镜行（表还没生成时为空）。 */
export function scriptRowsOf(scriptNodeId: string, graph: CanvasGraphSlice = liveGraph()) {
  const node = graph.nodes.find((candidate) => candidate.id === scriptNodeId);
  const result = node?.data?.scriptResult as { rows?: unknown } | undefined;
  return Array.isArray(result?.rows) ? (result.rows as FreezoneStoryScriptRow[]) : [];
}

/**
 * 脚本节点自己派生的分镜图节点，按行标识索引。
 *
 * 三个来源，按可信度依次尝试：
 * 1. `linkedImageGroupId` 指的分镜组成员（成组后的正常态）；
 * 2. 血缘边（`role: storyboard`）**目标仍是图片节点**的那些 —— 出图散开的中间态；
 * 3. 血缘边已并组重锚（目标变成组节点、原成员记在 `__sbOrigTarget`）的那些。
 *
 * 2 / 3 都要，因为组是**出完图才收拢**的：出图期间组不存在，只有边认得出这批图；而
 * 一旦并过组，同一条边的目标就被改指到组上（`mergeStoryboardGroup` 的行为），
 * 只按「目标是不是图片节点」筛会把整批漏掉。
 *
 * 同一行标识可能有多个节点（重建时旧的还在），优先取**已经出图**的那个：只有它有
 * 可用首帧。
 */
function scriptStoryboardImagesByRowKey(
  scriptNodeId: string,
  graph: CanvasGraphSlice,
): Map<string, CanvasNode> {
  const collected = storyboardImageNodesForScript(scriptNodeId, graph);

  const byKey = new Map<string, CanvasNode>();
  collected.forEach((node) => {
    const key = readScriptShotId(node.data);
    if (!key) return;
    const existing = byKey.get(key);
    const hasImage = firstFrameUrl(node) !== null;
    const existingHasImage = firstFrameUrl(existing) !== null;
    if (!existing || (hasImage && !existingHasImage)) byKey.set(key, node);
  });
  return byKey;
}

/**
 * 首帧是否可用：视频节点提交时取的就是 `imageUrl`（见 `VideoNode.submittableImageUrl`）。
 *
 * 刻意**不认** `referenceImageUrl`：分镜图节点上的参考图是脚本行的角色图 / 资产图，
 * 拿它当首帧会让「还没出图」的镜悄悄用一张角色头像去出片 —— 那是另一个镜的画面。
 * 与 `storyboardMemberImageUrl` 同一口径。
 */
function firstFrameUrl(node: CanvasNode | undefined): string | null {
  if (!node) return null;
  if (node.data?.isGenerating || node.data?.canvas_auto_generate_once || node.data?.generationError) return null;
  const url = node.data?.imageUrl ?? node.data?.previewImageUrl;
  return typeof url === 'string' && url.length > 0 ? url : null;
}

function capturedTailUrl(sourceId: string | undefined, graph: CanvasGraphSlice = liveGraph()): string {
  const node = graph.nodes.find(candidate => candidate.id === sourceId);
  const metadata = node?.data.captureMetadata as Record<string, unknown> | undefined;
  return metadata?.capture_mode === 'last' ? firstFrameUrl(node) ?? '' : '';
}

/** A captured frame is valid only for the exact source video version it records. */
export function scriptTailReferenceStaleReason(video: CanvasNode, graph: CanvasGraphSlice): string | null {
  const recorded = typeof video.data.scriptShotTailReferenceUrl === 'string' ? video.data.scriptShotTailReferenceUrl : '';
  const sources = graph.edges.filter(edge => edge.target === video.id && edge.data?.role === SCRIPT_SHOT_CONTINUITY_EDGE_ROLE)
    .map(edge => graph.nodes.find(node => node.id === edge.source))
    .filter((node): node is CanvasNode => Boolean(node));
  const tails = sources.filter(node => (node.data.captureMetadata as Record<string, unknown> | undefined)?.capture_mode === 'last');
  if (!recorded && tails.length === 0) return null;
  if (tails.length !== 1 || !recorded || firstFrameUrl(tails[0]) !== recorded) return '尾帧参考已变化，请重新生成这一镜';
  const metadata = tails[0].data.captureMetadata as Record<string, unknown>;
  const source = graph.nodes.find(node => node.id === metadata.source_node_id);
  if (metadata.source_kind !== 'video_frame_capture' || !source || !isVideoNode(source)
    || source.data.generationError || source.data.isGenerating
    || !source.data.videoUrl || source.data.videoUrl !== metadata.source_video_url) {
    return '上一镜视频已变化，原尾帧参考已过期，请重新截尾帧并生成这一镜';
  }
  return null;
}

function nodeRowKey(node: CanvasNode | undefined): string | null {
  return readScriptShotId(node?.data);
}

/**
 * 已派生的视频节点。两个来源取并集（去重）：节点自带的归属字段、以及角色为
 * {@link SCRIPT_SHOT_VIDEO_EDGE_ROLE} 的边的目标。
 *
 * 并集是刻意的：边会被人删、也会在分镜图重建时被连带删掉，而身份字段一直留着 ——
 * 任何一条线索断了都不能被当成「一个都没派过」，否则重复点击会再派生一批付费节点。
 */
function derivedShotVideoNodeIds(
  scriptNodeId: string,
  graph: CanvasGraphSlice,
): string[] {
  const ids = new Set<string>();
  graph.nodes.forEach((node) => {
    if (node.data?.[SCRIPT_SHOT_VIDEO_SOURCE_FIELD] === scriptNodeId && isVideoNode(node)) {
      ids.add(node.id);
    }
  });
  const byId = new Map(graph.nodes.map((node) => [node.id, node] as const));
  const storyboardImageIds = new Set(
    [...scriptStoryboardImagesByRowKey(scriptNodeId, graph).values()].map((node) => node.id),
  );
  graph.edges
    .filter((edge) => edge.data?.role === SCRIPT_SHOT_VIDEO_EDGE_ROLE)
    .forEach((edge) => {
      const target = byId.get(edge.target);
      if (!target || !isVideoNode(target)) return;
      // 边没有脚本身份，不能只凭 role 把画布上所有同角色边都算进来。正常节点靠自身
      // 的 source 字段归属；身份字段丢失时，只有“首帧源确属本脚本分镜图”的边能兜底。
      // 少了这一层，两个脚本节点会互相夺批：A 计划里带进 B 的节点，重建时把 B 删掉。
      if (
        target.data?.[SCRIPT_SHOT_VIDEO_SOURCE_FIELD] === scriptNodeId ||
        storyboardImageIds.has(edge.source)
      ) {
        ids.add(target.id);
      }
    });
  return [...ids];
}

/** 这条派生边是否已经挂上了（补边前先查）。 */
function hasShotVideoEdge(shotImageNodeId: string, videoNodeId: string): boolean {
  return useCanvasStore
    .getState()
    .edges.some((edge) => edge.source === shotImageNodeId && edge.target === videoNodeId);
}

/** 建（或补）分镜图 → 视频节点的首帧边。已存在时是幂等的。 */
function ensureShotVideoEdge(shotImageNodeId: string, videoNodeId: string): boolean {
  for (const edge of useCanvasStore.getState().edges) {
    if (edge.target === videoNodeId && edge.data?.role === SCRIPT_SHOT_VIDEO_EDGE_ROLE && edge.source !== shotImageNodeId) useCanvasStore.getState().deleteEdge(edge.id);
  }
  if (hasShotVideoEdge(shotImageNodeId, videoNodeId)) return true;
  const edgeId = useCanvasStore.getState().addEdgeWithData(
    shotImageNodeId,
    videoNodeId,
    {
      edgeKind: 'mainline_data',
      propagates: true,
      role: SCRIPT_SHOT_VIDEO_EDGE_ROLE,
      label: '首帧',
    },
    {
      id: `edge_${shotImageNodeId}_to_${videoNodeId}_scriptShotVideo`,
      sourceHandle: 'source',
      targetHandle: 'target',
    },
  );
  return edgeId !== null;
}

/** 这条承接边是否已经挂上了（补边前先查；按角色判，不按端点判）。 */
function hasContinuityEdge(sourceNodeId: string, videoNodeId: string): boolean {
  return useCanvasStore
    .getState()
    .edges.some(
      (edge) =>
        edge.source === sourceNodeId &&
        edge.target === videoNodeId &&
        edge.data?.role === SCRIPT_SHOT_CONTINUITY_EDGE_ROLE,
    );
}

/**
 * 建（或补）上一镜分镜图 → 视频节点的承接边。已存在时是幂等的。
 *
 * **必须在首帧边之后调用**：`collectUpstreamImageUrls` 按边序取图、第一张是首帧，
 * 而 `addEdgeWithData` 是追加，所以先首帧、后承接，顺序才是对的。
 *
 * 失败不删节点：首帧边才是准入条件（见 {@link ensureShotVideoEdge} 的调用点），
 * 承接边少了只是少一个画面锚，片子照样出得来。
 */
function ensureShotContinuityEdge(sourceNodeId: string, videoNodeId: string): boolean {
  useCanvasStore.getState().edges
    .filter(edge => edge.target === videoNodeId && edge.data?.role === SCRIPT_SHOT_CONTINUITY_EDGE_ROLE && edge.source !== sourceNodeId)
    .forEach(edge => useCanvasStore.getState().deleteEdge(edge.id));
  if (hasContinuityEdge(sourceNodeId, videoNodeId)) return true;
  const edgeId = useCanvasStore.getState().addEdgeWithData(
    sourceNodeId,
    videoNodeId,
    {
      edgeKind: 'mainline_data',
      propagates: true,
      role: SCRIPT_SHOT_CONTINUITY_EDGE_ROLE,
      label: '上一镜承接',
    },
    {
      id: `edge_${sourceNodeId}_to_${videoNodeId}_scriptShotContinuity`,
      sourceHandle: 'source',
      targetHandle: 'target',
    },
  );
  return edgeId !== null;
}

function removeShotContinuityEdges(videoNodeId: string): void {
  useCanvasStore.getState().edges
    .filter((edge) => edge.target === videoNodeId && edge.data?.role === SCRIPT_SHOT_CONTINUITY_EDGE_ROLE)
    .forEach((edge) => useCanvasStore.getState().deleteEdge(edge.id));
}

function ensureShotKeyframeEdges(keyframeNodeIds: readonly string[], videoNodeId: string): void {
  const store = useCanvasStore.getState();
  const wanted = new Set(keyframeNodeIds);
  store.edges
    .filter(edge => edge.target === videoNodeId && edge.data?.role === SCRIPT_SHOT_KEYFRAME_EDGE_ROLE && !wanted.has(edge.source))
    .forEach(edge => store.deleteEdge(edge.id));
  keyframeNodeIds.forEach((sourceNodeId, index) => {
    if (useCanvasStore.getState().edges.some(edge => edge.source === sourceNodeId && edge.target === videoNodeId && edge.data?.role === SCRIPT_SHOT_KEYFRAME_EDGE_ROLE)) return;
    useCanvasStore.getState().addEdgeWithData(
      sourceNodeId,
      videoNodeId,
      { edgeKind: 'mainline_data', propagates: true, role: SCRIPT_SHOT_KEYFRAME_EDGE_ROLE, label: `状态画面 ${index + 1}` },
      { id: `edge_${sourceNodeId}_to_${videoNodeId}_scriptShotKeyframe_${index}`, sourceHandle: 'source', targetHandle: 'target' },
    );
  });
}

/**
 * 只有脚本明确声明动作连续，才允许上一镜承接；其他衔接都按切镜处理。
 *
 * 这样脚本生成器的 `transition_plan` 会成为真实执行口径；旧脚本仍可通过
 * `transition` 兼容回退。
 */
function declaredHardCut(row: FreezoneStoryScriptRow): boolean {
  const declared = shotTransition(row).toLowerCase();
  return !transitionIsContinuous(declared);
}

/** 相邻两镜是不是同一个场景：场景标签有交集（任一侧为空时判不出，按不同场景处理）。 */
function rowsShareScene(
  previous: FreezoneStoryScriptRow,
  current: FreezoneStoryScriptRow,
): boolean {
  const previousTags = new Set(splitScriptTags(cellText(previous, 'scene_tags')));
  if (previousTags.size === 0) return false;
  return splitScriptTags(cellText(current, 'scene_tags')).some((tag) => previousTags.has(tag));
}

/** Missing metadata defaults to full reference; explicit limits still apply. */
export function shotVideoAcceptsChainReference(
  model:
    | {
        supportedModes?: readonly string[];
        referenceLimits?: Record<string, { image?: number }> | null;
      }
    | null
    | undefined,
): boolean {
  return scriptAssetVideoMode(2, {
    supportedModes: model?.supportedModes,
    referenceLimits: model?.referenceLimits ?? undefined,
  }) !== null;
}

function buildShotContractFacts(
  spec: ScriptShotVideoSpec,
  firstFrameUrl: string,
  executionPrompt: string,
): VideoShotContractFacts {
  return {
    shotId: spec.rowKey,
    shotSize: spec.shotSize,
    subject: spec.subject,
    action: spec.action,
    cameraMovement: spec.cameraMovement,
    firstFrame: spec.generationMode === 'textToVideo' ? spec.startState ?? '' : firstFrameUrl,
    lastFrame: spec.endState,
    continuityIn: spec.continuityIn,
    continuityOut: spec.continuityOut,
    transition: spec.transition,
    generationMode: spec.generationMode,
    referenceBindings: spec.referenceBindings,
    executionPrompt,
    ...(Object.keys(spec.creativeHandoff).length ? { creativeHandoff: spec.creativeHandoff } : {}),
  };
}

/**
 * 该视频节点是否还需要出片：没片 / 上次失败 / 派生输入、规格或镜头事实已经改过。
 *
 * 承接边（T-153）的来源**不在**比较项里：它只是上一镜的画面锚，首帧才是主导输入。
 * 把「上一镜分镜图重出过」也算进来的话，用户重出一镜就会把后面**每一镜**都重新
 * 排队付费，代价远大于收益。提交时读的是节点上的实时上游，所以即使不重跑，参考也
 * 不会用到过期的 URL。
 */
function shotVideoNeedsRender(params: {
  nodeId: string;
  expectedPrompt: string;
  expectedDurationSec: number;
  expectedGenerationDurationSec?: number | null;
  expectedFirstFrameUrl: string;
  expectedTailReferenceUrl?: string;
  expectedDeliverySpec: VideoDeliverySpec;
  expectedShotContractFacts: VideoShotContractFacts;
  expectedRowFingerprint: string;
  expectedAssetRevisionSnapshot: string | null;
  expectedModel?: string | null;
  expectedQuality?: VideoGenQuality | null;
}, graph: CanvasGraphSlice = liveGraph()): boolean {
  const {
    nodeId,
    expectedPrompt,
    expectedDurationSec,
    expectedFirstFrameUrl,
    expectedDeliverySpec,
    expectedShotContractFacts,
    expectedRowFingerprint,
    expectedAssetRevisionSnapshot,
    expectedModel,
    expectedQuality,
  } = params;
  const node = graph.nodes.find((candidate) => candidate.id === nodeId);
  if (!node) return false;
  const promptSnapshot = node.data?.[SCRIPT_SHOT_VIDEO_PROMPT_FIELD];
  const promptChanged =
    typeof promptSnapshot !== 'string' || promptSnapshot !== expectedPrompt ||
    !scriptVideoExecutionPromptMatches(node.data);
  const durationChanged =
    node.data?.[SCRIPT_SHOT_VIDEO_DURATION_FIELD] !== expectedDurationSec ||
    (params.expectedGenerationDurationSec != null && node.data?.durationSec !== params.expectedGenerationDurationSec);
  const firstFrameChanged =
    node.data?.[SCRIPT_SHOT_VIDEO_FIRST_FRAME_FIELD] !== expectedFirstFrameUrl;
  const deliverySpecChanged = !scriptDeliverySpecsEqual(
    normalizeScriptDeliverySpec(node.data?.deliverySpec),
    expectedDeliverySpec,
  );
  const shotContractFactsChanged =
    scriptShotContractFactsSnapshot(node.data?.shotContractFacts) !==
    scriptShotContractFactsSnapshot(expectedShotContractFacts);
  const rowFingerprintChanged =
    node.data?.[SCRIPT_SHOT_VIDEO_ROW_FINGERPRINT_FIELD] !== expectedRowFingerprint;
  const assetRevisionChanged =
    (node.data?.[SCRIPT_SHOT_VIDEO_ASSET_SNAPSHOT_FIELD] ?? null) !==
    expectedAssetRevisionSnapshot;
  const modelChanged =
    typeof expectedModel === 'string' && expectedModel.trim().length > 0
      ? node.data?.model !== expectedModel
      : false;
  const qualityChanged =
    expectedQuality !== undefined ? (node.data?.quality ?? null) !== expectedQuality : false;
  const hasVideo = typeof node.data?.videoUrl === 'string' && node.data.videoUrl.length > 0;
  const sourceChanged = hasVideo && !videoGenerationSourceMatches(String(node.data.videoUrl), node.data.videoGenerationSource, expectedShotContractFacts.executionPrompt ?? '');
  const failed = Boolean(node.data?.generationError);
  const keyframes = node.data.genMode === 'firstLastFrame' ? scriptShotKeyframes(node, graph, expectedRowFingerprint) : null;
  return (
    (expectedShotContractFacts.generationMode === 'textToVideo' && node.data.genMode !== 'textToVideo') ||
    (expectedShotContractFacts.generationMode !== 'textToVideo' && node.data.genMode === 'textToVideo') ||
    (keyframes !== null && (!keyframes.ok || node.data.scriptShotLastFrameUrl !== keyframes.lastFrameUrl)) ||
    promptChanged ||
    durationChanged ||
    firstFrameChanged ||
    (node.data.scriptShotTailReferenceUrl ?? '') !== (params.expectedTailReferenceUrl ?? '') ||
    deliverySpecChanged ||
    shotContractFactsChanged ||
    rowFingerprintChanged ||
    assetRevisionChanged ||
    modelChanged ||
    qualityChanged ||
    !hasVideo ||
    sourceChanged ||
    failed
  );
}

/** 该镜派生的视频节点数据。 */
/**
 * 单镜音频合同，口径与服务端 `workflow_runtime/freezone_videos._shot_audio_contract()`
 * 一致（T-152）：
 *
 * - 有台词 → 走外部配音：`generateAudio=false` / `audioType='dialogue'` /
 *   `nativeAudioStrategy='external'`，台词整句进 `dialogueText`、拆句进 `spokenDialogue`；
 * - 有音效设计、无台词 → 要模型原生音频：`generateAudio=true` /
 *   `nativeAudioStrategy='native'`。雨声、快门声这类镜头声音本来就该和画面一起出；
 * - 两者皆无 → 保持默认静音。
 *
 * 此前这里写死 `generateAudio: false`，脚本的 `sound` 列在画布这条路上没有任何消费方，
 * 于是有音效设计的镜头全被提交成静音。
 */
function scriptShotAudioParameters(spec: ScriptShotVideoSpec): Partial<VideoNodeData> {
  return {
    generateAudio: true,
    generateAudioUserSet: false,
    nativeAudioStrategy: 'native',
    ...(spec.dialogueLine.trim() ? {
      audioType: 'dialogue' as const,
      dialogueText: spec.dialogueLine.trim(),
      spokenDialogue: spec.spokenDialogue,
    } : {}),
  };
}

/** 单镜的音频路由（体检表与节点参数共用同一份判定）。 */
function scriptShotAudioRoute(_spec: ScriptShotVideoSpec): 'native' | 'external' | 'silent' {
  return 'native';
}

function shotVideoNodeData(params: {
  scriptNodeId: string;
  shotImageNodeId: string;
  firstFrameUrl: string;
  tailReferenceUrl?: string;
  spec: ScriptShotVideoSpec;
  model: string;
  aspectKey: string;
  deliverySpec: VideoDeliverySpec;
  quality: VideoGenQuality | null | undefined;
  defaultDurationSec: number;
  generateVideos: boolean;
  modelCapabilities?: ScriptVideoModelCapabilities | null;
}): Partial<VideoNodeData> {
  const { spec } = params;
  const editSeconds = spec.durationSec ?? params.defaultDurationSec;
  const generationSeconds = scriptGenerationDuration(editSeconds, params.modelCapabilities) ?? editSeconds;
  const executionPrompt = scriptGenerationPrompt(spec.prompt, editSeconds, generationSeconds);
  return {
    label: shotVideoLabel(spec),
    displayName: shotVideoLabel(spec),
    // 提示词＝该行的**视频运动**提示词（后端产的六段式那段），不是画面提示词。
    prompt: executionPrompt,
    // 图生视频：首帧靠挂上来的那条分镜图血缘边（`submittableImageUrl` 只认上游节点）。
    genMode: spec.generationMode === 'textToVideo' ? 'textToVideo' : 'imageToVideo',
    // 留档推荐值，但不在缺少首尾帧/文生准入条件时擅自切换实际模式。
    model: params.model,
    ...(params.quality ? { quality: params.quality } : {}),
    aspectRatio: params.deliverySpec.aspectRatio || params.aspectKey,
    deliverySpec: params.deliverySpec,
    shotContractFacts: buildShotContractFacts(spec, params.firstFrameUrl, executionPrompt),
    // 顶层原始事实与 canonical contract 分开：media_dispatch 读这些事实编译合同，
    // canonical `shotContract` 只能在 Python 校验成功后回写。
    shotId: spec.rowKey,
    action: spec.action,
    continuityIn: spec.continuityIn,
    continuityOut: spec.continuityOut,
    transition: spec.transition,
    transitionPlan: spec.transition,
    generationModeRecommendation: spec.generationMode,
    referenceBindings: spec.referenceBindings,
    durationSec: generationSeconds,
    [SCRIPT_SHOT_VIDEO_GENERATION_DURATION_FIELD]: generationSeconds,
    count: 1,
    // 运镜不再写进节点字段：它已经在上面那条提示词（`spec.prompt`）的运镜段里。
    // 曾经这里写 `cameraMovement: spec.cameraMovement`，把它当成预设 id 提交，
    // 每一镜都会撞 `unknown camera_template_id: …` 400（T-154）。
    ...scriptShotAudioParameters(spec),
    // 行身份（与分镜图节点同一套键）：分镜图重建之后，「这条视频是哪一行」只认它。
    [SCRIPT_SHOT_VIDEO_SOURCE_FIELD]: params.scriptNodeId,
    [SCRIPT_SHOT_VIDEO_SHOT_ID_FIELD]: spec.rowKey,
    [SCRIPT_SHOT_VIDEO_ROW_FIELD]: spec.rowKey,
    [SCRIPT_SHOT_VIDEO_PROMPT_FIELD]: spec.prompt,
    [SCRIPT_SHOT_VIDEO_DURATION_FIELD]:
      spec.durationSec ?? params.defaultDurationSec,
    [SCRIPT_SHOT_VIDEO_FIRST_FRAME_FIELD]: params.firstFrameUrl,
    scriptShotTailReferenceUrl: params.tailReferenceUrl ?? '',
    [SCRIPT_SHOT_VIDEO_ROW_FINGERPRINT_FIELD]: spec.rowFingerprint,
    [SCRIPT_SHOT_VIDEO_ASSET_SNAPSHOT_FIELD]: spec.assetRevisionSnapshot,
    [SCRIPT_SHOT_VIDEO_IMAGE_FIELD]: params.shotImageNodeId,
    // 留档：一眼看出这条视频的提示词来源那一行（镜号 / 景别）。
    shot_number: spec.shotNumber,
    shot_size: spec.shotSize,
    shot_motion_prompt: spec.prompt,
    canvas_auto_generate_once: params.generateVideos,
  } as Partial<VideoNodeData>;
}

export interface ScatterScriptShotVideosParams {
  scriptNodeId: string;
  modelCapabilities?: ScriptVideoModelCapabilities | null;
  model?: string | null;
  aspectRatio?: string | null;
  quality?: VideoGenQuality | null;
  /** 解析不出时长时的兜底秒数；默认 5。 */
  defaultDurationSec?: number | null;
  /** 建完是否立刻出视频（打 `canvas_auto_generate_once`）。默认 false。 */
  generateVideos?: boolean;
  /**
   * 所选视频模型能不能吃下第二张图（`shotVideoAcceptsChainReference` 的结果）。
   * 为真时，同场景的相邻镜会额外接一条「上一镜承接」边。默认 false＝维持单图首帧。
   */
  continuityReference?: boolean;
}

export type ScatterScriptShotVideosMode = 'created' | 'rebuilt' | 'rearmed';

export type ScatterScriptShotVideosResult =
  | {
      ok: true;
      mode: ScatterScriptShotVideosMode;
      nodeIds: string[];
      /** 本轮新增的节点数；rearmed 时可能大于 0（后补的分镜图逐步接入）。 */
      addedCount: number;
      /** 本轮实际排队出视频的条数。 */
      armed: number;
      /** 有几镜因为没有分镜图被跳过。 */
      skippedNoImage: number;
      /** 有几行连提示词都拼不出来。 */
      skippedNoPrompt: number;
    }
  | { ok: false; reason: string };

export interface ScriptShotVideoPlan {
  ok: true;
  /** create＝还没派过；regenerate＝已有这批节点，只重跑未落地的。 */
  mode: 'create' | 'regenerate';
  /** 本轮会落成视频节点的镜数（已出图且有提示词）。 */
  shotCount: number;
  /** 点「出视频」会真的重新提交的条数。 */
  pendingCount: number;
  /** 行集合对不上 → 整批重建（旧视频节点会被删掉）。 */
  willRebuild: boolean;
  /** 表里有几行拼不出提示词。 */
  skippedNoPrompt: number;
  /** 表里有几行还没有分镜图（会在出图后再派）。 */
  skippedNoImage: number;
  /** 已经派生出来的视频节点数。 */
  derivedCount: number;
  /** 这轮会接上「上一镜承接」边的镜数（0 ＝ 这批全是各拍各的单图首帧）。 */
  continuityCount: number;
  /** 本轮会提交的视频秒数合计（＝每条 `count(1) × 该镜时长`），供计价用。 */
  plannedSeconds: number;
  /** 表里有几行用的是画面提示词兜底（没有 `video_motion_prompt`）。 */
  fallbackPromptCount: number;
  /** 逐镜体检表：每一镜会被自动填进去的出片事实，供开拍前审核。 */
  rows: ScriptShotAuditRow[];
}

/** 体检表里的一条缺项；都不是错误，是「出片前该知道的事」。 */
export type ScriptShotAuditIssue =
  | 'noImage'
  | 'noPrompt'
  | 'noCamera'
  | 'cameraNeedsReview'
  | 'missingLastFrame'
  | 'invalidDuration'
  | 'modelUnsupported'
  | 'durationMismatch';

/** 单镜出片事实（审核面只读，不产生副作用）。 */
export interface ScriptShotAuditRow {
  rowKey: string;
  shotNumber: string;
  durationSec: number | null;
  generationDurationSec: number | null;
  /** 首帧（该镜分镜图）的 URL；空串＝还没有图，这一镜进不了这一批。 */
  firstFrameUrl: string;
  /** 提示词里的运镜正文；空串＝这一镜没有运镜，画面会靠主体动作自己动。 */
  camera: string;
  /** 提示词来自运动稿还是画面词兜底。 */
  promptSource: ScriptShotVideoPromptSource;
  /** 台词（空串＝本镜没有说话的人）。 */
  dialogue: string;
  /** 音频路由：native＝要模型原生声音；external＝外部配音；silent＝静音。 */
  audioRoute: 'native' | 'external' | 'silent';
  issues: ScriptShotAuditIssue[];
}

interface ShotVideoContext {
  scriptNodeId: string;
  deliverySpec: VideoDeliverySpec;
  model: string;
  quality: VideoGenQuality | null | undefined;
  specs: ScriptShotVideoSpec[];
  /** 拼得出提示词的行。 */
  withPrompt: ScriptShotVideoSpec[];
  /** 其中已经有分镜图、这轮能派的行。 */
  shootable: Array<{ spec: ScriptShotVideoSpec; shotImageNode?: CanvasNode }>;
  /** 行标识 → 该镜分镜图节点（审核面读首帧用，与落盘共用同一份查找）。 */
  imageByRowKey: Map<string, CanvasNode>;
  /**
   * 本镜要接的上一镜分镜图节点；行标识 → 节点 id。没有就是这一镜不接承接边
   * （硬切 / 换场景 / 上一镜还没出图 / 模型吃不下第二张图）。
   */
  continuitySourceByRowKey: Map<string, string>;
  /** 这轮会接上承接边的镜数（= `continuitySourceByRowKey` 里落得到的那些）。 */
  continuityCount: number;
  existingByKey: Map<string, string>;
  existingIds: string[];
  skippedNoPrompt: number;
  skippedNoImage: number;
  fallbackPromptCount: number;
  classification: 'create' | 'rearm' | 'rebuild';
}

/**
 * 读节点 + 拆行 + 分类：`plan` 与真正落盘共用，避免「弹层说 3 条、点下去建 5 条」。
 */
function readShotVideoContext(
  scriptNodeId: string,
  graph: CanvasGraphSlice = liveGraph(),
  expectations: {
    model?: string | null;
    aspectRatio?: string | null;
    quality?: VideoGenQuality | null;
    /** 所选模型能不能吃下第二张图；见 {@link shotVideoAcceptsChainReference}。 */
    continuityReference?: boolean;
  } = {},
): { ok: false; reason: string } | ({ ok: true } & ShotVideoContext) {
  const scriptNode = graph.nodes.find((node) => node.id === scriptNodeId);
  if (!scriptNode) return { ok: false, reason: '脚本节点已不存在' };
  const rows = scriptRowsOf(scriptNodeId, graph);
  if (rows.length === 0) return { ok: false, reason: '脚本表里还没有分镜行，先生成一次脚本' };
  const ledger = collectScriptAssetLedger(rows, scriptAssetImageNodes(scriptNodeId, graph.nodes));
  const directorPlan = (scriptNode.data?.scriptResult as { director_plan?: FreezoneStoryDirectorPlan | null } | undefined)?.director_plan;
  const specs = buildScriptShotVideoSpecs(rows, expectations.aspectRatio, ledger, directorPlan);
  const deliverySpec = specs[0]?.deliverySpec ?? resolveScriptDeliverySpec(expectations.aspectRatio);
  const withPrompt = specs.filter((spec) => spec.hasPrompt);
  if (withPrompt.length === 0) {
    return { ok: false, reason: '脚本表里还没有可用的提示词（视频运动提示词与画面提示词都空）' };
  }

  const imageByKey = scriptStoryboardImagesByRowKey(scriptNodeId, graph);
  const staleness = computeStoryboardStaleness({
    rows: buildScriptRowSnapshots(rows, ledger, directorPlan),
    members: [...imageByKey.values()].map(storyboardMemberSnapshot).filter((member): member is NonNullable<typeof member> => member !== null),
  });
  const staleShots = withPrompt.filter(spec => spec.generationMode !== 'textToVideo'
    && staleness.staleNodeIdsSet.has(imageByKey.get(spec.rowKey)?.id ?? ''));
  if (staleShots.length > 0) return { ok: false, reason: `第 ${staleShots.map(spec => spec.shotNumber).join('、')} 镜分镜已过期，请更新分镜图后再出视频` };
  const shootable: Array<{ spec: ScriptShotVideoSpec; shotImageNode?: CanvasNode }> = [];
  withPrompt.forEach((spec) => {
    const shotImageNode = imageByKey.get(spec.rowKey);
    if (spec.generationMode !== 'textToVideo' && !firstFrameUrl(shotImageNode)) return;
    shootable.push({ spec, shotImageNode: spec.generationMode === 'textToVideo' ? undefined : shotImageNode });
  });
  if (shootable.length === 0) {
    return { ok: false, reason: '还没有出好图的分镜，先把分镜图出出来再逐镜出视频' };
  }

  // 承接边的源：同场景、明确连续动作、且上一镜**已经生成并有真实尾帧**。
  // 不能拿上一镜的起始分镜图冒充结束状态；那会让模型收到一个错误的动作接力信号。
  // `specs` 与 `rows` 一一对应（`buildScriptShotVideoSpecs` 逐行 map），下标可以对齐。
  const continuitySourceByRowKey = new Map<string, string>();
  if (expectations.continuityReference === true) {
    rows.forEach((row, index) => {
      if (index === 0) return;
      if (specs[index]?.generationMode !== 'imageToVideo') return;
      const previous = rows[index - 1];
      if (declaredHardCut(previous) || !rowsShareScene(previous, row)) return;
      const previousSpec = specs[index - 1];
      if (!previousSpec) return;
      const previousVideos = graph.nodes.filter(node => isVideoNode(node)
        && node.data?.[SCRIPT_SHOT_VIDEO_SOURCE_FIELD] === scriptNodeId
        && readScriptShotId(node.data) === previousSpec.rowKey
        && !node.data.generationError && !node.data.isGenerating
        && node.data?.[SCRIPT_SHOT_VIDEO_ROW_FINGERPRINT_FIELD] === previousSpec.rowFingerprint
        && typeof node.data.videoUrl === 'string' && node.data.videoUrl.trim());
      if (previousVideos.length === 1) {
        const previousVideo = previousVideos[0];
        const tails = graph.nodes.filter(node => {
          const metadata = node.data.captureMetadata as Record<string, unknown> | undefined;
          return firstFrameUrl(node) && metadata?.source_kind === 'video_frame_capture'
            && metadata.capture_mode === 'last' && metadata.source_node_id === previousVideo.id
            && metadata.source_video_url === previousVideo.data.videoUrl;
        });
        if (tails.length === 1) {
          continuitySourceByRowKey.set(specs[index].rowKey, tails[0].id);
          return;
        }
      }
      // 没有当前上一镜尾帧就不建立承接关系。视频仍可按本镜首帧独立生成，
      // 等上一镜完成并截到真实尾帧后，再次点击时会补上这条边。
    });
  }
  const shootableRowKeys = new Set(shootable.map(({ spec }) => spec.rowKey));
  const continuityCount = [...continuitySourceByRowKey.keys()].filter((rowKey) =>
    shootableRowKeys.has(rowKey),
  ).length;

  const existingIds = derivedShotVideoNodeIds(scriptNodeId, graph);
  const existingByKey = new Map<string, string>();
  const byId = new Map(graph.nodes.map((node) => [node.id, node] as const));
  existingIds.forEach((nodeId) => {
    const key = nodeRowKey(byId.get(nodeId));
    if (key) existingByKey.set(key, nodeId);
  });

  // 已派生行必须是当前“有提示词的行”的子集。数量相同还不够：行标识整批换过时原地
  // 重跑会把旧身份留在节点上；同时也不能因为后来补出更多分镜图就整批重建，那会删掉
  // 已经完成甚至已经付费的视频。只有出现已删除 / 重命名 / 已无提示词的旧行才重建。
  let classification: 'create' | 'rearm' | 'rebuild' = 'create';
  if (existingIds.length > 0) {
    const currentRowKeys = new Set(withPrompt.map((spec) => spec.rowKey));
    const allExistingRowsKnown =
      existingByKey.size === existingIds.length &&
      [...existingByKey.keys()].every((rowKey) => currentRowKeys.has(rowKey));
    classification = allExistingRowsKnown ? 'rearm' : 'rebuild';
  }

  return {
    ok: true,
    scriptNodeId,
    deliverySpec,
    model: (expectations.model ?? '').trim(),
    quality: expectations.quality,
    specs,
    withPrompt,
    shootable,
    imageByRowKey: imageByKey,
    continuitySourceByRowKey,
    continuityCount,
    existingByKey,
    existingIds,
    skippedNoPrompt: specs.length - withPrompt.length,
    skippedNoImage: withPrompt.length - shootable.length,
    fallbackPromptCount: withPrompt.filter((spec) => spec.promptSource === 'fallback').length,
    classification,
  };
}

/** 打开确认弹层前先算一遍账（不产生副作用）。 */
/**
 * 运动稿第 6 段 `[时长：4.0s]` 写了几秒；读不出来返回 null。
 *
 * 服务端有同名的 advisory 规则（`script.motion.duration_match.v1`）：运动稿写的时长
 * 与时长列不一致时，两者都会进模型，自相矛盾。这里只把它如实标出来，不改用户数据。
 */
function motionPromptDurationSeconds(prompt: string | null | undefined): number | null {
  const chunk = splitPromptSegmentChunks(prompt).find((item) =>
    parsePromptSegment(item).label.startsWith('时长'),
  );
  if (!chunk) return null;
  const match = parsePromptSegment(chunk).body.match(/(\d+(?:\.\d+)?)/);
  if (!match) return null;
  const value = Number.parseFloat(match[1]);
  return Number.isFinite(value) ? value : null;
}

/** 逐镜体检表：把每一镜会被自动填进去的出片事实摊开。 */
function buildAuditRows(context: {
  specs: ScriptShotVideoSpec[];
  imageByRowKey: Map<string, CanvasNode>;
  existingByKey: Map<string, string>;
  continuitySourceByRowKey: Map<string, string>;
}, model?: ScriptVideoModelCapabilities | null, graph: CanvasGraphSlice = liveGraph()): ScriptShotAuditRow[] {
  return context.specs.map((spec) => {
    const shotImageNode = context.imageByRowKey.get(spec.rowKey);
    const frameUrl = shotImageNode ? firstFrameUrl(shotImageNode) : null;
    const camera = cameraDirectionText(spec.prompt);
    const promptDuration = motionPromptDurationSeconds(spec.prompt);
    const issues: ScriptShotAuditIssue[] = [];
    if (!spec.hasPrompt) issues.push('noPrompt');
    if (!frameUrl && spec.generationMode !== 'textToVideo') issues.push('noImage');
    if (!camera) issues.push('noCamera');
    if (cameraDirectionNeedsReview(camera)) issues.push('cameraNeedsReview');
    if (spec.generationMode === 'firstLastFrame' && !scriptShotKeyframes(graph.nodes.find(node => node.id === context.existingByKey.get(spec.rowKey)), graph, spec.rowFingerprint).ok) issues.push('missingLastFrame');
    if (spec.durationSec === null) issues.push('invalidDuration');
    if (scriptVideoModelIssue(spec, model, graph.nodes.find(node => node.id === context.existingByKey.get(spec.rowKey)), graph, context.continuitySourceByRowKey.get(spec.rowKey))) issues.push('modelUnsupported');
    if (
      promptDuration !== null &&
      spec.durationSec !== null &&
      Math.abs(promptDuration - spec.durationSec) > 0.5
    ) {
      issues.push('durationMismatch');
    }
    return {
      rowKey: spec.rowKey,
      shotNumber: spec.shotNumber,
      durationSec: spec.durationSec,
      generationDurationSec: scriptGenerationDuration(spec.durationSec, model),
      firstFrameUrl: frameUrl ?? '',
      camera,
      promptSource: spec.promptSource,
      dialogue: spec.dialogueLine,
      audioRoute: scriptShotAudioRoute(spec),
      issues,
    };
  });
}

export function planScriptShotVideos(
  scriptNodeId: string,
  graph: CanvasGraphSlice = liveGraph(),
  expectations: {
    modelCapabilities?: ScriptVideoModelCapabilities | null;
    model?: string | null;
    aspectRatio?: string | null;
    quality?: VideoGenQuality | null;
    continuityReference?: boolean;
  } = {},
): ScriptShotVideoPlan | { ok: false; reason: string } {
  const context = readShotVideoContext(scriptNodeId, graph, expectations);
  if (!context.ok) return { ok: false, reason: context.reason };
  const rearm = context.classification === 'rearm';
  const pending = rearm
    ? context.shootable.filter(({ spec, shotImageNode }) => {
        const nodeId = context.existingByKey.get(spec.rowKey);
        if (!nodeId) return true;
        const frameUrl = firstFrameUrl(shotImageNode) ?? '';
        if (!frameUrl && spec.generationMode !== 'textToVideo') return true;
        return shotVideoNeedsRender({
          nodeId,
          expectedPrompt: spec.prompt,
          expectedDurationSec: spec.durationSec ?? DEFAULT_DURATION_SEC,
          expectedGenerationDurationSec: scriptGenerationDuration(spec.durationSec, expectations.modelCapabilities),
          expectedFirstFrameUrl: frameUrl,
          expectedTailReferenceUrl: capturedTailUrl(context.continuitySourceByRowKey.get(spec.rowKey), graph),
          expectedDeliverySpec: context.deliverySpec,
          expectedShotContractFacts: buildShotContractFacts(
            spec,
            frameUrl,
            scriptGenerationPrompt(
              spec.prompt,
              spec.durationSec ?? DEFAULT_DURATION_SEC,
              scriptGenerationDuration(spec.durationSec, expectations.modelCapabilities) ?? (spec.durationSec ?? DEFAULT_DURATION_SEC),
            ),
          ),
          expectedRowFingerprint: spec.rowFingerprint,
          expectedAssetRevisionSnapshot: spec.assetRevisionSnapshot,
          expectedModel: context.model,
          expectedQuality: context.quality,
        }, graph);
      })
    : context.shootable;
  return {
    ok: true,
    mode: rearm ? 'regenerate' : 'create',
    shotCount: context.shootable.length,
    pendingCount: pending.length,
    willRebuild: context.classification === 'rebuild',
    skippedNoPrompt: context.skippedNoPrompt,
    skippedNoImage: context.skippedNoImage,
    derivedCount: context.existingIds.length,
    continuityCount: context.continuityCount,
    plannedSeconds: pending.reduce(
      (total, { spec }) => total + (scriptGenerationDuration(spec.durationSec, expectations.modelCapabilities) ?? spec.durationSec ?? DEFAULT_DURATION_SEC),
      0,
    ),
    fallbackPromptCount: context.fallbackPromptCount,
    rows: buildAuditRows(context, expectations.modelCapabilities, graph),
  };
}

/** 落一批新的镜头视频节点；create 与增量 rearm 共用同一套落位和对账。 */
function createShotVideoNodes(params: {
  modelCapabilities?: ScriptVideoModelCapabilities | null;
  scriptNodeId: string;
  targets: Array<{ spec: ScriptShotVideoSpec; shotImageNode?: CanvasNode }>;
  model: string;
  aspectKey: string;
  deliverySpec: VideoDeliverySpec;
  quality: VideoGenQuality | null | undefined;
  defaultDurationSec: number;
  generateVideos: boolean;
  /** 行标识 → 上一镜分镜图节点 id；没有的镜不接承接边。 */
  continuitySourceByRowKey: Map<string, string>;
}): Array<{ nodeId: string; rowKey: string }> {
  const {
    scriptNodeId,
    targets,
    model,
    aspectKey,
    deliverySpec,
    quality,
    defaultDurationSec,
    generateVideos,
    continuitySourceByRowKey,
  } = params;
  if (targets.length === 0) return [];

  const liveState = useCanvasStore.getState();
  const liveNodeMap = new Map(liveState.nodes.map((node) => [node.id, node] as const));
  const rectOf = (nodeId: string): StoryboardRect | null => {
    const node = liveNodeMap.get(nodeId);
    if (!node) return null;
    const absolute = resolveAbsolutePosition(node, liveNodeMap);
    return {
      x: absolute.x,
      y: absolute.y,
      width: node.measured?.width ?? SCRIPT_SHOT_VIDEO_CELL_WIDTH,
      height: node.measured?.height ?? SCRIPT_SHOT_VIDEO_CELL_HEIGHT,
    };
  };

  // 落位：贴在这批分镜图整块的右侧。已存在的视频节点也在 occupied 里，后补的不会压上去。
  const shotRects = targets
    .map(({ shotImageNode }) => shotImageNode ? rectOf(shotImageNode.id) : null)
    .filter((rect): rect is StoryboardRect => Boolean(rect));
  const anchor: StoryboardRect = shotRects.length > 0
    ? {
        x: Math.min(...shotRects.map((rect) => rect.x)),
        y: Math.min(...shotRects.map((rect) => rect.y)),
        width:
          Math.max(...shotRects.map((rect) => rect.x + rect.width)) -
          Math.min(...shotRects.map((rect) => rect.x)),
        height:
          Math.max(...shotRects.map((rect) => rect.y + rect.height)) -
          Math.min(...shotRects.map((rect) => rect.y)),
      }
    : rectOf(scriptNodeId) ?? { x: 0, y: 0, width: 800, height: 400 };

  const count = targets.length;
  const cols = storyboardGridCols(count);
  const gridRows = Math.ceil(count / cols);
  const gridWidth = cols * SCRIPT_SHOT_VIDEO_CELL_WIDTH + (cols - 1) * STORYBOARD_NODE_GAP_X;
  const gridHeight =
    gridRows * SCRIPT_SHOT_VIDEO_CELL_HEIGHT + (gridRows - 1) * STORYBOARD_NODE_GAP_Y;
  const occupied: StoryboardRect[] = liveState.nodes
    .map((node) => rectOf(node.id))
    .filter((rect): rect is StoryboardRect => Boolean(rect));
  const origin = findStoryboardBlockOrigin({
    script: anchor,
    gridWidth,
    gridHeight,
    occupied,
  });
  const positions = storyboardGridPositions({
    origin,
    count,
    cellWidth: SCRIPT_SHOT_VIDEO_CELL_WIDTH,
    cellHeight: SCRIPT_SHOT_VIDEO_CELL_HEIGHT,
    cols,
  });

  const created: Array<{ nodeId: string; rowKey: string }> = [];
  targets.forEach(({ spec, shotImageNode }, index) => {
    const position = positions[index] ?? origin;
    const newNodeId = useCanvasStore.getState().addNode(
      CANVAS_NODE_TYPES.video,
      position,
      shotVideoNodeData({
        scriptNodeId,
        shotImageNodeId: shotImageNode?.id ?? '',
        firstFrameUrl: firstFrameUrl(shotImageNode) ?? '',
        tailReferenceUrl: capturedTailUrl(continuitySourceByRowKey.get(spec.rowKey)),
        spec,
        model,
        aspectKey,
        deliverySpec,
        quality,
        defaultDurationSec,
        generateVideos,
        modelCapabilities: params.modelCapabilities,
      }),
    );
    if (!newNodeId) return;
    // 首帧边是这条链路的要害：i2v 提交只认上游节点的图。建不出来就不能算派生成功。
    if (shotImageNode && !ensureShotVideoEdge(shotImageNode.id, newNodeId)) {
      useCanvasStore.getState().deleteNodes([newNodeId]);
      return;
    }
    if (!generateVideos && spec.generationMode === 'firstLastFrame' && shotImageNode) {
      const video = useCanvasStore.getState().nodes.find(node => node.id === newNodeId);
      const rowIndex = buildScriptRowKeys(scriptRowsOf(scriptNodeId)).indexOf(spec.rowKey);
      if (video && rowIndex >= 0) prepareScriptEndFrameDraft(video, shotImageNode, scriptRowsOf(scriptNodeId)[rowIndex], spec.rowFingerprint);
    }
    const keyframeNodeIds = ensureShotKeyframeNodes({ scriptNodeId, spec, shotImageNode, generateImages: generateVideos });
    ensureShotKeyframeEdges(keyframeNodeIds, newNodeId);
    // 先首帧、再状态画面、后承接：`collectUpstreamImageUrls` 按边序取图，第一张必须是首帧。
    const continuitySourceId = continuitySourceByRowKey.get(spec.rowKey);
    if (continuitySourceId) ensureShotContinuityEdge(continuitySourceId, newNodeId);
    if (!ensureScriptVideoAssetReferences({ scriptNodeId, videoNodeId: newNodeId, firstFrameNodeId: shotImageNode?.id, keyframeNodeIds, references: spec.assetReferences, model: params.modelCapabilities })) {
      useCanvasStore.getState().deleteNodes([newNodeId]);
      return;
    }
    created.push({ nodeId: newNodeId, rowKey: spec.rowKey });
  });
  return created;
}

/**
 * 逐镜出视频。三态与图片那条路完全同构：
 * - `create`：还没派过 → 建节点 + 连首帧边；
 * - `rearm`：行集合没变 → 原地补边，只把未出片 / 失败 / 提示词改过的重新排队；
 * - `rebuild`：行集合变了 → 先删旧视频节点再重排（顺序不能反，否则旧节点会被当成
 *   障碍物把新一批顶下去，连着重来会一路漂）。
 */
export function scatterScriptShotVideos(
  params: ScatterScriptShotVideosParams,
): ScatterScriptShotVideosResult {
  if (useCanvasStore.getState().nodes.find(node => node.id === params.scriptNodeId)?.data?.scriptDirectorPlanNeedsSync === true) {
    return { ok: false, reason: DIRECTOR_PLAN_PENDING_REASON };
  }
  const context = readShotVideoContext(params.scriptNodeId, liveGraph(), {
    model: params.model,
    aspectRatio: params.aspectRatio,
    quality: params.quality,
    continuityReference: params.continuityReference === true,
  });
  if (!context.ok) return { ok: false, reason: context.reason };
  const {
    shootable,
    existingByKey,
    existingIds,
    skippedNoPrompt,
    skippedNoImage,
    classification,
    deliverySpec,
    continuitySourceByRowKey,
  } = context;

  const generateVideos = params.generateVideos === true;
  if (generateVideos) {
    const modelIssues = context.shootable.flatMap(({ spec }) => {
      const reason = scriptVideoModelIssue(spec, params.modelCapabilities, liveGraph().nodes.find(node => node.id === context.existingByKey.get(spec.rowKey)), liveGraph(), context.continuitySourceByRowKey.get(spec.rowKey));
      return reason ? [`第 ${spec.shotNumber} 镜：${reason}`] : [];
    });
    if (modelIssues.length > 0) return { ok: false, reason: modelIssues.join('；') };
    const missingDuration = context.shootable.filter(({ spec }) => spec.durationSec === null);
    if (missingDuration.length > 0) {
      return { ok: false, reason: `第 ${missingDuration.map(({ spec }) => spec.shotNumber).join('、')} 镜没有明确的正数秒时长，请先填写时长；可以先仅建草稿节点` };
    }
    const needsTail = context.shootable.filter(({ spec }) => spec.generationMode === 'firstLastFrame'
      && (classification !== 'rearm' || !scriptShotKeyframes(liveGraph().nodes.find(node => node.id === existingByKey.get(spec.rowKey)), liveGraph(), spec.rowFingerprint).ok));
    if (needsTail.length > 0) {
      return { ok: false, reason: needsTail.map(({ spec }) => {
        const frames = scriptShotKeyframes(liveGraph().nodes.find(node => node.id === existingByKey.get(spec.rowKey)), liveGraph(), spec.rowFingerprint);
        return `第 ${spec.shotNumber} 镜：${frames.ok ? '行集合已变化，请先仅建节点，再补齐本镜首尾帧' : frames.reason}`;
      }).join('；') };
    }
    if (context.shootable.some(({ spec }) => spec.generationMode === 'firstLastFrame') && !params.modelCapabilities?.supportedModes?.includes('firstLastFrame')) {
      return { ok: false, reason: '所选模型未声明支持首尾帧，请换模型' };
    }
  }
  // 节点提交用的模型绑定；只建节点时允许为空（视频节点自己会退回上次选的模型）。
  const model = (params.model ?? '').trim();
  const aspectKey = deliverySpec.aspectRatio;
  const quality = params.quality;
  const defaultDurationSec = params.defaultDurationSec ?? DEFAULT_DURATION_SEC;

  // ===== ① 已经派过且对得上：补边 + 原地重跑未落地的 =====
  if (classification === 'rearm') {
    const pending: Array<{
      nodeId: string;
      spec: ScriptShotVideoSpec;
      shotImageNode?: CanvasNode;
    }> = [];
    const missing: Array<{ spec: ScriptShotVideoSpec; shotImageNode?: CanvasNode }> = [];
    let referenceWiringFailed = false;
    shootable.forEach(({ spec, shotImageNode }) => {
      const nodeId = existingByKey.get(spec.rowKey);
      if (!nodeId) {
        // 上一次点的时候这张分镜图还没出好：保留已有节点，只把新补出来的镜追加进来。
        missing.push({ spec, shotImageNode });
        return;
      }
      // 首帧来源可能换过（分镜图重建过）：身份字段跟着更新，边补到新的那张上。
      const live = useCanvasStore.getState().nodes.find((node) => node.id === nodeId);
      if (shotImageNode && live?.data?.[SCRIPT_SHOT_VIDEO_IMAGE_FIELD] !== shotImageNode.id) {
        useCanvasStore.getState().updateNodeData(nodeId, {
          [SCRIPT_SHOT_VIDEO_IMAGE_FIELD]: shotImageNode.id,
        });
      }
      if (shotImageNode) ensureShotVideoEdge(shotImageNode.id, nodeId);
      const keyframeNodeIds = ensureShotKeyframeNodes({ scriptNodeId: params.scriptNodeId, spec, shotImageNode, generateImages: generateVideos });
      ensureShotKeyframeEdges(keyframeNodeIds, nodeId);
      // 老节点（本次改动之前派的）也会在这条路上把承接边补上：只补边、不重跑。
      // 承接边的源变化**不**触发重跑（见 `shotVideoNeedsRender` 的说明）。
      const continuitySourceId = continuitySourceByRowKey.get(spec.rowKey);
      if (continuitySourceId && spec.generationMode !== 'firstLastFrame') ensureShotContinuityEdge(continuitySourceId, nodeId);
      else removeShotContinuityEdges(nodeId);
      if (!ensureScriptVideoAssetReferences({ scriptNodeId: params.scriptNodeId, videoNodeId: nodeId, firstFrameNodeId: shotImageNode?.id, keyframeNodeIds, references: spec.assetReferences, model: params.modelCapabilities })) referenceWiringFailed = true;
      if (!generateVideos && spec.generationMode === 'firstLastFrame' && live && shotImageNode) {
        const rowIndex = buildScriptRowKeys(scriptRowsOf(params.scriptNodeId)).indexOf(spec.rowKey);
        if (rowIndex >= 0) prepareScriptEndFrameDraft(live, shotImageNode, scriptRowsOf(params.scriptNodeId)[rowIndex], spec.rowFingerprint);
      }
      const frameUrl = firstFrameUrl(shotImageNode) ?? '';
      if (!frameUrl && spec.generationMode !== 'textToVideo') return;
      if (
        shotVideoNeedsRender({
          nodeId,
          expectedPrompt: spec.prompt,
          expectedDurationSec: spec.durationSec ?? defaultDurationSec,
          expectedGenerationDurationSec: scriptGenerationDuration(spec.durationSec, params.modelCapabilities),
          expectedFirstFrameUrl: frameUrl,
          expectedTailReferenceUrl: capturedTailUrl(continuitySourceByRowKey.get(spec.rowKey)),
          expectedDeliverySpec: deliverySpec,
          expectedShotContractFacts: buildShotContractFacts(
            spec,
            frameUrl,
            scriptGenerationPrompt(
              spec.prompt,
              spec.durationSec ?? defaultDurationSec,
              scriptGenerationDuration(spec.durationSec, params.modelCapabilities) ?? (spec.durationSec ?? defaultDurationSec),
            ),
          ),
          expectedRowFingerprint: spec.rowFingerprint,
          expectedAssetRevisionSnapshot: spec.assetRevisionSnapshot,
          expectedModel: model,
          expectedQuality: quality,
        })
      ) {
        pending.push({ nodeId, spec, shotImageNode });
      }
    });
    if (referenceWiringFailed) return { ok: false, reason: '视频资产参考连接失败，请检查节点和连线后重试；未启动生成' };
    if (generateVideos && pending.length > 0) {
      pending.forEach(({ nodeId, spec, shotImageNode }) => {
        const frameUrl = firstFrameUrl(shotImageNode) ?? '';
        const editSeconds = spec.durationSec ?? defaultDurationSec;
        const generationSeconds = scriptGenerationDuration(editSeconds, params.modelCapabilities) ?? editSeconds;
        const executionPrompt = scriptGenerationPrompt(spec.prompt, editSeconds, generationSeconds);
        const frames = spec.generationMode === 'firstLastFrame'
          ? scriptShotKeyframes(useCanvasStore.getState().nodes.find(node => node.id === nodeId), liveGraph(), spec.rowFingerprint) : null;
        // 用户手动拨过音频开关的节点不再被脚本口径覆盖（`generateAudioUserSet` 是
        // UI 开关自己打的标记）。没拨过的才按行事实重新推一遍。
        const audioParameters =
          useCanvasStore.getState().nodes.find((node) => node.id === nodeId)?.data
            ?.generateAudioUserSet === true
            ? {}
            : scriptShotAudioParameters(spec);
        useCanvasStore.getState().updateNodeData(nodeId, {
          ...(model ? { model } : {}),
          ...(quality ? { quality } : {}),
          aspectRatio: deliverySpec.aspectRatio,
          deliverySpec,
          shotContractFacts: buildShotContractFacts(spec, frameUrl, executionPrompt),
          shotContract: null,
          shot_contract: null,
          shotId: spec.rowKey,
          action: spec.action,
          ...audioParameters,
          continuityIn: spec.continuityIn,
          continuityOut: spec.continuityOut,
          transition: spec.transition,
          transitionPlan: spec.transition,
          generationModeRecommendation: spec.generationMode,
          ...(spec.generationMode === 'textToVideo' ? { genMode: 'textToVideo', [SCRIPT_SHOT_VIDEO_IMAGE_FIELD]: '' }
            : useCanvasStore.getState().nodes.find(node => node.id === nodeId)?.data.genMode === 'textToVideo' ? { genMode: 'imageToVideo' } : {}),
          referenceBindings: spec.referenceBindings,
          durationSec: generationSeconds,
          [SCRIPT_SHOT_VIDEO_GENERATION_DURATION_FIELD]: generationSeconds,
          prompt: executionPrompt,
          [SCRIPT_SHOT_VIDEO_PROMPT_FIELD]: spec.prompt,
          [SCRIPT_SHOT_VIDEO_DURATION_FIELD]:
            spec.durationSec ?? defaultDurationSec,
          [SCRIPT_SHOT_VIDEO_FIRST_FRAME_FIELD]:
            frameUrl,
          ...(frames?.ok ? { scriptShotLastFrameUrl: frames.lastFrameUrl } : {}),
          scriptShotTailReferenceUrl: capturedTailUrl(continuitySourceByRowKey.get(spec.rowKey)),
          [SCRIPT_SHOT_VIDEO_ROW_FINGERPRINT_FIELD]: spec.rowFingerprint,
          [SCRIPT_SHOT_VIDEO_ASSET_SNAPSHOT_FIELD]: spec.assetRevisionSnapshot,
          canvas_auto_generate_once: true,
          generationError: null,
        });
        const keyframeNodeIds = ensureShotKeyframeNodes({ scriptNodeId: params.scriptNodeId, spec, shotImageNode, generateImages: generateVideos });
        ensureShotKeyframeEdges(keyframeNodeIds, nodeId);
        ensureScriptVideoAssetReferences({ scriptNodeId: params.scriptNodeId, videoNodeId: nodeId, firstFrameNodeId: shotImageNode?.id, keyframeNodeIds, references: spec.assetReferences, model: params.modelCapabilities });
      });
    }
    const addedIds = createShotVideoNodes({
      modelCapabilities: params.modelCapabilities,
      scriptNodeId: params.scriptNodeId,
      targets: missing,
      model,
      aspectKey,
      deliverySpec,
      quality,
      defaultDurationSec,
      generateVideos,
      continuitySourceByRowKey,
    });
    addedIds.forEach(({ nodeId, rowKey }) => existingByKey.set(rowKey, nodeId));
    const nodeIds = shootable
      .map(({ spec }) => existingByKey.get(spec.rowKey))
      .filter((id): id is string => Boolean(id));
    return {
      ok: true,
      mode: 'rearmed',
      nodeIds,
      addedCount: addedIds.length,
      armed: generateVideos ? pending.length + addedIds.length : 0,
      skippedNoImage,
      skippedNoPrompt,
    };
  }

  // ===== ② 行集合变了（脚本重生成过 / 分镜图重建过）：先清掉前一批，再重排 =====
  if (classification === 'rebuild' && existingIds.length > 0) {
    useCanvasStore.getState().deleteNodes(existingIds);
  }

  const created = createShotVideoNodes({
    modelCapabilities: params.modelCapabilities,
    scriptNodeId: params.scriptNodeId,
    targets: shootable,
    model,
    aspectKey,
    deliverySpec,
    quality,
    defaultDurationSec,
    generateVideos,
    continuitySourceByRowKey,
  });
  const nodeIds = created.map(({ nodeId }) => nodeId);
  if (nodeIds.length === 0) {
    return { ok: false, reason: '视频节点创建失败（分镜图与视频节点之间连不上线）' };
  }
  useCanvasStore.getState().updateNodeData(params.scriptNodeId, {
    videoGenConfig: {
      model: model || undefined,
      aspectRatio: deliverySpec.aspectRatio,
      deliverySpec,
    },
  });

  return {
    ok: true,
    mode: classification === 'rebuild' ? 'rebuilt' : 'created',
    nodeIds,
    addedCount: nodeIds.length,
    armed: generateVideos ? nodeIds.length : 0,
    skippedNoImage,
    skippedNoPrompt,
  };
}

/**
 * 脚本节点派生的视频节点里**已经有片子**的那些，按行序（＝片子顺序）。
 * 合成台靠它初始化时间线 —— 顺序错了成片就散了。
 */
export function scriptShotVideoNodesInRowOrder(
  scriptNodeId: string,
  graph: CanvasGraphSlice = liveGraph(),
): CanvasNode[] {
  const owned = new Set(derivedShotVideoNodeIds(scriptNodeId, graph));
  const rank = new Map(
    buildScriptRowKeys(scriptRowsOf(scriptNodeId, graph)).map((key, index) => [key, index]),
  );
  return graph.nodes
    .filter((node) => owned.has(node.id) && isVideoNode(node))
    .sort((a, b) => {
      const ra = rank.get(nodeRowKey(a) ?? '') ?? Number.MAX_SAFE_INTEGER;
      const rb = rank.get(nodeRowKey(b) ?? '') ?? Number.MAX_SAFE_INTEGER;
      if (ra !== rb) return ra - rb;
      return (
        (a.position?.y ?? 0) - (b.position?.y ?? 0) ||
        (a.position?.x ?? 0) - (b.position?.x ?? 0)
      );
    });
}

/**
 * 逐镜出视频这条链在画布上**真正读到的节点**（React 侧订阅用）。
 *
 * 返回脚本、资产、分镜、派生视频及其直接上游和截图尾帧。调用方用
 * `useShallow` 包住这个数组即可：React Flow 会为没变的节点复用对象身份
 * （见 `useUpstreamGraph` 的说明），所以拖动一个**无关**节点不会让这个数组
 * 逐项比较失败，脚本节点那张大表格也就不会跟着重渲染；而这个链条上任何一个
 * 节点自身的变化（分镜图出图落定、视频出片落定 / 失败 / 提示词快照被改、
 * 关联组换过）一定会让它的对象身份变化。
 */
export function scriptShotVideoGraphNodes(
  scriptNodeId: string,
  nodes: readonly CanvasNode[],
  edges: readonly CanvasEdge[] = [],
): CanvasNode[] {
  const scriptNode = nodes.find((node) => node.id === scriptNodeId);
  if (!scriptNode) return [];
  const graph: CanvasGraphSlice = { nodes: [...nodes], edges: [...edges] };
  const imageIds = new Set(
    [...scriptStoryboardImagesByRowKey(scriptNodeId, graph).values()].map((node) => node.id),
  );
  const videoIds = new Set(nodes.filter(node =>
    node.data?.[SCRIPT_SHOT_VIDEO_SOURCE_FIELD] === scriptNodeId && isVideoNode(node),
  ).map(node => node.id));
  const upstreamIds = new Set(edges.filter(edge => videoIds.has(edge.target)).map(edge => edge.source));
  const collected: CanvasNode[] = [scriptNode];
  nodes.forEach((node) => {
    const capture = node.data?.captureMetadata as Record<string, unknown> | undefined;
    const capturedTail = capture?.source_kind === 'video_frame_capture'
      && capture.capture_mode === 'last' && videoIds.has(String(capture.source_node_id));
    const ownsAsset = Boolean(node.data?.scriptAssetId) && node.data?.scriptAssetOwnerId === scriptNodeId;
    if (node.id !== scriptNodeId && (imageIds.has(node.id) || videoIds.has(node.id)
      || ownsAsset || upstreamIds.has(node.id) || capturedTail)) collected.push(node);
  });
  return collected;
}

/**
 * 上面那批节点之间的边（首帧边、脚本 → 分镜图、镜头视频的成片边）。
 *
 * 收集至少一端在这批节点中的边，以便检测连接的增加、删除或替换。
 */
export function scriptShotVideoChainEdges(
  chainNodes: readonly CanvasNode[],
  edges: readonly CanvasEdge[],
): CanvasEdge[] {
  const ids = new Set(chainNodes.map((node) => node.id));
  return edges.filter((edge) => ids.has(edge.source) || ids.has(edge.target));
}

/**
 * `useShallow` 用的**扁平**选择结果：节点与边混在一个数组里，逐项比引用。
 *
 * 为什么不直接选 `{ nodes, edges }` 对象：`useShallow` 只浅比一层，那两层的数组每次
 * 都是新建的，浅比必然失败，等于没做优化。扁平数组的每一项都是 React Flow 复用的
 * 同一个对象，才是真正稳定的比较单位。
 */
export type ScriptShotVideoChainItem = CanvasNode | CanvasEdge;

export function scriptShotVideoChain(
  scriptNodeId: string,
  nodes: readonly CanvasNode[],
  edges: readonly CanvasEdge[],
): ScriptShotVideoChainItem[] {
  const chainNodes = scriptShotVideoGraphNodes(scriptNodeId, nodes, edges);
  return [...chainNodes, ...scriptShotVideoChainEdges(chainNodes, edges)];
}

/** 把扁平选择结果还原成节点 / 边两半，供计划账使用。 */
export function graphSliceOf(items: readonly ScriptShotVideoChainItem[]): CanvasGraphSlice {
  const graphNodes: CanvasNode[] = [];
  const graphEdges: CanvasEdge[] = [];
  items.forEach((item) => {
    if ('position' in item) graphNodes.push(item as CanvasNode);
    else graphEdges.push(item as CanvasEdge);
  });
  return { nodes: graphNodes, edges: graphEdges };
}
import { DIRECTOR_PLAN_PENDING_REASON } from './directorSequenceCoverage';
