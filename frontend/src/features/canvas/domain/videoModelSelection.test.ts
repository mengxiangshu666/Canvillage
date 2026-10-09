import { describe, expect, it } from "vitest";

import type { ModelOption } from "@/features/canvas/ui/ProviderModelPicker";

import {
  imageEditModelDisabledReason,
  imageEditModelSupportsMode,
  findPersistedModel,
  modelResolutionOptions,
  persistedCanvasModelId,
  resolveModelResolution,
  selectLiveImageEditModel,
  selectVideoModel,
} from "./videoModelSelection";

function model(
  id: string,
  overrides: Partial<ModelOption> = {},
): ModelOption {
  return {
    id,
    providerId: "direct",
    apiModel: id,
    label: id,
    ...overrides,
  };
}

describe("selectVideoModel", () => {
  it("keeps the node-persisted model", () => {
    const models = [model("first"), model("default", { isDefault: true })];
    expect(selectVideoModel(models, "first")?.id).toBe("first");
  });

  it("uses the live model-center default for a new node", () => {
    const models = [model("first"), model("default", { isDefault: true })];
    expect(selectVideoModel(models, null)?.id).toBe("default");
  });

  it("skips a disabled default and uses the first live model", () => {
    const models = [
      model("offline-default", { isDefault: true, disabled: true }),
      model("live"),
    ];
    expect(selectVideoModel(models, null)?.id).toBe("live");
  });

  it("does not replace a disabled persisted model with an unrelated default", () => {
    const models = [
      model("disabled", { disabled: true }),
      model("live", { isDefault: true }),
    ];
    expect(selectVideoModel(models, "disabled")).toBeUndefined();
    expect(findPersistedModel(models, "disabled")?.supportedModes).toEqual(undefined);
  });

  it("retains a disabled model contract for presentation and diagnostics", () => {
    const disabled = model("disabled", {
      disabled: true,
      supportedModes: [],
      resolutionOptions: [],
    });
    expect(selectVideoModel([disabled], "disabled")).toBeUndefined();
    expect(findPersistedModel([disabled], "disabled")).toBe(disabled);
  });

  it("does not replace a missing persisted model with an unrelated default", () => {
    const models = [model("live", { isDefault: true })];
    expect(selectVideoModel(models, "removed")).toBeUndefined();
  });
});

describe("model resolution contract", () => {
  it("keeps explicit upstream options instead of resurrecting presets", () => {
    const configured = model("image", { resolutionOptions: ["768x1024"] });
    expect(modelResolutionOptions(configured)).toEqual(["768x1024"]);
    expect(resolveModelResolution(configured, "2K")).toBe("768x1024");
  });

  it("keeps an explicit empty capability list empty", () => {
    const configured = model("image", { resolutionOptions: [] });
    expect(modelResolutionOptions(configured)).toEqual([]);
    expect(resolveModelResolution(configured)).toBeUndefined();
  });

  it("uses legacy presets only for catalogs without capability metadata", () => {
    const legacy = model("legacy");
    expect(modelResolutionOptions(legacy)).toEqual(["1K", "2K", "4K"]);
    expect(resolveModelResolution(legacy, "2K")).toBe("2K");
  });
});

describe("image edit model contract", () => {
  it("selects an image-to-image capable default instead of a text-only default", () => {
    const models = [
      model("text-only", { isDefault: true, supportedModes: ["textToImage"] }),
      model("edit", { supportedModes: ["textToImage", "imageToImage"] }),
    ];
    expect(selectLiveImageEditModel(models, null)?.id).toBe("edit");
  });

  it("preserves an explicitly selected edit model and rejects text-only options", () => {
    const textOnly = model("text-only", { supportedModes: ["textToImage"] });
    const edit = model("edit", { supportedModes: ["image_to_image"] });
    expect(selectLiveImageEditModel([textOnly, edit], "edit")?.id).toBe("edit");
    expect(imageEditModelSupportsMode(textOnly)).toBe(false);
    expect(imageEditModelDisabledReason(textOnly)).toContain("图生图");
  });

  it("matches legacy api and upstream ids when resolving a persisted model", () => {
    const configured = model("direct/catalog-image", {
      apiModel: "direct/catalog-image",
      upstreamModel: "provider/image-edit-v2",
      supportedModes: ["imageToImage"],
    });
    expect(selectLiveImageEditModel([configured], "provider/image-edit-v2")?.id)
      .toBe("direct/catalog-image");
    expect(selectLiveImageEditModel([configured], "direct/catalog-image")?.id)
      .toBe("direct/catalog-image");
  });

  it("reads the canonical model reference from old and new node payloads", () => {
    expect(persistedCanvasModelId({ model: "direct/image" })).toBe("direct/image");
    expect(persistedCanvasModelId({ generationModelId: "direct/image" })).toBe("direct/image");
    expect(persistedCanvasModelId({ generationModel: "provider/image-v2" })).toBe("provider/image-v2");
    expect(persistedCanvasModelId({})).toBeNull();
  });

  it("keeps legacy models without mode metadata compatible", () => {
    expect(imageEditModelSupportsMode(model("legacy"))).toBe(true);
  });

  it("does not treat an explicitly unknown capability contract as editable", () => {
    const unknown = model("unknown", { capabilitySource: "unknown" });
    expect(imageEditModelSupportsMode(unknown)).toBe(false);
    expect(imageEditModelDisabledReason(unknown)).toContain("尚未确认");
    expect(selectLiveImageEditModel([unknown], null)).toBeUndefined();
  });
});
