import type {
  DirectModelConfig,
  DirectModelInput,
  DirectModelKind,
  DirectModelProtocol,
} from "@/lib/queries/model-gateway";

/** Model-center families. agent/text/vision are chat aliases kept for old bindings. */
export type LocalDirectModelKind = "chat" | "image" | "embedding";
export type DirectModelFamilyKind = LocalDirectModelKind | DirectModelKind;

/** The retired agent/text/vision families all describe the same chat model. */
export function isChatFamilyKind(kind: DirectModelFamilyKind): boolean {
  return kind === "chat" || kind === "agent" || kind === "text" || kind === "vision";
}

export interface LocalDirectModelInput {
  id?: string;
  label: string;
  modelId: string;
  baseUrl: string;
  apiKey?: string;
  protocol?: DirectModelProtocol | string;
  enabled: boolean;
  isDefault?: boolean;
  configured?: boolean;
  apiKeyPreview?: string;
  protocolLabel?: string;
  runtimeReady?: boolean;
  /** Server-side verdict (`usable`); the UI renders it instead of re-deriving. */
  usable?: boolean;
  runtimeProbeRequired?: boolean;
  runtimeProbeComplete?: boolean;
  probeContractVersion?: number;
  disabledReason?: string;
  catalogVerification?: DirectModelConfig["catalogVerification"];
  verificationStatus?: DirectModelConfig["verificationStatus"];
  lastProbe?: "ok" | "failed" | "unknown";
  lastProbeMessage?: string;
  catalogMissing?: boolean;
  supportsTools?: boolean;
  supportsVision?: boolean;
  toolProbeStatus?: string;
  toolCallingVerified?: boolean;
  visionProbeStatus?: string;
  supportedModes?: string[];
}

export type ModelConnectionTone = "ready" | "warning" | "danger" | "neutral";

export const DIRECT_MODEL_PROTOCOL_OPTIONS: Array<{
  value: DirectModelProtocol;
  label: string;
}> = [
  { value: "auto", label: "自动识别" },
  { value: "openai-compatible", label: "OpenAI 兼容" },
  { value: "anthropic-messages", label: "Anthropic Messages" },
  { value: "gemini", label: "Google Gemini" },
  { value: "gemini-image", label: "Gemini 图像" },
  { value: "ollama-openai", label: "Ollama OpenAI" },
  { value: "openai-images", label: "OpenAI Images" },
  { value: "openai-embeddings", label: "OpenAI Embeddings" },
  { value: "custom-http", label: "Custom HTTP" },
];

const DIRECT_MODEL_PROTOCOL_LABELS = Object.fromEntries(
  DIRECT_MODEL_PROTOCOL_OPTIONS.map((item) => [item.value, item.label]),
) as Record<DirectModelProtocol, string>;

export const LOCAL_DIRECT_MODEL_META: Record<
  LocalDirectModelKind,
  { title: string; empty: string; storageKey: string; placeholder: string }
> = {
  chat: {
    title: "直连对话模型",
    empty: "暂无对话模型。添加后对话、提示词、脚本、识图与 Agent 都从这里取模型。",
    storageKey: "village_canvas_direct_chat_models",
    placeholder: "例如：Gemini 3.8 Flash / DeepSeek V4 / GPT-5 / Claude",
  },
  image: {
    title: "直连生图模型",
    empty: "暂无生图模型。添加后填写图片生成或图片编辑模型。",
    storageKey: "village_canvas_direct_image_models",
    placeholder: "例如：Nano Banana / GPT Image / 即梦图片",
  },
  embedding: {
    title: "直连向量模型",
    empty: "暂无向量模型。添加后填写 embedding 模型。",
    storageKey: "village_canvas_direct_embedding_models",
    placeholder: "例如：text-embedding / bge / jina embedding",
  },
};

