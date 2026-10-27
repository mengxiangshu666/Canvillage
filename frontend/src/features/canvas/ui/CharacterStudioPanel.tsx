// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useEffect, useMemo, useRef, useState } from "react";
import {
  ImagePlus,
  Loader2,
  Mic,
  Palette,
  PersonStanding,
  Ruler,
  Save,
  Shirt,
  Sparkles,
  Upload,
  Wand2,
  X,
} from "lucide-react";
import { toast } from "sonner";

import {
  useCharacterIdentities,
  useGenerateIdentityImage,
  useIdentityAttempts,
  useUpdateCharacter,
  useUpdateIdentity,
  useUploadIdentityImage,
} from "@/lib/queries/characters";
import { uploadFreezoneImage } from "@/api/ops";
import { dataUrlToBlob } from "@/features/canvas/application/imageData";
import { CANVAS_NODE_TYPES } from "@/features/canvas/domain/canvasNodes";
import {
  BODY_SLIDERS,
  MAKEUP_SWATCH_ROWS,
  DEFAULT_MAKEUP,
  SKIN_SLIDERS,
  applyMakeupClause,
  applyTaggedClause,
  composeMakeupClause,
  composeSliderClause,
  hasStudioPreset,
  readTaggedClause,
  toggleStudioPreset,
  type StudioMakeupState,
  type StudioPreset,
  type StudioPresetAudience,
  type StudioSliderDef,
} from "@/features/canvas/domain/characterStudioPresets";
import {
  hairSpecFromEnum,
  outfitSpecFromEnum,
  bodyProportionsFromSliders,
} from "@/features/canvas/domain/characterStudio3dSpecs";
import {
  CharacterStudio3dCanvas,
  type CharacterStudio3dHandle,
} from "@/features/canvas/ui/CharacterStudio3dCanvas";
import { useCanvasStore } from "@/stores/canvasStore";
import type { Character } from "@/types/character";

type RailId = "skin" | "body" | "makeup" | "wardrobe" | "pose" | "voice";
type WardrobeTab = "hair" | "costume" | "packs";
type AudienceFilter = "all" | StudioPresetAudience;

/**
 * 左侧悬浮图标栏：皮肤 / 身材 / 妆造 / 换装 / 姿态 / 音色。
 * title 里保留参考产品左栏的全名（脸部 & 身体特征 / 身材雕形 / 脸部精修 /
 * 姿势调整 / 音色配置），点开是各自的悬浮面板。
 */
const RAIL_TOOLS: Array<{
  id: RailId;
  label: string;
  title: string;
  hint?: string;
  icon: typeof Sparkles;
}> = [
  { id: "skin", label: "皮肤", title: "脸部 & 身体特征", icon: Sparkles },
  { id: "body", label: "身材", title: "身材雕形", hint: "限免", icon: Ruler },
  { id: "makeup", label: "妆造", title: "脸部精修", hint: "限免", icon: Palette },
  { id: "wardrobe", label: "换装", title: "换装（发型 / 服饰 / 服装包）", icon: Shirt },
  { id: "pose", label: "姿态", title: "姿势调整", icon: PersonStanding },
  { id: "voice", label: "音色", title: "音色配置", icon: Mic },
];

const AUDIENCE_CHIPS: Array<{ id: AudienceFilter; label: string }> = [
  { id: "all", label: "全部" },
  { id: "female", label: "女性" },
  { id: "male", label: "男士" },
  { id: "other", label: "其他" },
  { id: "limited", label: "限量" },
];

/** 服饰筛选没有「限量 / 男士」两档（参考产品服饰库只有 女性 / 其他）。 */
const COSTUME_AUDIENCE: AudienceFilter[] = ["all", "female", "other"];

/** 发型筛选里「其他」与「限量」对应的目录风格。 */
const HAIR_OTHER_STYLES = new Set(["古风", "民国", "民族", "儿童"]);
const HAIR_LIMITED_STYLES = new Set(["玄幻仙侠", "科幻未来"]);

/** 服饰库侧挂的服装包名单（参考产品造型室侧挂 dock 实录）。 */
const COSTUME_PACKS: Array<{ id: string; label: string; style?: string }> = [
  { id: "interstellar-city", label: "星际都市", style: "科幻未来" },
  { id: "sport-career", label: "运动职业" },
  { id: "christmas", label: "圣诞节日" },
  { id: "euro-luxury", label: "欧式大牌" },
  { id: "han-style", label: "汉式", style: "古风" },
  { id: "class", label: "班级" },
  { id: "guild-lectern", label: "讲台公会" },
  { id: "koi-pass", label: "锦鲤通 beta" },
];

interface StylingCatalogItem {
  kind: "hair" | "outfit" | "makeup";
  style: string;
  gender: string;
  name: string;
  key: string;
  enum: string;
}

interface VoiceLibraryItem {
  voiceId: string;
  name: string;
  gender: "female" | "male";
  ageGroup: string;
}

/** 目录卡的本地占位缩略图：按资产 key 稳定生成渐变，不依赖平台素材。 */
function catalogThumbColors(seed: string): { from: string; to: string } {
  let hash = 0;
  for (let index = 0; index < seed.length; index += 1) {
    hash = (hash * 31 + seed.charCodeAt(index)) >>> 0;
  }
  const hue = hash % 360;
  return {
    from: `hsl(${hue} 38% 62%)`,
    to: `hsl(${(hue + 42) % 360} 34% 32%)`,
  };
}

