// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Clapperboard, FileVideo, Loader2, Upload, X } from "lucide-react";

import {
  submitFreezoneExtract,
  submitFreezoneAnalyze,
  uploadFreezoneImage,
} from "@/api/ops";
import { awaitTaskCompletion, type TaskState } from "@/api/tasks";
import { UiButton, UiInput, UiPanel } from "@/components/ui";
import {
  UI_CONTENT_OVERLAY_INSET_CLASS,
  UI_DIALOG_TRANSITION_MS,
} from "@/components/ui/motion";
import { useDialogTransition } from "@/components/ui/useDialogTransition";
import {
  resolveDirectCanvasModelId,
  useDirectModelCatalog,
} from "@/features/canvas/hooks/useDirectModelCatalog";
import { resolveExtractableVideoUrl } from "@/features/canvas/application/extractedFrames";

interface ExtractFramesDialogProps {
  project: string;
  onClose: () => void;
  onDone: (msg: string) => void;
  /**
   * Drop the extracted frames onto the canvas. Receives the frame URL list +
   * optional analyses; the parent integrates them via `addNode`.
   */
  onFramesReady: (frames: ExtractedFrame[]) => void;
  /**
   * 已有视频（画布视频节点上的 `videoUrl`）：直接抽这个，跳过本地选文件与上传。
   * 从视频节点进来时走这条路 —— 用户手上就是那段视频，让他再上传一遍没道理。
   *
   * 只接受后端能解析的项目内地址；跨源地址给进来时会自动退回「选文件」模式
   * （见 `resolveExtractableVideoUrl`）。
   */
  sourceVideoUrl?: string | null;
}

export interface ExtractedFrame {
  url: string;
  index: number;
  analysis?: ShotAnalysis | null;
}

export interface ShotAnalysis {
  shot_type?: string;
  angle?: string;
  camera_movement?: string;
  subject_action?: string;
  mood?: string;
  color_tone?: string;
  suggested_prompt?: string;
}

type Stage = "idle" | "uploading" | "extracting" | "analyzing" | "done" | "error";

/**
 * 这两个参数对创作者本来没有直觉：`scene_threshold` 是 ffmpeg 的场景检测阈值，
 * `max_frames` 是抽帧上限。所以先按「你手上是什么片子」「你想要多少帧」给档位，
 * 精确数值收在下面可改 —— 把工程旋钮翻译成创作选择，而不是让人猜 0.3 是什么。
 * 档位取值的依据写在原来那句提示里（长镜头 0.2-0.3、快剪 0.5+），这里只是把它变成控件。
 */
const SCENE_THRESHOLD_PRESETS = [
  // 中间档刻意取 0.3 —— 它既是本对话框原来的默认值，也落在原提示
  // 「长镜头视频选 0.2-0.3」的区间上，这样打开面板就有一个档位是高亮的。
  { label: "长镜头", value: 0.2, hint: "画面变化慢" },
  { label: "一般", value: 0.3, hint: "常规素材" },
  { label: "快剪", value: 0.5, hint: "大量切点" },
] as const;

const FRAME_COUNT_PRESETS = [
  { label: "快速看", value: 8, hint: "先扫一遍" },
  { label: "标准", value: 20, hint: "够用" },
  { label: "精细", value: 40, hint: "一镜不落" },
] as const;

interface ProgressState {
  stage: Stage;
  message: string;
  progress: number; // 0..1
}

