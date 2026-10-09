// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useCallback, useEffect, type Dispatch, type MutableRefObject, type SetStateAction } from "react";
import type { TFunction } from "i18next";

import type {
  CanvasNodeData,
  CanvasNode,
  Seedance2SceneOptimize,
  VideoGenCount,
  VideoGenMode,
  VideoGenQuality,
  VideoNodeData,
} from "@/features/canvas/domain/canvasNodes";
import { isAudioNode } from "@/features/canvas/domain/canvasNodes";
import type { FreezoneJobRef, FreezoneVideoReferenceItem } from "@/api/ops";
import {
  submitFreezoneVideoEdit,
  submitFreezoneVideoGen,
  submitFreezoneVideoI2v,
  submitFreezoneVideoKeyframes,
  submitFreezoneVideoOmniGen,
  uploadFreezoneImage,
} from "@/api/ops";
import { awaitFreezoneJobMediaResult } from "@/features/canvas/application/awaitFreezoneJobMediaResult";
import { videoGenerationSourcePatch } from '@/features/canvas/application/videoGenerationSource';
import type { FreezoneVideoGenerationSource } from '@/api/freezoneGenerationHistory';
import {
  cancelSubmittedTaskIfAborted,
  registerNodeGenerationTask,
} from "@/features/canvas/application/useCancelNodeGeneration";
import {
  buildGeneratedRightsPatch,
} from "@/features/canvas/domain/nodeRights";
import {
  buildGenerationTerminalPatch,
  isTaskCancelledError,
} from "@/features/canvas/application/generationTaskArbitration";
import {
  audioReferenceDurationRejection,
  formatAudioDurationClips,
  MAX_AUDIO_REFERENCE_DURATION_MS,
  MIN_AUDIO_REFERENCE_DURATION_MS,
  videoSubmitMediaRejectionReason,
  type VideoModelFamily,
} from "@/features/canvas/domain/videoCapabilityCompiler";
import {
  renderVideoReferenceRoleLegend,
  videoReferenceRoleLabel,
  inferVideoReferenceRole,
  referenceRoleFromVideoEdge,
  type VideoReferenceRoleEntry,
  type VideoReferenceRole,
} from "@/features/canvas/domain/videoReferenceRoles";
import { referenceVideoUrl } from "@/features/canvas/hooks/useVideoReferences";
import type { ReferenceMediaItem } from "@/features/canvas/hooks/useVideoReferences";
import { useCanvasStore } from "@/stores/canvasStore";
import { setAlbumPendingTotal } from "@/features/canvas/nodes/shared/albumPendingTotals";
import { sortUpstreamByReferenceOrder, upstreamNodesInEdgeOrder } from "@/features/canvas/nodes/referenceOrdering";
import { submittableImageUrl } from "./videoNodeModelRules";
import {
  captureVideoFrameBlob,
  clampVideoDuration,
  probeAudioDurationMs,
  qualityToResolution,
} from "./videoNodeModelRules";
import {
  GENERATION_CONCURRENCY_DEFAULT,
  clampGenerationBatchCount,
  clearGenerationIntent,
  markGenerationIntent,
  runGenerationQueue,
  withGlobalGenerationSlot,
} from "@/features/canvas/application/generationConcurrency";
import {
  UPSTREAM_GATE_RETRY_MS,
  upstreamGenerationGate,
} from "@/features/canvas/application/generationDependencies";
import { toast } from "sonner";
import { readUrl } from "@/lib/url-params";
import { backendErrorToastMessage } from "@/lib/api-errors";
import { resolveErrorContent, showErrorDialog } from "@/features/canvas/application/errorDialog";
import { resolveGenerationErrorDiagnostics } from "@/features/canvas/application/generationErrorReport";
import { scriptShotKeyframes, scriptShotKeyframeReadiness } from './script/scriptShotKeyframes';
import { compileScriptVideoReferences, scriptVideoReferenceFacts } from './script/scriptShotVideoReferences';

type UpdateNodeData = (nodeId: string, data: Partial<CanvasNodeData>) => void;

export interface VideoGenerationSubmissionContext {
  id: string;
  data: VideoNodeData;
  t: TFunction;
  updateNodeData: UpdateNodeData;
  submittingRef: MutableRefObject<boolean>;
  generationQueueAbortRef: MutableRefObject<AbortController | null>;
  setAutoSubmitRetryTick: Dispatch<SetStateAction<number>>;
  autoSubmitRetryTick: number;
  isGenerating: boolean;
  submitDisabled: boolean;
  capabilityVideoModel: import("@/features/canvas/ui/ProviderModelPicker").ModelOption | null | undefined;
  selectedVideoModel: import("@/features/canvas/ui/ProviderModelPicker").ModelOption | null | undefined;
  videoChannelEnabled: boolean;
  videoChannelDisabledReason: string | null | undefined;
  quality: VideoGenQuality;
  effectiveAspectRatio: string;
  durationParameterEnabled: boolean;
  durationSec: number;
  durationBounds: { min: number; max: number };
  durationOptions: readonly number[] | undefined;
  generateAudio: boolean;
  prompt: string;
  upstreamTextJoined: string;
  genMode: VideoGenMode;
  referenceMedia: readonly ReferenceMediaItem[];
  isDirectVideoModel: boolean;
  modelId: string;
  selectedVideoModelId: string;
  isSeedance20Model: boolean;
  sceneOptimize: Seedance2SceneOptimize | undefined;
  isHappyHorseModel: boolean;
  isKacangKlingV2vModel: boolean;
  autoTailFrameReference: boolean;
  videoModelFamily: VideoModelFamily;
  count: VideoGenCount;
  refreshHistory: () => Promise<unknown> | unknown;
}

