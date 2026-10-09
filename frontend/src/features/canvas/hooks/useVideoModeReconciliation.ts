// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useEffect } from "react";

import type { VideoGenMode } from "@/features/canvas/domain/canvasNodes";
import {
  reconcileVideoModeForNode,
  type VideoModelFamily,
  type VideoReferenceCounts,
} from "@/features/canvas/domain/videoCapabilityCompiler";

type VideoModePatchWriter = (
  nodeId: string,
  patch: { genMode: VideoGenMode },
) => void;

type VideoReferenceKind = "image" | "video" | "audio";
type VideoReferenceLimits = Partial<
  Record<VideoGenMode, Partial<Record<VideoReferenceKind, number>>>
>;

/**
 * 旧节点常把多图素材保存在 imageToVideo 模式。只要实时模型合同声明了
 * allReference 且能容纳当前图片数，就把这个旧默认模式升级到真正的多图模式。
 */
export function preferDeclaredMultiReferenceMode(
  storedMode: VideoGenMode | null | undefined,
  imageCount: number,
  supportedModes: readonly VideoGenMode[] = [],
  allReferenceImageLimit?: number,
): VideoGenMode | null {
  const isLegacyImageMode =
    storedMode == null ||
    storedMode === "textToVideo" ||
    storedMode === "imageToVideo";
  const fitsCurrentImages =
    allReferenceImageLimit == null || allReferenceImageLimit >= imageCount;
  if (
    imageCount > 1 &&
    isLegacyImageMode &&
    supportedModes.includes("allReference") &&
    fitsCurrentImages
  ) {
    return "allReference";
  }
  return null;
}

export function reconcileDeclaredVideoMode(
  familyTarget: VideoGenMode,
  renderedMode: VideoGenMode,
  supportedModes: readonly VideoGenMode[] | undefined,
  storedMode?: VideoGenMode | null,
): VideoGenMode {
  // 直连模型的 family 可能只能解析为 generic，但上游仍会声明完整的
  // supportedModes。保留用户已经选中的声明模式，避免 reconciliation
  // 用 generic 的旧回退规则把 allReference 强制改回 imageToVideo。
  // 文生视频除外：已有素材时仍由 family 规则自动切到合适的输入模式。
  if (
    storedMode &&
    storedMode !== "textToVideo" &&
    supportedModes?.includes(storedMode)
  ) {
    return storedMode;
  }
  // `undefined` is legacy metadata and permits the family fallback. An
  // explicit empty declaration has no legal mode, so preserve the rendered
  // value and let submit validation report the unavailable contract.
  if (supportedModes === undefined) return renderedMode === 'allReference' ? renderedMode : familyTarget;
  if (supportedModes.length === 0) return storedMode ?? renderedMode;
  if (supportedModes.includes(familyTarget)) {
    return familyTarget;
  }
  return supportedModes.includes(renderedMode) ? renderedMode : supportedModes[0];
}

export function useVideoModeReconciliation({
  nodeId,
  storedMode,
  renderedMode,
  videoModelFamily,
  mediaCounts,
  typedCounts,
  isHappyHorseModel,
  supportedModes,
  referenceLimits,
  catalogAuthoritative = true,
  updateNodeData,
}: {
  nodeId: string;
  storedMode: VideoGenMode | null | undefined;
  renderedMode: VideoGenMode;
  videoModelFamily: VideoModelFamily;
  mediaCounts: VideoReferenceCounts;
  typedCounts: VideoReferenceCounts;
  isHappyHorseModel: boolean;
  supportedModes?: readonly VideoGenMode[];
  referenceLimits?: VideoReferenceLimits | null;
  /**
   * 目录未就绪时模型族会退化成 `generic`、`supportedModes` 变成 `undefined`，
   * 于是 `reconcileVideoModeForNode` 会把 allReference 判成非法并改写成
   * imageToVideo。这个改写也会落盘，且目录回来后 `storedMode` 已经是
   * imageToVideo，回不到原来的模式。除非调用方确认目录权威，否则只渲染不写。
   */
  catalogAuthoritative?: boolean;
  updateNodeData: VideoModePatchWriter;
}): void {
  // genMode 只能由这一条 hook 写入。把模式状态机固定在 canvas hooks 层，
  // VideoNode 只负责传入模型族、上游计数和写回函数，避免 UI 大组件继续堆 effect。
  useEffect(() => {
    if (!catalogAuthoritative) return;
    const counts = isHappyHorseModel ? typedCounts : mediaCounts;
    const allReferenceImageLimit = referenceLimits?.allReference?.image;
    const preferredDeclaredMode = preferDeclaredMultiReferenceMode(
      storedMode,
      counts.images,
      supportedModes,
      allReferenceImageLimit,
    );
    const familyTarget = reconcileVideoModeForNode(
      videoModelFamily,
      counts,
      storedMode,
    );
    const target =
      preferredDeclaredMode ??
      reconcileDeclaredVideoMode(
        familyTarget,
        renderedMode,
        supportedModes,
        storedMode,
      );
    if (target !== storedMode) {
      updateNodeData(nodeId, { genMode: target });
    }
  }, [
    catalogAuthoritative,
    isHappyHorseModel,
    mediaCounts.audios,
    mediaCounts.images,
    mediaCounts.videos,
    nodeId,
    renderedMode,
    storedMode,
    supportedModes,
    typedCounts.audios,
    typedCounts.images,
    typedCounts.videos,
    updateNodeData,
    videoModelFamily,
    referenceLimits?.allReference?.image,
  ]);
}
