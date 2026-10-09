// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
//
// 成片质检台 — the human door onto the backend's T1–T8 verification stack.
//
// Before this panel existed, `/src/novelvideo/verification/` (8.4k lines, 13
// live endpoints) had zero callers in the product: the only 质检 surface was a
// prompt-only Agent skill that asked the model to eyeball what these endpoints
// already measure numerically. The panel deliberately labels cost per run —
// pixel-level checks are free, LLM reviews are not — so the two never look
// interchangeable.
import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { useQueryClient } from "@tanstack/react-query";
import { AlertCircle, CheckCircle2, Loader2, Play, ShieldCheck, Sparkles, X } from "lucide-react";

import { queryKeys } from "@/lib/query-keys";
import { cn } from "@/lib/utils";
import {
  useScoreBeat,
  useSketchSelect,
  useVerifyBeat,
  useVerifyConsistency,
  useVerifyContinuity,
  useVerifyEpisodeOverview,
  useVerifyFrame,
  useVerifySimilarity,
  useVerifySketchColors,
  type BeatScoreResult,
  type BeatVerifyResult,
  type ColorVerifyResult,
  type ConsistencyResult,
  type ContinuityResult,
  type EpisodeOverviewResult,
  type SimilarityResult,
  type SketchSelectResult,
} from "@/lib/queries/verification";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

type CapabilityId =
  | "similarity"
  | "sketchColors"
  | "continuity"
  | "consistency"
  | "overview"
  | "sketchSelect"
  | "beatVerify"
  | "beatScore"
  | "beatFrame";

type QcRun =
  | { id: "similarity"; result: SimilarityResult }
  | { id: "sketchColors"; result: ColorVerifyResult }
  | { id: "continuity"; result: ContinuityResult }
  | { id: "consistency"; result: ConsistencyResult }
  | { id: "overview"; result: EpisodeOverviewResult }
  | { id: "sketchSelect"; result: SketchSelectResult }
  | { id: "beatVerify"; result: BeatVerifyResult }
  | { id: "beatScore"; result: BeatScoreResult }
  | { id: "beatFrame"; result: BeatVerifyResult };

