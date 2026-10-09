// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import {
  AlertTriangle,
  ArrowDown,
  ArrowUp,
  ChevronDown,
  Copy,
  Cpu,
  Eye,
  EyeOff,
  HardDrive,
  Loader2,
  Plus,
  RotateCw,
  Star,
  Trash2,
} from "lucide-react";

import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ScrollArea } from "@/components/ui/scroll-area";
import { cn } from "@/lib/utils";
import { canvasOnlyProduct } from "@/lib/product-mode";
import {
  FEATURE_MODEL_GROUPS,
  FEATURE_MODEL_PRODUCT_GROUPS,
  type FeatureModelDef,
  type FeatureModelGroup,
} from "@/lib/feature-models";
import {
  useModelGatewayConfig,
  useEnableUnified,
  useSaveUnifiedConfig,
  useInitCustomNewApi,
  useSaveCustomChannel,
  useSaveCustomChannelsBatch,
  useSaveEmbeddingModel,
  useSaveMediaModels,
  useSaveProviderChannels,
  useSaveMediaRelayConfig,
  useSaveDirectVideoModels,
  useProbeDirectVideoModel,
  useSaveDirectModels,
  useProbeDirectModel,
  discoverDirectModels,
  discoverDirectVideoModels,
  useSyncProviderChannel,
  type GatewayMode,
  type ModelGatewayConfig,
  type CustomChannelInput,
  type NewApiDatabaseConfigInput,
  type SavedEmbeddingModelConfig,
  type SavedProviderChannelConfig,
  type DirectVideoModelConfig,
  type DirectVideoModelInput,
  type DirectVideoModelProbeResult,
  type DirectModelConfig,
  type DirectModelKind,
  type DirectDiscoveredModel,
} from "@/lib/queries/model-gateway";
import {
  DIRECT_MODEL_PROTOCOL_OPTIONS,
  LOCAL_DIRECT_MODEL_META,
  completeLocalDirectModel,
  directModelSavePayload,
  directModelsSnapshotKey,
  inferVillageDirectModelProtocol,
  localModelInputFromSaved,
  modelConnectionErrorMessage,
  modelConnectionSummary,
  newLocalDirectModel,
  normalizeVillageDirectModelProtocol,
  readLocalDirectModels,
  sameDirectModelEndpoint,
  villageDirectModelProtocolLabel,
  villageDirectModelRuntimeReady,
  writeLocalDirectModels,
  type LocalDirectModelInput,
  type LocalDirectModelKind,
  type ModelConnectionTone,
} from "@/components/settings/models/direct-model-config";

export {
  inferVillageDirectModelProtocol,
  normalizeVillageDirectModelProtocol,
  villageDirectModelRuntimeReady,
} from "@/components/settings/models/direct-model-config";
import { VideoModelHeaderChips } from "@/components/settings/models/video-wire-contract";
import {
  VideoModelCapabilityPreview,
  videoCapabilitySourceFor,
} from "@/components/settings/models/video-model-capability-preview";
export { videoWireContractBadge } from "@/components/settings/models/video-wire-contract";
import {
  useSettingsStore,
  FEATURE_MODEL_PROVIDERS,
  type AliyunOssStorageConfig,
  type CloudinaryStorageConfig,
  type EmbeddingModelEntry,
  type FeatureModelProvider,
  type MediaStorageProvider,
} from "@/stores/settingsStore";

interface SettingsDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}
const MEDIA_STORAGE_PROVIDERS: MediaStorageProvider[] = ["aliyun_oss", "cloudinary"];

export function SettingsDialog({ open, onOpenChange }: SettingsDialogProps) {
  const { t } = useTranslation();
  const [page, setPage] = useState<"models" | "storage">("models");
  const statusQuery = useModelGatewayConfig(open);
  const settingsStatus = statusQuery.data?.data;
  const directVideoConfigured = Boolean(
    settingsStatus?.directVideoModels?.some((model) => model.enabled && model.configured),
  );
  const directModelConfigured = Boolean(
    Object.values(settingsStatus?.directModels ?? {}).some((models) =>
      models?.some((model) => model.enabled && model.configured),
    ),
  );
  const modelConfigured = canvasOnlyProduct
    ? directVideoConfigured || directModelConfigured
    : Boolean(settingsStatus?.effective.configured);
  const mediaStorageConfigured = Boolean(settingsStatus?.mediaRelay?.configured);

  const pageStatus = (configured: boolean, label: string) => {
    if (statusQuery.isLoading) {
      return (
        <Loader2
          className="absolute top-1 right-1 size-3 animate-spin text-muted-foreground sm:static sm:ml-auto sm:size-3.5"
          aria-hidden
        />
      );
    }
    if (configured) {
      return (
        <span
          className="absolute top-1 right-1 size-2 shrink-0 rounded-full bg-emerald-400 sm:static sm:ml-auto"
          aria-label={t("settings.statusConfigured", { page: label })}
          title={t("settings.statusConfigured", { page: label })}
        />
      );
    }
    return (
      <AlertTriangle
        className="absolute top-1 right-1 size-3.5 shrink-0 text-amber-400 sm:static sm:ml-auto sm:size-4"
        aria-label={t("settings.statusNotConfigured", { page: label })}
      />
    );
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        showCloseButton
        data-agent-dialog="v4"
        className="village-agent-dialog-v4 village-agent-dialog-v4--settings village-settings-dialog flex h-[min(86vh,820px)] max-w-[calc(100%-1.5rem)] flex-col gap-0 overflow-hidden rounded-[20px] border border-white/[0.11] bg-[#111113]/98 p-0 shadow-[0_30px_100px_rgba(0,0,0,0.58)] ring-0 backdrop-blur-2xl sm:max-w-[1120px]"
      >
        <DialogHeader className="village-settings-dialog__header border-b border-white/[0.08] px-5 py-4">
          <DialogTitle>{t("settings.title")}</DialogTitle>
        </DialogHeader>

        <div className="flex min-h-0 flex-1">
          <nav
            aria-label={t("settings.navigationLabel")}
            className="village-settings-dialog__nav flex w-14 shrink-0 flex-col gap-1 border-r border-white/[0.08] px-2 py-4 sm:w-44 sm:px-3"
          >
            <button
              type="button"
              aria-current={page === "models" ? "page" : undefined}
              onClick={() => setPage("models")}
              className={cn(
                "relative flex h-10 items-center justify-center gap-2 rounded-md px-2 text-sm font-medium transition-colors sm:justify-start sm:px-3",
                page === "models"
                  ? "bg-white/[0.09] text-foreground"
                  : "text-muted-foreground hover:bg-white/[0.05] hover:text-foreground",
              )}
            >
              <Cpu className="size-4" aria-hidden />
              <span className="hidden sm:inline">{t("settings.pages.models")}</span>
              {pageStatus(modelConfigured, t("settings.pages.models"))}
            </button>
            <button
              type="button"
              aria-current={page === "storage" ? "page" : undefined}
              onClick={() => setPage("storage")}
              className={cn(
                "relative flex h-10 items-center justify-center gap-2 rounded-md px-2 text-sm font-medium transition-colors sm:justify-start sm:px-3",
                page === "storage"
                  ? "bg-white/[0.09] text-foreground"
                  : "text-muted-foreground hover:bg-white/[0.05] hover:text-foreground",
              )}
            >
              <HardDrive className="size-4" aria-hidden />
              <span className="hidden sm:inline">{t("settings.pages.storage")}</span>
              {pageStatus(mediaStorageConfigured, t("settings.pages.storage"))}
            </button>
          </nav>

          {page === "models" ? (
            <div className="min-w-0 flex-1">
            <ScrollArea className="h-full [&_[data-slot=scroll-area-scrollbar]]:!w-1 [&_[data-slot=scroll-area-scrollbar]]:!border-l-0 [&_[data-slot=scroll-area-scrollbar]]:!p-0">
              <ModelConfigSection open={open && page === "models"} />
            </ScrollArea>
            </div>
          ) : (
            <div className="min-w-0 flex-1">
            <ScrollArea className="h-full [&_[data-slot=scroll-area-scrollbar]]:!w-1 [&_[data-slot=scroll-area-scrollbar]]:!border-l-0 [&_[data-slot=scroll-area-scrollbar]]:!p-0">
              <MediaStorageSection />
            </ScrollArea>
            </div>
          )}
        </div>

        <div className="village-settings-dialog__footer flex justify-end border-t border-white/[0.08] px-5 py-3.5">
          <DialogClose render={<Button variant="outline" size="sm" />}>
            {t("settings.close")}
          </DialogClose>
        </div>
      </DialogContent>
    </Dialog>
  );
}
const GATEWAY_MODES: GatewayMode[] = ["unified"];

const DEFAULT_CUSTOM_NEWAPI_URL = "http://127.0.0.1:3000";
const EMPTY_DIRECT_MODEL_CONFIGS: DirectModelConfig[] = [];

async function getRequestErrorMessage(error: unknown, fallback: string): Promise<string> {
  const response = (error as { response?: Response } | null)?.response;
  if (response) {
    const body = await response.clone().json().catch(() => null);
    if (body && typeof body === "object") {
      const data = body as { detail?: unknown; error?: unknown; message?: unknown };
      for (const value of [data.detail, data.error, data.message]) {
        if (typeof value === "string" && value.trim()) return value.trim();
      }
    }
    const text = await response.clone().text().catch(() => "");
    if (text.trim()) return text.trim();
  }
  const message = (error as { message?: unknown } | null)?.message;
  return typeof message === "string" && message.trim() ? message.trim() : fallback;
}

function getResponseErrorMessage(response: unknown, fallback: string): string {
  if (response && typeof response === "object") {
    const data = response as { detail?: unknown; error?: unknown; message?: unknown };
    for (const value of [data.detail, data.error, data.message]) {
      if (typeof value === "string" && value.trim()) return value.trim();
    }
    const payload = (data as { data?: unknown }).data;
    if (payload && typeof payload === "object") {
      const result = (payload as { result?: unknown }).result;
      if (result && typeof result === "object") {
        const error = (result as { error?: unknown }).error;
        if (typeof error === "string" && error.trim()) return error.trim();
      }
      const results = (payload as { results?: unknown }).results;
      if (Array.isArray(results)) {
        const failed = results.find(
          (item) =>
            item &&
            typeof item === "object" &&
            typeof (item as { error?: unknown }).error === "string" &&
            Boolean(((item as { error?: string }).error ?? "").trim()),
        ) as
          | { error?: unknown }
          | undefined;
        if (typeof failed?.error === "string" && failed.error.trim()) {
          return failed.error.trim();
        }
      }
    }
    if (Array.isArray(data.detail)) {
      const first = data.detail.find((item) => item && typeof item === "object") as
        | { msg?: unknown }
        | undefined;
      if (typeof first?.msg === "string" && first.msg.trim()) return first.msg.trim();
    }
  }
  return fallback;
}

function ModelConfigSection({ open }: { open: boolean }) {
  const { t } = useTranslation();
  const configQuery = useModelGatewayConfig(open);
  const config = configQuery.data?.data;
  const loading = configQuery.isLoading;
  const directVideoConfigured = Boolean(
    config?.directVideoModels?.some((model) => model.enabled && model.configured),
  );
  const directModelConfigured = Boolean(
    Object.values(config?.directModels ?? {}).some((models) =>
      models?.some((model) => model.enabled && model.configured),
    ),
  );
  const modelGatewayMissing = config
    ? canvasOnlyProduct
      ? !(directVideoConfigured || directModelConfigured)
      : !config.effective.configured
    : false;

  const [mode, setMode] = useState<GatewayMode>("unified");
  // Legacy backend modes migrate server-side; the operator sees one gateway.
  const serverMode = config?.mode;
  useEffect(() => {
    if (serverMode) {
      setMode("unified");
    }
  }, [serverMode]);

  // CE 运行环境提供本地 NewAPI 管理地址；初始化与下方模型映射共用该地址。
  const [customBaseUrl, setCustomBaseUrl] = useState(DEFAULT_CUSTOM_NEWAPI_URL);
  const seededCustomBaseUrl =
    config?.custom?.adminBaseUrl ||
    config?.provisioner?.adminBaseUrl ||
    config?.custom?.baseUrl ||
    "";
  useEffect(() => {
    if (seededCustomBaseUrl) {
      setCustomBaseUrl((current) =>
        current === seededCustomBaseUrl ? current : seededCustomBaseUrl,
      );
    }
  }, [seededCustomBaseUrl]);

  // CE owns one local SQLite-backed NewAPI instance. Database paths and the
  // root username are deployment details, not user-editable model settings.
  const customDatabase: NewApiDatabaseConfigInput | undefined = undefined;

  return (
    <section className="px-5 py-5">
      {!canvasOnlyProduct ? (
        <>
          <div className="flex items-center gap-2">
            <span
              className={cn(
                "size-1.5 rounded-full",
                modelGatewayMissing ? "bg-amber-400" : "bg-emerald-400",
              )}
            />
            <h3 className="font-heading text-sm font-medium text-foreground">
              {t("settings.modelConfig.title")}
            </h3>
            {modelGatewayMissing ? (
              <AlertTriangle
                className="size-3.5 text-amber-400"
                aria-label={t("settings.modelConfig.gatewayWarningIconLabel")}
              />
            ) : null}
            {config ? (
              <span className="ml-1 rounded bg-white/[0.06] px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
                {t("settings.modelConfig.effectiveBadge", {
                  channel: t(`settings.modelConfig.modes.${config.effective.source}`, {
                    defaultValue: config.effective.source,
                  }),
                })}
              </span>
            ) : null}
          </div>
          <p className="mt-2 text-xs leading-relaxed text-muted-foreground">
            {t("settings.modelConfig.description")}
          </p>
        </>
      ) : null}
      {modelGatewayMissing ? (
        <div className="mt-3 flex gap-2 rounded-md border border-amber-500/35 bg-amber-500/10 px-3 py-2 text-[11px] leading-relaxed text-amber-100">
          <AlertTriangle className="mt-0.5 size-3.5 shrink-0 text-amber-300" aria-hidden />
          <p>
            {canvasOnlyProduct
              ? "还没有启用可用的直连模型。添加一个模型并保存后，系统会自动同步到对应节点和 Agent。"
              : t("settings.modelConfig.gatewayNotConfiguredImpact")}
          </p>
        </div>
      ) : null}

      {!canvasOnlyProduct ? (
        <>
          <Tabs
            className="mt-4"
            value={mode}
            onValueChange={(value) => setMode(value as GatewayMode)}
          >
            <TabsList>
              {GATEWAY_MODES.map((m) => (
                <TabsTrigger key={m} value={m}>
                  {t(`settings.modelConfig.modes.${m}`)}
                </TabsTrigger>
              ))}
            </TabsList>
          </Tabs>

          <div className="mt-4">
            {mode === "unified" ? (
              <OfficialGatewayPanel config={config} loading={loading} />
            ) : (
              <CustomGatewayPanel
                config={config}
                loading={loading}
                baseUrl={customBaseUrl}
              />
            )}
          </div>
        </>
      ) : null}

      {/* 旧的本地渠道管理组件保留兼容实现，但统一中转站界面不再暴露它。 */}
      {canvasOnlyProduct ? (
        <VillageCanvasModelFolders
          directVideoModels={config?.directVideoModels ?? []}
          directModels={config?.directModels ?? {}}
        />
      ) : mode === "custom" ? (
        <FeatureModelsBlock
          newApiBaseUrl={customBaseUrl}
          database={customDatabase}
          savedProviderChannels={config?.provisioner?.providerChannels ?? []}
          savedEmbeddingModel={config?.provisioner?.embeddingModel}
          savedMediaModels={config?.provisioner?.mediaModels ?? {}}
        />
      ) : null}

      {!canvasOnlyProduct ? (
        <DirectVideoModelsPanel models={config?.directVideoModels ?? []} />
      ) : null}
    </section>
  );
}
function VillageCanvasModelFolders({
  directVideoModels,
  directModels,
}: {
  directVideoModels: DirectVideoModelConfig[];
  directModels: Partial<Record<DirectModelKind, DirectModelConfig[]>>;
}) {
  const [activeSection, setActiveSection] = useState<VillageModelSectionKey>("chat");
  const [sectionRefresh, setSectionRefresh] = useState(0);
  const sections = useMemo(
    () => villageModelSections(directVideoModels, directModels),
    [directModels, directVideoModels, sectionRefresh],
  );
  const active = sections.find((section) => section.key === activeSection) ?? sections[0];
  const enabledModelCount = sections.reduce((total, section) => total + section.count, 0);

  const renderActivePanel = () => {
    if (active.key === "video") {
      return <DirectVideoModelsPanel key="video" models={directVideoModels} compact />;
    }
    return (
      <LocalDirectModelsPanel
        key={active.key}
        kind={active.key}
        savedModels={directModels[active.key] ?? EMPTY_DIRECT_MODEL_CONFIGS}
        onPersist={() => setSectionRefresh((value) => value + 1)}
      />
    );
  };

  return (
    <div className="village-model-console mt-5 space-y-3">
      <div className="village-model-console__intro">
        <div>
          <div className="village-model-console__eyebrow">
            <span /> 模型中心
          </div>
          <h4>直连模型</h4>
          <p>四类模型各配一次，保存后自动同步到画布节点与 Agent。</p>
        </div>
        <div className="village-model-console__status">
          <span /> 共 {enabledModelCount} 个模型可用
        </div>
      </div>

      <div className="village-model-console__tabs" role="tablist" aria-label="模型类型">
        {sections.map((section) => {
          const selected = section.key === active.key;
          return (
            <button
              key={section.key}
              type="button"
              role="tab"
              aria-selected={selected}
              onClick={() => setActiveSection(section.key)}
              className={cn("village-model-console__tab", selected && "is-active")}
            >
              <span>{section.shortLabel}</span>
              <small>{section.count}</small>
            </button>
          );
        })}
      </div>

      <section className="village-model-console__content">
        <header className="village-model-console__content-head">
          <div>
            <h4>{active.label}</h4>
            <p>{active.description}</p>
          </div>
          <span className="village-model-console__count">{active.count} 个启用</span>
        </header>
        <div className="village-model-console__content-body">{renderActivePanel()}</div>
      </section>
    </div>
  );
}

