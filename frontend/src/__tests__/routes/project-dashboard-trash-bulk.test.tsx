// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ComponentProps } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  purge: vi.fn<(project: string) => Promise<unknown>>(),
  toastSuccess: vi.fn(),
  toastError: vi.fn(),
  dashboardTab: "deleted" as "active" | "archived" | "deleted",
  dashboardView: "card" as "card" | "list",
  projects: [
    {
      id: "trash-a",
      name: "A",
      status: "deleted" as const,
      effectiveRole: "owner" as const,
      updatedAt: "2026-10-20T00:00:00Z",
    },
    {
      id: "trash-b",
      name: "B",
      status: "deleted" as const,
      effectiveRole: "owner" as const,
      updatedAt: "2026-10-19T00:00:00Z",
    },
    {
      id: "trash-shared",
      name: "Shared",
      status: "deleted" as const,
      effectiveRole: "editor" as const,
      updatedAt: "2026-10-18T00:00:00Z",
    },
  ],
}));

const labels: Record<string, string> = {
  "project.dashboardTitle": "项目管理中心",
  "nav.allProjects": "所有项目",
  "project.dashboardSubtitle": "管理你的短剧项目和脚本",
  "project.searchPlaceholder": "搜索项目…",
  "project.statusActive": "进行中",
  "project.statusArchived": "归档",
  "project.statusDeleted": "回收站",
  "project.trashRetentionHint": "回收站中的项目会一直保留，直到你永久删除。",
  "project.bulk.selectAll": "全选",
  "project.bulk.selectAllLabel": "全选回收站项目",
  "project.bulk.selectProject": "选择项目「{{name}}」",
  "project.bulk.selectedCount": "已选 {{count}} 项",
  "project.bulk.deleteSelected": "永久删除所选",
  "project.bulk.purgeDialog.title": "永久删除 {{count}} 个项目？",
  "project.bulk.purgeDialog.description":
    "这 {{count}} 个项目的所有脚本、草图、音频和视频都将被移除。此操作无法撤销。",
  "project.purgeDialog.confirm": "永久删除",
  "project.toasts.bulkPurgePartial":
    "已永久删除 {{purged}} 个项目；{{failed}} 个项目未删除，已保留勾选，可重试",
  "common.cancel": "取消",
};

function translate(key: string, values: Record<string, unknown> = {}) {
  return (labels[key] ?? key).replace(/\{\{(\w+)\}\}/g, (_, name: string) =>
    String(values[name] ?? ""),
  );
}

vi.mock("@tanstack/react-router", () => ({
  createFileRoute: () => (options: unknown) => options,
  useNavigate: () => vi.fn(),
  useRouter: () => ({ preloadRoute: vi.fn(() => Promise.resolve()) }),
}));

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: translate }),
}));

vi.mock("sonner", () => ({
  toast: { success: mocks.toastSuccess, error: mocks.toastError },
}));

vi.mock("framer-motion", () => ({
  motion: {
    div: ({
      children,
      initial: _initial,
      animate: _animate,
      variants: _variants,
      ...props
    }: ComponentProps<"div"> & {
      initial?: unknown;
      animate?: unknown;
      variants?: unknown;
    }) => <div {...props}>{children}</div>,
  },
}));

vi.mock("@/lib/queries/projects", () => ({
  fetchProjectSummaries: vi.fn(),
  useAllProjectSummaries: () => ({
    data: mocks.projects,
    isLoading: false,
    isError: false,
    isFetching: false,
    refetch: vi.fn(),
  }),
  useProjectCounts: () => ({
    active: 0,
    archived: 0,
    deleted: mocks.projects.length,
  }),
  useCreateProject: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useArchiveProject: () => ({ mutate: vi.fn() }),
  useUnarchiveProject: () => ({ mutate: vi.fn() }),
  useSoftDeleteProject: () => ({ mutate: vi.fn() }),
  useRestoreProject: () => ({ mutate: vi.fn() }),
  usePurgeProject: () => ({ mutate: vi.fn(), mutateAsync: mocks.purge }),
}));

vi.mock("@/stores/app-store", () => {
  const state = {
    get dashboardTab() {
      return mocks.dashboardTab;
    },
    get dashboardView() {
      return mocks.dashboardView;
    },
    setDashboardTab: (tab: typeof mocks.dashboardTab) => {
      mocks.dashboardTab = tab;
    },
    setDashboardView: (view: typeof mocks.dashboardView) => {
      mocks.dashboardView = view;
    },
  };
  return {
    useAppStore: (selector: (value: typeof state) => unknown) => selector(state),
  };
});

