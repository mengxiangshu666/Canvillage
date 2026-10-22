// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("@tanstack/react-router", () => ({
  Link: ({ children }: React.PropsWithChildren) => <a href="#">{children}</a>,
  useNavigate: () => vi.fn(),
  useParams: () => ({ project: "demo" }),
  useRouterState: ({ select }: { select: (state: { location: { pathname: string } }) => unknown }) =>
    select({ location: { pathname: "/projects/demo/tasks" } }),
}));

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (_key: string, fallback?: string) => fallback ?? "" }),
}));

import { AppSidebar } from "@/components/layout/app-sidebar";

describe("AppSidebar project navigation", () => {
  it("shows the complete project production path in the sidebar", () => {
    render(<AppSidebar />);

    for (const name of ["画布", "总控", "故事", "资产", "分镜", "制作", "任务记录"]) {
      expect(screen.getByRole("link", { name })).toBeInTheDocument();
    }
    expect(screen.queryByRole("link", { name: "工作流" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "设置" })).not.toBeInTheDocument();
  });
});
