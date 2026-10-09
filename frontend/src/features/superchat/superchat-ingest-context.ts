import { toast } from "sonner";
import type { TFunction } from "i18next";
import { api } from "@/lib/api";
import { backendErrorToastMessage, jsonWithBackendError } from "@/lib/api-errors";
import { p } from "@/lib/api-path";
import { dataUrlToAttachmentBlob, dataUrlToText, type AttachmentBlob } from "@/features/superchat/superchat-media";
import type { ChatAttachment } from "@/features/superchat/types";
import type { FormatCheck, UploadResult } from "@/lib/queries/ingest";
import type { ErrorResponse, OkResponse, TaskResponse } from "@/types/api";

export type IngestUploadResult = UploadResult;
export type PreparedIngestAttachment = {
  attachment: ChatAttachment;
  original: ChatAttachment;
  upload?: IngestUploadResult;
  error?: string;
};
export type UploadedIngestFile = {
  filename: string;
  originalName?: string;
  size: number;
  totalChars?: number;
  chapterCount?: number;
  uploadedAt: number;
};
export type ReingestConfirmation = {
  stage: "choose_overwrite" | "confirm_clear";
  filename: string;
  project: string;
  originalText: string;
};
type IngestAutomationResult = {
  filename: string;
  taskType?: string;
  taskKey?: string;
  message?: string;
  rebuild?: boolean;
};
const VIDEO_CREATION_RE =
  /(生成|创建|制作|开始|做|转|剪|出).{0,12}(视频|短剧|短片|成片|影片)|(?:视频|短剧|短片|成片|影片).{0,12}(生成|创建|制作|开始|做|转)|create.{0,16}video|make.{0,16}video|generate.{0,16}video|story.{0,12}video/i;
const UPLOADED_FILES_QUERY_RE =
  /(当前|现在|刚才|我)?\s*(上传|传了|传过|已上传).{0,12}(哪些|什么|列表|文件|剧本|小说)|(?:what|which|list|show).{0,20}uploaded.{0,10}(files?|scripts?)/i;

const NOVEL_ATTACHMENT_EXTENSIONS = new Set([".txt", ".md", ".doc", ".docx"]);
const INLINE_TEXT_ATTACHMENT_EXTENSIONS = new Set([".txt", ".md"]);
const NOVEL_ATTACHMENT_MIME_TYPES = new Set([
  "text/markdown",
  "text/plain",
  "application/msword",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
]);
const INLINE_TEXT_ATTACHMENT_LIMIT = 120_000;
const UPLOADED_INGEST_FILES_PREFIX = "superchat:ingest-uploads:";

function uploadedIngestFilesKey(project?: string): string | null {
  const id = project?.trim();
  if (!id) return null;
  return `${UPLOADED_INGEST_FILES_PREFIX}${id}`;
}

function isUploadedIngestFile(value: unknown): value is UploadedIngestFile {
  if (!value || typeof value !== "object") return false;
  const record = value as Record<string, unknown>;
  return (
    typeof record.filename === "string" &&
    typeof record.size === "number" &&
    typeof record.uploadedAt === "number"
  );
}

function loadUploadedIngestFiles(project?: string): UploadedIngestFile[] {
  const key = uploadedIngestFilesKey(project);
  if (!key) return [];
  try {
    const raw = JSON.parse(localStorage.getItem(key) || "[]");
    return Array.isArray(raw) ? raw.filter(isUploadedIngestFile).slice(-20) : [];
  } catch {
    return [];
  }
}

function saveUploadedIngestFiles(project: string | undefined, files: UploadedIngestFile[]) {
  const key = uploadedIngestFilesKey(project);
  if (!key) return;
  try {
    localStorage.setItem(key, JSON.stringify(files.slice(-20)));
  } catch {
    // best-effort chat context
  }
}

function mergeUploadedIngestFiles(
  current: UploadedIngestFile[],
  additions: UploadedIngestFile[],
): UploadedIngestFile[] {
  if (additions.length === 0) return current;
  const byFilename = new Map<string, UploadedIngestFile>();
  for (const item of current) byFilename.set(item.filename, item);
  for (const item of additions) byFilename.set(item.filename, item);
  return [...byFilename.values()]
    .sort((left, right) => left.uploadedAt - right.uploadedAt)
    .slice(-20);
}

