// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { queryKeys } from "@/lib/query-keys";
import type { ErrorResponse, OkResponse } from "@/types/api";

export const DIRECT_MODEL_REGISTRY_CHANGED_EVENT = "village:direct-model-registry-changed";

export type GatewayMode = "official" | "custom" | "unified";

/** 通用的「端点预览」：服务端只回 key 预览，绝不回完整 key。 */
export interface GatewayEndpointPreview {
  baseUrl: string;
  apiKeyPreview: string;
  configured: boolean;
}

export interface OfficialGatewayConfig extends GatewayEndpointPreview {
  /** "database" | "env" 等，标识官方配置来源。 */
  source: string;
  /** .env 里的官方默认（回退用）。 */
  environment: GatewayEndpointPreview;
}

/** The only operator-facing model route: the configured Village Canvas gateway. */
export interface UnifiedGatewayConfig extends OfficialGatewayConfig {}

export interface CustomGatewayConfig extends GatewayEndpointPreview {
  adminBaseUrl: string;
  tokenName: string;
  tokenId: string;
}

export interface EffectiveGatewayConfig {
  /** "official" | "custom"。 */
  source: string;
  baseUrl: string;
  apiKeyPreview: string;
  configured: boolean;
}

export interface NewApiDatabaseStatus {
  configured: boolean;
  available?: boolean;
  source: string;
  databaseType?: "sqlite" | "external";
}

export interface SavedProviderChannelConfig {
  provider: string;
  configured: boolean;
  upstreamKeyPreview: string;
  baseUrl: string;
}

export interface SavedMediaModelConfig {
  provider: string;
  upstreamModel: string;
}

export interface SavedEmbeddingModelConfig {
  provider: string;
  upstreamModel: string;
  dimension: number;
  batchSize?: number;
  sendDimensions?: boolean;
  internalModel?: string;
}

export interface ModelGatewayProvisionerConfig {
  enabled: boolean;
  adminBaseUrl: string;
  dbConfigured: boolean;
  adminUsername: string;
  relayTokenName: string;
  relayBaseUrl: string;
  providers: Record<string, { label: string; type: number; base_url: string }>;
  providerChannels?: SavedProviderChannelConfig[];
  mediaModels?: Record<string, SavedMediaModelConfig>;
  embeddingModel?: SavedEmbeddingModelConfig;
  database?: NewApiDatabaseStatus;
}

export interface MediaRelayConfig {
  source: string;
  provider: string;
  ttlSeconds: number;
  endpoint: string;
  bucket: string;
  accessKeyIdPreview: string;
  accessKeySecretPreview: string;
  cloudName?: string;
  cloudinaryApiKeyPreview?: string;
  cloudinaryApiSecretPreview?: string;
  apiFolder?: string;
  configurationError?: string;
  configured: boolean;
}

