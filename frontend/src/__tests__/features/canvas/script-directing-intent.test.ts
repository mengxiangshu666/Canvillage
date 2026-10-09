import { describe, expect, it } from 'vitest';
import { buildScriptShotVideoSpecs, scriptShotVideoPrompt } from '@/features/canvas/nodes/script/scriptShotVideos';
import { buildScriptShotSpecs } from '@/features/canvas/nodes/script/scriptStoryboard';
import { scriptFieldSequence } from '@/features/canvas/nodes/script/scriptFields';
import { scriptRowFingerprint } from '@/features/canvas/nodes/script/scriptRowFingerprint';
import { storyScriptRewriteBody } from '@/api/scriptContract';
import { normalizeScriptResultIdentities } from '@/features/canvas/domain/scriptShotIdentity';
import { buildScriptCreativeHandoff, buildScriptDirectorContext } from '@/features/canvas/nodes/script/scriptCreativeHandoff';

describe('script directing intent', () => {
  it('本镜固定空间同源进入首图与各模式视频，完整保存并排除无关场景和未来动作', () => {
    const geography = '红门在水塔西侧两米，栏杆连接两者；' + '石板纹理。'.repeat(500) + '落点是水塔南侧平台';
    const row = {
      shot_no: 2, shot_prompt: '水塔旁的栏杆', video_motion_prompt: '松开栏杆滑向南侧平台',
      scene_tags: '屋顶、无、门环微距',
      scene_descriptions: { 屋顶: geography, 无: '无', 门环微距: '镜内细节', 地下室: '无关场景' },
      start_state: '右手扶栏杆，双脚踩板', end_state: '手已离开栏杆，板停在南侧平台',
    };
    const plan = { sequences: [{ sequence_id: 'A', shot_nos: [2, 3], staging_plan: '下一镜跳进地下室' }] };
    const frame = buildScriptShotSpecs([row], undefined, undefined, plan)[0];
    expect(frame.creativeHandoff.sceneDescriptions).toEqual({ 屋顶: geography });
    for (const mode of ['image_to_video', 'first_last_frame', 'text_to_video']) {
      const video = buildScriptShotVideoSpecs([{ ...row, generation_mode: mode }], undefined, undefined, plan)[0];
      expect(video.creativeHandoff.sceneDescriptions).toEqual(frame.creativeHandoff.sceneDescriptions);
      for (const prompt of [frame.prompt, video.prompt]) {
        expect(prompt).toContain(geography);
        expect(prompt).toContain('不把屏幕左右当永久地理方向');
        expect(prompt).toContain('保留计划中的遮挡与揭示时点');
        expect(prompt).not.toContain('无关场景');
        expect(prompt).not.toContain('镜内细节');
        expect(prompt).not.toContain('下一镜跳进地下室');
      }
    }
    const changed = { ...row, scene_descriptions: { 屋顶: geography + '，水塔不再位于北侧' } };
    expect(buildScriptShotSpecs([changed])[0].prompt).not.toBe(frame.prompt);
    expect(buildScriptShotVideoSpecs([changed])[0].prompt).not.toBe(buildScriptShotVideoSpecs([row])[0].prompt);
    row.scene_descriptions.屋顶 = '已修改';
    expect(frame.creativeHandoff.sceneDescriptions?.屋顶).toBe(geography);
  });
  it('缺项和非法空间说明不制造画面或视频正文，旧脚本仍兼容', () => {
    for (const definitions of [null, [], { 屋顶: false }, { 屋顶: 'N/A' }, { 地下室: '另一地点' }]) {
      const handoff = buildScriptCreativeHandoff({ scene_tags: '屋顶', scene_descriptions: definitions } as never);
      expect(handoff.sceneDescriptions).toBeUndefined();
    }
    const row = { scene_tags: '屋顶', scene_descriptions: { 屋顶: '红门在水塔西侧' } };
    expect(buildScriptShotSpecs([row])[0].prompt).toBe('');
    expect(buildScriptShotVideoSpecs([row])[0].hasPrompt).toBe(false);
    expect(buildScriptCreativeHandoff({})).toEqual({});
  });
  it('同场后镜只写场景名时继承台账定义，显式本镜定义仍优先', () => {
    const rows = [
      { shot_no: 1, shot_prompt: '红门旁', scene_tags: '屋顶', scene_descriptions: { 屋顶: '红门在水塔西侧' } },
      { shot_no: 2, shot_prompt: '栏杆旁', video_motion_prompt: '松开栏杆', scene_tags: '屋顶' },
      { shot_no: 3, shot_prompt: '庭院', scene_tags: '庭院' },
    ];
    const specs = buildScriptShotSpecs(rows);
    expect(specs[1].creativeHandoff.sceneDescriptions).toEqual(specs[0].creativeHandoff.sceneDescriptions);
    expect(specs[1].prompt).toContain('红门在水塔西侧');
    expect(buildScriptShotVideoSpecs(rows)[1].prompt).toContain('红门在水塔西侧');
    expect(specs[2].creativeHandoff.sceneDescriptions).toBeUndefined();
    expect(buildScriptCreativeHandoff(rows[0], [], undefined, 1, { 屋顶: '旧布局' }).sceneDescriptions?.屋顶).toBe('红门在水塔西侧');
  });
  it('把全片视觉基准与实际镜号所属段落交给各节点，不带入无关段落', () => {
    const row = {
      shot_no: 2,
      shot_prompt: '雨中的门',
      shot_purpose: '看清选择',
      sequence_ids: ['wrong-id'],
    };
    const plan = {
      story_promise: '一次选择改变关系',
      protagonist_goal: '找到出口',
      visual_bible: { visual_style: '冷峻写实', lighting: '门缝侧光' },
      sequences: [
        { sequence_id: 'actual', title: '门口', dramatic_goal: '逼近选择', shot_nos: [1, 2], staging_plan: '从门口到台阶' },
        { sequence_id: 'other', dramatic_goal: '屋顶追逐', shot_nos: [99], staging_plan: '跑上屋顶' },
      ],
    };
    const handoff = buildScriptCreativeHandoff(row, [], plan);
    expect(handoff.directorContext?.storyPromise).toBe('一次选择改变关系');
    expect(handoff.directorContext?.visualBible?.visualStyle).toBe('冷峻写实');
    expect(handoff.directorContext?.sequences.map((sequence) => sequence.sequenceId)).toEqual(['actual']);
    expect(handoff.directorContext?.sequences[0].stagingPlan).toBe('从门口到台阶');
    expect(handoff.sequenceIds).toEqual(['actual']);
    expect(buildScriptDirectorContext(plan, ['99'])?.sequences[0].sequenceId).toBe('other');
    const video = buildScriptShotVideoSpecs([{ ...row, video_motion_prompt: '抬头看门' }], undefined, undefined, plan)[0];
    expect(video.creativeHandoff.directorContext).toEqual(handoff.directorContext);
    expect(video.prompt).toContain('全片视觉风格：冷峻写实');
    expect(video.prompt).not.toContain('一次选择改变关系');
    expect(video.prompt).not.toContain('从门口到台阶');
    expect(video.prompt).not.toContain('跑上屋顶');
    expect(buildScriptShotSpecs([row], undefined, undefined, plan)[0].prompt).toContain('全片视觉风格：冷峻写实');
  });
  it('允许段落重叠，按真实镜号匹配，不因展示编号或空选择带入其他段落', () => {
    const plan = { sequences: [
      { sequence_id: 'A', shot_nos: [2, 2], performance_plan: '等待对方先开口' },
      { sequence_id: 'B', shot_nos: [2, 3], turn: '下一镜才离开' },
      { sequence_id: 'C', shot_nos: [99] },
    ] };
    const handoff = buildScriptShotSpecs([{ shot_no: 2, display_shot_no: '序章', shot_prompt: '门口' }], undefined, undefined, plan)[0].creativeHandoff;
    expect(handoff.sequenceIds).toEqual(['A', 'B']);
    expect(handoff.directorContext?.sequences[0].shotNos).toEqual([2]);
    expect(buildScriptDirectorContext(plan, [])).toBeUndefined();
    expect(buildScriptDirectorContext(plan, ['invalid'])).toBeUndefined();
    expect(buildScriptDirectorContext({})).toBeUndefined();
    plan.sequences[0].shot_nos.push(99);
    expect(handoff.directorContext?.sequences[0].shotNos).toEqual([2]);
  });
  it('保留段落未来安排而不让静帧或单镜提前表演，导演基准不补造缺失画面稿', () => {
    const plan = {
      ending_change: '结局重逢', rhythm_curve: '先慢后快', sound_plan: '后段才有钟声',
      visual_bible: { visual_style: '共同画风', lighting: '共同光线', color_progression: '下一镜转为红色', camera_language: '以后环绕' },
      sequences: [{ sequence_id: 'A', shot_nos: [2], staging_plan: '先在门口，下一镜跳过栏杆', performance_plan: '从紧张到狂喜', turn: '后续才开门' }],
    };
    const row = { shot_no: 2, shot_prompt: '[视觉风格：本镜剪纸] + [光影：本镜烛光]', video_motion_prompt: '留在门口', start_state: '双脚站地' };
    const frame = buildScriptShotSpecs([row], undefined, undefined, plan)[0];
    const video = buildScriptShotVideoSpecs([row], undefined, undefined, plan)[0];
    expect(frame.creativeHandoff.directorContext?.endingChange).toBe('结局重逢');
    expect(frame.creativeHandoff).toEqual(video.creativeHandoff);
    for (const prompt of [frame.prompt, video.prompt]) {
      expect(prompt).toContain('本镜风格、光线、动作和目标状态优先');
      for (const future of ['结局重逢', '下一镜跳过栏杆', '从紧张到狂喜', '后续才开门', '下一镜转为红色', '以后环绕', '后段才有钟声']) expect(prompt).not.toContain(future);
    }
    expect(frame.prompt).toContain('本镜剪纸');
    expect(frame.prompt).toContain('本镜烛光');
    expect(buildScriptShotSpecs([{ shot_no: 2 }], undefined, undefined, plan)[0].prompt).toBe('');
    expect(buildScriptShotVideoSpecs([{ shot_no: 2 }], undefined, undefined, plan)[0].hasPrompt).toBe(false);
  });
  it('retains and snapshots the whole-film plan during restore and rewrite', () => {
    const plan = { story_promise: '观察等待', sequences: [{ sequence_id: 'S1', shot_nos: [1] }] };
    const result = normalizeScriptResultIdentities({ title: '等待', director_plan: plan, rows: [{ shot_no: 1 }] }) as { director_plan: typeof plan };
    expect(result.director_plan).toEqual(plan);
    const body = storyScriptRewriteBody({ currentRows: [{ shot_no: 1 }], directorPlan: plan });
    plan.sequences[0].shot_nos.push(2);
    expect(body.director_plan).toEqual({ story_promise: '观察等待', sequences: [{ sequence_id: 'S1', shot_nos: [1] }] });
  });
  const row = {
    shot_no: 1, duration: 4, visual_description: '起跳后擦梁再落下',
    shot_prompt: '侧面全景', video_motion_prompt: '人物向右起跳',
    start_state: '右脚压板，身体向右倾斜', end_state: '已越过钢梁，板头朝右',
    film_language: '动作匹配；主观视点；声音延续', cut_reason: '让观众看清落点',
  };
  it('passes visible poses to image and video without abstract editing instructions', () => {
    const frame = buildScriptShotSpecs([row])[0];
    expect(frame.basePrompt).toContain(row.start_state);
    expect(scriptShotVideoPrompt(row).prompt).toContain(row.end_state);
    expect(scriptShotVideoPrompt(row).prompt).not.toContain(row.cut_reason);
    const spec = buildScriptShotVideoSpecs([row])[0];
    expect(frame.creativeHandoff).toEqual(spec.creativeHandoff);
    expect(spec.creativeHandoff.cutReason).toBe(row.cut_reason);
    expect(spec.continuityIn.frame).toBe(row.start_state);
    expect(spec.continuityOut.frame).toBe(row.end_state);
  });
  it('makes directing decisions editable/exportable and invalidates old content', () => {
    const keys = scriptFieldSequence().map(f => f.key);
    expect(keys).toContain('film_language');
    expect(keys).toContain('cut_reason');
    expect(scriptRowFingerprint(row)).not.toBe(scriptRowFingerprint({ ...row, end_state: '落地' }));
  });
  it('records numbered reference responsibilities in the same handoff used by video', () => {
    const rowWithAsset = {
      ...row,
      character_1: '阿波',
      character_image_1: '/阿波.png',
      scene_tags: '屋顶',
    };
    const frame = buildScriptShotSpecs([rowWithAsset])[0];
    const video = buildScriptShotVideoSpecs([rowWithAsset])[0];
    expect(frame.creativeHandoff).toEqual(video.creativeHandoff);
    expect(frame.creativeHandoff.referenceResponsibilities).toEqual([
      expect.objectContaining({ scope: 'storyboard', imageNumber: 1, role: 'character', name: '阿波' }),
    ]);
    expect(frame.creativeHandoff.referenceResponsibilities?.[0].responsibility).toContain('锁定人物身份');
    expect(frame.creativeHandoff.referenceResponsibilities?.[0].prohibited).toContain('不让参考图覆盖本镜状态');
  });
  it('puts only the initial prop state in storyboard frames and changes the prompt when it changes', () => {
    const props = { ...row, prop_state_start: '深色轮板，护目镜在额头', prop_state_change: '板头抬起', prop_state_end: '板头朝右' };
    const shot = buildScriptShotSpecs([props])[0];
    expect(shot.basePrompt).toContain('首帧道具状态：深色轮板，护目镜在额头');
    expect(shot.basePrompt).not.toContain(props.prop_state_change);
    expect(shot.basePrompt).not.toContain(props.prop_state_end);
    expect(shot.basePrompt).not.toContain(props.cut_reason);
    expect(buildScriptShotSpecs([{ ...props, prop_state_start: '发光悬浮板' }])[0].prompt).not.toBe(shot.prompt);
  });
  it('does not treat pose or prop notes alone as a complete image or video prompt', () => {
    const incomplete = { shot_no: 1, start_state: '站在平台', end_state: '落地', prop_state_start: '深色滑板' };
    expect(buildScriptShotSpecs([incomplete])[0].basePrompt).toBe('');
    expect(scriptShotVideoPrompt(incomplete).prompt).toBe('');
    expect(buildScriptShotVideoSpecs([incomplete])[0].hasPrompt).toBe(false);
  });
});
