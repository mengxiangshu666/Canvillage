// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useCallback, useEffect, useMemo } from "react";

import {
  isAudioNode,
  isExportImageNode,
  isImageEditNode,
  isImageGenNode,
  isStoryboardGenNode,
  isUploadNode,
  isVideoNode,
  type CanvasNode,
  type VideoGenMode,
  type VideoShotContractFacts,
} from "@/features/canvas/domain/canvasNodes";
import { resolveImageDisplayUrl } from "@/features/canvas/application/imageData";
import {
  extractUpstreamContent,
  joinUpstreamText,
} from "@/features/canvas/application/graphContentResolver";
import { useUpstreamNodes } from "@/features/canvas/application/useUpstreamGraph";
import { sortUpstreamByReferenceOrder } from "@/features/canvas/nodes/referenceOrdering";
import { useReferenceMentionSync } from "@/features/canvas/nodes/useReferenceMentionSync";
import { compileScriptVideoReferences, scriptVideoReferenceBlock, scriptVideoReferenceFacts } from "@/features/canvas/nodes/script/scriptShotVideoReferences";
import type { MentionCandidate } from "@/features/canvas/nodes/PromptMentionEditor";
import { useCanvasStore } from "@/stores/canvasStore";
import type { VideoReferenceCounts } from "@/features/canvas/domain/videoCapabilityCompiler";
import {
  inferVideoReferenceRole,
  referenceRoleFromVideoEdge,
  type VideoReferenceRole,
} from "@/features/canvas/domain/videoReferenceRoles";

type ReferenceMediaKind = "image" | "video" | "audio";
type ReferenceLimits = Partial<
  Record<VideoGenMode, Partial<Record<ReferenceMediaKind, number>>>
>;
type VideoReferenceOrder = string[] | null | undefined;

export type ReferenceMediaItem =
  | {
      kind: "image";
      nodeId: string;
      imageUrl: string;
      displayName?: string | null;
      role?: VideoReferenceRole;
    }
  | {
      kind: "video";
      nodeId: string;
      videoUrl: string;
      thumbUrl?: string | null;
      displayName?: string | null;
      role?: VideoReferenceRole;
      /**
       * 素材总时长（毫秒）。缩略图左下角的 `2.0s` 角标读它 —— 与 LibTV 一样，
       * 只有带播放时长的素材（视频/音频）才显示这个角标，图片没有。
       */
      durationMs?: number | null;
    }
  | {
      kind: "audio";
      nodeId: string;
      audioUrl: string;
      displayName?: string | null;
      role?: VideoReferenceRole;
      /** 同 video：音频也给时长角标。 */
      durationMs?: number | null;
    };

export interface ReferenceMediaCapEntry {
  item: ReferenceMediaItem;
  /** 1-based 同类型序号（图片/视频/音频 各自累加），与 chip 角标 + @ 提及对齐。 */
  typeIndex: number;
  /** 是否在当前模式的引用上限内；表里没有的模式默认 true。 */
  withinCap: boolean;
}

/**
 * 素材时长（毫秒）——缩略图左下角 `2.0s` 角标的唯一数据源。
 *
 * 先认 `durationMs`（画布节点上的现行字段），再退到 `durationSec`（部分节点与
 * 历史数据只写到秒）。两者都没有就返回 null —— 宁可角标不显示，也不要用一个
 * 猜出来的数字骗用户「这段是 5 秒」。
 */
function mediaDurationMs(data: { durationMs?: unknown; durationSec?: unknown }): number | null {
  const ms = data?.durationMs;
  if (typeof ms === "number" && Number.isFinite(ms) && ms > 0) return ms;
  const sec = data?.durationSec;
  if (typeof sec === "number" && Number.isFinite(sec) && sec > 0) return sec * 1000;
  return null;
}

export function audioReferenceFileName(item: {
  displayName?: string | null;
  audioUrl: string;
}): string | null {
  const name = item.displayName?.trim();
  if (name) return name;
  try {
    const origin =
      typeof window !== "undefined" ? window.location.origin : "http://localhost";
    const path = new URL(item.audioUrl, origin).pathname;
    const base = decodeURIComponent(path.split("/").filter(Boolean).pop() ?? "");
    return base || null;
  } catch {
    return null;
  }
}