type VillageModelSectionKey = LocalDirectModelKind | "video";

function villageModelSections(
  directVideoModels: DirectVideoModelConfig[],
  directModels: Partial<Record<DirectModelKind, DirectModelConfig[]>>,
): Array<{
  key: VillageModelSectionKey;
  label: string;
  shortLabel: string;
  description: string;
  count: number;
}> {
  const localCount = (kind: LocalDirectModelKind) =>
    (directModels[kind] ?? []).filter((model) => model.enabled).length;
  return [
    { key: "chat", label: "对话模型", shortLabel: "对话", description: "对话、提示词、脚本、识图与 Agent 主脑", count: localCount("chat") },
    { key: "image", label: "生图模型", shortLabel: "生图", description: "图片生成与图片编辑", count: localCount("image") },
    { key: "video", label: "视频模型", shortLabel: "视频", description: "视频节点真实直连", count: directVideoModels.filter((model) => model.enabled).length },
    { key: "embedding", label: "向量模型", shortLabel: "向量", description: "Embedding 与语义检索", count: localCount("embedding") },
  ];
}

function ModelConnectionStatus({
  label,
  detail,
  tone,
}: {
  label: string;
  detail: string;
  tone: ModelConnectionTone;
}) {
  return (
    <div className={`village-model-connection is-${tone}`} role="status">
      <span className="village-model-connection__dot" aria-hidden />
      <div>
        <strong>{label}</strong>
        <span title={detail}>{detail}</span>
      </div>
    </div>
  );
}


const legacyDirectModelMigrations = new Set<string>();

