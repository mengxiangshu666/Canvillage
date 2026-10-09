// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { splitPromptSegmentChunks } from '@/features/canvas/domain/promptSegments';

// The relay recognizes this line and reserves space for it when compacting images.
export const SCRIPT_IMAGE_RENDER_QUALITY =
  'RENDER QUALITY: 无噪点、颗粒；保留身份、结构、材质与风格。保留毛发、织物、笔触与剧情磨损，不磨皮。干净通透，无随机噪点、彩色斑点、胶片颗粒、压缩块、摩尔纹；纯净高光、清晰暗部、平滑渐变，保留皮肤和材质细节，以及剧情要求的污渍，避免蜡质磨皮和锐化光晕。去噪不改变角色五官、结构轮廓、材质或既定美术风格。';

export const SCRIPT_VIDEO_RENDER_QUALITY =
  '画面采用低噪声成像，暗部清晰、色彩渐变平滑；皮肤、毛发、织物、材质纹理和剧情要求的磨损保持可辨识。纹理附着于物体并随其运动，静止区域帧间稳定，无随机噪点跳动、纹理爬动、边缘闪烁或压缩块；保留自然运动模糊和真实光影变化，避免蜡质磨皮及锐化光晕。';

export function withScriptImageQuality(prompt: string): string {
  if (!prompt.trim() || prompt.includes(SCRIPT_IMAGE_RENDER_QUALITY)) return prompt;
  return `${prompt}\n${SCRIPT_IMAGE_RENDER_QUALITY}`;
}

export function withScriptVideoQuality(prompt: string): string {
  if (!prompt.trim() || prompt.includes(SCRIPT_VIDEO_RENDER_QUALITY)) return prompt;
  const chunks = splitPromptSegmentChunks(prompt);
  if (chunks.length !== 6 || !chunks[2].endsWith(']')) {
    return `${prompt}\n${SCRIPT_VIDEO_RENDER_QUALITY}`;
  }
  chunks[2] = chunks[2].replace(/\]$/, `；${SCRIPT_VIDEO_RENDER_QUALITY}]`);
  return chunks.join(' + ');
}
