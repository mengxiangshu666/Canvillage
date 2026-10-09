// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import {
  CanvasSkillQuickPickPanel,
  rankCanvasQuickPickSkills,
} from '@/features/canvas/ui/CanvasSkillQuickPickPanel';
import type { SkillDefinition } from '@/features/freezone/context/skillRoles';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) =>
      ({
        'viewer.threeD.skillDefinitions.freezone_frame_from_context.name': '从镜头上下文生成分镜',
        'viewer.threeD.skillDefinitions.freezone_frame_from_context.description': '从镜头上下文、草图和参考图生成主线分镜候选。',
      })[key] ?? key,
  }),
}));

const skill = (overrides: Partial<SkillDefinition>): SkillDefinition => ({
  id: 'tool.generic',
  provider: 'tool',
  display_name: '通用工具',
  description: '基础工具能力',
  inputs: [],
  outputs: [],
  ...overrides,
});

const skills = [
  skill({ id: 'tool.style', display_name: '画面风格设计', description: '确定画面风格与光影。' }),
  skill({
    id: 'agent.storyboard',
    provider: 'agent',
    display_name: '分镜导演',
    description: '根据故事推进分镜。',
    capabilities: { can_read_canvas: true, can_propose_canvas_patch: true },
  }),
  skill({ id: 'workflow.audio', provider: 'workflow', display_name: '配音流程', description: '生成配音素材。' }),
];

describe('CanvasSkillQuickPickPanel', () => {
  it('优先排列可作用于画布的技能', () => {
    expect(rankCanvasQuickPickSkills(skills).map((item) => item.id)).toEqual([
      'agent.storyboard',
      'tool.style',
      'workflow.audio',
    ]);
  });

  it('按关键词筛选并把真实技能对象交回画布', async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    render(<CanvasSkillQuickPickPanel skillItems={skills} onSelect={onSelect} onClose={vi.fn()} />);

    await user.type(screen.getByRole('textbox', { name: '搜索画布技能' }), '分镜');
    expect(screen.getByRole('button', { name: '插入技能：分镜导演' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '插入技能：画面风格设计' })).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: '插入技能：分镜导演' }));
    expect(onSelect).toHaveBeenCalledWith(skills[1]);
  });

  it('用中文名称和说明展示后端英文技能，并支持中文搜索', async () => {
    const user = userEvent.setup();
    const englishSkill = skill({
      id: 'freezone.frame_from_context',
      provider: 'freezone_mainline',
      display_name: 'Frame From Context',
      description: 'Render a frame candidate from context.',
    });
    render(
      <CanvasSkillQuickPickPanel
        skillItems={[englishSkill]}
        onSelect={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    expect(screen.getByRole('button', { name: '插入技能：从镜头上下文生成分镜' })).toBeInTheDocument();
    expect(screen.getByText('从镜头上下文、草图和参考图生成主线分镜候选。')).toBeInTheDocument();

    await user.type(screen.getByRole('textbox', { name: '搜索画布技能' }), '主线分镜');
    expect(screen.getByRole('button', { name: '插入技能：从镜头上下文生成分镜' })).toBeInTheDocument();
  });
});