export interface DirectVideoModelConfig {
  id: string;
  label: string;
  modelId: string;
  baseUrl: string;
  enabled: boolean;
  isDefault: boolean;
  configured: boolean;
  apiKeyPreview: string;
  protocol: string;
  requestedProtocol?: string;
  protocolLabel?: string;
  runtimeReady?: boolean;
  runtimeProbeRequired?: boolean;
  runtimeProbeComplete?: boolean;
  probeContractVersion?: number;
  disabled?: boolean;
  disabledReason?: string;
  catalogVerification?:
    | "unverified"
    | "catalog-mismatch"
    | "catalog-confirmed"
    | "runtime-verified";
  verificationStatus?:
    | "unverified"
    | "catalog-mismatch"
    | "catalog-confirmed"
    | "directory-only"
    | "metadata"
    | "contract-resolved"
    | "runtime-verified"
    | "degraded";
  /** 最后一次**真实检测**的时间；有值也代表这是缓存读数，不是本次点击的结果。 */
  lastCheckedAt?: string;
  credentialValidation?: {
    status?: "accepted" | "rejected" | "permission_denied" | "unverified";
    httpStatus?: number | null;
  };
  /**
   * 出线合同从哪来：`channel` 渠道自带（随渠道增删）、`seed` 源码种子（未验证）、
   * `default` 通用兜底。配 `wireContractSourceLabel` 直接显示。
   */
  wireContractSource?: "channel" | "seed" | "default";
  wireContractSourceLabel?: string;
  wireContractProfileId?: string;
  wireContractEvidence?: string;
  wireContractVerified?: boolean;
  wireContractEvidenceAt?: string;
  wireContractSeedProfileId?: string;
  wireContractConflictsWithSeed?: string[];
  wireContractError?: string;
  detectedProtocol?: string;
  modelFound?: boolean;
  discoveredModelCount?: number;
  modelMetadata?: Record<string, unknown>;
  supportedProtocols?: string[];
  adapterFamily?: string;
  adapterConfidence?: number;
  adapterEvidence?: {
    family?: string;
    confidence?: number;
    sources?: string[];
    matchedPaths?: string[];
    resolved?: boolean;
  };
  openapiUrl?: string;
  openapiPaths?: string[];
  openapiOperations?: Array<{
    path?: string;
    method?: string;
    operationId?: string;
    requestContentTypes?: string[];
    responseContentTypes?: string[];
    tags?: string[];
  }>;
  family?: string;
  supportedModes?: string[];
  referenceLimits?: Record<string, Record<string, number>>;
  audioInputSemantics?: Array<
    "driving_audio" | "voice_profile" | "soundtrack" | "audio_prompt"
  >;
  audio_input_semantics?: Array<
    "driving_audio" | "voice_profile" | "soundtrack" | "audio_prompt"
  >;
  parameterDefaults?: Record<string, string | number | boolean>;
  resolutionOptions?: string[];
  advertisedResolutionOptions?: string[];
  runtimeResolutionOptions?: string[];
  runtimeRejectedResolutionOptions?: string[];
  runtimeCapabilityStatus?: "verified" | "degraded" | "unknown";
  runtimeCapabilityNote?: string;
  verificationStage?: "unknown" | "catalog" | "contract" | "submit" | "poll" | "artifact";
  promptRules?: {
    durationConsistency?: boolean;
    referenceConsistency?: boolean;
  };
  failureGracePolls?: number;
  sizeOptions?: string[];
  aspectRatioOptions?: string[];
  nativeAudio?: "unsupported" | "optional" | "required";
  minDuration?: number;
  maxDuration?: number;
  useCase?: string;
  priceHint?: string;
  recommendation?: string;
}

/** Canonical local families plus the retired agent/text/vision chat aliases. */
export type DirectModelKind = "chat" | "agent" | "text" | "vision" | "image" | "embedding";
export type DirectModelProtocol =
  | "auto"
  | "openai-compatible"
  | "anthropic-messages"
  | "gemini"
  | "gemini-image"
  | "ollama-openai"
  | "openai-images"
  | "openai-embeddings"
  | "custom-http";

