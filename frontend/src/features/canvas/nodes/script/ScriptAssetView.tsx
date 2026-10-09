// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import type { FreezoneStoryScriptRow } from '@/api/ops';
import { useState } from 'react';
import { ScanEye } from 'lucide-react';
import { ScriptAssetReview } from './ScriptAssetReview';
import { isRenderableImageSrc, resolveImageDisplayUrl } from '@/features/canvas/application/imageData';
import {
  SCRIPT_ASSET_ROLE_LABEL,
  SCRIPT_ASSET_ROLE_ORDER,
  assetContributesAsReference,
  describeMissingAssets,
  useScriptAssetLedger,
  type ScriptAsset,
  type ScriptAssetLedger,
  type ScriptAssetRole,
} from './scriptAssets';

/**
 * 资产视图：把分镜行里出现过的角色 / 场景 / 道具按名聚合成一张**资产台账**。
 *
 * **对齐说明（2026-09-13 复核后重写）**：LibTV 侧对应的不是「视图 switch 的一个分支」，
 * 而是 script-v2 的**流水线步骤 `prepare-assets`** —— `case"prepare-assets":return jsx(or,…)`，
 * 全 chunk 搜 `case"asset"` 零命中（先前把这点当成了「没有渲染体」，是错的）。
 * 它的资产台账三族 `ALL_ASSET_ROLE_KEYS = ["characters","scenes","props"]` 我们已经齐了：
 * 角色来自 `character_N` 三连列、场景来自 `scene_tags`、道具来自 `prop_tags`。
 *
 * **2026-09-16 起读台账而不是三个 `collect*`**：原先每次渲染现算现扔，没有 id、没有状态，
 * 于是「生成过一张资产图」无处安放。现在卡片的数据来自 {@link ScriptAssetLedger}，它能认回
 * 画布上已生成的资产图，也会把「一张图都没有」的资产单独列成缺口。还缺的操作面
 * （待生成占位、批量生成、个人库）见第三 / 第四刀。
 *
 * 用途：开拍前核对「这个角色在哪几镜出现、有没有图」，避免角色图缺项直接进生成。
 */

export interface ScriptAssetViewProps {
  rows: FreezoneStoryScriptRow[];
  /**
   * 资产台账。调用方（ScriptNode）已经算过一份就直接传进来 —— 两边各算一份的话，
   * 「生成分镜」用台账 A、这里显示台账 B，资产图出图后会出现一边认到一边没认到。
   */
  ledger?: ScriptAssetLedger;
}

export function ScriptAssetView({ rows, ledger }: ScriptAssetViewProps) {
  const [reviewId, setReviewId] = useState<string | null>(null);
  // 不传台账也要能独立渲染（单测 / 别的入口），所以这里有个内部兜底。
  const computed = useScriptAssetLedger(null, rows);
  const activeLedger = ledger ?? computed;
  const reviewedAsset = activeLedger.all.find(asset => asset.id === reviewId);
  if (rows.length === 0) {
    return (
      <div className="flex h-full items-center justify-center text-[12px] text-text-muted">
        还没有分镜行
      </div>
    );
  }
  // 缺口摘要在最上面：它是这个视图真正要回答的问题（「开拍前还差几张图」），
  // 而不是三族各自的计数。口径必须与生成分镜的 `ScriptAssetPreflight` 相同：
  // 场景行里借来的整镜参考帧只是预览，不能作为场景设定图传给下游。
  const missing = activeLedger.all.filter((entry) => !assetContributesAsReference(entry));

  return (
    <div className="ui-scrollbar flex h-full w-full flex-col gap-3 overflow-auto p-1">
      {reviewedAsset?.imageUrl && <ScriptAssetReview key={`${reviewedAsset.id}-${reviewedAsset.generatedOwnerId}-${reviewedAsset.imageUrl}-${reviewedAsset.revision}-${reviewedAsset.contentHash}`} asset={reviewedAsset} onClose={() => setReviewId(null)} />}
      {missing.length > 0 && (
        <p className="rounded-[8px] bg-amber-400/[0.08] px-2 py-1.5 text-[11px] text-amber-100/85">
          {`还有 ${describeMissingAssets(missing)}没有可用的资产图（共 ${missing.length} 项）。`}
        </p>
      )}
      {SCRIPT_ASSET_ROLE_ORDER.map((role) => (
        <ScriptAssetSection
          key={role}
          role={role}
          entries={assetsOfRole(activeLedger, role)}
          onReview={setReviewId}
          emptyHint={
            role === 'character'
              ? '分镜行里还没有填角色'
              : role === 'scene'
                ? '分镜行里还没有填场景标签'
                : '分镜行里还没有填道具标签'
          }
        />
      ))}
    </div>
  );
}