function extensionOf(filename?: string): string {
  const name = filename?.trim().toLowerCase() ?? "";
  const dot = name.lastIndexOf(".");
  return dot >= 0 ? name.slice(dot) : "";
}

function isNovelAttachment(attachment: ChatAttachment): boolean {
  return NOVEL_ATTACHMENT_EXTENSIONS.has(extensionOf(attachment.fileName));
}

function isAllowedScriptUpload(file: File): boolean {
  return NOVEL_ATTACHMENT_EXTENSIONS.has(extensionOf(file.name));
}

function isAllowedScriptDragItem(item: { name?: string; type?: string }): boolean {
  const extension = extensionOf(item.name);
  if (extension) return NOVEL_ATTACHMENT_EXTENSIONS.has(extension);
  const type = item.type?.trim().toLowerCase() ?? "";
  if (!type) return true;
  return NOVEL_ATTACHMENT_MIME_TYPES.has(type);
}

function isInlineTextAttachment(attachment: ChatAttachment): boolean {
  return INLINE_TEXT_ATTACHMENT_EXTENSIONS.has(extensionOf(attachment.fileName));
}

function shouldReportUploadedFiles(text: string): boolean {
  return UPLOADED_FILES_QUERY_RE.test(text);
}

function isOverwriteChoice(text: string): boolean {
  return /^覆盖[。.!！?？\s]*$/.test(text.trim());
}

function isFinalOverwriteConfirmation(text: string): boolean {
  return /^(确定|继续)[。.!！?？\s]*$/.test(text.trim());
}

function uploadedFileFromPrepared(item: PreparedIngestAttachment): UploadedIngestFile | null {
  if (!item.upload) return null;
  return {
    filename: item.upload.filename,
    originalName: item.original.fileName,
    size: item.upload.size,
    totalChars: item.upload.total_chars,
    chapterCount: item.upload.count,
    uploadedAt: Date.now(),
  };
}

function buildUploadedFilesContext(project: string | undefined, files: UploadedIngestFile[]): string {
  const lines = [
    "[VILLAGE_CANVAS_UPLOADED_FILES]",
    "If the user asks what files are currently uploaded, answer directly from this list. These files have already been uploaded to the current Village Infinite Canvas project ingest directory.",
    project ? `village_canvas_project_id: ${project}` : null,
  ].filter((line): line is string => line !== null);

  if (files.length === 0) {
    lines.push("no_uploaded_files: true");
  } else {
    files.forEach((file, index) => {
      lines.push("");
      lines.push(`file_${index + 1}_filename: ${file.filename}`);
      if (file.originalName && file.originalName !== file.filename) {
        lines.push(`file_${index + 1}_original_name: ${file.originalName}`);
      }
      lines.push(`file_${index + 1}_size_bytes: ${file.size}`);
      if (typeof file.totalChars === "number") {
        lines.push(`file_${index + 1}_total_chars: ${file.totalChars}`);
      }
      if (typeof file.chapterCount === "number") {
        lines.push(`file_${index + 1}_chapter_count: ${file.chapterCount}`);
      }
    });
  }

  lines.push("[/VILLAGE_CANVAS_UPLOADED_FILES]");
  return lines.join("\n");
}

function buildReingestConfirmationContext(
  pending: ReingestConfirmation,
): string {
  return [
    "[VILLAGE_CANVAS_REINGEST_CONFIRMATION]",
    `stage: ${pending.stage}`,
    `village_canvas_project_id: ${pending.project}`,
    `filename: ${pending.filename}`,
    pending.stage === "choose_overwrite"
      ? "The current project has already ingested a script. Do not call ingest/start yet. Tell the user the current project is not empty and ask only whether they want to overwrite this project. Do not recommend creating a new project, and do not offer to create another project from the current project flow."
      : "The user chose overwrite. Do not call ingest/start yet. Ask the second confirmation and warn that overwrite/rebuild will clear existing characters, episodes, scripts, sketches, audio, videos, and other pipeline outputs. Only an exact user reply of 确定 or 继续 may proceed.",
    "[/VILLAGE_CANVAS_REINGEST_CONFIRMATION]",
  ].join("\n");
}