export function ExtractFramesDialog({
  project,
  onClose,
  onDone,
  onFramesReady,
  sourceVideoUrl = null,
}: ExtractFramesDialogProps) {
  const [file, setFile] = useState<File | null>(null);
  const sourceUrl = resolveExtractableVideoUrl(sourceVideoUrl);
  const [maxFrames, setMaxFrames] = useState(20);
  const [sceneThreshold, setSceneThreshold] = useState(0.3);
  const [analyzeShots, setAnalyzeShots] = useState(true);
  const [progress, setProgress] = useState<ProgressState>({
    stage: "idle",
    message: "",
    progress: 0,
  });
  const [error, setError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const { models: visionModels } = useDirectModelCatalog("vision");
  const resolvedVisionModel = resolveDirectCanvasModelId(undefined, visionModels);
  const { shouldRender, isVisible } = useDialogTransition(true, UI_DIALOG_TRANSITION_MS);

  const submitting =
    progress.stage !== "idle" && progress.stage !== "error" && progress.stage !== "done";

  const requestClose = () => {
    if (submitting) return;
    onClose();
  };

  const handleSubmit = async () => {
    if (!sourceUrl && !file) {
      setError("请先选择视频文件");
      return;
    }
    if (analyzeShots && !resolvedVisionModel) {
      setError("当前没有可用的视觉模型，请关闭镜头分析或先配置视觉模型。");
      return;
    }
    setError(null);
    try {
      // 有源视频就跳过上传：视频节点上的 videoUrl 已经是后端 /static/... 地址，
      // 再走一遍 uploadFreezoneImage 只会白传一遍同一段视频。
      let videoUrl: string;
      if (sourceUrl) {
        videoUrl = sourceUrl;
        setProgress({
          stage: "extracting",
          message: "ffmpeg 抽帧（最多 60 秒）...",
          progress: 0.3,
        });
      } else {
        setProgress({ stage: "uploading", message: "上传视频...", progress: 0.1 });
        const upload = await uploadFreezoneImage(project, file as File, (file as File).name);
        videoUrl = upload.url;
      }

      setProgress({
        stage: "extracting",
        message: "ffmpeg 抽帧（最多 60 秒）...",
        progress: 0.3,
      });
      const extractRef = await submitFreezoneExtract(project, {
        videoUrl,
        maxFrames,
        sceneThreshold,
      });
      const extractTask = await awaitTaskCompletion(extractRef.task_key, project);
      const frameUrls = extractFrameUrls(extractTask);
      if (frameUrls.length === 0) {
        throw new Error("抽帧返回了空结果，可能视频太短或格式不支持");
      }

      let analyses: ShotAnalysis[] = [];
      if (analyzeShots) {
        setProgress({
          stage: "analyzing",
          message: `Vision 分析 ${frameUrls.length} 帧...`,
          progress: 0.7,
        });
        try {
          if (!resolvedVisionModel) {
            throw new Error("模型中心没有可用的视觉模型");
          }
          const analyzeRef = await submitFreezoneAnalyze(project, {
            frameUrls,
            provider: "direct",
            model: resolvedVisionModel,
          });
          const analyzeTask = await awaitTaskCompletion(analyzeRef.task_key, project);
          analyses = extractAnalyses(analyzeTask);
        } catch (err) {
          console.warn("[freezone] shot analysis failed (continuing without):", err);
        }
      }

      const frames: ExtractedFrame[] = frameUrls.map((url, i) => ({
        url,
        index: i,
        analysis: analyses[i] ?? null,
      }));
      onFramesReady(frames);
      setProgress({
        stage: "done",
        message: `已抽 ${frames.length} 帧${analyses.length > 0 ? "，并完成镜头分析" : ""}`,
        progress: 1,
      });
      onDone(`拉片完成：${frames.length} 帧已加入画布`);
      setTimeout(onClose, 600);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setError(msg);
      setProgress({ stage: "error", message: "失败", progress: 0 });
    }
  };

  if (!shouldRender || typeof document === "undefined") {
    return null;
  }

  return createPortal(
    <div className={`fixed ${UI_CONTENT_OVERLAY_INSET_CLASS} z-50 flex items-center justify-center`}>
      <div
        className={`absolute inset-0 bg-black/55 backdrop-blur-[2px] transition-opacity duration-200 ${
          isVisible ? "opacity-100" : "opacity-0"
        }`}
        onClick={requestClose}
      />
      <UiPanel
        className={`relative w-[580px] max-w-[calc(100vw-2rem)] overflow-hidden transition-[opacity,transform] duration-200 ${
          isVisible ? "opacity-100 translate-y-0" : "opacity-0 translate-y-1"
        }`}
      >
        <header className="flex items-start gap-3 border-b border-[color:var(--ui-border-soft)] px-5 py-4">
          <div className="tap-accent-soft flex h-9 w-9 shrink-0 items-center justify-center rounded-lg">
            <Clapperboard className="h-[18px] w-[18px]" />
          </div>
          <div className="min-w-0 flex-1">
            <h2 className="text-[15px] font-semibold leading-tight text-text-dark">拉片分析</h2>
            <p className="mt-1 text-xs leading-relaxed text-text-muted">
              把一条参考片拆成关键帧图片，散到画布上；打开镜头语言分析后，每一帧还会告诉你它是什么景别、怎么运镜。
            </p>
          </div>
          <button
            type="button"
            onClick={requestClose}
            disabled={submitting}
            className="text-text-muted hover:text-text-dark transition disabled:opacity-30"
            aria-label="关闭"
          >
            <X className="h-4 w-4" />
          </button>
        </header>

        <div className="px-5 py-4 space-y-5">
          <Section title="视频文件">
            {sourceUrl ? (
              <SourceVideoRow url={sourceUrl} />
            ) : (
              <FilePicker
                file={file}
                disabled={submitting}
                inputRef={fileInputRef}
                onChange={(f) => setFile(f)}
              />
            )}
          </Section>

          <Section title="这条片子怎么抽">
            <PresetRow
              options={SCENE_THRESHOLD_PRESETS}
              value={sceneThreshold}
              disabled={submitting}
              formatValue={(v) => String(v)}
              onChange={(v) => setSceneThreshold(v)}
            />
            <p className="mt-2 text-[11px] leading-relaxed text-text-muted/80">
              画面变化慢的片子选「长镜头」，快剪 MV 选「快剪」。
              数值是场景阈值：越大越只挑明显的切点。
            </p>
          </Section>

          <Section title="要抽多少帧">
            <PresetRow
              options={FRAME_COUNT_PRESETS}
              value={maxFrames}
              disabled={submitting}
              formatValue={(v) => `${v} 帧`}
              onChange={(v) => setMaxFrames(v)}
            />
            <p className="mt-2 text-[11px] leading-relaxed text-text-muted/80">
              帧越多越完整，抽帧和后续分析也越慢。
            </p>
          </Section>

          <Section title="想自己填精确值">
            <div className="grid grid-cols-2 gap-3">
              <Field label="场景阈值" hint="0.1 - 0.9">
                <UiInput
                  type="number"
                  min={0.1}
                  max={0.9}
                  step={0.05}
                  value={sceneThreshold}
                  onChange={(e) => setSceneThreshold(Number(e.target.value))}
                  disabled={submitting}
                />
              </Field>
              <Field label="最大帧数" hint="3 - 50">
                <UiInput
                  type="number"
                  min={3}
                  max={50}
                  value={maxFrames}
                  onChange={(e) => setMaxFrames(Number(e.target.value))}
                  disabled={submitting}
                />
              </Field>
            </div>
          </Section>

          <Section title="读懂镜头语言">
            <label
              className={`flex w-full items-start gap-3 rounded-lg border border-[color:var(--ui-border-soft)] bg-[var(--ui-surface-field)] px-3 py-2.5 text-left transition-colors hover:border-[color:var(--ui-border-strong)] ${
                submitting ? "cursor-not-allowed opacity-60" : "cursor-pointer"
              }`}
            >
              <input
                type="checkbox"
                checked={analyzeShots}
                onChange={(e) => setAnalyzeShots(e.target.checked)}
                disabled={submitting}
                className="sr-only peer"
              />
              <span className="tap-check__box mt-0.5 inline-flex h-4 w-4 shrink-0 items-center justify-center rounded">
                <svg viewBox="0 0 16 16" className="h-3 w-3" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M3.5 8.5l3 3 6-7" />
                </svg>
              </span>
              <div className="min-w-0 flex-1">
                <div className="text-sm text-text-dark">分析每一帧的景别、角度和运镜</div>
                <div className="mt-0.5 text-[11px] text-text-muted">
                  景别 / 角度 / 运镜 / 氛围 / 色调 · 结果写进每张帧节点的信息里，点开帧就能看
                </div>
              </div>
            </label>
          </Section>

          {progress.stage !== "idle" && (
            <ProgressBar progress={progress} />
          )}

          {error && (
            <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs leading-relaxed text-red-300 break-words">
              {error}
            </div>
          )}
        </div>

        <footer className="flex items-center justify-between gap-3 border-t border-[color:var(--ui-border-soft)] px-5 py-3.5">
          <p className="min-w-0 flex-1 text-[11px] leading-relaxed text-text-muted">
            完成后，帧会作为一组图片节点摆在画布右侧，并连回这条视频。
          </p>
          <div className="flex shrink-0 items-center gap-2">
            <UiButton variant="ghost" size="sm" onClick={requestClose} disabled={submitting}>
              取消
            </UiButton>
          <UiButton
            variant="primary"
            size="sm"
            onClick={handleSubmit}
            disabled={(!sourceUrl && !file) || submitting || (analyzeShots && !resolvedVisionModel)}
          >
            {submitting ? (
              <>
                <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                处理中
              </>
            ) : (
              "开始拉片"
            )}
          </UiButton>
          </div>
        </footer>
      </UiPanel>
    </div>,
    document.body
  );
}

interface PresetOption {
  label: string;
  value: number;
  hint: string;
}

/**
 * 档位选择：主标签说人话，副行给「它意味着什么 + 对应数值」。
 * 数值仍照旧送给后端；用户改了下方的精确输入框时，这里自然没有档位是高亮的。
 */
function PresetRow({
  options,
  value,
  disabled,
  formatValue,
  onChange,
}: {
  options: readonly PresetOption[];
  value: number;
  disabled: boolean;
  formatValue: (value: number) => string;
  onChange: (value: number) => void;
}) {
  return (
    <div className="grid grid-cols-3 gap-1.5">
      {options.map((option) => {
        const active = option.value === value;
        return (
          <button
            key={option.value}
            type="button"
            aria-pressed={active}
            disabled={disabled}
            onClick={() => onChange(option.value)}
            className="tap-option flex flex-col items-start gap-0.5 rounded-lg px-3 py-2 text-left"
          >
            <span className="tap-option__label text-[12px] leading-tight text-text-dark">
              {option.label}
            </span>
            <span className="text-[10px] leading-tight text-text-muted">
              {option.hint} · {formatValue(option.value)}
            </span>
          </button>
        );
      })}
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="mb-2 text-[10px] font-semibold uppercase tracking-[0.08em] text-text-muted">
        {title}
      </div>
      {children}
    </div>
  );
}

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block">
      <div className="mb-1 flex items-baseline justify-between">
        <span className="text-xs text-text-muted">{label}</span>
        {hint && <span className="text-[10px] text-text-muted/70">{hint}</span>}
      </div>
      {children}
    </label>
  );
}

