// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "@tanstack/react-router";
import {
  ExternalLink,
  ImagePlus,
  Loader2,
  Plus,
  Search,
  UserPlus,
  Wand2,
  X,
} from "lucide-react";
import { toast } from "sonner";

import {
  useCharacterIdentities,
  useCharacters,
  useCreateCharacter,
} from "@/lib/queries/characters";
import {
  CharacterCreateDialog,
  type CharacterCreateMode,
} from "@/features/canvas/ui/CharacterCreateDialog";
import { CharacterStudioPanel } from "@/features/canvas/ui/CharacterStudioPanel";
import { PROJECT_SECTION_ROUTES } from "@/components/layout/project-navigation-routes";
import {
  CANVAS_NODE_TYPES,
  type CanvasNodeType,
} from "@/features/canvas/domain/canvasNodes";
import { useCanvasStore } from "@/stores/canvasStore";
import type { Character, Identity } from "@/types/character";

/**
 * 画布里的角色造型室：左边角色，右边造型（身份），点一张造型直接落到画布。
 *
 * 生成/上传造型图仍走角色页（那里有完整的三视图、服装图和任务进度），这里只做
 * 「挑人、挑造型、放进画布」这一条最短路径，避免把生成表单再复制一份。
 */
export interface CharacterStudioModalProps {
  open: boolean;
  project: string;
  onClose: () => void;
}

function identityImage(identity: Identity): string | null {
  return (
    identity.portrait_image_url ||
    identity.image_url ||
    identity.costume_image_url ||
    null
  );
}

function characterImage(character: Character): string | null {
  return character.portrait_url || character.four_view_url || null;
}

function viewportCenterPosition(nodeType: CanvasNodeType) {
  const { currentViewport } = useCanvasStore.getState();
  const zoom = currentViewport?.zoom && currentViewport.zoom > 0 ? currentViewport.zoom : 1;
  const screenX = typeof window === "undefined" ? 640 : window.innerWidth / 2;
  const screenY = typeof window === "undefined" ? 360 : window.innerHeight / 2;
  const width = nodeType === CANVAS_NODE_TYPES.upload ? 320 : 480;
  return {
    x: (-(currentViewport?.x ?? 0) + screenX) / zoom - width / 2,
    y: (-(currentViewport?.y ?? 0) + screenY) / zoom - 160,
  };
}

interface OfficialCharacterItem {
  id: string;
  name: string;
  categoryCode: string;
  categoryLabel: string;
  gender: string;
  ageStage: string;
  region: string;
  era: string;
  temperament: string;
  shortDescription: string;
  appearanceDescription: string;
  personalityDescription: string;
  sceneHint: string;
}

