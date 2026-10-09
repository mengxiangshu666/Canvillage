// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from "vitest";

import {
  BackendStatusError,
  backendErrorDetailCode,
  requiresPaidMediaConfirmation,
} from "@/lib/api-errors";

function conflict(code: string, message = "server says no") {
  return new BackendStatusError(message, 409, {
    detail: { code, message },
  });
}

describe("backendErrorDetailCode", () => {
  it("reads the FastAPI detail code carried on a thrown API error", () => {
    expect(backendErrorDetailCode(conflict("run_terminal"))).toBe("run_terminal");
  });

  it("accepts a top-level code and ignores unrelated values", () => {
    expect(
      backendErrorDetailCode(new BackendStatusError("x", 500, { code: "boom" })),
    ).toBe("boom");
    expect(backendErrorDetailCode(new Error("plain"))).toBeNull();
    expect(
      backendErrorDetailCode(new BackendStatusError("x", 500, { detail: {} })),
    ).toBeNull();
    expect(backendErrorDetailCode(undefined)).toBeNull();
  });
});

describe("requiresPaidMediaConfirmation", () => {
  it("matches only the server's paid-media gate", () => {
    expect(
      requiresPaidMediaConfirmation(conflict("paid_media_confirmation_required")),
    ).toBe(true);
    expect(requiresPaidMediaConfirmation(conflict("run_command_conflict"))).toBe(
      false,
    );
    expect(requiresPaidMediaConfirmation(new Error("network down"))).toBe(false);
  });
});
