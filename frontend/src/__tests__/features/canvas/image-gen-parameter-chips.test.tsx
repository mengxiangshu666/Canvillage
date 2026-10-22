// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { AspectSizeChip } from '@/features/canvas/nodes/ImageGenParameterChips';

const ASPECT_OPTIONS = [
  { value: '16:9', label: '16:9' },
  { value: '1:1', label: '1:1' },
];

afterEach(cleanup);

function renderChip({
  supportedSizes = ['1K', '2K'],
  supportedQualities = ['low', 'medium', 'high', 'auto'],
}: {
  supportedSizes?: string[];
  supportedQualities?: string[];
} = {}) {
  const onChange = vi.fn();
  render(
    <AspectSizeChip
      aspectRatio="16:9"
      aspectOptions={ASPECT_OPTIONS}
      size="2K"
      supportedSizes={supportedSizes}
      quality="high"
      supportedQualities={supportedQualities}
      count={1}
      onChange={onChange}
    />,
  );
  fireEvent.click(screen.getByRole('button', { name: /16:9/ }));
  return onChange;
}

describe('AspectSizeChip', () => {
  it('keeps all common image sizes visible and only changes supported values', () => {
    const onChange = renderChip();

    for (const label of ['画质', '清晰度', '背景', '比例', '生成数量']) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
    for (const label of ['低画质', '标准画质', '高画质', '超高画质', '极致画质']) {
      expect(screen.getByRole('button', { name: label })).toBeInTheDocument();
    }
    expect(screen.getByText('自动')).toHaveAttribute('aria-current', 'true');
    expect(screen.getByRole('button', { name: '超高画质' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '极致画质' })).toBeDisabled();
    expect(screen.queryByRole('button', { name: '保留背景' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '透明背景' })).not.toBeInTheDocument();
    expect(screen.getByText('保留背景')).toBeInTheDocument();
    expect(screen.getByText('透明背景')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '1:2' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '9:21' })).toBeDisabled();

    const size1K = screen.getByRole('button', { name: '1K' });
    const size4K = screen.getByRole('button', { name: '4K' });
    expect(size1K).toBeEnabled();
    expect(size4K).toBeDisabled();
    expect(screen.getByRole('button', { name: '2K' })).toHaveAttribute('aria-pressed', 'true');

    fireEvent.click(size4K);
    fireEvent.click(size1K);

    fireEvent.click(screen.getByRole('button', { name: '2张' }));
    expect(onChange).toHaveBeenNthCalledWith(1, { size: '1K' });
    expect(onChange).toHaveBeenNthCalledWith(2, { count: 2 });
  });

  it('still renders fixed presets when the capability list is empty', () => {
    renderChip({ supportedSizes: [], supportedQualities: [] });

    for (const label of ['1K', '2K', '4K']) {
      expect(screen.getByRole('button', { name: label })).toBeDisabled();
    }
    for (const label of ['低画质', '标准画质', '高画质', '超高画质', '极致画质']) {
      expect(screen.getByRole('button', { name: label })).toBeDisabled();
    }
  });
});
