import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { ScriptAssetReview } from './ScriptAssetReview';
import { collectScriptAssetLedger } from './scriptAssets';

describe('asset review controls', () => {
  it('requires the actual image to load and preserves failed-image state', () => {
    const asset = { ...collectScriptAssetLedger([{ prop_tags: '滑板' }], new Map()).props[0], imageUrl: 'https://example.test/board.png', generatedNodeId: 'asset' };
    render(<ScriptAssetReview asset={asset} onClose={vi.fn()} />);
    const accept = screen.getByRole('button', { name: '确认画面' });
    expect(accept).toBeDisabled();
    fireEvent.load(screen.getByRole('img', { name: '滑板' }));
    expect(accept).toBeEnabled();
    for (const name of ['无随机噪点、压缩块、摩尔纹', '身份与风格一致', '结构和材质细节正确']) expect(screen.getByRole('checkbox', { name })).not.toBeChecked();
    fireEvent.error(screen.getByRole('img', { name: '滑板' }));
    expect(accept).toBeDisabled();
    expect(screen.getByRole('alert')).toHaveTextContent('图片加载失败');
  });
});
