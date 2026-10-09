// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useEffect, useMemo, useState } from 'react';
import { CheckCheck, Loader2, Sparkles, Square, TriangleAlert } from 'lucide-react';

import { DirectModelPicker } from '@/features/canvas/ui/DirectModelPicker';
import { STORYBOARD_ASPECTS } from '@/features/canvas/domain/storyboardGroup';
import type { ScriptAssetViewMode } from '@/features/canvas/domain/canvasNodes';
import {
  defaultScriptAssetGenerationSelection,
  SCRIPT_ASSET_ASPECT_AUTO,
  SCRIPT_ASSET_VIEW_MODE_OPTIONS,
} from './scriptAssetGen';
import { SCRIPT_NODE_Z } from './scriptNodeLayout';
import {
  SCRIPT_ASSET_ROLE_LABEL,
  SCRIPT_ASSET_ROLE_ORDER,
  type ScriptAsset,
  type ScriptAssetLedger,
} from './scriptAssets';

/**
 * 「生成资产图」确认弹层（对齐 LibTV 的批量生成素材模态框）。
 *
 * LibTV 那边是 `for (const role of ["characters","scenes","props"]) for (const item of assets[role])`
 * 逐个建图节点，复选框默认全选、已完成的跳过、footer 给一个总点数。这里照它的形状来，
 * 但默认勾选按本仓真实语料收窄：
 *
 * - **默认只勾「还没生成过」且值得跨镜复用的资产**。角色 / 道具即使只出现一镜也默认勾；
 *   场景只出现一镜时默认不勾 —— 真实语料里大量“场景”其实是逐镜环境细节，默认全勾会把
 *   5 镜脚本展开成十几张一次性图片。用户仍可在弹层里手动勾选；
 * - **已经有我们自己生成的图的资产默认不勾** —— 重出会覆盖掉用户可能已经挑过的那张；
 * - 已有脚本回填图（角色）或借来的参考帧（场景）算「有图但不是资产图」，默认勾上：
 *   那两张都不是「这个角色 / 场景的概念图」，进不了下游参考图（见 `assetContributesAsReference`）；
 * - 点数是**一张一张**加起来报的，与分镜弹层同一个队列口径（`image_selection`）；
 * - **模型目录没解析出可用模型时禁用提交**，避免把画布上的历史死模型 id 建进新节点。
 */

export interface ScriptAssetGenDialogProps {
  open: boolean;
  /** 台账（三族资产 + 各自有没有我们自己生成的图）。 */
  ledger: ScriptAssetLedger;
  model: string;
  /** 目录里是否有可实际运行的资产图模型；加载中 / 空目录时禁止提交。 */
  modelReady?: boolean;
  aspectKey: string;
  viewMode: ScriptAssetViewMode;
  /** 此刻不该动这个弹层（脚本自己正在重新生成：行随时会被替换）。 */
  busy?: boolean;
  busyReason?: string | null;
  /** 已格式化的单张预估点数；拿不到账单规则时为 null。 */
  priceDisplay?: string | null;
  onModelChange: (next: string) => void;
  onAspectChange: (next: string) => void;
  onViewModeChange: (next: ScriptAssetViewMode) => void;
  onCancel: () => void;
  /** 生成选中的资产图（直接出图）。 */
  onConfirm: (assetIds: string[]) => void;
  /** 只建节点不出图。 */
  onCreateOnly?: (assetIds: string[]) => void;
}