export function LocalDirectModelsPanel({
  kind,
  savedModels,
  onPersist,
}: {
  kind: LocalDirectModelKind;
  savedModels: DirectModelConfig[];
  onPersist?: () => void;
}) {
  const meta = LOCAL_DIRECT_MODEL_META[kind];
  const saveDirectModels = useSaveDirectModels();
  const probeDirectModel = useProbeDirectModel();
  const [models, setModels] = useState<LocalDirectModelInput[]>(() => readLocalDirectModels(kind));
  const [probingModelKeys, setProbingModelKeys] = useState<Set<string>>(() => new Set());
  const [discoveringModelKeys, setDiscoveringModelKeys] = useState<Set<string>>(() => new Set());
  const [discoveredModels, setDiscoveredModels] = useState<Record<string, {
    protocol: string;
    models: DirectDiscoveredModel[];
  }>>({});
  const savedModelsKey = useMemo(
    () => directModelsSnapshotKey(kind, savedModels),
    [kind, savedModels],
  );
  const loadedSnapshotRef = useRef<string | null>(null);
  const onPersistRef = useRef(onPersist);

  useEffect(() => {
    onPersistRef.current = onPersist;
  }, [onPersist]);

  // Versions before this registry saved every non-video model exclusively in
  // browser storage.  Migrate those records once on panel open so an existing
  // creator configuration appears in every node without retyping credentials.
  useEffect(() => {
    if (loadedSnapshotRef.current === savedModelsKey) {
      return;
    }
    loadedSnapshotRef.current = savedModelsKey;

    if (savedModels.length > 0) {
      const safeModels = savedModels.map(localModelInputFromSaved);
      setModels(safeModels);
      writeLocalDirectModels(kind, safeModels);
      return;
    }
    const legacy = readLocalDirectModels(kind);
    setModels(legacy);
    const migrationKey = `${kind}:${legacy.map((model) => `${model.id}|${model.modelId}|${model.baseUrl}`).join(";")}`;
    if (
      legacy.length === 0 ||
      legacyDirectModelMigrations.has(migrationKey) ||
      legacy.some((model) => !completeLocalDirectModel(model))
    ) {
      return;
    }
    legacyDirectModelMigrations.add(migrationKey);
    void saveDirectModels
      .mutateAsync({ kind, models: directModelSavePayload(legacy) })
      .then((response) => {
        if (response.ok) {
          writeLocalDirectModels(kind, response.data.map(localModelInputFromSaved));
          onPersistRef.current?.();
        }
      })
      .catch(() => {
        // The local mirror is retained for the next open/retry; no key is lost.
      });
  }, [kind, saveDirectModels, savedModels, savedModelsKey]);
  const updateModel = (index: number, patch: Partial<LocalDirectModelInput>) => {
    setModels((current) =>
      current.map((model, itemIndex) => (itemIndex === index ? { ...model, ...patch } : model)),
    );
  };
  const invalidateModelCatalog = (probeKey: string) => {
    setDiscoveredModels((current) => {
      if (!(probeKey in current)) return current;
      const next = { ...current };
      delete next[probeKey];
      return next;
    });
  };
  const setDefaultModel = (index: number) => {
    setModels((current) =>
      current.map((model, itemIndex) => ({ ...model, isDefault: itemIndex === index })),
    );
  };
  const moveModel = (index: number, direction: -1 | 1) => {
    setModels((current) => {
      const targetIndex = index + direction;
      if (targetIndex < 0 || targetIndex >= current.length) return current;
      const next = [...current];
      const [item] = next.splice(index, 1);
      next.splice(targetIndex, 0, item);
      return next;
    });
  };
  const copyModel = async (model: LocalDirectModelInput) => {
    const payload = JSON.stringify(
      {
        label: model.label,
        modelId: model.modelId,
        baseUrl: model.baseUrl,
        apiKey: model.apiKey ?? "",
        protocol: normalizeVillageDirectModelProtocol(model.protocol),
        enabled: model.enabled,
        isDefault: model.isDefault === true,
        supportedModes: model.supportedModes,
      },
      null,
      2,
    );
    try {
      await navigator.clipboard.writeText(payload);
      toast.success("模型配置已复制");
    } catch {
      toast.error("复制失败");
    }
  };
  const probeModel = async (model: LocalDirectModelInput, index: number, probeKey: string) => {
    const baseUrl = model.baseUrl.trim().replace(/\/+$/, "");
    const apiKey = (model.apiKey ?? "").trim();
    if (!baseUrl || (!apiKey && !model.configured)) {
      toast.error("检测连接需要 Base URL 和 API Key");
      return;
    }
    setProbingModelKeys((current) => {
      const next = new Set(current);
      next.add(probeKey);
      return next;
    });
    try {
      const response = await probeDirectModel.mutateAsync({
        kind,
        id: model.id,
        label: model.label,
        modelId: model.modelId,
        baseUrl,
        apiKey,
        protocol: normalizeVillageDirectModelProtocol(model.protocol),
        enabled: model.enabled,
        isDefault: model.isDefault,
        // Ask the probe to produce evidence for the declared chat capabilities.
        supportsTools: model.supportsTools,
        supportsVision: model.supportsVision,
      });
      if (!response.ok) {
        const message = modelConnectionErrorMessage(getResponseErrorMessage(response, "检测失败"));
        updateModel(index, { lastProbe: "failed", lastProbeMessage: message });
        toast.error(`连接失败：${message}`);
        return;
      }
      if (!response.data.ok) {
        const message = modelConnectionErrorMessage(response.data.error ?? "检测失败");
        updateModel(index, { lastProbe: "failed", lastProbeMessage: message });
        toast.error(`连接失败：${message}`);
        return;
      }
      const responseProtocol = normalizeVillageDirectModelProtocol(response.data.protocol);
      const modeHint = response.data.capabilities?.useCase;
      const catalogMissing = response.data.catalogMissing === true;
      const message = catalogMissing
        ? `上游目录未列出该 ID，但实测调用已通过${modeHint ? `；${modeHint}` : ""}`
        : response.data.modelFound
          ? `模型目录可达，模型 ID 已匹配${modeHint ? `；${modeHint}` : ""}`
          : `模型目录可达，但 /models 未匹配该模型 ID${modeHint ? `；${modeHint}` : ""}`;
      updateModel(index, {
        protocol: normalizeVillageDirectModelProtocol(model.protocol),
        protocolLabel: villageDirectModelProtocolLabel(responseProtocol),
        runtimeReady:
          response.data.capabilities?.runtimeReady
            ?? villageDirectModelRuntimeReady(kind, responseProtocol),
        runtimeProbeRequired: kind === "chat" || kind === "embedding",
        runtimeProbeComplete:
          response.data.capabilities?.runtimeReady !== false
          && (
            kind === "image"
            || response.data.verificationStatus === "runtime-verified"
          ),
        probeContractVersion: response.data.probeContractVersion,
        verificationStatus:
          response.data.verificationStatus
          ?? (response.data.modelFound ? "catalog-confirmed" : "metadata"),
        catalogMissing,
        toolProbeStatus: response.data.toolProbeStatus,
        toolCallingVerified: response.data.toolCallingVerified,
        visionProbeStatus: response.data.visionProbeStatus,
        lastProbe: "ok",
        lastProbeMessage: message,
      });
      if (catalogMissing) toast.warning(message);
      else toast.success(message);
    } catch (error) {
      const message = modelConnectionErrorMessage(error);
      updateModel(index, { lastProbe: "failed", lastProbeMessage: message });
      toast.error(`连接失败：${message}`);
    } finally {
      setProbingModelKeys((current) => {
        const next = new Set(current);
        next.delete(probeKey);
        return next;
      });
    }
  };
  const discoverModels = async (model: LocalDirectModelInput, probeKey: string) => {
    const baseUrl = model.baseUrl.trim().replace(/\/+$/, "");
    const apiKey = (model.apiKey ?? "").trim();
    if (!baseUrl || (!apiKey && !model.configured)) {
      toast.error("读取模型目录需要 Base URL 和 API Key");
      return;
    }
    setDiscoveringModelKeys((current) => new Set(current).add(probeKey));
    try {
      const response = await discoverDirectModels({
        kind,
        id: model.id,
        baseUrl,
        apiKey,
        protocol: normalizeVillageDirectModelProtocol(model.protocol),
      });
      if (!response.ok || !response.data.ok) {
        toast.error(`读取失败：${response.ok ? response.data.error ?? "上游没有可导入的模型" : getResponseErrorMessage(response, "读取模型目录失败")}`);
        return;
      }
      setDiscoveredModels((current) => ({
        ...current,
        [probeKey]: {
          protocol: response.data.detectedProtocol ?? response.data.protocol,
          models: response.data.models,
        },
      }));
      toast.success(`已读取 ${response.data.models.length} 个上游模型`);
    } catch (error) {
      toast.error(`读取失败：${modelConnectionErrorMessage(error)}`);
    } finally {
      setDiscoveringModelKeys((current) => {
        const next = new Set(current);
        next.delete(probeKey);
        return next;
      });
    }
  };
  const importDiscoveredModel = (
    index: number,
    model: DirectDiscoveredModel,
    protocol: string,
  ) => {
    const displayName = typeof model.metadata.displayName === "string"
      ? model.metadata.displayName.trim()
      : "";
    updateModel(index, {
      modelId: model.id,
      label: displayName || model.id,
      protocol: normalizeVillageDirectModelProtocol(protocol),
      protocolLabel: undefined,
      runtimeReady: undefined,
      runtimeProbeComplete: false,
      verificationStatus: undefined,
      lastProbe: "unknown",
      lastProbeMessage: "已从上游目录填入模型 ID，请检测连接",
    });
    toast.success(`已填入 ${model.id}，请继续检测连接`);
  };
  const save = async () => {
    const normalized = models
      .map((model) => ({
        ...model,
        label: model.label.trim(),
        modelId: model.modelId.trim(),
        baseUrl: model.baseUrl.trim(),
        apiKey: (model.apiKey ?? "").trim(),
      }))
      .filter((model) => model.label || model.modelId || model.baseUrl || model.apiKey);
    const incomplete = normalized.filter((model) => !completeLocalDirectModel(model));
    if (incomplete.length > 0) {
      toast.error("有模型字段没填完整：名称、模型 ID、Base URL、API Key 都要填");
      return;
    }
    // 整体替换语义下空数组等于删光整族（含密钥）；空保存仍合法，但必须先确认。
    let confirmClear = false;
    if (normalized.length === 0 && savedModels.length > 0) {
      const confirmed = window.confirm(
        `这会清空「${meta.title}」里已保存的 ${savedModels.length} 条配置（含密钥），确定吗？`,
      );
      if (!confirmed) return;
      confirmClear = true;
    }
    try {
      const response = await saveDirectModels.mutateAsync({
        kind,
        models: directModelSavePayload(normalized),
        confirmClear,
      });
      if (!response.ok) {
        toast.error(getResponseErrorMessage(response, `${meta.title}保存失败`));
        return;
      }
      writeLocalDirectModels(kind, normalized);
      setModels(response.data.map(localModelInputFromSaved));
      setDiscoveredModels({});
      onPersist?.();
      toast.success(`${meta.title}已同步到所有节点`);
    } catch (error) {
      toast.error(await getRequestErrorMessage(error, `${meta.title}保存失败`));
    }
  };

  return (
    <div className="village-direct-model-panel space-y-3">
      <div className="village-direct-model-panel__head">
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="shrink-0"
          onClick={() => setModels((current) => [...current, newLocalDirectModel(kind)])}
        >
          <Plus className="mr-1 size-3.5" /> 添加模型
        </Button>
      </div>

      <div className="village-direct-model-grid grid gap-3 [grid-template-columns:repeat(auto-fit,minmax(320px,440px))]">
        {models.length === 0 ? (
          <div className="rounded-2xl border border-dashed border-white/15 bg-black/15 px-4 py-6 text-center text-xs text-muted-foreground xl:col-span-2">
            {meta.empty}
          </div>
        ) : models.map((model, index) => {
          const requestedProtocol = normalizeVillageDirectModelProtocol(model.protocol);
          const inferredProtocol = inferVillageDirectModelProtocol(
            kind,
            model.baseUrl,
            model.modelId,
            requestedProtocol,
          );
          const protocolLabel = model.protocolLabel ?? villageDirectModelProtocolLabel(inferredProtocol);
          // Protocol support belongs to the current runtime, not to a stale
          // persisted probe. An older false flag must not downgrade a protocol
          // that this build now executes directly.
          const runtimeReady = model.runtimeReady === true;
          const protocolOptions = kind === "chat"
                ? DIRECT_MODEL_PROTOCOL_OPTIONS.filter((option) =>
                    option.value === "auto"
                    || option.value === "openai-compatible"
                    || option.value === "anthropic-messages"
                    || option.value === "gemini"
                    || option.value === "ollama-openai")
                : kind === "image"
                  ? DIRECT_MODEL_PROTOCOL_OPTIONS.filter((option) => option.value === "auto" || option.value === "openai-images" || option.value === "gemini-image")
                  : kind === "embedding"
                    ? DIRECT_MODEL_PROTOCOL_OPTIONS.filter((option) =>
                        option.value === "auto" || option.value === "openai-embeddings")
                    : [];
          const probeKey = model.id ?? `new-${index}`;
          const isProbing = probingModelKeys.has(probeKey);
          const isDiscovering = discoveringModelKeys.has(probeKey);
          const catalog = discoveredModels[probeKey];
          const connection = modelConnectionSummary({
            configured: model.configured,
            enabled: model.enabled,
            lastProbe: model.lastProbe,
            catalogMissing: model.catalogMissing,
            runtimeReady,
            runtimeProbeRequired: model.runtimeProbeRequired,
            runtimeProbeComplete: model.runtimeProbeComplete,
            verificationStatus: model.verificationStatus,
            message: model.lastProbeMessage || model.disabledReason,
          });
          return (
          <div key={model.id ?? `new-${index}`} className="village-direct-model-card rounded-2xl border border-white/10 bg-[linear-gradient(180deg,rgba(255,255,255,0.055),rgba(255,255,255,0.02))] p-4 shadow-[0_14px_40px_rgba(0,0,0,0.16)]">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
              <div className="flex flex-wrap items-center gap-1.5">
                <span className="rounded-full border border-white/10 bg-white/[0.055] px-2 py-0.5 text-[10px] font-medium text-muted-foreground">模型 {index + 1}</span>
                {model.isDefault ? (
                  <span className="inline-flex items-center gap-1 rounded border border-amber-400/40 bg-amber-400/10 px-2 py-0.5 text-[10px] font-medium text-amber-300">
                    <Star className="size-3" /> 默认
                  </span>
                ) : null}
                {requestedProtocol === "auto" ? (
                  <span className="rounded border border-cyan-300/35 bg-cyan-300/10 px-2 py-0.5 text-[10px] font-medium text-cyan-200">
                    协议推断：{protocolLabel}
                  </span>
                ) : (
                  <span className="rounded border border-violet-300/35 bg-violet-300/10 px-2 py-0.5 text-[10px] font-medium text-violet-200">
                    协议：{protocolLabel}
                  </span>
                )}
              </div>
              <div className="flex items-center gap-1">
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  className="h-7 px-2 text-muted-foreground"
                  onClick={() => moveModel(index, -1)}
                  disabled={index === 0}
                  title="上移"
                >
                  <ArrowUp className="size-3.5" />
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  className="h-7 px-2 text-muted-foreground"
                  onClick={() => moveModel(index, 1)}
                  disabled={index === models.length - 1}
                  title="下移"
                >
                  <ArrowDown className="size-3.5" />
                </Button>
              </div>
            </div>
            <ModelConnectionStatus {...connection} />
            {kind === "chat" ? (
              <div className="village-direct-model-card__capabilities mb-3 flex flex-wrap items-center gap-x-4 gap-y-2 rounded-lg border border-white/10 bg-black/15 px-3 py-2 text-xs text-muted-foreground">
                <span className="text-foreground/80">能力</span>
                {([
                  ["tools", "工具调用（Agent）", model.supportsTools !== false, model.toolCallingVerified === true, model.toolProbeStatus],
                  ["vision", "图片输入（识图）", model.supportsVision !== false, model.visionProbeStatus === "passed", model.visionProbeStatus],
                ] as const).map(([key, label, checked, verified, status]) => (
                  <label key={key} className="flex items-center gap-2">
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={(event) => updateModel(index, { [key === "tools" ? "supportsTools" : "supportsVision"]: event.target.checked })}
                      aria-label={`${model.label || "对话模型"}${label}`}
                    />
                    {label}
                    <span className={cn("text-[10px]", verified ? "text-emerald-300/80" : "text-muted-foreground/70")}>
                      {verified
                        ? "已实测"
                        : status === "rejected" || status === "probe-failed"
                          ? "实测不支持"
                          : "未验证"}
                    </span>
                  </label>
                ))}
                <span className="text-[11px] text-muted-foreground/70">
                  点「检测连接」会真的试一次工具调用与图片输入。
                </span>
              </div>
            ) : null}
            <div className="village-direct-model-card__fields grid gap-2.5 md:grid-cols-2">
              <label>
                <span>模型名称</span>
                <Input
                  value={model.label}
                  onChange={(event) => updateModel(index, { label: event.target.value })}
                  placeholder={meta.placeholder}
                  aria-label={`${meta.title}名称`}
                />
              </label>
              <label>
                <span>模型 ID</span>
                <Input
                  value={model.modelId}
                  onChange={(event) => {
                    invalidateModelCatalog(probeKey);
                    updateModel(index, {
                      modelId: event.target.value,
                      protocolLabel: undefined,
                      runtimeReady: undefined,
                      runtimeProbeComplete: false,
                      verificationStatus: undefined,
                      lastProbe: "unknown",
                      lastProbeMessage: "",
                    });
                  }}
                  placeholder="上游模型 ID"
                  aria-label={`${meta.title}模型 ID`}
                />
              </label>
              <label>
                <span>Base URL</span>
                <Input
                  value={model.baseUrl}
                  onChange={(event) => {
                    invalidateModelCatalog(probeKey);
                    updateModel(index, {
                      baseUrl: event.target.value,
                      ...(!sameDirectModelEndpoint(model.baseUrl, event.target.value)
                        ? { apiKey: "", configured: false, apiKeyPreview: "" }
                        : {}),
                      protocolLabel: undefined,
                      runtimeReady: undefined,
                      runtimeProbeComplete: false,
                      verificationStatus: undefined,
                      lastProbe: "unknown",
                      lastProbeMessage: "",
                    })
                  }}
                  placeholder="https://api.example.com/v1"
                  aria-label={`${meta.title} Base URL`}
                />
              </label>
              <label>
                <span>API Key</span>
                <Input
                  value={model.apiKey ?? ""}
                  type="password"
                  autoComplete="new-password"
                  data-1p-ignore="true"
                  data-lpignore="true"
                  onChange={(event) => {
                    invalidateModelCatalog(probeKey);
                    updateModel(index, {
                      apiKey: event.target.value,
                      runtimeProbeComplete: false,
                      verificationStatus: undefined,
                      lastProbe: "unknown",
                      lastProbeMessage: "",
                    })
                  }}
                  placeholder={model.configured ? `已保存：${model.apiKeyPreview}` : "API Key"}
                  aria-label={`${meta.title} API Key`}
                />
              </label>
            </div>
            <div className="village-direct-model-card__actions mt-2 flex items-center justify-between gap-3">
              <label className="flex items-center gap-2 text-xs text-muted-foreground">
                <input
                  type="checkbox"
                  checked={model.enabled}
                  onChange={(event) => updateModel(index, { enabled: event.target.checked })}
                />
                启用
              </label>
              <div className="flex items-center gap-1">
                <select
                  value={requestedProtocol}
                  onChange={(event) => {
                    invalidateModelCatalog(probeKey);
                    updateModel(index, {
                    protocol: normalizeVillageDirectModelProtocol(event.target.value),
                    protocolLabel: undefined,
                    runtimeReady: undefined,
                    runtimeProbeComplete: false,
                    verificationStatus: undefined,
                    lastProbe: "unknown",
                    lastProbeMessage: "",
                    });
                  }}
                  className="h-7 rounded-md border border-white/10 bg-black/20 px-2 text-[11px] text-muted-foreground outline-none"
                  aria-label={`${meta.title}协议`}
                >
                  {protocolOptions.map((option) => (
                    <option key={option.value} value={option.value}>{option.label}</option>
                  ))}
                </select>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="h-7 px-2 text-xs"
                  onClick={() => setDefaultModel(index)}
                  disabled={model.isDefault === true}
                >
                  <Star className="mr-1 size-3.5" /> 设默认
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="h-7 px-2 text-xs"
                  onClick={() => void probeModel(model, index, probeKey)}
                  disabled={isProbing}
                >
                  {isProbing ? (
                    <Loader2 className="mr-1 size-3.5 animate-spin" />
                  ) : (
                    <RotateCw className="mr-1 size-3.5" />
                  )}
                  检测连接
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="h-7 px-2 text-xs"
                  onClick={() => void discoverModels(model, probeKey)}
                  disabled={isDiscovering}
                >
                  {isDiscovering ? <Loader2 className="mr-1 size-3.5 animate-spin" /> : <RotateCw className="mr-1 size-3.5" />}
                  读取上游模型
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  className="h-7 px-2 text-muted-foreground"
                  onClick={() => void copyModel(model)}
                  title="复制配置"
                >
                  <Copy className="size-3.5" />
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  className="h-7 px-2 text-muted-foreground hover:text-destructive"
                  onClick={() => setModels((current) => current.filter((_, itemIndex) => itemIndex !== index))}
                  aria-label={`删除 ${model.label || meta.title}`}
                >
                  <Trash2 className="size-3.5" />
                </Button>
              </div>
            </div>
            {catalog ? (
              <div className="mt-2 flex flex-wrap items-center gap-2 rounded-lg border border-cyan-300/15 bg-cyan-300/[0.04] px-2.5 py-2 text-xs">
                <span className="text-cyan-100/75">已发现 {catalog.models.length} 个模型</span>
                <select
                  defaultValue=""
                  onChange={(event) => {
                    const item = catalog.models.find((candidate) => candidate.id === event.target.value);
                    if (item) importDiscoveredModel(index, item, catalog.protocol);
                  }}
                  className="min-w-[180px] max-w-full rounded-md border border-white/10 bg-black/25 px-2 py-1 text-xs text-foreground outline-none"
                  aria-label="选择上游模型并填入"
                >
                  <option value="">选择模型并填入</option>
                  {catalog.models.map((item) => (
                    <option key={item.id} value={item.id}>{item.id}</option>
                  ))}
                </select>
              </div>
            ) : null}
          </div>
          );
        })}
      </div>

      <div className="village-direct-model-panel__footer flex justify-end rounded-2xl border border-white/10 bg-black/15 p-3">
        <Button
          type="button"
          size="sm"
          onClick={() => void save()}
          disabled={saveDirectModels.isPending}
          className="rounded-full px-5"
        >
          {saveDirectModels.isPending ? <Loader2 className="mr-1 size-3.5 animate-spin" /> : null}
          {`保存${meta.title}`}
        </Button>
      </div>
    </div>
  );
}
export function DirectVideoModelsPanel({
  models: savedModels,
  compact = false,
}: {
  models: DirectVideoModelConfig[];
  compact?: boolean;
}) {
  const saveDirectVideoModels = useSaveDirectVideoModels();
  const probeDirectVideoModel = useProbeDirectVideoModel();
  const [models, setModels] = useState<DirectVideoModelInput[]>([]);
  const [probingModelKeys, setProbingModelKeys] = useState<Set<string>>(() => new Set());
  const [discoveringModelKeys, setDiscoveringModelKeys] = useState<Set<string>>(() => new Set());
  const [discoveredModels, setDiscoveredModels] = useState<
    Record<string, { protocol: string; models: DirectDiscoveredModel[] }>
  >({});
  const [probeResults, setProbeResults] = useState<Record<string, {
    protocol: string;
    verificationStatus: string;
    lastProbe: "ok" | "failed" | "unknown";
    credentialValidationStatus?: "accepted" | "rejected" | "permission_denied" | "unverified";
    message: string;
    capability?: DirectVideoModelProbeResult["capability"];
  }>>({});

  useEffect(() => {
    setModels(
      savedModels.map((model) => ({
        id: model.id,
        label: model.label,
        modelId: model.modelId,
        baseUrl: model.baseUrl,
        apiKey: "",
        protocol: model.requestedProtocol ?? model.protocol ?? "auto",
        enabled: model.enabled,
        isDefault: model.isDefault,
      })),
    );
  }, [savedModels]);

  const updateModel = (index: number, patch: Partial<DirectVideoModelInput>) => {
    setModels((current) => current.map((model, itemIndex) => (
      itemIndex === index ? { ...model, ...patch } : model
    )));
  };

  const invalidateProbeForModel = (probeKey: string) => {
    setProbeResults((current) => {
      if (!(probeKey in current)) return current;
      const next = { ...current };
      delete next[probeKey];
      return next;
    });
  };

  const setDefaultModel = (index: number) => {
    setModels((current) => current.map((model, itemIndex) => ({
      ...model,
      isDefault: itemIndex === index,
    })));
  };

  const save = async () => {
    // 与生图面板同一条确认：空数组会删光整族直连视频模型（含密钥）。
    let confirmClear = false;
    if (models.length === 0 && savedModels.length > 0) {
      const confirmed = window.confirm(
        `这会清空「直连视频模型」里已保存的 ${savedModels.length} 条配置（含密钥），确定吗？`,
      );
      if (!confirmed) return;
      confirmClear = true;
    }
    try {
      const response = await saveDirectVideoModels.mutateAsync({ models, confirmClear });
      if (!response.ok) {
        toast.error(getResponseErrorMessage(response, "直连视频模型保存失败"));
        return;
      }
      toast.success("直连视频模型已同步到画布、工作流和 Agent");
      setProbeResults({});
    } catch (error) {
      toast.error(await getRequestErrorMessage(error, "直连视频模型保存失败"));
    }
  };

  const probe = async (model: DirectVideoModelInput, probeKey: string) => {
    setProbingModelKeys((current) => {
      const next = new Set(current);
      next.add(probeKey);
      return next;
    });
    try {
      const response = await probeDirectVideoModel.mutateAsync(model);
      if (!response.ok) {
        const message = modelConnectionErrorMessage(
          getResponseErrorMessage(response, "直连视频模型检测失败"),
        );
        setProbeResults((current) => ({
          ...current,
          [probeKey]: {
            protocol: "unresolved",
            verificationStatus: "degraded",
            lastProbe: "failed",
            message,
          },
        }));
        toast.error(`连接失败：${message}`);
        return;
      }
      if (!response.data.ok) {
        const message = modelConnectionErrorMessage(response.data.error ?? "未知错误");
        setProbeResults((current) => ({
          ...current,
          [probeKey]: {
            protocol: response.data.protocol || "unresolved",
            verificationStatus: "degraded",
            lastProbe: "failed",
            message,
          },
        }));
        toast.error(`连接失败：${message}`);
        return;
      }
      if (response.data.modelFound) {
        const capability = response.data.capability;
        const credentialStatus = response.data.credentialValidation?.status ?? "unverified";
        const keySource = model.apiKey?.trim() ? "本次填写的 Key" : "已保存的 Key";
        const credentialMessage = credentialStatus === "accepted"
          ? `视频接口接受${keySource}；真实生成尚未验证。${model.apiKey?.trim() ? "请保存后再用于生成。" : ""}`
          : "目录可读，但视频接口鉴权未验证；这不代表当前可以生成视频。";
        const verificationStatus = capability?.verificationStatus ?? "directory-only";
        const capabilityReady = Boolean(
          capability?.modes?.length
          || capability?.sizeSlots?.length
          || capability?.resolutionOptions?.length
          || capability?.durationRange
          || capability?.referenceLimits
          || verificationStatus === "contract-resolved"
          || verificationStatus === "runtime-verified",
        );
        const details = [
          capability?.modes?.length ? `${capability.modes.length} 种生成模式` : "",
          capability?.sizeSlots?.length ? `${capability.sizeSlots.length} 个尺寸档位` : "",
          capability?.durationRange ? `${capability.durationRange[0]}-${capability.durationRange[1]} 秒` : "",
        ].filter(Boolean);
        setProbeResults((current) => ({
          ...current,
          [probeKey]: {
            protocol: response.data.protocol,
            verificationStatus,
            lastProbe: "ok",
            credentialValidationStatus: credentialStatus,
            message: capabilityReady
              ? details.length > 0
                ? `已识别 ${details.join("、")}。${credentialMessage}`
                : `模型 ID 已匹配。${credentialMessage}`
              : "模型 ID 已匹配，但上游目录没有返回视频能力字段；当前不会映射到视频节点。",
            capability,
          },
        }));
        if (credentialStatus === "accepted") {
          toast.success(
            `视频接口接受${keySource}${model.apiKey?.trim() ? "；请保存设置后用于生成" : ""}`,
          );
        } else {
          toast.warning(
            capabilityReady
              ? `模型目录已匹配，但视频接口鉴权未验证：${model.modelId}`
              : `模型目录已匹配，但缺少视频能力证据：${model.modelId}`,
          );
        }
        return;
      }
      const message = "模型目录可达，但模型 ID 未匹配。请核对模型 ID。";
      setProbeResults((current) => ({
        ...current,
        [probeKey]: {
          protocol: response.data.protocol || "unresolved",
          verificationStatus: "metadata",
          lastProbe: "failed",
          message,
        },
      }));
      toast.warning(message);
    } catch (error) {
      const message = modelConnectionErrorMessage(
        await getRequestErrorMessage(error, "直连视频模型检测失败"),
      );
      setProbeResults((current) => ({
        ...current,
        [probeKey]: {
          protocol: "unresolved",
          verificationStatus: "degraded",
          lastProbe: "failed",
          message,
        },
      }));
      toast.error(`连接失败：${message}`);
    } finally {
      setProbingModelKeys((current) => {
        const next = new Set(current);
        next.delete(probeKey);
        return next;
      });
    }
  };
  const discover = async (model: DirectVideoModelInput, probeKey: string) => {
    const baseUrl = model.baseUrl.trim().replace(/\/+$/, "");
    const apiKey = (model.apiKey ?? "").trim();
    const saved = model.id ? savedModels.find((item) => item.id === model.id) : undefined;
    const savedForEndpoint = saved && sameDirectModelEndpoint(saved.baseUrl, model.baseUrl) ? saved : undefined;
    if (!baseUrl || (!apiKey && !savedForEndpoint?.configured)) {
      toast.error("读取模型目录需要 Base URL 和 API Key");
      return;
    }
    setDiscoveringModelKeys((current) => new Set(current).add(probeKey));
    try {
      const response = await discoverDirectVideoModels({
        id: model.id,
        baseUrl,
        apiKey,
        protocol: model.protocol,
      });
      if (!response.ok || !response.data.ok) {
        const message = modelConnectionErrorMessage(
          response.ok ? response.data.error ?? "上游没有可导入的视频模型" : getResponseErrorMessage(response, "读取视频模型目录失败"),
        );
        toast.error(`读取失败：${message}`);
        return;
      }
      setDiscoveredModels((current) => ({
        ...current,
        [probeKey]: {
          protocol: response.data.detectedProtocol ?? response.data.protocol,
          models: response.data.models,
        },
      }));
      toast.success(`已读取 ${response.data.models.length} 个上游视频模型`);
    } catch (error) {
      toast.error(`读取失败：${modelConnectionErrorMessage(error)}`);
    } finally {
      setDiscoveringModelKeys((current) => {
        const next = new Set(current);
        next.delete(probeKey);
        return next;
      });
    }
  };
  const importDiscoveredVideoModel = (index: number, model: DirectDiscoveredModel, protocol: string) => {
    const probeKey = models[index]?.id ?? `new-${index}`;
    invalidateProbeForModel(probeKey);
    const displayName = typeof model.metadata.displayName === "string" ? model.metadata.displayName.trim() : "";
    updateModel(index, {
      modelId: model.id,
      label: displayName || model.id,
      protocol,
    });
    toast.success(`已填入 ${model.id}，请继续检测连接`);
  };
  return (
    <div className={cn("village-direct-model-panel space-y-3", !compact && "mt-6")}>
      <div className="village-direct-model-panel__head">
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="shrink-0"
          onClick={() => setModels((current) => [
            ...current,
            {
              label: "",
              modelId: "",
              baseUrl: "",
              apiKey: "",
              protocol: "auto",
              enabled: true,
              isDefault: current.length === 0,
            },
          ])}
        >
          <Plus className="mr-1 size-3.5" /> 添加模型
        </Button>
      </div>

      <div className="village-direct-model-grid grid gap-3 xl:grid-cols-2">
        {models.length === 0 ? (
          <div className="rounded-2xl border border-dashed border-white/15 bg-black/15 px-4 py-6 text-center text-xs text-muted-foreground xl:col-span-2">
            暂无直连模型。添加后只需填写名称、模型 ID、Base URL 与 Key。
          </div>
        ) : models.map((model, index) => {
          const saved = model.id ? savedModels.find((item) => item.id === model.id) : undefined;
          const savedForEndpoint =
            saved && sameDirectModelEndpoint(saved.baseUrl, model.baseUrl) ? saved : undefined;
          const probeKey = model.id ?? `new-${index}`;
          const isProbing = probingModelKeys.has(probeKey);
          const isDiscovering = discoveringModelKeys.has(probeKey);
          const catalog = discoveredModels[probeKey];
          const detectedProtocol = probeResults[probeKey]?.protocol
            ?? savedForEndpoint?.detectedProtocol
            ?? savedForEndpoint?.protocol
            ?? "";
          const verificationStatus = probeResults[probeKey]?.verificationStatus
            ?? savedForEndpoint?.verificationStatus
            ?? "unverified";
          const probeResult = probeResults[probeKey];
          const protocolLabel = detectedProtocol === "minimax-video-v2"
            ? "MiniMax Video v2"
            : detectedProtocol === "autodl-comfyui"
              ? "AutoDL ComfyUI API"
            : detectedProtocol === "openai-video"
              ? "OpenAI Video"
              : detectedProtocol === "unresolved"
                ? "协议待适配"
              : "待识别";
          const connection = modelConnectionSummary({
            configured: savedForEndpoint?.configured,
            enabled: model.enabled,
            lastProbe: probeResult?.lastProbe ?? "unknown",
            credentialValidated: probeResult?.credentialValidationStatus === "accepted",
            runtimeReady: probeResult
              ? probeResult.lastProbe === "ok"
                && probeResult.credentialValidationStatus === "accepted"
                && (
                  probeResult.verificationStatus === "metadata"
                  || probeResult.verificationStatus === "contract-resolved"
                  || probeResult.verificationStatus === "runtime-verified"
                )
              : savedForEndpoint?.runtimeReady === true,
            verificationStatus: verificationStatus as DirectModelConfig["verificationStatus"],
            message: probeResult?.message ?? savedForEndpoint?.disabledReason,
            capabilitySource: videoCapabilitySourceFor(
              probeResult?.capability,
              savedForEndpoint,
              model.modelId,
            ),
          });
          return (
            <div key={model.id ?? `new-${index}`} className="village-direct-model-card rounded-2xl border border-white/10 bg-[linear-gradient(180deg,rgba(255,255,255,0.055),rgba(255,255,255,0.02))] p-4 shadow-[0_14px_40px_rgba(0,0,0,0.16)]">
              <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className="rounded-full border border-white/10 bg-white/[0.055] px-2 py-0.5 text-[10px] font-medium text-muted-foreground">视频 {index + 1}</span>
                  {savedForEndpoint?.configured ? (
                    <span className="rounded-full border border-emerald-300/25 bg-emerald-300/10 px-2 py-0.5 text-[10px] font-medium text-emerald-200">已保存</span>
                  ) : null}
                  {model.enabled ? (
                    <span className="rounded-full border border-indigo-300/25 bg-indigo-300/10 px-2 py-0.5 text-[10px] font-medium text-indigo-200">启用</span>
                  ) : null}
                  {model.isDefault ? (
                    <span className="inline-flex items-center gap-1 rounded border border-amber-400/40 bg-amber-400/10 px-2 py-0.5 text-[10px] font-medium text-amber-300">
                      <Star className="size-3" /> 默认
                    </span>
                  ) : null}
                  <VideoModelHeaderChips protocolLabel={protocolLabel} saved={savedForEndpoint} />
                </div>
              </div>
              <ModelConnectionStatus {...connection} />
              <VideoModelCapabilityPreview
                saved={savedForEndpoint}
                probed={probeResult?.lastProbe === "ok" ? probeResult.capability : undefined}
              />
              <div className="village-direct-model-card__fields grid gap-2.5 md:grid-cols-2">
                <label>
                  <span>模型名称</span>
                  <Input
                    value={model.label}
                    onChange={(event) => updateModel(index, { label: event.target.value })}
                    placeholder="例如：Seedance 2.5 高质量"
                    aria-label="直连视频模型名称"
                  />
                </label>
                <label>
                  <span>模型 ID</span>
                  <Input
                    value={model.modelId}
                    onChange={(event) => {
                      invalidateProbeForModel(probeKey);
                      updateModel(index, { modelId: event.target.value });
                    }}
                    placeholder={model.protocol === "autodl-comfyui" ? "工作流 ID，例如 minimax_h3_lightx2v_no_pic" : "上游模型 ID"}
                    aria-label="直连视频模型 ID"
                  />
                </label>
                <label>
                  <span>Base URL</span>
                  <Input
                    value={model.baseUrl}
                    onChange={(event) => {
                      invalidateProbeForModel(probeKey);
                      updateModel(index, {
                        baseUrl: event.target.value,
                        ...(!sameDirectModelEndpoint(model.baseUrl, event.target.value)
                          ? { apiKey: "" }
                          : {}),
                      });
                    }}
                    placeholder={model.protocol === "autodl-comfyui" ? "https://autodl.art" : "http://127.0.0.1:9800/v1"}
                    aria-label="直连视频模型 Base URL"
                  />
                </label>
                <label>
                  <span>API Key</span>
                  <Input
                    value={model.apiKey ?? ""}
                    type="password"
                    autoComplete="new-password"
                    data-1p-ignore="true"
                    data-lpignore="true"
                    onChange={(event) => updateModel(index, { apiKey: event.target.value })}
                    placeholder={
                      savedForEndpoint?.configured
                        ? `已保存：${savedForEndpoint.apiKeyPreview}`
                        : "API Key"
                    }
                    aria-label="直连视频模型 API Key"
                  />
                </label>
              </div>
              <div className="village-direct-model-card__actions mt-2 flex flex-wrap items-center justify-between gap-3">
                <div className="flex flex-wrap items-center gap-3">
                  <select
                    value={model.protocol ?? "auto"}
                    onChange={(event) => {
                      invalidateProbeForModel(probeKey);
                      updateModel(index, { protocol: event.target.value });
                    }}
                    aria-label="直连视频模型协议"
                    className="h-8 rounded-md border border-white/10 bg-black/20 px-2 text-xs text-foreground outline-none"
                  >
                    <option value="auto">协议自动识别</option>
                    <option value="openai-video">OpenAI Video</option>
                    <option value="minimax-video-v2">MiniMax Video v2</option>
                    <option value="autodl-comfyui">AutoDL ComfyUI API</option>
                  </select>
                  <label className="flex items-center gap-2 text-xs text-muted-foreground">
                    <input
                      type="checkbox"
                      checked={model.enabled}
                      onChange={(event) => updateModel(index, { enabled: event.target.checked })}
                    />
                    启用
                  </label>
                  <button
                    type="button"
                    className="text-xs text-muted-foreground hover:text-amber-300"
                    onClick={() => setDefaultModel(index)}
                  >
                    {model.isDefault ? "当前默认" : "设为默认"}
                  </button>
                </div>
                <div className="flex items-center gap-1">
                <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    className="h-7 px-2 text-xs"
                    onClick={() => void probe(model, probeKey)}
                    disabled={isProbing}
                  >
                    {isProbing ? <Loader2 className="mr-1 size-3.5 animate-spin" /> : <RotateCw className="mr-1 size-3.5" />}
                  检测连接
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="h-7 px-2 text-xs"
                  onClick={() => void discover(model, probeKey)}
                  disabled={isDiscovering}
                >
                  {isDiscovering ? <Loader2 className="mr-1 size-3.5 animate-spin" /> : <RotateCw className="mr-1 size-3.5" />}
                  读取上游模型
                </Button>
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    className="h-7 px-2 text-muted-foreground hover:text-destructive"
                    onClick={() => setModels((current) => current.filter((_, itemIndex) => itemIndex !== index))}
                    aria-label={`删除 ${model.label || "直连视频模型"}`}
                  >
                    <Trash2 className="size-3.5" />
                  </Button>
              </div>
            </div>
            {catalog ? (
              <div className="mt-2 flex flex-wrap items-center gap-2 rounded-lg border border-cyan-300/15 bg-cyan-300/[0.04] px-2.5 py-2 text-xs">
                <span className="text-cyan-100/75">已发现 {catalog.models.length} 个视频模型</span>
                <select
                  defaultValue=""
                  onChange={(event) => {
                    const item = catalog.models.find((candidate) => candidate.id === event.target.value);
                    if (item) importDiscoveredVideoModel(index, item, catalog.protocol);
                  }}
                  className="min-w-[180px] max-w-full rounded-md border border-white/10 bg-black/25 px-2 py-1 text-xs text-foreground outline-none"
                  aria-label="选择上游视频模型并填入"
                >
                  <option value="">选择模型并填入</option>
                  {catalog.models.map((item) => (
                    <option key={item.id} value={item.id}>{item.id}</option>
                  ))}
                </select>
              </div>
            ) : null}
          </div>
          );
        })}
      </div>

      <div className="village-direct-model-panel__footer flex justify-end rounded-2xl border border-white/10 bg-black/15 p-3">
        <Button type="button" size="sm" onClick={() => void save()} disabled={saveDirectVideoModels.isPending} className="rounded-full px-5">
          {saveDirectVideoModels.isPending ? <Loader2 className="mr-1 size-3.5 animate-spin" /> : null}
          保存直连视频模型
        </Button>
      </div>
    </div>
  );
}

