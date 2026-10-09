import { useState, type ChangeEvent, type ReactNode } from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { TextAnnotationNode } from "@/features/canvas/nodes/TextAnnotationNode";
import type { TextAnnotationNodeData } from "@/features/canvas/domain/canvasNodes";

const mocks = vi.hoisted(() => {
  const updateNodeData = vi.fn();
  const canvasState = {
    updateNodeData,
    setSelectedNode: vi.fn(),
    deleteEdge: vi.fn(),
    addNode: vi.fn(),
    addEdge: vi.fn(),
    duplicateNodeAsSibling: vi.fn(),
    findNodePosition: vi.fn(),
    nodes: [],
    edges: [],
  };
  return {
    canvasState,
    updateNodeData,
    submitFreezoneTextPrepare: vi.fn(),
    fetchFreezoneTextPrepareResult: vi.fn(),
    awaitTaskCompletion: vi.fn(),
  };
});

vi.mock("@xyflow/react", async () => {
  const actual =
    await vi.importActual<typeof import("@xyflow/react")>("@xyflow/react");
  return {
    ...actual,
    Handle: () => <div data-testid="handle" />,
    useReactFlow: () => ({
      getNode: () => ({ measured: { width: 440, height: 220 }, position: { x: 0, y: 0 } }),
      getInternalNode: () => ({ internals: { positionAbsolute: { x: 0, y: 0 } } }),
      setCenter: vi.fn(),
    }),
  };
});

vi.mock("@/stores/canvasStore", () => {
  const useCanvasStore = (selector: (state: typeof mocks.canvasState) => unknown) =>
    selector(mocks.canvasState);
  useCanvasStore.getState = () => mocks.canvasState;
  return {
    useCanvasStore,
    useIsBoxSelecting: () => false,
  };
});

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => {
      const labels: Record<string, string> = {
        "node.textNode.placeholder": "写下故事、场景或角色灵感。",
        "node.textNode.prepare.action": "整理为脚本素材",
        "node.textNode.prepare.running": "整理中...",
        "node.textNode.prepare.previewTitle": "整理预览",
        "node.textNode.prepare.stale": "原稿已修改",
        "node.textNode.prepare.staleHint": "原稿已变化，这份整理结果已过期。",
        "node.textNode.prepare.changeSummary": "主要整理",
        "node.textNode.prepare.unresolved": "仍需确认",
        "node.textNode.prepare.discard": "放弃这次整理",
        "node.textNode.prepare.regenerate": "重新整理",
        "node.textNode.prepare.apply": "采用整理结果",
        "node.textNode.prepare.modelAria": "故事素材整理模型",
        "node.textNode.prepare.modeAria": "故事素材整理模式",
        "node.textNode.prepare.mode.faithful": "忠实整理",
        "node.textNode.prepare.mode.creative": "剧本补全",
        "node.textNode.translate": "翻译",
      };
      return labels[key] ?? key;
    },
  }),
}));

vi.mock("@/api/freezoneTextPrepare", () => ({
  submitFreezoneTextPrepare: mocks.submitFreezoneTextPrepare,
  fetchFreezoneTextPrepareResult: mocks.fetchFreezoneTextPrepareResult,
}));

vi.mock("@/api/tasks", () => ({
  awaitTaskCompletion: mocks.awaitTaskCompletion,
}));

vi.mock("@/lib/url-params", () => ({
  readUrl: () => ({ project: "demo", canvas: "canvas-a" }),
}));

vi.mock("@/features/canvas/hooks/useDirectModelCatalog", () => ({
  useDirectModelCatalog: () => ({
    models: [{ catalogId: "direct/text-primary", label: "Text Primary", modelId: "text-primary" }],
    defaultModel: null,
    isLoading: false,
  }),
  resolveDirectCanvasModelId: (value: string) => value || "direct/text-primary",
}));

vi.mock("@/features/canvas/hooks/useFreezoneVideoModels", () => ({
  useFreezoneVideoModels: () => ({ models: [] }),
}));

vi.mock("@/features/canvas/application/useNodeGenerationTaskState", () => ({
  useNodeGenerationTaskState: () => ({ isGenerating: false, task: null }),
}));

vi.mock("@/features/canvas/application/useCancelNodeGeneration", () => ({
  useCancelNodeGeneration: () => ({
    cancel: vi.fn(),
    isCancelling: false,
    canCancel: false,
  }),
}));

vi.mock("@/lib/queries/generation-credit-cost", () => ({
  useGenerationCreditCost: () => ({ data: null }),
}));

vi.mock("@/components/credit-cost-inline", () => ({
  CreditCostInline: () => null,
}));

vi.mock("@/components/ui", () => ({
  UiSelect: ({
    children,
    value,
    onChange,
    "aria-label": ariaLabel,
  }: {
    children: ReactNode;
    value: string;
    onChange: (event: ChangeEvent<HTMLSelectElement>) => void;
    "aria-label"?: string;
  }) => (
    <select aria-label={ariaLabel} value={value} onChange={onChange}>
      {children}
    </select>
  ),
}));