interface FilePickerProps {
  file: File | null;
  disabled: boolean;
  inputRef: React.RefObject<HTMLInputElement | null>;
  onChange: (file: File | null) => void;
}

/** 从视频节点进来时用：源视频已经确定，只报一下用的是哪段。 */
function SourceVideoRow({ url }: { url: string }) {
  const fileName = (() => {
    try {
      const path = new URL(url, window.location.origin).pathname;
      const last = path.split('/').filter(Boolean).pop();
      return last ? decodeURIComponent(last) : url;
    } catch {
      return url;
    }
  })();
  return (
    <div className="flex items-center gap-3 rounded-lg border border-[color:var(--ui-border-soft)] bg-[var(--ui-surface-field)] px-3 py-3">
      <div className="tap-accent-soft flex h-9 w-9 shrink-0 items-center justify-center rounded-md">
        <FileVideo className="h-4 w-4" />
      </div>
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm text-text-dark">{fileName}</div>
        <div className="mt-0.5 text-[11px] text-text-muted">
          使用视频节点上的源视频（无需重新上传）
        </div>
      </div>
    </div>
  );
}

function FilePicker({ file, disabled, inputRef, onChange }: FilePickerProps) {
  return (
    <div
      className={`flex items-center gap-3 rounded-lg border border-dashed px-3 py-3 transition-colors ${
        file
          ? "border-[color:var(--ui-border-soft)] bg-[var(--ui-surface-field)]"
          : "border-[color:var(--ui-border-soft)] bg-[var(--ui-surface-field)]/50 hover:border-[color:var(--ui-border-strong)]"
      }`}
    >
      <div className="tap-accent-soft flex h-9 w-9 shrink-0 items-center justify-center rounded-md">
        {file ? <FileVideo className="h-4 w-4" /> : <Upload className="h-4 w-4" />}
      </div>
      <div className="min-w-0 flex-1">
        {file ? (
          <>
            <div className="truncate text-sm text-text-dark">{file.name}</div>
            <div className="mt-0.5 text-[11px] text-text-muted">
              {(file.size / 1024 / 1024).toFixed(1)} MB
            </div>
          </>
        ) : (
          <>
            <div className="text-sm text-text-dark">选择视频文件</div>
            <div className="mt-0.5 text-[11px] text-text-muted">支持 mp4 / mov / webm 等格式</div>
          </>
        )}
      </div>
      <UiButton
        variant="muted"
        size="sm"
        disabled={disabled}
        onClick={() => inputRef.current?.click()}
      >
        {file ? "更换" : "浏览"}
      </UiButton>
      <input
        ref={inputRef}
        type="file"
        accept="video/*"
        className="hidden"
        disabled={disabled}
        onChange={(e) => onChange(e.target.files?.[0] ?? null)}
      />
    </div>
  );
}

