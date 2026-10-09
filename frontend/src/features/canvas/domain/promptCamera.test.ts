// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from 'vitest';
import cameraCases from '../../../../../tests/fixtures/script-camera-contract.json';

import { CAMERA_MOVEMENT_PRESETS } from './cameraMovementPresets';
import {
  cameraDirectionText,
  cameraDirectionTextFromCommand,
  cameraDirectionNeedsReview,
  cameraPresetForPrompt,
  clearCameraDirection,
  foldLegacyCameraMovement,
  hasCameraDirection,
  isCameraSegment,
  resetCameraDirection,
  resolveCameraPresetForText,
  setCameraDirection,
} from './promptCamera';
import { splitPromptSegmentChunks } from './promptSegments';

/**
 * 运镜的归口（T-154）。
 *
 * 事故背景：节点上曾有一个 `cameraMovement` 字段，UI 当「预设 id」写、脚本派生当
 * 「自由文本」写，提交时统一当 `camera_template_id` 发出去。真机画布上 12 个脚本派生
 * 的视频节点全部自由文本，每一镜都撞 `unknown camera_template_id: [运镜轨迹] …` 400。
 * 现在运镜唯一的存放位置就是提示词里的那一段 —— 这一层锁的是「写进去、换得掉、
 * 清得净、认得出」，尤其是**段数不能变**（服务端按段序判定 6 段 / 8 段）。
 */

const MOTION_PROMPT = [
  '[明确的摄影机运镜：固定机位，极慢微推]',
  '[主体极其具体的物理动作：她抬手压住帽檐]',
  '[环境物理动态：雨丝斜落]',
  '[音效与氛围：雨声]',
  '[对话台词：无]',
  '[时长：4.0s]',
].join(' + ');

describe('shared camera contract cases', () => {
  it.each(cameraCases)('$id: reads authored camera and only flags explicit possible contradictions', ({ text, camera, needs_review }) => {
    expect(cameraDirectionText(`[${text}]`)).toBe(camera);
    expect(cameraDirectionNeedsReview(camera)).toBe(needs_review);
  });

  it('edits and restores unlabelled camera without losing managed reference blocks or adding a seventh segment', () => {
    const original = MOTION_PROMPT.replace('[明确的摄影机运镜：固定机位，极慢微推]', '[固定的中景背影机位，镜头跟随跑者]');
    const references = '[视频参考用途：@图片1锁定身份[人物：跑者]，不复制运镜说明。]';
    const prompt = `${references}\n${original}`;
    expect(cameraDirectionText(prompt)).toBe('固定的中景背影机位，镜头跟随跑者');
    expect(hasCameraDirection(prompt)).toBe(true);
    const edited = setCameraDirection(prompt, '镜头先跟随后停住');
    expect(edited).toBe(`${references}\n${original.replace('[固定的中景背影机位，镜头跟随跑者]', '[镜头先跟随后停住]')}`);
    expect(cameraDirectionText(edited)).toBe('镜头先跟随后停住');
    expect(splitPromptSegmentChunks(edited)).toHaveLength(6);
    expect(resetCameraDirection(edited, prompt)).toBe(prompt);
    expect(clearCameraDirection(prompt)).toContain(references);
    expect(cameraDirectionText(clearCameraDirection(prompt))).toBe('');
  });

  it('keeps mixed free prose when adding camera, and preserves image design before a motion block', () => {
    const prose = '镜头缓缓推近门环，雨丝斜落，人物伸手';
    expect(setCameraDirection(prose, '固定观察')).toBe(`[运镜轨迹] 固定观察 + ${prose}`);
    const prompt = `画面设计：[画面构图：门环] + [技术参数：35mm]\n${MOTION_PROMPT}\n本镜观看目的：看清手与门环`;
    expect(cameraDirectionText(prompt)).toBe('固定机位，极慢微推');
    expect(setCameraDirection(prompt, '镜头横移')).toBe(prompt.replace('固定机位，极慢微推', '镜头横移'));
    expect(clearCameraDirection(prompt)).toBe(prompt.replace('[明确的摄影机运镜：固定机位，极慢微推] + ', ''));
    expect(setCameraDirection('[明确的摄影机运镜：] + [主体动作：伸手]', '固定观察')).toBe('[明确的摄影机运镜：固定观察] + [主体动作：伸手]');
    expect(setCameraDirection('[运镜轨迹：运镜]', '镜头停在 $& 标记前')).toBe('[运镜轨迹：镜头停在 $& 标记前]');
  });
});

