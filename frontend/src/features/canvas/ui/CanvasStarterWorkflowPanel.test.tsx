// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { CanvasStarterWorkflowPanel } from './CanvasStarterWorkflowPanel';

describe('CanvasStarterWorkflowPanel', () => {
  it('filters templates by creative intent and inserts the selected local graph', async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    render(<CanvasStarterWorkflowPanel onSelect={onSelect} onClose={vi.fn()} />);

    expect(screen.getByText('产品特写展示')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '动作重绘' }));

    expect(screen.getByText('源视频动作重绘')).toBeInTheDocument();
    expect(screen.queryByText('产品特写展示')).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /源视频动作重绘/ }));
    expect(onSelect).toHaveBeenCalledWith('motion-reference-redraw');
  });

  it('lets a long model hint wrap instead of squeezing the title into a vertical column', () => {
    render(<CanvasStarterWorkflowPanel onSelect={vi.fn()} onClose={vi.fn()} />);

    // 这张卡的徽标是全表最长的（21 字）：此前它把标题压到只剩一个字宽 → 中文竖排。
    const title = screen.getByText('故事连续性起步骨架');
    const hint = screen.getByText('起步骨架；先完成镜头合同，再选择媒体模型');
    const header = title.parentElement;

    expect(header?.className).toContain('flex-wrap');
    // truncate 让标题保持单行（配 min-w-0 才能在空间不足时省略而不是换行）。
    expect(title.className).toContain('truncate');
    expect(title.className).toContain('min-w-0');
    expect(hint.className).toContain('shrink-0');
  });

  it('shows the generated community recipes in their own category', async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    render(<CanvasStarterWorkflowPanel onSelect={onSelect} onClose={vi.fn()} />);

    // 「全部」里就有：社区配方排在人工骨架前面（多数人真这么搭 → 先给）。
    const recipeCard = screen.getByText(/^社区高频组合 · imageGenNode \+ videoNode$/);
    expect(recipeCard).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: '社区高频组合' }));
    expect(recipeCard).toBeInTheDocument();
    // 分类筛选是真的在筛：切到「短剧叙事」之后社区配方不该还在。
    await user.click(screen.getByRole('button', { name: '短剧叙事' }));
    expect(screen.queryByText(/^社区高频组合 · /)).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: '社区高频组合' }));
    await user.click(screen.getByText(/^社区高频组合 · imageGenNode \+ videoNode$/));
    expect(onSelect).toHaveBeenCalledWith('community-image-video');
  });

  it('inserts and deletes saved user templates from the personal library', async () => {
    const user = userEvent.setup();
    const onSelectUserTemplate = vi.fn();
    const onDeleteUserTemplate = vi.fn();
    render(
      <CanvasStarterWorkflowPanel
        onSelect={vi.fn()}
        onClose={vi.fn()}
        userTemplates={[{
          id: 'ct_1',
          title: '雨夜追逐三镜',
          description: '两个生成节点和一个合成节点',
          node_count: 3,
          edge_count: 2,
          created_at: '2026-09-17T00:00:00Z',
          updated_at: '2026-09-17T00:00:00Z',
        }]}
        onSelectUserTemplate={onSelectUserTemplate}
        onDeleteUserTemplate={onDeleteUserTemplate}
      />,
    );

    await user.click(screen.getByRole('button', { name: '我的模板 1' }));
    expect(screen.getByText('雨夜追逐三镜')).toBeInTheDocument();
    expect(screen.getByText('3 个节点 · 2 条连线')).toBeInTheDocument();

    const templateButton = screen.getByText('雨夜追逐三镜').closest('button');
    expect(templateButton).not.toBeNull();
    await user.click(templateButton as HTMLButtonElement);
    expect(onSelectUserTemplate).toHaveBeenCalledWith('ct_1');

    await user.click(screen.getByRole('button', { name: '删除模板 雨夜追逐三镜' }));
    expect(onDeleteUserTemplate).toHaveBeenCalledWith('ct_1');
  });
});
