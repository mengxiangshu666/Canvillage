import { useState } from "react";
import { Braces, Check, Copy, Plug, Sparkles, Terminal } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";

function cliValue(value: string, fallback: string): string {
  const clean = value.trim();
  return clean || fallback;
}

type AgentAccessTab = "plugin" | "cli";

export function AgentCliSkillDialog({
  open,
  onOpenChange,
  projectId,
  canvasId,
  mountedSkillCount,
  totalSkillCount,
  autoMatching,
  connected = true,
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
  connected?: boolean;
  onOpenSkillDrawer: () => void;
  onOpenSkillStore: () => void;
}) {
  const [tab, setTab] = useState<AgentAccessTab>("plugin");
  const [copiedPlugin, setCopiedPlugin] = useState(false);
  const [copiedCli, setCopiedCli] = useState(false);
  const project = cliValue(projectId || "", "PROJECT_ID");
  const canvas = cliValue(canvasId || "", "CANVAS_ID");
  // Run the relay directly so an Agent shell never opens a visible cmd.exe
  // window for the compatibility .bat wrapper.
  const command = `powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File ".\\village_canvas_cli_hidden.ps1" --json --project "${project}" --canvas "${canvas}" canvas context`;
  const installCommands = [
    'codex plugin marketplace add ".\\agent_codex_plugin"',
    "codex plugin add village-canvas@village-canvas",
  ].join("\n");

  const copyText = async (text: string, kind: "plugin" | "cli") => {
    try {
      await navigator.clipboard.writeText(text);
      if (kind === "plugin") {
        setCopiedPlugin(true);
        toast.success("插件安装指令已复制");
        window.setTimeout(() => setCopiedPlugin(false), 1600);
      } else {
        setCopiedCli(true);
        toast.success("CLI 命令已复制");
        window.setTimeout(() => setCopiedCli(false), 1600);
      }
    } catch {
      toast.error("复制失败，请手动复制命令");
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        data-agent-dialog="v4"
        className="village-agent-dialog-v4 village-agent-dialog-v4--cli village-agent-cli-dialog max-w-[min(820px,calc(100%-1.5rem))] overflow-hidden border-white/[0.1] bg-[#111113]/98 p-0 text-white shadow-[0_28px_100px_rgba(0,0,0,0.62)]"
      >
        <div className="border-b border-white/[0.08] px-5 py-4 pr-14">
          <div className="flex items-center gap-2.5">
            <span className="flex size-8 items-center justify-center rounded-xl border border-cyan-200/15 bg-cyan-200/[0.08] text-cyan-100">
              <Plug className="size-4" />
            </span>
            <div className="min-w-0">
              <DialogTitle className="text-[15px] font-semibold text-white/90">
                Agent 接入
              </DialogTitle>
              <p className="mt-1 text-[11px] text-white/42">
                让 Codex 等外部 Agent 安装插件后直接读改当前画布
              </p>
            </div>
          </div>
        </div>

        <div className="flex shrink-0 items-center gap-1.5 border-b border-white/[0.06] px-4 py-2.5">
          {([
            { id: "plugin" as const, label: "插件", icon: Plug },
            { id: "cli" as const, label: "CLI & Skill", icon: Terminal },
          ]).map((item) => {
            const Icon = item.icon;
            const active = tab === item.id;
            return (
              <button
                key={item.id}
                type="button"
                onClick={() => setTab(item.id)}
                aria-pressed={active}
                className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-[12px] transition-colors ${
                  active
                    ? "border-white/25 bg-white/[0.12] text-white"
                    : "border-transparent text-white/48 hover:bg-white/[0.06] hover:text-white/80"
                }`}
              >
                <Icon className="size-3.5" />
                {item.label}
              </button>
            );
          })}
        </div>

        <div className="max-h-[min(70vh,620px)] overflow-y-auto p-4">
          {tab === "plugin" ? (
            <div className="space-y-3">
              <div className="grid gap-3 md:grid-cols-[1.25fr_1fr]">
                <section className="rounded-2xl border border-white/[0.08] bg-white/[0.035] p-3.5">
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <div className="text-[12px] font-semibold text-white/82">
                        复制并发送以下内容给你的 Agent
                      </div>
                      <div className="mt-1 text-[11px] text-white/38">
                        让 Agent 在项目根目录执行这两步
                      </div>
                    </div>
                    <Button
                      type="button"
                      size="sm"
                      variant="ghost"
                      className="shrink-0 rounded-full text-white/65 hover:bg-white/[0.08] hover:text-white"
                      onClick={() => void copyText(installCommands, "plugin")}
                    >
                      {copiedPlugin ? (
                        <Check className="mr-1.5 size-3.5 text-emerald-300" />
                      ) : (
                        <Copy className="mr-1.5 size-3.5" />
                      )}
                      {copiedPlugin ? "已复制" : "复制指令"}
                    </Button>
                  </div>
                  <div className="mt-3 space-y-2">
                    <div className="rounded-xl border border-white/[0.07] bg-black/25 p-2.5">
                      <div className="mb-1 text-[10px] text-white/35">1. 注册本地插件市场</div>
                      <pre className="overflow-x-auto text-[11px] leading-5 text-cyan-100/85">
                        <code>{'codex plugin marketplace add ".\\agent_codex_plugin"'}</code>
                      </pre>
                    </div>
                    <div className="rounded-xl border border-white/[0.07] bg-black/25 p-2.5">
                      <div className="mb-1 text-[10px] text-white/35">2. 安装画布插件</div>
                      <pre className="overflow-x-auto text-[11px] leading-5 text-cyan-100/85">
                        <code>codex plugin add village-canvas@village-canvas</code>
                      </pre>
                    </div>
                  </div>
                  <p className="mt-2 text-[10px] leading-4 text-white/32">
                    在村长无限画布项目根目录执行；如果插件市场改放到 Git 仓库，
                    把第一步路径换成仓库地址即可。
                  </p>
                </section>

                <section className="rounded-2xl border border-white/[0.08] bg-white/[0.035] p-3.5">
                  <div className="flex items-center gap-2">
                    <div className="text-[12px] font-semibold text-white/82">
                      连接账户，开始创作
                    </div>
                    <span
                      className={`ml-auto inline-flex items-center gap-1.5 rounded-full border px-2 py-1 text-[10px] ${
                        connected
                          ? "border-emerald-300/15 bg-emerald-300/[0.06] text-emerald-100/70"
                          : "border-amber-300/15 bg-amber-300/[0.06] text-amber-100/70"
                      }`}
                    >
                      <span
                        className={`size-1.5 rounded-full ${
                          connected ? "bg-emerald-400" : "animate-pulse bg-amber-300"
                        }`}
                      />
                      {connected ? "本机在线" : "连接中"}
                    </span>
                  </div>
                  <p className="mt-2 text-[11px] leading-5 text-white/42">
                    授权完成后，Agent 可以通过插件读取当前画布、改提示词、建节点、连线、
                    跑图片视频任务或工作流。
                  </p>
                  <ul className="mt-3 space-y-2 text-[10px] leading-4 text-white/35">
                    <li>· 本机服务：<span className="text-white/55">127.0.0.1:8784</span></li>
                    <li>· 账户：<span className="text-white/55">local</span>（随本机启动）</li>
                    <li>
                      · 如果 Codex 把插件复制到缓存目录，设置
                      <span className="text-white/55"> VILLAGE_CANVAS_HOME </span>
                      或写入
                      <span className="text-white/55"> %USERPROFILE%\.village-canvas\home</span>
                    </li>
                  </ul>
                </section>
              </div>

              <section className="rounded-2xl border border-white/[0.08] bg-white/[0.035] p-3.5">
                <div className="flex items-center gap-2">
                  <Sparkles className="size-4 text-amber-200/75" />
                  <div className="text-[12px] font-semibold text-white/82">插件能做什么</div>
                </div>
                <div className="mt-2.5 flex flex-wrap gap-1.5 text-[10px] text-white/45">
                  {[
                    "读画布 revision",
                    "建图片/文本/视频节点",
                    "改提示词与参数",
                    "连线与移动",
                    "查模型能力",
                    "跑图片/视频任务",
                    "启动与续跑工作流",
                  ].map((item) => (
                    <span key={item} className="rounded-full border border-white/[0.07] px-2 py-1">
                      {item}
                    </span>
                  ))}
                </div>
              </section>
            </div>
          ) : (
            <div className="space-y-3">
              <section className="rounded-2xl border border-white/[0.08] bg-white/[0.035] p-3.5">
                <div className="mb-2.5 flex items-center justify-between gap-3">
                  <div>
                    <div className="text-[12px] font-semibold text-white/82">村长画布 CLI</div>
                    <div className="mt-1 text-[11px] text-white/38">
                      真实命令，读取画布上下文并支持节点、任务、工作流操作
                    </div>
                  </div>
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    className="shrink-0 rounded-full text-white/65 hover:bg-white/[0.08] hover:text-white"
                    onClick={() => void copyText(command, "cli")}
                  >
                    {copiedCli ? (
                      <Check className="mr-1.5 size-3.5 text-emerald-300" />
                    ) : (
                      <Copy className="mr-1.5 size-3.5" />
                    )}
                    {copiedCli ? "已复制" : "复制命令"}
                  </Button>
                </div>
                <pre className="overflow-x-auto rounded-xl border border-white/[0.07] bg-black/30 px-3 py-2.5 text-[11px] leading-5 text-cyan-100/80">
                  <code>{command}</code>
                </pre>
                <div className="mt-2.5 flex flex-wrap gap-1.5 text-[10px] text-white/42">
                  {["canvas context", "node list", "canvas apply", "workflow runs", "task stop"].map(
                    (item) => (
                      <span key={item} className="rounded-full border border-white/[0.07] px-2 py-1">
                        {item}
                      </span>
                    ),
                  )}
                </div>
              </section>

              <section className="rounded-2xl border border-white/[0.08] bg-white/[0.035] p-3.5">
                <div className="flex items-center gap-2">
                  <Sparkles className="size-4 text-amber-200/75" />
                  <div className="text-[12px] font-semibold text-white/82">Skill</div>
                  <span className="ml-auto text-[10px] text-white/38">
                    {mountedSkillCount} / {totalSkillCount} 已挂载
                  </span>
                </div>
                <p className="mt-1.5 text-[11px] text-white/42">
                  Skill 只负责把专业执行合同交给 Agent，真正的写入、对账和任务控制仍走画布内核。
                </p>
                <div className="mt-3 flex flex-wrap gap-2">
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    className="rounded-full border border-white/[0.08] bg-white/[0.035] text-white/70 hover:bg-white/[0.08] hover:text-white"
                    onClick={onOpenSkillDrawer}
                  >
                    打开技能栏
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    className="rounded-full border border-white/[0.08] bg-white/[0.035] text-white/70 hover:bg-white/[0.08] hover:text-white"
                    onClick={onOpenSkillStore}
                  >
                    技能商店
                  </Button>
                  <span className="inline-flex items-center gap-1.5 rounded-full border border-emerald-300/15 bg-emerald-300/[0.06] px-2.5 py-1.5 text-[10px] text-emerald-100/70">
                    <Check className="size-3" /> 自动匹配 {autoMatching ? "已开启" : "按需指定"}
                  </span>
                </div>
              </section>

              <section className="rounded-2xl border border-white/[0.08] bg-white/[0.035] p-3.5">
                <div className="flex items-center gap-2">
                  <Braces className="size-4 text-cyan-200/70" />
                  <div className="text-[12px] font-semibold text-white/82">执行引擎</div>
                  <span className="ml-auto rounded-full border border-emerald-300/15 bg-emerald-300/[0.06] px-2 py-1 text-[10px] text-emerald-100/70">
                    当前：小树
                  </span>
                </div>
                <p className="mt-2 text-[10px] leading-4 text-white/38">
                  小树负责理解需求、调用技能、操作画布并验收结果；复杂任务由工作流运行时调度。
                </p>
              </section>
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
