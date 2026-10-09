// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useEffect, useState } from "react";
import { ShieldCheck, Stamp } from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  normalizeCopyrightChain,
  normalizeProtectionType,
  readDefaultCopyrightName,
  writeDefaultCopyrightName,
} from "@/features/canvas/domain/nodeRights";
import { useCanvasStore } from "@/stores/canvasStore";

/**
 * 画布右键菜单用它把「署名与保护」转发给具体节点：右击菜单由 Canvas 统一渲染，
 * 编辑框状态却挂在节点内部的角标里，中间靠这个事件搭桥。
 */
export const NODE_RIGHTS_EDIT_EVENT = "village:node-rights-edit";

export function requestNodeRightsEdit(nodeId: string): void {
  document.dispatchEvent(
    new CustomEvent(NODE_RIGHTS_EDIT_EVENT, { detail: { nodeId } }),
  );
}

/**
 * 生成物权属角标：署名 + 保护策略，点开可就地编辑。
 *
 * 挂在节点内容框左下角，与「导演合成」角标（左上）、分辨率角标（右上）错开。
 * 未署名且未加保护时不渲染角标本体，只在节点被选中时留一个「＋权属」入口，
 * 免得每个节点都挂一条空条目。
 *
 * 写回走 `updateNodeData`，与画布整包 PUT 一致；空署名写 `null` 表示「移除署名」。
 */
