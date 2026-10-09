// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { EmptyCanvasStart } from './EmptyCanvasStart';

const translations: Record<string, string> = {
  'canvas.emptyHintBeforeTab': '点按',
  'canvas.emptyHintAfterTab': '键或双击鼠标新建节点',
};

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => translations[key] ?? key,
  }),
}));

describe('EmptyCanvasStart', () => {
  it('keeps one non-blocking hint on an empty canvas', () => {
    const { container } = render(<EmptyCanvasStart />);

    expect(screen.getByText(/键或双击鼠标新建节点/)).toBeInTheDocument();
    expect(container.firstElementChild?.className).toContain('pointer-events-none');
  });

  it('does not bring back the centered starter-route card', () => {
    render(<EmptyCanvasStart />);

    // 2026-09-30 用户反馈「不要这个卡片，太影响体验了」——这四条起手骨架和
    // 「浏览全部起步路线」都不该再出现在画布正中；入口留在右下角 dock。
    expect(screen.queryByText('从一条起步路线开始')).not.toBeInTheDocument();
    expect(screen.queryByText('原创竖屏短片')).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: '浏览全部起步路线' }),
    ).not.toBeInTheDocument();
  });
});