function ProgressBar({ progress }: { progress: ProgressState }) {
  const pct = Math.round(progress.progress * 100);
  const isDone = progress.stage === "done";
  return (
    <div className="rounded-lg border border-[color:var(--ui-border-soft)] bg-[var(--ui-surface-field)] px-3 py-2.5">
      <div className="mb-1.5 flex items-center justify-between text-xs">
        <span className="flex items-center gap-1.5 text-text-dark">
          {!isDone && <Loader2 className="tap-accent-text h-3 w-3 animate-spin" />}
          {progress.message}
        </span>
        <span className="text-[11px] tabular-nums text-text-muted">
          {isDone ? "完成" : `${pct}%`}
        </span>
      </div>
      <div className="h-1 overflow-hidden rounded-full bg-[rgba(255,255,255,0.06)]">
        <div
          className="tap-accent-solid h-full transition-all duration-300"
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

function extractFrameUrls(task: TaskState): string[] {
  const result = task.result;
  if (!result) return [];
  const urls = result["frame_urls"];
  return Array.isArray(urls) ? (urls as string[]).filter((u) => typeof u === "string") : [];
}

function extractAnalyses(task: TaskState): ShotAnalysis[] {
  const result = task.result;
  if (!result) return [];
  const analyses = result["analyses"];
  return Array.isArray(analyses) ? (analyses as ShotAnalysis[]) : [];
}