interface VerifyPanelProps {
  project: string;
  episode: number;
  /** Beat currently selected in the workbench; beat-scoped checks need it. */
  selectedBeat: number | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

function scoreTone(value: number, pass: number): string {
  if (value >= pass) return "text-emerald-300";
  if (value >= pass - 1.5) return "text-amber-300";
  return "text-red-300";
}

function ResultShell({
  passed,
  children,
}: {
  passed: boolean | null;
  children: ReactNode;
}) {
  return (
    <div
      className={cn(
        "mt-2 rounded-lg border px-3 py-2 text-[11px] leading-5",
        passed === null
          ? "border-white/[0.08] bg-white/[0.02]"
          : passed
            ? "border-emerald-300/20 bg-emerald-300/[0.045]"
            : "border-red-300/20 bg-red-300/[0.045]",
      )}
    >
      {children}
    </div>
  );
}

function BeatChips({ beats, tone }: { beats: number[]; tone: "warn" | "bad" }) {
  if (beats.length === 0) return null;
  return (
    <div className="mt-1 flex flex-wrap gap-1">
      {beats.map((n) => (
        <span
          key={n}
          className={cn(
            "rounded-full border px-1.5 py-0.5 font-mono text-[10px] tabular-nums leading-none",
            tone === "bad"
              ? "border-red-300/25 bg-red-300/[0.07] text-red-100/75"
              : "border-amber-300/25 bg-amber-300/[0.07] text-amber-100/75",
          )}
        >
          {n}
        </span>
      ))}
    </div>
  );
}

function RunResult({ run }: { run: QcRun }) {
  const { t } = useTranslation();

  if (run.id === "similarity") {
    const r = run.result;
    return (
      <ResultShell passed={r.overall_passed}>
        {r.pairs.length === 0 ? (
          <p className="text-white/55">{t("episode.workbench.qc.similarityClean")}</p>
        ) : (
          <ul className="space-y-0.5">
            {r.pairs.slice(0, 12).map((p) => (
              <li key={`${p.beat_a}-${p.beat_b}`} className="flex items-center gap-2">
                <span className="font-mono tabular-nums text-white/70">
                  {p.beat_a} ↔ {p.beat_b}
                </span>
                <span className={p.warning ? "text-red-300" : "text-white/45"}>
                  {(p.similarity * 100).toFixed(1)}%
                </span>
              </li>
            ))}
          </ul>
        )}
        <BeatChips beats={r.duplicate_beats} tone="bad" />
      </ResultShell>
    );
  }

  if (run.id === "sketchColors") {
    const r = run.result;
    return (
      <ResultShell passed={r.overall_passed}>
        <div className="flex flex-wrap gap-x-3 gap-y-1 text-white/60">
          <span>{t("episode.workbench.qc.colorCounts", {
            total: r.total_beats,
            passed: r.passed_beats,
            failed: r.failed_beats,
            warned: r.warned_beats,
          })}</span>
        </div>
        <BeatChips beats={r.failed_beat_numbers} tone="bad" />
      </ResultShell>
    );
  }

  if (run.id === "continuity") {
    const r = run.result;
    return (
      <ResultShell passed={r.weak_transitions.length === 0}>
        <p className="text-white/60">
          {t("episode.workbench.qc.continuityScore", {
            score: r.overall_score.toFixed(1),
            count: r.transitions.length,
          })}
        </p>
        <BeatChips beats={r.weak_transitions} tone="warn" />
        <div className="mt-1.5 space-y-0.5">
          {r.transitions
            .filter((tr) => r.weak_transitions.includes(tr.to_beat))
            .slice(0, 6)
            .map((tr) => (
              <p key={`${tr.from_beat}-${tr.to_beat}`} className="text-white/45">
                <span className="font-mono tabular-nums text-white/65">
                  {tr.from_beat}→{tr.to_beat}
                </span>{" "}
                {tr.total.toFixed(1)}
                {tr.issues.length > 0 && ` · ${tr.issues.join("；")}`}
              </p>
            ))}
        </div>
      </ResultShell>
    );
  }

  if (run.id === "consistency") {
    const r = run.result;
    return (
      <ResultShell passed={r.overall_passed}>
        {r.summary && <p className="text-white/60">{r.summary}</p>}
        <div className="mt-1 space-y-0.5">
          {r.characters.slice(0, 10).map((c) => (
            <div key={c.identity_id || c.character} className="flex items-center gap-2">
              <span className="min-w-0 truncate text-white/70">{c.character}</span>
              <span className={c.passed ? "text-emerald-300" : "text-amber-300"}>
                {c.passed
                  ? t("episode.workbench.verify.charPassed")
                  : t("episode.workbench.verify.charFailed")}
              </span>
              <span className="text-white/45">
                {t("episode.workbench.qc.consistencyScores", {
                  face: c.face_score.toFixed(1),
                  clothing: c.clothing_score.toFixed(1),
                })}
              </span>
            </div>
          ))}
        </div>
      </ResultShell>
    );
  }

  if (run.id === "overview") {
    const r = run.result;
    const dims: Array<[string, number]> = [
      [t("episode.workbench.qc.dimRhythm"), r.visual_rhythm],
      [t("episode.workbench.qc.dimComposition"), r.composition_diversity],
      [t("episode.workbench.qc.dimArc"), r.narrative_arc],
      [t("episode.workbench.qc.dimStyle"), r.style_unity],
    ];
    return (
      <ResultShell passed={r.overall_passed}>
        <div className="flex flex-wrap gap-x-3 gap-y-1">
          {dims.map(([label, value]) => (
            <span key={label} className="text-white/55">
              {label}{" "}
              <span className={cn("font-medium tabular-nums", scoreTone(value, 7))}>
                {value.toFixed(1)}
              </span>
            </span>
          ))}
          <span className="text-white/70">
            {t("episode.workbench.qc.overviewTotal")}{" "}
            <span className={cn("font-medium tabular-nums", scoreTone(r.total, 6))}>
              {r.total.toFixed(1)}
            </span>
          </span>
        </div>
        {r.summary && <p className="mt-1 text-white/60">{r.summary}</p>}
        {r.issues.length > 0 && (
          <ul className="mt-1.5 space-y-0.5">
            {r.issues.slice(0, 5).map((issue, index) => (
              <li key={`${issue.beat_number}-${index}`} className="text-white/55">
                <span
                  className={issue.severity === "critical" ? "text-red-300" : "text-amber-300"}
                >
                  {issue.severity === "critical"
                    ? t("episode.workbench.qc.severityCritical")
                    : t("episode.workbench.qc.severityWarning")}
                </span>{" "}
                <span className="font-mono tabular-nums text-white/70">
                  #{issue.beat_number}
                </span>{" "}
                {issue.description}
              </li>
            ))}
          </ul>
        )}
      </ResultShell>
    );
  }

  if (run.id === "sketchSelect") {
    const r = run.result;
    return (
      <ResultShell passed={r.needs_regeneration.length === 0 && r.no_candidates.length === 0}>
        <p className="text-white/65">{r.summary}</p>
        <p className="mt-0.5 text-white/50">
          {t("episode.workbench.qc.selectCounts", {
            selected: r.selected_count,
            total: r.total_beats,
            accepted: r.accepted_beats.length,
            provisional: r.provisional_beats.length,
          })}
          {typeof r.promoted_count === "number" && r.promoted_count > 0
            ? ` · ${t("episode.workbench.qc.selectPromoted", { count: r.promoted_count })}`
            : ""}
        </p>
        {r.needs_regeneration.length > 0 && (
          <div className="mt-1">
            <span className="text-white/45">{t("episode.workbench.qc.needsRegen")}</span>
            <BeatChips beats={r.needs_regeneration} tone="bad" />
          </div>
        )}
        {r.no_candidates.length > 0 && (
          <div className="mt-1">
            <span className="text-white/45">{t("episode.workbench.qc.noCandidates")}</span>
            <BeatChips beats={r.no_candidates} tone="warn" />
          </div>
        )}
        {r.all_disqualified.length > 0 && (
          <div className="mt-1">
            <span className="text-white/45">{t("episode.workbench.qc.allDisqualified")}</span>
            <BeatChips beats={r.all_disqualified} tone="warn" />
          </div>
        )}
      </ResultShell>
    );
  }

  // beatVerify / beatFrame share BeatVerifyResult; beatScore is the numeric one.
  if (run.id === "beatScore") {
    const r = run.result;
    return (
      <ResultShell passed={r.total >= 7}>
        <div className="flex flex-wrap gap-x-3 gap-y-1">
          <span className="text-white/55">
            {t("episode.workbench.qc.scriptMatch")}{" "}
            <span className={cn("font-medium tabular-nums", scoreTone(r.script_match, 7))}>
              {r.script_match.toFixed(1)}
            </span>
          </span>
          <span className="text-white/55">
            {t("episode.workbench.qc.identityClarity")}{" "}
            <span className={cn("font-medium tabular-nums", scoreTone(r.identity_clarity, 7))}>
              {r.identity_clarity.toFixed(1)}
            </span>
          </span>
          <span className="text-white/70">
            {t("episode.workbench.qc.beatTotal")}{" "}
            <span className={cn("font-medium tabular-nums", scoreTone(r.total, 7))}>
              {r.total.toFixed(1)}
            </span>
          </span>
        </div>
      </ResultShell>
    );
  }

  const r = run.result;
  return (
    <ResultShell passed={r.passed}>
      <p className="text-white/65">
        {t("episode.workbench.qc.beatScore", { score: r.score.toFixed(1) })}
        {r.suggested_action !== "none" && ` · ${r.suggested_action}`}
      </p>
      {r.summary && <p className="mt-0.5 text-white/55">{r.summary}</p>}
      {r.issues.length > 0 && (
        <ul className="mt-1 space-y-0.5">
          {r.issues.slice(0, 5).map((issue, index) => (
            <li key={index} className="text-white/50">
              <span
                className={issue.severity === "critical" ? "text-red-300" : "text-amber-300"}
              >
                {issue.severity}
              </span>{" "}
              {issue.description}
            </li>
          ))}
        </ul>
      )}
    </ResultShell>
  );
}

export function VerifyPanel({
  project,
  episode,
  selectedBeat,
  open,
  onOpenChange,
}: VerifyPanelProps) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [runs, setRuns] = useState<Partial<Record<CapabilityId, QcRun>>>({});
  const [activeId, setActiveId] = useState<CapabilityId | null>(null);
  const [error, setError] = useState<{ id: CapabilityId; message: string } | null>(null);
  const [promoteSelected, setPromoteSelected] = useState(false);

