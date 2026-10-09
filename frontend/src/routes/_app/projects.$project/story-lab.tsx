// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  BookOpenText,
  CheckCircle2,
  Download,
  FileCheck2,
  Loader2,
  RefreshCw,
  Save,
  Send,
  Sparkles,
  TriangleAlert,
} from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Progress } from "@/components/ui/progress";
import { Textarea } from "@/components/ui/textarea";
import { ProjectStoryNavigation } from "@/components/layout/project-story-navigation";
import {
  useExportStoryLab,
  useGenerateStoryLabStage,
  usePublishStoryLab,
  useSaveStoryLabConfig,
  useSaveStoryLabResult,
  useStoryLab,
  useStoryLabResult,
} from "@/lib/queries/story-lab";
import { useProject } from "@/lib/queries/projects";
import { useTasks } from "@/lib/queries/tasks";
import { useStyleDetail, useStyles } from "@/lib/queries/styles";
import { queryKeys } from "@/lib/query-keys";
import { downloadUrlAsFile } from "@/lib/browserDownload";
import { cn } from "@/lib/utils";
import {
  EMPTY_STORY_LAB_CONFIG,
  STORY_LAB_WORK_TYPES,
  type StoryLabArtifact,
  type StoryLabConfig,
  type StoryLabGenerationStage,
} from "@/types/story-lab";
import type { Style } from "@/types/style";

type StoryLabTab = "creative" | StoryLabGenerationStage | "units";

const TABS: StoryLabTab[] = [
  "creative",
  "bible",
  "outline",
  "units",
  "draft",
  "audit",
];

const ACTIVE_TASK_STATUSES = new Set([
  "submitting",
  "queued",
  "pending",
  "starting",
  "running",
]);

function generationStageForTab(tab: StoryLabTab): StoryLabGenerationStage | null {
  if (tab === "creative") return null;
  return tab === "units" ? "outline" : tab;
}

function formatArtifact(artifact: StoryLabArtifact | null | undefined): string {
  return artifact?.result ? JSON.stringify(artifact.result, null, 2) : "";
}

function toErrorMessage(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}

function compileStylePrompt(style: Style): string {
  return [
    style.style_instructions,
    style.style_tag,
    style.family,
    style.era,
    style.palette,
    style.lighting,
    style.optics,
    style.composition,
    style.camera_motion,
    style.image_prompt,
    style.video_prompt,
    style.negative_prompt ? `Avoid: ${style.negative_prompt}` : "",
  ]
    .filter((value): value is string => Boolean(value?.trim()))
    .join("\n");
}