export function useVideoGenerationSubmission(
  context: VideoGenerationSubmissionContext,
): () => Promise<void> {
  const {
    id,
    data,
    t,
    updateNodeData,
    submittingRef,
    generationQueueAbortRef,
    setAutoSubmitRetryTick,
    autoSubmitRetryTick,
    isGenerating,
    submitDisabled,
    capabilityVideoModel,
    selectedVideoModel,
    videoChannelEnabled,
    videoChannelDisabledReason,
    quality,
    effectiveAspectRatio,
    durationParameterEnabled,
    durationSec,
    durationBounds,
    durationOptions,
    generateAudio,
    prompt,
    upstreamTextJoined,
    genMode,
    referenceMedia,
    isDirectVideoModel,
    modelId,
    selectedVideoModelId,
    isSeedance20Model,
    sceneOptimize,
    isHappyHorseModel,
    isKacangKlingV2vModel,
    autoTailFrameReference,
    videoModelFamily,
    count,
    refreshHistory,
  } = context;

  const handleSubmit = useCallback(async () => {
      if (submitDisabled) return;
      // 在途守卫（与 ImageGenNode 一致）：第 1 条完成就会清 isGenerating，
      // submitDisabled 拦不住「旧批次 N-1 个任务还在跑时重新提交」——旧闭包
      // 会用过期的 completedUrls 覆写新批次的 generationBatch。
      if (submittingRef.current) return;
      // Channel offline is already folded into submitDisabled (selectedModelOffline).
      // Keep a defensive toast path only if the model flag flips mid-click without
      // re-render; use Boolean() so TS does not treat the earlier guard as exhaustive.
      const offlineNow = Boolean(
        selectedVideoModel?.disabled ||
          selectedVideoModel?.enabled === false ||
          !videoChannelEnabled,
      );
      if (offlineNow) {
        updateNodeData(id, {
          generationError:
            selectedVideoModel?.disabledReason?.trim() ||
            videoChannelDisabledReason?.trim() ||
            "视频渠道未接通",
          generationErrorDetails: null,
          generationErrorRequestId: null,
        });
        return;
      }
      const scriptDuration = data.scriptShotGenerationDurationSec ?? data.scriptShotRowDurationSec;
      if (typeof data.scriptShotSourceNodeId === "string" &&
          typeof scriptDuration === "number" && Number.isFinite(scriptDuration)) {
        const actualDuration = clampVideoDuration(durationSec, durationBounds, durationOptions);
        if (!durationParameterEnabled || Math.abs(scriptDuration - actualDuration) > 0.001) {
          updateNodeData(id, {
            generationError: durationParameterEnabled
              ? `本镜计划生成 ${scriptDuration}s，当前模型设置会提交 ${actualDuration}s。请回脚本节点按当前模型重新派生素材，或选择支持该生成时长的模型`
              : `本镜计划生成 ${scriptDuration}s，当前模型不能指定生成时长。请选择支持指定时长的模型`,
            generationErrorCode: "SCRIPT_VIDEO_DURATION_MISMATCH",
            generationErrorStage: "preflight",
            generationErrorRetryable: false,
          });
          return;
        }
      }
      const scriptFrames = genMode === 'firstLastFrame' && typeof data.scriptShotSourceNodeId === 'string'
        ? scriptShotKeyframes(useCanvasStore.getState().nodes.find(node => node.id === id), useCanvasStore.getState()) : null;
      if (scriptFrames && !scriptFrames.ok) {
        updateNodeData(id, { generationError: scriptFrames.reason, generationErrorCode: 'SCRIPT_KEYFRAMES_INVALID', generationErrorStage: 'preflight', generationErrorRetryable: false });
        return;
      }
      if (typeof data.scriptShotSourceNodeId === 'string') {
        const keyframeReadiness = scriptShotKeyframeReadiness(
          useCanvasStore.getState().nodes.find(node => node.id === id),
          useCanvasStore.getState(),
        );
        if (!keyframeReadiness.ok) {
          if (keyframeReadiness.pending && data.canvas_auto_generate_once === true) {
            setTimeout(() => setAutoSubmitRetryTick((tick) => tick + 1), UPSTREAM_GATE_RETRY_MS);
            return;
          }
          updateNodeData(id, {
            generationError: keyframeReadiness.reason,
            generationErrorCode: 'SCRIPT_SHOT_KEYFRAMES_PENDING',
            generationErrorStage: 'preflight',
            generationErrorRetryable: keyframeReadiness.pending,
          });
          return;
        }
      }
      if (genMode === 'textToVideo' && typeof data.scriptShotSourceNodeId === 'string') {
        const state = useCanvasStore.getState();
        if (upstreamNodesInEdgeOrder(state.nodes, state.edges, id).some(node => submittableImageUrl(node))) {
          updateNodeData(id, { generationError: '文生视频节点仍连接图片，请选择参考图生成方式或明确移除图片连线', generationErrorCode: 'SCRIPT_TEXT_VIDEO_HAS_IMAGES', generationErrorStage: 'preflight', generationErrorRetryable: false });
          return;
        }
      }
      submittingRef.current = true;
      // 从这一刻起本节点就算「在出图」：下游节点靠它按顺序排在后面（准备期也算）。
      markGenerationIntent(id);
      const abortController = new AbortController();
      generationQueueAbortRef.current?.abort();
      generationQueueAbortRef.current = abortController;
      try {
      const projectId = readUrl().project;
      if (!projectId) {
        console.error("[video-node] no project in URL");
        return;
      }
      updateNodeData(id, {
        isGenerating: true,
        generationStartedAt: Date.now(),
        // Clear any prior failure so the banner reflects only this attempt.
        // 注意 generationBatch 不在这里清：下面还有多条校验失败的早退路径，
        // 在这里清会让一次失败的提交白白毁掉已有画册——批次清空挪到真正开跑前。
        generationError: null,
        generationErrorDetails: null,
        generationErrorRequestId: null,
        generationErrorStage: null,
        generationErrorSuggestedAction: null,
        generationErrorCode: null,
        generationErrorRetryable: null,
        generationRecoveryJobId: null,
        generationRecoveryTaskType: null,
        generationTaskKey: null,
        generationTaskType: null,
        generationTaskJobId: null,
        generationTaskRefs: null,
        // Freeze request tier for honesty badge (requested vs actual pixels).
        lastRequestedResolution: qualityToResolution(quality),
        lastRequestedAspectRatio: effectiveAspectRatio,
        lastRequestedDurationSeconds: durationParameterEnabled ? durationSec : null,
        lastRequestedGenerateAudio: generateAudio,
      });
      // 上游 text 在前、用户自己写的 prompt 在后，两段以 \n\n 隔开
      // （与 ImageGenNode/ImageEditNode 一致）。运镜不再在这一步拼接：它已经在
      // prompt 的运镜段里（点卡片就是改那一段），再拼一次就是重复下发同一句话。
      const submissionGraph = useCanvasStore.getState();
      const submissionVideo = submissionGraph.nodes.find(node => node.id === id);
      const submittedImages: Array<{ node: CanvasNode; role?: VideoReferenceRole }> = [];
      const submittedRoles: VideoReferenceRoleEntry[] = [];
      const recordReference = (node: CanvasNode, kind: VideoReferenceRoleEntry['kind'], role?: VideoReferenceRole) => {
        const edge = submissionGraph.edges.find(item => item.source === node.id && item.target === id);
        if (kind === 'image') submittedImages.push({ node, role });
        submittedRoles.push({ kind, role: role ?? referenceRoleFromVideoEdge(edge?.data?.role, edge?.data?.label) ?? inferVideoReferenceRole(node.data), displayName: node.data.displayName });
      };
      const submissionPrompt = () => {
        const compiled = compileScriptVideoReferences(prompt.trim(), id, submissionGraph, submittedImages);
        const shotContractFacts = scriptVideoReferenceFacts(submissionVideo?.data.shotContractFacts as VideoNodeData['shotContractFacts'], compiled.references);
        if (shotContractFacts) updateNodeData(id, { shotContractFacts });
        const composed = [upstreamTextJoined, compiled.prompt].filter(Boolean).join("\n\n");
        const effective = composed.trim() || (genMode === 'allReference'
          ? "Keep the connected reference media as the main subject. Create a smooth cinematic motion shot with stable composition, natural lighting, clean details, and no unwanted style drift." : composed);
        return `${effective}${renderVideoReferenceRoleLegend(submittedRoles, genMode)}`.trim();
      };
      try {
        // Walk the current edges/nodes once — used by every non-textToVideo
        // branch to collect upstream resources. 必须与 UI 编号侧（useUpstreamNodes）
        // 同源：按连线顺序收集。曾按 state.nodes 顺序（节点创建顺序）收集，先创建
        // 但后连线的节点会排到 references 前面，@图片N 在后端就指向错位的图。
        const collectUpstream = () => {
          return sortUpstreamByReferenceOrder(
            upstreamNodesInEdgeOrder(submissionGraph.nodes, submissionGraph.edges, id),
            submissionVideo?.data.referenceOrder as VideoNodeData['referenceOrder'] ?? data.referenceOrder,
          );
        };
        const collectUpstreamImageUrls = (): string[] => {
          const upstream = collectUpstream();
          const urls: string[] = [];
          for (const node of upstream) {
            const url = submittableImageUrl(node);
            if (typeof url === "string" && url.length > 0) {
              urls.push(url);
              recordReference(node, 'image');
            }
          }
          return urls;
        };
        // A direct-model picker id is the saved endpoint identity.  Sending
        // only its upstream `apiModel` here would route the task back through
        // NewAPI and defeat the user's selected direct connection.
        const effectiveVideoModelId = isDirectVideoModel
          ? modelId
          : selectedVideoModelId;
        const effectiveModelId = modelId;

        const durationClamped = clampVideoDuration(durationSec, durationBounds, durationOptions);
        const durationPayload = durationParameterEnabled
          ? { durationSeconds: durationClamped }
          : {};
        // 画布路径不再下发 `camera_template_id`：运镜经提示词那一段表达，
        // 23 条目录只是快捷方式，不再是提交约束（`domain/promptCamera.ts`）。
        // 后端按 canvas_id + node_id 记录每个节点的生成历史。多条生成时每个
        // 兄弟节点用各自的 targetId 作 node_id，历史才能分别落到对应节点。
        const canvasId = readUrl().canvas ?? "default";
        // 台词交给模型自己的原生语音说：后端把台词装进 `对白：「…」口型同步` 槽位，
        // 画面描述里只留「说话表演」。这里以前会先调 TTS 合成一段参考音频喂给模型，
        // 并强制关掉模型自带人声——那既和模型的原生对白音频打架（同一句说两遍），
        // 也让「视频模型本来就能出声」白费。2026-09-15 按对标结论撤回：TapCanvas 的
        // v71 合同同样冻结供应商原生对白音频、`referenceAudioRequired:false`，并明文
        // 禁止在同一图里保留原生音轨 / 参考音轨的双轨分支。
        const submitGenMode: VideoGenMode = genMode;
        const dialoguePayload = {
          // An untouched switch is absence of a user decision. Sending
          // `false` here is an explicit mute instruction to required-audio
          // providers, so the backend strips the returned native track.
          generateAudioExplicit:
            data.generateAudioUserSet === true ? true : undefined,
          dialogueText: typeof data.dialogueText === "string" ? data.dialogueText : "",
          spokenDialogue: Array.isArray(data.spokenDialogue) ? data.spokenDialogue : [],
          audioType: data.audioType,
          speaker: typeof data.speaker === "string" ? data.speaker : "",
          nativeAudioStrategy: "native",
          audioAssetRef: data.audioAssetRef ?? null,
        } as const;

        // 后端不再支持一次出多条，改为按「生成数量」并发调用 N 次接口。先按
        // genMode 组装出一个「调一次接口」的闭包 doSubmit，校验失败则置空提前返回。
        let doSubmit: ((targetId: string) => Promise<FreezoneJobRef>) | null = null;
        if (submitGenMode === "firstLastFrame") {
          const imageUrls = collectUpstreamImageUrls();
          const firstFrameUrl = scriptFrames?.ok ? scriptFrames.firstFrameUrl : imageUrls[0] ?? null;
          const lastFrameUrl = scriptFrames?.ok ? scriptFrames.lastFrameUrl : imageUrls[1] ?? null;
          if (!firstFrameUrl && !lastFrameUrl) {
            console.warn(
              "[video-node] firstLastFrame submit without any frame",
            );
            updateNodeData(id, {
              isGenerating: false,
              generationStartedAt: null,
            });
            return;
          }
          submittedImages.length = 0;
          submittedRoles.length = 0;
          for (const [url, role] of [[firstFrameUrl, 'first_frame'], [lastFrameUrl, 'last_frame']] as const) {
            const frame = collectUpstream().find(node => submittableImageUrl(node) === url);
            if (frame) recordReference(frame, 'image', role);
          }
          const effectivePromptWithReferenceRoles = submissionPrompt();
          doSubmit = (targetId) =>
            submitFreezoneVideoKeyframes(projectId, {
              firstFrameUrl,
              lastFrameUrl,
              prompt: effectivePromptWithReferenceRoles,
              aspectRatio: effectiveAspectRatio,
              resolution: qualityToResolution(quality),
              ...durationPayload,
              generateAudio,
              ...dialoguePayload,
              model: effectiveVideoModelId,
              modelId: effectiveModelId,
              genMode: submitGenMode,
              humanReview: isSeedance20Model,
              sceneOptimize: sceneOptimize ?? null,
              canvasId,
              nodeId: targetId,
            });
        } else if (submitGenMode === "imageToVideo" || submitGenMode === "imageReference") {
          // Unified i2v endpoint: 1 image = 图生视频, 2-9 images = 图片参考视频.
          const imageUrls = collectUpstreamImageUrls();
          const limit = capabilityVideoModel?.referenceLimits?.[submitGenMode]?.image ?? (submitGenMode === 'imageReference' ? 9 : 1);
          if (imageUrls.length > limit) {
            void showErrorDialog(`当前模式最多支持 ${limit} 张图片，本镜连接了 ${imageUrls.length} 张；请使用支持全部参考图的模型和模式。`, t('common.error'));
            updateNodeData(id, { isGenerating: false, generationStartedAt: null });
            return;
          }
          if (imageUrls.length === 0) {
            console.warn("[video-node] i2v submit without any upstream image");
            updateNodeData(id, {
              isGenerating: false,
              generationStartedAt: null,
            });
            return;
          }
          const effectivePromptWithReferenceRoles = submissionPrompt();
          doSubmit = (targetId) =>
            submitFreezoneVideoI2v(projectId, {
              imageUrls,
              prompt: effectivePromptWithReferenceRoles,
              aspectRatio: effectiveAspectRatio,
              resolution: qualityToResolution(quality),
              ...durationPayload,
              generateAudio,
              ...dialoguePayload,
              model: effectiveVideoModelId,
              modelId: effectiveModelId,
              genMode: submitGenMode,
              humanReview: isSeedance20Model,
              sceneOptimize: sceneOptimize ?? null,
              canvasId,
              nodeId: targetId,
            });
        } else if (submitGenMode === "videoEdit") {
          // Source-video edit: exactly one source video plus the current
          // model contract's image-reference budget.
          const upstream = collectUpstream();
          const videoUrl =
            upstream
              .map((node) => referenceVideoUrl(node) ?? "")
              .find((url) => url.length > 0) ?? "";
          if (!videoUrl) {
            console.warn("[video-node] videoEdit submit without upstream video");
            updateNodeData(id, {
              isGenerating: false,
              generationStartedAt: null,
            });
            return;
          }
          const imageUrls = collectUpstreamImageUrls();
          const videoEditImageLimit =
            capabilityVideoModel?.referenceLimits?.videoEdit?.image
            ?? (isKacangKlingV2vModel ? 9 : 5);
          if (imageUrls.length > videoEditImageLimit) {
            void showErrorDialog(
              `当前模型的视频重绘最多支持 ${videoEditImageLimit} 张参考图，当前连接了 ${imageUrls.length} 张。请移除不需要的参考图，或选择支持全部参考图的模型。`,
              t("common.error"),
            );
            updateNodeData(id, { isGenerating: false, generationStartedAt: null });
            return;
          }
          const sourceVideo = upstream.find(node => referenceVideoUrl(node) === videoUrl);
          if (sourceVideo) recordReference(sourceVideo, 'video');
          const effectivePromptWithReferenceRoles = submissionPrompt();
          doSubmit = (targetId) =>
            submitFreezoneVideoEdit(projectId, {
              videoUrl,
              imageUrls,
              prompt: effectivePromptWithReferenceRoles,
              aspectRatio: effectiveAspectRatio,
              resolution: qualityToResolution(quality),
              ...durationPayload,
              audioSetting: "auto",
              generateAudio,
              ...dialoguePayload,
              model: effectiveVideoModelId,
              modelId: effectiveModelId,
              genMode: submitGenMode,
              humanReview: isSeedance20Model,
              canvasId,
              nodeId: targetId,
            });
        } else if (submitGenMode === "allReference") {
          if (isHappyHorseModel) {
            void showErrorDialog(
              "HappyHorse 不支持全能参考模式，请切换为文生视频或图生视频。",
              t("common.error"),
            );
            updateNodeData(id, {
              isGenerating: false,
              generationStartedAt: null,
            });
            return;
          }
          // Omni-gen: submit exactly the connected media set. The selected
          // model contract is the only limit source; exceeding it is an
          // explicit error instead of silently discarding references.
          const upstream = collectUpstream();
          const references: FreezoneVideoReferenceItem[] = [];
          // 与 references 里 type==="audio" 的项一一对应，用于提交前逐条校验音频时长。
          const audioRefs: {
            url: string;
            label: string;
            durationMs: number | null;
          }[] = [];
          const referenceByNodeId = new Map(
            referenceMedia.map((item) => [item.nodeId, item]),
          );
          let imageCount = 0;
          let videoCount = 0;
          let audioCount = 0;
          for (const node of upstream) {
            const videoRefUrl = referenceVideoUrl(node);
            if (videoRefUrl) {
              // 视频节点或携带 videoUrl 的 upload 节点（资产库视频）统一收集。
              const media = referenceByNodeId.get(node.id);
              if (autoTailFrameReference) {
                // H3's current AutoDL workflow has no video-reference slot.
                // Convert the connected predecessor to its final still so the
                // user gets continuity without a second node or a new field.
                const filename = `continuation-tail-${Date.now()}-${imageCount + 1}.png`;
                const blob = await captureVideoFrameBlob(
                  videoRefUrl,
                  Number.MAX_SAFE_INTEGER,
                );
                const uploaded = await uploadFreezoneImage(
                  projectId,
                  new File([blob], filename, { type: "image/png" }),
                  filename,
                );
                references.push({
                  type: "image",
                  url: uploaded.url,
                  role: "尾帧衔接",
                  label: media?.displayName
                    ? `${media.displayName} 尾帧`
                    : "上一段视频尾帧",
                });
                recordReference(node, 'image', 'continuity');
                imageCount += 1;
              } else {
                references.push({
                  type: "video",
                  url: videoRefUrl,
                  role: media?.role ? videoReferenceRoleLabel(media.role) : undefined,
                  label: media?.displayName || undefined,
                });
                recordReference(node, 'video');
                videoCount += 1;
              }
            } else if (isAudioNode(node)) {
              const url =
                typeof node.data.audioUrl === "string"
                  ? node.data.audioUrl
                  : "";
              if (url) {
                // 音频引用默认走「配乐参考」语义；label 用 sourceFileName /
                // displayName 之一，方便后端日志和后续 UI 展示对得上。
                const rawLabel =
                  (typeof node.data.sourceFileName === "string"
                    ? node.data.sourceFileName
                    : "") ||
                  (typeof node.data.displayName === "string"
                    ? node.data.displayName
                    : "");
                const media = referenceByNodeId.get(node.id);
                references.push({
                  type: "audio",
                  url,
                  role: media?.role ? videoReferenceRoleLabel(media.role) : "声音/节奏参考",
                  label: rawLabel || media?.displayName || undefined,
                });
                recordReference(node, 'audio', media?.role ?? 'audio');
                audioRefs.push({
                  url,
                  label:
                    rawLabel ||
                    t("node.videoNode.audio.clipFallbackLabel", {
                      index: audioCount + 1,
                    }),
                  durationMs:
                    typeof node.data.durationMs === "number"
                      ? node.data.durationMs
                      : null,
                });
                audioCount += 1;
              }
            } else {
              const url = submittableImageUrl(node);
              if (url) {
                const media = referenceByNodeId.get(node.id);
                references.push({
                  type: "image",
                  url,
                  role: media?.role ? videoReferenceRoleLabel(media.role) : undefined,
                  label: media?.displayName || undefined,
                });
                recordReference(node, 'image');
                imageCount += 1;
              }
            }
          }
          // 台词配音不再作为参考音频接入：说话由模型的原生对白音频负责，见上方
          // submitGenMode 处的说明。手动连入的音频节点仍走 type="audio" 通道。
          const currentMediaRejection = videoSubmitMediaRejectionReason(
            "allReference",
            videoModelFamily,
            { images: imageCount, videos: videoCount, audios: audioCount },
            selectedVideoModel,
          );
          if (currentMediaRejection) {
            void showErrorDialog(currentMediaRejection, t("common.error"));
            updateNodeData(id, {
              isGenerating: false,
              generationStartedAt: null,
            });
            return;
          }
          if (references.length === 0) {
            console.warn("[video-node] omni-gen submit without any reference");
            updateNodeData(id, {
              isGenerating: false,
              generationStartedAt: null,
            });
            return;
          }
          // Seedance 2.0 厂商对每条音频要求 1.8s ≤ 时长 ≤ 15.2s；这里逐条校验，
          // 不再错误累加总时长。durationMs 缺失时用 <audio> 探测，探测失败则交给
          // 后端兜底。仅对 seedance2 生效（其它模型边界未知）。
          if (isSeedance20Model && audioRefs.length > 0) {
            const resolvedDurations = await Promise.all(
              audioRefs.map((ref) =>
                typeof ref.durationMs === "number" && ref.durationMs > 0
                  ? Promise.resolve(ref.durationMs)
                  : probeAudioDurationMs(ref.url),
              ),
            );
            const rejection = audioReferenceDurationRejection(
              audioRefs.map((ref, index) => ({
                label: ref.label,
                durationMs: resolvedDurations[index] ?? null,
              })),
            );
            if (rejection) {
              const clips = formatAudioDurationClips(rejection.clips, (key, vars) =>
                t(key, vars),
              );
              void showErrorDialog(
                rejection.kind === "tooShort"
                  ? t("node.videoNode.audio.durationTooShort", {
                      min: MIN_AUDIO_REFERENCE_DURATION_MS / 1000,
                      clips,
                    })
                  : t("node.videoNode.audio.durationTooLong", {
                      max: MAX_AUDIO_REFERENCE_DURATION_MS / 1000,
                      clips,
                    }),
                t("common.error"),
              );
              updateNodeData(id, {
                isGenerating: false,
                generationStartedAt: null,
              });
              return;
            }
          }
          const effectivePromptWithReferenceRoles = submissionPrompt();
          doSubmit = (targetId) =>
            submitFreezoneVideoOmniGen(projectId, {
              prompt: effectivePromptWithReferenceRoles,
              references,
              aspectRatio: effectiveAspectRatio,
              resolution: qualityToResolution(quality),
              ...durationPayload,
              generateAudio,
              ...dialoguePayload,
              model: effectiveVideoModelId,
              modelId: effectiveModelId,
              genMode: submitGenMode,
              humanReview: isSeedance20Model,
              sceneOptimize: sceneOptimize ?? null,
              canvasId,
              nodeId: targetId,
            });
        } else {
          // textToVideo (default).
          const effectivePromptWithReferenceRoles = submissionPrompt();
          doSubmit = (targetId) =>
            submitFreezoneVideoGen(projectId, {
              prompt: effectivePromptWithReferenceRoles,
              aspectRatio: effectiveAspectRatio,
              resolution: qualityToResolution(quality),
              ...durationPayload,
              generateAudio,
              ...dialoguePayload,
              model: effectiveVideoModelId,
              modelId: effectiveModelId,
              genMode: submitGenMode,
              humanReview: isSeedance20Model,
              sceneOptimize: sceneOptimize ?? null,
              canvasId,
              nodeId: targetId,
            });
        }

        if (!doSubmit) {
          updateNodeData(id, { isGenerating: false, generationStartedAt: null });
          return;
        }
        const submitOnce = doSubmit;

        // 多条生成不再复制兄弟节点：N 个任务并发、全部回填到当前节点的
        // generationBatch（叠卡画册，与图片节点一致）。第 1 条完成的设为主视频，
        // 其余逐条追加。
        const total = clampGenerationBatchCount(count);
        // 各并发任务完成顺序不定，本地累积已完成的 URL，整组写回（避免读改写竞态）。
        const completedUrls: string[] = [];
        const completedSources: Record<string, FreezoneVideoGenerationSource> = {};
        // 收集每个子任务的失败，留到整批 settle 后统一决定是否弹错误框——避免
        // 「N 条里 1 条秒失败（如命中队列上限）、其余正常生成」时一边弹报错一边
        // 又冒加载动画的矛盾观感。
        const runErrors: unknown[] = [];
        const runOne = async (runIndex: number) => {
          let submittedJobId = "";
          try {
            // Freeze the exact model binding and request contract before the
            // submit hop; retries/history must not silently follow a later
            // model-center default.
            updateNodeData(id, {
              generationModel: selectedVideoModel?.apiModel ?? effectiveVideoModelId,
              generationModelId: effectiveModelId,
              generationProviderId: selectedVideoModel?.providerId ?? null,
              generationRequestSnapshot: {
                model: effectiveVideoModelId,
                modelId: effectiveModelId,
                providerId: selectedVideoModel?.providerId ?? null,
                genMode: submitGenMode,
                aspectRatio: effectiveAspectRatio,
                resolution: qualityToResolution(quality),
                ...(durationParameterEnabled ? { durationSeconds: durationClamped } : {}),
                generateAudio,
              },
            });
            const ref = await submitOnce(id);
            submittedJobId = ref.job_id;
            // Persist the task handle so a page refresh can resume this job.
            // N 个并发任务同节点只能存一个句柄——保留第 1 个（主视频）的。
            registerNodeGenerationTask(id, ref, { primary: runIndex === 0 });
            if (runIndex === 0) {
              updateNodeData(id, {
                generationRecoveryJobId: ref.job_id,
                generationRecoveryTaskType: ref.task_type,
              });
            }
            await cancelSubmittedTaskIfAborted(projectId, ref, abortController.signal);
            const mediaResult = await awaitFreezoneJobMediaResult(projectId, ref, {
              probeDelayMs: 900,
              maxProbeMs: 120_000,
              signal: abortController.signal,
              onTaskCompletedWithoutUrl: (completed) => {
                console.warn(
                  "[video-node] generation task completed without inline url",
                  completed,
                );
              },
            });
            if (abortController.signal.aborted) return;
            const url = mediaResult.url;
            if (url) {
              const sourcePatch = videoGenerationSourcePatch(url, mediaResult.result, ref.job_id);
              if (sourcePatch.videoGenerationSource) completedSources[url] = sourcePatch.videoGenerationSource;
              completedUrls.push(url);
              const isFirstCompleted = completedUrls.length === 1;
              updateNodeData(id, {
                // 第 1 条完成的设为主视频并结束 loading；后续只扩充画册。
                ...(isFirstCompleted
                  ? {
                      videoUrl: url,
                      ...sourcePatch,
                      ...(scriptFrames?.ok ? { scriptShotRenderedFrames: {
                        videoUrl: url, firstFrameUrl: scriptFrames.firstFrameUrl,
                        lastFrameUrl: scriptFrames.lastFrameUrl,
                        rowFingerprint: data.scriptShotRowFingerprint,
                      } } : {}),
                      ...buildGeneratedRightsPatch(data),
                      isGenerating: false,
                      generationStartedAt: null,
                      sourceFileName: null,
                      generationError: null,
                      generationErrorDetails: null,
                      generationErrorRequestId: null,
                      generationErrorCode: null,
                      generationErrorRetryable: null,
                      generationRecoveryJobId: null,
                      generationRecoveryTaskType: null,
                    }
                  : {}),
                ...(total > 1 ? { generationBatch: [...completedUrls], generationBatchSources: { ...completedSources } } : {}),
              });
            } else {
              console.warn(
                "[video-node] video gen completed without output url",
                mediaResult.task,
              );
              // 只有 run 0（任务句柄归属者）且尚无任何成功时才终结 loading——
              // 非首个任务先「无 URL 完成」不能把还在跑的整体 loading 掐掉。
              if (runIndex === 0 && completedUrls.length === 0) {
                updateNodeData(id, {
                  isGenerating: false,
                  generationStartedAt: null,
                  generationError: "视频生成未返回结果",
                  generationErrorDetails: null,
                  generationErrorRequestId: null,
                });
              }
            }
          } catch (error) {
            if (abortController.signal.aborted) return;
            if (isTaskCancelledError(error)) return;
            console.error("[video-node] video gen failed", error);
            // 先记下错误再决定是否早退 —— settle 后的聚合分支靠 runErrors 判断
            // 「部分失败」并弹 toast；早退前不记会把首个成功之后的失败彻底吞掉。
            runErrors.push(error);
            // 已有同批其它视频完成（主视频已落）时不覆盖成功态为错误——
            // 部分失败只影响画册条数。
            if (completedUrls.length > 0) return;
            const resolved = resolveErrorContent(error, "视频生成失败");
            const displayErrorMessage = backendErrorToastMessage(error, t);
            const diagnostics = resolveGenerationErrorDiagnostics(error, resolved.details);
            // Persist the failure on the node so the 重新生成 entry survives after
            // the user dismisses the dialog (previously the error was dialog-only).
            // 只有 run 0 失败才终结 loading：非首 run 失败时 run 0 可能还在跑，
            // 它的成功补丁会清掉这里写的错误横幅。
            updateNodeData(id, {
              ...(runIndex === 0
                ? { isGenerating: false, generationStartedAt: null }
              : {}),
              generationError: displayErrorMessage,
              generationErrorDetails: diagnostics.details,
              generationErrorRequestId: diagnostics.requestId,
              generationErrorStage: diagnostics.stage,
              generationErrorSuggestedAction: diagnostics.suggestedAction,
              generationErrorCode: diagnostics.errorCode,
              generationErrorRetryable: diagnostics.retryable,
              ...(submittedJobId
                ? {
                    generationRecoveryJobId: submittedJobId,
                    generationRecoveryTaskType: "freezone_video_gen",
                  }
                : {}),
            });
          }
        };

        // 旧画册清空 + 占位计数都在所有校验通过、真正开跑前才动——前面有多个
        // 校验失败的早退路径，提前动会白白毁掉已有画册 / 把「生成中」占位卡死。
        updateNodeData(id, {
          generationBatch: null,
          generationBatchSources: null,
          generationQueueTotal: total,
          generationQueueCompleted: 0,
          generationQueueConcurrency: Math.min(GENERATION_CONCURRENCY_DEFAULT, total),
        });
        setAlbumPendingTotal(id, total > 1 ? total : 0);
        await runGenerationQueue(
          Array.from({ length: total }, (_, runIndex) => runIndex),
          async (runIndex) => withGlobalGenerationSlot(
            () => runOne(runIndex),
            abortController.signal,
            // 上游还在排队 / 正在出图就先别提交（跟图片节点同一套闸门）。
            { nodeId: id, gate: () => upstreamGenerationGate(id) },
          ),
          GENERATION_CONCURRENCY_DEFAULT,
          (completed) => {
            if (!abortController.signal.aborted) {
              updateNodeData(id, { generationQueueCompleted: completed });
            }
          },
          abortController.signal,
        );
        setAlbumPendingTotal(id, 0);
        // Leaving the canvas stops local observation, not the persisted job.
        if (abortController.signal.aborted) return;
        updateNodeData(id, buildGenerationTerminalPatch());
        // 整批结束后再决定错误反馈：
        //  - 一条都没成功 → 弹一次错误框；
        //  - 部分成功 → 不弹模态打断，仅用轻量 toast 告知少出了几条。
        // 这样「N 条里 1 条命中队列上限秒失败、其余正常在跑」时不会再出现
        // 「先弹上限报错、节点却又冒出加载动画」的矛盾观感。
        if (completedUrls.length === 0 && runErrors.length > 0) {
          const firstError = runErrors[0];
          const resolved = resolveErrorContent(firstError, "视频生成失败");
          const displayErrorMessage = backendErrorToastMessage(firstError, t);
          const diagnostics = resolveGenerationErrorDiagnostics(firstError, resolved.details);
          void showErrorDialog(
            displayErrorMessage,
            t("common.error"),
            diagnostics.details ?? undefined,
          );
        } else if (runErrors.length > 0) {
          toast.error(
            t("node.videoNode.partialBatchFailed", {
              ok: completedUrls.length,
              total,
            }),
          );
        }
        // 所有任务尘埃落定后统一拉一次历史：N 条记录都落在本节点名下，run 0
        // settle 时就拉会漏掉后完成的 N-1 条（后端成功失败都会记）。
        void refreshHistory();
      } catch (error) {
        if (abortController.signal.aborted) return;
        console.error("[video-node] video gen failed", error);
        updateNodeData(id, buildGenerationTerminalPatch());
        setAlbumPendingTotal(id, 0);
      }
      } finally {
        if (generationQueueAbortRef.current === abortController) {
          generationQueueAbortRef.current = null;
        }
        submittingRef.current = false;
        clearGenerationIntent(id);
      }
    }, [
      autoTailFrameReference,
      data.scriptShotSourceNodeId,
      data.scriptShotRowDurationSec,
      effectiveAspectRatio,
      count,
      durationBounds,
      durationSec,
      durationOptions,
      durationParameterEnabled,
      generateAudio,
      genMode,
      isDirectVideoModel,
      id,
      isSeedance20Model,
      modelId,
      prompt,
      quality,
      refreshHistory,
      referenceMedia,
      sceneOptimize,
      selectedVideoModel,
      selectedVideoModelId,
      submitDisabled,
      updateNodeData,
      upstreamTextJoined,
      videoChannelDisabledReason,
      videoChannelEnabled,
    ]);

  useEffect(() => {
    if (data.canvas_auto_generate_once !== true || isGenerating) return;
    const keyframeReadiness = typeof data.scriptShotSourceNodeId === 'string'
      ? scriptShotKeyframeReadiness(useCanvasStore.getState().nodes.find(node => node.id === id), useCanvasStore.getState())
      : { ok: true as const };
    if (upstreamGenerationGate(id) !== "go" || (!keyframeReadiness.ok && keyframeReadiness.pending)) {
      const retryTimer = setTimeout(
        () => setAutoSubmitRetryTick((tick) => tick + 1),
        UPSTREAM_GATE_RETRY_MS,
      );
      return () => clearTimeout(retryTimer);
    }
    updateNodeData(id, { canvas_auto_generate_once: false });
    queueMicrotask(() => void handleSubmit());
  }, [
    autoSubmitRetryTick,
    data.canvas_auto_generate_once,
    data.scriptShotSourceNodeId,
    id,
    isGenerating,
    updateNodeData,
    handleSubmit,
  ]);

  return handleSubmit;
}