export function NodeRightsBadge({
  nodeId,
  data,
  placement = "bottom-left",
}: {
  nodeId: string;
  data: Record<string, unknown>;
  /** 视频节点左下角放相册角标，权属改挂右上。 */
  placement?: "bottom-left" | "top-right";
}) {
  const { t } = useTranslation();
  const updateNodeData = useCanvasStore((state) => state.updateNodeData);
  const selected = useCanvasStore(
    (state) => state.nodes.find((node) => node.id === nodeId)?.selected === true,
  );

  const chain = normalizeCopyrightChain(data.copyrightChain);
  const protection = normalizeProtectionType(data.protectionType);
  const hasRights = chain !== null || protection !== null;

  const [isEditing, setIsEditing] = useState(false);
  const [draftName, setDraftName] = useState(chain?.name ?? "");
  // 只在挂载时读一次：本机默认署名是「上次填过的名字」，编辑期间不需要跟着变。
  const [defaultName] = useState(() => readDefaultCopyrightName());

  // 外部改动（远端同步、撤销）后重开编辑框时以节点当前值为准。
  useEffect(() => {
    if (!isEditing) {
      setDraftName(chain?.name ?? "");
    }
  }, [chain?.name, isEditing]);

  // 节点失选或已带权属时收起编辑框，避免编辑框浮在无人关注的节点上。
  useEffect(() => {
    if (!selected) {
      setIsEditing(false);
    }
  }, [selected]);

  // 右键菜单「署名与保护」：只认自己那份 nodeId 的请求。
  useEffect(() => {
    const onRequest = (event: Event) => {
      const detail = (event as CustomEvent<{ nodeId?: string }>).detail;
      if (detail?.nodeId === nodeId) {
        setIsEditing(true);
      }
    };
    document.addEventListener(NODE_RIGHTS_EDIT_EVENT, onRequest);
    return () => document.removeEventListener(NODE_RIGHTS_EDIT_EVENT, onRequest);
  }, [nodeId]);

  // 右键菜单可以在节点未选中时触发；编辑期间不受"未选中即收起"影响。
  if (!hasRights && !selected && !isEditing) {
    return null;
  }

  const commitName = () => {
    const name = draftName.trim();
    if (name) {
      writeDefaultCopyrightName(name);
      updateNodeData(nodeId, {
        copyrightChain: { name, uuid: chain?.uuid ?? "" },
      });
    } else {
      updateNodeData(nodeId, { copyrightChain: null });
    }
    setIsEditing(false);
  };

  const toggleProtection = () => {
    updateNodeData(nodeId, {
      protectionType: protection ? null : "watermark",
    });
  };

  return (
    <div
      className={`absolute z-10 flex max-w-[calc(100%-1rem)] flex-col gap-1.5 ${
        placement === "top-right"
          ? "right-2 top-2 items-end"
          : "bottom-2 left-2 items-start"
      }`}
    >
      {isEditing ? (
        <div
          className="nodrag nowheel flex w-[220px] flex-col gap-2 rounded-md border border-white/12 bg-black/85 p-2.5 text-[11px] text-white/85 shadow-[0_10px_28px_rgba(0,0,0,0.45)] backdrop-blur"
          onClick={(event) => event.stopPropagation()}
          onPointerDown={(event) => event.stopPropagation()}
        >
          <label className="text-white/55" htmlFor={`node-rights-name-${nodeId}`}>
            {t("node.rights.nameLabel", { defaultValue: "署名" })}
          </label>
          <input
            id={`node-rights-name-${nodeId}`}
            className="nodrag w-full rounded border border-white/15 bg-white/[0.06] px-2 py-1 text-[11px] text-white outline-none placeholder:text-white/35 focus:border-sky-300/50"
            value={draftName}
            placeholder={t("node.rights.namePlaceholder", { defaultValue: "例如：村长工作室" })}
            onChange={(event) => setDraftName(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                commitName();
              }
              if (event.key === "Escape") {
                event.preventDefault();
                setIsEditing(false);
              }
            }}
          />
          {defaultName && defaultName !== draftName.trim() ? (
            <button
              type="button"
              className="nodrag self-start rounded border border-dashed border-white/20 px-2 py-1 text-left text-white/55 transition-colors hover:border-white/40 hover:text-white/85"
              onClick={() => setDraftName(defaultName)}
            >
              {t("node.rights.useDefault", {
                defaultValue: "用上次的署名：{{name}}",
                name: defaultName,
              })}
            </button>
          ) : null}
          <button
            type="button"
            className="nodrag inline-flex items-center gap-1.5 rounded border border-white/15 px-2 py-1 text-left text-white/80 transition-colors hover:border-white/30 hover:text-white"
            onClick={toggleProtection}
          >
            <ShieldCheck className="size-3.5 shrink-0" />
            {protection
              ? t("node.rights.protectionOn", { defaultValue: "已加保护水印（点击移除）" })
              : t("node.rights.protectionOff", { defaultValue: "加保护水印" })}
          </button>
          <div className="flex items-center justify-end gap-1.5">
            <button
              type="button"
              className="nodrag rounded px-2 py-1 text-white/55 transition-colors hover:text-white/85"
              onClick={() => setIsEditing(false)}
            >
              {t("common.cancel", { defaultValue: "取消" })}
            </button>
            <button
              type="button"
              className="nodrag rounded bg-sky-500/85 px-2 py-1 font-medium text-white transition-colors hover:bg-sky-500"
              onClick={commitName}
            >
              {t("common.save", { defaultValue: "保存" })}
            </button>
          </div>
        </div>
      ) : null}

      <div className="flex max-w-full flex-wrap items-center gap-1.5">
        {chain ? (
          <button
            type="button"
            className="nodrag inline-flex max-w-full items-center gap-1.5 rounded-md border border-sky-200/35 bg-black/62 px-2 py-1 text-[11px] font-medium leading-none text-sky-50 shadow-[0_6px_18px_rgba(0,0,0,0.28)] backdrop-blur transition-colors hover:border-sky-200/60"
            title={t("node.rights.creditTooltip", {
              defaultValue: "署名：{{name}}（点击编辑）",
              name: chain.name,
            })}
            onClick={(event) => {
              event.stopPropagation();
              setIsEditing((open) => !open);
            }}
          >
            <Stamp className="size-3.5 shrink-0" />
            <span className="truncate">{chain.name}</span>
          </button>
        ) : null}

        {protection ? (
          <button
            type="button"
            className="nodrag inline-flex items-center gap-1.5 rounded-md border border-emerald-200/35 bg-black/62 px-2 py-1 text-[11px] font-medium leading-none text-emerald-50 shadow-[0_6px_18px_rgba(0,0,0,0.28)] backdrop-blur transition-colors hover:border-emerald-200/60"
            title={t("node.rights.protectionTooltip", { defaultValue: "已加保护：水印（点击编辑）" })}
            onClick={(event) => {
              event.stopPropagation();
              setIsEditing((open) => !open);
            }}
          >
            <ShieldCheck className="size-3.5 shrink-0" />
            <span className="truncate">{t("node.rights.protectionWatermark", { defaultValue: "水印" })}</span>
          </button>
        ) : null}

        {!hasRights && selected ? (
          <button
            type="button"
            className="nodrag inline-flex items-center gap-1.5 rounded-md border border-dashed border-white/25 bg-black/45 px-2 py-1 text-[11px] leading-none text-white/60 backdrop-blur transition-colors hover:border-white/45 hover:text-white/90"
            title={t("node.rights.addTooltip", { defaultValue: "添加署名与保护" })}
            onClick={(event) => {
              event.stopPropagation();
              setIsEditing(true);
            }}
          >
            <Stamp className="size-3.5 shrink-0" />
            <span>{t("node.rights.add", { defaultValue: "＋权属" })}</span>
          </button>
        ) : null}
      </div>
    </div>
  );
}
