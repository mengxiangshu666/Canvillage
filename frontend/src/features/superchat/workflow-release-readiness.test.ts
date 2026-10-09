import { describe, expect, it } from "vitest";

import {
  workflowReleaseBlockingChecks,
  workflowReleaseReadinessFromRun,
  workflowReleaseRequiresNotice,
  workflowReleaseSummary,
  workflowRunCanPublish,
} from "./workflow-release-readiness";
import type { WorkflowRun } from "@/types/workflow-runtime";

function runWithReadiness(
  status: "not_applicable" | "unverified" | "blocked" | "ready",
): WorkflowRun {
  return {
    id: "run-release",
    release_readiness: {
      schema: "release_readiness_contract.v1",
      status,
      reason_code:
        status === "ready" ? "delivery_qc_passed" : "delivery_qc_failed",
      can_publish: status === "ready",
      required: status !== "not_applicable",
      failed_checks: status === "blocked" ? ["freeze_frames"] : [],
      not_run_checks: status === "unverified" ? ["loudness"] : [],
      missing_checks: [],
    },
  } as unknown as WorkflowRun;
}

describe("workflow release readiness", () => {
  it("fails closed when the response contract is absent or malformed", () => {
    expect(workflowReleaseReadinessFromRun(null)).toBeNull();
    expect(workflowReleaseReadinessFromRun({} as WorkflowRun)).toBeNull();
    expect(workflowRunCanPublish(runWithReadiness("blocked"))).toBe(false);
    expect(workflowRunCanPublish(runWithReadiness("unverified"))).toBe(false);
  });

  it("shows a notice only when a final film exists and reports every blocker", () => {
    expect(
      workflowReleaseRequiresNotice(
        runWithReadiness("not_applicable").release_readiness,
      ),
    ).toBe(false);
    expect(
      workflowReleaseRequiresNotice(
        runWithReadiness("blocked").release_readiness,
      ),
    ).toBe(true);
    expect(
      workflowReleaseBlockingChecks(
        runWithReadiness("blocked").release_readiness!,
      ),
    ).toEqual(["静帧"]);
  });

  it("allows publishing only from an explicit ready gate", () => {
    expect(workflowRunCanPublish(runWithReadiness("ready"))).toBe(true);
    const forged = runWithReadiness("blocked");
    forged.release_readiness!.can_publish = true;
    expect(workflowRunCanPublish(forged)).toBe(false);
  });

  it("explains artifact integrity blockers in creator language", () => {
    const mismatched = runWithReadiness("blocked").release_readiness!;
    mismatched.reason_code = "delivery_artifact_hash_mismatch";
    mismatched.failed_checks = ["artifact_sha256_match"];

    expect(workflowReleaseSummary(mismatched)).toBe("QC 报告与当前成片不匹配");
    expect(workflowReleaseBlockingChecks(mismatched)).toEqual([
      "成片与质量报告一致",
    ]);

    const missingArtifact = runWithReadiness("unverified").release_readiness!;
    missingArtifact.reason_code = "final_compose_artifact_missing";
    expect(workflowReleaseSummary(missingArtifact)).toBe(
      "缺少最终成片文件，无法核验发布状态",
    );

    const missingHash = runWithReadiness("unverified").release_readiness!;
    missingHash.reason_code = "delivery_artifact_hash_missing";
    expect(workflowReleaseSummary(missingHash)).toBe(
      "缺少成片文件指纹，无法核验 QC 对应文件",
    );
  });
});
