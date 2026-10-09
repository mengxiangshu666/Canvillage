import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import type { FreezoneStoryScriptRow } from '@/api/scriptContract';
import { buildScriptCreativeHandoff, scriptDirectorVisualContext, scriptShotVisualContext } from './scriptCreativeHandoff';
import { buildScriptShotVideoSpecs, scriptShotVideoPrompt } from './scriptShotVideos';
import { scriptRowFingerprint } from './scriptRowFingerprint';
import { scriptVideoExecutionPromptMatches } from './scriptShotVideoReferences';

const fixtures: { name: string; row: FreezoneStoryScriptRow; lines: string[] }[] = JSON.parse(
  readFileSync(resolve(process.cwd(), '../tests/fixtures/script_shot_visual_context.json'), 'utf8'),
);

describe('camera and shot visual execution', () => {
  it.each(fixtures)('projects only authored visual fields: $name', ({ row, lines }) => {
    const context = scriptShotVisualContext(row);
    expect(context ? context.split('\n').slice(1) : []).toEqual(lines);
  });

  const camera = '从红门西侧平视中景起，人物向南移动时沿栏杆东移，保持侧面间距；越过水塔后减速，落幅为平台全景，最后仍在缓慢跟随';
  const row = {
    shot_no: 1, duration: 8, shot_purpose: '越过水塔时才看见南侧落点',
    shot_prompt: '[画面构图：门西侧平视中景] + [角色卡：[阿波：红夹克]] + [主体状态：手扶红门] + [光影几何：冷色窗光] + [视觉风格/质感：手绘轮廓、纸纤维] + [技术参数：35mm]',
    video_motion_prompt: `[明确的摄影机运镜轨迹与速度：${camera}] + [主体动作：松开红门向南滑行] + [环境物理动态：红门打开后暖光落到平台] + [音效氛围：轮声] + [对话台词：无] + [时长：8s]`,
    lighting_mood: '冷色窗光，开门后暖光进入',
  };
  const plan = {
    visual_bible: { visual_style: '统一手绘', texture: '纸纤维', lighting: '下一镜才换日光', camera_language: '下一镜才环绕', color_progression: '下一镜满屏红色' },
  };

  it.each(['image_to_video', 'first_last_frame', 'text_to_video'])('actual prompt retains camera and local treatment: %s', mode => {
    const input = { ...row, generation_mode: mode };
    const before = JSON.stringify(input);
    const spec = buildScriptShotVideoSpecs([input], undefined, undefined, plan)[0];
    expect(spec.cameraMovement).toBe(camera);
    for (const text of [camera, '光影几何：冷色窗光', '手绘轮廓、纸纤维', row.lighting_mood, '红门打开后暖光落到平台', row.shot_purpose]) expect(spec.prompt).toContain(text);
    expect(spec.prompt).toContain('光线变化继续发生');
    expect(spec.prompt).not.toContain('下一镜才环绕');
    expect(spec.prompt).not.toContain('下一镜满屏红色');
    expect(spec.prompt).not.toContain('下一镜才换日光');
    if (mode !== 'text_to_video') {
      expect(spec.prompt).not.toContain('手扶红门');
      expect(spec.prompt).not.toContain('阿波：红夹克');
      expect(spec.prompt).not.toContain('技术参数：35mm');
    }
    expect(JSON.stringify(input)).toBe(before);
  });

  it('keeps global lighting progression in the plan, outside every individual visual baseline', () => {
    const handoff = buildScriptCreativeHandoff(row, [], plan);
    expect(handoff.directorContext?.visualBible?.lighting).toBe('下一镜才换日光');
    expect(scriptDirectorVisualContext(handoff)).toContain('纸纤维');
    expect(scriptDirectorVisualContext(handoff)).not.toContain('下一镜才换日光');
  });

  it('local visual edits change the executable version with unchanged motion', () => {
    const old = buildScriptShotVideoSpecs([row], undefined, undefined, plan)[0];
    const changed = { ...row, shot_prompt: row.shot_prompt.replace('冷色窗光', '烛火左侧低光') };
    const next = buildScriptShotVideoSpecs([changed], undefined, undefined, plan)[0];
    expect(next.prompt).not.toBe(old.prompt);
    expect(scriptRowFingerprint(changed)).not.toBe(scriptRowFingerprint(row));
    expect(scriptVideoExecutionPromptMatches({ prompt: next.prompt, shotContractFacts: { executionPrompt: next.prompt } })).toBe(true);
    expect(scriptVideoExecutionPromptMatches({ prompt: next.prompt, shotContractFacts: { executionPrompt: old.prompt } })).toBe(false);
    expect(next.cameraMovement).toBe(old.cameraMovement);
  });

  it('explicit camera overrides stay authoritative and held views remain valid', () => {
    const spec = buildScriptShotVideoSpecs([{ ...row, camera_movement: '固定机位，人物离画后继续观察空平台' }])[0];
    expect(spec.cameraMovement).toBe('固定机位，人物离画后继续观察空平台');
    expect(spec.prompt).not.toContain(camera);
    expect(spec.prompt).toContain(spec.cameraMovement);
    expect(spec.prompt).toContain('手绘轮廓、纸纤维');
  });

  it('does not create missing prompts from visual planning', () => {
    expect(scriptShotVideoPrompt({ lighting_mood: '烛光' }).prompt).toBe('');
    expect(buildScriptShotVideoSpecs([{ lighting_mood: '烛光' }], undefined, undefined, plan)[0].hasPrompt).toBe(false);
  });

  it('retains long visual endings and does not replace material with render cleanup', () => {
    const material = '织物笔触。'.repeat(2600) + '末尾：火光照到袖口，保留磨损纤维';
    const prompt = scriptShotVideoPrompt({ video_motion_prompt: '保持观察', shot_prompt: `[视觉风格/质感：${material}]` }).prompt;
    expect(prompt).toContain(material);
    expect(prompt).toContain('皮肤、毛发、织物、材质纹理');
  });
});
