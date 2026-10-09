// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

import { ProjectStoryNavigation } from "@/components/layout/project-story-navigation";

describe("ProjectStoryNavigation", () => {
  it("exposes the three real story workspace destinations", () => {
    render(<ProjectStoryNavigation project="demo" active="library" />);

    expect(
      screen.getByRole("navigation", { name: "storyWorkspace.navigationLabel" }),
    ).toBeInTheDocument();
    expect(screen.getByText("storyWorkspace.create").closest("a")).toHaveAttribute(
      "href",
      "/projects/demo/story-lab",
    );
    expect(screen.getByText("storyWorkspace.import").closest("a")).toHaveAttribute(
      "href",
      "/projects/demo/ingest?view=import",
    );
    expect(screen.getByText("storyWorkspace.library").closest("a")).toHaveAttribute(
      "href",
      "/projects/demo/ingest?view=library",
    );
    expect(screen.getByText("storyWorkspace.library").closest("a")).toHaveAttribute(
      "aria-current",
      "page",
    );
  });
});