  const similarity = useVerifySimilarity(project, episode);
  const sketchColors = useVerifySketchColors(project, episode);
  const continuity = useVerifyContinuity(project, episode);
  const consistency = useVerifyConsistency(project, episode, "sketch");
  const overview = useVerifyEpisodeOverview(project, episode);
  const sketchSelect = useSketchSelect(project, episode);
  const beatNum = selectedBeat ?? 0;
  const beatVerify = useVerifyBeat(project, episode, beatNum);
  const beatScore = useScoreBeat(project, episode, beatNum);
  const beatFrame = useVerifyFrame(project, episode, beatNum);

  const run = async (id: CapabilityId) => {
    setActiveId(id);
    setError(null);
    try {
      let next: QcRun;
      switch (id) {
        case "similarity":
          next = { id, result: await similarity.mutateAsync() };
          break;
        case "sketchColors":
          next = { id, result: await sketchColors.mutateAsync() };
          break;
        case "continuity":
          next = { id, result: await continuity.mutateAsync(undefined) };
          break;
        case "consistency":
          next = { id, result: await consistency.mutateAsync() };
          break;
        case "overview":
          next = { id, result: await overview.mutateAsync() };
          break;
        case "sketchSelect":
          next = { id, result: await sketchSelect.mutateAsync({ promoteSelected }) };
          if (promoteSelected) {
            void qc.invalidateQueries({ queryKey: queryKeys.grids(project, episode) });
            void qc.invalidateQueries({ queryKey: queryKeys.beats(project, episode) });
          }
          break;
        case "beatVerify":
          next = { id, result: await beatVerify.mutateAsync(undefined) };
          break;
        case "beatScore":
          next = { id, result: await beatScore.mutateAsync(undefined) };
          break;
        case "beatFrame":
          next = { id, result: await beatFrame.mutateAsync() };
          break;
      }
      setRuns((current) => ({ ...current, [id]: next }));
    } catch (err) {
      setError({ id, message: err instanceof Error ? err.message : String(err) });
    } finally {
      setActiveId(null);
    }
  };

