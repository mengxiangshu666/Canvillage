import { useState } from "react";
import { Braces, Check, Copy, Sparkles } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";

function cliValue(value: string, fallback: string): string {
  const clean = value.trim();
  return clean || fallback;
}

export function AgentCliSkillDialog({
  open,
  onOpenChange,
  projectId,
  canvasId,
  mountedSkillCount,
  totalSkillCount,
  autoMatching,
  onOpenSkillDrawer,
  onOpenSkillStore,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  projectId?: string | null;
  canvasId?: string | null;
  mountedSkillCount: number;
  totalSkillCount: number;
  autoMatching: boolean;
  onOpenSkillDrawer: () => void;
  onOpenSkillStore: () => void;
}) {
  const [copied, setCopied] = useState(false);
  const project = cliValue(projectId || "", "PROJECT_ID");
  const canvas = cliValue(canvasId || "", "CANVAS_ID");
  // Run the relay directly so an Agent shell never opens a visible cmd.exe
  // window for the compatibility .bat wrapper.
  const command = `powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File ".\\village_canvas_cli_hidden.ps1" --json --project "${project}" --canvas "${canvas}" canvas context`;

  const copyCommand = async () => {
    try {
      await navigator.clipboard.writeText(command);
      setCopied(true);
      toast.success("CLI 命令已复制");
      window.setTimeout(() => setCopied(false), 1600);
    } catch {
      toast.error("复制失败，请手动复制命令");
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        data-agent-dialog="v4"
        className="village-agent-dialog-v4 village-agent-dialog-v4--cli village-agent-cli-dialog max-w-[min(720px,calc(100%-1.5rem))] overflow-hidden border-white/[0.1] bg-[#111113]/98 p-0 text-white shadow-[0_28px_100px_rgba(0,0,0,0.62)]"
      >
        <div className="border-b border-white/[0.08] px-5 py-4 pr-14">
          <div className="flex items-center gap-2.5">
            <span className="flex size-8 items-center justify-center rounded-xl border border-cyan-200/15 bg-cyan-200/[0.08] text-cyan-100">
              <Braces className="size-4" />
            </span>
            <div className="min-w-0">
              <DialogTitle className="text-[15px] font-semibold text-white/90">CLI & Skill</DialogTitle>
              <p className="mt-1 text-[11px] text-white/42">让外部 Agent 和工具直接操作当前画布</p>
            </div>
          </div>
        </div>

        <div className="max-h-[min(70vh,620px)] space-y-3 overflow-y-auto p-4">
          <section className="rounded-2xl border border-white/[0.08] bg-white/[0.035] p-3.5">
            <div className="mb-2.5 flex items-center justify-between gap-3">
              <div>
                <div className="text-[12px] font-semibold text-white/82">村长画布 CLI</div>
                <div className="mt-1 text-[11px] text-white/38">真实命令，读取画布上下文并支持节点、任务、工作流操作</div>
              </div>
              <Button type="button" size="sm" variant="ghost" className="shrink-0 rounded-full text-white/65 hover:bg-white/[0.08] hover:text-white" onClick={() => void copyCommand()}>
                {copied ? <Check className="mr-1.5 size-3.5 text-emerald-300" /> : <Copy className="mr-1.5 size-3.5" />}
                {copied ? "已复制" : "复制命令"}
              </Button>
            </div>
            <pre className="overflow-x-auto rounded-xl border border-white/[0.07] bg-black/30 px-3 py-2.5 text-[11px] leading-5 text-cyan-100/80"><code>{command}</code></pre>
            <div className="mt-2.5 flex flex-wrap gap-1.5 text-[10px] text-white/42">
              {['canvas context', 'node list', 'canvas apply', 'workflow runs', 'task stop'].map((item) => (
                <span key={item} className="rounded-full border border-white/[0.07] px-2 py-1">{item}</span>
              ))}
            </div>
          </section>

          <section className="rounded-2xl border border-white/[0.08] bg-white/[0.035] p-3.5">
            <div className="flex items-center gap-2">
              <Sparkles className="size-4 text-amber-200/75" />
              <div className="text-[12px] font-semibold text-white/82">Skill</div>
              <span className="ml-auto text-[10px] text-white/38">{mountedSkillCount} / {totalSkillCount} 已挂载</span>
            </div>
            <p className="mt-1.5 text-[11px] text-white/42">Skill 只负责把专业执行合同交给 Agent，真正的写入、对账和任务控制仍走画布内核。</p>
            <div className="mt-3 flex flex-wrap gap-2">
              <Button type="button" size="sm" variant="ghost" className="rounded-full border border-white/[0.08] bg-white/[0.035] text-white/70 hover:bg-white/[0.08] hover:text-white" onClick={onOpenSkillDrawer}>打开技能栏</Button>
              <Button type="button" size="sm" variant="ghost" className="rounded-full border border-white/[0.08] bg-white/[0.035] text-white/70 hover:bg-white/[0.08] hover:text-white" onClick={onOpenSkillStore}>技能商店</Button>
              <span className="inline-flex items-center gap-1.5 rounded-full border border-emerald-300/15 bg-emerald-300/[0.06] px-2.5 py-1.5 text-[10px] text-emerald-100/70"><Check className="size-3" /> 自动匹配 {autoMatching ? "已开启" : "按需指定"}</span>
            </div>
          </section>

          <section className="rounded-2xl border border-white/[0.08] bg-white/[0.035] p-3.5">
            <div className="flex items-center gap-2">
              <div className="text-[12px] font-semibold text-white/82">执行引擎</div>
              <span className="ml-auto rounded-full border border-emerald-300/15 bg-emerald-300/[0.06] px-2 py-1 text-[10px] text-emerald-100/70">当前：小树</span>
            </div>
            <p className="mt-2 text-[10px] leading-4 text-white/38">小树负责理解需求、调用技能、操作画布并验收结果；复杂任务由工作流运行时调度。</p>
          </section>
        </div>
      </DialogContent>
    </Dialog>
  );
}
