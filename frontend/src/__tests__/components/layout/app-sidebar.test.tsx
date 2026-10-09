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
  it("keeps project actions distinct from top-level workflow and settings controls", () => {
    render(<AppSidebar />);

    expect(screen.getByRole("link", { name: "任务管理" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "工作流" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "设置" })).not.toBeInTheDocument();
  });
});