export function normalizeVillageDirectModelProtocol(value?: string): DirectModelProtocol {
  const clean = (value ?? "").trim().toLowerCase().replace(/_/g, "-");
  const aliases: Record<string, DirectModelProtocol> = {
    "": "auto",
    openai: "openai-compatible",
    "openai-chat": "openai-compatible",
    "openai-chat-vision": "openai-compatible",
    ollama: "ollama-openai",
    anthropic: "anthropic-messages",
    claude: "anthropic-messages",
    google: "gemini",
    "google-gemini": "gemini",
    "gemini-images": "gemini-image",
    "google-gemini-image": "gemini-image",
    custom: "custom-http",
  };
  const normalized = aliases[clean] ?? clean;
  return DIRECT_MODEL_PROTOCOL_OPTIONS.some((item) => item.value === normalized)
    ? (normalized as DirectModelProtocol)
    : "auto";
}

export function inferVillageDirectModelProtocol(
  kind: DirectModelFamilyKind,
  baseUrl: string,
  modelId: string,
  requestedProtocol?: string,
): DirectModelProtocol {
  const requested = normalizeVillageDirectModelProtocol(requestedProtocol);
  const endpoint = `${baseUrl} ${modelId}`.trim().toLowerCase();
  const model = modelId.trim().toLowerCase();
  const urlPath = baseUrl.trim().toLowerCase().replace(/\/+$/, "");
  const officialGemini = endpoint.includes("generativelanguage.googleapis.com") || endpoint.includes("googleapis.com");
  const openAiGateway = urlPath.endsWith("/v1") || urlPath.includes("/v1/") || ["aiwble", "openai", "openrouter", "newapi", "oneapi", "opencode", "wokey", "siliconflow", "deepseek"].some((token) => endpoint.includes(token));
  if (requested === "gemini" && !officialGemini && openAiGateway && isChatFamilyKind(kind) && model.startsWith("gemini")) {
    return "openai-compatible";
  }
  if (requested !== "auto") return requested;
  if (endpoint.includes("localhost:11434") || endpoint.includes("127.0.0.1:11434") || endpoint.includes("ollama")) return "ollama-openai";
  if (endpoint.includes("anthropic") || model.startsWith("claude")) return "anthropic-messages";
  if (kind === "image" && (model.includes("nano-banana") || (model.includes("gemini") && model.includes("image")))) return "gemini-image";
  if (officialGemini) return "gemini";
  if (kind === "image") return "openai-images";
  if (kind === "embedding") return "openai-embeddings";
  if (urlPath.endsWith("/v1") || urlPath.includes("/v1/")) return "openai-compatible";
  if (["aiwble", "openai", "openrouter", "newapi", "oneapi", "opencode", "wokey", "siliconflow", "deepseek"].some((token) => endpoint.includes(token))) return "openai-compatible";
  if (isChatFamilyKind(kind) && model.startsWith("gemini")) return "openai-compatible";
  if (isChatFamilyKind(kind)) return "openai-compatible";
  return "custom-http";
}

export function villageDirectModelProtocolLabel(protocol?: string): string {
  return DIRECT_MODEL_PROTOCOL_LABELS[normalizeVillageDirectModelProtocol(protocol)];
}

export function villageDirectModelRuntimeReady(
  kind: DirectModelFamilyKind,
  protocol?: string,
): boolean {
  const normalized = normalizeVillageDirectModelProtocol(protocol);
  if (kind === "chat" || kind === "text" || kind === "vision") {
    return normalized === "openai-compatible" || normalized === "ollama-openai" || normalized === "anthropic-messages" || normalized === "gemini";
  }
  if (kind === "agent") return normalized === "openai-compatible" || normalized === "ollama-openai";
  if (kind === "image") return normalized === "openai-images" || normalized === "gemini-image";
  if (kind === "embedding") return normalized === "openai-embeddings";
  return false;
}

/**
 * State the provenance of the parameter numbers without judging the link.
 *
 * The dot answers "can I use this model?"; this sentence answers "where did
 * the numbers come from?".  A relay `/models` response carries no capability
 * fields, so the numbers are often local presets, and the wording has to say
 * so — but that is a caveat about the preset, not evidence that the
 * connection is failing.
 */
function capabilitySourceDetail(source: "upstream" | "model-name" | "local"): string {
  if (source === "upstream") return "能力合同已验证，节点会按该模型声明的能力显示参数。";
  if (source === "model-name") return "连接已确认；能力按模型名中的声明推断，节点按该推断显示参数。";
  return "连接已确认；当前使用的是本地预设能力，上游未声明参数。";
}