  type Row = {
    id: CapabilityId;
    label: string;
    hint: string;
    /** `free` = pixel-level, zero model calls. `llm` = burns real model calls. */
    cost: "free" | "llm";
    needsBeat?: boolean;
  };

  const groups: Array<{ title: string; rows: Row[] }> = [
    {
      title: t("episode.workbench.qc.groupFree"),
      rows: [
        {
          id: "similarity",
          label: t("episode.workbench.qc.similarityLabel"),
          hint: t("episode.workbench.qc.similarityHint"),
          cost: "free",
        },
        {
          id: "sketchColors",
          label: t("episode.workbench.qc.sketchColorsLabel"),
          hint: t("episode.workbench.qc.sketchColorsHint"),
          cost: "free",
        },
      ],
    },
    {
      title: t("episode.workbench.qc.groupLlm"),
      rows: [
        {
          id: "sketchSelect",
          label: t("episode.workbench.qc.sketchSelectLabel"),
          hint: t("episode.workbench.qc.sketchSelectHint"),
          cost: "llm",
        },
        {
          id: "overview",
          label: t("episode.workbench.qc.overviewLabel"),
          hint: t("episode.workbench.qc.overviewHint"),
          cost: "llm",
        },
        {
          id: "continuity",
          label: t("episode.workbench.qc.continuityLabel"),
          hint: t("episode.workbench.qc.continuityHint"),
          cost: "llm",
        },
        {
          id: "consistency",
          label: t("episode.workbench.qc.consistencyLabel"),
          hint: t("episode.workbench.qc.consistencyHint"),
          cost: "llm",
        },
      ],
    },
    {
      title: t("episode.workbench.qc.groupBeat", {
        n: selectedBeat ?? "—",
      }),
      rows: [
        {
          id: "beatVerify",
          label: t("episode.workbench.qc.beatVerifyLabel"),
          hint: t("episode.workbench.qc.beatVerifyHint"),
          cost: "llm",
          needsBeat: true,
        },
        {
          id: "beatScore",
          label: t("episode.workbench.qc.beatScoreLabel"),
          hint: t("episode.workbench.qc.beatScoreHint"),
          cost: "llm",
          needsBeat: true,
        },
        {
          id: "beatFrame",
          label: t("episode.workbench.qc.beatFrameLabel"),
          hint: t("episode.workbench.qc.beatFrameHint"),
          cost: "llm",
          needsBeat: true,
        },
      ],
    },
  ];

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        data-verify-panel="true"
        className="!flex !flex-col w-[calc(100vw-2rem)] max-h-[min(820px,88vh)] !max-w-[calc(100vw-2rem)] overflow-hidden border-white/[0.09] bg-[#111114] p-0 text-white shadow-2xl min-[900px]:!max-w-[900px]"
      >
        <div className="border-b border-white/[0.08] px-6 pb-4 pt-5">
          <DialogHeader className="space-y-0">
            <DialogTitle className="flex items-center gap-2 text-[18px] font-semibold tracking-[-0.02em]">
              <ShieldCheck className="size-4 text-emerald-300" />
              {t("episode.workbench.qc.title")}
            </DialogTitle>
            <DialogDescription className="mt-1 text-[12px] text-white/45">
              {t("episode.workbench.qc.subtitle")}
            </DialogDescription>
          </DialogHeader>
          <div className="mt-3 flex flex-wrap items-center gap-2 text-[11px]">
            <Badge variant="outline" className="border-emerald-300/20 bg-emerald-300/[0.05] text-emerald-100/70">
              {t("episode.workbench.qc.costFree")}
            </Badge>
            <Badge variant="outline" className="border-amber-300/20 bg-amber-300/[0.05] text-amber-100/70">
              {t("episode.workbench.qc.costLlm")}
            </Badge>
            <span className="text-white/35">{t("episode.workbench.qc.costLegend")}</span>
          </div>
        </div>

