import { describe, expect, it } from 'vitest';
import { scriptKeyframePlan, scriptKeyframePlanIssues, scriptKeyframePlanReport, scriptKeyframeStatePrompt, scriptKeyframeVisualContext } from './scriptKeyframePlan';

describe('keyframe visual differences', () => {
  it('omits an empty-purpose opening repeat and an exact planned duplicate', () => {
    const row = {
      start_state: '拳头与小臂接触。',
      keyframe_plan: [
        { role: 'action_state', state: ' 拳头与小臂接触 ', purpose: '', required: true },
        { role: 'ending_state', state: '双方分开，退后两步', purpose: '锁结束站位', required: false },
        { role: 'ending_state', state: '双方分开,退后两步。', purpose: '锁结束站位', required: false },
      ],
    };
    expect(scriptKeyframePlan(row)).toEqual([row.keyframe_plan[1]]);
  });

  it('keeps the same visible state when its purpose or responsibility differs', () => {
    const row = {
      start_state: '双方分开',
      keyframe_plan: [
        { role: 'spatial_reveal', state: '双方分开', purpose: '锁定两人间距', required: false },
        { role: 'ending_state', state: '双方分开', purpose: '锁定结束重心', required: true },
      ],
    };
    expect(scriptKeyframePlan(row)).toEqual(row.keyframe_plan);
    expect(scriptKeyframePlan({ start_state: '手握栏杆', keyframe_plan: [
      { role: 'contact_state', state: '手握栏杆', purpose: '', required: true },
    ] })).toHaveLength(1);
  });

  it('returns the reason and source index for rejected plan entries', () => {
    const report = scriptKeyframePlanReport({
      start_state: '站在门边',
      keyframe_plan: [
        { role: 'action_state', state: '站在门边', purpose: '' },
        { role: 'action_state', state: '转身', purpose: '锁转身' },
        { role: 'action_state', state: '转身', purpose: '锁转身' },
      ],
    });
    expect(report.rejections).toEqual([
      { index: 0, reason: 'opening_state' },
      { index: 2, reason: 'duplicate_plan', duplicateOf: 1 },
    ]);
  });

  it('merges the required marker without mutating the original plan', () => {
    const plan = [
      { role: 'contact_state', state: '手握栏杆', purpose: '锁接触', required: false },
      { role: 'contact_state', state: '手握栏杆', purpose: '锁接触', required: true },
    ];
    expect(scriptKeyframePlan({ keyframe_plan: plan })).toEqual([{ ...plan[0], required: true }]);
    expect(plan[0].required).toBe(false);
  });

  it('does not infer semantic equality from similar wording when the visible contact changes', () => {
    const plan = [{ role: 'contact_state', state: '左手抓住衣领', purpose: '衣物受力', required: true }];
    expect(scriptKeyframePlan({ start_state: '左手伸向衣领，尚未抓住', keyframe_plan: plan })).toEqual(plan);
  });

  it('keeps signed and decimal coordinates distinct', () => {
    const plan = [{ role: 'spatial_reveal', state: '人物位于x=2.5', purpose: '人物已穿过中心', required: false }];
    expect(scriptKeyframePlan({ start_state: '人物位于x=-2.5', keyframe_plan: plan })).toEqual(plan);
  });

  it.each(['无', '没有', 'none', 'N/A', '-', ''])('does not generate a state image from the missing marker %s', (state) => {
    expect(scriptKeyframePlan({ keyframe_plan: [{ state, required: true }] })).toEqual([]);
  });

  it('separates target geometry from the actual opening image and shared composition', () => {
    const row = { start_state: '双方拳臂已经接触', shot_prompt: '[画面构图：双方紧挨] + [光影几何：暖光]' };
    const prompt = scriptKeyframeStatePrompt('角色引用@图片2',
      { role: 'ending_state', state: '双方分开，退后两步', purpose: '锁结束站位', required: false },
      0, scriptKeyframeVisualContext(row));
    expect(prompt).toContain('先看@图片1中实际可见');
    expect(prompt).toContain('姿态、人物间距和接触关系由目标状态决定');
    expect(prompt).toContain('真实间隙');
    expect(prompt).not.toContain('[画面构图：双方紧挨]');
    expect(prompt).toContain('双方分开，退后两步');
    expect(prompt).toContain('角色引用@图片2');
    expect(prompt).not.toContain('首帧契约：');
  });

  it('uses the visible separated state over a contradictory old contact purpose', () => {
    const prompt = scriptKeyframeStatePrompt('角色引用@图片2；场景引用@图片3',
      { role: 'ending_state', state: '右手已经离开栏杆，人物重心移到板面', purpose: '锁定右手抓栏杆的接触点', required: true },
      0, '首图右手握紧栏杆。');
    expect(prompt).toContain('如用途与可见状态冲突，以可见状态为准');
    expect(prompt).toContain('不恢复已释放的接触');
    expect(prompt.indexOf('角色引用@图片2')).toBeLessThan(prompt.indexOf('首图右手握紧栏杆'));
    expect(prompt).toContain('不同时表现互斥阶段');
    expect(prompt).toContain('可见状态：右手已经离开栏杆');
  });

  it('carries stable design without the opening state or expression', () => {
    const row = { start_state: '双方拳臂已经接触', shot_prompt: '[微表情：怒视] + [光影几何：暖光]' };
    const context = scriptKeyframeVisualContext(row);
    expect(context).not.toContain(row.start_state);
    expect(context).toContain('[光影几何：暖光]');
    expect(context).not.toContain('怒视');
  });

  it('keeps distinct views of the same state and merges exact repeats across legacy and explicit edit strategy', () => {
    const item = { role: 'spatial_reveal', state: '女孩持剑，敌人化雾', purpose: '看清敌人的位置', required: false };
    const plan = [
      { ...item, generation_strategy: 'independent' as const, framing: '女孩过肩看向敌人' },
      { ...item, generation_strategy: 'independent' as const, framing: '高位俯视两者间距' },
    ];
    expect(scriptKeyframePlan({ keyframe_plan: plan })).toEqual(plan);
    expect(scriptKeyframePlan({ keyframe_plan: [item, { ...item, generation_strategy: 'state_edit' }] })).toEqual([item]);
  });

  it('uses the alternate framing without assigning the first asset the role of opening image', () => {
    const item = { role: 'spatial_reveal', generation_strategy: 'independent' as const,
      framing: '女孩过肩看向敌人', state: '敌人已化为雾气', purpose: '揭示两人的相对位置', required: true };
    const prompt = scriptKeyframeStatePrompt('角色引用@图片1', item, 0,
      scriptKeyframeVisualContext({ start_state: '女孩正面持剑', shot_prompt: '[画面构图：女孩正面全身] + [光影几何：月光]' }));
    expect(prompt).toContain('本张构图：女孩过肩看向敌人');
    expect(prompt).toContain('不沿用首图机位');
    expect(prompt).toContain('角色引用@图片1');
    expect(prompt).not.toContain('先看@图片1');
    expect(prompt).not.toContain('女孩正面');
    expect(prompt).toContain('月光');
  });

  it('reports incomplete independent inputs without dropping their required plan', () => {
    const item = { generation_strategy: 'independent' as const, state: '站在门边', purpose: '', required: true };
    const row = { start_state: item.state, keyframe_plan: [item] };
    expect(scriptKeyframePlan(row)).toHaveLength(1);
    expect(scriptKeyframePlanIssues([row])).toMatchObject([
      { rule_id: 'script.keyframe.input.v1', severity: 'blocking', message: expect.stringContaining('缺少景别') },
    ]);
    expect(scriptKeyframePlanIssues([{ ...row, keyframe_plan: [{ ...item, framing: '侧面近景' }] }])[0].message).toContain('缺少新增信息');
  });
});