export function modelConnectionSummary(input: {
  configured?: boolean;
  enabled: boolean;
  lastProbe?: "ok" | "failed" | "unknown";
  credentialValidated?: boolean;
  catalogMissing?: boolean;
  runtimeReady?: boolean;
  /** Server-side verdict (`usable`): the model is verified and can run now. */
  usable?: boolean;
  runtimeProbeRequired?: boolean;
  runtimeProbeComplete?: boolean;
  verificationStatus?: DirectModelConfig["verificationStatus"];
  message?: string;
  /**
   * Where the displayed capability contract came from.
   *
   * `upstream`  — the relay declared it (typed capability fields or a
   *               connection probe that resolved a contract);
   * `model-name` — parsed out of the model name (for example ``10图``);
   * `local`     — the built-in family profile for this model id.
   *
   * This changes the sentence under the dot, never its colour: a model the
   * runtime has verified is green even when its numbers are local presets.
   */
  capabilitySource?: "upstream" | "model-name" | "local";
}): { label: string; detail: string; tone: ModelConnectionTone } {
  if (!input.enabled) {
    return { label: "已停用", detail: input.message || "启用后才会出现在节点和 Agent 中。", tone: "neutral" };
  }
  if (input.lastProbe === "failed") return { label: "连接失败", detail: input.message || "检查 URL、Key、模型 ID 或接口协议。", tone: "danger" };
  if (input.lastProbe === "ok" && input.credentialValidated === false) {
    return {
      label: "视频鉴权待确认",
      detail: input.message || "模型目录可读，但视频接口尚未接受该 Key。",
      tone: "warning",
    };
  }
  if (input.catalogMissing === true && (input.runtimeReady === true || input.usable === true)) {
    return {
      label: "连接可用",
      detail: input.message || "上游目录未列出该模型 ID，但实测调用已通过（预览/实验模型常见）。",
      tone: "ready",
    };
  }
  if (
    input.lastProbe === "ok"
    && input.runtimeProbeRequired
    && input.runtimeProbeComplete === false
  ) {
    return {
      label: "目录已匹配，待运行验证",
      detail: input.message || "目录与协议已匹配；首次真实调用成功后才升级为运行态验证。",
      tone: "warning",
    };
  }
  if (
    input.runtimeProbeRequired
    && input.runtimeProbeComplete === false
    && input.lastProbe !== "ok"
  ) {
    return {
      label: "需重新检测",
      detail: input.message || "当前缓存没有完整运行合同，请重新检测连接。",
      tone: "warning",
    };
  }
  if (
    input.lastProbe === "ok"
    && input.verificationStatus === "contract-resolved"
    && input.runtimeReady !== false
    && input.usable !== true
  ) {
    return {
      label: "合同已解析",
      detail: input.message || "已匹配视频执行合同；真实生成 smoke 通过后才标记为运行态验证。",
      tone: "warning",
    };
  }
  // Green covers everything the runtime can actually run, whether the server
  // marked it usable or this row only carries the client-side probe verdict.
  // Capability provenance only changes the sentence, never the colour.
  if (input.usable === true || (input.lastProbe === "ok" && input.runtimeReady !== false)) {
    return {
      label: "连接可用",
      detail: input.message
        || (input.capabilitySource
          ? capabilitySourceDetail(input.capabilitySource)
          : "连接已确认，可在对应节点中使用。"),
      tone: "ready",
    };
  }
  if (input.verificationStatus === "catalog-mismatch") return { label: "模型 ID 不匹配", detail: input.message || "上游目录可达，但没有这个模型 ID。", tone: "danger" };
  if (input.runtimeReady === false || input.verificationStatus === "degraded") {
    const needsProbe = input.verificationStatus === "unverified" || input.verificationStatus === "directory-only";
    return { label: needsProbe ? "待检测" : "协议待适配", detail: input.message || (needsProbe ? "先检测连接，确认模型 ID 与上游目录。" : "模型已经记录，但当前执行器还未匹配该协议。"), tone: "warning" };
  }
  if (input.configured && input.enabled && input.runtimeReady === true) {
    return {
      label: "连接可用",
      detail: input.message
        || (input.capabilitySource
          ? capabilitySourceDetail(input.capabilitySource)
          : "连接已确认，可在对应节点中使用。"),
      tone: "ready",
    };
  }
  if (input.configured) return { label: input.enabled ? "已保存，待检测" : "已停用", detail: input.enabled ? "点击检测连接，确认模型 ID 与接口能力。" : "启用后才会出现在节点和 Agent 中。", tone: "neutral" };
  return { label: "待配置", detail: "依次填写名称、模型 ID、Base URL 和 API Key。", tone: "neutral" };
}