/** One locally persisted direct model outside the video registry. */
export interface DirectModelConfig {
  id: string;
  label: string;
  modelId: string;
  baseUrl: string;
  enabled: boolean;
  isDefault: boolean;
  configured: boolean;
  apiKeyPreview: string;
  protocol: DirectModelProtocol | string;
  requestedProtocol?: DirectModelProtocol | string;
  protocolLabel?: string;
  runtimeReady?: boolean;
  runtimeProbeRequired?: boolean;
  runtimeProbeComplete?: boolean;
  probeContractVersion?: number;
  /** Upstream catalog does not list this id, yet the runtime probe passed. */
  catalogMissing?: boolean;
  /** Server-side verdict; the UI renders this instead of re-deriving it. */
  usable?: boolean;
  usableReason?: string;
  /** Registry ids absorbed when the retired chat families merged into chat. */
  aliases?: string[];
  /** Chat rows only: declared tool-calling / image-input capability. */
  supportsTools?: boolean;
  supportsVision?: boolean;
  /** Server-side disabled marker shared by all direct model families. */
  disabled?: boolean;
  disabledReason?: string;
  catalogVerification?:
    | "unverified"
    | "catalog-mismatch"
    | "catalog-confirmed"
    | "runtime-verified";
  verificationStatus?:
    | "unverified"
    | "catalog-mismatch"
    | "catalog-confirmed"
    | "directory-only"
    | "metadata"
    | "contract-resolved"
    | "runtime-verified"
    | "degraded";
  detectedProtocol?: string;
  modelFound?: boolean;
  discoveredModelCount?: number;
  modelMetadata?: Record<string, unknown>;
  responsesProbeStatus?:
    | "native-confirmed"
    | "adapter-selected"
    | "adapter-fallback"
    | "probe-failed"
    | "native-rejected";
  responsesProbeError?: string;
  toolCallingVerified?: boolean;
  visionProbeStatus?: string;
  chatProbeStatus?: string;
  chatHttpStatus?: number;
  chatFirstTokenLatencyMs?: number;
  chatResponseUsable?: boolean;
  chatProbeError?: string;
  streamProbeStatus?: string;
  streamHttpStatus?: number;
  streamFirstEventLatencyMs?: number;
  streamResponseUsable?: boolean;
  streamProbeError?: string;
  toolProbeStatus?: string;
  toolProbeMode?: string;
  toolHttpStatus?: number;
  toolProbeError?: string;
  hermesProbeStatus?: string;
  hermesProbeError?: string;
  embeddingProbeStatus?: string;
  embeddingHttpStatus?: number;
  embeddingResponseUsable?: boolean;
  embeddingProbeLatencyMs?: number;
  embeddingDimensions?: number;
  embeddingProbeError?: string;
  capabilityRevision?: string;
  modelKey?: string;
  modality?: string;
  modeType?: string[];
  inputSlots?: string[];
  referenceLimits?: { images?: number; videos?: number; audio?: number };
  parameterSchema?: Record<string, { default?: unknown; type?: string }>;
  defaults?: Record<string, unknown>;
  fallback?: { strategy?: string; allowSilentModelSwitch?: boolean };
  runtimeProbe?: { required?: boolean; method?: string; credentialFree?: boolean };
  transportContract?: {
    protocol?: string;
    auth?: { type?: string; header?: string; prefix?: string; query_param?: string };
    endpoints?: { catalog_path?: string; invoke_path?: string; edit_path?: string | null };
    input_modalities?: string[];
    output_modalities?: string[];
    runtime_adapter?: string;
    runtime_ready?: boolean;
  };
  supportedModes?: string[];
  useCase?: string;
  parameterDefaults?: Record<string, string | number | boolean>;
}

export interface ModelGatewayConfig {
  mode: GatewayMode;
  effective: EffectiveGatewayConfig;
  unified: UnifiedGatewayConfig;
  official: OfficialGatewayConfig;
  custom: CustomGatewayConfig;
  provisioner?: ModelGatewayProvisionerConfig;
  mediaRelay?: MediaRelayConfig;
  directVideoModels?: DirectVideoModelConfig[];
  directModels?: Partial<Record<DirectModelKind, DirectModelConfig[]>>;
}

export interface SaveOfficialConfigInput {
  newApiApiKey: string;
}

export interface SaveUnifiedConfigInput {
  newApiApiKey: string;
}

export interface DirectVideoModelInput {
  id?: string;
  label: string;
  modelId: string;
  baseUrl: string;
  /** Empty retains the locally stored key for an existing model. */
  apiKey?: string;
  protocol?: string;
  enabled: boolean;
  isDefault?: boolean;
}

export interface SaveDirectVideoModelsInput {
  models: DirectVideoModelInput[];
  confirmClear?: boolean;
}

export interface DirectModelInput {
  id?: string;
  label: string;
  modelId: string;
  baseUrl: string;
  /** Empty retains the locally stored key for an existing model. */
  apiKey?: string;
  protocol?: DirectModelProtocol | string;
  enabled: boolean;
  isDefault?: boolean;
  supportedModes?: string[];
  /** Chat rows only; null/undefined keeps the stored value. */
  supportsTools?: boolean | null;
  supportsVision?: boolean | null;
}

export interface SaveDirectModelsInput {
  kind: DirectModelKind;
  models: DirectModelInput[];
  confirmClear?: boolean;
}