vi.mock("@/features/canvas/ui/NodeHeader", () => ({
  NODE_HEADER_FLOATING_POSITION_CLASS: "",
  NodeHeader: ({ titleText }: { titleText: string }) => <div>{titleText}</div>,
}));

vi.mock("@/features/canvas/ui/NodeResizeHandle", () => ({
  NodeResizeHandle: () => <div data-testid="resize-handle" />,
}));

vi.mock("@/features/canvas/ui/NodeGenerationOverlay", () => ({
  NodeGenerationOverlay: () => null,
}));

vi.mock("@/features/canvas/ui/ProviderModelPicker", () => ({
  DEFAULT_SHARED_MODEL_ID: "default",
  DEFAULT_VIDEO_MODEL_ID: "default-video",
  ProviderModelPicker: () => null,
}));

vi.mock("@/features/canvas/ui/DirectModelPicker", () => ({
  DirectModelPicker: () => <div data-testid="direct-model-picker" />,
}));

function makeData(
  overrides: Partial<TextAnnotationNodeData> = {},
): TextAnnotationNodeData {
  return {
    displayName: "文本",
    content: "旧车站里，一封信让两个人重新见面。",
    model: "direct/text-primary",
    mode: "writing",
    isGenerating: false,
    ...overrides,
  };
}

function Harness({ initialData }: { initialData: TextAnnotationNodeData }) {
  const [data, setData] = useState(initialData);
  mocks.updateNodeData.mockImplementation((_id: string, patch: Partial<TextAnnotationNodeData>) => {
    setData((current) => ({ ...current, ...patch }));
  });
  return (
    <TextAnnotationNode
      id="text-a"
      type="textAnnotationNode"
      data={data}
      selected
      dragging={false}
      draggable
      selectable
      deletable
      zIndex={0}
      isConnectable
      positionAbsoluteX={0}
      positionAbsoluteY={0}
    />
  );
}

describe("TextAnnotationNode story preparation", () => {
  beforeEach(() => {
    mocks.updateNodeData.mockReset();
    mocks.submitFreezoneTextPrepare.mockReset();
    mocks.fetchFreezoneTextPrepareResult.mockReset();
    mocks.awaitTaskCompletion.mockReset();
  });

  it("previews prepared text and only writes content after explicit apply", async () => {
    const user = userEvent.setup();
    mocks.submitFreezoneTextPrepare.mockResolvedValue({
      task_type: "freezone_text_prepare",
      job_id: "job-1",
      task_key: "task:freezone_text_prepare:demo:0:job-1",
    });
    mocks.awaitTaskCompletion.mockResolvedValue({ status: "completed", result: {} });
    mocks.fetchFreezoneTextPrepareResult.mockResolvedValue({
      prepared_text: "场景 1\n旧车站的雨声压低了两个人的脚步。",
      source_text: "旧车站里，一封信让两个人重新见面。",
      source_hash: "a".repeat(64),
      mode: "faithful",
      model: "direct/text-primary",
      change_summary: ["补全了场景动作"],
      unresolved: [],
      warnings: [],
    });

    render(<Harness initialData={makeData()} />);

    await user.click(screen.getByRole("button", { name: "整理为脚本素材" }));
    expect(mocks.submitFreezoneTextPrepare).toHaveBeenCalledWith(
      "demo",
      expect.objectContaining({
        text: "旧车站里，一封信让两个人重新见面。",
        mode: "faithful",
        nodeId: "text-a",
      }),
    );
    expect(await screen.findByText("整理预览")).toBeInTheDocument();
    expect(screen.getByText(/旧车站的雨声压低/)).toBeInTheDocument();
    expect(mocks.updateNodeData).not.toHaveBeenCalledWith(
      "text-a",
      expect.objectContaining({ content: expect.stringContaining("场景 1") }),
    );

    await user.click(screen.getByRole("button", { name: "采用整理结果" }));
    await waitFor(() => {
      expect(mocks.updateNodeData).toHaveBeenCalledWith(
        "text-a",
        expect.objectContaining({
          content: "场景 1\n旧车站的雨声压低了两个人的脚步。",
          textPreparePreview: null,
        }),
      );
    });
  });

  it("marks the result stale after the source is edited", () => {
    render(
      <Harness
        initialData={makeData({
          content: "旧车站里，一封信让两个人重新见面。补充。",
          textPreparePreview: {
            preparedText: "场景 1\n旧车站的雨声压低了两个人的脚步。",
            sourceText: "旧车站里，一封信让两个人重新见面。",
            sourceHash: "a".repeat(64),
            mode: "faithful",
            model: "direct/text-primary",
            jobId: "job-1",
            taskKey: "task:freezone_text_prepare:demo:0:job-1",
            changeSummary: [],
            unresolved: [],
            warnings: [],
            createdAt: 1,
          },
        })}
      />,
    );

    expect(screen.getByText("原稿已修改")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "采用整理结果" })).toBeDisabled();
  });
});
