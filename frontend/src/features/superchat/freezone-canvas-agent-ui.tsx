// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
/**
 * Shared canvas Agent UI primitives.
 * The v4 shell owns presentation; this module owns the functional controls.
 */
import type { LucideIcon } from "lucide-react";
import {
  BadgeCheck,
  Bot,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Circle,
  Clapperboard,
  ClipboardCheck,
  Film,
  FileText,
  LayoutGrid,
  LifeBuoy,
  Loader2,
  Play,
  RefreshCw,
  ScanFace,
  ShieldAlert,
  Search,
  Sparkles,
  Store,
  Trash2,
  Undo2,
  Wand2,
  X,
} from "lucide-react";
import { Fragment, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { AgentMark } from "@/components/ui/AgentMark";
export { AgentMark } from "@/components/ui/AgentMark";
import { cn } from "@/lib/utils";
import { useCanvasStore } from "@/stores/canvasStore";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import {
  formatAgentExecutionDuration,
  type AgentExecutionTimeline,
  type AgentExecutionTimelineStatus,
} from "@/features/superchat/agent-execution-timeline";
import {
  CANVAS_AGENT_SKILLS,
  type CanvasAgentNodeRef,
  type CanvasAgentSkill,
  selectedCanvasAgentSkills,
} from "@/features/superchat/canvas-agent-skills";
import type {
  DirectorConsoleState,
  DirectorGateTone,
} from "@/features/superchat/director-console-model";
import type {
  StructureApplyRecord,
  StructureProposal,
} from "@/features/superchat/structure-proposal-store";
import type {
  AgentWorkflowState,
  ChatMessage,
  ChatRecoveryState,
} from "@/features/superchat/types";
import type { WorkflowRun } from "@/types/workflow-runtime";
import {
  workflowComposeAuthorizationRequestFromRun,
  workflowMediaAuthorizationRequestFromRun,
  workflowRecoveryFromRun,
  workflowFailureDisplayKey,
  workflowStepHasBlockingCanvasRecovery,
  workflowStepCanRetryWholeStep,
} from "@/features/superchat/workflow-failure-dismissal";
import {
  planWorkflowAssetBindingRepairs,
} from "@/features/superchat/workflow-asset-binding-repair";
import { focusWorkflowCanvasNodes } from "@/features/superchat/workflow-canvas-focus";
import {
  workflowReleaseBlockingChecks,
  workflowReleaseReadinessFromRun,
  workflowReleaseRequiresNotice,
  workflowReleaseStatusLabel,
  workflowReleaseSummary,
} from "@/features/superchat/workflow-release-readiness";

export const FREEZONE_AGENT_DRAWER_CLASS = "canvas-agent-drawer-chat";
export const FREEZONE_AGENT_WELCOME_CLASS = "chat-welcome-root";
// Keep the versioned key so historical oversized drawer preferences do not leak in.
export const FREEZONE_AGENT_WIDTH_KEY = "st.freezone.agentDrawerWidth.v2";
export const FREEZONE_AGENT_WIDTH_V4_KEY = "st.freezone.agentDrawerWidth.v4";
export const FREEZONE_AGENT_WIDTH_MIN = 320;
export const FREEZONE_AGENT_WIDTH_MAX = 560;
export const FREEZONE_AGENT_WIDTH_DEFAULT_WIDE = 370;
export const FREEZONE_AGENT_WIDTH_DEFAULT_NARROW = 360;

export type WorkflowItemSelectionGroup = {
  stepId: string;
  itemIds: string[];
};

/**
 * Stable copy exports shared by the shell and its regression tests.
 */
export const LIBTV_WELCOME_GREETING = "你好，我是小树。告诉我这次要交付什么。";
export const LIBTV_WELCOME_CREATE_TOGETHER = "和小树一起把作品做出来";
export const LIBTV_WELCOME_SKILL_REFRESH = "换一批";
export const LIBTV_CHAT_INPUT_PLACEHOLDER = "告诉小树要做什么，或者 @ 引用节点 / 素材";
export const LIBTV_CHAT_RICH_INPUT_PLACEHOLDER = "描述交付目标，或用 @ 引用节点、/ 调用技能";
export const LIBTV_ADD_TO_AGENT = "交给小树";
export const LIBTV_HEADER_SUBTITLE = "对齐目标，直接落地";
export const LIBTV_THINKING = "思考中…";
export const LIBTV_EXECUTING = "执行中…";

/** welcomeSkillTitle1..17 from LibTV — cycle with 换一批. */
export const LIBTV_WELCOME_SKILL_TITLES: readonly string[] = [
  "说个想法。或者，选个 Skill",
  "今天从哪个 Skill 开始？",
  "选一个 Skill，让创作更快一步",
  "一个 Skill，打开一种可能",
  "你的 Skill，已准备就绪",
  "用 Skill，开启今天的故事",
  "每个 Skill，都是一个开场",
  "新的一天，新的 Skill",
  "让 Skill 帮你迈出第一步",
  "从 Skill 出发，抵达成片",
  "一个 Skill，一部作品",
  "每天，换一个 Skill 开场",
  "一个 Skill，慢慢打磨你的故事",
  "Skill 全开，故事走起",
  "Skill 已加载，等待你的输入_",
  "/skill：你的创作入口",
  "Skill 就位，ready when you are",
];

const SKILL_PAGE_SIZE = 4;


const SKILL_COVER: Record<string, string> = {
  "one-click-film": "from-fuchsia-500/90 to-violet-600/75",
  "canvas-director": "from-sky-500/90 to-cyan-600/75",
  "template-director": "from-violet-500/90 to-indigo-600/75",
  storyboard: "from-amber-500/90 to-orange-600/75",
  "identity-continuity": "from-emerald-500/90 to-teal-600/75",
  "prompt-engineer": "from-indigo-500/90 to-blue-600/75",
  "expression-director": "from-rose-500/90 to-pink-600/75",
  "failure-rescue": "from-red-500/90 to-rose-600/75",
  "delivery-qc": "from-violet-500/90 to-purple-600/75",
};

const SKILL_ICON: Record<string, LucideIcon> = {
  "one-click-film": Clapperboard,
  "canvas-director": LayoutGrid,
  "template-director": Wand2,
  storyboard: Film,
  "identity-continuity": ScanFace,
  "prompt-engineer": Wand2,
  "expression-director": Sparkles,
  "failure-rescue": LifeBuoy,
  "delivery-qc": ClipboardCheck,
};

const SKILL_TAG: Record<string, string> = {
  "one-click-film": "Production",
  "canvas-director": "Canvas",
  "template-director": "Template",
  storyboard: "Shot",
  "identity-continuity": "Identity",
  "prompt-engineer": "Prompt",
  "expression-director": "Performance",
  "failure-rescue": "Rescue",
  "delivery-qc": "QC",
};

const SKILL_GROUPS = [
  { id: "all", label: "全部" },
  { id: "production", label: "生产" },
  { id: "canvas", label: "画布" },
  { id: "story", label: "分镜" },
  { id: "character", label: "角色" },
  { id: "prompt", label: "提示词" },
  { id: "quality", label: "质检" },
] as const;

type SkillGroupId = (typeof SKILL_GROUPS)[number]["id"];

const SKILL_GROUP_BY_ID: Record<string, Exclude<SkillGroupId, "all">> = {
  "one-click-film": "production",
  "canvas-director": "canvas",
  "template-director": "production",
  storyboard: "story",
  "identity-continuity": "character",
  "prompt-engineer": "prompt",
  "expression-director": "character",
  "failure-rescue": "quality",
  "delivery-qc": "quality",
};

export function skillIconFor(skillId: string): LucideIcon {
  return SKILL_ICON[skillId] ?? Bot;
}

export function SkillGlyph({
  skillId,
  className,
  iconClassName,
  coverImage,
}: {
  skillId: string;
  className?: string;
  iconClassName?: string;
  coverImage?: string;
}) {
  const [coverFailed, setCoverFailed] = useState(false);
  const Icon = skillIconFor(skillId);
  return (
    <span
      className={cn(
        "flex shrink-0 items-center justify-center rounded-[13px] border border-white/10 bg-gradient-to-br text-white shadow-[inset_0_1px_0_rgba(255,255,255,0.12)]",
        skillCoverClass({ id: skillId } as CanvasAgentSkill),
        className,
      )}
      aria-hidden
    >
      {coverImage && !coverFailed ? (
        <img
          src={coverImage}
          alt=""
          className="h-full w-full object-cover"
          onError={() => setCoverFailed(true)}
        />
      ) : (
        <Icon className={cn("size-3.5", iconClassName)} strokeWidth={2.25} />
      )}
    </span>
  );
}

export function defaultFreezoneAgentWidth(viewportWidth = typeof window !== "undefined" ? window.innerWidth : 1440): number {
  if (viewportWidth >= 1520) return 370;
  return FREEZONE_AGENT_WIDTH_DEFAULT_NARROW;
}

export function clampFreezoneAgentWidth(width: number): number {
  if (!Number.isFinite(width)) return defaultFreezoneAgentWidth();
  return Math.min(FREEZONE_AGENT_WIDTH_MAX, Math.max(FREEZONE_AGENT_WIDTH_MIN, Math.round(width)));
}

export function loadFreezoneAgentWidth(storageKey = FREEZONE_AGENT_WIDTH_KEY): number {
  try {
    const raw = window.localStorage.getItem(storageKey);
    if (!raw) return defaultFreezoneAgentWidth();
    const width = clampFreezoneAgentWidth(Number(raw));
    return width === 388 || width === 400 || width === 420 ? defaultFreezoneAgentWidth() : width;
  } catch {
    return defaultFreezoneAgentWidth();
  }
}

export function saveFreezoneAgentWidth(width: number, storageKey = FREEZONE_AGENT_WIDTH_KEY): void {
  try {
    window.localStorage.setItem(storageKey, String(clampFreezoneAgentWidth(width)));
  } catch {
    /* ignore quota */
  }
}

export function skillDisplayTag(skill: CanvasAgentSkill): string {
  return SKILL_TAG[skill.id] ?? skill.skillKey.replace(/^village-canvas-/, "");
}

export function skillCoverClass(skill: CanvasAgentSkill): string {
  return SKILL_COVER[skill.id] ?? "from-white/30 to-white/10";
}

/** Shared welcome Skill card presentation. */
export const LIBTV_SKILL_CARD_CLASS =
  "flex items-center gap-2 overflow-hidden rounded-2xl border-[0.5px] border-[#363636] bg-white/[0.03] p-3 text-left transition-colors hover:bg-white/[0.06]";

export function FreezoneWelcomeSkills({
  selectedIds,
  onToggle,
  disabled,
  displayName,
  showSkillPicker = true,
  compact = false,
}: {
  selectedIds: readonly string[];
  onToggle: (skillId: string) => void;
  disabled?: boolean;
  displayName?: string;
  showSkillPicker?: boolean;
  /** LibTV-like empty state used by the canvas-only Agent workbench. */
  compact?: boolean;
}) {
  const [page, setPage] = useState(0);
  const [skillPickerOpen, setSkillPickerOpen] = useState(false);
  const pageCount = Math.max(1, Math.ceil(CANVAS_AGENT_SKILLS.length / SKILL_PAGE_SIZE));
  const safePage = page % pageCount;
  const visible = useMemo(
    () => CANVAS_AGENT_SKILLS.slice(safePage * SKILL_PAGE_SIZE, safePage * SKILL_PAGE_SIZE + SKILL_PAGE_SIZE),
    [safePage],
  );
  const refreshBatch = () => {
    setPage((current) => (current + 1) % pageCount);
  };

  const greetingName = displayName?.trim() || "创作者";
  if (compact) {
    return (
      <div
        className={cn(
          FREEZONE_AGENT_WELCOME_CLASS,
          "village-agent-welcome village-agent-welcome--libtv",
        )}
        data-welcome-ui="village-agent-libtv"
      >
        <div className="village-agent-welcome-libtv-head">
          <div className="min-w-0">
            <p className="village-agent-welcome-eyebrow">小树创作台</p>
            <h2>选择一个 Skill，让创作更快一步</h2>
          </div>
          {pageCount > 1 && (
            <button
              type="button"
              onClick={refreshBatch}
              disabled={disabled}
              className="village-agent-welcome-refresh"
              title={LIBTV_WELCOME_SKILL_REFRESH}
            >
              <RefreshCw className="size-3.5" aria-hidden />
              <span>{LIBTV_WELCOME_SKILL_REFRESH}</span>
            </button>
          )}
        </div>
        <div className="village-agent-welcome-skill-grid" aria-label="小树推荐技能">
          {visible.map((skill) => {
            const selected = selectedIds.includes(skill.id);
            return (
              <button
                key={skill.id}
                type="button"
                disabled={disabled}
                aria-pressed={selected}
                aria-label={`使用技能 ${skill.label}`}
                title={`${skill.description} · ${selected ? "已选中，点击取消" : "点击挂到输入区"}`}
                onClick={() => onToggle(skill.id)}
                className={cn(
                  "flex items-center gap-2 overflow-hidden rounded-2xl border-[0.5px] border-[#363636] bg-white/[0.03] p-3 text-left transition-colors hover:bg-white/[0.06]",
                  "village-agent-welcome-skill-card",
                  selected && "is-selected",
                  disabled && "pointer-events-none opacity-40",
                )}
              >
                <SkillGlyph
                  skillId={skill.id}
                  coverImage={skill.coverImage}
                  className="size-9 rounded-[10px]"
                  iconClassName="size-4"
                />
                <span className="min-w-0 flex-1">
                  <span className="village-agent-welcome-skill-title">{skill.label}</span>
                  <span className="village-agent-welcome-skill-tag">/{skillDisplayTag(skill)}</span>
                </span>
                {selected && <BadgeCheck className="size-4 shrink-0 text-cyan-200/90" aria-hidden />}
              </button>
            );
          })}
        </div>
      </div>
    );
  }

  return (
    <div
      className={cn(FREEZONE_AGENT_WELCOME_CLASS, "village-agent-welcome flex w-full flex-col gap-3")}
      data-welcome-ui="village-agent-standard"
    >
      <div className="village-agent-greeting">
        <span className="village-agent-user-mark" aria-hidden>{greetingName.slice(0, 1)}</span>
        <div className="min-w-0">
          <p className="text-[15px] font-semibold leading-6 text-[#f2f2f3]">Hi {greetingName}!</p>
          <p className="text-[22px] font-semibold leading-8 tracking-[-0.035em] text-[#f2f2f3]">今天一起创作点什么？</p>
        </div>
      </div>
      {showSkillPicker && (
        <details
          className="village-agent-skill-picker"
          open={skillPickerOpen}
          onToggle={(event) => setSkillPickerOpen(event.currentTarget.open)}
        >
          <summary className="village-agent-skill-summary flex cursor-pointer list-none items-center gap-2 text-[11px] text-white/42 [&::-webkit-details-marker]:hidden">
            <Bot className="size-3.5" aria-hidden />
            <span>更多能力</span>
            <span className="ml-auto">
              {selectedIds.length > 0 ? `${selectedIds.length} 已指定 · 点开调整` : "自动匹配 · 可手动指定"}
            </span>
          </summary>
          <div className="mt-2 flex items-center justify-between gap-2">
            <span className="text-[10px] text-white/30">已显示 {visible.length} / {CANVAS_AGENT_SKILLS.length}</span>
            {pageCount > 1 && (
              <button
                type="button"
                onClick={refreshBatch}
                className="inline-flex items-center gap-1 rounded-full border border-white/[0.10] px-2 py-1 text-[10px] text-white/55 transition-colors hover:bg-white/[0.06] hover:text-white"
                title={LIBTV_WELCOME_SKILL_REFRESH}
              >
                <RefreshCw className="size-3 shrink-0" />
                换一组
              </button>
            )}
          </div>
          <div className="mt-2 grid grid-cols-2 gap-2">
            {visible.map((skill) => {
              const selected = selectedIds.includes(skill.id);
              return (
                <button
                  key={skill.id}
                  type="button"
                  disabled={disabled}
                  aria-pressed={selected}
                  aria-label={`使用技能 ${skill.label}`}
                  title={`${skill.description} · ${selected ? "已选中，点击取消" : "点击挂到输入区"}`}
                  onClick={() => onToggle(skill.id)}
                  className={cn(
                    LIBTV_SKILL_CARD_CLASS,
                    "min-h-[54px] shadow-[inset_0_1px_0_rgba(255,255,255,0.04)]",
                    selected && "border-cyan-300/50 bg-cyan-400/[0.10] ring-1 ring-cyan-200/25",
                    disabled && "pointer-events-none opacity-40",
                  )}
                >
                  <SkillGlyph skillId={skill.id} coverImage={skill.coverImage} className="size-7 rounded-lg" iconClassName="size-3.5" />
                  <span className="flex min-w-0 flex-1 flex-col gap-0.5">
                    <span className="truncate text-[12px] font-semibold leading-5 text-[#f7f7f7]">{skill.label}</span>
                    <span className="truncate text-[10px] leading-[14px] text-[#919191]">
                      {selected ? "已选中 · 点取消" : skill.description}
                    </span>
                  </span>
                  {selected && <BadgeCheck className="size-4 shrink-0 text-cyan-200/90" aria-hidden />}
                </button>
              );
            })}
          </div>
        </details>
      )}
    </div>
  );
}

export function FreezoneComposerTags({
  skillIds,
  pinnedNodes,
  onRemoveSkill,
  onUnpinNode,
  availableSkills = CANVAS_AGENT_SKILLS,
}: {
  skillIds: readonly string[];
  pinnedNodes: readonly CanvasAgentNodeRef[];
  onRemoveSkill: (skillId: string) => void;
  onUnpinNode?: (nodeId: string) => void;
  availableSkills?: readonly CanvasAgentSkill[];
}) {
  const skills = selectedCanvasAgentSkills(skillIds, availableSkills);
  if (skills.length === 0 && pinnedNodes.length === 0) return null;
  return (
    <div className="neo-agent-reference-strip village-agent-reference-strip flex flex-wrap gap-1.5 px-3.5 pt-2.5">
      {skills.map((skill) => {
        const Icon = skillIconFor(skill.id);
        return (
          <button
            key={skill.id}
            type="button"
            onClick={() => onRemoveSkill(skill.id)}
            className="village-agent-skill-chip inline-flex max-w-full items-center gap-1 rounded-full border border-fuchsia-300/30 bg-fuchsia-400/[0.12] px-2 py-0.5 text-[11px] text-fuchsia-50 shadow-[inset_0_1px_0_rgba(255,255,255,0.06)]"
            title="取消手动指定 Skill"
          >
            <Icon className="size-3 shrink-0 opacity-90" strokeWidth={2.25} />
            <span className="truncate">{skill.label}</span>
            <X className="size-3 shrink-0 opacity-70" />
          </button>
        );
      })}
      {pinnedNodes.map((node) => (
        <CanvasNodeReferenceChip
          key={`pin-${node.id}`}
          node={node}
          onClick={() => onUnpinNode?.(node.id)}
        />
      ))}
    </div>
  );
}

export function FreezoneComposerSkillDrawer({
  skillIds,
  onToggleSkill,
  skills = CANVAS_AGENT_SKILLS,
  onOpenStore,
  disabled,
}: {
  skillIds: readonly string[];
  onToggleSkill: (skillId: string) => void;
  skills?: readonly CanvasAgentSkill[];
  onOpenStore?: () => void;
  disabled?: boolean;
}) {
  const [query, setQuery] = useState("");
  const [activeGroup, setActiveGroup] = useState<SkillGroupId>("all");
  const selected = selectedCanvasAgentSkills(skillIds, skills);
  const normalizedQuery = query.trim().toLocaleLowerCase();
  const visibleSkills = useMemo(
    () => skills.filter((skill) => {
      const inGroup = activeGroup === "all" || SKILL_GROUP_BY_ID[skill.id] === activeGroup;
      if (!inGroup) return false;
      if (!normalizedQuery) return true;
      return [skill.label, skill.description, skillDisplayTag(skill)]
        .some((value) => value.toLocaleLowerCase().includes(normalizedQuery));
    }),
    [activeGroup, normalizedQuery, skills],
  );
  return (
    <div
      className="village-agent-skill-drawer"
      data-agent-skill-drawer="true"
      role="region"
      aria-label="小树技能栏"
    >
      <div className="village-agent-skill-drawer-head">
        <span>技能</span>
        <div className="flex items-center gap-2">
          <em>{selected.length > 0 ? `${selected.length} 已指定` : "自动匹配"}</em>
          {onOpenStore && (
            <button
              type="button"
              onClick={onOpenStore}
              className="inline-flex items-center gap-1 rounded-full border border-white/[0.09] bg-white/[0.04] px-2 py-1 text-[10px] not-italic text-white/58 transition hover:bg-white/[0.075] hover:text-white"
            >
              <Store className="size-3" />
              技能商店
            </button>
          )}
        </div>
      </div>
      {selected.length > 0 && (
        <div className="village-agent-skill-drawer-selected" aria-label="已指定技能">
          {selected.map((skill) => {
            const Icon = skillIconFor(skill.id);
            return (
              <button
                key={skill.id}
                type="button"
                className="village-agent-skill-drawer-selected-chip"
                onClick={() => onToggleSkill(skill.id)}
                disabled={disabled}
                title={`取消指定 ${skill.label}`}
              >
                <Icon className="size-3.5 shrink-0" strokeWidth={2.15} />
                <span>{skill.label}</span>
                <X className="size-3 shrink-0 opacity-55" />
              </button>
            );
          })}
        </div>
      )}
      <div className="village-agent-skill-drawer-toolbar">
        <label className="village-agent-skill-search">
          <Search className="size-3.5 shrink-0 text-white/38" aria-hidden />
          <span className="sr-only">搜索技能</span>
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="搜索技能"
            aria-label="搜索技能"
            disabled={disabled}
          />
          {query && (
            <button
              type="button"
              className="village-agent-skill-search-clear"
              onClick={() => setQuery("")}
              aria-label="清除技能搜索"
              title="清除搜索"
            >
              <X className="size-3" aria-hidden />
            </button>
          )}
        </label>
        <div className="village-agent-skill-filter-row" role="tablist" aria-label="技能分类">
          {SKILL_GROUPS.map((group) => (
            <button
              key={group.id}
              type="button"
              role="tab"
              aria-selected={activeGroup === group.id}
              className={cn(
                "village-agent-skill-filter",
                activeGroup === group.id && "is-active",
              )}
              onClick={() => setActiveGroup(group.id)}
              disabled={disabled}
            >
              {group.label}
            </button>
          ))}
        </div>
      </div>
      {visibleSkills.length === 0 ? (
        <div className="village-agent-skill-empty" role="status">
          没找到匹配技能
        </div>
      ) : (
        <div className="village-agent-skill-drawer-grid" aria-label="筛选后的技能">
          {visibleSkills.map((skill) => {
            const selectedSkill = skillIds.includes(skill.id);
            return (
              <button
                key={skill.id}
                type="button"
                className={cn(
                  "village-agent-skill-drawer-item",
                  selectedSkill && "is-selected",
                )}
                onClick={() => onToggleSkill(skill.id)}
                disabled={disabled}
                aria-pressed={selectedSkill}
                title={skill.description}
              >
                <SkillGlyph skillId={skill.id} coverImage={skill.coverImage} className="size-11 rounded-[14px]" iconClassName="size-4" />
                <span className="min-w-0">
                  <strong>{skill.label}</strong>
                  <em>{selectedSkill ? "已指定 · 点击取消" : skill.description}</em>
                </span>
                {selectedSkill ? (
                  <BadgeCheck className="size-4 shrink-0 text-cyan-200/90" aria-hidden />
                ) : (
                  <Circle className="size-4 shrink-0 text-white/18" aria-hidden />
                )}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

export function CanvasNodePreviewThumb({
  node,
  className,
  onPreviewError,
}: {
  node: CanvasAgentNodeRef;
  className?: string;
  onPreviewError?: () => void;
}) {
  const src = node.previewUrl?.trim();
  const previewKind = node.previewKind;
  if (!src && previewKind !== "audio" && previewKind !== "text") return null;

  const alt = `${node.label || node.type || node.id} 节点预览`;
  return (
    <span
      className={cn(
        "relative size-7 shrink-0 overflow-hidden rounded-[6px] border border-white/15 bg-black/35",
        previewKind === "audio" && "canvas-agent-preview-audio",
        previewKind === "text" && "canvas-agent-preview-text",
        className,
      )}
      role="img"
      aria-label={alt}
    >
      {previewKind === "audio" ? (
        <>
          <span className="canvas-agent-preview-wave" aria-hidden>
            {[34, 58, 82, 48, 72, 42, 64, 30].map((height, index) => (
              <i key={`${height}-${index}`} style={{ height: `${height}%` }} />
            ))}
          </span>
          <Play className="pointer-events-none absolute bottom-0.5 right-0.5 size-2 text-white/90 drop-shadow" fill="currentColor" aria-hidden />
        </>
      ) : previewKind === "text" ? (
        <>
          <FileText className="absolute left-1 top-1 size-3 text-white/75" aria-hidden />
          <span className="canvas-agent-preview-lines" aria-hidden>
            <i /><i /><i /><i />
          </span>
        </>
      ) : previewKind === "video" ? (
        <video
          src={src}
          poster={node.previewPosterUrl}
          muted
          playsInline
          preload="metadata"
          aria-hidden
          className="h-full w-full object-cover"
          onError={onPreviewError}
        />
      ) : (
        <img
          src={src}
          alt=""
          draggable={false}
          className="h-full w-full object-cover"
          onError={onPreviewError}
        />
      )}
      {previewKind === "video" && (
        <Film className="pointer-events-none absolute bottom-0.5 right-0.5 size-2.5 text-white/90 drop-shadow" aria-hidden />
      )}
    </span>
  );
}

function CanvasNodeReferenceChip({
  node,
  onClick,
}: {
  node: CanvasAgentNodeRef;
  onClick?: () => void;
}) {
  const [previewFailed, setPreviewFailed] = useState(false);
  const hasPreview = Boolean(node.previewUrl?.trim()) && !previewFailed;
  const hasVisualPreview = (node.previewKind === "audio" || node.previewKind === "text") && !previewFailed;
  const label = node.label || node.type || node.id;
  const kindLabel = node.previewKind === "video"
    ? "视频"
    : node.previewKind === "audio"
      ? "音频"
      : node.previewKind === "text"
        ? "文本"
        : "图片";
  const title = "取消固定 Agent 引用";
  const actionLabel = `取消固定 Agent 引用：${label}`;

  if (hasPreview || hasVisualPreview) {
    return (
      <button
        type="button"
        onClick={onClick}
        className="neo-agent-reference-thumb village-agent-node-ref-card inline-flex h-8 max-w-[8rem] shrink-0 items-center gap-1.5 overflow-hidden rounded-[10px] border border-white/20 bg-black/35 py-1 pl-1 pr-1.5 shadow-sm transition hover:border-white/45"
        title={title}
        aria-label={actionLabel}
      >
        <CanvasNodePreviewThumb
          node={node}
          className="size-6 rounded-[7px] border-0"
          onPreviewError={() => setPreviewFailed(true)}
        />
        <span className="truncate text-[12px] font-semibold text-white/78">{kindLabel}</span>
        <X className="size-3 shrink-0 text-white/45" aria-hidden />
      </button>
    );
  }
  return null;
}

export interface AgentPlanStep {
  id: string;
  label: string;
  status: "done" | "running" | "pending" | "failed" | "blocked" | "skipped" | "unverified";
  detail?: string;
}

const PLAN_STATUSES = new Set<AgentPlanStep["status"]>([
  "done",
  "running",
  "pending",
  "failed",
  "blocked",
  "skipped",
  "unverified",
]);

function planStepsFromWorkflow(workflow: AgentWorkflowState): AgentPlanStep[] {
  return workflow.steps
    .filter((step) => PLAN_STATUSES.has(step.status))
    .map((step) => ({
      id: step.id,
      label: step.label,
      status: step.status,
    }));
}

/** Prefer the server-owned workflow receipt; tool text is only a legacy fallback. */
export function deriveAgentPlanFromMessages(
  messages: readonly ChatMessage[],
  busy: boolean,
  activeWorkflow?: AgentWorkflowState | null,
): AgentPlanStep[] {
  if (activeWorkflow) {
    const steps = planStepsFromWorkflow(activeWorkflow);
    if (steps.length > 0) return steps;
  }
  if (!busy) return [];
  const latestUserTurnId = [...messages]
    .reverse()
    .find((message) => message.role === "user")
    ?.turnId;
  const tools = messages
    .filter((message) => (
      message.role === "tool"
      && Boolean(latestUserTurnId)
      && message.turnId === latestUserTurnId
    ))
    .slice(-10);
  if (tools.length === 0 && busy) {
    return [{ id: "starting", label: "正在启动执行器", status: "running" }];
  }
  return tools.map((message, index) => {
    const firstLine = String(message.text || "").split(/\r?\n/).find((line) => line.trim()) || "工具步骤";
    const label = firstLine.replace(/^\[|\]$/g, "").slice(0, 96);
    const isLast = index === tools.length - 1;
    return {
      id: message.id,
      label,
      status: busy && isLast ? "running" : "done",
    };
  });
}

export function FreezoneAgentCompactStatus({
  busy,
  connected = true,
  label,
  progressLabel,
  stage,
  elapsedSeconds,
  lastProgressAgeSeconds,
  workerAlive,
  timeline,
  workflowRun,
  recovery,
  onStop,
  onResumeRecovery,
  onDismiss,
  onRetryWorkflowItems,
  onRetryWorkflowStep,
  onDismissWorkflowItems,
  onRepairWorkflowAssetBindings,
  onAuthorizeCompose,
  onAuthorizeMedia,
  retryingWorkflowItems = false,
  retryingWorkflowStepId = null,
  dismissingWorkflowItems = false,
  repairingWorkflowAssetBindings = false,
  authorizingCompose = false,
  authorizingMediaStep = null,
  failureDismissed = false,
}: {
  busy: boolean;
  connected?: boolean;
  label?: string | null;
  progressLabel?: string | null;
  stage?: string | null;
  elapsedSeconds?: number | null;
  lastProgressAgeSeconds?: number | null;
  workerAlive?: boolean | null;
  timeline?: AgentExecutionTimeline | null;
  workflowRun?: WorkflowRun | null;
  recovery?: ChatRecoveryState | null;
  onStop: () => void;
  onResumeRecovery: () => void;
  onDismiss?: (failureKey: string) => void;
  onRetryWorkflowItems?: (groups: WorkflowItemSelectionGroup[]) => void;
  onRetryWorkflowStep?: (stepId: string) => void;
  onDismissWorkflowItems?: (groups: WorkflowItemSelectionGroup[]) => void;
  onRepairWorkflowAssetBindings?: () => void;
  onAuthorizeCompose?: () => void;
  onAuthorizeMedia?: (
    stepId: "storyboard_images" | "shot_videos",
    itemIds?: readonly string[],
  ) => void;
  retryingWorkflowItems?: boolean;
  retryingWorkflowStepId?: string | null;
  dismissingWorkflowItems?: boolean;
  repairingWorkflowAssetBindings?: boolean;
  authorizingCompose?: boolean;
  authorizingMediaStep?: "storyboard_images" | "shot_videos" | null;
  failureDismissed?: boolean;
}) {
  const [expanded, setExpanded] = useState(true);
  const [dismissConfirmationOpen, setDismissConfirmationOpen] = useState(false);
  const workflowRecovery = useMemo(
    () => workflowRecoveryFromRun(workflowRun),
    [workflowRun],
  );
  const canvasNodes = useCanvasStore((state) => state.nodes);
  const composeAuthorizationRequest = useMemo(
    () => workflowComposeAuthorizationRequestFromRun(workflowRun),
    [workflowRun],
  );
  const mediaAuthorizationRequest = useMemo(
    () => workflowMediaAuthorizationRequestFromRun(workflowRun),
    [workflowRun],
  );
  const releaseReadiness = useMemo(
    () => workflowReleaseReadinessFromRun(workflowRun),
    [workflowRun],
  );
  const releaseReadinessRequired = workflowReleaseRequiresNotice(releaseReadiness);
  const releaseBlockingChecks = releaseReadiness
    ? workflowReleaseBlockingChecks(releaseReadiness)
    : [];
  const mediaAuthorizationStepId = mediaAuthorizationRequest?.step_id ?? null;
  const retryGroups = useMemo(() => {
    if (!workflowRun || !["failed", "paused"].includes(workflowRun.status)) return [];
    return Object.values(workflowRun.step_states).flatMap((step) => {
      if (step.id === mediaAuthorizationStepId) return [];
      if (workflowStepHasBlockingCanvasRecovery(workflowRun, step.id)) return [];
      if (step.execution_mode !== "itemized" && step.retry_scope !== "failed_items_only") {
        return [];
      }
      const artifact = workflowRun.artifacts[step.id];
      if (!artifact || typeof artifact !== "object" || Array.isArray(artifact)) return [];
      const itemStates = (artifact as { item_states?: unknown }).item_states;
      if (!itemStates || typeof itemStates !== "object" || Array.isArray(itemStates)) return [];
      const items = Object.entries(itemStates)
        .filter(([, item]) => item && typeof item === "object" && (item as { status?: unknown }).status === "failed")
        .map(([id, item]) => {
          const state = item as { label?: unknown; error?: unknown; attempt?: unknown };
          return {
            id,
            label: typeof state.label === "string" && state.label.trim() ? state.label.trim() : id,
            error: typeof state.error === "string" ? state.error.trim() : "",
            attempt: typeof state.attempt === "number" ? state.attempt : null,
          };
        });
      return items.length ? [{ stepId: step.id, stepLabel: step.label, items }] : [];
    });
  }, [mediaAuthorizationStepId, workflowRun]);
  const wholeStepRetries = useMemo(() => {
    if (!workflowRun || !["failed", "paused"].includes(workflowRun.status)) return [];
    return Object.values(workflowRun.step_states)
      .filter((step) => (
        step.id !== mediaAuthorizationStepId
        && !workflowStepHasBlockingCanvasRecovery(workflowRun, step.id)
        && workflowStepCanRetryWholeStep(step)
      ))
      .map((step) => ({
        stepId: step.id,
        stepLabel: step.label,
        error: step.error,
      }));
  }, [mediaAuthorizationStepId, workflowRun]);
  const [selectedRetryItems, setSelectedRetryItems] = useState<Set<string>>(new Set());
  const [locallyDismissedFailureKey, setLocallyDismissedFailureKey] = useState<string | null>(null);
  const failureDisplayKey = workflowFailureDisplayKey(workflowRun);
  useEffect(() => {
    if (locallyDismissedFailureKey && failureDisplayKey !== locallyDismissedFailureKey) {
      setLocallyDismissedFailureKey(null);
    }
  }, [failureDisplayKey, locallyDismissedFailureKey]);
  useEffect(() => {
    const available = new Set(
      retryGroups.flatMap((group) => group.items.map((item) => `${group.stepId}:${item.id}`)),
    );
    setSelectedRetryItems((current) => new Set([...current].filter((key) => available.has(key))));
  }, [retryGroups]);
  const hasRetryableItems = retryGroups.length > 0;
  const hasRetryableSteps = wholeStepRetries.length > 0;
  const hasRetryableFailures = hasRetryableItems || hasRetryableSteps;
  const hasActionableFailures = hasRetryableFailures
    || Boolean(
      workflowRecovery
      || composeAuthorizationRequest
      || mediaAuthorizationRequest
    );
  const currentFailureDismissed = Boolean(
    failureDismissed
    || (failureDisplayKey && failureDisplayKey === locallyDismissedFailureKey),
  );
  const selectedItemGroups = retryGroups
    .map((group) => ({
      stepId: group.stepId,
      itemIds: group.items
        .filter((item) => selectedRetryItems.has(`${group.stepId}:${item.id}`))
        .map((item) => item.id),
    }))
    .filter((group) => group.itemIds.length > 0);
  const itemActionBusy = retryingWorkflowItems || dismissingWorkflowItems;
  const failureActionBusy = itemActionBusy || Boolean(retryingWorkflowStepId);
  if (!busy && !recovery && !hasActionableFailures && !releaseReadinessRequired) {
    return null;
  }
  if (hasActionableFailures && currentFailureDismissed) return null;

  const recovering = Boolean(recovery);
  const recoveryBlocked = Boolean(recovery?.packet.retryable === false);
  const workflowRecoveryTitle = workflowRecovery
    ? "title" in workflowRecovery && workflowRecovery.title.trim()
      ? workflowRecovery.title
        : workflowRecovery.action === "request_compose_authorization"
          ? "等待最终合成授权"
          : workflowRecovery.action === "request_media_authorization"
            ? "等待付费媒体授权"
            : workflowRecovery.action === "retry_failed_items"
              ? "等待失败项恢复授权"
              : "需要恢复工作流"
    : composeAuthorizationRequest
      ? "等待最终合成授权"
      : null;
  const workflowRecoveryInstruction = workflowRecovery?.instruction
    || (composeAuthorizationRequest
      ? "确认后只恢复原 Run 的最终合成步骤。"
      : "");
  const workflowRecoveryTargetNodeIds = (
    workflowRecovery?.schema === "workflow_step_recovery.v1"
      ? workflowRecovery.target_node_ids ?? []
      : []
  );
  const focusRecoveryNodes = (targetNodeIds: readonly string[]) => {
    if (!focusWorkflowCanvasNodes(targetNodeIds)) {
      toast.error("恢复目标节点已不在当前画布");
    }
  };
  const focusWorkflowRecoveryNode = () => {
    focusRecoveryNodes(workflowRecoveryTargetNodeIds);
  };
  const workflowAssetRepairPlans = (
    workflowRecovery?.schema === "workflow_step_recovery.v1"
      ? planWorkflowAssetBindingRepairs(workflowRecovery, canvasNodes)
      : []
  );
  const canFocusWorkflowRecoveryNodes = Boolean(
    workflowRecovery
    && workflowRecovery.requires_paid_media === false
    && workflowRecoveryTargetNodeIds.length > 0
    && [
      "repair_canvas_asset_binding",
      "repair_asset_ledger",
      "repair_canvas_asset_path",
      "reduce_asset_references",
    ].includes(workflowRecovery.action)
  );
  const canRepairWorkflowAssetBindings = Boolean(
    workflowRecovery
    && workflowRecovery.requires_paid_media === false
    && workflowAssetRepairPlans.length > 0
    && onRepairWorkflowAssetBindings
  );
  const title = recovery
    ? recovery.autoRetrying
      ? "正在从恢复点继续"
      : "已保存恢复点"
    : workflowRecovery?.schema === "canvas_command_recovery.v1"
      ? "需要先修复前置条件"
      : releaseReadinessRequired && releaseReadiness
        ? releaseReadiness.status === "ready"
          ? "成片已通过发布门"
          : releaseReadiness.status === "blocked"
            ? "成片已生成，但发布门未通过"
            : "成片发布状态未验证"
      : workflowRecoveryTitle || timeline?.title || label?.trim() || "正在执行画布任务";
  const normalizedElapsed = typeof elapsedSeconds === "number" && Number.isFinite(elapsedSeconds)
    ? Math.max(0, Math.round(elapsedSeconds))
    : null;
  const normalizedAge = typeof lastProgressAgeSeconds === "number" && Number.isFinite(lastProgressAgeSeconds)
    ? Math.max(0, Math.round(lastProgressAgeSeconds))
    : null;
  const stageLabel = humanizeAgentExecutionStage(stage);
  const activityLabel = workerAlive === false
    ? "执行器正在重连"
    : normalizedAge !== null && normalizedAge >= 18
      ? `等待回传 ${normalizedAge} 秒`
      : normalizedElapsed !== null && normalizedElapsed > 0
        ? `已运行 ${formatExecutionDuration(normalizedElapsed)}`
        : null;
  const timelineElapsed = formatAgentExecutionDuration(timeline?.elapsedMs);
  const secondaryLabel = recovery?.message?.trim()
    || (workflowRecovery
      ? composeAuthorizationRequest
        ? "最终合成未执行 · 授权后恢复原步骤"
        : workflowRecovery.requires_paid_media
          ? mediaAuthorizationRequest
            ? "付费媒体未提交 · 授权后恢复原步骤"
            : "付费命令未执行 · 修复前置条件后再重新出图"
          : "画布命令未执行 · 请先完成恢复步骤"
      : null)
    || (releaseReadinessRequired && releaseReadiness
      ? workflowReleaseSummary(releaseReadiness)
      : null)
    || [timeline?.stageLabel || stageLabel, timelineElapsed ? `已运行 ${timelineElapsed}` : activityLabel]
      .filter(Boolean)
      .join(" · ");
  const health = recovery
    ? "recovery"
    : releaseReadinessRequired && releaseReadiness
      ? releaseReadiness.status
    : workerAlive === false
      ? "reconnecting"
      : normalizedAge !== null && normalizedAge >= 18
        ? "waiting"
        : "running";

  const effectiveProgressLabel = timeline?.totalCount
    ? `步骤 ${timeline.completedCount}/${timeline.totalCount}`
    : progressLabel;

  return (
    <div
      className="w-full overflow-hidden rounded-xl border border-white/[0.075] bg-[#1b1b1d]/96 text-[11px] text-white/68 shadow-[0_10px_30px_rgba(0,0,0,0.22)]"
      data-agent-compact-status={
        recovering ? "recovery" : releaseReadinessRequired ? "release" : "running"
      }
      data-agent-health={health}
      role="status"
      aria-live="polite"
    >
      <div className="flex min-h-11 items-center gap-2 px-2.5 py-1.5">
        <span className="inline-flex size-6 shrink-0 items-center justify-center rounded-full border border-white/[0.07] bg-white/[0.045] text-white/58">
          {recovering ? (
            <RefreshCw className={cn("size-3", recovery?.autoRetrying && "animate-spin")} aria-hidden />
          ) : hasActionableFailures ? (
            <button
              type="button"
              className="inline-flex size-6 items-center justify-center rounded-full text-rose-100/65 transition hover:bg-white/[0.08] hover:text-white"
              aria-label="关闭失败详情"
              title="关闭这个失败详情，后续聊天不再重复显示"
              onClick={() => {
                if (!failureDisplayKey) return;
                setLocallyDismissedFailureKey(failureDisplayKey);
                onDismiss?.(failureDisplayKey);
              }}
            >
              <X className="size-3 text-rose-200/80" aria-hidden />
            </button>
          ) : releaseReadinessRequired ? (
            releaseReadiness?.status === "ready" ? (
              <BadgeCheck className="size-3 text-emerald-200/85" aria-hidden />
            ) : (
              <ShieldAlert className="size-3 text-amber-200/85" aria-hidden />
            )
          ) : (
            <Loader2 className="size-3 animate-spin" aria-hidden />
          )}
        </span>
        <span className="flex min-w-0 flex-1 flex-col justify-center" title={[title, secondaryLabel].filter(Boolean).join(" · ")}>
          <span className="truncate font-medium text-white/82">{title}</span>
          {secondaryLabel && (
            <span className="truncate text-[9px] leading-3.5 text-white/38">{secondaryLabel}</span>
          )}
        </span>
        {!recovering && effectiveProgressLabel && (
          <span className="shrink-0 tabular-nums text-white/36">{effectiveProgressLabel}</span>
        )}
        {!recovering && timeline && timeline.steps.length > 0 && (
          <button
            type="button"
            onClick={() => setExpanded((current) => !current)}
            className="inline-flex size-6 shrink-0 items-center justify-center rounded-lg text-white/38 transition hover:bg-white/[0.06] hover:text-white/75"
            aria-label={expanded ? "收起执行详情" : "展开执行详情"}
            aria-expanded={expanded}
          >
            {expanded ? <ChevronUp className="size-3.5" /> : <ChevronDown className="size-3.5" />}
          </button>
        )}
        {recovering ? (
          <button
            type="button"
            onClick={onResumeRecovery}
            disabled={recovery?.autoRetrying || recoveryBlocked}
            className="inline-flex h-6 shrink-0 items-center rounded-lg bg-white px-2.5 text-[10px] font-medium text-black transition-colors hover:bg-white/88 disabled:cursor-default disabled:opacity-45"
          >
            {recovery?.autoRetrying ? "恢复中" : recoveryBlocked ? "已失效" : "继续"}
          </button>
        ) : busy ? (
          <button
            type="button"
            onClick={onStop}
            className="inline-flex h-6 shrink-0 items-center rounded-lg border border-white/[0.09] bg-white/[0.035] px-2.5 text-[10px] text-white/62 transition-colors hover:bg-white/[0.09] hover:text-white"
          >
            停止
          </button>
        ) : null}
      </div>
      {releaseReadinessRequired && releaseReadiness && (
        <div
          className={cn(
            "border-t px-3 py-2.5",
            releaseReadiness.status === "ready"
              ? "border-emerald-200/[0.14] bg-emerald-200/[0.035]"
              : "border-amber-200/[0.14] bg-amber-200/[0.035]",
          )}
          data-workflow-release-readiness="v1"
          data-release-status={releaseReadiness.status}
          data-can-publish={String(releaseReadiness.can_publish)}
        >
          <div className="flex items-center gap-2">
            <span
              className={cn(
                "text-[10px] font-medium",
                releaseReadiness.status === "ready"
                  ? "text-emerald-100/90"
                  : "text-amber-100/88",
              )}
            >
              {workflowReleaseStatusLabel(releaseReadiness.status)}
            </span>
            <span className="text-[9px] text-white/35">
              {workflowReleaseSummary(releaseReadiness)}
            </span>
          </div>
          {releaseBlockingChecks.length > 0 && (
            <div className="mt-1 text-[9px] leading-4 text-white/42">
              待处理检查：{releaseBlockingChecks.join("、")}
            </div>
          )}
          {releaseReadiness.status !== "ready" && (
            <div className="mt-0.5 text-[9px] leading-4 text-white/35">
              工程发布门未通过时，不能把成片标记为可交付或可发布。
            </div>
          )}
        </div>
      )}
      {!recovering && timeline && expanded && timeline.steps.length > 0 && (
        <div className="max-h-56 overflow-y-auto border-t border-white/[0.055] px-3 py-2" data-agent-execution-timeline="v1">
          <div className="mb-1.5 flex items-center gap-2 text-[9px] uppercase tracking-[0.08em] text-white/28">
            <span>当前阶段</span>
            <span className="h-px flex-1 bg-white/[0.05]" />
          </div>
          <ol className="relative space-y-1.5 before:absolute before:bottom-2 before:left-[7px] before:top-2 before:w-px before:bg-white/[0.075]">
            {timeline.steps.map((step) => (
              <ExecutionTimelineStepRow key={step.id} step={step} />
            ))}
          </ol>
        </div>
      )}
      {(workflowRecovery || composeAuthorizationRequest || mediaAuthorizationRequest) && (
        <div
          className="border-t border-amber-200/[0.14] bg-amber-200/[0.035] px-3 py-2.5"
          data-workflow-canvas-recovery="v1"
          data-workflow-recovery-schema={
            workflowRecovery?.schema
            ?? (mediaAuthorizationRequest
              ? "workflow_media_authorization_request"
              : "compose-authorization-request")
          }
          data-workflow-canvas-recovery-action={
            workflowRecovery?.action
            ?? (mediaAuthorizationRequest
              ? "request_media_authorization"
              : "request_compose_authorization")
          }
        >
          <div className="flex items-start gap-2">
            <ShieldAlert className="mt-0.5 size-3.5 shrink-0 text-amber-200/75" aria-hidden />
            <div className="min-w-0 flex-1">
              <div className="text-[10px] font-medium text-amber-100/85">{workflowRecoveryTitle}</div>
              <p className="mt-1 text-[10px] leading-4 text-white/52">{workflowRecoveryInstruction}</p>
              <div className="mt-1.5 flex flex-wrap gap-x-2 gap-y-1 text-[9px] text-white/34">
                <span>
                  {workflowRecovery?.auto_retry_allowed
                    ? "可自动重试"
                    : composeAuthorizationRequest
                      ? "不会自动重放原合成命令"
                      : mediaAuthorizationRequest
                        ? "不会自动重放原付费媒体命令"
                      : "不会自动重放原付费命令"}
                </span>
                {workflowRecovery?.requires_paid_media && !mediaAuthorizationRequest && (
                  <span>修复前置条件后需重新出图</span>
                )}
              </div>
              {composeAuthorizationRequest && (
                <button
                  type="button"
                  className="mt-2 inline-flex h-7 items-center justify-center rounded-lg bg-amber-100 px-3 text-[10px] font-medium text-black transition hover:bg-amber-50 disabled:cursor-default disabled:opacity-45"
                  disabled={busy || !connected || authorizingCompose || !onAuthorizeCompose}
                  title={busy || !connected ? "当前回合结束后可发起恢复授权" : undefined}
                  onClick={() => onAuthorizeCompose?.()}
                >
                  {authorizingCompose ? "正在授权…" : "授权并继续合成"}
                </button>
              )}
              {mediaAuthorizationRequest && (
                <button
                  type="button"
                  className="mt-2 inline-flex h-7 items-center justify-center rounded-lg bg-amber-100 px-3 text-[10px] font-medium text-black transition hover:bg-amber-50 disabled:cursor-default disabled:opacity-45"
                  disabled={
                    busy
                    || !connected
                    || authorizingMediaStep === mediaAuthorizationRequest.step_id
                    || !onAuthorizeMedia
                  }
                  title={busy || !connected ? "当前回合结束后可发起恢复授权" : undefined}
                  onClick={() => (
                    mediaAuthorizationRequest.retry_scope === "failed_items_only"
                      ? onAuthorizeMedia?.(
                        mediaAuthorizationRequest.step_id,
                        mediaAuthorizationRequest.item_ids,
                      )
                      : onAuthorizeMedia?.(mediaAuthorizationRequest.step_id)
                  )}
                >
                  {authorizingMediaStep === mediaAuthorizationRequest.step_id
                    ? "正在授权…"
                    : mediaAuthorizationRequest.retry_scope === "failed_items_only"
                      ? mediaAuthorizationRequest.media_kind === "image"
                        ? `授权并重试失败分镜（${mediaAuthorizationRequest.item_ids.length}）`
                        : `授权并重试失败视频（${mediaAuthorizationRequest.item_ids.length}）`
                      : mediaAuthorizationRequest.media_kind === "image"
                        ? "授权并恢复出图"
                        : "授权并恢复出视频"}
                </button>
              )}
              {canFocusWorkflowRecoveryNodes && (
                <button
                  type="button"
                  className="mt-2 inline-flex h-7 items-center justify-center rounded-lg border border-amber-100/25 bg-amber-100/[0.06] px-3 text-[10px] font-medium text-amber-50/90 transition hover:bg-amber-100/[0.12]"
                  data-workflow-recovery-focus="v1"
                  onClick={focusWorkflowRecoveryNode}
                >
                  定位问题节点
                </button>
              )}
              {canRepairWorkflowAssetBindings && (
                <button
                  type="button"
                  className="ml-2 mt-2 inline-flex h-7 items-center justify-center rounded-lg border border-emerald-100/25 bg-emerald-100/[0.06] px-3 text-[10px] font-medium text-emerald-50/90 transition hover:bg-emerald-100/[0.12]"
                  data-workflow-recovery-repair="v1"
                  disabled={repairingWorkflowAssetBindings || !onRepairWorkflowAssetBindings}
                  onClick={() => onRepairWorkflowAssetBindings?.()}
                >
                  {repairingWorkflowAssetBindings
                    ? "正在整理…"
                    : `整理重复绑定（${workflowAssetRepairPlans.length} 组）`}
                </button>
              )}
            </div>
          </div>
        </div>
      )}
      {hasRetryableSteps && (
        <div className="border-t border-rose-300/[0.12] bg-rose-300/[0.025] px-3 py-2.5" data-workflow-step-retry="v1">
          <div className="text-[10px] font-medium text-rose-100/80">整步失败可重试</div>
          <div className="mt-1.5 space-y-1.5">
            {wholeStepRetries.map((step) => (
              <div key={step.stepId} className="flex items-start justify-between gap-2">
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[10px] text-white/62" title={step.stepLabel}>{step.stepLabel}</span>
                  {step.error && <span className="block truncate text-[9px] text-rose-100/45" title={step.error}>{step.error}</span>}
                </span>
                <button
                  type="button"
                  className="inline-flex h-7 shrink-0 items-center justify-center rounded-lg bg-white px-2.5 text-[10px] font-medium text-black transition hover:bg-white/90 disabled:cursor-default disabled:opacity-40"
                  disabled={failureActionBusy || !onRetryWorkflowStep}
                  onClick={() => onRetryWorkflowStep?.(step.stepId)}
                >
                  {retryingWorkflowStepId === step.stepId ? "正在重试…" : "重试整个步骤"}
                </button>
              </div>
            ))}
          </div>
        </div>
      )}
      {hasRetryableItems && (
        <div className="border-t border-rose-300/[0.12] bg-rose-300/[0.025] px-3 py-2.5" data-workflow-item-retry="v1">
          <div className="flex items-center justify-between gap-2 text-[10px]">
            <span className="font-medium text-rose-100/80">失败项可单独重试</span>
            <button
              type="button"
              className="text-white/45 transition hover:text-white/80 disabled:opacity-40"
              disabled={failureActionBusy}
              onClick={() => {
                const all = retryGroups.flatMap((group) => group.items.map((item) => `${group.stepId}:${item.id}`));
                setSelectedRetryItems((current) => current.size === all.length ? new Set() : new Set(all));
              }}
            >
              {selectedRetryItems.size === retryGroups.reduce((total, group) => total + group.items.length, 0) ? "取消全选" : "全选失败项"}
            </button>
          </div>
          <div className="mt-1.5 space-y-1.5">
            {retryGroups.map((group) => (
              <div key={group.stepId}>
                <div className="mb-1 text-[9px] text-white/38">{group.stepLabel}</div>
                {group.items.map((item) => {
                  const key = `${group.stepId}:${item.id}`;
                  return (
                    <label key={key} className="flex cursor-pointer items-start gap-2 rounded-md px-1 py-1 text-[10px] text-white/62 hover:bg-white/[0.045]">
                      <input
                        type="checkbox"
                        className="mt-0.5 size-3 accent-[hsl(var(--primary))]"
                        checked={selectedRetryItems.has(key)}
                        disabled={failureActionBusy}
                        onChange={(event) => {
                          setSelectedRetryItems((current) => {
                            const next = new Set(current);
                            if (event.target.checked) next.add(key); else next.delete(key);
                            return next;
                          });
                        }}
                      />
                      <span className="min-w-0 flex-1">
                        <span className="block truncate" title={item.id}>{item.label}</span>
                        {item.error && <span className="block truncate text-rose-100/45" title={item.error}>{item.error}</span>}
                      </span>
                      {item.attempt ? <span className="shrink-0 text-white/30">第 {item.attempt} 次</span> : null}
                    </label>
                  );
                })}
              </div>
            ))}
          </div>
          <div className="mt-2 grid grid-cols-2 gap-2">
            <button
              type="button"
              className="inline-flex h-7 items-center justify-center gap-1 rounded-lg border border-rose-200/20 bg-rose-300/[0.05] px-2.5 text-[10px] font-medium text-rose-100/78 transition hover:bg-rose-300/[0.1] hover:text-rose-50 disabled:cursor-default disabled:opacity-40"
              disabled={failureActionBusy || selectedRetryItems.size === 0 || !onDismissWorkflowItems}
              onClick={() => setDismissConfirmationOpen(true)}
            >
              <Trash2 className="size-3" aria-hidden />
              {dismissingWorkflowItems ? "正在删除…" : `删除选中项（${selectedRetryItems.size}）`}
            </button>
            <button
              type="button"
              className="inline-flex h-7 items-center justify-center rounded-lg bg-white px-2.5 text-[10px] font-medium text-black transition hover:bg-white/90 disabled:cursor-default disabled:opacity-40"
              disabled={failureActionBusy || selectedRetryItems.size === 0 || !onRetryWorkflowItems}
              onClick={() => onRetryWorkflowItems?.(selectedItemGroups)}
            >
              {retryingWorkflowItems ? "正在重试…" : `重试选中项（${selectedRetryItems.size}）`}
            </button>
          </div>
        </div>
      )}
      <AlertDialog open={dismissConfirmationOpen} onOpenChange={setDismissConfirmationOpen}>
        <AlertDialogContent size="sm">
          <AlertDialogHeader>
            <AlertDialogTitle>删除选中的失败记录？</AlertDialogTitle>
            <AlertDialogDescription>
              这 {selectedRetryItems.size} 条记录会从失败重试列表移除。画布节点、已成功素材和任务审计都会保留。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={dismissingWorkflowItems}>取消</AlertDialogCancel>
            <AlertDialogAction
              disabled={dismissingWorkflowItems || selectedItemGroups.length === 0}
              onClick={() => {
                onDismissWorkflowItems?.(selectedItemGroups);
                setDismissConfirmationOpen(false);
              }}
              className="bg-rose-500 text-white hover:bg-rose-500/90"
            >
              确认删除
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

function timelineStatusIcon(status: AgentExecutionTimelineStatus) {
  if (status === "completed") return <CheckCircle2 className="size-3.5 text-emerald-300/75" aria-hidden />;
  if (status === "failed" || status === "cancelled") return <X className="size-3.5 text-rose-300/80" aria-hidden />;
  if (status === "running") return <Loader2 className="size-3.5 animate-spin text-sky-300/85" aria-hidden />;
  if (status === "paused") return <Circle className="size-3.5 text-amber-200/70" aria-hidden />;
  return <Circle className="size-3.5 text-white/20" aria-hidden />;
}

function ExecutionTimelineStepRow({ step }: { step: AgentExecutionTimeline["steps"][number] }) {
  const duration = formatAgentExecutionDuration(step.durationMs);
  return (
    <li className="relative grid grid-cols-[16px_minmax(0,1fr)_auto] items-start gap-x-2" data-agent-step-status={step.status}>
      <span className="relative z-10 mt-0.5 inline-flex size-4 items-center justify-center rounded-full bg-[#1b1b1d]">
        {timelineStatusIcon(step.status)}
      </span>
      <div className="min-w-0">
        <div className={cn(
          "truncate text-[11px] leading-4",
          step.status === "running" ? "font-medium text-white/82" : "text-white/58",
          step.status === "pending" && "text-white/30",
          step.status === "failed" && "text-rose-100/78",
        )} title={step.label}>
          {step.label}
        </div>
        {step.message && <div className="truncate text-[9px] leading-3.5 text-white/30" title={step.message}>{step.message}</div>}
        {step.details.length > 0 && (
          <div className="mt-1 space-y-0.5 pb-0.5">
            {step.details.slice(-6).map((detail) => {
              const detailDuration = formatAgentExecutionDuration(detail.durationMs);
              return (
                <Fragment key={detail.id}>
                  <div className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-2 text-[9px] leading-3.5 text-white/35">
                  <span className="min-w-0 truncate" title={[detail.technicalName, detail.message].filter(Boolean).join(" · ")}>
                    <span className={cn(
                      "mr-1",
                      detail.status === "completed" && "text-emerald-200/48",
                      detail.status === "running" && "text-sky-200/60",
                      detail.status === "failed" && "text-rose-200/65",
                    )}>
                      {detail.status === "completed" ? "✓" : detail.status === "running" ? "◉" : detail.status === "failed" ? "×" : "○"}
                    </span>
                    {detail.label}
                    {detail.count > 1 && <span className="ml-1 text-white/22">×{detail.count}</span>}
                    {detail.technicalName && <span className="ml-1 font-mono text-white/18">{detail.technicalName}</span>}
                  </span>
                  {detailDuration && <span className="tabular-nums text-white/27">{detailDuration}</span>}
                  </div>
                  {detail.status === "failed" && detail.message && (
                    <div className="truncate pl-4 text-[9px] leading-3.5 text-rose-200/60" title={detail.message}>
                      {detail.message}
                    </div>
                  )}
                </Fragment>
              );
            })}
          </div>
        )}
      </div>
      <span className="pt-0.5 tabular-nums text-[9px] text-white/27">{duration}</span>
    </li>
  );
}

export function humanizeAgentExecutionStage(stage?: string | null): string | null {
  const value = stage?.trim().toLowerCase() || "";
  if (!value) return null;
  if (value.includes("reconnect") || value.includes("recover")) return "恢复连接";
  if (value.includes("verif") || value.includes("receipt")) return "核验结果";
  if (value.includes("canvas") || value.includes("patch") || value.includes("command")) return "写入画布";
  if (value.includes("workflow") || value.includes("step")) return "推进工作流";
  if (value.includes("tool")) return "调用工具";
  if (value.includes("plan") || value.includes("observ")) return "读取并规划";
  if (value.includes("model") || value.includes("assistant") || value.includes("stream")) return "等待模型回传";
  return "执行任务";
}

function formatExecutionDuration(seconds: number): string {
  if (seconds < 60) return `${seconds} 秒`;
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return rest > 0 ? `${minutes} 分 ${rest} 秒` : `${minutes} 分钟`;
}

/**
 * The default Agent state should explain what will happen next without forcing
 * a large director console open over the conversation. Detailed controls still
 * live in FreezoneDirectorConsole and are revealed by this card.
 */
export function FreezoneAgentContextCard({
  state,
  onExpand,
}: {
  state: DirectorConsoleState;
  onExpand: () => void;
}) {
  const focus = state.pinnedNodes[0]?.label
    ?? (state.counts.skills > 0 ? `${state.counts.skills} 个已指定能力` : "技能自动匹配");
  const ready = state.readiness === "ready";
  const running = state.readiness === "running";

  return (
    <button
      type="button"
      onClick={onExpand}
      className="neo-agent-context-card mx-3 mt-2 flex w-[calc(100%-1.5rem)] shrink-0 flex-col gap-2 rounded-xl border px-3 py-2.5 text-left"
      data-agent-context-card={state.readiness}
      aria-label={`打开小树工作台：${readinessLabel(state.readiness)}`}
    >
      <span className="flex min-w-0 items-center gap-2">
        <AgentMark size="sm" className={cn(running && "agent-mark--pulse")} />
        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-1.5">
            <span className="text-[11px] font-semibold text-white/88">小树工作台</span>
            <span className={cn(
              "rounded-full border px-1.5 py-0.5 text-[9px] font-medium",
              ready && "border-emerald-300/25 bg-emerald-400/[0.09] text-emerald-100/90",
              running && "border-amber-300/28 bg-amber-400/[0.1] text-amber-100/90",
              !ready && !running && "border-white/[0.08] bg-white/[0.035] text-white/48",
            )}>
              {readinessLabel(state.readiness)}
            </span>
          </span>
          <span className="mt-0.5 block truncate text-[10px] text-white/42" title={focus}>
            当前参考 · {focus}
          </span>
        </span>
        <span className="shrink-0 text-[10px] text-white/34">详情</span>
      </span>

      <span className="neo-agent-context-card__next grid grid-cols-[auto_minmax(0,1fr)] items-center gap-2 rounded-lg px-2 py-1.5">
        <span className="text-[9px] font-medium uppercase tracking-[0.08em] text-violet-100/55">下一步</span>
        <span className="truncate text-[11px] text-white/73" title={state.nextStep.detail}>{state.nextStep.label}</span>
      </span>

      <span className="flex flex-wrap gap-1.5 text-[9px] text-white/38">
        <span className="rounded-md border border-white/[0.06] bg-black/10 px-1.5 py-0.5">{state.counts.skills} 能力</span>
        {state.counts.pins > 0 && <span className="rounded-md border border-white/[0.06] bg-black/10 px-1.5 py-0.5">{state.counts.pins} 固定节点</span>}
        {state.counts.planSteps > 0 && <span className="rounded-md border border-white/[0.06] bg-black/10 px-1.5 py-0.5">{state.counts.planSteps} 步证据</span>}
      </span>
    </button>
  );
}

export function FreezoneAgentRunBar({
  running,
  connected,
  toolHint,
  stage,
  workflow,
}: {
  running: boolean;
  connected: boolean;
  toolHint?: string | null;
  stage?: string | null;
  workflow?: AgentWorkflowState | null;
}) {
  // LibTV chat-first: no permanent "director manual" strip when idle+connected.
  if (!running && connected) return null;
  const workflowLabel = workflow?.status === "observing"
    ? "观察中"
    : workflow?.status === "planning"
      ? "规划中"
      : workflow?.status === "acting"
        ? "执行中"
        : workflow?.status === "verifying"
          ? "核验中"
          : workflow?.status === "awaiting_confirmation"
            ? "等待任务授权"
            : stage?.startsWith("workflow.")
              ? "工作流推进中"
              : LIBTV_THINKING;
  return (
    <div className="neo-agent-runbar mx-3 mt-2 shrink-0 rounded-xl border border-white/[0.06] bg-black/25 px-3 py-1.5">
      <div className="flex items-center gap-2 text-[12px] text-white/70">
        <AgentMark size="sm" className={cn(running && "animate-pulse")} />
        <span
          className={cn(
            "size-1.5 rounded-full",
            running ? "animate-pulse bg-amber-300" : "bg-muted-foreground",
          )}
          aria-hidden
        />
        <span className="font-medium text-white/90">
          {running ? workflowLabel : "连接中…"}
        </span>
        {toolHint && <span className="min-w-0 truncate text-white/45">· {toolHint}</span>}
      </div>
    </div>
  );
}

function gateToneClass(tone: DirectorGateTone): string {
  switch (tone) {
    case "ready":
      return "border-emerald-300/25 bg-emerald-400/[0.08] text-emerald-50/90";
    case "active":
      return "border-amber-300/30 bg-amber-400/[0.10] text-amber-50/90";
    case "warn":
      return "border-rose-300/25 bg-rose-400/[0.08] text-rose-50/85";
    default:
      return "border-white/[0.08] bg-white/[0.03] text-white/55";
  }
}

function readinessLabel(readiness: DirectorConsoleState["readiness"]): string {
  switch (readiness) {
    case "running":
      return "执行中";
    case "ready":
      return "可下令";
    case "partial":
      return "半就绪";
    default:
      return "可对话";
  }
}

/**
 * V1 permanent director workbench — skills / pins / gates / next step.
 * V2: structure proposal queue (preview → apply → undo).
 */
export function FreezoneDirectorConsole({
  state,
  onRemoveSkill,
  onUnpinNode,
  onToggleSkill,
  collapsed,
  onToggleCollapsed,
  proposals = [],
  lastApply = null,
  onApplyProposal,
  onDismissProposal,
  onUndoLastApply,
}: {
  state: DirectorConsoleState;
  onRemoveSkill: (skillId: string) => void;
  onUnpinNode: (nodeId: string) => void;
  onToggleSkill?: (skillId: string) => void;
  collapsed?: boolean;
  onToggleCollapsed?: () => void;
  proposals?: readonly StructureProposal[];
  lastApply?: StructureApplyRecord | null;
  onApplyProposal?: (commandId: string) => void;
  onDismissProposal?: (commandId: string) => void;
  onUndoLastApply?: () => void;
}) {
  const headProposal = proposals[0] ?? null;
  const visualPinnedNodes = state.pinnedNodes.filter(
    (node) => Boolean(node.previewUrl?.trim()) || node.previewKind === "audio" || node.previewKind === "text",
  );

  return (
    <div
      className="director-console-v1 village-agent-workbench structure-proposal-v2 shrink-0 border-b border-white/[0.055] bg-[#0b0b0d]/86 backdrop-blur-xl"
      data-director-console="village-agent-director"
      data-structure-proposal="v2"
      data-readiness={state.readiness}
      data-pending-proposals={proposals.length}
    >
      <div className={cn("flex items-center gap-2 px-3", collapsed ? "py-1.5" : "py-2")}>
        <div className="relative flex size-6 shrink-0 items-center justify-center rounded-lg border border-white/[0.07] bg-white/[0.035]">
          <LayoutGrid className="size-3.5 text-white/60" aria-hidden />
          {proposals.length > 0 && (
            <span className="absolute -right-1 -top-1 flex min-h-3.5 min-w-3.5 items-center justify-center rounded-full bg-amber-300 px-0.5 text-[8px] font-bold text-black ring-2 ring-[#0b0b0d]">
              {Math.min(9, proposals.length)}
            </span>
          )}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <span className="truncate text-[11px] font-semibold tracking-[-0.01em] text-white/82">
              小树工作台
            </span>
            <span
              className={cn(
                "rounded-full border px-1.5 py-px text-[9px] leading-3.5",
                state.readiness === "ready" && "border-emerald-300/20 bg-emerald-400/[0.07] text-emerald-100/75",
                state.readiness === "running" && "border-amber-300/20 bg-amber-400/[0.07] text-amber-100/75",
                state.readiness === "partial" && "border-sky-300/18 bg-sky-400/[0.06] text-sky-100/70",
                state.readiness === "empty" && "border-white/[0.07] bg-white/[0.025] text-white/35",
              )}
            >
              {readinessLabel(state.readiness)}
            </span>
          </div>
          <div className="mt-px flex min-w-0 items-center gap-1 text-[9px] text-white/30">
            <span>{state.counts.skills} 能力</span>
            <span>·</span>
            <span>{state.counts.pins} 节点</span>
            {state.counts.attachments > 0 && <span>· {state.counts.attachments} 图</span>}
            {headProposal && <span className="min-w-0 truncate text-amber-100/55">· {headProposal.title}</span>}
          </div>
        </div>
        {onToggleCollapsed && (
          <button
            type="button"
            onClick={onToggleCollapsed}
            className="inline-flex h-6 shrink-0 items-center rounded-full border border-white/[0.07] bg-white/[0.025] px-2 text-[9px] font-medium text-white/40 transition-colors hover:bg-white/[0.06] hover:text-white/75"
            title={collapsed ? "展开上下文" : "收起上下文"}
            aria-expanded={!collapsed}
          >
            {collapsed ? "展开" : "收起"}
          </button>
        )}
      </div>

      {!collapsed && (
        <div className="space-y-2.5 px-3 pb-2.5">
          {/* V2 structure proposals */}
          {headProposal && (
            <section
              className={cn(
                "structure-proposal-card rounded-xl border px-2.5 py-2",
                headProposal.risk === "high" && "border-rose-300/35 bg-rose-500/[0.10]",
                headProposal.risk === "medium" && "border-amber-300/35 bg-amber-500/[0.10]",
                headProposal.risk === "low" && "border-sky-300/30 bg-sky-500/[0.09]",
              )}
              data-proposal-id={headProposal.id}
            >
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="text-[10px] font-medium uppercase tracking-[0.06em] text-white/50">
                    结构提案 · 预览应用
                  </div>
                  <div className="mt-0.5 text-[12px] font-semibold leading-4 text-[#f7f7f7]">
                    {headProposal.title}
                  </div>
                  <div className="mt-0.5 text-[10px] text-white/45">
                    {headProposal.totalOps} 动作
                    {headProposal.createCount > 0 ? ` · 将新建 ${headProposal.createCount}` : ""}
                    {headProposal.affectedNodeIds.length > 0
                      ? ` · 影响 ${headProposal.affectedNodeIds.length} 节点`
                      : ""}
                  </div>
                </div>
                <span
                  className={cn(
                    "shrink-0 rounded-full border px-1.5 py-px text-[9px] uppercase tracking-[0.04em]",
                    headProposal.risk === "high" && "border-rose-300/40 text-rose-100",
                    headProposal.risk === "medium" && "border-amber-300/40 text-amber-100",
                    headProposal.risk === "low" && "border-sky-300/35 text-sky-100",
                  )}
                >
                  {headProposal.risk === "high" ? "高风险" : headProposal.risk === "medium" ? "中" : "低"}
                </span>
              </div>
              <ol className="mt-1.5 max-h-20 space-y-0.5 overflow-y-auto">
                {headProposal.summaries.slice(0, 8).map((item, index) => (
                  <li key={`${headProposal.id}-${index}`} className="truncate text-[11px] leading-4 text-white/65">
                    <span className="mr-1 text-white/30">{index + 1}.</span>
                    {item.label}
                  </li>
                ))}
                {headProposal.summaries.length > 8 && (
                  <li className="text-[10px] text-white/35">…另有 {headProposal.summaries.length - 8} 步</li>
                )}
              </ol>
              <div className="mt-2 flex flex-wrap gap-1.5">
                <button
                  type="button"
                  onClick={() => onApplyProposal?.(headProposal.id)}
                  className="inline-flex items-center gap-1 rounded-md border border-emerald-300/40 bg-emerald-400/20 px-2.5 py-1 text-[11px] font-medium text-emerald-50 transition-colors hover:bg-emerald-400/30"
                >
                  <CheckCircle2 className="size-3" />
                  应用结构
                </button>
                <button
                  type="button"
                  onClick={() => onDismissProposal?.(headProposal.id)}
                  className="inline-flex items-center gap-1 rounded-md border border-white/15 bg-white/[0.04] px-2.5 py-1 text-[11px] text-white/70 transition-colors hover:bg-white/[0.08]"
                >
                  <X className="size-3" />
                  丢弃
                </button>
                {proposals.length > 1 && (
                  <span className="self-center text-[10px] text-white/35">
                    队列还有 {proposals.length - 1} 条
                  </span>
                )}
              </div>
            </section>
          )}

          {lastApply && !headProposal && onUndoLastApply && (
            <div className="flex items-center gap-2 rounded-xl border border-white/[0.08] bg-white/[0.03] px-2.5 py-2">
              <div className="min-w-0 flex-1">
                <div className="text-[10px] text-white/40">最近应用</div>
                <div className="truncate text-[11px] text-white/75">{lastApply.title}</div>
              </div>
              <button
                type="button"
                onClick={onUndoLastApply}
                className="inline-flex shrink-0 items-center gap-1 rounded-md border border-white/15 bg-white/[0.05] px-2 py-1 text-[11px] text-white/75 hover:bg-white/[0.09]"
                title="撤销最近一次结构应用"
              >
                <Undo2 className="size-3" />
                撤销
              </button>
            </div>
          )}

          {/* Next step */}
          <div className="rounded-xl border border-violet-300/20 bg-violet-400/[0.07] px-2.5 py-2">
            <div className="text-[10px] font-medium uppercase tracking-[0.06em] text-violet-100/55">
              下一步
            </div>
            <div className="mt-0.5 text-[12px] font-medium leading-4 text-[#f7f7f7]">
              {headProposal ? "应用或丢弃结构提案" : state.nextStep.label}
            </div>
            <div className="mt-0.5 text-[11px] leading-4 text-white/50">
              {headProposal
                ? "应用时会直接写入画布；导航类已可自动聚焦"
                : state.nextStep.detail}
            </div>
          </div>

          {/* Skills — surface copy stays plain; backend skill keys unchanged */}
          <section>
            <div className="mb-1 flex items-center justify-between gap-2">
              <div className="text-[10px] font-medium uppercase tracking-[0.06em] text-white/40">
                手动指定
              </div>
              <div className="text-[10px] text-white/30">
                {state.counts.skills}/{state.counts.skillCatalog}
              </div>
            </div>
            {state.mountedSkills.length === 0 ? (
              <div className="rounded-lg border border-dashed border-white/[0.08] px-2 py-1.5 text-[11px] leading-4 text-white/40">
                默认按当前需求自动匹配；这里仅显示你强制指定的技能
              </div>
            ) : (
              <div className="flex flex-wrap gap-1.5">
                {state.mountedSkills.map((skill) => {
                  const Icon = skillIconFor(skill.id);
                  return (
                    <button
                      key={skill.id}
                      type="button"
                      onClick={() => onRemoveSkill(skill.id)}
                      className="inline-flex max-w-full items-center gap-1 rounded-full border border-fuchsia-300/30 bg-fuchsia-400/[0.12] px-2 py-0.5 text-[11px] text-fuchsia-50"
                      title={`取消指定 ${skill.label}`}
                    >
                      <Icon className="size-3 shrink-0 opacity-90" strokeWidth={2.25} />
                      <span className="truncate">{skill.label}</span>
                      <X className="size-3 shrink-0 opacity-70" />
                    </button>
                  );
                })}
              </div>
            )}
            {onToggleSkill && state.mountedSkills.length < 3 && (
              <div className="mt-1.5 flex flex-wrap gap-1">
                {CANVAS_AGENT_SKILLS.filter(
                  (skill) => !state.mountedSkills.some((mounted) => mounted.id === skill.id),
                )
                  .slice(0, 4)
                  .map((skill) => (
                    <button
                      key={`quick-${skill.id}`}
                      type="button"
                      onClick={() => onToggleSkill(skill.id)}
                      className="inline-flex items-center gap-1 rounded-md border border-white/[0.08] bg-white/[0.03] px-1.5 py-0.5 text-[10px] text-white/50 transition-colors hover:border-white/15 hover:bg-white/[0.06] hover:text-white/75"
                      title={skill.description}
                    >
                      <SkillGlyph skillId={skill.id} coverImage={skill.coverImage} className="size-4 rounded-md" iconClassName="size-2.5" />
                      {skill.label}
                    </button>
                  ))}
              </div>
            )}
          </section>

          {/* Pins */}
          <section>
            <div className="mb-1 flex items-center justify-between gap-2">
              <div className="text-[10px] font-medium uppercase tracking-[0.06em] text-white/40">
                钉选节点
              </div>
              <div className="text-[10px] text-white/30">{state.counts.pins}</div>
            </div>
            {state.pinnedNodes.length === 0 ? (
              <div className="rounded-lg border border-dashed border-white/[0.08] px-2 py-1.5 text-[11px] leading-4 text-white/40">
                画布右键「{LIBTV_ADD_TO_AGENT}」固定节点
              </div>
            ) : visualPinnedNodes.length > 0 ? (
              <div className="flex flex-wrap gap-1.5">
                {visualPinnedNodes.map((node) => (
                  <button
                    key={node.id}
                    type="button"
                    onClick={() => onUnpinNode(node.id)}
                    className="relative inline-flex size-10 items-center justify-center overflow-hidden rounded-[10px] border border-emerald-300/30 bg-emerald-400/[0.12] text-emerald-50"
                    title="取消钉选节点"
                    aria-label={`取消钉选节点：${node.label || node.id}`}
                  >
                    <CanvasNodePreviewThumb node={node} className="size-full rounded-[9px] border-0" />
                    <X className="pointer-events-none absolute right-0.5 top-0.5 size-2.5 text-white/65" aria-hidden />
                  </button>
                ))}
              </div>
            ) : null}
          </section>

          {/* The safety/side-effect contract remains available, but stays out of
              the main path until the user explicitly asks for advanced detail. */}
          <details className="village-agent-advanced rounded-xl border border-white/[0.07] bg-black/10">
            <summary className="flex cursor-pointer list-none items-center gap-1.5 px-2.5 py-2 text-[10px] font-medium text-white/48 [&::-webkit-details-marker]:hidden">
              <ShieldAlert className="size-3 opacity-70" aria-hidden />
              执行规则与写入
              <span className="ml-auto text-[9px] text-white/25">展开</span>
            </summary>
            <div className="grid grid-cols-2 gap-1.5 border-t border-white/[0.05] px-2.5 py-2">
              {state.gates.map((gate) => (
                <div
                  key={gate.id}
                  className={cn("rounded-lg border px-2 py-1.5", gateToneClass(gate.tone))}
                  title={gate.detail}
                >
                  <div className="flex items-center justify-between gap-1">
                    <span className="truncate text-[11px] font-medium">{gate.short}</span>
                    <span className="shrink-0 text-[9px] uppercase tracking-[0.04em] opacity-70">
                      {gate.id === "paid_media"
                        ? (gate.tone === "ready" ? "已授权" : "已锁定")
                        : gate.tone === "warn"
                          ? "直写"
                          : gate.tone === "active"
                            ? "进行中"
                            : gate.tone === "ready"
                              ? "就绪"
                              : "待命"}
                    </span>
                  </div>
                  <div className="mt-0.5 line-clamp-2 text-[10px] leading-[14px] opacity-80">
                    {gate.detail}
                  </div>
                </div>
              ))}
            </div>
          </details>
        </div>
      )}
    </div>
  );
}

export function FreezoneSlashSkillMenu({
  query,
  onPick,
  onClose,
  skills = CANVAS_AGENT_SKILLS,
}: {
  query: string;
  onPick: (skillId: string) => void;
  onClose: () => void;
  skills?: readonly CanvasAgentSkill[];
}) {
  const q = query.trim().toLowerCase();
  const items = skills.filter((skill) => {
    if (!q) return true;
    return (
      skill.label.toLowerCase().includes(q)
      || skill.skillKey.toLowerCase().includes(q)
      || skillDisplayTag(skill).toLowerCase().includes(q)
    );
  }).slice(0, 8);
  if (items.length === 0) return null;
  return (
    <div className="absolute bottom-[calc(100%+6px)] left-2 right-2 z-50 overflow-hidden rounded-xl border border-white/10 bg-zinc-950/95 shadow-2xl backdrop-blur-xl">
      <div className="flex items-center gap-1.5 border-b border-white/[0.06] px-3 py-1.5 text-[10px] uppercase tracking-[0.08em] text-white/40">
        <Bot className="size-3 text-violet-200/80" />
        / 选择能力
      </div>
      <div className="max-h-56 overflow-y-auto py-1">
        {items.map((skill) => (
          <button
            key={skill.id}
            type="button"
            className="flex w-full items-center gap-2.5 px-3 py-2 text-left hover:bg-white/[0.06]"
            onClick={() => {
              onPick(skill.id);
              onClose();
            }}
          >
            <SkillGlyph skillId={skill.id} coverImage={skill.coverImage} className="size-7 rounded-lg" />
            <span className="min-w-0 flex-1">
              <span className="block truncate text-[13px] text-white/90">{skill.label}</span>
              <span className="block truncate text-[11px] text-white/40">{skill.description}</span>
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}
