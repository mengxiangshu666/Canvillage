// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import type { FreezoneStoryScriptRow } from '@/api/ops';
import { isRenderableImageSrc, resolveImageDisplayUrl } from '@/features/canvas/application/imageData';
import { rowCharacters, rowImagePrompt, scriptRowShotNumber } from './scriptViews';
import { ScriptRoughPreview } from './ScriptRoughPreview';
import { useShallow } from 'zustand/react/shallow';
import { useCanvasStore } from '@/stores/canvasStore';
import type { ScriptAssetLedger } from './scriptAssets';
import { scriptPreviewFrames, type ScriptPreviewFrame } from './scriptPreviewFrames';
import { scriptPreviewVideos } from './scriptPreviewVideos';

/**
 * 创意视图：一镜一卡。
 *
 * **来源要写清楚**：创意视图不是 script-v2 那一代的（script-v2 的默认双视图是
 * 「表格 + 资产」），它属于**老 script 节点** —— `getDefaultScriptViews()` 才是
 * `[table, creative]`。所以「抄创意视图」等于抄上一代节点，不要与我们对齐 script-v2
 * 的那部分（列集、shotColumns、toolbar）混为一谈。
 *
 * LibTV 那张卡的真实规格（`0o6g…` / `15epcn…` chunk，已核）：
 * 网格 `repeat(auto-fill, 220px)`，画面区 `aspect-[4/3]`（**4:3，不是 16:9**），
 * 左上可选勾选框、镜号胶囊，右上 `durationSeconds.toFixed(2)` + 秒，
 * 无图时放 32px 图标 + `scriptNoImageLabel`；卡片下半依次是角色名 chip、
 * `plotDescription` 三行截断、`shotSize · emotion` 一行、底部署名 `scriptSceneLabel{镜号}`。
 *
 * 我们的差异（**不是缺陷，是没照抄的部分**）：画面区用 16:9（与节点里其他画面区一致）、
 * 卡片 228px、无勾选框（我们没有「按行批量」这个入口）、底部署名换成了角色芯片区、
 * 额外多渲染了分镜提示词与对白（LibTV 这张卡不显示提示词，它在「查看提示词」弹层里）。
 */

export interface ScriptCreativeViewProps {
  rows: FreezoneStoryScriptRow[];
  scriptNodeId?: string;
  ledger?: ScriptAssetLedger;
}

export function ScriptCreativeView({ rows, scriptNodeId = '', ledger }: ScriptCreativeViewProps) {
  const graph = useCanvasStore(useShallow(state => ({ nodes: state.nodes, edges: state.edges })));
  const frames = scriptPreviewFrames(scriptNodeId, rows, graph, ledger);
  const videos = scriptPreviewVideos(scriptNodeId, rows, graph);
  if (rows.length === 0) {
    return (
      <div className="flex h-full items-center justify-center text-[12px] text-text-muted">
        还没有分镜行
      </div>
    );
  }

  return (
    <div className="ui-scrollbar h-full w-full overflow-auto p-1">
      <ScriptRoughPreview rows={rows} frames={frames} videos={videos} />
      <div
        className="grid gap-2"
        style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(228px, 1fr))' }}
      >
        {rows.map((row, index) => (
          <ScriptCreativeCard
            key={`${scriptRowShotNumber(row, index)}-${index}`}
            row={row}
            index={index}
            frame={frames[index]}
          />
        ))}
      </div>
    </div>
  );
}

function ScriptCreativeCard({
  row,
  index,
  frame,
}: {
  row: FreezoneStoryScriptRow;
  index: number;
  frame: ScriptPreviewFrame;
}) {
  const shotNumber = scriptRowShotNumber(row, index);
  const characters = rowCharacters(row);
  const prompt = rowImagePrompt(row);
  const thumbUrl = frame.url;
  const duration = row.duration == null ? '' : String(row.duration).trim();
  const shotSize = typeof row.shot === 'string' ? row.shot.trim() : '';
  const emotion = typeof row.emotion === 'string' ? row.emotion.trim() : '';
  const dialogue = typeof row.dialogue === 'string' ? row.dialogue.trim() : '';
  const description =
    typeof row.visual_description === 'string' ? row.visual_description.trim() : '';

  return (
    <article className="flex min-w-0 flex-col overflow-hidden rounded-[10px] bg-white/[0.03] transition-colors hover:bg-white/[0.055]">
      {/* 画面区：16:9，和有参考帧的构图一致 */}
      <div className="relative w-full overflow-hidden bg-black/25" style={{ aspectRatio: '16 / 9' }}>
        {thumbUrl && isRenderableImageSrc(thumbUrl) ? (
          <img
            src={resolveImageDisplayUrl(thumbUrl)}
            alt=""
            className="h-full w-full object-cover"
            draggable={false}
          />
        ) : (
          <div className="flex h-full w-full items-center justify-center text-[11px] text-text-muted/45">
            {frame.label}
          </div>
        )}
        <span className="absolute left-2 top-2 rounded-[6px] bg-black/55 px-1.5 py-0.5 text-[11px] font-medium text-white/90 backdrop-blur-sm">
          镜 {shotNumber}
        </span>
        {(duration || shotSize) && (
          <span className="absolute bottom-2 right-2 flex items-center gap-1 rounded-[6px] bg-black/55 px-1.5 py-0.5 text-[11px] text-white/85 backdrop-blur-sm">
            {duration && <span>{duration}s</span>}
            {duration && shotSize && <span className="text-white/40">·</span>}
            {shotSize && <span className="truncate">{shotSize}</span>}
          </span>
        )}
      </div>

      {/* 信息条 */}
      <div className="flex min-w-0 flex-col gap-1.5 p-2">
        {thumbUrl && <p className="text-[11px] text-text-muted">{frame.label}</p>}
        {description.length > 0 && (
          <p
            className="line-clamp-3 text-[12px] leading-[1.5] text-text-dark/90"
            title={description}
          >
            {description}
          </p>
        )}
        {prompt.length > 0 && (
          <p
            className="line-clamp-3 rounded-[6px] bg-black/20 px-1.5 py-1 text-[11px] leading-[1.45] text-text-muted"
            title={`分镜提示词：${prompt}`}
          >
            {prompt}
          </p>
        )}
        {characters.length > 0 && (
          <div className="flex flex-wrap gap-1">
            {characters.map((character) => (
              <span
                key={character.slot}
                className="inline-flex items-center gap-1 rounded-full bg-white/[0.07] py-0.5 pl-0.5 pr-1.5 text-[11px] text-text-dark/90"
              >
                {character.imageUrl && isRenderableImageSrc(character.imageUrl) ? (
                  <img
                    src={resolveImageDisplayUrl(character.imageUrl)}
                    alt=""
                    className="h-4 w-4 rounded-full object-cover"
                    draggable={false}
                  />
                ) : null}
                <span className="max-w-[96px] truncate">{character.name || `角色${character.slot}`}</span>
              </span>
            ))}
          </div>
        )}
        {(emotion || dialogue) && (
          <div className="flex min-w-0 flex-col gap-0.5 text-[11px] leading-[1.45] text-text-muted">
            {emotion && <span className="truncate">情绪 · {emotion}</span>}
            {dialogue && (
              <span className="line-clamp-2 whitespace-pre-wrap break-words">对白 · {dialogue}</span>
            )}
          </div>
        )}
      </div>
    </article>
  );
}