vi.mock("@/lib/project-permissions", () => ({
  canDeleteProject: (summary: { effectiveRole?: string }) =>
    summary.effectiveRole === "owner",
  canManageProjectGrants: () => false,
  isSharedProject: (summary: { effectiveRole?: string }) =>
    summary.effectiveRole !== "owner",
  projectRole: (summary: { effectiveRole?: string }) =>
    summary.effectiveRole ?? "owner",
}));

vi.mock("@/components/projects/share-project-dialog", () => ({
  ShareProjectDialog: () => null,
}));

vi.mock("@/features/superchat/SkillStoreDialog", () => ({
  SkillStoreDialog: () => null,
}));

import { ProjectDashboard } from "@/routes/_app/studio";

describe("project dashboard trash bulk purge", () => {
  beforeEach(() => {
    mocks.purge.mockReset();
    mocks.toastSuccess.mockReset();
    mocks.toastError.mockReset();
    mocks.dashboardTab = "deleted";
    mocks.dashboardView = "card";
  });

  it("selects all filtered trash items, confirms the count, and keeps failures selected", async () => {
    mocks.purge.mockImplementation(async (project) => {
      if (project === "trash-b") throw new Error("locked");
    });

    render(<ProjectDashboard />);
    fireEvent.click(screen.getAllByRole("button", { name: "所有项目" })[0]);
    expect(
      screen.queryByRole("checkbox", { name: "选择项目「Shared」" }),
    ).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("checkbox", { name: "选择项目「A」" }));
    expect(screen.getByText("已选 1 项")).toBeInTheDocument();

    fireEvent.change(screen.getByRole("textbox", { name: "搜索项目…" }), {
      target: { value: "A" },
    });
    await waitFor(() => expect(screen.getByText("已选 0 项")).toBeInTheDocument());
    fireEvent.change(screen.getByRole("textbox", { name: "搜索项目…" }), {
      target: { value: "" },
    });

    fireEvent.click(screen.getByRole("checkbox", { name: "全选回收站项目" }));
    expect(screen.getByText("已选 2 项")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "永久删除所选" }));

    const dialog = await screen.findByRole("alertdialog");
    expect(within(dialog).getByText("永久删除 2 个项目？")).toBeInTheDocument();
    expect(
      within(dialog).getByText(
        "这 2 个项目的所有脚本、草图、音频和视频都将被移除。此操作无法撤销。",
      ),
    ).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "永久删除" }));

    await waitFor(() => expect(mocks.purge).toHaveBeenCalledTimes(2));
    expect(mocks.purge).toHaveBeenNthCalledWith(1, "trash-a");
    expect(mocks.purge).toHaveBeenNthCalledWith(2, "trash-b");
    await waitFor(() => expect(screen.getByText("已选 1 项")).toBeInTheDocument());
    expect(
      screen.getByRole("checkbox", { name: "选择项目「B」" }),
    ).toBeChecked();
    expect(
      screen.getByRole("checkbox", { name: "选择项目「A」" }),
    ).not.toBeChecked();
    expect(mocks.toastError).toHaveBeenCalledWith(
      "已永久删除 1 个项目；1 个项目未删除，已保留勾选，可重试",
    );
  });

  it("hides the selection control for a shared project in list view", () => {
    mocks.dashboardView = "list";

    render(<ProjectDashboard />);
    fireEvent.click(screen.getAllByRole("button", { name: "所有项目" })[0]);

    expect(
      screen.getByRole("checkbox", { name: "选择项目「A」" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("checkbox", { name: "选择项目「Shared」" }),
    ).not.toBeInTheDocument();
  });

  it("selects only the filtered deletable results and clears on tab change", async () => {
    const view = render(<ProjectDashboard />);
    fireEvent.click(screen.getAllByRole("button", { name: "所有项目" })[0]);
    fireEvent.change(screen.getByRole("textbox", { name: "搜索项目…" }), {
      target: { value: "B" },
    });

    await waitFor(() =>
      expect(screen.getByText("已选 0 项")).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole("checkbox", { name: "全选回收站项目" }));
    expect(screen.getByText("已选 1 项")).toBeInTheDocument();
    expect(
      screen.queryByRole("checkbox", { name: "选择项目「A」" }),
    ).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "进行中0" }));
    view.rerender(<ProjectDashboard />);
    fireEvent.click(screen.getByRole("button", { name: "回收站3" }));
    view.rerender(<ProjectDashboard />);

    await waitFor(() =>
      expect(screen.getByText("已选 0 项")).toBeInTheDocument(),
    );
  });
});