function assetsOfRole(ledger: ScriptAssetLedger, role: ScriptAssetRole): ScriptAsset[] {
  return role === 'character' ? ledger.characters : role === 'scene' ? ledger.scenes : ledger.props;
}

function ScriptAssetSection({
  role,
  emptyHint,
  entries,
  onReview,
}: {
  role: ScriptAssetRole;
  emptyHint: string;
  entries: ScriptAsset[];
  onReview: (id: string) => void;
}) {
  const missingCount = entries.filter((entry) => !assetContributesAsReference(entry)).length;
  return (
    <section className="flex flex-col gap-1.5">
      <header className="flex items-center gap-2 px-0.5 text-[11px] font-semibold tracking-wide text-text-muted/90">
        {SCRIPT_ASSET_ROLE_LABEL[role]}
        <span className="font-normal text-text-muted/60">{entries.length}</span>
        {missingCount > 0 && (
          <span className="font-normal text-amber-200/70">{`待补 ${missingCount}`}</span>
        )}
      </header>
      {entries.length === 0 ? (
        <p className="px-0.5 text-[11px] text-text-muted/60">{emptyHint}</p>
      ) : (
        // 两列网格：资产是「看一眼有没有缺图」的活儿，单列长列表要一直滚。
        <ul
          className="grid gap-1.5"
          style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(210px, 1fr))' }}
        >
          {entries.map((entry) => (
            <li
              key={entry.id}
              className="flex min-w-0 items-start gap-2 rounded-[10px] bg-white/[0.03] p-2 transition-colors hover:bg-white/[0.055]"
            >
              {entry.imageUrl && isRenderableImageSrc(entry.imageUrl) ? (
                <img
                  src={resolveImageDisplayUrl(entry.imageUrl)}
                  alt=""
                  className="h-12 w-12 shrink-0 rounded-[8px] object-cover"
                  draggable={false}
                />
              ) : (
                <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-[8px] bg-black/25 text-[10px] text-text-muted/45">
                  无图
                </div>
              )}
              <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                <span className="truncate text-[12px] font-medium text-text-dark" title={entry.name}>
                  {entry.name}
                </span>
                {entry.imageSource === 'generated' && entry.imageUrl && <div className="flex items-center gap-1 text-[11px]"><span>{entry.visualReview?.status === 'passed' ? '人工画面确认' : entry.visualReview?.status === 'blocked' ? '画面需重做' : '画面未审看'}</span><button type="button" title="审看资产画面" aria-label={`审看 ${entry.name}`} onClick={() => onReview(entry.id)} className="nodrag p-1"><ScanEye size={14} /></button></div>}
                {entry.description.length > 0 && (
                  <span
                    className="line-clamp-2 text-[11px] leading-[1.4] text-text-muted"
                    title={entry.description}
                  >
                    {entry.description}
                  </span>
                )}
                <span className="truncate text-[11px] text-text-muted/70">
                  {`出现镜次：${entry.shotNumbers.join('、') || '-'}`}
                </span>
                {(entry.requiredViews ?? []).length > 0 && (
                  <span
                    className={`truncate text-[10px] ${(entry.missingViews ?? []).length > 0 ? 'text-amber-200/80' : 'text-emerald-200/70'}`}
                    title={`需要：${(entry.requiredViews ?? []).join('、')}；已验收：${(entry.availableViews ?? []).join('、') || '未登记'}；计划：${(entry.plannedViews ?? []).join('、') || '未登记'}`}
                  >
                    {(entry.missingViews ?? []).length > 0
                      ? `待核对视图：${(entry.missingViews ?? []).join('、')}`
                      : (entry.availableViews ?? []).length > 0
                        ? '视图已验收'
                        : '计划视图待核对'}
                  </span>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
