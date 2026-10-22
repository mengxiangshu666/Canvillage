// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AppUpdateAvailable } from "@/components/app-update-available";
import {
  markUpdateAvailable,
  resetUpdateAvailableForTests,
} from "@/lib/app-update-available";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

describe("AppUpdateAvailable", () => {
  afterEach(() => {
    resetUpdateAvailableForTests();
    vi.unstubAllGlobals();
  });

  it("refreshes immediately even while automatic reload protection is pending", () => {
    const replace = vi.fn();
    vi.stubGlobal("location", {
      href: "http://127.0.0.1:8784/projects/p1/freezone?canvas=c1",
      replace,
      reload: vi.fn(),
    });

    render(<AppUpdateAvailable />);
    act(() => markUpdateAvailable());

    fireEvent.click(
      screen.getByRole("button", { name: "app.updateAvailable.refresh" }),
    );

    expect(replace).toHaveBeenCalledTimes(1);
    const reloadUrl = new URL(replace.mock.calls[0][0]);
    expect(reloadUrl.pathname).toBe("/projects/p1/freezone");
    expect(reloadUrl.searchParams.get("canvas")).toBe("c1");
    expect(reloadUrl.searchParams.get("__village_canvas_manual_reload")).toBeTruthy();
  });
});

