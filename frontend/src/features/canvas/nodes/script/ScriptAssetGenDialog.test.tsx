import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { ScriptAssetGenDialog } from './ScriptAssetGenDialog';
import { collectScriptAssetLedger } from './scriptAssets';

vi.mock('@/features/canvas/ui/DirectModelPicker', () => ({
  DirectModelPicker: () => <span>test-image</span>,
}));

describe('unified asset image view control', () => {
  const ledger = collectScriptAssetLedger([{ scene_tags: '屋顶', prop_tags: '滑板' }], new Map());
  const props = {
    open: true, ledger, model: 'test-image', aspectKey: 'auto', viewMode: 'multi_view' as const,
    onModelChange: vi.fn(), onAspectChange: vi.fn(), onCancel: vi.fn(), onConfirm: vi.fn(),
  };

  it('is available without characters and switches the whole asset specification', () => {
    const onViewModeChange = vi.fn();
    const onCreateOnly = vi.fn();
    const { rerender } = render(<ScriptAssetGenDialog {...props} onViewModeChange={onViewModeChange} onCreateOnly={onCreateOnly} />);
    expect(screen.getByRole('group', { name: '资产图视图' })).toBeVisible();
    expect(screen.queryByText('角色模板')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '多视图' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByText(/场景空镜全景与俯视拓扑/)).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: '单视图' }));
    expect(onViewModeChange).toHaveBeenCalledWith('single_view');
    rerender(<ScriptAssetGenDialog {...props} viewMode="single_view" onViewModeChange={onViewModeChange} onCreateOnly={onCreateOnly} />);
    expect(screen.getByRole('button', { name: '单视图' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByText(/场景单幅空景；道具单幅主视图/)).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: '全选' }));
    fireEvent.click(screen.getByRole('button', { name: '仅建节点' }));
    expect(onCreateOnly).toHaveBeenCalledWith(ledger.all.map(asset => asset.id));
  });

  it('does not change the specification while the script is busy', () => {
    const onViewModeChange = vi.fn();
    render(<ScriptAssetGenDialog {...props} busy onViewModeChange={onViewModeChange} />);
    expect(screen.getByRole('button', { name: '多视图' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '单视图' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: '单视图' }));
    expect(onViewModeChange).not.toHaveBeenCalled();
  });
});