export function CharacterStudioModal({
  open,
  project,
  onClose,
}: CharacterStudioModalProps) {
  const navigate = useNavigate();
  const charactersRes = useCharacters(project);
  const characters = charactersRes.data?.data ?? [];
  const [selectedName, setSelectedName] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [libraryTab, setLibraryTab] = useState<"mine" | "official">("mine");
  const [genderFilter, setGenderFilter] = useState("全部");
  const [ageFilter, setAgeFilter] = useState("全部");
  const [regionFilter, setRegionFilter] = useState("全部");
  const [officialItems, setOfficialItems] = useState<OfficialCharacterItem[]>([]);
  const [officialBusy, setOfficialBusy] = useState<string | null>(null);
  const [createMode, setCreateMode] = useState<CharacterCreateMode | null>(null);
  const [studioOpen, setStudioOpen] = useState(false);

  const createCharacter = useCreateCharacter(project);

  useEffect(() => {
    if (!open || libraryTab !== "official" || officialItems.length) return;
    let cancelled = false;
    void fetch("/data/character-studio/officialCharacters.json")
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (!cancelled && Array.isArray(data?.items)) setOfficialItems(data.items);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [libraryTab, officialItems.length, open]);

  const activeName = selectedName ?? characters[0]?.name ?? null;
  const activeCharacter = useMemo(
    () => characters.find((character) => character.name === activeName) ?? null,
    [activeName, characters],
  );
  const identitiesRes = useCharacterIdentities(project, activeName ?? "");
  const identities = identitiesRes.data?.data ?? [];

  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open, onClose]);

  const filtered = useMemo(() => {
    const query = search.trim().toLowerCase();
    return characters.filter((character) => {
      if (genderFilter !== "全部" && !(character.gender ?? "").includes(genderFilter)) {
        return false;
      }
      if (ageFilter !== "全部" && !(character.age_group ?? "").includes(ageFilter)) {
        return false;
      }
      if (!query) return true;
      return `${character.name} ${character.description ?? ""} ${character.role ?? ""}`
        .toLowerCase()
        .includes(query);
    });
  }, [ageFilter, characters, genderFilter, search]);

  const officialFiltered = useMemo(() => {
    const query = search.trim().toLowerCase();
    return officialItems.filter((item) => {
      if (genderFilter !== "全部" && item.gender !== genderFilter) return false;
      if (ageFilter !== "全部" && item.ageStage !== ageFilter) return false;
      if (regionFilter !== "全部" && item.region !== regionFilter) return false;
      if (!query) return true;
      return `${item.name} ${item.shortDescription} ${item.categoryLabel} ${item.temperament}`
        .toLowerCase()
        .includes(query);
    });
  }, [ageFilter, genderFilter, officialItems, regionFilter, search]);

  const applyOfficial = async (item: OfficialCharacterItem) => {
    setOfficialBusy(item.id);
    try {
      const created = await createCharacter.mutateAsync({
        name: item.name,
        description: `${item.shortDescription}性格：${item.personalityDescription}`,
        role: item.categoryLabel,
        gender: item.gender,
        age_group: item.ageStage,
        face_prompt: item.appearanceDescription,
      });
      const name = created?.data?.name ?? item.name;
      setSelectedName(name);
      setLibraryTab("mine");
      setStudioOpen(true);
      toast.success(`已把「${item.name}」加进我的角色库并进入造型室`);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "应用官方角色失败");
    } finally {
      setOfficialBusy(null);
    }
  };

  const insertImage = (url: string, label: string) => {
    const nodeType = CANVAS_NODE_TYPES.upload;
    const id = useCanvasStore.getState().addNode(
      nodeType,
      viewportCenterPosition(nodeType),
      {
        displayName: label,
        imageUrl: url,
        previewImageUrl: url,
        aspectRatio: "1:1",
      },
      { avoidCollisions: true },
    );
    if (!id) {
      toast.error("没能放进画布，请重试");
      return;
    }
    useCanvasStore.getState().setSelectedNode(id);
    toast.success(`已把「${label}」放到画布上`);
    onClose();
  };

  const openCharactersPage = () => {
    onClose();
    void navigate({
      to: PROJECT_SECTION_ROUTES.characters,
      params: { project },
    });
  };

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-[95] flex items-center justify-center bg-black/60 p-6 backdrop-blur-[2px]"
      role="dialog"
      aria-modal="true"
      aria-label="角色造型室"
      onClick={onClose}
    >
      <div
        className="flex h-[min(760px,86vh)] w-[min(1160px,94vw)] flex-col overflow-hidden rounded-[16px] border border-white/[0.12] bg-[#101114]/97 text-white shadow-[0_30px_90px_rgba(0,0,0,0.6)]"
        onClick={(event) => event.stopPropagation()}
      >
        <header className="flex shrink-0 items-center justify-between gap-4 border-b border-white/[0.07] px-6 py-4">
          <div className="min-w-0">
            <h2 className="text-[17px] font-semibold">角色造型室</h2>
            <div className="mt-2 flex items-center gap-1">
              {(
                [
                  ["mine", "我的角色库"],
                  ["official", "官方角色库"],
                ] as const
              ).map(([id, label]) => (
                <button
                  key={id}
                  type="button"
                  aria-pressed={libraryTab === id}
                  onClick={() => setLibraryTab(id)}
                  className={`h-7 rounded-md px-2.5 text-[11.5px] transition-colors ${
                    libraryTab === id
                      ? "bg-white/[0.12] text-white"
                      : "text-white/42 hover:bg-white/[0.06] hover:text-white/75"
                  }`}
                >
                  {label}
                </button>
              ))}
              <button
                type="button"
                onClick={openCharactersPage}
                className="inline-flex h-7 items-center gap-1.5 rounded-md px-2.5 text-[11.5px] text-white/42 transition-colors hover:bg-white/6 hover:text-white/75"
              >
                <ExternalLink className="size-3" />
                生成 / 上传造型图
              </button>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              aria-label="关闭"
              onClick={onClose}
              className="inline-flex size-8 items-center justify-center rounded-lg text-white/55 transition-colors hover:bg-white/10 hover:text-white"
            >
              <X className="size-4" />
            </button>
          </div>
        </header>

        <div className="flex min-h-0 flex-1">
          <aside className="flex w-[300px] shrink-0 flex-col border-r border-white/[0.07]">
            <div className="flex items-center gap-2 px-4 py-3">
              <div className="relative min-w-0 flex-1">
                <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-white/35" />
                <input
                  value={search}
                  onChange={(event) => setSearch(event.target.value)}
                  placeholder="搜索角色"
                  aria-label="搜索角色"
                  className="h-8 w-full rounded-lg border border-white/10 bg-white/[0.04] pl-8 pr-2 text-[12px] text-white outline-none placeholder:text-white/30 focus:border-white/25"
                />
              </div>
              <button
                type="button"
                aria-label="新建角色"
                title="新建角色"
                onClick={() => setCreateMode("character")}
                className="inline-flex size-8 shrink-0 items-center justify-center rounded-lg border border-white/12 text-white/70 transition-colors hover:bg-white/10 hover:text-white"
              >
                <UserPlus className="size-4" />
              </button>
            </div>
            <div className="flex flex-wrap gap-1 px-4 pb-2">
              {["全部", "女", "男"].map((item) => (
                <button
                  key={`gender-${item}`}
                  type="button"
                  aria-pressed={genderFilter === item}
                  onClick={() => setGenderFilter(item)}
                  className={`h-6 rounded-md px-2 text-[10.5px] transition-colors ${
                    genderFilter === item
                      ? "bg-white/[0.12] text-white"
                      : "text-white/38 hover:bg-white/[0.06] hover:text-white/70"
                  }`}
                >
                  {item}
                </button>
              ))}
              <span className="mx-1 w-px bg-white/10" />
              {["全部", "青年", "中年", "老年", "儿童"].map((item) => (
                <button
                  key={`age-${item}`}
                  type="button"
                  aria-pressed={ageFilter === item}
                  onClick={() => setAgeFilter(item)}
                  className={`h-6 rounded-md px-2 text-[10.5px] transition-colors ${
                    ageFilter === item
                      ? "bg-white/[0.12] text-white"
                      : "text-white/38 hover:bg-white/[0.06] hover:text-white/70"
                  }`}
                >
                  {item}
                </button>
              ))}
              {libraryTab === "official" ? (
                <>
                  <span className="mx-1 w-px bg-white/10" />
                  {["全部", "东亚", "欧美"].map((item) => (
                    <button
                      key={`region-${item}`}
                      type="button"
                      aria-pressed={regionFilter === item}
                      onClick={() => setRegionFilter(item)}
                      className={`h-6 rounded-md px-2 text-[10.5px] transition-colors ${
                        regionFilter === item
                        ? "bg-white/[0.12] text-white"
                        : "text-white/38 hover:bg-white/[0.06] hover:text-white/70"
                      }`}
                    >
                      {item}
                    </button>
                  ))}
                </>
              ) : null}
            </div>
            <div className="ui-scrollbar min-h-0 flex-1 overflow-y-auto px-3 pb-3">
              {libraryTab === "official" ? (
                officialItems.length === 0 ? (
                  <div className="flex items-center justify-center py-12 text-white/40">
                    <Loader2 className="size-4 animate-spin" />
                  </div>
                ) : officialFiltered.length === 0 ? (
                  <p className="px-2 py-10 text-center text-[12px] text-white/35">
                    没有匹配的官方角色，换个筛选或搜索词试试。
                  </p>
                ) : (
                  <div className="grid grid-cols-2 gap-2.5">
                    {officialFiltered.map((item) => (
                      <article
                        key={item.id}
                        className="group overflow-hidden rounded-[12px] border border-white/[0.08] bg-white/[0.03]"
                      >
                        <div className="relative aspect-[4/5] bg-[radial-gradient(circle_at_50%_30%,rgba(255,255,255,0.09),transparent_65%)]">
                          <div className="absolute inset-0 flex items-center justify-center text-[34px] font-semibold text-white/25">
                            {item.name.slice(0, 1)}
                          </div>
                          <div className="pointer-events-none absolute inset-0 hidden flex-col gap-1 bg-[#101114]/92 p-2 group-hover:flex">
                            <div className="grid grid-cols-2 gap-1 text-[9.5px] leading-3 text-white/45">
                              {(
                                [
                                  ["立绘", "hero"],
                                  ["脸部", "face"],
                                  ["表情九宫格", "expression"],
                                  ["三视图", "turnaround"],
                                ] as const
                              ).map(([label, blockId]) => (
                                <span
                                  key={blockId}
                                  className="flex h-full items-center justify-center rounded-md border border-white/10 bg-white/[0.04] px-1 py-2 text-center"
                                >
                                  {label}
                                  <br />
                                  （素材待接入）
                                </span>
                              ))}
                            </div>
                          </div>
                        </div>
                        <div className="p-2.5">
                          <p className="truncate text-[12.5px] font-medium">{item.name}</p>
                          <p className="mt-0.5 line-clamp-2 min-h-[30px] text-[10.5px] leading-[14px] text-white/38">
                            {item.shortDescription}
                          </p>
                          <div className="mt-1.5 flex flex-wrap items-center gap-1 text-[9.5px] text-white/35">
                            <span className="rounded-full border border-white/10 px-1.5 py-0.5">{item.categoryLabel}</span>
                            <span>{item.gender} · {item.ageStage} · {item.region} · {item.era}</span>
                          </div>
                          <button
                            type="button"
                            disabled={officialBusy === item.id}
                            onClick={() => void applyOfficial(item)}
                            className="mt-2 inline-flex h-7 w-full items-center justify-center gap-1.5 rounded-md bg-white text-[11.5px] font-medium text-black transition-opacity hover:opacity-90 disabled:opacity-40"
                          >
                            {officialBusy === item.id ? (
                              <Loader2 className="size-3.5 animate-spin" />
                            ) : (
                              <Wand2 className="size-3.5" />
                            )}
                            应用到造型室
                          </button>
                        </div>
                      </article>
                    ))}
                  </div>
                )
              ) : charactersRes.isLoading ? (
                <div className="flex items-center justify-center py-12 text-white/40">
                  <Loader2 className="size-4 animate-spin" />
                </div>
              ) : filtered.length === 0 ? (
                <p className="px-2 py-10 text-center text-[12px] text-white/35">
                  还没有角色。先新建一个，或去角色页导入。
                </p>
              ) : (
                <div className="flex flex-col gap-1">
                  {filtered.map((character) => {
                    const image = characterImage(character);
                    const active = character.name === activeName;
                    return (
                      <button
                        key={character.name}
                        type="button"
                        onClick={() => setSelectedName(character.name)}
                        className={`flex items-center gap-3 rounded-[10px] px-2 py-2 text-left transition-colors ${
                          active ? "bg-white/[0.09]" : "hover:bg-white/[0.05]"
                        }`}
                      >
                        <span className="flex size-10 shrink-0 items-center justify-center overflow-hidden rounded-lg border border-white/10 bg-white/[0.05]">
                          {image ? (
                            <img src={image} alt="" className="size-full object-cover" />
                          ) : (
                            <span className="text-[13px] font-semibold text-white/45">
                              {character.name.slice(0, 1)}
                            </span>
                          )}
                        </span>
                        <span className="min-w-0 flex-1">
                          <span className="block truncate text-[13px] font-medium">
                            {character.name}
                          </span>
                          <span className="block truncate text-[11px] text-white/35">
                            {character.role || character.description || "未填写简介"}
                          </span>
                        </span>
                        {character.is_main ? (
                          <span className="shrink-0 rounded-full border border-amber-300/25 bg-amber-400/10 px-1.5 py-0.5 text-[10px] text-amber-100/80">
                            主角
                          </span>
                        ) : null}
                      </button>
                    );
                  })}
                </div>
              )}
            </div>
          </aside>

          <section className="ui-scrollbar min-w-0 flex-1 overflow-y-auto px-6 py-5">
            {!activeCharacter ? (
              <p className="py-16 text-center text-[12.5px] text-white/35">
                左边选一个角色，这里会列出它的全部造型。
              </p>
            ) : (
              <>
                <div className="mb-5 flex items-start justify-between gap-4">
                  <div className="min-w-0">
                    <h3 className="text-[16px] font-semibold">{activeCharacter.name}</h3>
                    <p className="mt-1 line-clamp-2 text-[12px] leading-5 text-white/45">
                      {activeCharacter.description || "还没有角色简介"}
                    </p>
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    {activeCharacter.four_view_url ? (
                      <button
                        type="button"
                        onClick={() =>
                          insertImage(
                            activeCharacter.four_view_url as string,
                            `${activeCharacter.name} · 四视图`,
                          )
                        }
                        className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-white/12 px-3 text-[12px] text-white/70 transition-colors hover:bg-white/8 hover:text-white"
                      >
                        <ImagePlus className="size-3.5" />
                        四视图入画布
                      </button>
                    ) : null}
                    <button
                      type="button"
                      onClick={() => setStudioOpen(true)}
                      className="inline-flex h-8 items-center gap-1.5 rounded-lg bg-white px-3 text-[12px] font-medium text-black transition-opacity hover:opacity-90"
                    >
                      <Wand2 className="size-3.5" />
                      进入造型室
                    </button>
                    <button
                      type="button"
                      onClick={() => setCreateMode("identity")}
                      className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-white/12 px-3 text-[12px] text-white/75 transition-colors hover:bg-white/8 hover:text-white"
                    >
                      <Plus className="size-3.5" />
                      新建造型
                    </button>
                  </div>
                </div>

                {identitiesRes.isLoading ? (
                  <div className="flex items-center justify-center py-16 text-white/40">
                    <Loader2 className="size-4 animate-spin" />
                  </div>
                ) : identities.length === 0 ? (
                  <p className="py-12 text-center text-[12px] text-white/35">
                    这个角色还没有造型。新建一个造型，或去角色页生成造型图。
                  </p>
                ) : (
                  <div className="grid grid-cols-2 gap-3 xl:grid-cols-3">
                    {identities.map((identity) => {
                      const image = identityImage(identity);
                      return (
                        <article
                          key={identity.identity_id}
                          className="group overflow-hidden rounded-[12px] border border-white/[0.08] bg-white/[0.03]"
                        >
                          <div className="relative aspect-[4/5] bg-black/30">
                            {image ? (
                              <img
                                src={image}
                                alt={identity.identity_name}
                                className="size-full object-cover"
                              />
                            ) : (
                              <div className="flex size-full items-center justify-center text-[11px] text-white/30">
                                还没有造型图
                              </div>
                            )}
                          </div>
                          <div className="p-3">
                            <p className="truncate text-[13px] font-medium">
                              {identity.identity_name}
                            </p>
                            <p className="mt-0.5 line-clamp-2 min-h-[30px] text-[11px] leading-[15px] text-white/38">
                              {identity.appearance_details || identity.age_group || "—"}
                            </p>
                            <button
                              type="button"
                              disabled={!image}
                              onClick={() =>
                                image &&
                                insertImage(
                                  image,
                                  `${activeCharacter.name} · ${identity.identity_name}`,
                                )
                              }
                              className="mt-2 inline-flex h-7 w-full items-center justify-center gap-1.5 rounded-md border border-white/12 text-[11.5px] text-white/70 transition-colors hover:bg-white/8 hover:text-white disabled:cursor-not-allowed disabled:opacity-35"
                            >
                              <ImagePlus className="size-3.5" />
                              放进画布
                            </button>
                          </div>
                        </article>
                      );
                    })}
                  </div>
                )}
              </>
            )}
          </section>
        </div>
      </div>

      {studioOpen && activeCharacter ? (
        <CharacterStudioPanel
          project={project}
          character={activeCharacter}
          onClose={() => setStudioOpen(false)}
        />
      ) : null}

      {createMode ? (
        <CharacterCreateDialog
          mode={createMode}
          open
          project={project}
          characterName={activeName ?? undefined}
          onClose={() => setCreateMode(null)}
          onCreated={(name) => {
            if (createMode === "character") setSelectedName(name);
          }}
        />
      ) : null}

    </div>
  );
}