export interface DirectVideoModelProbeResult {
  ok: boolean;
  modelFound: boolean;
  discoveredModelCount: number;
  protocol: string;
  credentialValidation?: {
    status: "accepted" | "rejected" | "permission_denied" | "unverified";
    httpStatus?: number;
  };
  capability?: {
    verificationStage?: "unknown" | "catalog" | "contract" | "submit" | "poll" | "artifact";
    verificationStatus?:
      | "unverified"
      | "catalog-mismatch"
      | "catalog-confirmed"
      | "metadata"
      | "directory-only"
      | "contract-resolved"
      | "runtime-verified"
      | "degraded";
    detectedProtocol?: string;
    supportedProtocols?: string[];
    modes?: string[];
    sizeSlots?: string[];
    resolutionOptions?: string[];
    aspectRatios?: string[];
    durationRange?: [number, number];
    nativeAudio?: "unsupported" | "optional" | "required";
    audioInputSemantics?: Array<
      "driving_audio" | "voice_profile" | "soundtrack" | "audio_prompt"
    >;
    audio_input_semantics?: Array<
      "driving_audio" | "voice_profile" | "soundtrack" | "audio_prompt"
    >;
    referenceLimits?: {
      inputImages?: number;
      referenceImages?: number;
      referenceVideos?: number;
      referenceAudios?: number;
    };
    referenceLimitsKnown?: string[];
    adapterFamily?: string;
    adapterConfidence?: number;
    adapterEvidence?: DirectVideoModelConfig["adapterEvidence"];
    openapiUrl?: string;
    openapiPaths?: string[];
    openapiOperations?: DirectVideoModelConfig["openapiOperations"];
    promptRules?: DirectVideoModelConfig["promptRules"];
    failureGracePolls?: number;
  };
  errorCode?: string;
  httpStatus?: number;
  error?: string;
}

export interface DirectDiscoveredModel {
  id: string;
  metadata: Record<string, unknown>;
}

export interface DirectModelDiscoveryResult {
  ok: boolean;
  models: DirectDiscoveredModel[];
  discoveredModelCount: number;
  protocol: string;
  detectedProtocol?: string;
  errorCode?: string;
  error?: string;
}

export function discoverDirectVideoModels(
  input: Pick<DirectVideoModelInput, "id" | "baseUrl" | "apiKey" | "protocol">,
) {
  return api
        .post("api/v1/model-gateway/direct-video-models/discover", {
          json: input,
          // Discovery may read /models plus a bounded OpenAPI document. Keep
          // the browser budget above the backend's per-request probe budget.
          timeout: 30_000,
    })
    .json<OkResponse<DirectModelDiscoveryResult> | ErrorResponse>();
}

export function discoverDirectModels(input: {
  kind: DirectModelKind;
  id?: string;
  baseUrl: string;
  apiKey?: string;
  protocol?: DirectModelProtocol | string;
}) {
  const { kind, ...body } = input;
  return api
    .post(`api/v1/model-gateway/direct-models/${encodeURIComponent(kind)}/discover`, {
      json: body,
      timeout: 15_000,
    })
    .json<OkResponse<DirectModelDiscoveryResult> | ErrorResponse>();
}

export interface DirectModelProbeResult {
  ok: boolean;
  modelFound: boolean;
  /** Catalog lacks the id but the runtime call succeeded (preview models). */
  catalogMissing?: boolean;
  catalogMissingNotice?: string;
  discoveredModelCount: number;
  protocol: string;
  verificationStatus?: DirectModelConfig["verificationStatus"];
  probeContractVersion?: number;
  chatProbeStatus?: string;
  streamProbeStatus?: string;
  toolProbeStatus?: string;
  visionProbeStatus?: string;
  visionProbeError?: string;
  hermesProbeStatus?: string;
  embeddingProbeStatus?: string;
  embeddingDimensions?: number;
  detectedProtocol?: string;
  modelMetadata?: Record<string, unknown>;
  models?: DirectDiscoveredModel[];
  responsesProbeStatus?:
    | "native-confirmed"
    | "adapter-selected"
    | "adapter-fallback"
    | "probe-failed"
    | "native-rejected";
  responsesProbeError?: string;
  toolCallingVerified?: boolean;
  capabilities?: {
    supportedModes?: string[];
    useCase?: string;
    parameterDefaults?: Record<string, string | number | boolean>;
    runtimeReady?: boolean;
    verificationStatus?: DirectModelConfig["verificationStatus"];
  };
  error?: string;
}

