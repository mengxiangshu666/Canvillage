// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ThemeToggle } from "@/components/theme-toggle";
import { ThemeProvider } from "@/components/theme-provider";
import { useAppStore } from "@/stores/app-store";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => ({
      "theme.toggle": "切换主题",
      "theme.white": "白色",
      "theme.gray": "灰色",
      "theme.black": "黑色",
    })[key] ?? key,
  }),
}));

describe("ThemeToggle", () => {
  beforeEach(() => {
    useAppStore.setState({ theme: "dark" });
  });

  it("offers three neutral themes and persists the selected gray theme", async () => {
    render(<ThemeToggle />);
    fireEvent.click(screen.getByRole("button", { name: "切换主题" }));

    expect(await screen.findByRole("menuitem", { name: "白色" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("menuitem", { name: "灰色" }));

    expect(useAppStore.getState().theme).toBe("gray");
    expect(JSON.parse(window.localStorage.getItem("village-app")!).state.theme).toBe("gray");
  });

  it("applies a neutral gray surface while keeping dark component semantics", async () => {
    render(<ThemeProvider><div /></ThemeProvider>);
    act(() => useAppStore.getState().setTheme("gray"));

    await waitFor(() => {
      expect(document.documentElement).toHaveClass("dark");
      expect(document.documentElement).toHaveAttribute("data-theme", "gray");
    });

    act(() => useAppStore.getState().setTheme("light"));
    await waitFor(() => {
      expect(document.documentElement).toHaveClass("light");
      expect(document.documentElement).not.toHaveClass("dark");
      expect(document.documentElement).not.toHaveAttribute("data-theme");
    });
  });
});