export function referenceImageUrl(
  node: CanvasNode | undefined | null,
): string | null {
  if (!node) return null;
  if (isImageGenNode(node)) {
    const data = node.data;
    if (data.scriptShotKeyframeRowKey) return data.imageUrl || null;
    // imageGen 上传给生图用的「参考图」会写到 data.referenceImageUrl；
    // 在 imageGen 自身还没生成结果之前，它就是该节点对外呈现的图片，
    // 视频节点也应该把它当成上游图引用。
    const ref = firstReferenceUrl(data, [
      "imageUrl",
      "image_url",
      "previewImageUrl",
      "preview_image_url",
      "outputImageUrl",
      "output_image_url",
      "referenceImageUrl",
      "reference_image_url",
      "committed_slot_url",
      "mediaUrl",
      "media_url",
    ]);
    return ref;
  }
  if (
    isUploadNode(node) ||
    isImageEditNode(node) ||
    isExportImageNode(node) ||
    isStoryboardGenNode(node)
  ) {
    return firstReferenceUrl(node.data, [
      "imageUrl",
      "image_url",
      "previewImageUrl",
      "preview_image_url",
      "outputImageUrl",
      "output_image_url",
      "committed_slot_url",
      "mediaUrl",
      "media_url",
    ]);
  }
  return null;
}

// 上游「视频引用」：视频节点自带 videoUrl，但从资产库选入的视频是 upload 节点，
// 地址同样写在 data.videoUrl。所以「是不是视频上游」应按「存在非空 data.videoUrl」
// 判定，而非节点类型——否则资产库视频会被漏认（HappyHorse 不自动切 videoEdit、
// 提交找不到 videoUrl），还会被 referenceImageUrl / isUploadNode 误当图片。
export function referenceVideoUrl(
  node: CanvasNode | undefined | null,
): string | null {
  if (!node) return null;
  return firstReferenceUrl(node.data, [
    "videoUrl",
    "video_url",
    "outputVideoUrl",
    "output_video_url",
    "resultVideoUrl",
    "result_video_url",
    "previewVideoUrl",
    "preview_video_url",
    "sourceVideoUrl",
    "source_video_url",
  ]);
}

