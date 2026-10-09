// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import {
  CANVAS_NODE_TYPES,
  type CanvasNodeData,
  type ImageGenCameraSelection,
  type ImageGenNodeData,
  type VideoNodeData,
} from '@/features/canvas/domain/canvasNodes';
import type { StructureOperation } from '@/features/superchat/structure-proposal-store';
import {
  cameraDirectionTextFromCommand,
  setCameraDirection,
} from '@/features/canvas/domain/promptCamera';
import {
  isValidImageAspectRatio,
  isValidImageSize,
  normalizeVideoQualityValue,
} from '@/features/canvas/models/imageCapabilityValues';

const COUNTS = new Set([1, 2, 4, 6, 8, 12]);
const VIDEO_MODES = new Set([
  'textToVideo',
  'allReference',
  'imageToVideo',
  'firstLastFrame',
  'imageReference',
  'videoEdit',
]);

function boundedText(value: unknown, maxLength = 200): string | null {
  if (typeof value !== 'string') return null;
  const text = value.trim();
  return text.length > 0 && text.length <= maxLength ? text : null;
}

function finiteInteger(value: unknown, min: number, max: number): number | null {
  if (typeof value !== 'number' || !Number.isFinite(value)) return null;
  const normalized = Math.round(value);
  return normalized >= min && normalized <= max ? normalized : null;
}

function normalizeCameraSelection(
  camera: StructureOperation['camera'],
): ImageGenCameraSelection | null {
  if (!camera || typeof camera !== 'object') return null;
  const selection: ImageGenCameraSelection = {};
  const cameraBodyId = boundedText(camera.camera_body_id);
  const lensId = boundedText(camera.lens_id);
  const focalLengthMm = finiteInteger(camera.focal_length_mm, 1, 2000);
  const aperture = boundedText(camera.aperture, 32);
  if (cameraBodyId) selection.cameraBodyId = cameraBodyId;
  if (lensId) selection.lensId = lensId;
  if (focalLengthMm !== null) selection.focalLengthMm = focalLengthMm;
  if (aperture) selection.aperture = aperture;
  return Object.keys(selection).length > 0 ? selection : null;
}

/**
 * Maps the Agent wire contract to the real canvas-node fields. A stated ratio,
 * resolution, duration, or camera must become a node parameter, not prompt prose.
 */
export function agentNodeParameters(
  command: StructureOperation,
  nodeType: typeof CANVAS_NODE_TYPES.imageGen | typeof CANVAS_NODE_TYPES.video,
): Partial<CanvasNodeData> {
  const aspectRatio = boundedText(command.aspect_ratio, 16);
  const model = boundedText(command.model);
  const count = finiteInteger(command.count, 1, 12);

  if (nodeType === CANVAS_NODE_TYPES.imageGen) {
    const data: Partial<ImageGenNodeData> = {};
    if (aspectRatio && isValidImageAspectRatio(aspectRatio)) {
      data.requestAspectRatio = aspectRatio;
      if (aspectRatio !== 'auto') data.aspectRatio = aspectRatio;
    }
    const imageSize = boundedText(command.image_size, 32);
    if (imageSize && isValidImageSize(imageSize)) data.size = imageSize as ImageGenNodeData['size'];
    if (model) data.model = model;
    if (count !== null && COUNTS.has(count)) data.count = count as ImageGenNodeData['count'];
    const cameraSelection = normalizeCameraSelection(command.camera);
    if (cameraSelection) data.cameraSelection = cameraSelection;
    return data as Partial<CanvasNodeData>;
  }

  const data: Partial<VideoNodeData> = {};
  if (aspectRatio && isValidImageAspectRatio(aspectRatio, false)) {
    data.aspectRatio = aspectRatio;
  }
  const videoQuality = boundedText(command.video_quality, 8);
  const normalizedQuality = videoQuality ? normalizeVideoQualityValue(videoQuality) : null;
  if (normalizedQuality) data.quality = normalizedQuality as VideoNodeData['quality'];
  const durationSec = finiteInteger(command.duration_sec, 1, 120);
  if (durationSec !== null) data.durationSec = durationSec;
  const generationMode = boundedText(command.generation_mode, 32);
  if (generationMode && VIDEO_MODES.has(generationMode)) {
    data.genMode = generationMode as VideoNodeData['genMode'];
  }
  if (model) data.model = model;
  if (typeof command.generate_audio === 'boolean') {
    data.generateAudio = command.generate_audio;
    data.generateAudioUserSet = true;
  }
  if (count !== null && COUNTS.has(count)) data.count = count as VideoNodeData['count'];
  return data as Partial<CanvasNodeData>;
}

/**
 * 命令里的运镜归口到提示词（T-154）：节点上不再有「运镜预设 id」字段，
 * 所以这里返回的是**带运镜的提示词**，由调用方写成 `prompt`。
 *
 * 只认 `camera_movement`；空值原样返回入参提示词。
 */
export function agentPromptWithCameraMovement(
  command: StructureOperation,
  prompt: string,
): string {
  const cameraText = cameraDirectionTextFromCommand(command.camera_movement);
  return cameraText ? setCameraDirection(prompt, cameraText) : prompt;
}