export function ScriptAssetGenDialog({
  open,
  ledger,
  model,
  modelReady = true,
  aspectKey,
  viewMode,
  busy = false,
  busyReason = null,
  priceDisplay = null,
  onModelChange,
  onAspectChange,
  onViewModeChange,
  onCancel,
  onConfirm,
  onCreateOnly,
}: ScriptAssetGenDialogProps) {
  // 默认勾选「还没有我们自己生成的资产图」的那些。弹层每次打开都按当前台账重算，
  // 不用上次的残留选择 —— 脚本可能已经改过了。
  const defaultSelected = useMemo(
    () => new Set(defaultScriptAssetGenerationSelection(ledger.pendingGeneration).map((asset) => asset.id)),
    [ledger],
  );
  const [selected, setSelected] = useState<Set<string>>(defaultSelected);
  useEffect(() => {
    if (open) setSelected(defaultSelected);
  }, [open, defaultSelected]);

  useEffect(() => {
    if (!open) return;
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onCancel();
    };
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, [open, onCancel]);

  if (!open) return null;

  const toggle = (assetId: string) => {
    setSelected((previous) => {
      const next = new Set(previous);
      if (next.has(assetId)) next.delete(assetId);
      else next.add(assetId);
      return next;
    });
  };
  const count = selected.size;
  const allAssetIds = ledger.all.map((asset) => asset.id);
  const allSelected = allAssetIds.length > 0 && allAssetIds.every((assetId) => selected.has(assetId));

  const toggleAll = () => {
    setSelected(() => {
      if (allSelected) return new Set();
      return new Set(allAssetIds);
    });
  };

  return (
    <div
      className="fixed inset-0 flex items-center justify-center bg-black/70 p-6"
      style={{ zIndex: SCRIPT_NODE_Z.storyboardDialog }}
      onClick={(event) => event.stopPropagation()}
    >
      <div className="flex max-h-[86vh] w-[min(520px,92vw)] flex-col gap-4 rounded-[14px] border border-white/[0.1] bg-[#1c1c1e]/98 p-4 text-text-dark shadow-[0_20px_60px_rgba(0,0,0,0.55)] backdrop-blur-xl">
        <header className="flex items-center gap-2">
          <Sparkles className="h-4 w-4 text-text-muted" />
          <span className="text-[13px] font-medium">生成资产图</span>
        </header>

        <p className="text-[12px] leading-relaxed text-text-muted">
          为脚本里的角色 / 场景 / 道具各出一张资产图，落在脚本节点左侧。出图后这些图会成为分镜图的参考图，
          并在提示词里按「图片N」标出谁是谁。
        </p>

        <p className="text-[12px] leading-relaxed text-text-muted/80">
          {viewMode === 'multi_view'
            ? '角色四视图设定表；场景空镜全景与俯视拓扑；道具正面、侧面、背面与细节四格。'
            : '角色正面全身照；场景单幅空景；道具单幅主视图。'}
          比例默认自动：角色 4:3、场景 16:9、道具 1:1。
        </p>

        {ledger.missing.length > 0 && (
          <div className="flex items-start gap-1.5 rounded-[10px] border border-amber-300/20 bg-amber-400/[0.07] p-2.5 text-[12px] leading-relaxed text-amber-100/85">
            <TriangleAlert className="h-3.5 w-3.5 shrink-0 translate-y-0.5" />
            <span>
              {`脚本里有 ${ledger.missing.length} 个角色 / 场景 / 道具没有任何图（既没有脚本回填的，也没有生成过的）。`}
            </span>
          </div>
        )}

        {!modelReady && (
          <div className="flex items-start gap-1.5 rounded-[10px] border border-rose-300/20 bg-rose-400/[0.07] p-2.5 text-[12px] leading-relaxed text-rose-100/85">
            <TriangleAlert className="h-3.5 w-3.5 shrink-0 translate-y-0.5" />
            <span>还没有可用的直连生图模型，请先在模型中心配置后再生成资产图。</span>
          </div>
        )}

        <div className="ui-scrollbar flex min-h-0 flex-1 flex-col gap-3 overflow-auto rounded-[10px] bg-white/[0.03] p-2.5">
          <div className="flex items-center justify-between gap-2 border-b border-white/[0.06] px-0.5 pb-2">
            <span className="text-[11px] text-text-muted/70">
              {ledger.all.length > 0 ? `共 ${ledger.all.length} 个资产，已选 ${count} 个` : '暂无可选资产'}
            </span>
            <button
              type="button"
              onClick={toggleAll}
              disabled={allAssetIds.length === 0 || busy}
              aria-pressed={allSelected}
              className="inline-flex h-7 items-center gap-1.5 rounded-[7px] border border-white/[0.12] px-2 text-[11px] text-text-muted transition-colors hover:border-white/25 hover:bg-white/[0.06] hover:text-text-dark disabled:cursor-not-allowed disabled:opacity-40"
              title={allSelected ? '取消选择全部资产' : '选择全部资产'}
            >
              {allSelected ? <CheckCheck className="h-3.5 w-3.5" /> : <Square className="h-3.5 w-3.5" />}
              {allSelected ? '取消全选' : '全选'}
            </button>
          </div>
          {SCRIPT_ASSET_ROLE_ORDER.map((role) => {
            const assets: ScriptAsset[] =
              role === 'character' ? ledger.characters : role === 'scene' ? ledger.scenes : ledger.props;
            if (assets.length === 0) return null;
            return (
              <section key={role} className="flex flex-col gap-1">
                <h4 className="px-0.5 text-[11px] font-semibold tracking-wide text-text-muted/90">
                  {SCRIPT_ASSET_ROLE_LABEL[role]}
                  <span className="ml-1.5 font-normal text-text-muted/60">{assets.length}</span>
                </h4>
                <ul className="flex flex-col gap-0.5">
                  {assets.map((asset) => (
                    <li key={asset.id}>
                      <label className="flex cursor-pointer items-center gap-2 rounded-[8px] px-1.5 py-1 text-[12px] transition-colors hover:bg-white/[0.05]">
                        <input
                          type="checkbox"
                          className="h-3.5 w-3.5 accent-white"
                          checked={selected.has(asset.id)}
                          onChange={() => toggle(asset.id)}
                        />
                        <span className="min-w-0 flex-1 truncate" title={asset.name}>
                          {asset.name}
                        </span>
                        <span className="shrink-0 text-[11px] text-text-muted/70">
                          {assetStatusLabel(asset)}
                        </span>
                      </label>
                    </li>
                  ))}
                </ul>
              </section>
            );
          })}
          {ledger.all.length === 0 && (
            <p className="px-1 py-2 text-[12px] text-text-muted/70">
              脚本行里还没有角色 / 场景 / 道具标签，先在表里补上再回来。
            </p>
          )}
        </div>

        <div className="flex flex-col gap-3 rounded-[10px] bg-white/[0.03] p-3">
          <label className="flex items-center justify-between gap-3">
            <span className="shrink-0 text-[12px] text-text-muted">图片模型</span>
            <DirectModelPicker
              kind="image"
              value={model}
              onChange={onModelChange}
              ariaLabel="资产图模型"
              className="!h-7"
            />
          </label>
          <div className="flex items-start justify-between gap-3">
            <span className="shrink-0 pt-1 text-[12px] text-text-muted">资产图</span>
            <div className="flex max-w-[360px] flex-wrap justify-end gap-1" role="group" aria-label="资产图视图">
              {SCRIPT_ASSET_VIEW_MODE_OPTIONS.map((option) => (
                <button
                  key={option.key}
                  type="button"
                  aria-pressed={option.key === viewMode}
                  disabled={busy}
                  title={option.description}
                  onClick={() => onViewModeChange(option.key)}
                  className={`h-7 rounded-[8px] px-2 text-[12px] transition-colors disabled:cursor-not-allowed disabled:opacity-40 ${
                    option.key === viewMode
                      ? 'bg-white/[0.14] text-text-dark'
                      : 'text-text-muted hover:bg-white/[0.06] hover:text-text-dark'
                  }`}
                >
                  {option.label}
                </button>
              ))}
            </div>
          </div>
          <div className="flex items-center justify-between gap-3">
            <span className="shrink-0 text-[12px] text-text-muted">比例</span>
            <div className="flex flex-wrap justify-end gap-1">
              {/* 「自动」在最前：三族各取所需（角色 4:3 / 场景 16:9 / 道具 1:1）。这是默认，
                  也是唯一能同时喂对下游两组参考图的那个（角色要脸、场景要空间）。 */}
              {[{ key: SCRIPT_ASSET_ASPECT_AUTO, label: '自动' }, ...STORYBOARD_ASPECTS].map(
                (option) => (
                  <button
                    key={option.key}
                    type="button"
                    aria-pressed={option.key === aspectKey}
                    onClick={() => onAspectChange(option.key)}
                    className={`h-7 rounded-[8px] px-2 text-[12px] transition-colors ${
                      option.key === aspectKey
                        ? 'bg-white/[0.14] text-text-dark'
                        : 'text-text-muted hover:bg-white/[0.06] hover:text-text-dark'
                    }`}
                  >
                    {option.label}
                  </button>
                ),
              )}
            </div>
          </div>
        </div>

        <footer className="flex items-center justify-between gap-2">
          <span className="text-[12px] text-text-muted">
            {busy
              ? (busyReason ?? '脚本正在生成中，等它结束再生成资产图')
              : count > 0 && priceDisplay
                ? // 点数按**单张**报（价格 hook 按 quantity=1 取）：本弹层里一张资产图就是一张，
                  // 勾 N 个就是 N 倍，不再另算。
                  `将出图 ${count} 张 · 预计消耗 ${scalePriceDisplay(priceDisplay, count)}`
                : ''}
          </span>
          <div className="flex items-center gap-2">
            {onCreateOnly && (
              <button
                type="button"
                className="h-7 rounded-[8px] px-2.5 text-[12px] text-text-muted transition-colors hover:bg-white/[0.06] hover:text-text-dark disabled:opacity-50"
                onClick={() => onCreateOnly([...selected])}
                disabled={busy || !modelReady || count === 0}
              >
                仅建节点
              </button>
            )}
            <button
              type="button"
              className="h-7 rounded-[8px] px-2.5 text-[12px] text-text-muted transition-colors hover:bg-white/[0.06] hover:text-text-dark"
              onClick={onCancel}
            >
              取消
            </button>
            <button
              type="button"
              className="inline-flex h-7 items-center gap-1.5 rounded-[8px] bg-white px-3 text-[12px] font-medium text-bg-dark transition-colors hover:bg-white/90 disabled:opacity-50"
              onClick={() => onConfirm([...selected])}
              disabled={busy || !modelReady || count === 0}
              title={!modelReady ? '请先配置可用的资产图模型' : count === 0 ? '至少选一个资产' : undefined}
            >
              {busy && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
              {`生成资产图${count > 0 ? `（${count}）` : ''}`}
            </button>
          </div>
        </footer>
        {/* 弹层里出图是异步的：建完节点就返回，卡片会在画布左侧自己出图。 */}
        {busy && <p className="text-[11px] text-text-muted/70">{busyReason ?? ''}</p>}
      </div>
    </div>
  );
}

/** 资产卡右侧的状态：已经生成过 / 有脚本回填图（角色）/ 借的参考帧（场景）/ 完全没图。 */
function assetStatusLabel(asset: ScriptAsset): string {
  if (asset.generatedImageState === 'stale') return '旧版本，需重出';
  if (asset.generatedImageState === 'failed' || asset.qaStatus === 'blocked') return '验收未通过';
  if (asset.imageSource === 'generated' && asset.qaStatus === 'unverified') return '待验收';
  if (asset.imageSource === 'generated') return '已生成，画面待核对';
  if (asset.imageSource === 'row') return asset.role === 'character' ? '脚本回填图' : '借参考帧';
  return '无图';
}

/**
 * 单张价格 × 张数。
 *
 * 价格串是后端格式化好的（可能带千分位 / 单位后缀），不做数值解析，只按「纯数字才乘」
 * 处理：解析不出就把单张价原样显示，宁少报不误报一个假的总额。
 */
function scalePriceDisplay(priceDisplay: string, count: number): string {
  const digits = priceDisplay.replace(/[,\s]/g, '');
  if (!/^\d+(\.\d+)?$/.test(digits)) return priceDisplay;
  const scaled = Number(digits) * count;
  return Number.isFinite(scaled) ? String(Number(scaled.toFixed(2))) : priceDisplay;
}

/** 已勾选资产的逐族摘要（日志 / 提示用）。 */
export function describeChosenAssets(assets: readonly ScriptAsset[]): string {
  return SCRIPT_ASSET_ROLE_ORDER.map((role) => {
    const names = assets.filter((asset) => asset.role === role).map((asset) => asset.name);
    if (names.length === 0) return '';
    return `${SCRIPT_ASSET_ROLE_LABEL[role]}：${names.join('、')}`;
  })
    .filter(Boolean)
    .join('；');
}
