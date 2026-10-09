// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen } from '@testing-library/react';
import type { ReactElement } from 'react';
import { describe, expect, it, vi } from 'vitest';

import type { FreezoneStoryScriptRow } from '@/api/ops';
import { ScriptResultTable } from '@/features/canvas/nodes/ScriptNode';
import { ScriptAssetView } from '@/features/canvas/nodes/script/ScriptAssetView';
import { ScriptCreativeView } from '@/features/canvas/nodes/script/ScriptCreativeView';
import { scriptFieldSequence } from '@/features/canvas/nodes/script/scriptFields';
import type { ScriptPaidActionGate } from '@/features/canvas/nodes/script/scriptPaidActionGate';
import { ScriptStoryboardDialog } from '@/features/canvas/nodes/script/ScriptStoryboardDialog';
import { ScriptShotVideoDialog } from '@/features/canvas/nodes/script/ScriptShotVideoDialog';
import { ScriptViewSwitcher } from '@/features/canvas/nodes/script/ScriptViewSwitcher';

describe('脚本节点视图切换', () => {
  it('展开后列出四种视图，当前视图带勾选', () => {
    render(<ScriptViewSwitcher value="creative" onChange={() => {}} />);
    // 收起态只显示当前视图名。
    expect(screen.getByRole('button', { name: /创意视图/ })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /创意视图/ }));
    const items = screen.getAllByRole('menuitemradio');
    expect(items.map((item) => item.textContent)).toEqual([
      '脚本视图',
      '创意视图',
      '资产视图',
      '视频素材',
    ]);
    expect(items[1].getAttribute('aria-checked')).toBe('true');
    expect(items[0].getAttribute('aria-checked')).toBe('false');
  });

  it('点选其它视图后回调并收起菜单', () => {
    const onChange = vi.fn();
    render(<ScriptViewSwitcher value="table" onChange={onChange} />);
    fireEvent.click(screen.getByRole('button', { name: /脚本视图/ }));
    fireEvent.click(screen.getByRole('menuitemradio', { name: '资产视图' }));
    expect(onChange).toHaveBeenCalledWith('asset');
    expect(screen.queryByRole('menu')).toBeNull();
  });
});

describe('创意视图', () => {
  it('按行铺卡片，展示镜号、提示词与角色', () => {
    render(
      <ScriptCreativeView
        rows={[
          {
            shot_no: '1',
            duration: '3',
            visual_description: '祠堂开场',
            shot_prompt: '镜头推近祠堂',
            character_1: '阿雀',
            emotion: '警觉',
          },
          { shot_no: '2', visual_description: '收尾' },
        ]}
      />,
    );
    expect(screen.getByText('镜 1')).toBeTruthy();
    expect(screen.getByText('镜 2')).toBeTruthy();
    expect(screen.getByTitle('祠堂开场')).toBeTruthy();
    expect(screen.getByText('镜头推近祠堂')).toBeTruthy();
    expect(screen.getByText('阿雀')).toBeTruthy();
    expect(screen.getByText('情绪 · 警觉')).toBeTruthy();
    // 有画面描述也有分镜提示词时，两张卡各显示一次提示词。
    expect(screen.getByTitle('分镜提示词：镜头推近祠堂')).toBeTruthy();
  });

  it('没有分镜行时给出空态文案', () => {
    render(<ScriptCreativeView rows={[]} />);
    expect(screen.getByText('还没有分镜行')).toBeTruthy();
  });
});

