import { describe, expect, it } from 'vitest';
import { buildScriptShotSpecs } from './scriptStoryboard';
import { buildScriptShotVideoSpecs } from './scriptShotVideos';

describe('storyboard initial instant', () => {
  it('prioritizes the observed start without freezing ongoing motion or drawing the ending', () => {
    const row = { shot_id: 'slide', shot_no: 1, shot_prompt: '跳过间隙后落地',
      start_state: '滑板刚离开左平台，双脚踩板，身体前倾',
      end_state: '落在右平台继续滑行', prop_state_start: '板身完整，脚贴板面' };
    const spec = buildScriptShotSpecs([row])[0];
    expect(spec.basePrompt.startsWith('首帧契约：')).toBe(true);
    expect(spec.basePrompt).toContain(`首帧可见状态：${row.start_state}`);
    expect(spec.basePrompt).toContain('不提前完成视频动作');
    expect(spec.basePrompt).toContain('尚未接触的部位保留可辨识的真实间隙');
    expect(spec.basePrompt).toContain('起点已在运动时保留该瞬间姿态');
    expect(spec.basePrompt).toContain(`首帧道具状态：${row.prop_state_start}`);
    expect(spec.basePrompt).not.toContain(row.end_state);
    expect(spec.prompt).toContain(spec.basePrompt);
    expect(row.shot_prompt).toBe('跳过间隙后落地');
  });

  it('does not invent a start state for historical rows', () => {
    expect(buildScriptShotSpecs([{ shot_no: 1, shot_prompt: '原画面' }])[0].basePrompt).toBe('原画面');
  });

  it.each(['无', '没有', 'none', 'N/A', '-', '—', ''])('does not render missing state marker %s', (marker) => {
    const row = { shot_no: 1, shot_prompt: '阿波站在屋顶', video_motion_prompt: '阿波迈步',
      start_state: marker, prop_state_start: marker };
    expect(buildScriptShotSpecs([row])[0].basePrompt).toBe(row.shot_prompt);
    const video = buildScriptShotVideoSpecs([row])[0];
    expect(video.startState).toBe('');
    expect(video.continuityIn.frame).toBe('本镜分镜图首帧');
    expect(video.continuityIn).not.toHaveProperty('action_state');
  });
});
