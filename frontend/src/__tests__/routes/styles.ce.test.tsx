// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import type { ComponentType } from "react";
import type { Style } from "@/types/style";

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import i18next from "i18next";
import { I18nextProvider, initReactI18next } from "react-i18next";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

const runtimeState = vi.hoisted(() => ({ isCeRuntime: true }));
const toastErrorMock = vi.hoisted(() => vi.fn());
const updateProjectMock = vi.hoisted(() => vi.fn());
const styleMutationMocks = vi.hoisted(() => ({
  create: vi.fn(),
  remove: vi.fn(),
  analyze: vi.fn(),
  upload: vi.fn(),
  discard: vi.fn(),
}));
const styleQueryState = vi.hoisted(() => ({
  list: [{ id: "ink", name: "Ink", label: "Ink style", type: "preset" }] as Style[],
  detail: {
    id: "ink",
    name: "Ink",
    label: "Ink style",
    type: "preset",
    style_instructions: "clean ink lines",
    avoid_instructions: "muddy colors",
    style_tag: "ink",
  } as Style,
}));

vi.mock("@/lib/runtime-config", () => ({
  isCeRuntime: () => runtimeState.isCeRuntime,
}));

vi.mock("sonner", () => ({
  toast: {
    error: toastErrorMock,
    success: vi.fn(),
  },
}));

vi.mock("@tanstack/react-router", () => ({
  createFileRoute: () => (options: { component: ComponentType }) => ({
    options,
    useParams: () => ({ project: "demo" }),
  }),
}));

vi.mock("@/lib/queries/styles", () => ({
  useStyles: () => ({
    isLoading: false,
    isRefetching: false,
    refetch: vi.fn(),
    data: {
      ok: true,
      data: styleQueryState.list,
    },
  }),
  useStyleDetail: () => ({
    isFetching: false,
    data: {
      ok: true,
      data: styleQueryState.detail,
    },
  }),
  useCreateStyle: () => ({ mutateAsync: styleMutationMocks.create, isPending: false }),
  useDeleteStyle: () => ({ mutateAsync: styleMutationMocks.remove, isPending: false }),
  useAnalyzeStyle: () => ({ mutateAsync: styleMutationMocks.analyze, isPending: false }),
  useUploadStylePreview: () => ({ mutateAsync: styleMutationMocks.upload, isPending: false }),
  useDiscardStylePreview: () => ({ mutateAsync: styleMutationMocks.discard, isPending: false }),
}));

vi.mock("@/lib/queries/projects", () => ({
  useProject: () => ({ data: { ok: true, data: { visual_style: "ink" } } }),
  useUpdateProject: () => ({ mutateAsync: updateProjectMock, isPending: false }),
}));

import { Route } from "@/routes/_app/projects.$project/styles";

const i18n = i18next.createInstance();

beforeAll(async () => {
  await i18n.use(initReactI18next).init({
    lng: "en",
    fallbackLng: "en",
    interpolation: { escapeValue: false },
    resources: {
      en: {
        translation: {
          nav: { styles: "Styles" },
          common: {
            refresh: "Refresh",
            refreshed: "Refreshed",
            loading: "Loading",
            save: "Save",
            cancel: "Cancel",
            error: "Error",
          },
          styles: {
            selectStyleHint: "Select a style.",
            createStyle: "Create style",
            projectDefault: "Project default",
            preset: "Preset",
            custom: "Custom",
            customPreviewUnavailable: "No preview",
            customPreviewHint: "Upload a preview.",
            labelField: "Label",
            labelPlaceholder: "Label",
            projectStyleSection: "Project style",
            styleDirective: "Style directive",
            avoidDirective: "Avoid directive",
            styleTag: "Style tag",
            styleTagHint: "Short tag",
            styleTagPlaceholder: "tag",
            jsonEdit: "JSON",
            save: "Save",
            alreadyDefault: "Already default",
            applyToProject: "Apply to project",
            scriptAutoApplied: "Restored script-directed styling",
            useScriptAuto: "No preset · Follow script",
            delete: "Delete",
            createTitle: "Create style",
            createHint: "Create a new style.",
            styleId: "Style ID",
            nameField: "Name",
            namePlaceholder: "Name",
            aiAnalyze: "AI analyze",
            uploadRef: "Upload ref",
            reupload: "Reupload",
            previewTypeError: "Use PNG, JPEG, WebP, or GIF.",
            previewTooLarge: "Reference image must be under 20 MB.",
            styleIdRequired: "Enter a style ID first.",
            referencePreview: "Reference preview",
          },
        },
      },
    },
  });
});

function classNameContains(container: HTMLElement, token: string) {
  return Array.from(container.querySelectorAll("*")).some((node) =>
    String(node.getAttribute("class") ?? "").includes(token),
  );
}

