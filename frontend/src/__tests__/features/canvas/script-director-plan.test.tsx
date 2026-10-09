import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { directorPlanGenerationInstruction, directorSequenceIssues } from '@/features/canvas/nodes/script/directorSequenceCoverage';
import { ScriptDirectorPlan } from '@/features/canvas/nodes/script/ScriptDirectorPlan';
import { scriptCutReviews } from '@/features/canvas/nodes/script/scriptCutReview';

it('checks coverage without forbidding overlapping film sequences', () => {
  const rows = [{ shot_no: 1 }, { shot_no: 2 }];
  expect(directorSequenceIssues({ sequences: [
    { sequence_id: 'S1', shot_nos: [1, 2] }, { sequence_id: 'S2', shot_nos: [2] },
  ] }, rows)).toEqual([]);
  const issues = directorSequenceIssues({ sequences: [{ sequence_id: 'S1', shot_nos: [1, 3] }] }, rows);
  expect(issues).toContain('序列 1引用了不存在的镜 3');
  expect(issues).toContain('镜 2 尚未归入序列');
  expect(directorSequenceIssues(undefined, rows)).toEqual(['尚未规划序列']);
});

it('carries edited creative direction into regeneration', () => {
  expect(directorPlanGenerationInstruction({ story_promise: '惊险但温暖', sound_plan: '环境声延续' })).toContain('惊险但温暖');
  expect(directorPlanGenerationInstruction(undefined)).toBe('');
});

it('reviews outgoing cuts without treating intentional ellipsis as broken motion', () => {
  const rows = [{ shot_no: 1, end_state: '门前等待', transition_plan: '椭圆省略', cut_reason: '省略一夜' }, { shot_no: 2, start_state: '清晨街口' }];
  expect(scriptCutReviews(rows)[0].note).toBe('切镜规划已有，实际动作与声音仍未验');
  expect(scriptCutReviews([{ ...rows[0], transition_plan: 'continuous_action' }, rows[1]])[0].note).toContain('需复核');
  expect(scriptCutReviews([{ shot_no: 1 }, { shot_no: 2 }])[0].note).toContain('待补规划');
  for (const marker of ['无', 'none', ' N/A ', 'null', '待定', '待补充']) {
    expect(scriptCutReviews([{ ...rows[0], end_state: marker, transition_plan: 'continuous_action' }, rows[1]])[0].note).toContain('待补规划');
  }
});

it('shows the plan, preserves other decisions on edit and reports unsynced rows', () => {
  const onCommit = vi.fn();
  const plan = { story_promise: '一场冒险', sound_plan: '环境声延续', visual_bible: { visual_style: '动画' }, sequences: [{ sequence_id: 'S1', shot_nos: [1], dramatic_goal: '期待落点' }] };
  render(<ScriptDirectorPlan plan={plan} rows={[{ shot_no: 1 }]} onCommit={onCommit} pending />);
  fireEvent.click(screen.getByText('导演规划 · 1 段'));
  expect(screen.getByRole('status')).toHaveTextContent('已有镜头尚未同步');
  const field = screen.getByLabelText('故事承诺');
  fireEvent.change(field, { target: { value: '惊险但温暖' } });
  fireEvent.blur(field);
  expect(onCommit).toHaveBeenCalledWith({ ...plan, story_promise: '惊险但温暖' });
  expect(plan.story_promise).toBe('一场冒险');
});

it.each([
  ['dramatic_goal', '期待'], ['resistance', '阻力'], ['escalation', '发展'], ['turn', '转折'], ['release', '余波'],
  ['staging_plan', '空间调度'], ['performance_plan', '表演推进'],
] as const)('edits sequence %s and carries it into regeneration without changing other sequences', (key, label) => {
  const onCommit = vi.fn();
  const plan = { story_promise: '一场冒险', sequences: [
    { sequence_id: 'S1', shot_nos: [1], dramatic_goal: '旧期待', resistance: '阻力' },
    { sequence_id: 'S2', shot_nos: [2], dramatic_goal: '后段', turn: '有意停留' },
  ] };
  const original = structuredClone(plan);
  render(<ScriptDirectorPlan plan={plan} rows={[{ shot_no: 1 }, { shot_no: 2 }]} onCommit={onCommit} />);
  fireEvent.click(screen.getByText('导演规划 · 2 段'));
  const field = screen.getByLabelText(`第 2 段 · ${label}`);
  fireEvent.change(field, { target: { value: '新的期待' } });
  fireEvent.blur(field);
  expect(onCommit).toHaveBeenCalledWith({
    ...plan,
    sequences: [plan.sequences[0], { ...plan.sequences[1], [key]: '新的期待' }],
  });
  expect(plan).toEqual(original);
  expect(directorPlanGenerationInstruction(onCommit.mock.calls[0][0])).toContain('新的期待');
});

it('keeps sequence decisions read-only while generation is busy', () => {
  render(<ScriptDirectorPlan plan={{ sequences: [{ sequence_id: 'S1', shot_nos: [1] }] }} rows={[{ shot_no: 1 }]} disabled onCommit={vi.fn()} />);
  fireEvent.click(screen.getByText('导演规划 · 1 段'));
  for (const label of ['期待', '阻力', '发展', '转折', '余波', '空间调度', '表演推进']) {
    expect(screen.getByLabelText(`第 1 段 · ${label}`)).toBeDisabled();
  }
});

it('rewrites the chosen sequence by identity and disables commands during a job', () => {
  const onRewriteSequence = vi.fn();
  const props = { plan: { sequences: [{ sequence_id: 'S1', shot_nos: [1] }, { sequence_id: 'S2', shot_nos: [2] }] },
    rows: [{ shot_no: 1 }, { shot_no: 2 }], onCommit: vi.fn(), onRewriteSequence };
  const { rerender } = render(<ScriptDirectorPlan {...props} />);
  fireEvent.click(screen.getByText('导演规划 · 2 段'));
  fireEvent.click(screen.getAllByRole('button', { name: '联合返工' })[1]);
  expect(onRewriteSequence).toHaveBeenCalledWith('S2');
  rerender(<ScriptDirectorPlan {...props} disabled />);
  expect(screen.getAllByRole('button', { name: '联合返工' }).every(button => button.hasAttribute('disabled'))).toBe(true);
});
