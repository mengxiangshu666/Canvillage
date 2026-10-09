import { describe, expect, it } from "vitest";

import {
  generationTaskDescriptor,
  generationTaskRefsFromNode,
} from "./resumeGeneration";
import {
  buildGenerationTerminalPatch,
  buildVideoMetadataPatch,
  CLEARED_GENERATION_ERROR_PATCH,
} from "./generationTaskArbitration";

const ref = {
  task_type: "freezone_video_gen" as const,
  task_key: "task-video-1",
  job_id: "job-video-1",
};

describe("node generation cancellation handles", () => {
  it("keeps all batch task refs while retaining the legacy primary fields", () => {
    const descriptor = generationTaskDescriptor(ref);
    expect(descriptor.generationTaskRefs).toEqual([
      { taskKey: "task-video-1", taskType: "freezone_video_gen", jobId: "job-video-1" },
    ]);
    expect(
      generationTaskRefsFromNode({
        ...descriptor,
        generationTaskRefs: [
          ...descriptor.generationTaskRefs,
          { taskKey: "task-video-2", taskType: "freezone_video_gen", jobId: "job-video-2" },
        ],
      }),
    ).toHaveLength(2);
  });

  it("falls back to the persisted primary task for older nodes", () => {
    expect(
      generationTaskRefsFromNode({
        generationTaskKey: "task-old",
        generationTaskType: "freezone_audio_speech",
        generationTaskJobId: "job-old",
      }),
    ).toEqual([
      { taskKey: "task-old", taskType: "freezone_audio_speech", jobId: "job-old" },
    ]);
  });

  it("clears every durable task and queue field before a retry", () => {
    expect(buildGenerationTerminalPatch({ generationError: "provider failed" })).toEqual({
      isGenerating: false,
      generationStartedAt: null,
      generationTaskKey: null,
      generationTaskType: null,
      generationTaskJobId: null,
      generationTaskRefs: null,
      generationQueueTotal: null,
      generationQueueCompleted: null,
      generationQueueConcurrency: null,
      scriptGenerationInputFingerprint: null,
      scriptGenerationAttemptId: null,
      generationError: "provider failed",
    });
  });

  it("dismisses a failure without leaving a handle that can resurrect it", () => {
    expect(CLEARED_GENERATION_ERROR_PATCH).toEqual({
      generationError: null,
      generationErrorDetails: null,
      generationErrorRequestId: null,
      generationErrorStage: null,
      generationErrorSuggestedAction: null,
      generationErrorCode: null,
      generationErrorRetryable: null,
      generationRecoveryJobId: null,
      generationRecoveryTaskType: null,
      generationJobId: null,
      generationProviderId: null,
      generationClientSessionId: null,
    });
  });

  it("records real video dimensions and compares them with the frozen request", () => {
    expect(buildVideoMetadataPatch({
      width: 1080,
      height: 1920,
      durationSeconds: 5.001,
      requestedAspectRatio: "16:9",
    })).toEqual({
      widthPx: 1080,
      heightPx: 1920,
      actualWidth: 1080,
      actualHeight: 1920,
      actualAspectRatio: "9:16",
      aspectRatioMismatch: true,
      durationMs: 5001,
    });

    expect(buildVideoMetadataPatch({
      width: 1920,
      height: 1080,
      requestedAspectRatio: "16:9",
    }).aspectRatioMismatch).toBe(false);
  });
});