function OfficialGatewayPanel({
  config,
  loading,
}: {
  config: ModelGatewayConfig | undefined;
  loading: boolean;
}) {
  const { t } = useTranslation();
  const unified = config?.unified;
  const enableUnified = useEnableUnified();
  const saveUnified = useSaveUnifiedConfig();

  const [apiKey, setApiKey] = useState("");
  const [revealKey, setRevealKey] = useState(false);
  const savedApiKeyPreview = unified?.configured ? unified.apiKeyPreview : "";

  const handleSave = async () => {
    const trimmedApiKey = apiKey.trim();
    try {
      if (!trimmedApiKey) {
        if (!unified?.configured) {
          toast.error(t("settings.modelConfig.official.missingFields"));
          return;
        }
        const response = await enableUnified.mutateAsync();
        if (!response.ok) {
          toast.error(getResponseErrorMessage(response, t("settings.modelConfig.requestFailed")));
          return;
        }
        toast.success(t("settings.modelConfig.official.saved"));
        return;
      }
      const response = await saveUnified.mutateAsync({
        newApiApiKey: trimmedApiKey,
      });
      if (!response.ok) {
        toast.error(getResponseErrorMessage(response, t("settings.modelConfig.requestFailed")));
        return;
      }
      setApiKey("");
      setRevealKey(false);
      toast.success(t("settings.modelConfig.official.saved"));
    } catch (error) {
      toast.error(await getRequestErrorMessage(error, t("settings.modelConfig.requestFailed")));
    }
  };

  return (
    <div className="space-y-3">
      <p className="text-xs leading-relaxed text-muted-foreground">
        {t("settings.modelConfig.unified.description")}
      </p>

      <div className="space-y-2.5">
        <div className="grid grid-cols-[120px_1fr] items-center gap-3">
          <Label className="justify-start text-[11px] font-normal tracking-wide text-muted-foreground uppercase">
            {t("settings.modelConfig.fields.apiKey")}
          </Label>
          <div className="relative">
            <Input
              name="village-canvas-unified-gateway-api-key"
              autoComplete="new-password"
              data-1p-ignore="true"
              data-lpignore="true"
              type={revealKey ? "text" : "password"}
              value={apiKey}
              onChange={(e) => {
                setApiKey(e.target.value);
                if (!e.target.value) setRevealKey(false);
              }}
              placeholder={
                savedApiKeyPreview
                  ? t("settings.secretSavedPlaceholder", { preview: savedApiKeyPreview })
                  : "sk-..."
              }
              autoCapitalize="none"
              spellCheck={false}
              className={cn(
                "h-9 rounded-md border-input/80 focus-visible:border-ring/70 focus-visible:ring-1 focus-visible:ring-ring/30",
                apiKey ? "pr-9" : savedApiKeyPreview ? "pr-16" : "",
              )}
            />
            {apiKey ? (
              <button
                type="button"
                onClick={() => setRevealKey((r) => !r)}
                aria-label={
                  revealKey
                    ? t("settings.mediaStorage.hideSecret")
                    : t("settings.mediaStorage.showSecret")
                }
                className="absolute top-1/2 right-2 -translate-y-1/2 text-muted-foreground transition-colors hover:text-foreground"
              >
                {revealKey ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
              </button>
            ) : savedApiKeyPreview ? (
              <span className="absolute top-1/2 right-2 -translate-y-1/2 rounded bg-emerald-400/10 px-1.5 py-0.5 text-[10px] font-medium text-emerald-400">
                {t("settings.secretSavedBadge")}
              </span>
            ) : null}
          </div>
        </div>
      </div>

      <div className="flex justify-end">
        <Button
          type="button"
          size="sm"
          onClick={handleSave}
          disabled={loading || saveUnified.isPending || enableUnified.isPending}
        >
          {saveUnified.isPending || enableUnified.isPending ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : null}
          {t("settings.modelConfig.official.save")}
        </Button>
      </div>
    </div>
  );
}

function CustomGatewayPanel({
  config,
  loading,
  baseUrl,
}: {
  config: ModelGatewayConfig | undefined;
  loading: boolean;
  baseUrl: string;
}) {
  const { t } = useTranslation();
  const initCustom = useInitCustomNewApi();
  const [setupPassword, setSetupPassword] = useState("");
  const [setupConfirmPassword, setSetupConfirmPassword] = useState("");
  const [initError, setInitError] = useState("");
  const [initNotice, setInitNotice] = useState("");

  const showInitError = (message: string) => {
    setInitError(message);
    setInitNotice("");
    toast.error(message);
  };

  const showInitResponseError = (message: string) => {
    const displayMessage =
      message.includes("setupUsername") || message.includes("NewAPI is not initialized")
        ? t("settings.modelConfig.custom.setupRequired")
        : message;
    showInitError(displayMessage);
  };

  const handleInit = async () => {
    setInitError("");
    setInitNotice("");
    const trimmedSetupPassword = setupPassword.trim();
    const trimmedSetupConfirmPassword = setupConfirmPassword.trim();
    const hasSetupPassword = Boolean(trimmedSetupPassword || trimmedSetupConfirmPassword);
    if (hasSetupPassword && (!trimmedSetupPassword || !trimmedSetupConfirmPassword)) {
      showInitError(t("settings.modelConfig.custom.setupPasswordIncomplete"));
      return;
    }
    if (hasSetupPassword && trimmedSetupPassword.length < 8) {
      showInitError(t("settings.modelConfig.custom.setupPasswordTooShort"));
      return;
    }
    if (hasSetupPassword && trimmedSetupPassword !== trimmedSetupConfirmPassword) {
      showInitError(t("settings.modelConfig.custom.setupPasswordMismatch"));
      return;
    }

    try {
      const response = await initCustom.mutateAsync(
        {
          ...(baseUrl.trim() ? { newApiBaseUrl: baseUrl.trim() } : {}),
          ...(hasSetupPassword
            ? {
                setupUsername: "root",
                setupPassword: trimmedSetupPassword,
                setupConfirmPassword: trimmedSetupConfirmPassword,
              }
            : {}),
        },
      );
      if (response.ok !== true) {
        showInitResponseError(
          getResponseErrorMessage(response, t("settings.modelConfig.requestFailed")),
        );
        return;
      }
      const passwordIgnored =
        hasSetupPassword && response.data.newApiSetup?.alreadyInitialized === true;
      setSetupPassword("");
      setSetupConfirmPassword("");
      if (passwordIgnored) {
        setInitNotice(t("settings.modelConfig.custom.setupAlreadyInitializedPasswordIgnored"));
      }
      toast.success(t("settings.modelConfig.custom.initialized"));
    } catch (error) {
      const message = await getRequestErrorMessage(error, t("settings.modelConfig.requestFailed"));
      showInitResponseError(message);
    }
  };

  const databaseStatus = config?.provisioner?.database;
  const databaseReady = Boolean(databaseStatus?.available);
  const customConfigured = Boolean(config?.custom?.configured);

  return (
    <div className="space-y-3">
      <p className="text-xs leading-relaxed text-muted-foreground">
        {t("settings.modelConfig.custom.description")}
      </p>

      <div className="rounded-md border border-border/70 p-3">
        <div className="flex items-center justify-between gap-3">
          <p className="text-[11px] font-medium tracking-wide text-muted-foreground uppercase">
            {t("settings.modelConfig.custom.localStatusTitle")}
          </p>
          <span className={cn("text-[11px]", customConfigured ? "text-emerald-400" : "text-amber-300")}>
            {customConfigured
              ? t("settings.modelConfig.custom.localReady")
              : t("settings.modelConfig.custom.localNeedsInit")}
          </span>
        </div>
        <p className="mt-1.5 text-[11px] leading-relaxed text-muted-foreground">
          {databaseReady
            ? t("settings.modelConfig.custom.sqliteReady")
            : t("settings.modelConfig.custom.sqliteWaiting")}
        </p>
      </div>

      {!customConfigured ? (
        <div className="rounded-md border border-border/70 p-3">
          <p className="text-[11px] font-medium tracking-wide text-muted-foreground uppercase">
            {t("settings.modelConfig.custom.setupAdminTitle")}
          </p>
          <p className="mt-1.5 text-[11px] leading-relaxed text-muted-foreground">
            {t("settings.modelConfig.custom.setupAdminDescription")}
          </p>
          <div className="mt-3 space-y-2.5">
            <FieldRow
              secret
              name="newapi-setup-password"
              autoComplete="new-password"
              label={t("settings.modelConfig.custom.setupPassword")}
              value={setupPassword}
              onChange={setSetupPassword}
              placeholder={t("settings.modelConfig.custom.setupPasswordPlaceholder")}
            />
            <FieldRow
              secret
              name="newapi-setup-password-confirmation"
              autoComplete="new-password"
              label={t("settings.modelConfig.custom.setupConfirmPassword")}
              value={setupConfirmPassword}
              onChange={setSetupConfirmPassword}
              placeholder={t("settings.modelConfig.custom.setupConfirmPasswordPlaceholder")}
            />
          </div>
          <p className="mt-2 text-[11px] leading-relaxed text-muted-foreground">
            {t("settings.modelConfig.custom.setupPasswordOnlyOnce")}
          </p>
        </div>
      ) : null}

      {initError ? (
        <p
          role="alert"
          aria-live="polite"
          className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-[11px] leading-relaxed text-destructive"
        >
          {initError}
        </p>
      ) : null}
      {!initError && initNotice ? (
        <p
          role="status"
          aria-live="polite"
          className="rounded-md border border-amber-400/35 bg-amber-400/10 px-3 py-2 text-[11px] leading-relaxed text-amber-200"
        >
          {initNotice}
        </p>
      ) : null}

      <div className="flex justify-end">
        <Button type="button" size="sm" onClick={handleInit} disabled={loading || initCustom.isPending}>
          {initCustom.isPending ? <Loader2 className="size-3.5 animate-spin" /> : null}
          {t(
            customConfigured
              ? "settings.modelConfig.custom.repair"
              : "settings.modelConfig.custom.init",
          )}
        </Button>
      </div>
    </div>
  );
}

// 功能模型映射每行的三列栅格：功能 | 供应商 | 模型名称。表头与行共用同一模板以对齐。
const FEATURE_ROW_GRID =
  "grid grid-cols-[minmax(0,1fr)_150px_minmax(0,1fr)] items-center gap-3";

function splitFeatureModelGroups(
  groups: readonly FeatureModelGroup[],
  predicate: (feature: FeatureModelDef) => boolean,
): FeatureModelGroup[] {
  return groups.map((group) => ({
    ...group,
    features: group.features.filter(predicate),
  })).filter((group) => group.features.length > 0);
}

const MEDIA_MODEL_ROWS: readonly {
  model: string;
  kind: "image" | "video" | "audio";
  officialOnly?: boolean;
}[] = [
  { model: "LingShan-G2", kind: "image" },
  { model: "LingShan-NB-2", kind: "image" },
  { model: "seedance-1.0-pro-fast", kind: "video" },
  { model: "seedance-1.5-pro", kind: "video" },
  { model: "seedance-2.0", kind: "video" },
  { model: "seedance-2.0-fast", kind: "video" },
  { model: "happyhorse-1.0", kind: "video" },
  { model: "index-tts-2", kind: "audio" },
  { model: "LingShan-MU-11", kind: "audio" },
  { model: "seedance-2.0-value", kind: "video", officialOnly: true },
  { model: "seedance-2.0-fast-value", kind: "video", officialOnly: true },
];

const MEDIA_ROW_GRID =
  "grid grid-cols-[90px_minmax(0,1fr)_150px_minmax(0,1fr)] items-center gap-3";

const DEFAULT_EMBEDDING_DIMENSION = 1024;
const DEFAULT_EMBEDDING_BATCH_SIZE = 10;

const FEATURE_PROVIDER_LABELS: Record<FeatureModelProvider, string> = {
  openai: "OpenAI",
  midjourney: "Midjourney",
  azure: "Azure",
  ollama: "Ollama",
  midjourneyplus: "MidjourneyPlus",
  openaimax: "OpenAIMax",
  ohmygpt: "OhMyGPT",
  custom: "Custom",
  ails: "AILS",
  aiproxy: "AIProxy",
  palm: "PaLM",
  api2gpt: "API2GPT",
  aigc2d: "AIGC2D",
  anthropic: "Anthropic",
  baidu: "Baidu",
  zhipu: "Zhipu",
  ali: "Ali",
  xunfei: "Xunfei",
  '360': "360",
  openrouter: "OpenRouter",
  aiproxylibrary: "AIProxyLibrary",
  fastgpt: "FastGPT",
  tencent: "Tencent",
  gemini: "Gemini",
  moonshot: "Moonshot",
  zhipuv4: "ZhipuV4",
  perplexity: "Perplexity",
  lingyiwanwu: "LingYiWanWu",
  aws: "AWS",
  cohere: "Cohere",
  minimax: "MiniMax",
  sunoapi: "SunoAPI",
  dify: "Dify",
  jina: "Jina",
  cloudflare: "Cloudflare",
  siliconflow: "SiliconFlow",
  vertexai: "VertexAI",
  mistral: "Mistral",
  deepseek: "DeepSeek",
  mokaai: "MokaAI",
  volcengine: "VolcEngine",
  baiduv2: "BaiduV2",
  xinference: "Xinference",
  xai: "xAI",
  coze: "Coze",
  kling: "Kling",
  jimeng: "Jimeng",
  vidu: "Vidu",
  submodel: "Submodel",
  doubaovideo: "DoubaoVideo",
  sora: "Sora",
  replicate: "Replicate",
};


function FeatureModelsBlock({
  newApiBaseUrl,
  database,
  savedProviderChannels,
  savedEmbeddingModel,
  savedMediaModels,
}: {
  newApiBaseUrl: string;
  database: NewApiDatabaseConfigInput | undefined;
  savedProviderChannels: SavedProviderChannelConfig[];
  savedEmbeddingModel: SavedEmbeddingModelConfig | undefined;
  savedMediaModels: Record<string, { provider: string; upstreamModel: string }>;
}) {
  const { t } = useTranslation();
  const featureModels = useSettingsStore((s) => s.featureModelConfig.featureModels);
  const providerChannels = useSettingsStore((s) => s.featureModelConfig.providerChannels);
  const saveBatch = useSaveCustomChannelsBatch();
  const addFeatureProviderChannel = useSettingsStore((s) => s.addFeatureProviderChannel);
  const updateFeatureProviderChannel = useSettingsStore((s) => s.updateFeatureProviderChannel);

  const configuredProviders = useMemo(
    () => FEATURE_MODEL_PROVIDERS.filter((p) => Boolean(providerChannels[p])),
    [providerChannels],
  );
  const textFeatureGroups = useMemo(
    () =>
      splitFeatureModelGroups(
        FEATURE_MODEL_PRODUCT_GROUPS,
        (feature) => !feature.requiresVision && feature.id !== "COGNEE",
      ),
    [],
  );
  const visionFeatureGroups = useMemo(
    () =>
      splitFeatureModelGroups(
        FEATURE_MODEL_PRODUCT_GROUPS,
        (feature) => Boolean(feature.requiresVision),
      ),
    [],
  );
  const savedChannelByProvider = useMemo(() => {
    return new Map(savedProviderChannels.map((channel) => [channel.provider, channel]));
  }, [savedProviderChannels]);

  const savedProviderChannelsKey = JSON.stringify(savedProviderChannels);
  const lastSyncedProviderChannelsKey = useRef("");
  useEffect(() => {
    if (lastSyncedProviderChannelsKey.current === savedProviderChannelsKey) return;
    lastSyncedProviderChannelsKey.current = savedProviderChannelsKey;
    for (const channel of savedProviderChannels) {
      if (!FEATURE_MODEL_PROVIDERS.includes(channel.provider as FeatureModelProvider)) continue;
      const provider = channel.provider as FeatureModelProvider;
      const current = useSettingsStore.getState().featureModelConfig.providerChannels?.[provider];
      const savedBaseUrl = channel.baseUrl ?? "";
      if (!current) {
        addFeatureProviderChannel(provider);
        if (savedBaseUrl) {
          updateFeatureProviderChannel(provider, { baseUrl: savedBaseUrl });
        }
        continue;
      }
      if (current.baseUrl !== savedBaseUrl && !current.upstreamKey) {
        updateFeatureProviderChannel(provider, { baseUrl: savedBaseUrl });
      }
    }
  }, [addFeatureProviderChannel, savedProviderChannels, savedProviderChannelsKey, updateFeatureProviderChannel]);

  // 把功能行按 provider 分组拼成渠道：modelMapping = { DC内部模型名: 上游模型名 }。
  const buildChannels = (): CustomChannelInput[] => {
    const byProvider = new Map<FeatureModelProvider, Record<string, string>>();
    for (const group of FEATURE_MODEL_GROUPS) {
      for (const feature of group.features) {
        const entry = featureModels[feature.id];
        if (!entry || !entry.model.trim() || !providerChannels[entry.provider]) continue;
        const mapping = byProvider.get(entry.provider) ?? {};
        mapping[feature.defaultModel] = entry.model.trim();
        byProvider.set(entry.provider, mapping);
      }
    }
    return [...byProvider.entries()].map(([provider, modelMapping]) => {
      const channel = providerChannels[provider];
      return {
        provider,
        upstreamKey: (channel?.upstreamKey ?? "").trim(),
        modelMapping,
        group: "default",
        priority: 0,
        weight: 0,
        baseUrl: (channel?.baseUrl ?? "").trim(),
        testModel: "",
      };
    });
  };

  const handleSave = async () => {
    if (configuredProviders.length === 0) {
      toast.error(t("settings.modelConfig.featureModels.noChannels"));
      return;
    }
    const channels = buildChannels();
    if (channels.length === 0) {
      toast.error(t("settings.modelConfig.featureModels.noMappings"));
      return;
    }
    if (!newApiBaseUrl.trim()) {
      toast.error(t("settings.modelConfig.featureModels.missingBaseUrl"));
      return;
    }
    const missing = channels
      .filter((c) => {
        if (c.upstreamKey) return false;
        return !savedChannelByProvider.get(c.provider)?.configured;
      })
      .map((c) => FEATURE_PROVIDER_LABELS[c.provider as FeatureModelProvider]);
    if (missing.length > 0) {
      toast.error(
        t("settings.modelConfig.featureModels.missingKeys", { providers: missing.join("、") }),
      );
      return;
    }
    try {
      const res = await saveBatch.mutateAsync({
        newApiBaseUrl: newApiBaseUrl.trim(),
        ...(database ? { database } : {}),
        channels,
      });
      if (!res.ok) {
        toast.error(res.error);
        return;
      }
      const { succeeded, failed } = res.data;
      if (failed > 0) {
        toast.warning(
          t("settings.modelConfig.featureModels.savedPartial", { succeeded, failed }),
        );
      } else {
        toast.success(t("settings.modelConfig.featureModels.saved", { count: succeeded }));
      }
    } catch {
      toast.error(t("settings.modelConfig.requestFailed"));
    }
  };

  return (
    <>
      <ProviderChannelsBlock
        savedProviderChannels={savedProviderChannels}
        newApiBaseUrl={newApiBaseUrl}
        database={database}
      />

      <CogneeModelsBlock
        configuredProviders={configuredProviders}
        newApiBaseUrl={newApiBaseUrl}
        database={database}
        providerChannels={providerChannels}
        savedChannelByProvider={savedChannelByProvider}
        savedEmbeddingModel={savedEmbeddingModel}
      />

      {/* 功能模型映射 */}
      <h4 className="mt-5 text-xs font-medium text-foreground">
        {t("settings.modelConfig.featureModels.title")}
      </h4>
      <p className="mt-1.5 text-[11px] leading-relaxed text-muted-foreground">
        {t("settings.modelConfig.featureModels.description")}
      </p>

      <FeatureModelCapabilitySection
        title={t("settings.modelConfig.featureModels.textModelsTitle")}
        groups={textFeatureGroups}
        newApiBaseUrl={newApiBaseUrl}
        database={database}
        configuredProviders={configuredProviders}
        providerChannels={providerChannels}
        savedChannelByProvider={savedChannelByProvider}
      />

      <FeatureModelCapabilitySection
        title={t("settings.modelConfig.featureModels.multimodalModelsTitle")}
        hint={t("settings.modelConfig.featureModels.visionRequiredHint")}
        groups={visionFeatureGroups}
        newApiBaseUrl={newApiBaseUrl}
        database={database}
        configuredProviders={configuredProviders}
        providerChannels={providerChannels}
        savedChannelByProvider={savedChannelByProvider}
      />

      <div className="mt-3 flex items-center justify-end gap-3">
        <p className="text-[11px] leading-relaxed text-muted-foreground">
          {t("settings.modelConfig.featureModels.saveHint")}
        </p>
        <Button
          type="button"
          size="sm"
          className="shrink-0"
          onClick={handleSave}
          disabled={saveBatch.isPending}
        >
          {saveBatch.isPending ? <Loader2 className="size-3.5 animate-spin" /> : null}
          {t("settings.modelConfig.featureModels.save")}
        </Button>
      </div>

      <MediaModelsBlock
        configuredProviders={configuredProviders}
        newApiBaseUrl={newApiBaseUrl}
        database={database}
        savedChannelByProvider={savedChannelByProvider}
        savedMediaModels={savedMediaModels}
      />
    </>
  );
}

function CogneeModelsBlock({
  configuredProviders,
  newApiBaseUrl,
  database,
  providerChannels,
  savedChannelByProvider,
  savedEmbeddingModel,
}: {
  configuredProviders: readonly FeatureModelProvider[];
  newApiBaseUrl: string;
  database: NewApiDatabaseConfigInput | undefined;
  providerChannels: Record<string, { upstreamKey: string; baseUrl: string }>;
  savedChannelByProvider: Map<string, SavedProviderChannelConfig>;
  savedEmbeddingModel: SavedEmbeddingModelConfig | undefined;
}) {
  const { t } = useTranslation();

  return (
    <div className="mt-6 rounded-md border border-border/70 px-3 py-3">
      <h4 className="text-xs font-medium text-foreground">
        {t("settings.modelConfig.featureModels.groups.novelImport")}
      </h4>
      <p className="mt-1.5 text-[11px] leading-relaxed text-muted-foreground">
        {t("settings.modelConfig.featureModels.cogneeDescription")}
      </p>

      <div
        className={cn(
          FEATURE_ROW_GRID,
          "mt-3 text-[11px] font-medium tracking-wide text-muted-foreground uppercase",
        )}
      >
        <span>{t("settings.modelConfig.featureModels.colFeature")}</span>
        <span>{t("settings.modelConfig.featureModels.colProvider")}</span>
        <span>{t("settings.modelConfig.featureModels.colModel")}</span>
      </div>
      <div className="mt-2">
        <FeatureModelRow
          featureId="COGNEE"
          defaultModel="DC-cognee-LLM"
          requiresVision={false}
          newApiBaseUrl={newApiBaseUrl}
          database={database}
          configuredProviders={configuredProviders}
          providerChannels={providerChannels}
          savedChannelByProvider={savedChannelByProvider}
        />
      </div>

      <EmbeddingModelBlock
        configuredProviders={configuredProviders}
        newApiBaseUrl={newApiBaseUrl}
        database={database}
        savedChannelByProvider={savedChannelByProvider}
        savedEmbeddingModel={savedEmbeddingModel}
      />
    </div>
  );
}

function EmbeddingModelBlock({
  configuredProviders,
  newApiBaseUrl,
  database,
  savedChannelByProvider,
  savedEmbeddingModel,
}: {
  configuredProviders: readonly FeatureModelProvider[];
  newApiBaseUrl: string;
  database: NewApiDatabaseConfigInput | undefined;
  savedChannelByProvider: Map<string, SavedProviderChannelConfig>;
  savedEmbeddingModel: SavedEmbeddingModelConfig | undefined;
}) {
  const { t } = useTranslation();
  const localSavedEmbeddingModel = useSettingsStore((s) => s.featureModelConfig.embeddingModel);
  const setEmbeddingModel = useSettingsStore((s) => s.setEmbeddingModel);
  const saveEmbeddingModel = useSaveEmbeddingModel();
  const [localModel, setLocalModel] = useState<EmbeddingModelEntry | undefined>(
    localSavedEmbeddingModel,
  );
  const savedKey = JSON.stringify(savedEmbeddingModel ?? null);
  const localSavedKey = JSON.stringify(localSavedEmbeddingModel ?? null);
  const [saveError, setSaveError] = useState("");

  useEffect(() => {
    const fromBackend = savedEmbeddingModel
      ? {
          provider: savedEmbeddingModel.provider as FeatureModelProvider,
          upstreamModel: savedEmbeddingModel.upstreamModel,
          dimension: savedEmbeddingModel.dimension,
          batchSize: savedEmbeddingModel.batchSize,
        }
      : undefined;
    const next = fromBackend ?? localSavedEmbeddingModel;
    const nextKey = JSON.stringify(next ?? null);
    setLocalModel((current) =>
      JSON.stringify(current ?? null) === nextKey ? current : next,
    );
  }, [localSavedEmbeddingModel, localSavedKey, savedEmbeddingModel, savedKey]);

  const selectedProvider = localModel?.provider ?? "";
  const upstreamModel = localModel?.upstreamModel ?? "";
  const dimension =
    localModel === undefined ? DEFAULT_EMBEDDING_DIMENSION : localModel.dimension;
  const batchSize =
    localModel === undefined ? DEFAULT_EMBEDDING_BATCH_SIZE : localModel.batchSize;

  const updateLocal = (patch: Partial<EmbeddingModelEntry>) => {
    setLocalModel((prev) => ({
      provider: patch.provider ?? prev?.provider ?? configuredProviders[0] ?? "ali",
      upstreamModel: patch.upstreamModel ?? prev?.upstreamModel ?? "",
      dimension: patch.dimension ?? prev?.dimension ?? DEFAULT_EMBEDDING_DIMENSION,
      batchSize:
        "batchSize" in patch
          ? patch.batchSize
          : prev?.batchSize ?? DEFAULT_EMBEDDING_BATCH_SIZE,
    }));
  };

  const handleSave = async () => {
    setSaveError("");
    if (configuredProviders.length === 0) {
      toast.error(t("settings.modelConfig.embeddingModel.noChannels"));
      return;
    }
    const provider = localModel?.provider;
    if (!provider || !configuredProviders.includes(provider)) {
      toast.error(t("settings.modelConfig.embeddingModel.missingProvider"));
      return;
    }
    if (!savedChannelByProvider.get(provider)?.configured) {
      const message = t("settings.modelConfig.featureModels.missingKeys", {
        providers: FEATURE_PROVIDER_LABELS[provider],
      });
      setSaveError(message);
      toast.error(message);
      return;
    }
    const model = upstreamModel.trim();
    if (!model) {
      toast.error(t("settings.modelConfig.embeddingModel.missingModel"));
      return;
    }
    const normalizedDimension = Number(dimension);
    if (!Number.isInteger(normalizedDimension) || normalizedDimension <= 0) {
      toast.error(t("settings.modelConfig.embeddingModel.invalidDimension"));
      return;
    }
    const normalizedBatchSize =
      batchSize == null || String(batchSize).trim() === "" ? undefined : Math.round(Number(batchSize));
    if (
      normalizedBatchSize !== undefined &&
      (!Number.isFinite(normalizedBatchSize) || normalizedBatchSize <= 0)
    ) {
      toast.error(t("settings.modelConfig.embeddingModel.invalidBatchSize"));
      return;
    }
    if (!newApiBaseUrl.trim()) {
      toast.error(t("settings.modelConfig.featureModels.missingBaseUrl"));
      return;
    }
    try {
      const res = await saveEmbeddingModel.mutateAsync({
        newApiBaseUrl: newApiBaseUrl.trim(),
        ...(database ? { database } : {}),
        provider,
        upstreamModel: model,
        dimension: normalizedDimension,
        ...(normalizedBatchSize ? { batchSize: normalizedBatchSize } : {}),
      });
      if (!res.ok) {
        const message = getResponseErrorMessage(res, t("settings.modelConfig.requestFailed"));
        setSaveError(message);
        toast.error(message);
        return;
      }
      const saved = {
        provider: res.data.embeddingModel.provider as FeatureModelProvider,
        upstreamModel: res.data.embeddingModel.upstreamModel,
        dimension: res.data.embeddingModel.dimension,
        batchSize: res.data.embeddingModel.batchSize,
      };
      setEmbeddingModel(saved);
      setLocalModel(saved);
      toast.success(t("settings.modelConfig.embeddingModel.saved"));
    } catch (error) {
      const message = await getRequestErrorMessage(error, t("settings.modelConfig.requestFailed"));
      setSaveError(message);
      toast.error(message);
    }
  };

  return (
    <div className="mt-6">
      <h4 className="text-xs font-medium text-foreground">
        {t("settings.modelConfig.embeddingModel.title")}
      </h4>
      <p className="mt-1.5 text-[11px] leading-relaxed text-muted-foreground">
        {t("settings.modelConfig.embeddingModel.description")}
      </p>

      <div className="mt-3 rounded-md border border-border/70 px-3 py-3">
        <div className="grid grid-cols-[140px_minmax(0,1fr)_100px_110px] items-center gap-3 text-[11px] font-medium tracking-wide text-muted-foreground uppercase">
          <span>{t("settings.modelConfig.embeddingModel.colProvider")}</span>
          <span>{t("settings.modelConfig.embeddingModel.colUpstreamModel")}</span>
          <span>{t("settings.modelConfig.embeddingModel.colDimension")}</span>
          <span>{t("settings.modelConfig.embeddingModel.colBatchSize")}</span>
        </div>
        <div className="mt-2 grid grid-cols-[140px_minmax(0,1fr)_100px_110px] items-center gap-3">
          <Select
            value={selectedProvider}
            onValueChange={(provider) => updateLocal({ provider: provider as FeatureModelProvider })}
            disabled={configuredProviders.length === 0}
          >
            <SelectTrigger size="sm" className="w-full">
              <SelectValue placeholder={t("settings.modelConfig.embeddingModel.defaultProvider")}>
                {(provider: string) => FEATURE_PROVIDER_LABELS[provider as FeatureModelProvider]}
              </SelectValue>
            </SelectTrigger>
            <SelectContent alignItemWithTrigger={false}>
              {configuredProviders.map((provider) => (
                <SelectItem key={provider} value={provider}>
                  {FEATURE_PROVIDER_LABELS[provider]}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Input
            value={upstreamModel}
            onChange={(event) => updateLocal({ upstreamModel: event.target.value })}
            placeholder={t("settings.modelConfig.embeddingModel.upstreamModelPlaceholder")}
            className="h-8 rounded-md border-input/80 focus-visible:border-ring/70 focus-visible:ring-1 focus-visible:ring-ring/30"
            disabled={configuredProviders.length === 0}
          />
          <Input
            value={String(dimension)}
            onChange={(event) => {
              if (event.target.value.trim()) {
                updateLocal({
                  dimension: Number(event.target.value),
                });
              }
            }}
            inputMode="numeric"
            min={1}
            step={1}
            type="number"
            className="h-8 rounded-md border-input/80 focus-visible:border-ring/70 focus-visible:ring-1 focus-visible:ring-ring/30"
            disabled={configuredProviders.length === 0}
          />
          <Input
            value={batchSize == null ? "" : String(batchSize)}
            onChange={(event) =>
              updateLocal({
                batchSize: event.target.value.trim() ? Number(event.target.value) : undefined,
              })
            }
            inputMode="numeric"
            min={1}
            step={1}
            type="number"
            placeholder={t("settings.modelConfig.embeddingModel.batchSizePlaceholder")}
            className="h-8 rounded-md border-input/80 focus-visible:border-ring/70 focus-visible:ring-1 focus-visible:ring-ring/30"
            disabled={configuredProviders.length === 0}
          />
        </div>
        <p className="mt-2 text-[11px] leading-relaxed text-amber-300/80">
          {t("settings.modelConfig.embeddingModel.dimensionWarning")}
        </p>
      </div>

      {saveError ? (
        <p className="mt-2 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-[11px] leading-relaxed text-destructive">
          {saveError}
        </p>
      ) : null}

      <div className="mt-3 flex items-center justify-end gap-3">
        <p className="text-[11px] leading-relaxed text-muted-foreground">
          {configuredProviders.length > 0
            ? t("settings.modelConfig.embeddingModel.saveHint")
            : t("settings.modelConfig.embeddingModel.noChannelsHint")}
        </p>
        <Button
          type="button"
          size="sm"
          className="shrink-0"
          onClick={handleSave}
          disabled={configuredProviders.length === 0 || saveEmbeddingModel.isPending}
        >
          {saveEmbeddingModel.isPending ? <Loader2 className="size-3.5 animate-spin" /> : null}
          {t("settings.modelConfig.embeddingModel.save")}
        </Button>
      </div>
    </div>
  );
}

function MediaModelsBlock({
  configuredProviders,
  newApiBaseUrl,
  database,
  savedChannelByProvider,
  savedMediaModels,
  kinds,
  title,
  description,
}: {
  configuredProviders: readonly FeatureModelProvider[];
  newApiBaseUrl: string;
  database: NewApiDatabaseConfigInput | undefined;
  savedChannelByProvider: Map<string, SavedProviderChannelConfig>;
  savedMediaModels: Record<string, { provider: string; upstreamModel: string }>;
  kinds?: ReadonlyArray<"image" | "video" | "audio">;
  title?: string;
  description?: string;
}) {
  const { t } = useTranslation();
  const localSavedMediaModels = useSettingsStore((s) => s.featureModelConfig.mediaModels ?? {});
  const setMediaModels = useSettingsStore((s) => s.setMediaModels);
  const saveMediaModels = useSaveMediaModels();
  const [mediaModels, setLocalMediaModels] = useState(localSavedMediaModels);
  const [saveError, setSaveError] = useState("");
  const savedMediaModelsKey = JSON.stringify(savedMediaModels);
  const localSavedMediaModelsKey = JSON.stringify(localSavedMediaModels);
  const visibleRows = useMemo(
    () =>
      kinds && kinds.length > 0
        ? MEDIA_MODEL_ROWS.filter((row) => kinds.includes(row.kind))
        : MEDIA_MODEL_ROWS,
    [kinds],
  );

  useEffect(() => {
    const fromBackend = Object.fromEntries(
      Object.entries(savedMediaModels).map(([model, entry]) => [
        model,
        {
          provider: entry.provider as FeatureModelProvider,
          upstreamModel: entry.upstreamModel,
        },
      ]),
    );
    const next = Object.keys(fromBackend).length > 0 ? fromBackend : localSavedMediaModels;
    const nextKey = JSON.stringify(next ?? {});
    setLocalMediaModels((current) =>
      JSON.stringify(current ?? {}) === nextKey ? current : (next ?? {}),
    );
  }, [localSavedMediaModelsKey, savedMediaModelsKey]);

  const handleSave = async () => {
    setSaveError("");
    const next: typeof localSavedMediaModels = {};
    for (const row of visibleRows) {
      if (row.officialOnly) continue;
      const entry = mediaModels[row.model];
      if (entry?.provider && configuredProviders.includes(entry.provider)) {
        next[row.model] = {
          provider: entry.provider,
          upstreamModel: entry.upstreamModel.trim(),
        };
      }
    }
    if (Object.keys(next).length === 0) {
      toast.error(t("settings.modelConfig.mediaModels.noMappings"));
      return;
    }
    const missingProviders = Array.from(
      new Set(
        Object.values(next)
          .map((entry) => entry.provider)
          .filter((provider) => !savedChannelByProvider.get(provider)?.configured),
      ),
    );
    if (missingProviders.length > 0) {
      const message = t("settings.modelConfig.featureModels.missingKeys", {
        providers: missingProviders
          .map((provider) => FEATURE_PROVIDER_LABELS[provider])
          .join("、"),
      });
      setSaveError(message);
      toast.error(message);
      return;
    }
    if (!newApiBaseUrl.trim()) {
      toast.error(t("settings.modelConfig.featureModels.missingBaseUrl"));
      return;
    }
    try {
      const res = await saveMediaModels.mutateAsync({
        newApiBaseUrl: newApiBaseUrl.trim(),
        ...(database ? { database } : {}),
        models: next,
      });
      if (!res.ok) {
        const message = getResponseErrorMessage(res, t("settings.modelConfig.requestFailed"));
        setSaveError(message);
        toast.error(message);
        return;
      }
      const { succeeded, failed, models, results } = res.data;
      if (failed > 0) {
        const firstError = results.find((item) => item.error)?.error;
        const message = firstError ||
          t("settings.modelConfig.featureModels.savedPartial", { succeeded, failed });
        setSaveError(message);
        toast.warning(message);
        return;
      }
      const saved = Object.fromEntries(
        Object.entries(models).map(([model, entry]) => [
          model,
          {
            provider: entry.provider as FeatureModelProvider,
            upstreamModel: entry.upstreamModel,
          },
        ]),
      );
      setMediaModels(saved);
      setLocalMediaModels(saved);
      toast.success(t("settings.modelConfig.mediaModels.saved"));
    } catch (error) {
      const message = await getRequestErrorMessage(error, t("settings.modelConfig.requestFailed"));
      setSaveError(message);
      toast.error(message);
    }
  };

  return (
    <div className="mt-6">
      <h4 className="text-xs font-medium text-foreground">
        {title ?? t("settings.modelConfig.mediaModels.title")}
      </h4>
      <p className="mt-1.5 text-[11px] leading-relaxed text-muted-foreground">
        {description ?? t("settings.modelConfig.mediaModels.description")}
      </p>

      <div
        className={cn(
          MEDIA_ROW_GRID,
          "mt-3 px-3 text-[11px] font-medium tracking-wide text-muted-foreground uppercase",
        )}
      >
        <span>{t("settings.modelConfig.mediaModels.colType")}</span>
        <span>{t("settings.modelConfig.mediaModels.colModel")}</span>
        <span>{t("settings.modelConfig.mediaModels.colProvider")}</span>
        <span>{t("settings.modelConfig.mediaModels.colUpstreamModel")}</span>
      </div>

      <div className="mt-2 rounded-md border border-border/70">
        {visibleRows.map((row, index) => {
          const entry = mediaModels[row.model];
          const value = entry?.provider ?? "";
          return (
            <div
              key={row.model}
              className={cn(
                MEDIA_ROW_GRID,
                "px-3 py-2.5",
                index > 0 && "border-t border-border/70",
              )}
            >
              <span className="text-xs text-muted-foreground">
                {t(`settings.modelConfig.mediaModels.types.${row.kind}`)}
              </span>
              <code className="truncate rounded border border-border/60 bg-white/[0.03] px-2 py-1.5 text-[11px] text-muted-foreground">
                {row.model}
              </code>
              {row.officialOnly ? (
                <div className="h-8 rounded-md border border-border/60 bg-white/[0.03] px-3 py-1.5 text-xs text-muted-foreground">
                  {t("settings.modelConfig.mediaModels.officialOnly")}
                </div>
              ) : (
                <Select
                  value={value}
                  onValueChange={(provider) =>
                    setLocalMediaModels((prev) => ({
                      ...prev,
                      [row.model]: {
                        provider: provider as FeatureModelProvider,
                        upstreamModel: prev[row.model]?.upstreamModel ?? "",
                      },
                    }))
                  }
                  disabled={configuredProviders.length === 0}
                >
                  <SelectTrigger size="sm" className="w-full">
                    <SelectValue placeholder={t("settings.modelConfig.mediaModels.defaultProvider")}>
                      {(provider: string) => FEATURE_PROVIDER_LABELS[provider as FeatureModelProvider]}
                    </SelectValue>
                  </SelectTrigger>
                  <SelectContent alignItemWithTrigger={false}>
                    {configuredProviders.map((provider) => (
                      <SelectItem key={provider} value={provider}>
                        {FEATURE_PROVIDER_LABELS[provider]}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              )}
              {row.officialOnly ? (
                <div className="h-8 rounded-md border border-border/60 bg-white/[0.03] px-3 py-1.5 text-xs text-muted-foreground">
                  {t("settings.modelConfig.mediaModels.officialOnly")}
                </div>
              ) : (
                <Input
                  value={entry?.upstreamModel ?? ""}
                  onChange={(event) =>
                    setLocalMediaModels((prev) => ({
                      ...prev,
                      [row.model]: {
                        provider: prev[row.model]?.provider ?? configuredProviders[0] ?? "ali",
                        upstreamModel: event.target.value,
                      },
                    }))
                  }
                  placeholder={t("settings.modelConfig.mediaModels.upstreamModelPlaceholder", {
                    model: row.model,
                  })}
                  className="h-8 rounded-md border-input/80 focus-visible:border-ring/70 focus-visible:ring-1 focus-visible:ring-ring/30"
                  disabled={configuredProviders.length === 0}
                />
              )}
            </div>
          );
        })}
      </div>

      {saveError ? (
        <p className="mt-2 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-[11px] leading-relaxed text-destructive">
          {saveError}
        </p>
      ) : null}

      <div className="mt-3 flex items-center justify-end gap-3">
        <p className="text-[11px] leading-relaxed text-muted-foreground">
          {configuredProviders.length > 0
            ? t("settings.modelConfig.mediaModels.saveHint")
            : t("settings.modelConfig.mediaModels.noChannelsHint")}
        </p>
        <Button
          type="button"
          size="sm"
          className="shrink-0"
          onClick={handleSave}
          disabled={configuredProviders.length === 0 || saveMediaModels.isPending}
        >
          {saveMediaModels.isPending ? <Loader2 className="size-3.5 animate-spin" /> : null}
          {t("settings.modelConfig.mediaModels.save")}
        </Button>
      </div>
    </div>
  );
}

function ProviderChannelsBlock({
  savedProviderChannels,
  newApiBaseUrl,
  database,
}: {
  savedProviderChannels: SavedProviderChannelConfig[];
  newApiBaseUrl: string;
  database: NewApiDatabaseConfigInput | undefined;
}) {
  const { t } = useTranslation();
  const providerChannels = useSettingsStore((s) => s.featureModelConfig.providerChannels);
  const addFeatureProviderChannel = useSettingsStore((s) => s.addFeatureProviderChannel);
  const saveProviderChannels = useSaveProviderChannels();

  const configuredProviders = useMemo(
    () => FEATURE_MODEL_PROVIDERS.filter((p) => Boolean(providerChannels[p])),
    [providerChannels],
  );
  const availableProviders = useMemo(
    () => FEATURE_MODEL_PROVIDERS.filter((p) => !providerChannels[p]),
    [providerChannels],
  );
  const savedChannelByProvider = useMemo(() => {
    return new Map(savedProviderChannels.map((channel) => [channel.provider, channel]));
  }, [savedProviderChannels]);
  const [selectedProvider, setSelectedProvider] = useState<FeatureModelProvider>(
    availableProviders[0] ?? FEATURE_MODEL_PROVIDERS[0],
  );

  useEffect(() => {
    if (!availableProviders.includes(selectedProvider) && availableProviders[0]) {
      setSelectedProvider(availableProviders[0]);
    }
  }, [availableProviders, selectedProvider]);

  const handleAdd = () => {
    if (!availableProviders.includes(selectedProvider)) return;
    addFeatureProviderChannel(selectedProvider);
  };

  const handleSaveChannels = () => {
    if (configuredProviders.length === 0) {
      toast.error(t("settings.modelConfig.featureModels.noChannels"));
      return;
    }
    const channelsToSave = configuredProviders
      .filter((provider) => {
        if ((providerChannels[provider]?.upstreamKey ?? "").trim()) return true;
        return Boolean(savedChannelByProvider.get(provider)?.configured);
      })
      .map((provider) => ({
        provider,
        upstreamKey: (providerChannels[provider]?.upstreamKey ?? "").trim() || undefined,
        baseUrl: (providerChannels[provider]?.baseUrl ?? "").trim(),
      }));
    if (channelsToSave.length === 0) {
      toast.error(
        t("settings.modelConfig.featureModels.missingKeys", {
          providers: configuredProviders.map((provider) => FEATURE_PROVIDER_LABELS[provider]).join("、"),
        }),
      );
      return;
    }
    saveProviderChannels.mutate(
      {
        channels: channelsToSave,
      },
      {
        onSuccess: (res) => {
          if (!res.ok) {
            toast.error(res.error);
            return;
          }
          for (const { provider } of channelsToSave) {
            if ((providerChannels[provider]?.upstreamKey ?? "").trim()) {
              useSettingsStore.getState().clearFeatureProviderUpstreamKey(provider);
            }
          }
          toast.success(t("settings.modelConfig.featureModels.channelsSaved"));
        },
        onError: () => {
          toast.error(t("settings.modelConfig.requestFailed"));
        },
      },
    );
  };

  return (
    <div className="mt-5 rounded-md border border-border/70 p-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h4 className="text-xs font-medium text-foreground">
            {t("settings.modelConfig.featureModels.channelsTitle")}
          </h4>
          <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
            {t("settings.modelConfig.featureModels.channelsDescription")}
          </p>
        </div>
        <div className="flex min-w-[260px] items-center gap-2">
          <Select
            value={selectedProvider}
            onValueChange={(value) => setSelectedProvider(value as FeatureModelProvider)}
            disabled={availableProviders.length === 0}
          >
            <SelectTrigger size="sm" className="min-w-[170px] flex-1">
              <SelectValue>
                {(value: string) => FEATURE_PROVIDER_LABELS[value as FeatureModelProvider]}
              </SelectValue>
            </SelectTrigger>
            <SelectContent alignItemWithTrigger={false}>
              {availableProviders.map((p) => (
                <SelectItem key={p} value={p}>
                  {FEATURE_PROVIDER_LABELS[p]}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="shrink-0"
            onClick={handleAdd}
            disabled={availableProviders.length === 0}
          >
            <Plus className="size-3.5" />
            {t("settings.modelConfig.featureModels.addChannel")}
          </Button>
        </div>
      </div>

      {configuredProviders.length > 0 ? (
        <>
          <div className="mt-3 space-y-2.5">
            {configuredProviders.map((provider) => (
              <ProviderChannelRow
                key={provider}
                provider={provider}
                savedChannel={savedChannelByProvider.get(provider)}
                newApiBaseUrl={newApiBaseUrl}
                database={database}
              />
            ))}
          </div>
          <div className="mt-3 flex items-center justify-end gap-3">
            <p className="text-[11px] leading-relaxed text-muted-foreground">
              {t("settings.modelConfig.featureModels.channelsSaveHint")}
            </p>
            <Button
              type="button"
              size="sm"
              onClick={handleSaveChannels}
              disabled={saveProviderChannels.isPending}
            >
              {saveProviderChannels.isPending ? <Loader2 className="size-3.5 animate-spin" /> : null}
              {t("settings.modelConfig.featureModels.saveChannels")}
            </Button>
          </div>
        </>
      ) : (
        <p className="mt-3 rounded-md border border-dashed border-border/70 px-3 py-2 text-[11px] text-muted-foreground">
          {t("settings.modelConfig.featureModels.noChannelsHint")}
        </p>
      )}
    </div>
  );
}

function ProviderChannelRow({
  provider,
  savedChannel,
  newApiBaseUrl,
  database,
}: {
  provider: FeatureModelProvider;
  savedChannel: SavedProviderChannelConfig | undefined;
  newApiBaseUrl: string;
  database: NewApiDatabaseConfigInput | undefined;
}) {
  const { t } = useTranslation();
  const channel = useSettingsStore((s) => s.featureModelConfig.providerChannels[provider]);
  const updateFeatureProviderChannel = useSettingsStore((s) => s.updateFeatureProviderChannel);
  const removeFeatureProviderChannel = useSettingsStore((s) => s.removeFeatureProviderChannel);
  const clearFeatureProviderUpstreamKey = useSettingsStore((s) => s.clearFeatureProviderUpstreamKey);
  const syncProviderChannel = useSyncProviderChannel();
  const [revealed, setRevealed] = useState(false);
  const upstreamKeyValue = channel?.upstreamKey ?? "";
  const savedKeyPreview = savedChannel?.configured ? savedChannel.upstreamKeyPreview : "";
  const upstreamPlaceholder = savedKeyPreview || "sk-...";
  useEffect(() => {
    if (!upstreamKeyValue) setRevealed(false);
  }, [upstreamKeyValue]);
  const handleSync = async () => {
    if (!newApiBaseUrl.trim()) {
      toast.error(t("settings.modelConfig.featureModels.missingBaseUrl"));
      return;
    }
    const upstreamKey = upstreamKeyValue.trim();
    if (!upstreamKey && !savedChannel?.configured) {
      toast.error(
        t("settings.modelConfig.featureModels.missingKeys", {
          providers: FEATURE_PROVIDER_LABELS[provider],
        }),
      );
      return;
    }
    try {
      const res = await syncProviderChannel.mutateAsync({
        newApiBaseUrl: newApiBaseUrl.trim(),
        ...(database ? { database } : {}),
        provider,
        ...(upstreamKey ? { upstreamKey } : {}),
        baseUrl: (channel?.baseUrl ?? "").trim(),
      });
      if (res.ok !== true) {
        toast.error(getResponseErrorMessage(res, t("settings.modelConfig.requestFailed")));
        return;
      }
      if (upstreamKey) {
        clearFeatureProviderUpstreamKey(provider);
        setRevealed(false);
      }
      toast.success(t("settings.modelConfig.featureModels.channelSynced"));
    } catch (error) {
      toast.error(await getRequestErrorMessage(error, t("settings.modelConfig.requestFailed")));
    }
  };

  return (
    <div className="grid gap-2 rounded-md border border-border/60 p-2.5 sm:grid-cols-[130px_minmax(0,1fr)_minmax(0,1fr)_auto] sm:items-end">
      <div>
        <Label className="justify-start text-[11px] font-normal text-muted-foreground">
          {t("settings.modelConfig.featureModels.channelProvider")}
        </Label>
        <div className="mt-1.5 h-9 rounded-md border border-border/70 bg-white/[0.03] px-3 py-2 text-xs text-foreground">
          {FEATURE_PROVIDER_LABELS[provider]}
        </div>
      </div>
      <div>
        <Label className="justify-start text-[11px] font-normal text-muted-foreground">
          {t("settings.modelConfig.featureModels.upstreamKey")}
        </Label>
        <div className="relative mt-1.5">
          <Input
            name={`provider-${provider}-upstream-api-key`}
            autoComplete="new-password"
            data-1p-ignore="true"
            data-lpignore="true"
            type={revealed ? "text" : "password"}
            value={upstreamKeyValue}
            onChange={(e) => updateFeatureProviderChannel(provider, { upstreamKey: e.target.value })}
            placeholder={
              savedKeyPreview
                ? t("settings.secretSavedPlaceholder", { preview: savedKeyPreview })
                : upstreamPlaceholder
            }
            autoCapitalize="none"
            spellCheck={false}
            className={cn(
              "h-9 rounded-md border-input/80 focus-visible:border-ring/70 focus-visible:ring-1 focus-visible:ring-ring/30",
              upstreamKeyValue ? "pr-9" : savedKeyPreview ? "pr-16" : "",
            )}
          />
          {upstreamKeyValue ? (
            <button
              type="button"
              onClick={() => setRevealed((r) => !r)}
              aria-label={
                revealed
                  ? t("settings.mediaStorage.hideSecret")
                  : t("settings.mediaStorage.showSecret")
              }
              className="absolute top-1/2 right-2 -translate-y-1/2 text-muted-foreground transition-colors hover:text-foreground"
            >
              {revealed ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
            </button>
          ) : savedKeyPreview ? (
            <span className="absolute top-1/2 right-2 -translate-y-1/2 rounded bg-emerald-400/10 px-1.5 py-0.5 text-[10px] font-medium text-emerald-400">
              {t("settings.secretSavedBadge")}
            </span>
          ) : null}
        </div>
      </div>
      <div>
        <Label className="justify-start text-[11px] font-normal text-muted-foreground">
          {t("settings.modelConfig.featureModels.baseUrlOverride")}
        </Label>
        <Input
          value={channel?.baseUrl ?? ""}
          onChange={(e) => updateFeatureProviderChannel(provider, { baseUrl: e.target.value })}
          placeholder={t("settings.modelConfig.featureModels.baseUrlPlaceholder")}
          className="mt-1.5 h-9 rounded-md border-input/80 focus-visible:border-ring/70 focus-visible:ring-1 focus-visible:ring-ring/30"
        />
      </div>
      <div className="flex items-center justify-end gap-1.5 sm:self-end">
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="h-9 whitespace-nowrap px-2 text-[11px]"
          onClick={handleSync}
          disabled={syncProviderChannel.isPending}
          title={t("settings.modelConfig.featureModels.syncChannelHint")}
        >
          {syncProviderChannel.isPending ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : (
            <RotateCw className="size-3.5" />
          )}
          {t("settings.modelConfig.featureModels.syncChannel")}
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          className="text-muted-foreground hover:text-destructive"
          onClick={() => removeFeatureProviderChannel(provider)}
          title={t("settings.modelConfig.featureModels.removeChannel")}
        >
          <Trash2 className="size-4" />
        </Button>
      </div>
    </div>
  );
}

function FeatureModelCapabilitySection({
  title,
  hint,
  groups,
  newApiBaseUrl,
  database,
  configuredProviders,
  providerChannels,
  savedChannelByProvider,
}: {
  title: string;
  hint?: string;
  groups: readonly FeatureModelGroup[];
  newApiBaseUrl: string;
  database: NewApiDatabaseConfigInput | undefined;
  configuredProviders: readonly FeatureModelProvider[];
  providerChannels: Record<string, { upstreamKey: string; baseUrl: string }>;
  savedChannelByProvider: Map<string, SavedProviderChannelConfig>;
}) {
  const { t } = useTranslation();
  const [bulkProvider, setBulkProvider] = useState<FeatureModelProvider | "">(
    configuredProviders[0] ?? "",
  );
  const [bulkModel, setBulkModel] = useState("");
  const updateFeatureModel = useSettingsStore((s) => s.updateFeatureModel);
  const featureCount = useMemo(
    () => groups.reduce((total, group) => total + group.features.length, 0),
    [groups],
  );

  useEffect(() => {
    if (!bulkProvider || !configuredProviders.includes(bulkProvider)) {
      setBulkProvider(configuredProviders[0] ?? "");
    }
  }, [bulkProvider, configuredProviders]);

  const handleApplyBulk = () => {
    const provider = bulkProvider;
    const model = bulkModel.trim();
    if (!provider) {
      toast.error(t("settings.modelConfig.featureModels.noChannels"));
      return;
    }
    if (!model) {
      toast.error(t("settings.modelConfig.featureModels.bulkMissingModel"));
      return;
    }
    for (const group of groups) {
      for (const feature of group.features) {
        updateFeatureModel(feature.id, { provider, model });
      }
    }
    toast.success(
      t("settings.modelConfig.featureModels.bulkApplied", { count: featureCount }),
    );
  };

  return (
    <div className="mt-4">
      <h5 className="text-[11px] font-medium text-foreground">{title}</h5>
      {hint ? (
        <p className="mt-1 text-[11px] leading-relaxed text-amber-300/80">{hint}</p>
      ) : null}
      <div className="mt-2 grid grid-cols-[150px_minmax(0,1fr)_auto] items-center gap-2 rounded-md border border-border/70 px-3 py-2">
        <Select
          value={bulkProvider}
          onValueChange={(value) => setBulkProvider(value as FeatureModelProvider)}
          disabled={configuredProviders.length === 0}
        >
          <SelectTrigger size="sm" className="w-full">
            <SelectValue placeholder={t("settings.modelConfig.featureModels.noChannelsShort")}>
              {(value: string) => FEATURE_PROVIDER_LABELS[value as FeatureModelProvider]}
            </SelectValue>
          </SelectTrigger>
          <SelectContent alignItemWithTrigger={false}>
            {configuredProviders.map((provider) => (
              <SelectItem key={provider} value={provider}>
                {FEATURE_PROVIDER_LABELS[provider]}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Input
          value={bulkModel}
          onChange={(event) => setBulkModel(event.target.value)}
          placeholder={t("settings.modelConfig.featureModels.bulkModelPlaceholder")}
          className="h-8 rounded-md border-input/80 focus-visible:border-ring/70 focus-visible:ring-1 focus-visible:ring-ring/30"
          disabled={configuredProviders.length === 0}
        />
        <Button
          type="button"
          size="sm"
          className="shrink-0"
          onClick={handleApplyBulk}
          disabled={configuredProviders.length === 0}
        >
          {t("settings.modelConfig.featureModels.applyToAll")}
        </Button>
      </div>

      {/* 表头：功能 / 供应商 / 上游模型名（与下方行栅格对齐） */}
      <div
        className={cn(
          FEATURE_ROW_GRID,
          "mt-2 px-3 text-[11px] font-medium tracking-wide text-muted-foreground uppercase",
        )}
      >
        <span>{t("settings.modelConfig.featureModels.colFeature")}</span>
        <span>{t("settings.modelConfig.featureModels.colProvider")}</span>
        <span>{t("settings.modelConfig.featureModels.colModel")}</span>
      </div>

      <div className="mt-2 space-y-2">
        {groups.map((group) => (
          <FeatureModelGroupBlock
            key={group.key}
            groupKey={group.key}
            features={group.features}
            newApiBaseUrl={newApiBaseUrl}
            database={database}
            configuredProviders={configuredProviders}
            providerChannels={providerChannels}
            savedChannelByProvider={savedChannelByProvider}
          />
        ))}
      </div>
    </div>
  );
}

function FeatureModelGroupBlock({
  groupKey,
  features,
  newApiBaseUrl,
  database,
  configuredProviders,
  providerChannels,
  savedChannelByProvider,
}: {
  groupKey: string;
  features: readonly FeatureModelDef[];
  newApiBaseUrl: string;
  database: NewApiDatabaseConfigInput | undefined;
  configuredProviders: readonly FeatureModelProvider[];
  providerChannels: Record<string, { upstreamKey: string; baseUrl: string }>;
  savedChannelByProvider: Map<string, SavedProviderChannelConfig>;
}) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="rounded-md border border-border/70">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full items-center justify-between gap-2 px-3 py-2 text-left"
      >
        <span className="text-xs font-medium text-foreground">
          {t(`settings.modelConfig.featureModels.groups.${groupKey}`)}
        </span>
        <ChevronDown
          className={cn(
            "size-4 shrink-0 text-muted-foreground transition-transform",
            expanded && "rotate-180",
          )}
        />
      </button>
      {expanded ? (
        <div className="space-y-2.5 border-t border-border/70 px-3 py-3">
          {features.map((feature) => (
            <FeatureModelRow
              key={feature.id}
              featureId={feature.id}
              defaultModel={feature.defaultModel}
              requiresVision={Boolean(feature.requiresVision)}
              newApiBaseUrl={newApiBaseUrl}
              database={database}
              configuredProviders={configuredProviders}
              providerChannels={providerChannels}
              savedChannelByProvider={savedChannelByProvider}
            />
          ))}
        </div>
      ) : null}
    </div>
  );
}

function FeatureModelRow({
  featureId,
  defaultModel,
  requiresVision,
  newApiBaseUrl,
  database,
  configuredProviders,
  providerChannels,
  savedChannelByProvider,
}: {
  featureId: string;
  defaultModel: string;
  requiresVision: boolean;
  newApiBaseUrl: string;
  database: NewApiDatabaseConfigInput | undefined;
  configuredProviders: readonly FeatureModelProvider[];
  providerChannels: Record<string, { upstreamKey: string; baseUrl: string }>;
  savedChannelByProvider: Map<string, SavedProviderChannelConfig>;
}) {
  const { t } = useTranslation();
  const entry = useSettingsStore((s) => s.featureModelConfig.featureModels[featureId]);
  const updateFeatureModel = useSettingsStore((s) => s.updateFeatureModel);
  const saveChannel = useSaveCustomChannel();
  const configuredSet = useMemo(() => new Set(configuredProviders), [configuredProviders]);
  const fallbackProvider = configuredProviders[0];
  const provider = entry?.provider && configuredSet.has(entry.provider) ? entry.provider : fallbackProvider;
  const model = entry?.model ?? "";

  // 单条保存：仅把该功能拼成一个渠道（modelMapping 只含这一条）写入。
  const handleSaveRow = async () => {
    const m = model.trim();
    if (!m) {
      toast.error(t("settings.modelConfig.featureModels.noMappings"));
      return;
    }
    if (!provider) {
      toast.error(t("settings.modelConfig.featureModels.noChannels"));
      return;
    }
    if (!newApiBaseUrl.trim()) {
      toast.error(t("settings.modelConfig.featureModels.missingBaseUrl"));
      return;
    }
    const channel = providerChannels[provider];
    const upstreamKey = (channel?.upstreamKey ?? "").trim();
    if (!upstreamKey && !savedChannelByProvider.get(provider)?.configured) {
      toast.error(
        t("settings.modelConfig.featureModels.missingKeys", {
          providers: FEATURE_PROVIDER_LABELS[provider],
        }),
      );
      return;
    }
    try {
      const res = await saveChannel.mutateAsync({
        newApiBaseUrl: newApiBaseUrl.trim(),
        ...(database ? { database } : {}),
        provider,
        upstreamKey,
        modelMapping: { [defaultModel]: m },
        group: "default",
        priority: 0,
        weight: 0,
        baseUrl: (channel?.baseUrl ?? "").trim(),
        testModel: "",
      });
      if (!res.ok) {
        toast.error(res.error);
        return;
      }
      toast.success(t("settings.modelConfig.featureModels.savedOne"));
    } catch {
      toast.error(t("settings.modelConfig.requestFailed"));
    }
  };

  return (
    <div className={FEATURE_ROW_GRID}>
      <span className="flex flex-wrap items-center gap-1.5 text-xs text-foreground">
        <span>{t(`settings.modelConfig.featureModels.features.${featureId}`)}</span>
        {requiresVision ? (
          <span className="rounded border border-amber-400/40 bg-amber-400/10 px-1.5 py-0.5 text-[10px] font-medium text-amber-300">
            {t("settings.modelConfig.featureModels.multimodalRequiredBadge")}
          </span>
        ) : null}
      </span>
      <Select
        value={provider ?? ""}
        onValueChange={(value) =>
          updateFeatureModel(featureId, { provider: value as FeatureModelProvider })
        }
        disabled={configuredProviders.length === 0}
      >
        <SelectTrigger size="sm" className="w-full">
          <SelectValue placeholder={t("settings.modelConfig.featureModels.noChannelsShort")}>
            {(value: string) => FEATURE_PROVIDER_LABELS[value as FeatureModelProvider]}
          </SelectValue>
        </SelectTrigger>
        <SelectContent alignItemWithTrigger={false}>
          {configuredProviders.map((p) => (
            <SelectItem key={p} value={p}>
              {FEATURE_PROVIDER_LABELS[p]}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <div className="flex items-center gap-2">
        <Input
          value={model}
          onChange={(e) =>
            updateFeatureModel(featureId, {
              provider: provider ?? fallbackProvider,
              model: e.target.value,
            })
          }
          placeholder={t("settings.modelConfig.featureModels.upstreamModelPlaceholder")}
          className="h-9 flex-1 rounded-md border-input/80 focus-visible:border-ring/70 focus-visible:ring-1 focus-visible:ring-ring/30"
          disabled={configuredProviders.length === 0}
        />
        <Button
          type="button"
          size="sm"
          className="shrink-0"
          onClick={handleSaveRow}
          disabled={saveChannel.isPending || configuredProviders.length === 0}
          title={t("settings.modelConfig.featureModels.saveRow")}
        >
          {saveChannel.isPending ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : (
            t("settings.modelConfig.featureModels.saveRow")
          )}
        </Button>
      </div>
    </div>
  );
}

function MediaStorageSection() {
  const { t } = useTranslation();
  const configQuery = useModelGatewayConfig(true);
  const mediaRelay = configQuery.data?.data.mediaRelay;
  const mediaStorage = useSettingsStore((s) => s.mediaStorage);
  const setProvider = useSettingsStore((s) => s.setMediaStorageProvider);
  const updateCloudinary = useSettingsStore((s) => s.updateCloudinaryStorageConfig);
  const updateAliyunOss = useSettingsStore((s) => s.updateAliyunOssStorageConfig);
  const saveMediaRelay = useSaveMediaRelayConfig();

  const { provider, cloudinary, aliyunOss } = mediaStorage;
  const [ttlSeconds, setTtlSeconds] = useState("1800");
  const mediaRelayKey = JSON.stringify(mediaRelay ?? {});
  useEffect(() => {
    if (!mediaRelay) return;
    if (mediaRelay.provider === "aliyun_oss" || mediaRelay.provider === "cloudinary") {
      setProvider(mediaRelay.provider as MediaStorageProvider);
    }
    if (mediaRelay.endpoint || mediaRelay.bucket) {
      updateAliyunOss({
        endpoint: mediaRelay.endpoint || aliyunOss.endpoint,
        bucket: mediaRelay.bucket || aliyunOss.bucket,
        ...(mediaRelay.configured ? { accessKeyId: "", accessKeySecret: "" } : {}),
      });
    }
    if (mediaRelay.cloudName || mediaRelay.apiFolder) {
      updateCloudinary({
        cloudName: mediaRelay.cloudName || cloudinary.cloudName,
        apiFolder: mediaRelay.apiFolder || cloudinary.apiFolder,
        ...(mediaRelay.provider === "cloudinary" && mediaRelay.configured
          ? { apiKey: "", apiSecret: "" }
          : {}),
      });
    }
    if (mediaRelay.ttlSeconds) {
      setTtlSeconds((current) =>
        current === String(mediaRelay.ttlSeconds) ? current : String(mediaRelay.ttlSeconds),
      );
    }
    // Full AccessKey values must never be kept after the backend has a saved config.
    // Users re-enter them only when creating/updating the OSS relay credentials.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mediaRelayKey]);

  const hasConfiguredMediaRelay = Boolean(mediaRelay?.configured);
  const configuredProvider = hasConfiguredMediaRelay ? mediaRelay?.provider : provider;
  const handleSave = async () => {
    const ttl = Number(ttlSeconds.trim() || "0");
    if (!Number.isFinite(ttl) || ttl <= 0) {
      toast.error(t("settings.mediaStorage.validation.ttlSeconds"));
      return;
    }
    try {
      const res = await saveMediaRelay.mutateAsync(
        provider === "cloudinary"
          ? {
              provider: "cloudinary",
              ttlSeconds: Math.trunc(ttl),
              cloudName: cloudinary.cloudName.trim(),
              apiFolder: cloudinary.apiFolder.trim(),
              ...(cloudinary.apiKey.trim()
                ? { apiKey: cloudinary.apiKey.trim() }
                : {}),
              ...(cloudinary.apiSecret.trim()
                ? { apiSecret: cloudinary.apiSecret.trim() }
                : {}),
            }
          : {
              provider: "aliyun_oss",
              ttlSeconds: Math.trunc(ttl),
              endpoint: aliyunOss.endpoint.trim(),
              bucket: aliyunOss.bucket.trim(),
              ...(aliyunOss.accessKeyId.trim()
                ? { accessKeyId: aliyunOss.accessKeyId.trim() }
                : {}),
              ...(aliyunOss.accessKeySecret.trim()
                ? { accessKeySecret: aliyunOss.accessKeySecret.trim() }
                : {}),
            },
      );
      if (!res.ok) {
        toast.error(res.error);
        return;
      }
      if (provider === "cloudinary") {
        updateCloudinary({ apiKey: "", apiSecret: "" });
      } else {
        updateAliyunOss({ accessKeyId: "", accessKeySecret: "" });
      }
      toast.success(
        provider === "cloudinary"
          ? t("settings.mediaStorage.cloudinarySaveSuccess")
          : t("settings.mediaStorage.saveSuccess"),
      );
    } catch (error) {
      toast.error(await getRequestErrorMessage(error, t("settings.mediaStorage.saveFailed")));
    }
  };

  return (
    <section className="px-5 py-5">
      <div className="flex items-center gap-2">
        <span
          className={cn(
            "size-1.5 rounded-full",
            hasConfiguredMediaRelay ? "bg-emerald-400" : "bg-amber-400",
          )}
        />
        <h3 className="font-heading text-sm font-medium text-foreground">
          {t("settings.mediaStorage.title")}
        </h3>
        {!hasConfiguredMediaRelay ? (
          <AlertTriangle
            className="size-3.5 text-amber-400"
            aria-label={t("settings.mediaStorage.warningIconLabel")}
          />
        ) : null}
        <span className="ml-1 rounded bg-white/[0.06] px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
          {t("settings.mediaStorage.currentPlan")}: {configuredProvider === "cloudinary"
            ? t("settings.mediaStorage.providerCloudinary")
            : t("settings.mediaStorage.providerAliyunOss")}
        </span>
      </div>

      <p className="mt-2 text-xs leading-relaxed text-muted-foreground">
        {t("settings.mediaStorage.description")}
      </p>

      <p className="mt-3 text-xs text-muted-foreground">
        {t("settings.mediaStorage.status")}: {" "}
        <span className={hasConfiguredMediaRelay ? "text-emerald-400" : "text-amber-300"}>
          {hasConfiguredMediaRelay
            ? t("settings.mediaStorage.configured")
            : t("settings.mediaStorage.notConfigured")}
        </span>
        {hasConfiguredMediaRelay && mediaRelay?.source ? (
          <span className="ml-2 text-[11px] text-muted-foreground/80">
            {t("settings.mediaStorage.source", { source: mediaRelay.source })}
          </span>
        ) : null}
      </p>
      {!hasConfiguredMediaRelay ? (
        <div className="mt-3 flex gap-2 rounded-md border border-amber-500/35 bg-amber-500/10 px-3 py-2 text-[11px] leading-relaxed text-amber-100">
          <AlertTriangle className="mt-0.5 size-3.5 shrink-0 text-amber-300" aria-hidden />
          <p>{mediaRelay?.configurationError || t("settings.mediaStorage.notConfiguredImpact")}</p>
        </div>
      ) : null}

      <div className="mt-4 flex items-center gap-3">
        <span className="w-[64px] shrink-0 text-xs text-muted-foreground">
          {t("settings.mediaStorage.provider")}
        </span>
        <Tabs
          value={provider}
          onValueChange={(value) => setProvider(value as MediaStorageProvider)}
        >
          <TabsList>
            {MEDIA_STORAGE_PROVIDERS.map((p) => (
              <TabsTrigger key={p} value={p}>
                {p === "aliyun_oss"
                  ? t("settings.mediaStorage.providerAliyunOss")
                  : t("settings.mediaStorage.providerCloudinary")}
              </TabsTrigger>
            ))}
          </TabsList>
        </Tabs>
      </div>

      <div className="mt-4 space-y-2.5">
        {provider === "cloudinary" ? (
          <CloudinaryFields
            config={cloudinary}
            onChange={updateCloudinary}
            apiKeyPreview={mediaRelay?.cloudinaryApiKeyPreview ?? ""}
            apiSecretPreview={mediaRelay?.cloudinaryApiSecretPreview ?? ""}
          />
        ) : (
          <AliyunOssFields
            config={aliyunOss}
            onChange={updateAliyunOss}
            ttlSeconds={ttlSeconds}
            onTtlSecondsChange={setTtlSeconds}
            accessKeyIdPreview={mediaRelay?.accessKeyIdPreview ?? ""}
            accessKeySecretPreview={mediaRelay?.accessKeySecretPreview ?? ""}
          />
        )}
      </div>

      <div className="mt-4 flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <p className="text-[11px] leading-relaxed text-muted-foreground">
            {provider === "cloudinary"
              ? (
                <>
                  {t("settings.mediaStorage.cloudinaryFieldsHint")}{" "}
                  <a
                    href="https://cloudinary.com/users/register/free"
                    target="_blank"
                    rel="noreferrer"
                    className="text-cyan-400 hover:text-cyan-300"
                  >
                    {t("settings.mediaStorage.cloudinaryRegisterLink")}
                  </a>
                </>
              )
              : t("settings.mediaStorage.fieldsHint")}
          </p>
        </div>
        <Button
          type="button"
          size="sm"
          className="shrink-0"
          onClick={handleSave}
          disabled={saveMediaRelay.isPending || configQuery.isLoading}
        >
          {saveMediaRelay.isPending ? <Loader2 className="size-3.5 animate-spin" /> : null}
          {provider === "cloudinary"
            ? t("settings.mediaStorage.saveCloudinary")
            : t("settings.mediaStorage.save")}
        </Button>
      </div>
    </section>
  );
}

function CloudinaryFields({
  config,
  onChange,
  apiKeyPreview,
  apiSecretPreview,
}: {
  config: CloudinaryStorageConfig;
  onChange: (patch: Partial<CloudinaryStorageConfig>) => void;
  apiKeyPreview: string;
  apiSecretPreview: string;
}) {
  const { t } = useTranslation();
  return (
    <>
      <FieldRow
        label={t("settings.mediaStorage.fields.cloudName")}
        value={config.cloudName}
        onChange={(v) => onChange({ cloudName: v })}
      />
      <FieldRow
        secret
        name="cloudinary-api-key"
        label={t("settings.mediaStorage.fields.apiKey")}
        value={config.apiKey}
        onChange={(v) => onChange({ apiKey: v })}
        placeholder={apiKeyPreview || undefined}
        savedPreview={apiKeyPreview}
      />
      <FieldRow
        secret
        name="cloudinary-api-secret"
        label={t("settings.mediaStorage.fields.apiSecret")}
        value={config.apiSecret}
        onChange={(v) => onChange({ apiSecret: v })}
        placeholder={apiSecretPreview || undefined}
        savedPreview={apiSecretPreview}
      />
      <FieldRow
        label={t("settings.mediaStorage.fields.apiFolder")}
        value={config.apiFolder}
        onChange={(v) => onChange({ apiFolder: v })}
      />
    </>
  );
}

function AliyunOssFields({
  config,
  onChange,
  ttlSeconds,
  onTtlSecondsChange,
  accessKeyIdPreview,
  accessKeySecretPreview,
}: {
  config: AliyunOssStorageConfig;
  onChange: (patch: Partial<AliyunOssStorageConfig>) => void;
  ttlSeconds: string;
  onTtlSecondsChange: (value: string) => void;
  accessKeyIdPreview: string;
  accessKeySecretPreview: string;
}) {
  const { t } = useTranslation();
  return (
    <>
      <FieldRow
        name="aliyun-oss-access-key-id"
        label={t("settings.mediaStorage.fields.accessKeyId")}
        value={config.accessKeyId}
        onChange={(v) => onChange({ accessKeyId: v })}
        placeholder={accessKeyIdPreview || undefined}
        savedPreview={accessKeyIdPreview}
      />
      <FieldRow
        secret
        name="aliyun-oss-access-key-secret"
        label={t("settings.mediaStorage.fields.accessKeySecret")}
        value={config.accessKeySecret}
        onChange={(v) => onChange({ accessKeySecret: v })}
        placeholder={accessKeySecretPreview || undefined}
        savedPreview={accessKeySecretPreview}
      />
      <FieldRow
        label={t("settings.mediaStorage.fields.bucket")}
        value={config.bucket}
        onChange={(v) => onChange({ bucket: v })}
      />
      <FieldRow
        label={t("settings.mediaStorage.fields.endpoint")}
        value={config.endpoint}
        onChange={(v) => onChange({ endpoint: v })}
      />
      <FieldRow
        label={t("settings.mediaStorage.fields.ttlSeconds")}
        value={ttlSeconds}
        onChange={onTtlSecondsChange}
      />
    </>
  );
}

function FieldRow({
  label,
  value,
  onChange,
  secret = false,
  placeholder,
  name,
  autoComplete,
  savedPreview,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  secret?: boolean;
  placeholder?: string;
  name?: string;
  autoComplete?: string;
  savedPreview?: string;
}) {
  const { t } = useTranslation();
  const [revealed, setRevealed] = useState(false);
  useEffect(() => {
    if (!value) setRevealed(false);
  }, [value]);
  const hasSavedSecret = Boolean(savedPreview && !value);
  return (
    <div className="grid grid-cols-[120px_1fr] items-center gap-3">
      <Label className="justify-start text-[11px] font-normal tracking-wide text-muted-foreground uppercase">
        {label}
      </Label>
      <div className="relative">
        <Input
          name={name}
          autoComplete={autoComplete ?? (secret ? "new-password" : undefined)}
          type={secret && !revealed ? "password" : "text"}
          value={value}
          placeholder={
            hasSavedSecret
              ? t("settings.secretSavedPlaceholder", { preview: savedPreview })
              : placeholder
          }
          onChange={(e) => onChange(e.target.value)}
          className={cn(
            "h-9 rounded-md border-input/80 focus-visible:border-ring/70 focus-visible:ring-1 focus-visible:ring-ring/30",
            secret && value && "pr-9",
            hasSavedSecret && "pr-16",
          )}
        />
        {secret && value ? (
          <button
            type="button"
            onClick={() => setRevealed((r) => !r)}
            aria-label={
              revealed
                ? t("settings.mediaStorage.hideSecret")
                : t("settings.mediaStorage.showSecret")
            }
            className="absolute top-1/2 right-2 -translate-y-1/2 text-muted-foreground transition-colors hover:text-foreground"
          >
            {revealed ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
          </button>
        ) : hasSavedSecret ? (
          <span className="absolute top-1/2 right-2 -translate-y-1/2 rounded bg-emerald-400/10 px-1.5 py-0.5 text-[10px] font-medium text-emerald-400">
            {t("settings.secretSavedBadge")}
          </span>
        ) : null}
      </div>
    </div>
  );
}