function firstReferenceUrl(
  data: Record<string, unknown>,
  keys: readonly string[],
): string | null {
  for (const key of keys) {
    const value = data[key];
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return null;
}

export function useVideoReferences({
  nodeId,
  prompt,
  referenceOrder,
  genMode,
  referenceLimits,
  capsByMode,
  updatePrompt,
}: {
  nodeId: string;
  prompt: string;
  referenceOrder: VideoReferenceOrder;
  genMode: VideoGenMode;
  referenceLimits?: ReferenceLimits | null;
  capsByMode: ReferenceLimits;
  updatePrompt: (nextPrompt: string) => void;
}): {
  upstreamNodes: CanvasNode[];
  isConnected: boolean;
  referenceMedia: ReferenceMediaItem[];
  referenceMediaCapInfo: ReferenceMediaCapEntry[];
  mentionCandidates: MentionCandidate[];
  upstreamContents: ReturnType<typeof extractUpstreamContent>[];
  upstreamTextContents: ReturnType<typeof extractUpstreamContent>[];
  upstreamTextJoined: string;
  upstreamCounts: VideoReferenceCounts;
  upstreamTypeCounts: VideoReferenceCounts;
  handleDetachUpstream: (sourceNodeId: string) => void;
} {
  const upstreamNodes = useUpstreamNodes(nodeId);
  // 「上一镜承接」边的角色只能从边推（同一条边对本镜是首帧、对下一镜是上一镜画面），
  // 所以这里读一次入边，给下面填参考素材角色当兜底用。
  const incomingEdges = useCanvasStore((state) => state.edges);
  const isConnected = useCanvasStore((state) =>
    state.edges.some((edge) => edge.target === nodeId),
  );
  const deleteEdge = useCanvasStore((state) => state.deleteEdge);

  const edgeRoles = useMemo(() => {
    const sources = new Map<string, VideoReferenceRole>();
    for (const edge of incomingEdges) {
      if (edge.target !== nodeId) continue;
      const role = referenceRoleFromVideoEdge(edge.data?.role, edge.data?.label);
      if (role) sources.set(edge.source, role);
    }
    return sources;
  }, [incomingEdges, nodeId]);

  const referenceMedia = useMemo<ReferenceMediaItem[]>(() => {
    const upstream = sortUpstreamByReferenceOrder(upstreamNodes, Array.isArray(referenceOrder) ? referenceOrder : undefined);
    const items: ReferenceMediaItem[] = [];
    // 节点自己声明过角色就以它为准；只有承接边上的图需要边来补角色。
    const roleFor = (node: CanvasNode): VideoReferenceRole | undefined =>
      edgeRoles.get(node.id) ?? inferVideoReferenceRole(node.data as Record<string, unknown>);
    for (const node of upstream) {
      const videoUrl = referenceVideoUrl(node);
      if (videoUrl) {
        const vdata = node.data as {
          previewImageUrl?: string | null;
          displayName?: string | null;
        };
        const thumbUrl =
          typeof vdata.previewImageUrl === "string" &&
          vdata.previewImageUrl.length > 0
            ? vdata.previewImageUrl
            : null;
        items.push({
          kind: "video",
          nodeId: node.id,
          videoUrl,
          thumbUrl,
          displayName: vdata.displayName ?? null,
          role: roleFor(node),
          durationMs: mediaDurationMs(node.data as { durationMs?: unknown }),
        });
        continue;
      }
      if (isAudioNode(node)) {
        const audioUrl = firstReferenceUrl(node.data, [
          "audioUrl",
          "audio_url",
          "outputAudioUrl",
          "output_audio_url",
          "resultAudioUrl",
          "result_audio_url",
          "previewAudioUrl",
          "preview_audio_url",
          "sourceAudioUrl",
          "source_audio_url",
        ]);
        if (!audioUrl) continue;
        items.push({
          kind: "audio",
          nodeId: node.id,
          audioUrl,
          displayName: node.data.displayName ?? null,
          role: "audio",
          durationMs: mediaDurationMs(node.data as { durationMs?: unknown }),
        });
        continue;
      }
      const url = referenceImageUrl(node);
      if (url) {
        items.push({
          kind: "image",
          nodeId: node.id,
          imageUrl: url,
          displayName:
            (node.data as { displayName?: string | null }).displayName ?? null,
          role: roleFor(node),
        });
      }
    }
    return items;
  }, [upstreamNodes, referenceOrder, edgeRoles]);

  const orderedImageIds = useMemo(
    () =>
      referenceMedia
        .filter((item) => item.kind === "image")
        .map((item) => item.nodeId),
    [referenceMedia],
  );
  const orderedVideoIds = useMemo(
    () =>
      referenceMedia
        .filter((item) => item.kind === "video")
        .map((item) => item.nodeId),
    [referenceMedia],
  );
  const orderedAudioIds = useMemo(
    () =>
      referenceMedia
        .filter((item) => item.kind === "audio")
        .map((item) => item.nodeId),
    [referenceMedia],
  );

  const updateReferencePrompt = useCallback((next: string) => {
    const current = useCanvasStore.getState().nodes.find(node => node.id === nodeId)?.data;
    const compiledOrder = current?.scriptVideoAssetReferenceOrder;
    const block = scriptVideoReferenceBlock(String(current?.scriptShotSourceNodeId ? current.prompt : prompt))?.text;
    // Script rearm already compiled this block against the new order.
    // Keep normal/manual mentions remapped without remapping that block twice.
    if (block && Array.isArray(compiledOrder) && compiledOrder.length === orderedImageIds.length
      && compiledOrder.every((id, index) => id === orderedImageIds[index])) {
      const span = scriptVideoReferenceBlock(next);
      if (span) next = next.slice(0, span.start) + block + next.slice(span.end);
    }
    updatePrompt(next);
  }, [nodeId, orderedImageIds, prompt, updatePrompt]);

  useEffect(() => {
    const state = useCanvasStore.getState();
    const node = state.nodes.find(item => item.id === nodeId);
    if (!node?.data.scriptShotSourceNodeId) return;
    const compiled = compileScriptVideoReferences(String(node.data.prompt ?? ''), nodeId, state);
    const next = compiled.prompt;
    const shotContractFacts = scriptVideoReferenceFacts(node.data.shotContractFacts as VideoShotContractFacts | undefined, compiled.references);
    const factsChanged = shotContractFacts && JSON.stringify(shotContractFacts) !== JSON.stringify(node.data.shotContractFacts);
    const order = node.data.scriptVideoAssetReferenceOrder;
    if (next !== node.data.prompt || factsChanged || !Array.isArray(order) || order.join('\n') !== orderedImageIds.join('\n')) {
      state.updateNodeData(nodeId, { prompt: next, scriptVideoAssetReferenceOrder: orderedImageIds, ...(shotContractFacts ? { shotContractFacts } : {}) });
    }
  }, [nodeId, prompt, referenceMedia, orderedImageIds]);

  useReferenceMentionSync(
    prompt,
    [
      { prefix: "图片", ids: orderedImageIds },
      { prefix: "视频", ids: orderedVideoIds },
      { prefix: "音频", ids: orderedAudioIds },
    ],
    updateReferencePrompt,
  );

  const referenceMediaCapInfo = useMemo(() => {
    const counts: Record<ReferenceMediaKind, number> = {
      image: 0,
      video: 0,
      audio: 0,
    };
    const caps = referenceLimits?.[genMode] ?? capsByMode[genMode];
    return referenceMedia.map((item) => {
      counts[item.kind] += 1;
      const cap = caps?.[item.kind];
      const withinCap = cap == null || counts[item.kind] <= cap;
      return { item, typeIndex: counts[item.kind], withinCap };
    });
  }, [capsByMode, genMode, referenceLimits, referenceMedia]);

  const mentionCandidates = useMemo<MentionCandidate[]>(() => {
    const out: MentionCandidate[] = [];
    let imageIdx = 0;
    let videoIdx = 0;
    let audioIdx = 0;
    const enforceCap = (referenceLimits?.[genMode] ?? capsByMode[genMode]) != null;
    for (const info of referenceMediaCapInfo) {
      const item = info.item;
      if (item.kind === "image") {
        imageIdx += 1;
        if (enforceCap && !info.withinCap) continue;
        out.push({
          key: item.nodeId,
          name: `图片${imageIdx}`,
          imageUrl: resolveImageDisplayUrl(item.imageUrl),
          index: imageIdx,
        });
      } else if (item.kind === "video") {
        videoIdx += 1;
        if (enforceCap && !info.withinCap) continue;
        out.push({
          key: item.nodeId,
          name: `视频${videoIdx}`,
          imageUrl: item.thumbUrl ? resolveImageDisplayUrl(item.thumbUrl) : "",
          videoUrl: resolveImageDisplayUrl(item.videoUrl),
          index: videoIdx,
        });
      } else if (item.kind === "audio") {
        audioIdx += 1;
        if (enforceCap && !info.withinCap) continue;
        out.push({
          key: item.nodeId,
          name: `音频${audioIdx}`,
          imageUrl: "",
          index: audioIdx,
          audioUrl: resolveImageDisplayUrl(item.audioUrl),
          displayName: audioReferenceFileName(item),
        });
      }
    }
    return out;
  }, [capsByMode, genMode, referenceLimits, referenceMediaCapInfo]);

  const handleDetachUpstream = useCallback(
    (sourceNodeId: string) => {
      useCanvasStore
        .getState()
        .edges.filter(
          (edge) => edge.source === sourceNodeId && edge.target === nodeId,
        )
        .forEach((edge) => deleteEdge(edge.id));
    },
    [deleteEdge, nodeId],
  );

  const upstreamContents = useMemo(
    () => upstreamNodes.map(extractUpstreamContent),
    [upstreamNodes],
  );
  const upstreamTextContents = useMemo(
    () =>
      upstreamContents.filter(
        (content) =>
          typeof content.text === "string" && content.text.trim().length > 0,
      ),
    [upstreamContents],
  );
  const upstreamTextJoined = useMemo(
    () => joinUpstreamText(upstreamContents),
    [upstreamContents],
  );

  const upstreamCounts = useMemo(() => {
    let images = 0;
    let videos = 0;
    let audios = 0;
    for (const node of upstreamNodes) {
      if (referenceVideoUrl(node)) {
        videos += 1;
      } else if (isAudioNode(node)) {
        if (
          typeof node.data.audioUrl === "string" &&
          node.data.audioUrl.length > 0
        ) {
          audios += 1;
        }
      } else if (referenceImageUrl(node)) {
        images += 1;
      }
    }
    return { images, videos, audios };
  }, [upstreamNodes]);

  const upstreamTypeCounts = useMemo(() => {
    let images = 0;
    let videos = 0;
    let audios = 0;
    for (const node of upstreamNodes) {
      if (isVideoNode(node) || referenceVideoUrl(node)) {
        videos += 1;
      } else if (isAudioNode(node)) {
        audios += 1;
      } else if (
        isImageGenNode(node) ||
        isUploadNode(node) ||
        isImageEditNode(node) ||
        isExportImageNode(node) ||
        isStoryboardGenNode(node)
      ) {
        images += 1;
      }
    }
    return { images, videos, audios };
  }, [upstreamNodes]);

  return {
    upstreamNodes,
    isConnected,
    referenceMedia,
    referenceMediaCapInfo,
    mentionCandidates,
    upstreamContents,
    upstreamTextContents,
    upstreamTextJoined,
    upstreamCounts,
    upstreamTypeCounts,
    handleDetachUpstream,
  };
}