describe("styles page CE generation credit gating", () => {
  beforeEach(() => {
    runtimeState.isCeRuntime = true;
    toastErrorMock.mockClear();
    updateProjectMock.mockReset();
    updateProjectMock.mockResolvedValue({ ok: true });
    for (const mock of Object.values(styleMutationMocks)) mock.mockReset();
    styleMutationMocks.upload.mockResolvedValue({
      ok: true,
      data: { preview_token: "assets/styles/.staging/custom/token.png" },
    });
    styleMutationMocks.analyze.mockResolvedValue({ ok: true, data: {} });
    styleMutationMocks.create.mockResolvedValue({ ok: true, data: { id: "custom" } });
    styleMutationMocks.discard.mockResolvedValue({ ok: true, data: { discarded: true } });
    styleQueryState.list = [
      { id: "ink", name: "Ink", label: "Ink style", type: "preset" },
    ];
    styleQueryState.detail = {
      id: "ink",
      name: "Ink",
      label: "Ink style",
      type: "preset",
      style_instructions: "clean ink lines",
      avoid_instructions: "muddy colors",
      style_tag: "ink",
    };
  });

  it("renders style controls without credit UI, credit styling, or credit errors", async () => {
    const Component = Route.options.component as ComponentType;
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    const { container } = render(
      <QueryClientProvider client={queryClient}>
        <I18nextProvider i18n={i18n}>
          <Component />
        </I18nextProvider>
      </QueryClientProvider>,
    );

    expect(await screen.findByText("Ink style")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Create style" })).toBeInTheDocument();

    expect(screen.queryByText(/credits?/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/积分|额度/)).not.toBeInTheDocument();
    expect(classNameContains(container, "#007A87")).toBe(false);
    expect(toastErrorMock).not.toHaveBeenCalledWith(
      expect.stringMatching(/积分不足|credit|insufficient/i),
    );
  });

  it("restores script-directed styling from the no-preset action", async () => {
    const user = userEvent.setup();
    const Component = Route.options.component as ComponentType;

    render(
      <QueryClientProvider client={new QueryClient()}>
        <I18nextProvider i18n={i18n}>
          <Component />
        </I18nextProvider>
      </QueryClientProvider>,
    );

    await user.click(
      await screen.findByRole("button", { name: "No preset · Follow script" }),
    );

    await waitFor(() =>
      expect(updateProjectMock).toHaveBeenCalledWith({
        visual_style: "script_auto",
      }),
    );
  });

  it("rejects unsupported reference images before upload or analysis", async () => {
    const user = userEvent.setup();
    const Component = Route.options.component as ComponentType;
    const queryClient = new QueryClient();
    const { container } = render(
      <QueryClientProvider client={queryClient}>
        <I18nextProvider i18n={i18n}><Component /></I18nextProvider>
      </QueryClientProvider>,
    );

    await user.click(screen.getByRole("button", { name: "Create style" }));
    await user.type(screen.getByPlaceholderText("cyberpunk_v1"), "custom");
    const input = container.ownerDocument.querySelector<HTMLInputElement>('input[type="file"]');
    fireEvent.change(input!, {
      target: { files: [new File(["image"], "reference.avif", { type: "image/avif" })] },
    });

    expect(styleMutationMocks.upload).not.toHaveBeenCalled();
    expect(styleMutationMocks.analyze).not.toHaveBeenCalled();
    expect(toastErrorMock).toHaveBeenCalledWith("Use PNG, JPEG, WebP, or GIF.");
  });

  it("stops analysis when preview staging returns an error envelope", async () => {
    styleMutationMocks.upload.mockResolvedValue({ ok: false, error: "Invalid preview" });
    const user = userEvent.setup();
    const Component = Route.options.component as ComponentType;
    const queryClient = new QueryClient();
    const { container } = render(
      <QueryClientProvider client={queryClient}>
        <I18nextProvider i18n={i18n}><Component /></I18nextProvider>
      </QueryClientProvider>,
    );

    await user.click(screen.getByRole("button", { name: "Create style" }));
    await user.type(screen.getByPlaceholderText("cyberpunk_v1"), "custom");
    fireEvent.change(container.ownerDocument.querySelector<HTMLInputElement>('input[type="file"]')!, {
      target: { files: [new File(["image"], "reference.png", { type: "image/png" })] },
    });

    await waitFor(() => expect(toastErrorMock).toHaveBeenCalledWith("Invalid preview"));
    expect(styleMutationMocks.analyze).not.toHaveBeenCalled();
  });

  it("renders persisted custom previews in both list and detail", async () => {
    const previewUrl = "/api/v1/projects/demo/media/assets/styles/custom/preview.webp";
    styleQueryState.list = [{
      id: "custom", name: "Custom", label: "Custom style", type: "custom", preview_url: previewUrl,
    }];
    styleQueryState.detail = {
      id: "custom",
      name: "Custom",
      label: "Custom style",
      type: "custom",
      preview_url: previewUrl,
    };
    const Component = Route.options.component as ComponentType;
    render(
      <QueryClientProvider client={new QueryClient()}>
        <I18nextProvider i18n={i18n}><Component /></I18nextProvider>
      </QueryClientProvider>,
    );

    const images = await screen.findAllByRole("img");
    expect(images.filter((image) => image.getAttribute("src") === previewUrl)).toHaveLength(2);
  });
});