describe('cameraDirectionText', () => {
  it('读出 `[标签] 正文` 两段式的运镜正文', () => {
    expect(cameraDirectionText('[运镜轨迹] 固定机位，极慢微推 + [画面构图] 祠堂正门')).toBe(
      '固定机位，极慢微推',
    );
  });

  it('读出 `[标签：正文]` 规范式的运镜正文', () => {
    expect(cameraDirectionText(MOTION_PROMPT)).toBe('固定机位，极慢微推');
  });

  it('没有运镜段时返回空串', () => {
    expect(cameraDirectionText('[画面构图] 祠堂正门')).toBe('');
    expect(cameraDirectionText('')).toBe('');
    expect(cameraDirectionText(null)).toBe('');
  });
});

describe('isCameraSegment / hasCameraDirection', () => {
  it('认标签与无标签正文两种开头', () => {
    expect(isCameraSegment('[运镜轨迹] 固定机位')).toBe(true);
    expect(isCameraSegment('[明确的摄影机运镜：极慢推进]')).toBe(true);
    expect(isCameraSegment('[画面构图] 祠堂正门')).toBe(false);
  });

  it('hasCameraDirection 只看有没有那一段', () => {
    expect(hasCameraDirection(MOTION_PROMPT)).toBe(true);
    expect(hasCameraDirection('[画面构图] 祠堂正门')).toBe(false);
  });
});

describe('setCameraDirection', () => {
  it('已有运镜段时只换正文，段数与标签都不动', () => {
    const next = setCameraDirection(MOTION_PROMPT, '镜头前推，极慢');
    const chunks = splitPromptSegmentChunks(next);
    expect(chunks).toHaveLength(6);
    expect(chunks[0]).toBe('[明确的摄影机运镜：镜头前推，极慢]');
    expect(chunks[5]).toBe('[时长：4.0s]');
  });

  it('没有运镜段时插在最前，不追加在尾巴上', () => {
    expect(setCameraDirection('[画面构图] 祠堂正门 + [技术参数] 35mm', '镜头前推')).toBe(
      '[运镜轨迹] 镜头前推 + [画面构图] 祠堂正门 + [技术参数] 35mm',
    );
  });

  it('标签写法保持原样，不被改写', () => {
    expect(setCameraDirection('[运镜轨迹] 固定机位 + [画面构图] 门', '镜头前推，缓慢')).toBe(
      '[运镜轨迹] 镜头前推，缓慢 + [画面构图] 门',
    );
  });

  it('重复写入不会堆出第二个运镜段', () => {
    const once = setCameraDirection('[画面构图] 门', '镜头前推');
    const twice = setCameraDirection(once, '镜头左摇');
    expect(splitPromptSegmentChunks(twice)).toEqual(['[运镜轨迹] 镜头左摇', '[画面构图] 门']);
  });

  it('传空文本等于删掉运镜段', () => {
    expect(setCameraDirection(MOTION_PROMPT, '   ')).toBe(
      [
        '[主体极其具体的物理动作：她抬手压住帽檐]',
        '[环境物理动态：雨丝斜落]',
        '[音效与氛围：雨声]',
        '[对话台词：无]',
        '[时长：4.0s]',
      ].join(' + '),
    );
    expect(clearCameraDirection(MOTION_PROMPT)).not.toContain('运镜');
  });

  it('没有运镜段时删除是空操作', () => {
    expect(clearCameraDirection('[画面构图] 门')).toBe('[画面构图] 门');
  });

  it('结尾的句号不影响写入结果', () => {
    expect(setCameraDirection('', '固定机位。')).toBe('[运镜轨迹] 固定机位');
  });
});

describe('resetCameraDirection', () => {
  it('脚本派生的节点回到该行运动稿原文（6 段式不能少一段）', () => {
    const edited = setCameraDirection(MOTION_PROMPT, '用户手写的运镜');
    expect(resetCameraDirection(edited, MOTION_PROMPT)).toBe(MOTION_PROMPT);
  });

  it('没有原文可还原时删掉运镜段', () => {
    expect(resetCameraDirection('[运镜轨迹] 手写 + [画面构图] 门', '')).toBe('[画面构图] 门');
    expect(resetCameraDirection('[运镜轨迹] 手写 + [画面构图] 门', null)).toBe('[画面构图] 门');
  });

  it('原文本身没有运镜段时不拿它覆盖（避免静默丢段）', () => {
    expect(resetCameraDirection('[运镜轨迹] 手写 + [画面构图] 门', '[画面构图] 门')).toBe(
      '[画面构图] 门',
    );
  });
});

