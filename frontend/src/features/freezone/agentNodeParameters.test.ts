// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from 'vitest';

import { CANVAS_NODE_TYPES } from '@/features/canvas/domain/canvasNodes';

import { agentNodeParameters, agentPromptWithCameraMovement } from './agentNodeParameters';

describe('agent node parameter mapping', () => {
  it('writes declared image settings into actual image-node fields', () => {
    expect(agentNodeParameters({
      type: 'create_image_prompt_node',
      aspect_ratio: '9:16',
      image_size: '2K',
      model: 'image-model',
      count: 4,
      camera: { focal_length_mm: 50, aperture: 'f/2.8' },
    }, CANVAS_NODE_TYPES.imageGen)).toMatchObject({
      aspectRatio: '9:16',
      requestAspectRatio: '9:16',
      size: '2K',
      model: 'image-model',
      count: 4,
      cameraSelection: { focalLengthMm: 50, aperture: 'f/2.8' },
    });
  });

  it('writes declared video settings into actual video-node fields', () => {
    const data = agentNodeParameters({
      type: 'create_video_prompt_node',
      aspect_ratio: '16:9',
      video_quality: '1080P',
      duration_sec: 8,
      generation_mode: 'imageToVideo',
      generate_audio: false,
      camera_movement: 'dolly_in',
      count: 2,
    }, CANVAS_NODE_TYPES.video);
    expect(data).toMatchObject({
      aspectRatio: '16:9',
      quality: '1080P',
      durationSec: 8,
      genMode: 'imageToVideo',
      generateAudio: false,
      count: 2,
    });
    // 运镜不再落成节点字段：留着会被当成预设 id 提交，后端 400（T-154）。
    expect(data).not.toHaveProperty('cameraMovement');
  });

  it('folds a declared camera movement into the first prompt segment', () => {
    expect(agentPromptWithCameraMovement(
      { type: 'create_video_prompt_node', camera_movement: 'dolly_in' },
      '[画面构图] 祠堂正门 + [技术参数] 35mm',
    )).toBe('[运镜轨迹] 镜头前推 + [画面构图] 祠堂正门 + [技术参数] 35mm');
  });

  it('rewrites an existing camera segment instead of appending a second one', () => {
    expect(agentPromptWithCameraMovement(
      { type: 'create_video_prompt_node', camera_movement: '镜头前推，极慢' },
      '[运镜轨迹] 固定机位 + [画面构图] 祠堂正门',
    )).toBe('[运镜轨迹] 镜头前推，极慢 + [画面构图] 祠堂正门');
  });

  it('leaves the prompt untouched when the command declares no camera movement', () => {
    expect(agentPromptWithCameraMovement(
      { type: 'create_video_prompt_node' },
      '[运镜轨迹] 固定机位',
    )).toBe('[运镜轨迹] 固定机位');
  });

  it('drops invalid structured values instead of corrupting default node settings', () => {
    expect(agentNodeParameters({
      type: 'create_image_prompt_node',
      aspect_ratio: 'invalid',
      image_size: 'huge',
      count: 3,
      camera: { focal_length_mm: -10 },
    }, CANVAS_NODE_TYPES.imageGen)).toEqual({});
  });

  it('keeps valid portrait and photographic image ratios', () => {
    expect(agentNodeParameters({
      type: 'create_image_prompt_node',
      aspect_ratio: '2:3',
    }, CANVAS_NODE_TYPES.imageGen)).toMatchObject({
      aspectRatio: '2:3',
      requestAspectRatio: '2:3',
    });
  });

  it('keeps valid upstream-specific image ratios and explicit dimensions', () => {
    expect(agentNodeParameters({
      type: 'create_image_prompt_node',
      aspect_ratio: '7:5',
      image_size: '2048x1376',
    }, CANVAS_NODE_TYPES.imageGen)).toMatchObject({
      aspectRatio: '7:5',
      requestAspectRatio: '7:5',
      size: '2048x1376',
    });
  });

  it('keeps provider-specific video resolution labels instead of dropping them', () => {
    expect(agentNodeParameters({
      type: 'create_video_prompt_node',
      video_quality: '1440p',
    }, CANVAS_NODE_TYPES.video)).toMatchObject({
      quality: '1440P',
    });
  });
});
