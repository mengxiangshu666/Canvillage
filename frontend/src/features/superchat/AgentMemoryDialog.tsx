import { Check, Lock, LockOpen, Pencil, Plus, Search, Trash2, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import {
  createAgentMemory,
  createManualAgentMemory,
  deleteAgentMemory,
  getAgentMemoryStats,
  getGrowthMemoryContract,
  getTasteGraph,
  listAgentMemories,
  promoteAgentMemory,
  updateAgentMemory,
  type AgentMemoryDistillationReceipt,
  type AgentMemoryCreateKind,
  type AgentMemoryItem,
  type AgentMemoryStats,
  type GrowthMemoryContract,
  type TasteGraph,
  type TasteGraphNode,
} from "@/api/agent-memory";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

type MemoryTab =
  | "rules"
  | "preferences"
  | "verified"
  | "episodes"
  | "candidate"
  | "taste";

const TABS: Array<{ id: MemoryTab; label: string }> = [
  { id: "rules", label: "执行规则" },
  { id: "preferences", label: "我的偏好" },
  { id: "verified", label: "已验证经验" },
  { id: "episodes", label: "实践案例" },
  { id: "candidate", label: "候选经验" },
  { id: "taste", label: "口味图谱" },
];

function memoryBelongsToTab(item: AgentMemoryItem, tab: MemoryTab): boolean {
  // The taste tab is a different projection over the same store, not a slice
  // of the list — it renders from its own endpoint, so nothing routes here.
  if (tab === "taste") return false;
  if (tab === "candidate") {
    return item.status === "candidate" && item.kind.includes("experience");
  }
  if (tab === "verified") {
    return item.kind.includes("experience")
      && (item.status === "validated" || item.status === "confirmed");
  }
  if (tab === "episodes") {
    return item.status === "confirmed"
      && (item.kind === "episodic_example" || item.kind === "failure_episode");
  }
  if (item.status !== "confirmed") return false;
  if (tab === "rules") return item.kind === "learned_rule";
  if (tab === "preferences") return item.kind === "preference";
  return false;
}

function scopeLabel(item: AgentMemoryItem): string {
  if (item.scope_kind === "professional") return "专业";
  if (item.scope_kind === "project") return "项目";
  return "用户";
}

function memoryKindLabel(item: AgentMemoryItem): string {
  if (item.kind === "preference") return "偏好";
  if (item.kind === "project_fact") return "项目事实";
  if (item.kind === "episodic_example") return "成功案例";
  if (item.kind === "failure_episode") return "失败复盘";
  if (item.kind.includes("experience")) return "经验";
  return "规则";
}

function formatDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function memoryDistillationStatusLabel(status: AgentMemoryDistillationReceipt["status"]): string {
  const labels: Record<AgentMemoryDistillationReceipt["status"], string> = {
    pending: "等待提炼",
    processing: "正在提炼",
    retryable: "等待重试",
    compiled: "已整理为候选经验",
    evidence_only: "已保存为证据",
    ignored: "已忽略",
    needs_review: "等待复核",
  };
  return labels[status] ?? "状态未知";
}

export function AgentMemoryDialog({
  open,
  onOpenChange,
  project,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Scopes project-level preferences in the taste projection. */
  project?: string;
}) {
  const [items, setItems] = useState<AgentMemoryItem[]>([]);
  const [stats, setStats] = useState<AgentMemoryStats | null>(null);
  const [taste, setTaste] = useState<TasteGraph | null>(null);
  const [contract, setContract] = useState<GrowthMemoryContract | null>(null);
  const [tab, setTab] = useState<MemoryTab>("rules");
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [newRule, setNewRule] = useState("");
  const [manualOpen, setManualOpen] = useState(false);
  const [manualContent, setManualContent] = useState("");
  const [manualKind, setManualKind] = useState<AgentMemoryCreateKind>("learned_rule");
  const [manualLocked, setManualLocked] = useState(true);
  const [manualSaving, setManualSaving] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editingText, setEditingText] = useState("");
  const [lastReceipt, setLastReceipt] = useState<AgentMemoryDistillationReceipt | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      // The taste graph and growth contract live on separate endpoints that
      // nobody read until now; failures there must not take down the list.
      const [nextItems, nextStats, nextTaste, nextContract] = await Promise.all([
        listAgentMemories(),
        getAgentMemoryStats(),
        getTasteGraph(project).catch(() => null),
        getGrowthMemoryContract().catch(() => null),
      ]);
      setItems(nextItems);
      setStats(nextStats);
      setTaste(nextTaste);
      setContract(nextContract);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "成长记忆读取失败");
    } finally {
      setLoading(false);
    }
  }, [project]);

  useEffect(() => {
    if (open) void refresh();
  }, [open, refresh]);

  const visibleItems = useMemo(() => {
    const cleanQuery = query.trim().toLocaleLowerCase();
    return items.filter((item) => {
      if (!memoryBelongsToTab(item, tab)) return false;
      return !cleanQuery || item.content.toLocaleLowerCase().includes(cleanQuery);
    });
  }, [items, query, tab]);

  const counts = useMemo(() => Object.fromEntries(
    TABS.map((entry) => [
      entry.id,
      entry.id === "taste"
        ? (taste?.stats.eligible ?? 0)
        : items.filter((item) => memoryBelongsToTab(item, entry.id)).length,
    ]),
  ) as Record<MemoryTab, number>, [items, taste]);

  const replaceItem = useCallback((next: AgentMemoryItem, previousId = next.id) => {
    setItems((current) => [
      next,
      ...current.filter((item) => item.id !== previousId && item.id !== next.id),
    ]);
  }, []);

  const addRule = useCallback(async () => {
    const content = newRule.trim();
    if (!content) return;
    try {
      const accepted = await createAgentMemory({
        content,
        kind: "learned_rule",
        locked: true,
      });
      setLastReceipt(accepted.receipt);
      setNewRule("");
      setTab("candidate");
      toast.success(`已进入成长提炼队列 · 事件 #${accepted.event_id}`);
      void refresh();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "长期规则保存失败");
    }
  }, [newRule, refresh]);

  const addManualMemory = useCallback(async () => {
    const content = manualContent.trim();
    if (!content || manualSaving) return;
    setManualSaving(true);
    try {
      const created = await createManualAgentMemory({
        content,
        kind: manualKind,
        locked: manualLocked,
      });
      replaceItem(created);
      setManualContent("");
      setManualOpen(false);
      setTab(created.kind === "preference"
        ? "preferences"
        : created.kind.includes("experience")
          ? "verified"
          : "rules");
      void getAgentMemoryStats().then(setStats);
      toast.success("已手动添加，立即生效");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "手动记忆保存失败");
    } finally {
      setManualSaving(false);
    }
  }, [manualContent, manualKind, manualLocked, manualSaving, replaceItem]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        data-agent-dialog="v4"
        className="village-agent-dialog-v4 village-agent-dialog-v4--memory !flex !flex-col w-[calc(100vw-2rem)] max-h-[min(760px,88vh)] !max-w-[calc(100vw-2rem)] overflow-hidden border-white/[0.09] bg-[#111114] p-0 text-white shadow-2xl min-[760px]:!max-w-[760px]"
      >
        <div className="border-b border-white/[0.08] px-6 pb-4 pt-5">
          <DialogTitle className="text-[18px] font-semibold tracking-[-0.02em]">
            小树 · 成长记忆
          </DialogTitle>
          <p className="mt-1 text-[12px] text-white/45">
            原始对话只做证据；这里展示的是小树可以直接执行的规则和经验。
          </p>
          <div className="mt-4 flex flex-wrap items-center gap-2 text-[11px] text-white/45">
            <Badge variant="outline" className="border-white/10 bg-white/[0.035] text-white/55">
              {stats?.effective ?? items.length} 条有效记忆
            </Badge>
            <Badge variant="outline" className="border-white/10 bg-white/[0.035] text-white/55">
              {stats?.compiled ?? items.filter((item) => item.provenance.compiled).length} 条已整理
            </Badge>
            <Badge variant="outline" className="border-amber-300/20 bg-amber-300/[0.06] text-amber-100/70">
              {stats?.pending_events ?? 0} 个待整理事件
            </Badge>
            <Badge variant="outline" className="border-emerald-300/20 bg-emerald-300/[0.05] text-emerald-100/65">
              {stats?.successful_applications ?? 0} 次完成采用
            </Badge>
            <Badge variant="outline" className="border-sky-300/20 bg-sky-300/[0.05] text-sky-100/65">
              {stats?.feedback_count ?? 0} 次明确反馈
            </Badge>
            <Badge variant="outline" className="border-violet-300/20 bg-violet-300/[0.05] text-violet-100/65">
              {stats?.workflow_evidence ?? 0} 条验收证据
            </Badge>
          </div>
          {lastReceipt && (
            <div
              data-memory-distillation-receipt="true"
              className="mt-3 rounded-lg border border-sky-300/15 bg-sky-300/[0.045] px-3 py-2 text-[11px] leading-5 text-sky-50/70"
            >
              <span className="text-sky-100/40">最近提炼：</span>
              事件 #{lastReceipt.event_id} · {memoryDistillationStatusLabel(lastReceipt.status)}
              {lastReceipt.memory_id > 0 && ` · 经验 #${lastReceipt.memory_id}`}
              {lastReceipt.attempt_count > 0 && ` · 第 ${lastReceipt.attempt_count} 次处理`}
            </div>
          )}
        </div>

        <div className="flex min-h-0 flex-1 flex-col px-6 pb-6">
          <div className="grid grid-cols-3 gap-1 border-b border-white/[0.07] py-3 min-[760px]:grid-cols-6">
            {TABS.map((entry) => (
              <button
                key={entry.id}
                type="button"
                onClick={() => setTab(entry.id)}
                className={cn(
                  "min-w-0 w-full whitespace-nowrap rounded-full px-2.5 py-1.5 text-center text-[12px] font-medium transition min-[760px]:px-3",
                  tab === entry.id
                    ? "bg-white text-black"
                    : "text-white/48 hover:bg-white/[0.06] hover:text-white/85",
                )}
              >
                {entry.label} · {counts[entry.id]}
              </button>
            ))}
          </div>

          {tab === "taste" ? (
            <TasteGraphTab
              taste={taste}
              contract={contract}
              loading={loading}
              onOpenPreference={() => setTab("preferences")}
            />
          ) : (
          <>

          <div className="flex gap-2 py-3">
            <div className="relative min-w-0 flex-1">
              <Search className="pointer-events-none absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-white/30" />
              <Input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="搜索成长记忆"
                className="h-9 border-white/[0.08] bg-white/[0.035] pl-9 text-[12px] text-white placeholder:text-white/25"
              />
            </div>
            <Button variant="outline" size="sm" onClick={() => void refresh()} disabled={loading}>
              {loading ? "读取中" : "刷新"}
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => setManualOpen((current) => !current)}
              aria-expanded={manualOpen}
            >
              <Plus className="mr-1 size-3.5" />{manualOpen ? "收起" : "手动添加"}
            </Button>
          </div>

          {manualOpen && (
            <div className="mb-3 space-y-2.5 rounded-xl border border-sky-300/15 bg-sky-300/[0.035] p-3">
              <div className="flex items-center justify-between gap-2">
                <span className="text-[12px] font-medium text-white/80">手动添加一条记忆</span>
                <span className="text-[10px] text-white/35">保存后立即生效</span>
              </div>
              <Textarea
                value={manualContent}
                onChange={(event) => setManualContent(event.target.value)}
                placeholder="写一条具体规则或偏好，例如：视频节点默认关闭原生音频"
                className="min-h-20 border-white/[0.1] bg-black/20 text-[12px] leading-6"
                autoFocus
              />
              <div className="flex flex-wrap items-center gap-2">
                <select
                  aria-label="记忆类型"
                  value={manualKind}
                  onChange={(event) => setManualKind(event.target.value as AgentMemoryCreateKind)}
                  className="h-8 rounded-lg border border-white/[0.1] bg-black/20 px-2 text-[11px] text-white/75 outline-none"
                >
                  <option value="learned_rule">执行规则</option>
                  <option value="preference">我的偏好</option>
                  <option value="verified_experience">已验证经验</option>
                </select>
                <label className="flex items-center gap-1.5 text-[11px] text-white/55">
                  <input
                    type="checkbox"
                    checked={manualLocked}
                    onChange={(event) => setManualLocked(event.target.checked)}
                    className="size-3.5 accent-sky-300"
                  />
                  锁定
                </label>
                <div className="ml-auto flex items-center gap-1.5">
                  <Button variant="ghost" size="sm" onClick={() => setManualOpen(false)}>
                    取消
                  </Button>
                  <Button size="sm" onClick={() => void addManualMemory()} disabled={!manualContent.trim() || manualSaving}>
                    {manualSaving ? "保存中" : "直接保存"}
                  </Button>
                </div>
              </div>
            </div>
          )}

          {tab === "rules" && !manualOpen && (
            <div className="mb-3 flex gap-2 rounded-xl border border-white/[0.08] bg-white/[0.025] p-2.5">
              <Input
                value={newRule}
                onChange={(event) => setNewRule(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") void addRule();
                }}
                placeholder="写下经验，小树会提炼触发条件、动作和验收标准"
                className="h-9 border-0 bg-transparent text-[12px] shadow-none focus-visible:ring-0"
              />
              <Button size="sm" onClick={() => void addRule()} disabled={!newRule.trim()}>
                <Plus className="mr-1 size-3.5" />提炼
              </Button>
            </div>
          )}

          <div className="min-h-0 flex-1 space-y-2 overflow-y-auto pr-1">
            {!loading && visibleItems.length === 0 && (
              <div className="flex min-h-40 items-center justify-center rounded-xl border border-dashed border-white/[0.08] text-[12px] text-white/30">
                这里还没有真实沉淀的内容
              </div>
            )}
            {visibleItems.map((item) => (
              <article key={item.id} className="min-w-0 rounded-xl border border-white/[0.075] bg-white/[0.025] p-3.5">
                <div className="flex items-start gap-3">
                  <div className="min-w-0 flex-1">
                    <div className="mb-2 flex flex-wrap items-center gap-1.5 text-[10px] text-white/35">
                      <span>{scopeLabel(item)}</span>
                      <span>·</span>
                      <span>{memoryKindLabel(item)}</span>
                      <span>·</span>
                      <span>置信度 {Math.round(item.confidence * 100)}%</span>
                      <span>·</span>
                      <span>召回 {item.retrieved_count} 次</span>
                      {item.applied_count > 0 && <span>· 完成采用 {item.applied_count} 次</span>}
                      {item.status === "validated" && <span>· 已通过重复验收</span>}
                      {item.kind === "episodic_example" && <span>· 用户明确采用</span>}
                      {item.kind === "failure_episode" && <span>· 已进入失败复盘</span>}
                      {item.evidence_count > 1 && <span>· {item.evidence_count} 条证据</span>}
                      <span>· {item.provenance.compiled ? "已整理" : "历史待迁移"}</span>
                      {item.provenance.rule_type && <span>· {item.provenance.rule_type}</span>}
                      {item.provenance.hook_id && <span>· preview hook</span>}
                      {item.version > 1 && <span>· v{item.version}</span>}
                      <span className="ml-auto">{formatDate(item.updated_at)}</span>
                    </div>
                    {editingId === item.id ? (
                      <Textarea
                        value={editingText}
                        onChange={(event) => setEditingText(event.target.value)}
                        className="min-h-24 border-white/[0.1] bg-black/25 text-[12px] leading-6"
                      />
                    ) : (
                      <div className="space-y-2">
                        <p className="break-words whitespace-pre-wrap text-[12px] leading-6 text-white/82 [overflow-wrap:anywhere]">
                          {item.content}
                        </p>
                        {item.provenance.action.length > 0
                          && item.provenance.action.join("；").replace(/。/g, "")
                            !== item.content.replace(/。/g, "") && (
                          <div className="rounded-lg bg-white/[0.025] px-3 py-2 text-[11px] leading-5 text-white/55">
                            <span className="text-white/32">执行：</span>
                            {item.provenance.action.join("；")}
                          </div>
                        )}
                        {item.provenance.avoid.length > 0 && (
                          <div className="rounded-lg bg-amber-300/[0.035] px-3 py-2 text-[11px] leading-5 text-amber-50/58">
                            <span className="text-amber-100/35">避免：</span>
                            {item.provenance.avoid.join("；")}
                          </div>
                        )}
                        {item.provenance.hook_id && (
                          <div className="rounded-lg bg-sky-300/[0.035] px-3 py-2 text-[11px] leading-5 text-sky-50/60">
                            <span className="text-sky-100/35">规则钩子：</span>
                            {item.provenance.hook_id}（当前仅 preview，不自动改写）
                          </div>
                        )}
                        {item.provenance.evidence.length > 0 && (
                          <details className="text-[10px] text-white/32">
                            <summary className="cursor-pointer select-none hover:text-white/55">
                              查看 {item.provenance.evidence.length} 条验证证据
                            </summary>
                            <div className="mt-2 space-y-1 border-l border-white/[0.08] pl-2.5">
                              {item.provenance.evidence.slice(-3).map((evidence, index) => (
                                <p key={`${item.id}-evidence-${index}`} className="break-words [overflow-wrap:anywhere]">
                                  {String(evidence.notes || evidence.ref || "已记录验证结果")}
                                </p>
                              ))}
                            </div>
                          </details>
                        )}
                      </div>
                    )}
                  </div>
                  <div className="flex shrink-0 items-center gap-0.5">
                    {editingId === item.id ? (
                      <>
                        <Button
                          variant="ghost"
                          size="icon-sm"
                          title="保存修改"
                          onClick={() => void updateAgentMemory(item.id, { content: editingText.trim() })
                            .then((next) => {
                              replaceItem(next);
                              setEditingId(null);
                              toast.success("记忆已更新");
                            })}
                        >
                          <Check className="size-3.5" />
                        </Button>
                        <Button variant="ghost" size="icon-sm" title="取消" onClick={() => setEditingId(null)}>
                          <X className="size-3.5" />
                        </Button>
                      </>
                    ) : (
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        title="修改"
                        onClick={() => {
                          setEditingId(item.id);
                          setEditingText(item.content);
                        }}
                      >
                        <Pencil className="size-3.5" />
                      </Button>
                    )}
                    {item.status === "candidate" && (
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        title="确认为长期经验"
                        onClick={() => void promoteAgentMemory(item.id)
                          .then((next) => replaceItem(next, item.id))}
                      >
                        <Check className="size-3.5 text-emerald-300" />
                      </Button>
                    )}
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      title={item.locked ? "解除锁定" : "锁定"}
                      onClick={() => void updateAgentMemory(item.id, { locked: !item.locked }).then(replaceItem)}
                    >
                      {item.locked ? <Lock className="size-3.5" /> : <LockOpen className="size-3.5" />}
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      title="删除"
                      onClick={() => {
                        if (!window.confirm("确定删除这条成长记忆？")) return;
                        void deleteAgentMemory(item.id).then(() => {
                          setItems((current) => current.filter((entry) => entry.id !== item.id));
                          void getAgentMemoryStats().then(setStats);
                        });
                      }}
                      className="hover:text-red-300"
                    >
                      <Trash2 className="size-3.5" />
                    </Button>
                  </div>
                </div>
              </article>
            ))}
          </div>
          </>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}