        <div className="min-h-0 flex-1 space-y-5 overflow-y-auto px-6 py-5">
          {groups.map((group) => (
            <section key={group.title}>
              <h4 className="mb-2 text-[11px] font-medium uppercase tracking-wide text-white/35">
                {group.title}
              </h4>
              <div className="space-y-2">
                {group.rows.map((row) => {
                  const running = activeId === row.id;
                  const blocked = Boolean(row.needsBeat) && !selectedBeat;
                  const result = runs[row.id];
                  const rowError = error?.id === row.id ? error.message : null;
                  return (
                    <article
                      key={row.id}
                      className="rounded-xl border border-white/[0.075] bg-white/[0.025] px-3.5 py-3"
                    >
                      <div className="flex items-start gap-3">
                        <div className="min-w-0 flex-1">
                          <div className="flex flex-wrap items-center gap-2">
                            <span className="text-[12px] font-medium text-white/85">
                              {row.label}
                            </span>
                            <span
                              className={cn(
                                "rounded-full border px-1.5 py-0.5 text-[10px] leading-none",
                                row.cost === "free"
                                  ? "border-emerald-300/20 bg-emerald-300/[0.05] text-emerald-100/65"
                                  : "border-amber-300/20 bg-amber-300/[0.05] text-amber-100/65",
                              )}
                            >
                              {row.cost === "free"
                                ? t("episode.workbench.qc.tagFree")
                                : t("episode.workbench.qc.tagLlm")}
                            </span>
                          </div>
                          <p className="mt-0.5 text-[11px] leading-5 text-white/45">
                            {blocked
                              ? t("episode.workbench.qc.needsBeat")
                              : row.hint}
                          </p>
                        </div>
                        <Button
                          size="sm"
                          variant={row.cost === "free" ? "outline" : "default"}
                          disabled={running || blocked || activeId !== null}
                          onClick={() => void run(row.id)}
                          className="shrink-0 gap-1"
                        >
                          {running ? (
                            <Loader2 className="size-3.5 animate-spin" />
                          ) : row.cost === "free" ? (
                            <Play className="size-3.5" />
                          ) : (
                            <Sparkles className="size-3.5" />
                          )}
                          {running
                            ? t("episode.workbench.qc.running")
                            : t("episode.workbench.qc.run")}
                        </Button>
                      </div>

                      {row.id === "sketchSelect" && (
                        <label className="mt-2 flex items-center gap-1.5 text-[11px] text-white/50">
                          <input
                            type="checkbox"
                            checked={promoteSelected}
                            onChange={(event) => setPromoteSelected(event.target.checked)}
                            className="size-3.5 accent-emerald-300"
                          />
                          {t("episode.workbench.qc.promoteSelected")}
                        </label>
                      )}

                      {rowError && (
                        <ResultShell passed={false}>
                          <span className="flex items-start gap-1.5 text-red-100/75">
                            <AlertCircle className="mt-0.5 size-3 shrink-0" />
                            {rowError}
                          </span>
                        </ResultShell>
                      )}

                      {result && <RunResult run={result} />}
                    </article>
                  );
                })}
              </div>
            </section>
          ))}

          <p className="flex items-start gap-1.5 text-[11px] leading-5 text-white/32">
            <CheckCircle2 className="mt-0.5 size-3 shrink-0" />
            {t("episode.workbench.qc.reportHint")}
          </p>
        </div>

        <div className="flex items-center justify-end gap-2 border-t border-white/[0.08] px-6 py-3">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              setRuns({});
              setError(null);
            }}
            disabled={Object.keys(runs).length === 0 && !error}
            className="gap-1"
          >
            <X className="size-3.5" />
            {t("episode.workbench.qc.clear")}
          </Button>
          <Button variant="outline" size="sm" onClick={() => onOpenChange(false)}>
            {t("common.close")}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