function buildReingestCancelledContext(pending: ReingestConfirmation): string {
  return [
    "[VILLAGE_CANVAS_REINGEST_CANCELLED]",
    `stage: ${pending.stage}`,
    `village_canvas_project_id: ${pending.project}`,
    `filename: ${pending.filename}`,
    "The overwrite/re-ingest flow was cancelled or not explicitly confirmed. Do not call any write API. Briefly tell the user no overwrite was performed.",
    "[/VILLAGE_CANVAS_REINGEST_CANCELLED]",
  ].join("\n");
}

async function uploadNovelForIngest(
  project: string,
  file: AttachmentBlob,
): Promise<IngestUploadResult> {
  const formData = new FormData();
  formData.append("file", file.blob, file.filename);
  const response = await jsonWithBackendError<OkResponse<IngestUploadResult> | ErrorResponse>(
    api.post(p`api/v1/projects/${project}/ingest/upload`, { body: formData }),
  );
  if (!response.ok) {
    const fc = (response as ErrorResponse & { format_check?: FormatCheck }).format_check;
    throw new Error(fc?.summary || response.error);
  }
  return response.data;
}

// Surface non-blocking format warnings as a success+risk toast per file. Upload
// already succeeded for these (warning never blocks), so we only notify and let
// the user open the details dialog. Iterate every prepared file, not just the first.
function surfaceFormatCheckWarnings(
  prepared: PreparedIngestAttachment[],
  t: TFunction,
  onViewDetails: (fc: FormatCheck, filename: string) => void,
): void {
  for (const item of prepared) {
    const fc = item.upload?.format_check;
    if (!fc || fc.level !== "warning") continue;
    const filename = item.upload?.filename || item.original.fileName || "";
    toast.warning(fc.summary, {
      action: {
        label: t("aiAssistant.formatCheck.viewDetails"),
        onClick: () => onViewDetails(fc, filename),
      },
    });
  }
}

async function uploadAttachmentsForIngest(
  project: string,
  attachments: ChatAttachment[],
  t: TFunction,
): Promise<PreparedIngestAttachment[]> {
  const prepared: PreparedIngestAttachment[] = [];

  for (const attachment of attachments) {
    const file = isNovelAttachment(attachment)
      ? dataUrlToAttachmentBlob(attachment)
      : null;

    if (!file) {
      prepared.push({ attachment, original: attachment });
      continue;
    }

    try {
      toast.info(t("aiAssistant.attachmentAnalysisUploading", { filename: file.filename }));
      const upload = await uploadNovelForIngest(project, file);
      const { content: _content, path: _path, url: _url, ...attachmentMetadata } = attachment;
      prepared.push({
        upload,
        original: attachment,
        attachment: {
          ...attachmentMetadata,
          fileName: upload.filename,
          fileSize: upload.size,
        },
      });
    } catch (error) {
      const message = backendErrorToastMessage(error, t);
      const { content: _content, ...attachmentMetadata } = attachment;
      prepared.push({
        original: attachment,
        attachment: attachmentMetadata,
        error: message,
      });
    }
  }

  return prepared;
}

async function startNovelIngest(
  project: string,
  filename: string,
  options: { rebuild?: boolean } = {},
): Promise<TaskResponse> {
  const response = await jsonWithBackendError<TaskResponse | ErrorResponse>(
    api.post(p`api/v1/projects/${project}/ingest/start`, {
      json: {
        filename,
        rebuild: options.rebuild ?? false,
      },
    }),
  );
  if (!response.ok) {
    throw new Error(response.error);
  }
  return response;
}

async function projectHasIngestedContent(project: string): Promise<boolean> {
  const response = await api
    .get(p`api/v1/projects/${project}/pipeline/status`)
    .json<
      | OkResponse<{ global?: { ingested?: boolean } }>
      | ErrorResponse
    >();
  if (!response.ok) {
    throw new Error(response.error);
  }
  return Boolean(response.data.global?.ingested);
}

