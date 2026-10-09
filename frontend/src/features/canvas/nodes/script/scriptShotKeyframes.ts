// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { isVideoNode, type CanvasNode, type CanvasEdge } from '@/features/canvas/domain/canvasNodes';
import { readScriptShotId } from '@/features/canvas/domain/scriptShotIdentity';
import { sortUpstreamByReferenceOrder, upstreamNodesInEdgeOrder } from '../referenceOrdering';
import { submittableImageUrl } from '../videoNodeModelRules';

export type ScriptShotKeyframeReadiness =
  | { ok: true }
  | { ok: false; pending: boolean; reason: string };

/** A script state frame is ready only after its own generated image exists. */
export function scriptShotKeyframeReadiness(
  video: CanvasNode | undefined,
  graph: { nodes: CanvasNode[]; edges: CanvasEdge[] },
): ScriptShotKeyframeReadiness {
  if (!video) return { ok: true };
  const keyframeIds = graph.edges
    .filter(edge => edge.target === video.id && edge.data?.role === 'scriptShotKeyframe')
    .map(edge => edge.source);
  if (keyframeIds.length === 0) return { ok: true };
  const byId = new Map(graph.nodes.map(node => [node.id, node] as const));
  const frames = keyframeIds.map(id => byId.get(id)).filter((node): node is CanvasNode => Boolean(node));
  if (frames.length !== keyframeIds.length) {
    return { ok: false, pending: false, reason: '本镜状态关键帧节点缺失，请重新建立本镜视频节点' };
  }
  const failed = frames.find(node => node.data.generationError);
  if (failed) return { ok: false, pending: false, reason: '本镜状态关键帧生成失败，请先重试关键帧' };
  const pending = frames.some(node => !node.data.imageUrl || node.data.isGenerating || node.data.canvas_auto_generate_once);
  return pending
    ? { ok: false, pending: true, reason: '本镜状态关键帧尚未完成，视频会在关键帧全部出图后自动提交' }
    : { ok: true };
}

/** Manual frame pairing uses the same visible order as video submission. */
export function scriptShotKeyframes(video: CanvasNode | undefined, graph: { nodes: CanvasNode[]; edges: CanvasEdge[] }, fingerprint?: string):
  { ok: true; firstFrameUrl: string; lastFrameUrl: string } | { ok: false; reason: string } {
  if (!video || video.data.genMode !== 'firstLastFrame') return { ok: false, reason: '请在本镜视频节点选择首尾帧模式并连接本镜尾帧' };
  const order = Array.isArray(video.data.referenceOrder) ? video.data.referenceOrder.filter((id): id is string => typeof id === 'string') : undefined;
  const upstream = sortUpstreamByReferenceOrder(upstreamNodesInEdgeOrder(graph.nodes, graph.edges, video.id), order);
  const images = upstream.filter(node => submittableImageUrl(node));
  if (images.length !== 2) return { ok: false, reason: '首尾帧需要恰好两张已完成的图片，按首帧、尾帧顺序连接' };
  if (images[0].id !== video.data.scriptShotImageNodeId) return { ok: false, reason: '第一张图片必须是本镜分镜首帧，请调整引用顺序' };
  for (const image of images) {
    if (!image.data.imageUrl || image.data.isGenerating || image.data.canvas_auto_generate_once || image.data.generationError) return { ok: false, reason: '首尾帧图片尚未完成或生成失败' };
    if (graph.edges.some(edge => edge.source === image.id && edge.target === video.id && edge.data?.role === 'scriptShotContinuity')) return { ok: false, reason: '上一镜承接参考不能充当本镜尾帧，请连接本镜结束画面' };
    const shotId = readScriptShotId(image.data);
    if (shotId && shotId !== readScriptShotId(video.data)) return { ok: false, reason: '其他镜头的分镜图不能充当本镜尾帧' };
    const expected = fingerprint ?? video.data.scriptShotRowFingerprint;
    if (image.data.scriptShotEndFrameForVideo && (image.data.scriptShotEndFrameForVideo !== video.id
      || image.data.scriptShotEndFrameSourceUrl !== images[0].data.imageUrl
      || image.data.scriptShotRowFingerprint !== expected)) return { ok: false, reason: '尾帧草稿的首图或脚本已变化，请重新准备尾帧' };
    if (image.data.scriptShotRowFingerprint && expected && image.data.scriptShotRowFingerprint !== expected) return { ok: false, reason: '首尾帧对应的脚本已修改，请重新准备图片' };
    const capture = image.data.captureMetadata as Record<string, unknown> | undefined;
    if (capture) {
      const source = graph.nodes.find(node => node.id === capture.source_node_id);
      if (capture.source_kind !== 'video_frame_capture' || capture.capture_mode !== 'last'
        || !source || !isVideoNode(source) || source.id !== video.id
        || source.data.isGenerating || source.data.generationError
        || !source.data.videoUrl || source.data.videoUrl !== capture.source_video_url
        || (expected && source.data.scriptShotRowFingerprint !== expected)) return { ok: false, reason: '截图尾帧必须来自本镜当前视频版本，请重新准备尾帧' };
    }
  }
  return { ok: true, firstFrameUrl: images[0].data.imageUrl as string, lastFrameUrl: images[1].data.imageUrl as string };
}

/** Render provenance compares submitted inputs, not the source video's newer result. */
export function scriptShotFrameChangeReason(video: CanvasNode, graph: { nodes: readonly CanvasNode[]; edges: readonly CanvasEdge[] }): string | null {
  const rendered = video.data.scriptShotRenderedFrames as Record<string, unknown> | undefined;
  if (video.data.genMode === 'firstLastFrame' && (!rendered || rendered.videoUrl !== video.data.videoUrl
    || rendered.rowFingerprint !== video.data.scriptShotRowFingerprint)) return '缺少当前视频的首尾帧出片记录，请重新生成这一镜';
  const recordedFirst = video.data.scriptShotFirstFrameUrl;
  if (typeof recordedFirst !== 'string' || !recordedFirst) return video.data.genMode === 'firstLastFrame' ? '缺少首尾帧出片记录，请重新生成这一镜' : null;
  const first = graph.nodes.find(node => node.id === video.data.scriptShotImageNodeId);
  if (!first || !graph.edges.some(edge => edge.source === first.id && edge.target === video.id)
    || first.data.imageUrl !== recordedFirst || first.data.isGenerating || first.data.canvas_auto_generate_once || first.data.generationError) return '首帧已变化或不可用，请重新生成这一镜';
  if (video.data.genMode !== 'firstLastFrame') return null;
  const order = Array.isArray(video.data.referenceOrder) ? video.data.referenceOrder.filter((id): id is string => typeof id === 'string') : undefined;
  const images = sortUpstreamByReferenceOrder(upstreamNodesInEdgeOrder([...graph.nodes], graph.edges, video.id), order).filter(node => submittableImageUrl(node));
  if (images.length !== 2 || images[0].id !== first.id
    || images[0].data.imageUrl !== rendered?.firstFrameUrl
    || !rendered?.lastFrameUrl || images[1].data.imageUrl !== rendered.lastFrameUrl
    || images[1].data.isGenerating || images[1].data.canvas_auto_generate_once || images[1].data.generationError
    || graph.edges.some(edge => edge.source === images[1].id && edge.target === video.id && edge.data?.role === 'scriptShotContinuity')) return '首尾帧输入已变化，请重新生成这一镜';
  return null;
}