function parseSliderValues(
  clause: string,
  sliders: ReadonlyArray<StudioSliderDef>,
): Record<string, number> {
  const values: Record<string, number> = {};
  if (!clause) return values;
  for (const slider of sliders) {
    for (const text of [slider.prompt, slider.negative]) {
      if (!text) continue;
      const re = new RegExp(`${text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}(\\d+)%`);
      const match = clause.match(re);
      if (match) {
        values[slider.id] = text === slider.negative ? -Number(match[1]) : Number(match[1]);
        break;
      }
    }
  }
  return values;
}

/**
 * 角色造型室：3D 白模全幅铺底，左悬浮图标栏 + 悬浮参数面板，
 * 右悬浮「快捷生成」，底部悬浮胶囊（保存 / 放进画布 / 应用到当前节点 / 金积分）。
 *
 * 与参考产品的差别只有素材：发型/服饰/音色目录来自 libtv r79 静态注册表的
 * 名称蒸馏，缩略图是本地渐变占位，不下载平台素材。
 */
export function CharacterStudioPanel({
  project,
  character,
  onClose,
}: {
  project: string;
  character: Character;
  onClose?: () => void;
}) {
  const identitiesRes = useCharacterIdentities(project, character.name);
  const identities = identitiesRes.data?.data ?? [];
  const [identityId, setIdentityId] = useState<string | null>(null);
  const activeIdentity =
    identities.find((item) => item.identity_id === identityId) ?? identities[0] ?? null;

  const [description, setDescription] = useState(character.description ?? "");
  const [role, setRole] = useState(character.role ?? "");
  const [gender, setGender] = useState(character.gender ?? "");
  const [characterAge, setCharacterAge] = useState(character.age_group ?? "");
  const [appearance, setAppearance] = useState(activeIdentity?.appearance_details ?? "");
  const [facePrompt, setFacePrompt] = useState(activeIdentity?.face_prompt ?? "");
  const [bodyType, setBodyType] = useState(activeIdentity?.body_type ?? "");
  const [identityAge, setIdentityAge] = useState(activeIdentity?.age_group ?? "");
  const [tab, setTab] = useState<"3d" | "image" | "portrait" | "four">("3d");
  const [rail, setRail] = useState<RailId | null>(null);
  const [wardrobeTab, setWardrobeTab] = useState<WardrobeTab>("hair");
  const [audience, setAudience] = useState<AudienceFilter>("all");
  const [makeup, setMakeup] = useState<StudioMakeupState>(DEFAULT_MAKEUP);
  const [genPrompt, setGenPrompt] = useState("");
  const [genMode, setGenMode] = useState<"text" | "face">("text");
  const [busy, setBusy] = useState<"save" | "generate" | "upload" | "apply" | null>(null);
  const canvas3dRef = useRef<CharacterStudio3dHandle | null>(null);
  const [skinSliders, setSkinSliders] = useState<Record<string, number>>({});
  const [bodySliders, setBodySliders] = useState<Record<string, number>>({});
  const [catalogItems, setCatalogItems] = useState<StylingCatalogItem[]>([]);
  const [voiceItems, setVoiceItems] = useState<VoiceLibraryItem[]>([]);
  const [voiceSearch, setVoiceSearch] = useState("");
  const [voiceFilter, setVoiceFilter] = useState<"all" | "female" | "male">("all");
  const [selectedVoiceId, setSelectedVoiceId] = useState<string | null>(null);

  const updateCharacter = useUpdateCharacter(project, character.name);
  const updateIdentity = useUpdateIdentity(project, character.name);
  const generateIdentity = useGenerateIdentityImage(project, character.name);
  const uploadIdentityImage = useUploadIdentityImage(project, character.name);
  const attemptsRes = useIdentityAttempts(project, character.name, activeIdentity?.identity_id);
  const attempts = attemptsRes.data?.ok ? attemptsRes.data.data : null;

  useEffect(() => {
    setDescription(character.description ?? "");
    setRole(character.role ?? "");
    setGender(character.gender ?? "");
    setCharacterAge(character.age_group ?? "");
  }, [character]);

  useEffect(() => {
    setAppearance(activeIdentity?.appearance_details ?? "");
    setFacePrompt(activeIdentity?.face_prompt ?? "");
    setBodyType(activeIdentity?.body_type ?? "");
    setIdentityAge(activeIdentity?.age_group ?? "");
    setSkinSliders(
      parseSliderValues(readTaggedClause(activeIdentity?.face_prompt ?? "", "皮肤"), SKIN_SLIDERS),
    );
    setBodySliders(
      parseSliderValues(readTaggedClause(activeIdentity?.body_type ?? "", "身材"), BODY_SLIDERS),
    );
  }, [activeIdentity]);

  useEffect(() => {
    let cancelled = false;
    void fetch("/data/character-studio/stylingCatalog.json")
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (!cancelled && Array.isArray(data?.items)) setCatalogItems(data.items);
      })
      .catch(() => {});
    void fetch("/data/character-studio/voiceLibrary.json")
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (!cancelled && Array.isArray(data?.items)) setVoiceItems(data.items);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!onClose) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !busy) onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [busy, onClose]);

  const image =
    tab === "four"
      ? character.four_view_url ?? null
      : tab === "portrait"
        ? activeIdentity?.portrait_image_url ?? null
        : tab === "image"
          ? activeIdentity?.image_url ??
            activeIdentity?.portrait_image_url ??
            activeIdentity?.costume_image_url ??
            null
          : null;

  const tabs = [
    { id: "3d" as const, label: "3D 试衣" },
    { id: "image" as const, label: "造型图" },
    { id: "portrait" as const, label: "肖像" },
    { id: "four" as const, label: "四视图" },
  ];

  const hairItems = useMemo(
    () => catalogItems.filter((item) => item.kind === "hair"),
    [catalogItems],
  );
  const costumeItems = useMemo(
    () => catalogItems.filter((item) => item.kind === "outfit"),
    [catalogItems],
  );
  const hairPreset = (item: StylingCatalogItem): StudioPreset => ({
    id: item.key,
    label: item.name,
    prompt: `发型：${item.name}`,
  });
  const outfitPreset = (item: StylingCatalogItem): StudioPreset => ({
    id: item.key,
    label: item.name,
    prompt: `服饰：${item.name}`,
  });
  const activeHairItem = useMemo(
    () => hairItems.find((item) => hasStudioPreset(appearance, hairPreset(item))),
    [appearance, hairItems],
  );
  const activeOutfitItem = useMemo(
    () => costumeItems.find((item) => hasStudioPreset(appearance, outfitPreset(item))),
    [appearance, costumeItems],
  );
  const studioHair = useMemo(
    () => (activeHairItem ? hairSpecFromEnum(activeHairItem.enum) : null),
    [activeHairItem],
  );
  const studioOutfit = useMemo(
    () => (activeOutfitItem ? outfitSpecFromEnum(activeOutfitItem.enum) : null),
    [activeOutfitItem],
  );
  const studioBody = useMemo(() => bodyProportionsFromSliders(bodySliders), [bodySliders]);

  const updateMakeup = (patch: Partial<StudioMakeupState>) => {
    const next = { ...makeup, ...patch };
    setMakeup(next);
    setFacePrompt((value) => applyMakeupClause(value, composeMakeupClause(next)));
  };

  const updateSkinSliders = (patch: Partial<Record<string, number>>) => {
    const next: Record<string, number> = { ...skinSliders };
    for (const [key, value] of Object.entries(patch)) {
      if (typeof value === "number") next[key] = value;
    }
    setSkinSliders(next);
    setFacePrompt((value) =>
      applyTaggedClause(value, "皮肤", composeSliderClause("皮肤", SKIN_SLIDERS, next)),
    );
  };

  const updateBodySliders = (patch: Partial<Record<string, number>>) => {
    const next: Record<string, number> = { ...bodySliders };
    for (const [key, value] of Object.entries(patch)) {
      if (typeof value === "number") next[key] = value;
    }
    setBodySliders(next);
    setBodyType((value) =>
      applyTaggedClause(value, "身材", composeSliderClause("身材", BODY_SLIDERS, next)),
    );
  };

  const insertIntoCanvas = (url: string) => {
    const { currentViewport } = useCanvasStore.getState();
    const zoom = currentViewport?.zoom && currentViewport.zoom > 0 ? currentViewport.zoom : 1;
    const nodeType = CANVAS_NODE_TYPES.upload;
    const id = useCanvasStore.getState().addNode(
      nodeType,
      {
        x: (-(currentViewport?.x ?? 0) + window.innerWidth / 2) / zoom - 160,
        y: (-(currentViewport?.y ?? 0) + window.innerHeight / 2) / zoom - 160,
      },
      {
        displayName: `${character.name}${activeIdentity ? ` · ${activeIdentity.identity_name}` : ""}`,
        imageUrl: url,
        previewImageUrl: url,
        aspectRatio: "3:4",
      },
      { avoidCollisions: true },
    );
    if (!id) {
      toast.error("没能放进画布，请重试");
      return null;
    }
    useCanvasStore.getState().setSelectedNode(id);
    toast.success("已放进画布");
    return id as string;
  };

  /** 把当前 3D 画面存成项目图片，并作为参考接到选中的节点。 */
  const applyToSelectedNode = async () => {
    const dataUrl = canvas3dRef.current?.capture();
    if (!dataUrl) {
      toast.info("3D 预览还没准备好");
      return;
    }
    const targetNodeId = useCanvasStore.getState().selectedNodeId;
    setBusy("apply");
    try {
      const blob = dataUrlToBlob(dataUrl);
      const uploaded = await uploadFreezoneImage(
        project,
        blob,
        `${character.name}-3d.png`,
      );
      const newNodeId = insertIntoCanvas(uploaded.url);
      if (newNodeId && targetNodeId) {
        useCanvasStore.getState().addEdge(newNodeId, targetNodeId);
        toast.success("已作为参考接到当前节点");
      }
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "应用到当前节点失败");
    } finally {
      setBusy(null);
    }
  };

  const saveAll = async (): Promise<boolean> => {
    setBusy("save");
    try {
      await updateCharacter.mutateAsync({
        description: description.trim() || undefined,
        role: role.trim() || undefined,
        gender: gender.trim() || undefined,
        age_group: characterAge.trim() || undefined,
      });
      if (activeIdentity) {
        await updateIdentity.mutateAsync({
          identityId: activeIdentity.identity_id,
          data: {
            appearance_details: appearance.trim() || undefined,
            face_prompt: facePrompt.trim() || undefined,
            body_type: bodyType.trim() || undefined,
            age_group: identityAge.trim() || undefined,
          },
        });
      }
      toast.success("角色与造型已保存");
      return true;
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "保存失败");
      return false;
    } finally {
      setBusy(null);
    }
  };

  const runGenerate = async () => {
    if (!activeIdentity) {
      toast.info("先给这个角色建一个造型");
      return;
    }
    const saved = await saveAll();
    if (!saved) return;
    setBusy("generate");
    const mergedAppearance = [appearance.trim(), genPrompt.trim()].filter(Boolean).join("；");
    try {
      await generateIdentity.mutateAsync({
        identityId: activeIdentity.identity_id,
        appearanceDetails: mergedAppearance || undefined,
        facePrompt: facePrompt.trim() || undefined,
        ageGroup: identityAge.trim() || undefined,
        bodyType: bodyType.trim() || undefined,
      });
      setTab("image");
      toast.success("造型图已更新");
    } catch (error) {
      toast.error(
        error instanceof Error ? error.message : "生成造型图失败（可能需要较长时间）",
      );
    } finally {
      setBusy(null);
    }
  };

  const uploadReference = async (file: File | null) => {
    if (!file || !activeIdentity) return;
    setBusy("upload");
    try {
      await uploadIdentityImage.mutateAsync({
        identityName: activeIdentity.identity_name,
        file,
      });
      setTab("image");
      toast.success("参考图已更新");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "上传参考图失败");
    } finally {
      setBusy(null);
    }
  };

  const fieldClass =
    "mt-1.5 w-full rounded-lg border border-white/12 bg-white/[0.04] px-3 py-2 text-[12.5px] leading-5 text-white outline-none placeholder:text-white/25 focus:border-white/30";

  const voiceSlots = Object.keys(character.voice_samples_by_age_group ?? {});

  const wardrobeCards =
    wardrobeTab === "hair" ? hairItems : wardrobeTab === "costume" ? costumeItems : [];
  const wardrobeAudienceChips =
    wardrobeTab === "hair" ? AUDIENCE_CHIPS : AUDIENCE_CHIPS.filter((chip) => COSTUME_AUDIENCE.includes(chip.id));

  const railPanel = () => {
    if (!rail) return null;
    const title =
      rail === "wardrobe"
        ? wardrobeTab === "hair"
          ? "换装 · 造型（发型）"
          : wardrobeTab === "costume"
            ? "换装 · 服饰"
            : "换装 · 服装包"
        : RAIL_TOOLS.find((tool) => tool.id === rail)?.title ?? "";
    return (
      <aside
        aria-label={title}
        className="absolute left-[64px] top-2.5 z-10 flex max-h-[calc(100%-76px)] w-[276px] flex-col overflow-hidden rounded-2xl border border-white/[0.1] bg-[#141519]/95 shadow-[0_18px_50px_rgba(0,0,0,0.45)] backdrop-blur-md"
      >
        <div className="flex shrink-0 items-center justify-between border-b border-white/[0.07] px-3 py-2">
          <span className="truncate text-[12px] font-medium">{title}</span>
          <button
            type="button"
            aria-label="收起面板"
            onClick={() => setRail(null)}
            className="inline-flex size-6 shrink-0 items-center justify-center rounded-lg text-white/50 transition-colors hover:bg-white/10 hover:text-white"
          >
            <X className="size-3.5" />
          </button>
        </div>
        <div className="ui-scrollbar flex min-h-0 flex-1 flex-col gap-2.5 overflow-y-auto px-3 py-2.5">
          {(rail === "skin" || rail === "body") && (
            <div className="flex flex-col gap-2.5">
              <p className="text-[10.5px] leading-4 text-white/30">
                {rail === "skin"
                  ? "皮肤效果强度 0–100，写入面部锚点；白模不显示贴图，效果以生成图为准。"
                  : "身材雕形是 ± 双向滑杆（-100~100，0 为中性），写入体型并驱动白模比例。"}
              </p>
              {(rail === "skin" ? SKIN_SLIDERS : BODY_SLIDERS).map((slider) => {
                const value = (rail === "skin" ? skinSliders : bodySliders)[slider.id] ?? 0;
                return (
                  <label key={slider.id} className="block">
                    <span className="flex items-center justify-between text-[10.5px] text-white/45">
                      {slider.label}
                      <span className="text-white/25">{value}</span>
                    </span>
                    <input
                      type="range"
                      min={slider.min}
                      max={slider.max}
                      step={1}
                      value={value}
                      aria-label={slider.label}
                      onChange={(event) =>
                        (rail === "skin" ? updateSkinSliders : updateBodySliders)({
                          [slider.id]: Number(event.target.value),
                        })
                      }
                      className="mt-1 w-full accent-white"
                    />
                  </label>
                );
              })}
            </div>
          )}

          {rail === "makeup" && (
            <div className="flex flex-col gap-3">
              <p className="text-[10.5px] leading-4 text-white/30">
                色板与滑杆写进面部锚点；白模不显示妆面，效果以生成图为准。
              </p>
              {MAKEUP_SWATCH_ROWS.map((row) => (
                <div key={row.id}>
                  <div className="mb-1 text-[10.5px] text-white/40">{row.label}</div>
                  <div className="flex gap-1.5">
                    {row.swatches.map((swatch) => {
                      const active = makeup[row.id] === swatch.id;
                      return (
                        <button
                          key={swatch.id}
                          type="button"
                          title={swatch.label}
                          aria-label={`${row.label}${swatch.label}`}
                          aria-pressed={active}
                          onClick={() => updateMakeup({ [row.id]: swatch.id })}
                          className={`size-6 rounded-full border-2 transition-transform ${
                            active ? "border-white scale-110" : "border-white/20"
                          }`}
                          style={{ backgroundColor: swatch.color }}
                        />
                      );
                    })}
                  </div>
                </div>
              ))}
              {(
                [
                  ["brow", "眉妆"],
                  ["eyeshadow", "眼影"],
                  ["lipIntensity", "唇色"],
                ] as const
              ).map(([key, label]) => (
                <label key={key} className="block text-[10.5px] text-white/40">
                  <span className="flex items-center justify-between">
                    {label}
                    <span className="text-white/25">{makeup[key]}%</span>
                  </span>
                  <input
                    type="range"
                    min={0}
                    max={100}
                    value={makeup[key]}
                    aria-label={`${label}浓度`}
                    onChange={(event) =>
                      updateMakeup({ [key]: Number(event.target.value) })
                    }
                    className="mt-1 w-full accent-white"
                  />
                </label>
              ))}
              <div className="flex gap-2">
                <button
                  type="button"
                  onClick={() => {
                    setMakeup(DEFAULT_MAKEUP);
                    setFacePrompt((value) =>
                      applyMakeupClause(value, composeMakeupClause(DEFAULT_MAKEUP)),
                    );
                  }}
                  className="h-7 flex-1 rounded-md border border-white/10 text-[11px] text-white/50 hover:bg-white/[0.06] hover:text-white/80"
                >
                  全部重置
                </button>
                <button
                  type="button"
                  onClick={() =>
                    updateMakeup({ brow: 50, eyeshadow: 50, lipIntensity: 50 })
                  }
                  className="h-7 flex-1 rounded-md border border-white/10 text-[11px] text-white/50 hover:bg-white/[0.06] hover:text-white/80"
                >
                  自动均匀
                </button>
                <button
                  type="button"
                  aria-pressed={makeup.autoSet}
                  onClick={() => updateMakeup({ autoSet: !makeup.autoSet })}
                  className={`h-7 flex-1 rounded-md border text-[11px] ${
                    makeup.autoSet
                      ? "border-white/30 bg-white/[0.1] text-white/85"
                      : "border-white/10 text-white/45 hover:text-white/75"
                  }`}
                >
                  自动定妆
                </button>
              </div>
            </div>
          )}

          {rail === "wardrobe" && (
            <div className="flex flex-col gap-2.5">
              <div className="flex gap-1 rounded-lg border border-white/10 bg-white/[0.03] p-1">
                {(
                  [
                    ["hair", "造型"],
                    ["costume", "服饰"],
                    ["packs", "服装包"],
                  ] as const
                ).map(([id, label]) => (
                  <button
                    key={id}
                    type="button"
                    aria-pressed={wardrobeTab === id}
                    onClick={() => {
                      setWardrobeTab(id);
                      setAudience("all");
                    }}
                    className={`h-6.5 flex-1 rounded-md text-[11px] transition-colors ${
                      wardrobeTab === id
                        ? "bg-white/[0.12] text-white"
                        : "text-white/45 hover:text-white/75"
                    }`}
                  >
                    {label}
                  </button>
                ))}
              </div>
              {wardrobeTab !== "packs" ? (
                <>
                  <div className="flex flex-wrap gap-1">
                    {wardrobeAudienceChips.map((chip) => (
                      <button
                        key={chip.id}
                        type="button"
                        aria-pressed={audience === chip.id}
                        onClick={() => setAudience(chip.id)}
                        className={`h-6 rounded-md px-2 text-[10.5px] transition-colors ${
                          audience === chip.id
                            ? "bg-white/[0.12] text-white"
                            : "text-white/40 hover:bg-white/[0.06] hover:text-white/75"
                        }`}
                      >
                        {chip.label}
                      </button>
                    ))}
                  </div>
                  <p className="text-[10.5px] leading-4 text-white/28">
                    点选写入服饰造型，再点一次移除；同名目录项会同步 3D 替身轮廓。
                  </p>
                  <div className="grid grid-cols-3 gap-1.5">
                    {wardrobeCards
                      .filter((item) => {
                        if (audience === "female") return item.gender === "女";
                        if (audience === "male") return item.gender === "男";
                        if (audience === "other")
                          return wardrobeTab === "hair"
                            ? HAIR_OTHER_STYLES.has(item.style)
                            : item.gender !== "女";
                        if (audience === "limited") return HAIR_LIMITED_STYLES.has(item.style);
                        return true;
                      })
                      .map((item) => {
                        const preset =
                          wardrobeTab === "hair" ? hairPreset(item) : outfitPreset(item);
                        const active = hasStudioPreset(appearance, preset);
                        const colors = catalogThumbColors(item.key);
                        return (
                          <button
                            key={item.key}
                            type="button"
                            title={preset.prompt}
                            aria-pressed={active}
                            onClick={() =>
                              setAppearance((value) => toggleStudioPreset(value, preset))
                            }
                            className={`overflow-hidden rounded-lg border text-left transition-colors ${
                              active
                                ? "border-white/45 bg-white/[0.1]"
                                : "border-white/10 bg-white/[0.03] hover:border-white/25"
                            }`}
                          >
                            <span
                              className="block aspect-square w-full"
                              style={{
                                background: `linear-gradient(135deg, ${colors.from}, ${colors.to})`,
                              }}
                              aria-hidden="true"
                            />
                            <span
                              className={`block truncate px-1.5 py-1 text-[10px] ${
                                active ? "text-white" : "text-white/60"
                              }`}
                            >
                              {item.name}
                            </span>
                          </button>
                        );
                      })}
                  </div>
                </>
              ) : (
                <>
                  <p className="text-[10.5px] leading-4 text-white/28">
                    服装包是平台付费资产；这里只保留目录，可映射的风格会切到服饰页签挑选。
                  </p>
                  <div className="flex flex-col gap-1.5">
                    {COSTUME_PACKS.map((pack) => (
                      <button
                        key={pack.id}
                        type="button"
                        onClick={() => {
                          if (pack.style) {
                            setWardrobeTab("costume");
                            setAudience("all");
                            toast.success(`已切到服饰页签，风格「${pack.label}」请在列表里挑选`);
                          } else {
                            toast.info("该服饰包待接入");
                          }
                        }}
                        className="flex items-center justify-between rounded-lg border border-white/10 bg-white/[0.03] px-2.5 py-2 text-left text-[11.5px] text-white/70 transition-colors hover:border-white/20 hover:bg-white/[0.07] hover:text-white"
                      >
                        <span>{pack.label}</span>
                        <span className="rounded-full border border-emerald-300/25 bg-emerald-400/10 px-1.5 py-0.5 text-[9px] text-emerald-200/80">
                          有免费名额 时长不限
                        </span>
                      </button>
                    ))}
                  </div>
                </>
              )}
            </div>
          )}

          {rail === "pose" && (
            <div className="flex flex-col gap-2">
              <p className="text-[10.5px] leading-4 text-white/30">
                白模当前是 T-pose，用来量比例和看衣服轮廓。
              </p>
              <div className="flex flex-wrap gap-1.5">
                <span className="rounded-md border border-white/25 bg-white/[0.1] px-2 py-1.5 text-[11px] text-white/85">
                  T-pose（当前）
                </span>
                {["站立", "交谈", "行走"].map((label) => (
                  <span
                    key={label}
                    title="姿态动画待接入"
                    className="rounded-md border border-white/8 px-2 py-1.5 text-[11px] text-white/22"
                  >
                    {label}
                  </span>
                ))}
              </div>
              <p className="text-[10.5px] leading-4 text-white/25">
                参考产品的姿态库依赖 3D 骨骼动作；我们目前只开放静态白模，避免衣服与
                骨骼错位。
              </p>
            </div>
          )}

          {rail === "voice" && (
            <div className="flex flex-col gap-2.5">
              <input
                value={voiceSearch}
                onChange={(event) => setVoiceSearch(event.target.value)}
                placeholder="搜索音色"
                aria-label="搜索音色"
                className="h-8 w-full rounded-lg border border-white/10 bg-white/[0.04] px-2.5 text-[12px] text-white outline-none placeholder:text-white/30 focus:border-white/25"
              />
              <div className="flex gap-1">
                {(
                  [
                    ["all", "全部"],
                    ["female", "中文女"],
                    ["male", "中文男"],
                  ] as const
                ).map(([id, label]) => (
                  <button
                    key={id}
                    type="button"
                    aria-pressed={voiceFilter === id}
                    onClick={() => setVoiceFilter(id)}
                    className={`h-6.5 flex-1 rounded-md px-2 py-1 text-[10.5px] transition-colors ${
                      voiceFilter === id
                        ? "bg-white/[0.12] text-white"
                        : "text-white/40 hover:bg-white/[0.06] hover:text-white/75"
                    }`}
                  >
                    {label}
                  </button>
                ))}
              </div>
              <div className="flex max-h-[300px] flex-col gap-0.5 overflow-y-auto">
                {voiceItems
                  .filter((item) => {
                    if (voiceFilter === "female") return item.gender === "female";
                    if (voiceFilter === "male") return item.gender === "male";
                    return true;
                  })
                  .filter((item) => {
                    const query = voiceSearch.trim().toLowerCase();
                    if (!query) return true;
                    return `${item.name} ${item.voiceId} ${item.ageGroup}`
                      .toLowerCase()
                      .includes(query);
                  })
                  .map((item) => (
                    <button
                      key={item.voiceId}
                      type="button"
                      aria-pressed={selectedVoiceId === item.voiceId}
                      onClick={() => {
                        setSelectedVoiceId(item.voiceId);
                        toast.success(`已选择音色：${item.name}`);
                      }}
                      className={`flex items-center justify-between gap-2 rounded-lg px-2.5 py-1.5 text-left text-[11.5px] transition-colors ${
                        selectedVoiceId === item.voiceId
                          ? "bg-white/[0.12] text-white"
                          : "text-white/60 hover:bg-white/[0.06] hover:text-white"
                      }`}
                    >
                      <span className="min-w-0 truncate">{item.name}</span>
                      <span className="shrink-0 rounded-md border border-white/10 px-1.5 py-0.5 text-[9.5px] text-white/35">
                        {item.ageGroup}
                      </span>
                    </button>
                  ))}
              </div>
              {voiceSlots.length > 0 ? (
                <p className="text-[10px] leading-4 text-white/25">
                  该角色已有音色样本：{voiceSlots.join("、")}；这里选择的音色在生成配音时优先使用。
                </p>
              ) : (
                <p className="text-[10px] leading-4 text-white/25">
                  音色样本在角色页录制；这里选择的音色在生成配音时优先使用。
                </p>
              )}
            </div>
          )}
        </div>
      </aside>
    );
  };

  return (
    <div
      className="fixed inset-0 z-[110] flex flex-col bg-[#0d0e11] text-white"
      role="dialog"
      aria-modal="true"
      aria-label="角色造型室"
      onClick={(event) => event.stopPropagation()}
    >
      <header className="flex h-12 shrink-0 items-center justify-between border-b border-white/[0.07] px-4">
        <div className="flex min-w-0 items-center gap-2.5">
          <h2 className="truncate text-[14.5px] font-semibold">{character.name}</h2>
          <span className="flex flex-wrap items-center gap-1 text-[10px] text-white/38">
            {[gender, characterAge, role].filter(Boolean).map((tag) => (
              <span key={tag} className="rounded-full border border-white/10 px-2 py-0.5">
                {tag}
              </span>
            ))}
          </span>
        </div>
        <button
          type="button"
          aria-label="关闭"
          disabled={Boolean(busy)}
          onClick={onClose}
          className="inline-flex size-8 items-center justify-center rounded-lg text-white/55 transition-colors hover:bg-white/10 hover:text-white disabled:opacity-40"
        >
          <X className="size-4" />
        </button>
      </header>

      <div className="relative min-h-0 flex-1 overflow-hidden">
        {/* 全幅底：3D 白模或图片 */}
        <div className="absolute inset-0">
          {tab === "3d" ? (
            <CharacterStudio3dCanvas
              ref={canvas3dRef}
              bare
              hair={studioHair}
              outfit={studioOutfit}
              body={studioBody}
              onUnsupported={(reason) => {
                toast.info(`3D 预览不可用，已切到造型图：${reason}`);
                setTab("image");
              }}
            />
          ) : (
            <div className="absolute inset-0 flex items-center justify-center bg-[radial-gradient(circle,rgba(255,255,255,0.06)_1px,transparent_1px)] [background-size:20px_20px]">
              {image ? (
                <img
                  src={image}
                  alt={character.name}
                  className="max-h-full max-w-full object-contain"
                />
              ) : (
                <p className="text-[12px] text-white/30">
                  {tab === "four" ? "还没有四视图" : "还没有造型图"}
                </p>
              )}
            </div>
          )}
        </div>

        {/* 顶部中央：视图切换胶囊 */}
        <div className="absolute left-1/2 top-2.5 z-10 flex -translate-x-1/2 gap-0.5 rounded-full border border-white/[0.1] bg-[#141519]/92 px-1 py-1 backdrop-blur-md">
          {tabs.map((item) => (
            <button
              key={item.id}
              type="button"
              aria-pressed={tab === item.id}
              onClick={() => setTab(item.id)}
              className={`h-7 rounded-full px-3 text-[11.5px] transition-colors ${
                tab === item.id
                  ? "bg-white text-black"
                  : "text-white/48 hover:bg-white/[0.07] hover:text-white"
              }`}
            >
              {item.label}
            </button>
          ))}
        </div>

        {/* 左侧：悬浮图标工具栏 */}
        <nav
          aria-label="造型工具"
          className="absolute left-2.5 top-2.5 z-10 flex flex-col gap-1 rounded-2xl border border-white/[0.1] bg-[#141519]/92 p-1.5 backdrop-blur-md"
        >
          {RAIL_TOOLS.map((tool) => {
            const Icon = tool.icon;
            const active = rail === tool.id;
            return (
              <button
                key={tool.id}
                type="button"
                title={tool.title}
                aria-pressed={active}
                aria-label={tool.label}
                onClick={() => {
                  setRail(active ? null : tool.id);
                  if (tool.id === "wardrobe") setAudience("all");
                }}
                className={`relative flex size-11 flex-col items-center justify-center gap-0.5 rounded-xl transition-colors ${
                  active
                    ? "bg-white/[0.14] text-white"
                    : "text-white/45 hover:bg-white/[0.07] hover:text-white/85"
                }`}
              >
                <Icon className="size-4" />
                <span className="text-[9px] leading-none">{tool.label}</span>
                {tool.hint ? (
                  <span className="absolute right-1 top-1 rounded-full bg-amber-400/15 px-1 text-[8px] text-amber-200/80">
                    {tool.hint}
                  </span>
                ) : null}
              </button>
            );
          })}
        </nav>

        {/* 左侧：当前工具的悬浮面板 */}
        {railPanel()}

        {/* 右侧：悬浮「快捷生成」面板 */}
        <aside
          aria-label="快捷生成"
          className="absolute right-2.5 top-2.5 z-10 flex max-h-[calc(100%-76px)] w-[288px] flex-col overflow-hidden rounded-2xl border border-white/[0.1] bg-[#141519]/95 shadow-[0_18px_50px_rgba(0,0,0,0.45)] backdrop-blur-md"
        >
          <div className="flex shrink-0 items-center justify-between border-b border-white/[0.07] px-3 py-2">
            <span className="text-[12px] font-medium">快捷生成</span>
            <span className="text-[10px] text-white/30">
              造型图 {attempts?.image_attempts ?? 0} · 肖像 {attempts?.portrait_attempts ?? 0}
            </span>
          </div>
          <div className="ui-scrollbar flex min-h-0 flex-1 flex-col gap-2.5 overflow-y-auto px-3 py-2.5">
            <p className="text-[10.5px] leading-4 text-white/28">
              用左侧属性生成新的造型图；每次生成都消耗图片额度。
            </p>
            <textarea
              value={genPrompt}
              onChange={(event) => setGenPrompt(event.target.value)}
              rows={4}
              aria-label="生成补充描述"
              placeholder="补充描述：光线、姿态、场景…（会并入本次造型描述）"
              className={`${fieldClass} resize-none`}
            />
            <div className="flex gap-1 rounded-lg border border-white/10 bg-white/[0.03] p-1">
              {(
                [
                  ["text", "文本生图"],
                  ["face", "人脸微调模型"],
                ] as const
              ).map(([id, label]) => (
                <button
                  key={id}
                  type="button"
                  aria-pressed={genMode === id}
                  onClick={() => setGenMode(id)}
                  className={`h-7 flex-1 rounded-md text-[11px] transition-colors ${
                    genMode === id
                      ? "bg-white/[0.12] text-white"
                      : "text-white/45 hover:text-white/75"
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>
            <button
              type="button"
              disabled={!activeIdentity || Boolean(busy)}
              onClick={() => void runGenerate()}
              className="inline-flex h-9 w-full items-center justify-center gap-2 rounded-lg bg-white text-[12.5px] font-medium text-black transition-opacity hover:opacity-90 disabled:opacity-40"
            >
              {busy === "generate" ? (
                <Loader2 className="size-3.5 animate-spin" />
              ) : (
                <Wand2 className="size-3.5" />
              )}
              {busy === "generate" ? "生成中…" : "确认生成"}
            </button>
            <p className="flex items-center justify-center gap-1 text-[10.5px] text-white/35">
              ⚡70 <span className="text-white/25">金积分/次 · 完成后造型图自动刷新</span>
            </p>

            <details className="rounded-lg border border-white/[0.07] bg-white/[0.02] px-2.5 py-2">
              <summary className="cursor-pointer text-[10.5px] text-white/35">
                文本锚点与基础属性
              </summary>
              <div className="mt-2 flex flex-col gap-2">
                <label className="block text-[10.5px] text-white/45">
                  简介
                  <textarea
                    value={description}
                    onChange={(event) => setDescription(event.target.value)}
                    rows={2}
                    placeholder="年龄、身份、气质"
                    className={`${fieldClass} resize-none`}
                  />
                </label>
                <div className="grid grid-cols-2 gap-2">
                  <label className="block text-[10.5px] text-white/45">
                    定位
                    <input
                      value={role}
                      onChange={(event) => setRole(event.target.value)}
                      placeholder="女主 / 配角"
                      className={fieldClass}
                    />
                  </label>
                  <label className="block text-[10.5px] text-white/45">
                    性别
                    <input
                      value={gender}
                      onChange={(event) => setGender(event.target.value)}
                      placeholder="女 / 男"
                      className={fieldClass}
                    />
                  </label>
                </div>
                <div className="grid grid-cols-2 gap-2">
                  <label className="block text-[10.5px] text-white/45">
                    年龄段
                    <input
                      value={characterAge}
                      onChange={(event) => setCharacterAge(event.target.value)}
                      placeholder="青年 / 中年"
                      className={fieldClass}
                    />
                  </label>
                  <label className="block text-[10.5px] text-white/45">
                    造型年龄段
                    <input
                      value={identityAge}
                      onChange={(event) => setIdentityAge(event.target.value)}
                      placeholder="青年 / 中年"
                      className={fieldClass}
                    />
                  </label>
                </div>
                <label className="block text-[10.5px] text-white/45">
                  面部特征
                  <textarea
                    value={facePrompt}
                    onChange={(event) => setFacePrompt(event.target.value)}
                    rows={3}
                    placeholder="骨相、五官、皮肤质感"
                    className={`${fieldClass} resize-none`}
                  />
                </label>
                <label className="block text-[10.5px] text-white/45">
                  服饰造型
                  <textarea
                    value={appearance}
                    onChange={(event) => setAppearance(event.target.value)}
                    rows={3}
                    placeholder="服装、发型、妆容、配饰"
                    className={`${fieldClass} resize-none`}
                  />
                </label>
                <div className="grid grid-cols-2 gap-2">
                  <label className="block text-[10.5px] text-white/45">
                    体型
                    <input
                      value={bodyType}
                      onChange={(event) => setBodyType(event.target.value)}
                      placeholder="纤细 / 健壮"
                      className={fieldClass}
                    />
                  </label>
                  <label className="block text-[10.5px] text-white/45">
                    造型
                    <select
                      value={activeIdentity?.identity_id ?? ""}
                      onChange={(event) => setIdentityId(event.target.value)}
                      className={fieldClass}
                      aria-label="选择造型"
                    >
                      {identities.length === 0 ? <option value="">还没有造型</option> : null}
                      {identities.map((identity) => (
                        <option key={identity.identity_id} value={identity.identity_id}>
                          {identity.identity_name}
                        </option>
                      ))}
                    </select>
                  </label>
                </div>
              </div>
            </details>
          </div>
        </aside>

        {/* 底部：悬浮胶囊（保存 / 放进画布 / 应用到当前节点 / 金积分） */}
        <div className="absolute bottom-3 left-1/2 z-10 flex -translate-x-1/2 items-center gap-1.5 rounded-full border border-white/[0.1] bg-[#141519]/92 px-2 py-1.5 backdrop-blur-md">
          <button
            type="button"
            disabled={Boolean(busy)}
            onClick={() => void saveAll()}
            className="inline-flex h-8 items-center gap-1.5 rounded-full bg-white px-4 text-[12px] font-medium text-black transition-opacity hover:opacity-90 disabled:opacity-40"
          >
            {busy === "save" ? (
              <Loader2 className="size-3.5 animate-spin" />
            ) : (
              <Save className="size-3.5" />
            )}
            保存
          </button>
          {image ? (
            <button
              type="button"
              disabled={Boolean(busy)}
              onClick={() => image && insertIntoCanvas(image)}
              className="inline-flex h-8 items-center gap-1.5 rounded-full border border-white/12 px-3.5 text-[12px] text-white/78 transition-colors hover:bg-white/8 hover:text-white disabled:opacity-35"
            >
              <ImagePlus className="size-3.5" />
              放进画布
            </button>
          ) : null}
          {tab === "3d" ? (
            <button
              type="button"
              disabled={Boolean(busy)}
              onClick={() => void applyToSelectedNode()}
              className="inline-flex h-8 items-center gap-1.5 rounded-full border border-white/12 px-3.5 text-[12px] text-white/78 transition-colors hover:bg-white/8 hover:text-white disabled:opacity-35"
            >
              {busy === "apply" ? (
                <Loader2 className="size-3.5 animate-spin" />
              ) : (
                <ImagePlus className="size-3.5" />
              )}
              应用到当前节点
            </button>
          ) : null}
          <label className="inline-flex h-8 cursor-pointer items-center gap-1.5 rounded-full border border-white/12 px-3.5 text-[12px] text-white/78 transition-colors hover:bg-white/8 hover:text-white">
            {busy === "upload" ? (
              <Loader2 className="size-3.5 animate-spin" />
            ) : (
              <Upload className="size-3.5" />
            )}
            上传参考图
            <input
              type="file"
              accept="image/*"
              className="hidden"
              aria-label="上传造型参考图"
              onChange={(event) => {
                void uploadReference(event.target.files?.[0] ?? null);
                event.target.value = "";
              }}
            />
          </label>
          <span
            title="参考产品里这是单次生成的价格读数（r79 实证：⚡70 = power/calculator 单次生成价）；本产品的生成额度在角色页任务里计。"
            className="inline-flex h-8 items-center gap-1 rounded-full border border-amber-300/25 bg-amber-400/10 px-3 text-[11px] text-amber-100/80"
          >
            ⚡70
          </span>
        </div>
      </div>
    </div>
  );
}