describe('resolveCameraPresetForText / cameraPresetForPrompt', () => {
  it('目录正文全等时点亮那条卡片', () => {
    expect(resolveCameraPresetForText(CAMERA_MOVEMENT_PRESETS, '镜头前推')?.id).toBe('dolly-in');
  });

  it('用户在其后补了速度 / 终点，仍认得出是哪一条', () => {
    expect(resolveCameraPresetForText(CAMERA_MOVEMENT_PRESETS, '镜头前推，极慢，落在门环上')?.id).toBe(
      'dolly-in',
    );
  });

  it('手写的运镜不点亮任何卡片', () => {
    expect(resolveCameraPresetForText(CAMERA_MOVEMENT_PRESETS, '镜头贴着水面滑行')).toBeNull();
    expect(resolveCameraPresetForText(CAMERA_MOVEMENT_PRESETS, '')).toBeNull();
  });

  it('cameraPresetForPrompt 从提示词里取正文再反解', () => {
    expect(cameraPresetForPrompt(CAMERA_MOVEMENT_PRESETS, '[运镜轨迹] 镜头环绕拍摄')?.id).toBe(
      'orbit',
    );
    expect(cameraPresetForPrompt(CAMERA_MOVEMENT_PRESETS, '[画面构图] 门')).toBeNull();
  });
});

describe('cameraDirectionTextFromCommand', () => {
  it('命令写目录 id（后端 snake_case 或前端 kebab）就用目录正文', () => {
    expect(cameraDirectionTextFromCommand('dolly_in')).toBe('镜头前推');
    expect(cameraDirectionTextFromCommand('dolly-in')).toBe('镜头前推');
    expect(cameraDirectionTextFromCommand('orbit_around')).toBe('镜头环绕拍摄');
  });

  it('命令写目录名称也用目录正文（全等，不做前缀）', () => {
    expect(cameraDirectionTextFromCommand('镜头前推')).toBe('镜头前推');
    expect(cameraDirectionTextFromCommand('镜头前推，然后停住')).toBe('镜头前推，然后停住');
  });

  it('自由文本原样保留（语言优先，不要求命中目录）', () => {
    expect(cameraDirectionTextFromCommand('镜头贴着水面滑行，极慢')).toBe('镜头贴着水面滑行，极慢');
  });

  it('带 `[运镜轨迹] ` 壳的写法先剥壳', () => {
    expect(cameraDirectionTextFromCommand('[运镜轨迹] 固定机位，极慢微推')).toBe('固定机位，极慢微推');
  });

  it('空值与非法类型返回空串', () => {
    expect(cameraDirectionTextFromCommand('')).toBe('');
    expect(cameraDirectionTextFromCommand(null)).toBe('');
    expect(cameraDirectionTextFromCommand({ id: 'x' })).toBe('');
  });
});

describe('foldLegacyCameraMovement（旧画布水合）', () => {
  it('旧值是目录 id：按目录正文补一段（只补空，不覆盖已有的运镜段）', () => {
    expect(foldLegacyCameraMovement('[画面构图] 门', 'dolly_in')).toBe(
      '[运镜轨迹] 镜头前推 + [画面构图] 门',
    );
    expect(
      foldLegacyCameraMovement('[运镜轨迹] 固定机位 + [画面构图] 门', 'dolly_in'),
    ).toBeNull();
  });

  it('旧值是自由文本、提示词已有运镜段时不折（提示词是真相）', () => {
    expect(
      foldLegacyCameraMovement(MOTION_PROMPT, '[运镜轨迹] 固定机位，极慢微推，不切碎表演'),
    ).toBeNull();
  });

  it('旧值是自由文本、提示词没有运镜段时补一段', () => {
    expect(foldLegacyCameraMovement('[画面构图] 门', '固定机位，极慢微推')).toBe(
      '[运镜轨迹] 固定机位，极慢微推 + [画面构图] 门',
    );
  });

  it('旧值是整条提示词的副本时不补段（不把同一个运镜说两遍）', () => {
    expect(foldLegacyCameraMovement('镜头缓缓推近，阿雀抬起眼睛', '镜头缓缓推近，阿雀抬起眼睛')).toBeNull();
    expect(foldLegacyCameraMovement('镜头前推，极慢', '镜头前推')).toBeNull();
  });

  it('折完没有变化时返回 null（调用方据此跳过写回）', () => {
    expect(foldLegacyCameraMovement('[运镜轨迹] 镜头前推', 'dolly_in')).toBeNull();
    expect(foldLegacyCameraMovement('[画面构图] 门', '')).toBeNull();
    expect(foldLegacyCameraMovement('[画面构图] 门', null)).toBeNull();
    expect(foldLegacyCameraMovement('[画面构图] 门', 42)).toBeNull();
  });
});