describe('资产视图', () => {
  it('按角色 / 场景 / 道具聚合，并标出出场镜次', () => {
    render(
      <ScriptAssetView
        rows={[
          { shot_no: '1', character_1: '阿雀', scene_tags: '祠堂、黄昏', prop_tags: '玉佩' },
          { shot_no: '2', character_1: '阿雀', scene_tags: '祠堂', prop_tags: '玉佩、青铜钥匙' },
        ]}
      />,
    );
    expect(screen.getByText('角色')).toBeTruthy();
    expect(screen.getByText('场景')).toBeTruthy();
    expect(screen.getByText('道具')).toBeTruthy();
    expect(screen.getByText('阿雀')).toBeTruthy();
    // 阿雀、祠堂、玉佩都出现在第 1、2 镜。
    expect(screen.getAllByText('出现镜次：1、2')).toHaveLength(3);
    expect(screen.getByText('祠堂')).toBeTruthy();
    expect(screen.getByText('黄昏')).toBeTruthy();
    expect(screen.getByText('青铜钥匙')).toBeTruthy();
  });

  it('道具没有图就老实说无图，不借参考帧凑预览', () => {
    render(
      <ScriptAssetView
        rows={[{ shot_no: '1', prop_tags: '玉佩', reference: '/static/frame-1.png' }]}
      />,
    );
    expect(screen.getByText('玉佩')).toBeTruthy();
    // 参考帧是整镜构图，不是「玉佩长什么样」—— 道具卡一律无图占位。
    expect(screen.getByText('无图')).toBeTruthy();
    expect(screen.queryByRole('img')).toBeNull();
  });

  it('场景只有借来的整镜参考帧时仍标成待补，和生成分镜预检保持同一口径', () => {
    render(
      <ScriptAssetView
        rows={[{ shot_no: '1', scene_tags: '祠堂', reference: '/static/frame-1.png' }]}
      />,
    );

    expect(screen.getByText(/还有 1 个场景没有可用的资产图（共 1 项）/)).toBeTruthy();
    expect(screen.getByText('待补 1')).toBeTruthy();
  });

  it('「无」是「本镜没有道具」的占位写法，不能长成一张叫「无」的卡片', () => {
    render(<ScriptAssetView rows={[{ shot_no: '1', prop_tags: '无', scene_tags: '无' }]} />);
    expect(screen.queryByText('无')).toBeNull();
    expect(screen.getByText('分镜行里还没有填道具标签')).toBeTruthy();
  });

  it('没有角色 / 场景 / 道具时给出提示', () => {
    render(<ScriptAssetView rows={[{ shot_no: '1' }]} />);
    expect(screen.getByText('分镜行里还没有填角色')).toBeTruthy();
    expect(screen.getByText('分镜行里还没有填场景标签')).toBeTruthy();
    expect(screen.getByText('分镜行里还没有填道具标签')).toBeTruthy();
  });
});