function StoryLabPage() {
  const { project } = Route.useParams();
  const { t } = useTranslation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const storyLab = useStoryLab(project);
  const projectQuery = useProject(project);
  const projectStyleId = projectQuery.data?.data?.visual_style?.trim() ?? "";
  const projectStyleQuery = useStyleDetail(project, projectStyleId || null);
  const projectStyle = projectStyleQuery.data?.data;
  const tasks = useTasks({ project });
  const styles = useStyles(project);
  const saveConfig = useSaveStoryLabConfig(project);
  const generateStage = useGenerateStoryLabStage(project);
  const saveResult = useSaveStoryLabResult(project);
  const exportDraft = useExportStoryLab(project);
  const publishDraft = usePublishStoryLab(project);
  const [activeTab, setActiveTab] = useState<StoryLabTab>("creative");
  const [config, setConfig] = useState<StoryLabConfig>(EMPTY_STORY_LAB_CONFIG);
  const [configDirty, setConfigDirty] = useState(false);
  const [resultText, setResultText] = useState("");
  const [resultDirty, setResultDirty] = useState(false);
  const [instructions, setInstructions] = useState("");

  const generationStage = generationStageForTab(activeTab);
  const state = storyLab.data?.data;
  const styleDetail = useStyleDetail(
    project,
    config.style_mode === "confirmed" && config.style_id ? config.style_id : null,
  );
  const resultQuery = useStoryLabResult(
    project,
    generationStage ?? "bible",
    generationStage !== null && Boolean(state?.stages[generationStage]),
  );
  const artifact = generationStage
    ? (resultQuery.data?.data ?? state?.stages[generationStage] ?? null)
    : null;

  const currentTask = useMemo(() => {
    if (!generationStage) return undefined;
    const matching = (tasks.data?.data ?? []).filter(
      (task) =>
        task.task_type === `story_lab_${generationStage}` ||
        task.scope === generationStage,
    );
    return matching.find((task) => ACTIVE_TASK_STATUSES.has(task.status)) ?? matching[0];
  }, [generationStage, tasks.data?.data]);
  const taskIsActive = currentTask
    ? ACTIVE_TASK_STATUSES.has(currentTask.status)
    : false;
  const progress = Math.max(
    0,
    Math.min(100, Math.round((currentTask?.progress ?? 0) * 100)),
  );
  const missingDependencies = useMemo(() => {
    if (!generationStage || generationStage === "bible") return [];
    const required: StoryLabGenerationStage[] =
      generationStage === "outline"
        ? ["bible"]
        : generationStage === "draft"
          ? ["bible", "outline"]
          : ["bible", "outline", "draft"];
    return required.filter((stage) => !state?.stages[stage]);
  }, [generationStage, state?.stages]);

  useEffect(() => {
    if (!state?.config || configDirty) return;
    setConfig({ ...EMPTY_STORY_LAB_CONFIG, ...state.config });
  }, [configDirty, state?.config]);

  useEffect(() => {
    const detail = styleDetail.data?.data;
    if (!detail || config.style_mode !== "confirmed" || config.style_prompt.trim()) return;
    const prompt = compileStylePrompt(detail);
    if (!prompt) return;
    setConfig((current) =>
      current.style_id === detail.id ? { ...current, style_prompt: prompt } : current,
    );
    setConfigDirty(true);
  }, [config.style_mode, config.style_prompt, styleDetail.data?.data]);

  useEffect(() => {
    setResultDirty(false);
    setInstructions("");
  }, [generationStage]);

  useEffect(() => {
    if (!generationStage || resultDirty) return;
    setResultText(formatArtifact(artifact));
  }, [artifact, generationStage, resultDirty]);

  useEffect(() => {
    if (!currentTask || !generationStage) return;
    if (currentTask.status !== "completed" && currentTask.status !== "failed") return;
    void queryClient.invalidateQueries({ queryKey: queryKeys.storyLab(project) });
    void queryClient.invalidateQueries({
      queryKey: queryKeys.storyLabResult(project, generationStage),
    });
  }, [currentTask?.status, generationStage, project, queryClient]);

  const updateConfig = <K extends keyof StoryLabConfig>(
    key: K,
    value: StoryLabConfig[K],
  ) => {
    setConfig((current) => ({ ...current, [key]: value }));
    setConfigDirty(true);
  };

  const applyProjectStyle = () => {
    if (!projectStyleId) return;
    const stylePrompt = [
      projectStyle?.style_instructions,
      projectStyle?.style_tag,
      projectStyle?.base,
      projectStyle?.era,
      projectStyle?.palette,
      projectStyle?.lighting,
      projectStyle?.optics,
      projectStyle?.composition,
      projectStyle?.image_prompt,
      projectStyle?.video_prompt,
    ]
      .map((value) => value?.trim())
      .filter(Boolean)
      .join("\n")
      .slice(0, 8000);
    setConfig((current) => ({
      ...current,
      style_mode: "confirmed",
      style_id: projectStyleId,
      style_name:
        projectStyle?.label?.trim() ||
        projectStyle?.name?.trim() ||
        projectStyleId,
      style_prompt: stylePrompt || current.style_prompt,
    }));
    setConfigDirty(true);
  };

  const selectStyle = (styleId: string) => {
    const selected = styles.data?.data.find((style) => style.id === styleId);
    setConfig((current) => ({
      ...current,
      style_id: styleId,
      style_name: selected?.label || selected?.name || "",
      style_prompt: selected ? compileStylePrompt(selected) : "",
    }));
    setConfigDirty(true);
  };

  const handleSaveConfig = async () => {
    if (!config.title.trim() || !config.logline.trim()) {
      toast.error(t("storyLab.messages.titleAndLoglineRequired"));
      return false;
    }
    try {
      await saveConfig.mutateAsync({
        ...config,
        title: config.title.trim(),
        logline: config.logline.trim(),
        target_units: Math.max(1, Number(config.target_units) || 1),
        target_length: Math.max(100, Number(config.target_length) || 100),
        target_duration_seconds:
          config.target_duration_seconds == null
            ? null
            : Math.max(10, Number(config.target_duration_seconds) || 10),
      });
      setConfigDirty(false);
      toast.success(t("storyLab.messages.configSaved"));
      return true;
    } catch (error) {
      toast.error(toErrorMessage(error, t("storyLab.messages.saveFailed")));
      return false;
    }
  };

  const handleGenerate = async () => {
    if (!generationStage || taskIsActive) return;
    if (configDirty || !state?.config) {
      const saved = await handleSaveConfig();
      if (!saved) return;
    }
    try {
      await generateStage.mutateAsync({
        stage: generationStage,
        instructions,
      });
      toast.success(t("storyLab.messages.taskStarted"));
    } catch (error) {
      toast.error(toErrorMessage(error, t("storyLab.messages.generateFailed")));
    }
  };

  const handleSaveResult = async () => {
    if (!generationStage) return;
    let result: Record<string, unknown>;
    try {
      const parsed: unknown = JSON.parse(resultText);
      if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") {
        throw new Error(t("storyLab.messages.resultMustBeObject"));
      }
      result = parsed as Record<string, unknown>;
    } catch (error) {
      toast.error(toErrorMessage(error, t("storyLab.messages.invalidJson")));
      return;
    }
    try {
      await saveResult.mutateAsync({ stage: generationStage, result });
      setResultDirty(false);
      toast.success(t("storyLab.messages.resultSaved"));
    } catch (error) {
      toast.error(toErrorMessage(error, t("storyLab.messages.saveFailed")));
    }
  };

  const handleExport = async () => {
    try {
      const response = await exportDraft.mutateAsync(undefined);
      const downloadUrl = response.data.download_url ?? response.data.url;
      if (downloadUrl) {
        await downloadUrlAsFile(downloadUrl, response.data.filename);
      }
      toast.success(t("storyLab.messages.exported", { filename: response.data.filename }));
    } catch (error) {
      toast.error(toErrorMessage(error, t("storyLab.messages.exportFailed")));
    }
  };

  const handlePublish = async () => {
    try {
      await publishDraft.mutateAsync({ rebuild: true });
      toast.success(t("storyLab.messages.published"));
      await navigate({
        to: "/projects/$project/ingest",
        params: { project },
        search: { view: "library" },
      });
    } catch (error) {
      toast.error(toErrorMessage(error, t("storyLab.messages.publishFailed")));
    }
  };

  if (storyLab.isLoading) {
    return (
      <div className="flex h-full items-center justify-center text-muted-foreground">
        <Loader2 className="mr-2 size-5 animate-spin" />
        {t("storyLab.loading")}
      </div>
    );
  }

  if (storyLab.isError) {
    return (
      <div className="flex h-full items-center justify-center px-6">
        <div className="max-w-md rounded-xl border border-destructive/30 bg-destructive/5 p-6 text-center">
          <TriangleAlert className="mx-auto size-8 text-destructive" />
          <p className="mt-3 text-sm text-destructive">
            {toErrorMessage(storyLab.error, t("storyLab.messages.loadFailed"))}
          </p>
          <Button className="mt-4" variant="outline" onClick={() => storyLab.refetch()}>
            <RefreshCw className="size-4" />
            {t("common.retry")}
          </Button>
        </div>
      </div>
    );
  }

  return (
    <main className="h-full min-h-0 overflow-y-auto bg-background px-5 py-5 lg:px-8">
      <div className="mx-auto flex w-full max-w-[1500px] flex-col gap-4">
        <ProjectStoryNavigation project={project} active="create" clientNavigation />
        <header className="flex flex-col gap-3 rounded-xl border border-white/[0.08] bg-white/[0.035] p-5 md:flex-row md:items-center md:justify-between">
          <div>
            <div className="flex items-center gap-2">
              <BookOpenText className="size-5 text-primary" />
              <h1 className="text-xl font-semibold tracking-tight">{t("storyLab.title")}</h1>
            </div>
            <p className="mt-1.5 text-sm text-muted-foreground">{t("storyLab.subtitle")}</p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button
              variant="outline"
              onClick={handleExport}
              disabled={!state?.stages.draft || exportDraft.isPending}
            >
              {exportDraft.isPending ? <Loader2 className="size-4 animate-spin" /> : <Download className="size-4" />}
              {t("storyLab.actions.export")}
            </Button>
            <Button
              onClick={handlePublish}
              disabled={!state?.stages.draft || publishDraft.isPending}
            >
              {publishDraft.isPending ? <Loader2 className="size-4 animate-spin" /> : <Send className="size-4" />}
              {t("storyLab.actions.publish")}
            </Button>
          </div>
        </header>

        <nav
          aria-label={t("storyLab.stageNavigation")}
          className="flex min-h-11 items-center gap-1 overflow-x-auto rounded-xl border border-white/[0.08] bg-white/[0.025] p-1.5"
        >
          {TABS.map((tab) => {
            const stage = generationStageForTab(tab);
            const complete = stage ? Boolean(state?.stages[stage]) : Boolean(state?.config);
            return (
              <button
                key={tab}
                type="button"
                onClick={() => setActiveTab(tab)}
                className={cn(
                  "inline-flex h-8 shrink-0 items-center gap-1.5 rounded-lg px-3 text-xs font-medium transition-colors",
                  activeTab === tab
                    ? "bg-foreground text-background"
                    : "text-muted-foreground hover:bg-white/[0.05] hover:text-foreground",
                )}
              >
                {complete ? <CheckCircle2 className="size-3.5" /> : null}
                {t(`storyLab.tabs.${tab}`)}
              </button>
            );
          })}
          {state?.config && state.updated_at ? (
            <span className="ml-auto hidden shrink-0 px-2 text-[11px] text-muted-foreground lg:inline">
              {t("storyLab.lastSaved", { time: new Date(state.updated_at).toLocaleString() })}
            </span>
          ) : null}
        </nav>

        {activeTab === "creative" ? (
          <section className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_340px]">
            <div className="rounded-xl border border-white/[0.08] bg-white/[0.025] p-5">
              <div className="grid gap-4 md:grid-cols-2">
                <label className="space-y-1.5 text-xs text-muted-foreground">
                  {t("storyLab.fields.title")}
                  <Input value={config.title} onChange={(event) => updateConfig("title", event.target.value)} />
                </label>
                <label className="space-y-1.5 text-xs text-muted-foreground">
                  {t("storyLab.fields.workType")}
                  <select
                    className="h-8 w-full rounded-lg border border-input bg-background px-2.5 text-sm"
                    value={config.work_type}
                    onChange={(event) => updateConfig("work_type", event.target.value as StoryLabConfig["work_type"])}
                  >
                    {STORY_LAB_WORK_TYPES.map((type) => (
                      <option key={type} value={type}>{t(`storyLab.workTypes.${type}`)}</option>
                    ))}
                  </select>
                </label>
                <label className="space-y-1.5 text-xs text-muted-foreground md:col-span-2">
                  {t("storyLab.fields.logline")}
                  <Textarea rows={4} value={config.logline} onChange={(event) => updateConfig("logline", event.target.value)} />
                </label>
                <label className="space-y-1.5 text-xs text-muted-foreground">
                  {t("storyLab.fields.genre")}
                  <Input value={config.genre} onChange={(event) => updateConfig("genre", event.target.value)} />
                </label>
                <label className="space-y-1.5 text-xs text-muted-foreground">
                  {t("storyLab.fields.theme")}
                  <Input value={config.theme} onChange={(event) => updateConfig("theme", event.target.value)} />
                </label>
                <label className="space-y-1.5 text-xs text-muted-foreground">
                  {t("storyLab.fields.targetUnits")}
                  <Input type="number" min={1} value={config.target_units} onChange={(event) => updateConfig("target_units", Number(event.target.value))} />
                </label>
                <label className="space-y-1.5 text-xs text-muted-foreground">
                  {t("storyLab.fields.targetLength")}
                  <Input type="number" min={100} step={100} value={config.target_length} onChange={(event) => updateConfig("target_length", Number(event.target.value))} />
                </label>
                {config.work_type !== "long_novel" && config.work_type !== "short_novel" && (
                  <label className="space-y-1.5 text-xs text-muted-foreground">
                    {t("storyLab.fields.targetDuration")}
                    <Input
                      type="number"
                      min={10}
                      step={5}
                      value={config.target_duration_seconds ?? ""}
                      onChange={(event) =>
                        updateConfig(
                          "target_duration_seconds",
                          event.target.value ? Number(event.target.value) : null,
                        )
                      }
                    />
                  </label>
                )}
                <label className="space-y-1.5 text-xs text-muted-foreground">
                  {t("storyLab.fields.pov")}
                  <Input value={config.point_of_view} onChange={(event) => updateConfig("point_of_view", event.target.value)} />
                </label>
                <label className="space-y-1.5 text-xs text-muted-foreground">
                  {t("storyLab.fields.audience")}
                  <Input value={config.audience} onChange={(event) => updateConfig("audience", event.target.value)} />
                </label>
              </div>
              <div className="mt-5 flex justify-end">
                <Button onClick={handleSaveConfig} disabled={!configDirty || saveConfig.isPending}>
                  {saveConfig.isPending ? <Loader2 className="size-4 animate-spin" /> : <Save className="size-4" />}
                  {t("storyLab.actions.saveCreative")}
                </Button>
              </div>
            </div>

            <aside className="rounded-xl border border-white/[0.08] bg-white/[0.025] p-5">
              <h2 className="text-sm font-semibold">{t("storyLab.style.title")}</h2>
              <p className="mt-1 text-xs leading-5 text-muted-foreground">{t("storyLab.style.description")}</p>
              <div className="mt-4 grid gap-2">
                {(["infer", "confirmed"] as const).map((mode) => (
                  <button
                    key={mode}
                    type="button"
                    onClick={() => {
                      if (mode === "confirmed" && projectStyleId) {
                        applyProjectStyle();
                        return;
                      }
                      updateConfig("style_mode", mode);
                    }}
                    className={cn(
                      "rounded-lg border p-3 text-left text-xs transition-colors",
                      config.style_mode === mode
                        ? "border-primary/40 bg-primary/10 text-foreground"
                        : "border-white/[0.08] text-muted-foreground hover:bg-white/[0.03]",
                    )}
                  >
                    <span className="font-medium">{t(`storyLab.style.${mode}`)}</span>
                    <span className="mt-1 block leading-5">{t(`storyLab.style.${mode}Hint`)}</span>
                  </button>
                ))}
              </div>
              {config.style_mode === "confirmed" ? (
                <div className="mt-4 space-y-3">
                  {projectStyleId ? (
                    <Button
                      className="w-full justify-start"
                      type="button"
                      variant="outline"
                      onClick={applyProjectStyle}
                      disabled={projectStyleQuery.isLoading}
                    >
                      {projectStyleQuery.isLoading ? (
                        <Loader2 className="size-4 animate-spin" />
                      ) : (
                        <Sparkles className="size-4" />
                      )}
                      {t("storyLab.style.chooseStyle")}: {projectStyle?.label || projectStyle?.name || projectStyleId}
                    </Button>
                  ) : (
                    <p className="rounded-lg border border-dashed border-white/[0.1] p-3 text-xs leading-5 text-muted-foreground">
                      {t("storyLab.style.noProjectStyle")}
                    </p>
                  )}
                  <label className="space-y-1.5 text-xs text-muted-foreground">
                    {t("storyLab.fields.stylePreset")}
                    <select
                      className="h-8 w-full rounded-lg border border-input bg-background px-2.5 text-sm"
                      value={config.style_id}
                      onChange={(event) => selectStyle(event.target.value)}
                    >
                      <option value="">{t("storyLab.style.selectPreset")}</option>
                      {(styles.data?.data ?? []).map((style) => (
                        <option key={style.id} value={style.id}>{style.label || style.name}</option>
                      ))}
                    </select>
                  </label>
                  <label className="space-y-1.5 text-xs text-muted-foreground">
                    {t("storyLab.fields.styleName")}
                    <Input value={config.style_name} onChange={(event) => updateConfig("style_name", event.target.value)} />
                  </label>
                  <label className="space-y-1.5 text-xs text-muted-foreground">
                    {t("storyLab.fields.stylePrompt")}
                    <Textarea rows={5} value={config.style_prompt} onChange={(event) => updateConfig("style_prompt", event.target.value)} />
                  </label>
                </div>
              ) : null}
            </aside>
          </section>
        ) : (
          <section className="grid min-h-[560px] gap-4 xl:grid-cols-[minmax(0,1fr)_340px]">
            <div className="flex min-h-0 flex-col rounded-xl border border-white/[0.08] bg-white/[0.025] p-5">
              <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
                <div>
                  <h2 className="text-base font-semibold">{t(`storyLab.stageTitles.${activeTab}`)}</h2>
                  <p className="mt-1 text-xs text-muted-foreground">{t(`storyLab.stageDescriptions.${activeTab}`)}</p>
                </div>
                {artifact ? (
                  <div className="flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground">
                    {artifact.model ? <Badge variant="outline">{artifact.model}</Badge> : null}
                    {artifact.prompt_version ? <Badge variant="outline">Prompt {artifact.prompt_version}</Badge> : null}
                    {artifact.updated_at ? <span>{new Date(artifact.updated_at).toLocaleString()}</span> : null}
                  </div>
                ) : null}
              </div>
              {resultQuery.isLoading ? (
                <div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">
                  <Loader2 className="mr-2 size-4 animate-spin" />{t("common.loading")}
                </div>
              ) : (
                <Textarea
                  aria-label={t("storyLab.resultEditor")}
                  className="min-h-[430px] flex-1 resize-y font-mono text-xs leading-5"
                  placeholder={t("storyLab.emptyResult")}
                  value={resultText}
                  onChange={(event) => {
                    setResultText(event.target.value);
                    setResultDirty(true);
                  }}
                />
              )}
              <div className="mt-3 flex justify-end">
                <Button variant="outline" onClick={handleSaveResult} disabled={!resultDirty || saveResult.isPending}>
                  {saveResult.isPending ? <Loader2 className="size-4 animate-spin" /> : <Save className="size-4" />}
                  {t("storyLab.actions.saveResult")}
                </Button>
              </div>
            </div>

            <aside className="space-y-4">
              <div className="rounded-xl border border-white/[0.08] bg-white/[0.025] p-5">
                <div className="flex items-center gap-2">
                  <Sparkles className="size-4 text-primary" />
                  <h2 className="text-sm font-semibold">{t("storyLab.generation.title")}</h2>
                </div>
                <p className="mt-1 text-xs leading-5 text-muted-foreground">{t("storyLab.generation.description")}</p>
                <Textarea
                  className="mt-4 min-h-28 text-xs"
                  placeholder={t("storyLab.generation.instructionsPlaceholder")}
                  value={instructions}
                  onChange={(event) => setInstructions(event.target.value)}
                />
                <Button
                  className="mt-3 w-full"
                  onClick={handleGenerate}
                  disabled={taskIsActive || generateStage.isPending || missingDependencies.length > 0}
                >
                  {taskIsActive || generateStage.isPending ? <Loader2 className="size-4 animate-spin" /> : <Sparkles className="size-4" />}
                  {artifact ? t("storyLab.actions.regenerate") : t("storyLab.actions.generate")}
                </Button>
                {missingDependencies.length ? (
                  <p className="mt-2 text-xs leading-5 text-amber-300/80">
                    {t("storyLab.generation.missingDependencies", {
                      stages: missingDependencies
                        .map((stage) => t(`storyLab.tabs.${stage}`))
                        .join("、"),
                    })}
                  </p>
                ) : null}
              </div>

              {currentTask ? (
                <div className="rounded-xl border border-white/[0.08] bg-white/[0.025] p-5" aria-live="polite">
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-sm font-medium">{t("storyLab.task.title")}</span>
                    <Badge variant={currentTask.status === "failed" ? "destructive" : "outline"}>
                      {t(`storyLab.task.status.${currentTask.status}`)}
                    </Badge>
                  </div>
                  <p className="mt-2 text-xs text-muted-foreground">{currentTask.current_task || t("storyLab.task.waiting")}</p>
                  <div className="mt-3 flex items-center gap-3">
                    <Progress className="flex-1" value={progress} />
                    <span className="w-9 text-right font-mono text-xs text-muted-foreground">{progress}%</span>
                  </div>
                  {currentTask.error ? <p className="mt-3 text-xs leading-5 text-destructive">{currentTask.error}</p> : null}
                </div>
              ) : (
                <div className="rounded-xl border border-dashed border-white/[0.1] p-5 text-xs leading-5 text-muted-foreground">
                  <FileCheck2 className="mb-2 size-5" />
                  {t("storyLab.task.empty")}
                </div>
              )}
            </aside>
          </section>
        )}
      </div>
    </main>
  );
}

export const Route = createFileRoute("/_app/projects/$project/story-lab")({
  component: StoryLabPage,
});
