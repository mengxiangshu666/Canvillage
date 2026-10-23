// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useCallback, useState } from 'react';
import { Wand2 } from 'lucide-react';
import { toast } from 'sonner';

import { completeFreezoneScriptAssetContents } from '@/api/scriptAssetCompletion';
import type {
  FreezoneStoryDirectorPlan,
  FreezoneStoryScriptResult,
  FreezoneStoryScriptRow,
} from '@/api/scriptContract';
import { readUrl } from '@/lib/url-params';
import { useCanvasStore } from '@/stores/canvasStore';

export interface ScriptAssetCompletionButtonProps {
  scriptNodeId: string;
  disabled?: boolean;
}

/**
 * Complete structured asset definitions only. Media generation and paid image
 * calls stay behind the existing asset dialogs; this button never starts them.
 */
export function ScriptAssetCompletionButton({
  scriptNodeId,
  disabled = false,
}: ScriptAssetCompletionButtonProps) {
  const [busy, setBusy] = useState(false);
  const updateNodeData = useCanvasStore((state) => state.updateNodeData);
  const scriptNode = useCanvasStore((state) => state.nodes.find((node) => node.id === scriptNodeId));
  const result = scriptNode?.data?.scriptResult as FreezoneStoryScriptResult | undefined;
  const plan: FreezoneStoryDirectorPlan | null | undefined = result?.director_plan;
  const rows: readonly FreezoneStoryScriptRow[] = result?.rows ?? [];
  const sourceText = typeof scriptNode?.data?.prompt === 'string' ? scriptNode.data.prompt : '';
  const model = typeof scriptNode?.data?.model === 'string' ? scriptNode.data.model : null;
  const run = useCallback(async () => {
    const project = readUrl().project;
    if (!project || !plan?.asset_contents?.length) return;
    setBusy(true);
    try {
      const result = await completeFreezoneScriptAssetContents(project, {
        directorPlan: plan,
        rows: [...rows],
        sourceText,
        model,
      });
      const completed = result.receipts.filter((receipt) => receipt.status === 'completed');
      const failed = result.receipts.filter((receipt) => receipt.status === 'failed');
      const node = useCanvasStore.getState().nodes.find((item) => item.id === scriptNodeId);
      const current = node?.data?.scriptResult as FreezoneStoryScriptResult | undefined;
      if (current) {
        updateNodeData(scriptNodeId, {
          scriptResult: { ...current, director_plan: result.director_plan },
          scriptDirectorPlanNeedsSync: false,
          scriptContractReport: null,
        });
      }
      if (completed.length > 0) toast.success(`已补全 ${completed.length} 个资产设定；不会自动出图`);
      if (failed.length > 0) {
        toast.error(`${failed.length} 个资产补全失败：${failed.map((item) => item.asset_key).join('、')}`);
      }
      if (completed.length === 0 && failed.length === 0) toast.success('资产设定已经完整，无需补全');
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }, [model, plan, rows, scriptNodeId, sourceText, updateNodeData]);

  if (!plan?.asset_contents?.length) return null;
  return (
    <div className="flex items-center justify-between gap-2 rounded-[8px] border border-white/[0.08] px-2 py-1.5">
      <span className="text-[11px] leading-relaxed text-text-muted">
        结构化资产 {plan.asset_contents.length} 项；只补字段，不出图、不扣媒体费。
      </span>
      <button
        type="button"
        disabled={disabled || busy}
        onClick={() => void run()}
        className="nodrag inline-flex shrink-0 items-center gap-1 rounded-[7px] bg-white/[0.12] px-2 py-1 text-[11px] text-text-dark transition-colors hover:bg-white/[0.2] disabled:cursor-not-allowed disabled:opacity-45"
      >
        <Wand2 className={`h-3 w-3 ${busy ? 'animate-pulse' : ''}`} />
        {busy ? '补全中' : '补全设定'}
      </button>
    </div>
  );
}