export interface NewApiDatabaseConfigInput {
  sqlDsn?: string;
  sqlitePath?: string;
  adminUsername?: string;
}

export interface InitCustomNewApiInput {
  /** 可选；不传则后端用 NEWAPI_BASE_URL 环境变量。 */
  newApiBaseUrl?: string;
  database?: NewApiDatabaseConfigInput;
  setupUsername?: string;
  setupPassword?: string;
  setupConfirmPassword?: string;
}

export interface NewApiSetupInitStatus {
  initialized: boolean;
  rootInitialized: boolean;
  databaseType: string;
  setupPerformed: boolean;
  alreadyInitialized: boolean;
}

export interface InitCustomNewApiResult {
  mode: "custom";
  newApiAdminBaseUrl: string;
  newApiBaseUrl: string;
  newApiSetup?: NewApiSetupInitStatus;
}

export interface FastApiErrorResponse {
  detail?: unknown;
  error?: unknown;
  message?: unknown;
  ok?: false;
}

/** 一个 NewAPI 渠道：provider + 上游 Key + DC 模型名→上游模型名映射。 */
export interface CustomChannelInput {
  provider: string;
  /** 渠道名，可选；不填后端自动生成。 */
  name?: string;
  upstreamKey: string;
  /** DC 内部模型名 -> 真实上游模型名。 */
  modelMapping: Record<string, string>;
  group: string;
  priority: number;
  weight: number;
  /** 可选；仅自定义 provider 或覆盖默认地址时填。 */
  baseUrl: string;
  /** 可选；不填后端用 modelMapping 第一个 key。 */
  testModel: string;
}

export interface SaveProviderChannelsInput {
  channels: Array<{ provider: string; upstreamKey?: string; baseUrl?: string }>;
}

export interface SyncProviderChannelInput {
  newApiBaseUrl: string;
  database?: NewApiDatabaseConfigInput;
  provider: string;
  upstreamKey?: string;
  baseUrl?: string;
}

export interface SaveMediaModelsInput {
  newApiBaseUrl: string;
  database?: NewApiDatabaseConfigInput;
  models: Record<string, SavedMediaModelConfig>;
}

export interface SaveEmbeddingModelInput {
  newApiBaseUrl: string;
  database?: NewApiDatabaseConfigInput;
  provider: string;
  upstreamModel: string;
  dimension: number;
  batchSize?: number;
}

export interface SaveMediaRelayConfigInput {
  provider: "aliyun_oss" | "cloudinary";
  ttlSeconds: number;
  endpoint?: string;
  bucket?: string;
  accessKeyId?: string;
  accessKeySecret?: string;
  cloudName?: string;
  apiKey?: string;
  apiSecret?: string;
  apiFolder?: string;
}

export interface SaveCustomChannelsBatchInput {
  newApiBaseUrl: string;
  database?: NewApiDatabaseConfigInput;
  channels: CustomChannelInput[];
}

export interface CustomChannelWriteResult {
  provider?: string;
  name?: string;
  ok?: boolean;
  channelId?: number | string;
  error?: string;
  /** 后端已 mask，不含完整 key。 */
  upstreamKey?: string;
  [key: string]: unknown;
}

export interface SaveCustomChannelsBatchResult {
  succeeded: number;
  failed: number;
  results: CustomChannelWriteResult[];
}

export interface SyncProviderChannelResult {
  provider: string;
  channelId?: number | string;
  httpStatus?: number;
  savedChannel?: SavedProviderChannelConfig | null;
  sentPayload?: unknown;
  newApiResponse?: unknown;
}

export interface SaveMediaModelsResult extends SaveCustomChannelsBatchResult {
  models: Record<string, SavedMediaModelConfig>;
}

export interface SaveEmbeddingModelResult {
  embeddingModel: SavedEmbeddingModelConfig;
  result: CustomChannelWriteResult;
}