describe('生成分镜确认弹层', () => {
  // 弹层里的模型选择器读 gate-way 目录（react-query），渲染要带 provider，
  // 否则 useQuery 直接抛 "No QueryClient set"。
  const renderDialog = (element: ReactElement) =>
    render(
      <QueryClientProvider
        client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
      >
        {element}
      </QueryClientProvider>,
    );

  const baseProps = {
    open: true,
    shotCount: 6,
    pendingCount: 6,
    groupLabel: '分镜图 · 天空之跃',
    model: 'direct/image-1',
    aspectKey: '16:9',
    onModelChange: () => {},
    onAspectChange: () => {},
    onCancel: () => {},
    onConfirm: () => {},
  } as const;

  it('首次生成：主按钮是真出图，另有「仅建节点」这个不出图的出口', () => {
    const onConfirm = vi.fn();
    const onCreateOnly = vi.fn();
    renderDialog(
      <ScriptStoryboardDialog
        {...baseProps}
        mode="create"
        priceDisplay="120"
        onConfirm={onConfirm}
        onCreateOnly={onCreateOnly}
      />,
    );
    expect(screen.getByText(/按 6 个分镜/)).toBeTruthy();
    // 报数时把「这一轮真的会提交的张数」写在点数前面 —— 点数按它算出来的。
    expect(screen.getByText(/将出图 6 张 · 预计消耗 120/)).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: '生成分镜' }));
    expect(onConfirm).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole('button', { name: '仅建节点' }));
    expect(onCreateOnly).toHaveBeenCalledTimes(1);
  });

  it('重新生成：只说重跑未出图的，且不再提供「仅建节点」', () => {
    renderDialog(
      <ScriptStoryboardDialog {...baseProps} mode="regenerate" pendingCount={2} />,
    );
    expect(screen.getByText(/未出图或失败的 2 张分镜图重新生成/)).toBeTruthy();
    expect(screen.getByRole('button', { name: '开始生成' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: '仅建节点' })).toBeNull();
  });

  it('分镜行变了：说明会按当前比例重建整组', () => {
    renderDialog(
      <ScriptStoryboardDialog
        {...baseProps}
        mode="regenerate"
        willRebuild
        aspectKey="9:16"
        pendingCount={0}
      />,
    );
    expect(screen.getByText(/分镜行已变化，将重建/)).toBeTruthy();
    expect(screen.getByText(/按 9:16 逐张出图，共 6 张/)).toBeTruthy();
    // 没有要出的图时主按钮不可点。
    expect(
      screen.getByRole('button', { name: '开始生成' }).hasAttribute('disabled'),
    ).toBe(true);
  });

  it('整张都出好了：说明无需重生成，Esc 关闭', () => {
    const onCancel = vi.fn();
    renderDialog(
      <ScriptStoryboardDialog
        {...baseProps}
        mode="regenerate"
        pendingCount={0}
        onCancel={onCancel}
      />,
    );
    expect(screen.getByText('所有分镜图都已出图，无需重新生成。')).toBeTruthy();
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it('脚本自己正在生成时：两个动手按钮禁用并说明原因，取消仍可点', () => {
    // `busy` 此前从未从 ScriptNode 传进来，弹层里的 Loader2 与 disabled 全是死代码。
    const onConfirm = vi.fn();
    const onCancel = vi.fn();
    const onCreateOnly = vi.fn();
    renderDialog(
      <ScriptStoryboardDialog
        {...baseProps}
        mode="create"
        busy
        priceDisplay="120"
        onConfirm={onConfirm}
        onCancel={onCancel}
        onCreateOnly={onCreateOnly}
      />,
    );

    // 原因写在底部，而不是只留一个转圈。
    expect(screen.getByText('脚本正在生成中，等它结束再生成分镜图')).toBeTruthy();
    expect(
      screen.getByRole('button', { name: /生成分镜/ }).hasAttribute('disabled'),
    ).toBe(true);
    expect(screen.getByRole('button', { name: '仅建节点' }).hasAttribute('disabled')).toBe(true);

    // 取消不禁用：关一个本地弹层不该被拦住，否则用户被关在弹层里。
    fireEvent.click(screen.getByRole('button', { name: '取消' }));
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it('付费门禁阻断时只锁出图，仅建节点与取消仍可用', () => {
    const paidActionGate: ScriptPaidActionGate = {
      action: 'storyboard-images',
      allowed: false,
      reason: '先处理「分镜图已与当前脚本脱节」，再出分镜图。',
      blockers: [],
      warnings: [],
    };
    const onConfirm = vi.fn();
    const onCreateOnly = vi.fn();
    renderDialog(
      <ScriptStoryboardDialog
        {...baseProps}
        mode="create"
        paidActionGate={paidActionGate}
        onConfirm={onConfirm}
        onCreateOnly={onCreateOnly}
      />,
    );

    expect(screen.getByRole('alert').textContent).toContain('先处理');
    expect(screen.getByRole('button', { name: '生成分镜' }).hasAttribute('disabled')).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: '仅建节点' }));
    fireEvent.click(screen.getByRole('button', { name: '取消' }));
    expect(onCreateOnly).toHaveBeenCalledTimes(1);
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it('未展开时不渲染', () => {
    render(<ScriptStoryboardDialog {...baseProps} mode="create" open={false} />);
    expect(screen.queryByRole('button', { name: '生成分镜' })).toBeNull();
  });
});

describe('逐镜出视频确认弹层', () => {
  const renderDialog = (element: ReactElement) =>
    render(
      <QueryClientProvider
        client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
      >
        {element}
      </QueryClientProvider>,
    );
  const baseProps = {
    open: true,
    mode: 'create',
    shotCount: 3,
    pendingCount: 3,
    model: 'direct/video-1',
    aspectKey: '16:9',
    onModelChange: () => {},
    onAspectChange: () => {},
    onCancel: () => {},
    onConfirm: () => {},
  } as const;

  it('视频门禁阻断时锁住出片并显示雷达原因', () => {
    const onConfirm = vi.fn();
    renderDialog(
      <ScriptShotVideoDialog
        {...baseProps}
        paidActionGate={{
          action: 'shot-videos',
          allowed: false,
          reason: '先处理「分镜图已与当前脚本脱节」，再出逐镜视频。',
          blockers: [],
          warnings: [],
        }}
        onConfirm={onConfirm}
      />,
    );

    expect(screen.getByRole('alert').textContent).toContain('分镜图已与当前脚本脱节');
    expect(screen.getByRole('button', { name: '逐镜出视频' }).hasAttribute('disabled')).toBe(true);
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it('逐镜体检表把每一镜会被自动填进去的事实摊开，缺项如实计数', () => {
    renderDialog(
      <ScriptShotVideoDialog
        {...baseProps}
        rows={[
          {
            rowKey: 'shot:1',
            shotNumber: '1',
            durationSec: 4,
            generationDurationSec: 5,
            firstFrameUrl: 'frame-0.png',
            camera: '镜头前推',
            promptSource: 'motion',
            dialogue: '',
            audioRoute: 'native',
            issues: [],
          },
          {
            rowKey: 'shot:2',
            shotNumber: '2',
            durationSec: 3,
            generationDurationSec: 5,
            firstFrameUrl: '',
            camera: '',
            promptSource: 'fallback',
            dialogue: '你到底来不来？',
            audioRoute: 'external',
            issues: ['noImage', 'noCamera'],
          },
        ]}
        onConfirm={() => {}}
      />,
    );

    expect(screen.getByText('共 2 镜 · 1 镜需核对')).toBeTruthy();
    // 运镜、音轨、提示词来源三列都读得出来 —— 审核面要能扫，不用逐个点开节点。
    expect(screen.getByText('镜头前推')).toBeTruthy();
    expect(screen.getByText('原生声音')).toBeTruthy();
    expect(screen.getByText('外部配音')).toBeTruthy();
    expect(screen.getByText('缺首帧')).toBeTruthy();
    expect(screen.getByText('未识别摄影安排')).toBeTruthy();
    expect(screen.getByText('画面词')).toBeTruthy();
  });

  it('没有体检数据时不渲染空表', () => {
    renderDialog(<ScriptShotVideoDialog {...baseProps} onConfirm={() => {}} />);
    expect(screen.queryByText('逐镜核对')).toBeNull();
  });

  it('首尾帧推荐缺尾帧时暂停批量生成，并保留仅建节点', () => {
    renderDialog(<ScriptShotVideoDialog {...baseProps} rows={[{
      rowKey: 'shot:1', shotNumber: '1', durationSec: 4, generationDurationSec: 4, firstFrameUrl: 'frame.png',
      camera: '固定', promptSource: 'motion', dialogue: '', audioRoute: 'silent', issues: ['missingLastFrame'],
    }]} onConfirm={() => {}} onCreateOnly={() => {}} />);
    expect(screen.getByText('本镜首尾帧尚未准备齐全或引用已过期')).toBeTruthy();
    expect((screen.getByRole('button', { name: '逐镜出视频' }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole('button', { name: '仅建节点' }) as HTMLButtonElement).disabled).toBe(false);
  });

  it.each(['invalidDuration', 'modelUnsupported'] as const)('准入缺项%s可见提示并保留草稿入口', (issue) => {
    const confirm = vi.fn();
    const create = vi.fn();
    renderDialog(<ScriptShotVideoDialog {...baseProps} rows={[{
      rowKey: 'shot:1', shotNumber: '1', durationSec: null, generationDurationSec: null, firstFrameUrl: 'frame.png',
      camera: '固定', promptSource: 'motion', dialogue: '', audioRoute: 'silent', issues: [issue],
    }]} onConfirm={confirm} onCreateOnly={create} />);
    expect(screen.getByText(issue === 'invalidDuration' ? '缺少明确时长' : '所选模型不支持本镜生成方式、时长或声音方案')).toBeTruthy();
    const generate = screen.getByRole('button', { name: '逐镜出视频' }) as HTMLButtonElement;
    expect(generate.disabled).toBe(true);
    fireEvent.click(generate);
    expect(confirm).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: '仅建节点' }));
    expect(create).toHaveBeenCalledOnce();
  });
});

describe('脚本表格行操作', () => {
  const columns = scriptFieldSequence(1);
  const rows: FreezoneStoryScriptRow[] = [
    { shot_no: '1', visual_description: '开场' },
    { shot_no: '2', visual_description: '收尾' },
  ];
  const makeOperations = (canDelete = true) => ({
    onInsertAfter: vi.fn(),
    onDuplicate: vi.fn(),
    onMove: vi.fn(),
    onDelete: vi.fn(),
    canDelete,
  });

  it('插 / 复制 / 上移 / 下移 / 删各自接上回调，且带上真实行号', () => {
    const operations = makeOperations();
    render(<ScriptResultTable rows={rows} columns={columns} rowOperations={operations} />);

    fireEvent.click(screen.getAllByTitle('在下方插入一行')[0]);
    expect(operations.onInsertAfter).toHaveBeenCalledWith(0);

    fireEvent.click(screen.getAllByTitle('复制这一行（新镜头，新身份）')[1]);
    expect(operations.onDuplicate).toHaveBeenCalledWith(1);

    fireEvent.click(screen.getAllByTitle('上移一位')[1]);
    expect(operations.onMove).toHaveBeenCalledWith(1, -1);

    fireEvent.click(screen.getAllByTitle('下移一位')[0]);
    expect(operations.onMove).toHaveBeenCalledWith(0, 1);

    fireEvent.click(screen.getAllByTitle('删除这一行')[0]);
    expect(operations.onDelete).toHaveBeenCalledWith(0);
  });

  it('首行不能上移、末行不能下移，只剩一行时不能删', () => {
    const operations = makeOperations(false);
    const { unmount } = render(
      <ScriptResultTable rows={rows} columns={columns} rowOperations={operations} />,
    );
    const upButtons = screen.getAllByTitle('上移一位');
    const downButtons = screen.getAllByTitle('下移一位');
    expect(upButtons[0].hasAttribute('disabled')).toBe(true);
    expect(upButtons[1].hasAttribute('disabled')).toBe(false);
    expect(downButtons[0].hasAttribute('disabled')).toBe(false);
    expect(downButtons[1].hasAttribute('disabled')).toBe(true);
    expect(screen.getAllByTitle('删除这一行')[0].hasAttribute('disabled')).toBe(true);
    unmount();

    render(
      <ScriptResultTable rows={[rows[0]]} columns={columns} rowOperations={makeOperations(false)} />,
    );
    expect(screen.getByTitle('上移一位').hasAttribute('disabled')).toBe(true);
    expect(screen.getByTitle('下移一位').hasAttribute('disabled')).toBe(true);
    expect(screen.getByTitle('删除这一行').hasAttribute('disabled')).toBe(true);
  });

  it('不传行操作时整列不渲染', () => {
    render(<ScriptResultTable rows={rows} columns={columns} />);
    expect(screen.queryByTitle('复制这一行（新镜头，新身份）')).toBeNull();
    expect(screen.queryByTitle('删除这一行')).toBeNull();
  });
});