export function modelConnectionErrorMessage(error: unknown): string {
  const message = error instanceof Error ? error.message : String(error ?? "");
  const normalized = message.toLowerCase();
  if (/\b401\b|unauthorized|invalid api key/.test(normalized)) return "API Key 校验失败，请重新填写后检测。";
  if (/\b403\b|forbidden/.test(normalized)) return "渠道拒绝访问，请检查模型权限或接口协议。";
  if (/\b404\b|not found/.test(normalized)) return "接口路径或模型 ID 不存在，请核对 URL 和模型 ID。";
  if (/\b429\b|rate limit|too many requests/.test(normalized)) return "渠道请求过多或额度不足，请稍后再检测。";
  if (/timeout|timed out/.test(normalized)) return "连接超时，请检查接口地址或网络状态。";
  if (/failed to fetch|network|dns|connection refused/.test(normalized)) return "接口暂时不可达，请检查 Base URL 和网络状态。";
  const trimmed = message.trim();
  return trimmed.length > 160 ? `${trimmed.slice(0, 157)}...` : trimmed || "检测失败";
}

export function newLocalDirectModel(kind?: LocalDirectModelKind): LocalDirectModelInput {
  const chat = kind === undefined || kind === "chat";
  return { label: "", modelId: "", baseUrl: "", apiKey: "", protocol: "auto", enabled: true, lastProbe: "unknown", supportsTools: chat ? true : undefined, supportsVision: chat ? true : undefined };
}

export function localDirectModelId(kind: LocalDirectModelKind): string {
  return `${kind}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

export function completeLocalDirectModel(model: LocalDirectModelInput): boolean {
  return Boolean(model.label.trim() && model.modelId.trim() && model.baseUrl.trim() && ((model.apiKey ?? "").trim() || model.configured));
}

export function normalizeLocalDirectModels(
  kind: LocalDirectModelKind,
  models: LocalDirectModelInput[],
): LocalDirectModelInput[] {
  const normalized = models.map((model) => ({ ...model, id: model.id || localDirectModelId(kind), label: model.label.trim(), modelId: model.modelId.trim(), baseUrl: model.baseUrl.trim().replace(/\/+$/, ""), apiKey: (model.apiKey ?? "").trim(), protocol: normalizeVillageDirectModelProtocol(model.protocol), enabled: Boolean(model.enabled), isDefault: Boolean(model.isDefault), configured: Boolean(model.configured), supportsTools: kind === "chat" ? model.supportsTools !== false : undefined, supportsVision: kind === "chat" ? model.supportsVision !== false : undefined, apiKeyPreview: model.apiKeyPreview ?? "", lastProbe: model.lastProbe ?? "unknown", lastProbeMessage: model.lastProbeMessage ?? "", supportedModes: undefined })).filter((model) => model.label || model.modelId || model.baseUrl || model.apiKey);
  const firstDefault = normalized.findIndex((model) => model.isDefault);
  return normalized.map((model, index) => ({ ...model, isDefault: firstDefault >= 0 ? index === firstDefault : index === 0 && model.enabled && completeLocalDirectModel(model) }));
}

export function readLocalDirectModels(kind: LocalDirectModelKind): LocalDirectModelInput[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(LOCAL_DIRECT_MODEL_META[kind].storageKey);
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object").map((item) => ({ id: typeof item.id === "string" ? item.id : undefined, label: typeof item.label === "string" ? item.label : "", modelId: typeof item.modelId === "string" ? item.modelId : "", baseUrl: typeof item.baseUrl === "string" ? item.baseUrl : "", apiKey: typeof item.apiKey === "string" ? item.apiKey : "", protocol: normalizeVillageDirectModelProtocol(typeof item.protocol === "string" ? item.protocol : "auto"), enabled: typeof item.enabled === "boolean" ? item.enabled : true, isDefault: typeof item.isDefault === "boolean" ? item.isDefault : false, configured: typeof item.configured === "boolean" ? item.configured : false, apiKeyPreview: typeof item.apiKeyPreview === "string" ? item.apiKeyPreview : "", lastProbe: item.lastProbe === "ok" || item.lastProbe === "failed" ? item.lastProbe : "unknown", lastProbeMessage: typeof item.lastProbeMessage === "string" ? item.lastProbeMessage : "", supportsTools: kind === "chat" ? item.supportsTools !== false : undefined, supportsVision: kind === "chat" ? item.supportsVision !== false : undefined, supportedModes: undefined }));
  } catch {
    return [];
  }
}

export function writeLocalDirectModels(kind: LocalDirectModelKind, models: LocalDirectModelInput[]) {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(LOCAL_DIRECT_MODEL_META[kind].storageKey, JSON.stringify(normalizeLocalDirectModels(kind, models.map((model) => ({ ...model, apiKey: "" })))));
}

export function sameDirectModelEndpoint(left: string, right: string): boolean {
  const normalize = (value: string) => {
    const raw = value.trim().replace(/\/+$/, "");
    try {
      const parsed = new URL(raw);
      let pathname = parsed.pathname.replace(/\/+$/, "");
      if (!pathname || pathname === "/") {
        pathname = parsed.hostname.includes("generativelanguage.googleapis.com")
          ? "/v1beta"
          : "/v1";
      }
      return `${parsed.protocol.toLowerCase()}//${parsed.host.toLowerCase()}${pathname.toLowerCase()}`;
    } catch {
      return raw.toLowerCase();
    }
  };
  return normalize(left) === normalize(right);
}