/**
 * Taste graph tab — renders `chat/memories/taste-graph`, the evidence-backed
 * preference projection the backend has exposed all along.
 *
 * The distinction that matters on screen: the memory list next door shows
 * every stored record, while this only contains records that passed the
 * eligibility gate and carries a derived strength. Showing the reject count
 * keeps "why isn't my preference here" answerable without reading the code.
 */
function TasteGraphTab({
  taste,
  contract,
  loading,
  onOpenPreference,
}: {
  taste: TasteGraph | null;
  contract: GrowthMemoryContract | null;
  loading: boolean;
  onOpenPreference: () => void;
}) {
  if (!taste) {
    return (
      <div className="flex min-h-40 flex-1 items-center justify-center rounded-xl border border-dashed border-white/[0.08] text-[12px] text-white/30">
        {loading ? "读取中" : "口味图谱读取失败"}
      </div>
    );
  }

  const groups: Array<{
    title: string;
    nodeList: TasteGraphNode[];
    tone: "love" | "anti";
    note: string;
  }> = [
    {
      title: "硬性偏好",
      nodeList: taste.hard_loves,
      tone: "love",
      note: "这些是提炼出来会主动遵守的取向。",
    },
    {
      title: "硬性禁忌",
      nodeList: taste.hard_antis,
      tone: "anti",
      note: "这些是被判为「不要」的取向，生成时优先规避。",
    },
  ];

  return (
    <div className="min-h-0 flex-1 space-y-3 overflow-y-auto py-3 pr-1">
      <div className="flex flex-wrap items-center gap-2 rounded-xl border border-white/[0.075] bg-white/[0.025] px-3 py-2.5 text-[11px]">
        <Badge variant="outline" className="border-emerald-300/20 bg-emerald-300/[0.05] text-emerald-100/70">
          合格 {taste.stats.eligible}
        </Badge>
        <Badge variant="outline" className="border-white/10 bg-white/[0.035] text-white/55">
          偏好 {taste.stats.love_count}
        </Badge>
        <Badge variant="outline" className="border-amber-300/20 bg-amber-300/[0.05] text-amber-100/70">
          禁忌 {taste.stats.anti_count}
        </Badge>
        <Badge
          variant="outline"
          className="border-white/10 bg-white/[0.035] text-white/45"
          title="未通过准入：需要结构化 schema、已锁定或带证据，且正面证据多于负面"
        >
          未通过 {taste.stats.rejected}
        </Badge>
        <span className="ml-auto text-white/28" title={taste.source.commit}>
          投影 {taste.source.project} · {taste.revision}
        </span>
      </div>

      {taste.stats.eligible === 0 && (
        <div className="rounded-xl border border-dashed border-white/[0.08] px-3 py-6 text-center text-[12px] leading-6 text-white/35">
          还没有任何偏好同时满足「已锁定 / 有证据 / 结构化」。
          <br />
          在「我的偏好」里加一条并勾选锁定，它就会出现在这里。
          <Button variant="link" size="sm" className="ml-1 h-auto px-0 text-[12px]" onClick={onOpenPreference}>
            去添加
          </Button>
        </div>
      )}

      {groups.map((group) =>
        group.nodeList.length === 0 ? null : (
          <section key={group.tone}>
            <div className="mb-1.5 flex items-baseline gap-2">
              <h4 className="text-[11px] font-medium uppercase tracking-wide text-white/35">
                {group.title}
              </h4>
              <span className="text-[10px] text-white/28">{group.note}</span>
            </div>
            <div className="space-y-1.5">
              {group.nodeList.map((node) => (
                <article
                  key={node.id}
                  className={cn(
                    "rounded-xl border px-3 py-2.5",
                    group.tone === "anti"
                      ? "border-amber-300/15 bg-amber-300/[0.03]"
                      : "border-white/[0.075] bg-white/[0.025]",
                  )}
                >
                  <div className="mb-1 flex flex-wrap items-center gap-1.5 text-[10px] text-white/35">
                    <span
                      className={cn(
                        "rounded-full border px-1.5 py-0.5 leading-none",
                        node.confidence === "H"
                          ? "border-emerald-300/25 bg-emerald-300/[0.06] text-emerald-100/70"
                          : node.confidence === "M"
                            ? "border-sky-300/25 bg-sky-300/[0.06] text-sky-100/70"
                            : "border-white/12 bg-white/[0.04] text-white/45",
                      )}
                      title="置信度：H 已锁定或正负 2:0；M 已确认或至少 1 条正面证据"
                    >
                      {node.confidence}
                    </span>
                    <span>{node.category}</span>
                    <span>·</span>
                    <span>强度 {node.strength.toFixed(2)}</span>
                    {node.domains.length > 0 && (
                      <>
                        <span>·</span>
                        <span className="truncate">{node.domains.join(" / ")}</span>
                      </>
                    )}
                    {node.locked && (
                      <span className="inline-flex items-center gap-0.5 text-white/45">
                        <Lock className="size-2.5" /> 锁定
                      </span>
                    )}
                    <span className="ml-auto">记忆 #{node.memory_id}</span>
                  </div>
                  <p className="break-words whitespace-pre-wrap text-[12px] leading-6 text-white/82 [overflow-wrap:anywhere]">
                    {node.description}
                  </p>
                  {node.evidence_ids.length > 0 && (
                    <details className="mt-1 text-[10px] text-white/28">
                      <summary className="cursor-pointer select-none hover:text-white/50">
                        {node.evidence_ids.length} 条证据
                      </summary>
                      <div className="mt-1 space-y-0.5 border-l border-white/[0.08] pl-2.5">
                        {node.evidence_ids.map((id) => (
                          <p key={id} className="break-all">
                            {id}
                          </p>
                        ))}
                      </div>
                    </details>
                  )}
                </article>
              ))}
            </div>
          </section>
        ),
      )}

      {contract && (
        <div className="rounded-xl border border-white/[0.075] bg-white/[0.02] px-3 py-2.5 text-[11px] leading-5 text-white/45">
          <div className="mb-1 flex flex-wrap items-center gap-2">
            <span className="text-white/70">提炼角色</span>
            <Badge
              variant="outline"
              className={
                contract.modelSource === "unconfigured"
                  ? "border-amber-300/25 bg-amber-300/[0.06] text-amber-100/75"
                  : "border-emerald-300/20 bg-emerald-300/[0.05] text-emerald-100/70"
              }
            >
              {contract.modelSource === "unconfigured" ? "未配置模型" : "已配置"}
            </Badge>
            <span className="text-white/45">{contract.modelRef || "—"}</span>
            <span className="ml-auto text-white/28">
              来源 {contract.modelSource}
            </span>
          </div>
          写入副作用：{contract.sideEffects.join("、")}
          {contract.modelSource === "unconfigured" &&
            " · 没有模型时新记忆会一直停在「等待提炼」"}
        </div>
      )}
    </div>
  );
}