export function useModelGatewayConfig(enabled = true) {
  return useQuery({
    queryKey: queryKeys.modelGateway(),
    queryFn: ({ signal }) =>
      api
        .get("api/v1/model-gateway/config", { signal })
        .json<OkResponse<ModelGatewayConfig>>(),
    enabled,
  });
}
export function useSaveOfficialConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: SaveOfficialConfigInput) =>
      api
        .post("api/v1/model-gateway/official/config", { json: input })
        .json<OkResponse<ModelGatewayConfig> | ErrorResponse>(),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.modelGateway() });
    },
  });
}
export function useEnableOfficial() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      api
        .post("api/v1/model-gateway/official/enable")
        .json<OkResponse<ModelGatewayConfig> | ErrorResponse>(),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.modelGateway() });
    },
  });
}

export function useEnableUnified() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      api
        .post("api/v1/model-gateway/unified/enable")
        .json<OkResponse<ModelGatewayConfig> | ErrorResponse>(),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.modelGateway() });
    },
  });
}

export function useSaveUnifiedConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: SaveUnifiedConfigInput) =>
      api
        .post("api/v1/model-gateway/unified/config", { json: input })
        .json<OkResponse<ModelGatewayConfig> | ErrorResponse>(),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.modelGateway() });
    },
  });
}

export function useInitCustomNewApi() {
  const qc = useQueryClient();
  return useMutation({
    // 初始化要连 NewAPI、建 token、写库，耗时较长，放宽超时。
    mutationFn: (input: InitCustomNewApiInput) =>
      api
        .post("api/v1/model-gateway/custom/newapi/init", {
          json: input,
          timeout: 60_000,
          throwHttpErrors: false,
        })
        .json<OkResponse<InitCustomNewApiResult> | ErrorResponse | FastApiErrorResponse>(),
    onSuccess: (data) => {
      if (data.ok === true) {
        qc.invalidateQueries({ queryKey: queryKeys.modelGateway() });
      }
    },
  });
}


/** 保存供应商渠道级配置。 */
export function useSaveProviderChannels() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: SaveProviderChannelsInput) =>
      api
        .post("api/v1/model-gateway/custom/newapi/provider-channels", {
          json: input,
          timeout: 60_000,
        })
        .json<OkResponse<{ channels: SavedProviderChannelConfig[] }> | ErrorResponse>(),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.modelGateway() });
    },
  });
}

/** 更新 NewAPI 中已存在的供应商渠道 key / Base URL，不改模型映射。 */
export function useSyncProviderChannel() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: SyncProviderChannelInput) =>
      api
        .post("api/v1/model-gateway/custom/newapi/provider-channel/sync", {
          json: input,
          timeout: 60_000,
          throwHttpErrors: false,
        })
        .json<OkResponse<SyncProviderChannelResult> | ErrorResponse | FastApiErrorResponse>(),
    onSuccess: (data) => {
      if (data.ok === true) {
        qc.invalidateQueries({ queryKey: queryKeys.modelGateway() });
      }
    },
  });
}

/** 写入单个 NewAPI 渠道。 */
export function useSaveCustomChannel() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: CustomChannelInput & { newApiBaseUrl: string; database?: NewApiDatabaseConfigInput }) =>
      api
        .post("api/v1/model-gateway/custom/newapi/channels", {
          json: input,
          timeout: 60_000,
        })
        .json<OkResponse<CustomChannelWriteResult> | ErrorResponse>(),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.modelGateway() });
    },
  });
}

/** 保存图片 / 视频固定模型映射。 */
export function useSaveMediaModels() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: SaveMediaModelsInput) =>
      api
        .post("api/v1/model-gateway/custom/newapi/media-models", {
          json: input,
          timeout: 120_000,
        })
        .json<OkResponse<SaveMediaModelsResult> | ErrorResponse>(),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.modelGateway() });
    },
  });
}

/** 保存 Cognee embedding 模型映射；维度只保存到 CE 本地配置。 */
export function useSaveEmbeddingModel() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: SaveEmbeddingModelInput) =>
      api
        .post("api/v1/model-gateway/custom/newapi/embedding-model", {
          json: input,
          timeout: 120_000,
        })
        .json<OkResponse<SaveEmbeddingModelResult> | ErrorResponse>(),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.modelGateway() });
    },
  });
}

