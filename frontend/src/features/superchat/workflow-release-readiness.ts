import type {
  WorkflowReleaseReadiness,
  WorkflowReleaseStatus,
  WorkflowRun,
} from "@/types/workflow-runtime";

const RELEASE_STATUSES = new Set<WorkflowReleaseStatus>([
  "not_applicable",
  "unverified",
  "blocked",
  "ready",
]);

const RELEASE_CHECK_LABELS: Record<string, string> = {
  artifact_sha256_match: "成片与质量报告一致",
  audio_activity: "音频有效性",
  audio_stream_present: "音频轨道",
  av_sync: "音画同步",
  bitrate: "码率",
  black_frames: "黑场",
  color_space: "色彩空间",
  container_allowed: "视频格式",
  dimensions: "分辨率",
  duration: "成片时长",
  file_readback: "文件读取",
  frame_rate: "帧率",
  freeze_frames: "静帧",
  loudness: "响度",
  sha256: "文件指纹",
  subtitle_stream: "字幕轨道",
  true_peak: "音频峰值",
  video_stream_present: "视频轨道",
};

export function workflowReleaseReadinessFromRun(
  run: WorkflowRun | null | undefined,
): WorkflowReleaseReadiness | null {
  const readiness = run?.release_readiness;
  if (
    !readiness ||
    readiness.schema !== "release_readiness_contract.v1" ||
    !RELEASE_STATUSES.has(readiness.status)
  ) {
    return null;
  }
  return readiness;
}

export function workflowReleaseRequiresNotice(
  readiness: WorkflowReleaseReadiness | null | undefined,
): boolean {
  return Boolean(readiness?.required && readiness.status !== "not_applicable");
}

export function workflowReleaseStatusLabel(
  status: WorkflowReleaseStatus,
): string {
  if (status === "ready") return "可发布";
  if (status === "blocked") return "发布被阻断";
  if (status === "unverified") return "发布未验证";
  return "尚未成片";
}

export function workflowReleaseSummary(
  readiness: WorkflowReleaseReadiness,
): string {
  if (readiness.status === "ready") return "工程发布检查已通过";
  if (readiness.reason_code === "delivery_qc_missing") {
    return "终片缺少工程 QC 回执";
  }
  if (readiness.reason_code === "delivery_qc_invalid") {
    return "终片工程 QC 回执无效";
  }
  if (readiness.reason_code === "final_compose_artifact_missing") {
    return "缺少最终成片文件，无法核验发布状态";
  }
  if (readiness.reason_code === "delivery_artifact_hash_missing") {
    return "缺少成片文件指纹，无法核验 QC 对应文件";
  }
  if (readiness.reason_code === "delivery_artifact_hash_mismatch") {
    return "QC 报告与当前成片不匹配";
  }
  if (readiness.status === "blocked") return "终片工程 QC 存在失败项";
  if (readiness.status === "unverified") return "终片工程 QC 尚未全部完成";
  return "当前 Run 没有终片";
}

export function workflowReleaseBlockingChecks(
  readiness: WorkflowReleaseReadiness,
): string[] {
  return Array.from(
    new Set(
      [
        ...readiness.failed_checks,
        ...readiness.not_run_checks,
        ...readiness.missing_checks,
      ].filter(Boolean),
    ),
  ).map((check) => RELEASE_CHECK_LABELS[check] ?? check);
}

export function workflowRunCanPublish(
  run: WorkflowRun | null | undefined,
): boolean {
  const readiness = workflowReleaseReadinessFromRun(run);
  return readiness?.status === "ready" && readiness.can_publish === true;
}
