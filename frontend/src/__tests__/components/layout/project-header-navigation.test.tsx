// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const routerState = vi.hoisted(() => ({
  pathname: "/projects/demo/freezone",
  navigate: vi.fn(),
}));

const navigationState = vi.hoisted(() => ({
  rememberSection: vi.fn(),
  setLastEpisodeLocation: vi.fn(),
  clearLastEpisodeLocation: vi.fn(),
}));

vi.mock("@tanstack/react-router", () => ({
  Link: ({
    children,
    "aria-current": ariaCurrent,
  }: React.PropsWithChildren<{
    "aria-current"?: React.AriaAttributes["aria-current"];
  }>) => (
    <a href="#workflow-section" aria-current={ariaCurrent}>
      {children}
    </a>
  ),
  useNavigate: () => routerState.navigate,
  useRouterState: ({
    select,
  }: {
    select: (state: { location: { pathname: string } }) => unknown;
  }) => select({ location: { pathname: routerState.pathname } }),
}));

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => key,
  }),
}));

vi.mock("@/stores/episode-workbench-store", () => ({
  normalizeLastEpisodeLocation: () => null,
  useEpisodeWorkbenchStore: (
    selector: (state: {
      lastEpisodeLocationByProject: Record<string, string>;
      setLastEpisodeLocation: typeof navigationState.setLastEpisodeLocation;
      clearLastEpisodeLocation: typeof navigationState.clearLastEpisodeLocation;
    }) => unknown,
  ) => selector({
    lastEpisodeLocationByProject: {},
    setLastEpisodeLocation: navigationState.setLastEpisodeLocation,
    clearLastEpisodeLocation: navigationState.clearLastEpisodeLocation,
  }),
}));

vi.mock("@/stores/project-nav-store", () => ({
  isRememberedSection: () => true,
  useProjectNavStore: (
    selector: (state: {
      lastWorkflowSectionByProject: Record<string, "production">;
      rememberSection: typeof navigationState.rememberSection;
    }) => unknown,
  ) => selector({
    lastWorkflowSectionByProject: { demo: "production" },
    rememberSection: navigationState.rememberSection,
  }),
}));

import {
  ProjectHeaderNavigation,
  ProjectWorkflowMenu,
} from "@/components/layout/project-header-navigation";

describe("ProjectHeaderNavigation Village Canvas entry", () => {
  beforeEach(() => {
    routerState.pathname = "/projects/demo/freezone";
    routerState.navigate.mockReset();
    navigationState.rememberSection.mockReset();
    navigationState.setLastEpisodeLocation.mockReset();
    navigationState.clearLastEpisodeLocation.mockReset();
  });

  it("opens the production command center from the canvas", () => {
    render(<ProjectHeaderNavigation project="demo" />);

    const workflowButton = screen.getByRole("button", { name: "工作流" });
    expect(workflowButton.closest("nav")).toHaveClass("village-project-mode-navigation");
    fireEvent.click(workflowButton);

    expect(routerState.navigate).toHaveBeenCalledWith({
      to: "/projects/$project/production",
      params: { project: "demo" },
    });
  });

  it("keeps the current workflow page when the workflow mode is already active", () => {
    routerState.pathname = "/projects/demo/production";
    render(<ProjectHeaderNavigation project="demo" />);

    fireEvent.click(screen.getByRole("button", { name: "工作流" }));

    expect(routerState.navigate).not.toHaveBeenCalled();
  });

  it("keeps a professional workflow section active instead of forcing a redirect", () => {
    routerState.pathname = "/projects/demo/characters";
    render(<ProjectHeaderNavigation project="demo" />);

    const workflowButton = screen.getByRole("button", { name: "工作流" });
    expect(workflowButton).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(workflowButton);

    expect(routerState.navigate).not.toHaveBeenCalled();
  });

  it("shows the compact Village Canvas workflow sections", () => {
    routerState.pathname = "/projects/demo/production";
    render(<ProjectWorkflowMenu project="demo" />);

    const menu = screen.getByRole("navigation", { name: "nav.workflowMenu" });
    for (const label of ["总控", "故事", "资产", "分镜", "制作"]) {
      expect(within(menu).getByText(label)).toBeInTheDocument();
    }
    expect(within(menu).queryByText("nav.ingest")).not.toBeInTheDocument();
    expect(within(menu).queryByText("nav.aiAssistant")).not.toBeInTheDocument();
    expect(within(menu).queryByText("nav.styles")).not.toBeInTheDocument();
  });

  it("keeps the shared story entry active on the ingest route", () => {
    routerState.pathname = "/projects/demo/ingest";
    render(<ProjectWorkflowMenu project="demo" />);

    const menu = screen.getByRole("navigation", { name: "nav.workflowMenu" });
    expect(within(menu).getByText("故事")).toHaveAttribute(
      "aria-current",
      "page",
    );
  });
});