async function buildAttachmentAnalysisContext(
  project: string | undefined,
  preparedAttachments: PreparedIngestAttachment[],
): Promise<string> {
  const lines = [
    "[VILLAGE_CANVAS_ATTACHMENT_CONTEXT]",
    "The user attached file(s). No explicit video-generation instruction was detected, so do not start the Village Infinite Canvas video pipeline unless the user asks for it later. Analyze the attached text when available, and ask a focused follow-up if the intent is ambiguous.",
  ];

  for (const prepared of preparedAttachments) {
    const attachment = prepared.attachment;
    const originalAttachment = prepared.original;
    const filename = attachment.fileName || "attachment";
    const ext = extensionOf(filename);
    lines.push("");
    lines.push(`file: ${filename}`);
    lines.push(`mime_type: ${attachment.mimeType || "application/octet-stream"}`);
    if (typeof attachment.fileSize === "number") {
      lines.push(`size_bytes: ${attachment.fileSize}`);
    }

    if (project && isNovelAttachment(originalAttachment)) {
      if (prepared.upload) {
        lines.push(`village_canvas_upload_filename: ${prepared.upload.filename}`);
        lines.push(`village_canvas_project_id: ${project}`);
        lines.push("village_canvas_upload_target: village_ingest");
        if (typeof prepared.upload.total_chars === "number") {
          lines.push(`village_canvas_total_chars: ${prepared.upload.total_chars}`);
        }
        if (typeof prepared.upload.count === "number") {
          lines.push(`village_canvas_chapter_count: ${prepared.upload.count}`);
        }
      } else if (prepared.error) {
        lines.push(`village_canvas_upload_error: ${prepared.error}`);
      }
    }

    if (isInlineTextAttachment(originalAttachment)) {
      const text = dataUrlToText(originalAttachment);
      if (text) {
        const truncated = text.length > INLINE_TEXT_ATTACHMENT_LIMIT;
        lines.push(`text_content${truncated ? "_truncated" : ""}:`);
        lines.push("```text");
        lines.push(text.slice(0, INLINE_TEXT_ATTACHMENT_LIMIT));
        lines.push("```");
        if (truncated) {
          lines.push(`truncated_after_chars: ${INLINE_TEXT_ATTACHMENT_LIMIT}`);
        }
      } else if (ext) {
        lines.push(`text_decode_error: unable to decode ${ext} attachment in the browser`);
      }
    } else if (isNovelAttachment(attachment)) {
      lines.push("text_content_unavailable: this attachment type cannot be decoded in the browser without starting the video ingest flow");
    }
  }

  lines.push("[/VILLAGE_CANVAS_ATTACHMENT_CONTEXT]");
  return lines.join("\n");
}

function appendIngestAutomationContext(
  text: string,
  result: IngestAutomationResult,
): string {
  return [
    text,
    "",
    "[VILLAGE_CANVAS_INGEST_AUTOMATION]",
    `novel_filename: ${result.filename}`,
    result.rebuild ? "rebuild: true" : "rebuild: false",
    result.taskType ? `task_type: ${result.taskType}` : null,
    result.taskKey ? `task_key: ${result.taskKey}` : null,
    result.message ? `message: ${result.message}` : null,
    "The uploaded novel has already been submitted to the project ingest API. Continue the Village Infinite Canvas video creation workflow from this task instead of asking the user to upload a novel again.",
    "[/VILLAGE_CANVAS_INGEST_AUTOMATION]",
  ].filter((line): line is string => line !== null).join("\n");
}

function appendAttachmentAnalysisContext(text: string, context: string): string {
  return [text, "", context].join("\n");
}

export {
  VIDEO_CREATION_RE,
  UPLOADED_FILES_QUERY_RE,
  isAllowedScriptUpload,
  isAllowedScriptDragItem,
  isNovelAttachment,
  shouldReportUploadedFiles,
  isOverwriteChoice,
  isFinalOverwriteConfirmation,
  loadUploadedIngestFiles,
  saveUploadedIngestFiles,
  mergeUploadedIngestFiles,
  uploadedFileFromPrepared,
  buildUploadedFilesContext,
  buildReingestConfirmationContext,
  buildReingestCancelledContext,
  uploadAttachmentsForIngest,
  startNovelIngest,
  projectHasIngestedContent,
  buildAttachmentAnalysisContext,
  surfaceFormatCheckWarnings,
  appendIngestAutomationContext,
  appendAttachmentAnalysisContext,
};