export function localModelInputFromSaved(model: DirectModelConfig): LocalDirectModelInput {
  return { id: model.id, label: model.label, modelId: model.modelId, baseUrl: model.baseUrl, apiKey: "", protocol: normalizeVillageDirectModelProtocol(model.requestedProtocol ?? model.protocol), enabled: model.enabled, isDefault: model.isDefault, configured: model.configured, apiKeyPreview: model.apiKeyPreview, protocolLabel: model.protocolLabel, runtimeReady: model.runtimeReady, usable: model.usable, runtimeProbeRequired: model.runtimeProbeRequired, runtimeProbeComplete: model.runtimeProbeComplete, probeContractVersion: model.probeContractVersion, verificationStatus: model.verificationStatus, disabledReason: model.disabledReason, catalogVerification: model.catalogVerification, lastProbe: "unknown", lastProbeMessage: "", catalogMissing: model.catalogMissing, supportsTools: model.supportsTools, supportsVision: model.supportsVision, toolProbeStatus: model.toolProbeStatus, toolCallingVerified: model.toolCallingVerified, visionProbeStatus: model.visionProbeStatus, supportedModes: model.supportedModes };
}

export function directModelSavePayload(models: LocalDirectModelInput[]): DirectModelInput[] {
  return models.map((model) => ({ id: model.id, label: model.label.trim(), modelId: model.modelId.trim(), baseUrl: model.baseUrl.trim(), apiKey: (model.apiKey ?? "").trim(), protocol: normalizeVillageDirectModelProtocol(model.protocol), enabled: Boolean(model.enabled), isDefault: Boolean(model.isDefault), supportedModes: model.supportedModes, supportsTools: model.supportsTools, supportsVision: model.supportsVision }));
}

export function directModelsSnapshotKey(kind: LocalDirectModelKind, models: DirectModelConfig[]): string {
  return `${kind}:${models.map((model) => [model.id, model.label, model.modelId, model.baseUrl, normalizeVillageDirectModelProtocol(model.requestedProtocol ?? model.protocol), model.enabled ? "1" : "0", model.isDefault ? "1" : "0", model.configured ? "1" : "0", model.apiKeyPreview, model.catalogMissing === true ? "1" : "0", model.supportsTools === false ? "0" : "1", model.supportsVision === false ? "0" : "1", (model.supportedModes ?? []).join(",")].join("\u001f")).join("\u001e")}`;
}