/** 保存 NewAPI 参考媒体 relay 配置。 */
export function useSaveMediaRelayConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: SaveMediaRelayConfigInput) =>
      api
        .post("api/v1/model-gateway/media-relay/config", {
          json: input,
          timeout: 60_000,
        })
        .json<OkResponse<MediaRelayConfig> | ErrorResponse>(),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.modelGateway() });
    },
  });
}

/** Save locally managed, OpenAI-compatible direct video endpoints. */
export function useSaveDirectVideoModels() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ models, confirmClear }: SaveDirectVideoModelsInput) =>
      api
        .post("api/v1/model-gateway/direct-video-models", {
          json: { models, confirmClear: confirmClear === true },
          timeout: 30_000,
        })
        .json<OkResponse<DirectVideoModelConfig[]> | ErrorResponse>(),
    onSuccess: (response) => {
      qc.invalidateQueries({ queryKey: queryKeys.modelGateway() });
      if (response.ok && typeof window !== "undefined") {
        window.dispatchEvent(new CustomEvent(DIRECT_MODEL_REGISTRY_CHANGED_EVENT, {
          detail: { kind: "video" },
        }));
      }
    },
  });
}

/** Probe the configured `GET /models` endpoint without submitting a video task. */
export function useProbeDirectVideoModel() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: DirectVideoModelInput) =>
      api
        .post("api/v1/model-gateway/direct-video-models/probe", {
          json: input,
          timeout: 30_000,
        })
        .json<OkResponse<DirectVideoModelProbeResult> | ErrorResponse>(),
    onSuccess: (response) => {
      if (!response.ok) return;
      qc.invalidateQueries({ queryKey: queryKeys.modelGateway() });
      if (typeof window !== "undefined") {
        window.dispatchEvent(new CustomEvent(DIRECT_MODEL_REGISTRY_CHANGED_EVENT, {
          detail: { kind: "video" },
        }));
      }
    },
  });
}

/** Save a durable direct model family used by canvas, workflow and Agent. */
export function useSaveDirectModels() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ kind, models, confirmClear }: SaveDirectModelsInput) =>
      api
        .post(`api/v1/model-gateway/direct-models/${encodeURIComponent(kind)}`, {
          json: { models, confirmClear: confirmClear === true },
          timeout: 30_000,
        })
        .json<OkResponse<DirectModelConfig[]> | ErrorResponse>(),
    onSuccess: (response, variables) => {
      qc.invalidateQueries({ queryKey: queryKeys.modelGateway() });
      if (response.ok && typeof window !== "undefined") {
        window.dispatchEvent(new CustomEvent(DIRECT_MODEL_REGISTRY_CHANGED_EVENT, {
          detail: { kind: variables.kind },
        }));
      }
    },
  });
}

/** Non-billing discovery against the configured upstream's GET /models endpoint. */
export function useProbeDirectModel() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ kind, ...model }: DirectModelInput & { kind: DirectModelKind }) =>
      api
        .post(`api/v1/model-gateway/direct-models/${encodeURIComponent(kind)}/probe`, {
          json: model,
          // Agent probing proves non-stream, stream, and tool contracts in
          // sequence; give it a bounded but realistic budget. Other kinds
          // only read the catalog or run their bounded probe.
          timeout: kind === "agent" ? 45_000 : 30_000,
        })
        .json<OkResponse<DirectModelProbeResult> | ErrorResponse>(),
    onSuccess: (response, variables) => {
      if (!response.ok) return;
      qc.invalidateQueries({ queryKey: queryKeys.modelGateway() });
      if (typeof window !== "undefined") {
        window.dispatchEvent(new CustomEvent(DIRECT_MODEL_REGISTRY_CHANGED_EVENT, {
          detail: { kind: variables.kind },
        }));
      }
    },
  });
}

/** 批量写入 NewAPI 渠道（功能模型映射保存）。后端支持部分成功。 */
export function useSaveCustomChannelsBatch() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: SaveCustomChannelsBatchInput) =>
      api
        .post("api/v1/model-gateway/custom/newapi/channels/batch", {
          json: input,
          timeout: 120_000,
        })
        .json<OkResponse<SaveCustomChannelsBatchResult> | ErrorResponse>(),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.modelGateway() });
    },
  });
}
