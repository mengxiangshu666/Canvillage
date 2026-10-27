// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useEffect, useRef, useState } from "react";
import { FolderOpen, Loader2, Sparkles, Upload, UserRound, X } from "lucide-react";
import { toast } from "sonner";

import { AssetLibraryModal } from "@/features/canvas/ui/AssetLibraryModal";
import {
  useCreateCharacter,
  useCreateIdentity,
  useParseCharacterProfile,
  useUploadIdentityImage,
  useUploadPortrait,
} from "@/lib/queries/characters";
import type { CharacterProfileResult } from "@/types/character";

export type CharacterCreateMode = "character" | "identity";

export interface CharacterCreateDialogProps {
  mode: CharacterCreateMode;
  open: boolean;
  project: string;
  /** identity 模式必填：给哪个角色加造型。 */
  characterName?: string;
  onClose: () => void;
  /** 创建成功后回传新角色/造型的名字。 */
  onCreated?: (name: string) => void;
}

async function assetUrlToFile(url: string, filename: string): Promise<File> {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`参考图读取失败（${response.status}）`);
  }
  const blob = await response.blob();
  return new File([blob], `${filename || "reference"}.png`, {
    type: blob.type || "image/png",
  });
}

/**
 * 新建角色 / 新建造型，参考产品那样先放一张参考图再填文字。
 *
 * 本地图与「我的资产」最后都统一成 File 走角色现有的上传接口：角色肖像走
 * `portrait/upload`，造型参考图走 `identities/{name}/upload`，不新增后端。
 */
export function CharacterCreateDialog({
  mode,
  open,
  project,
  characterName,
  onClose,
  onCreated,
}: CharacterCreateDialogProps) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [assetUrl, setAssetUrl] = useState<string | null>(null);
  const [localPreview, setLocalPreview] = useState<string | null>(null);
  const [assetOpen, setAssetOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [profile, setProfile] = useState<CharacterProfileResult | null>(null);
  const fileRef = useRef<HTMLInputElement | null>(null);

  const createCharacter = useCreateCharacter(project);
  const createIdentity = useCreateIdentity(project, characterName ?? "");
  const uploadPortrait = useUploadPortrait(project, name.trim());
  const uploadIdentityImage = useUploadIdentityImage(project, characterName ?? "");
  const parseProfile = useParseCharacterProfile(project);
  const preview = localPreview ?? assetUrl;
  const parseBusy = parseProfile.isPending;
  // 参考图只能喂项目内 URL；本地图还没上传，所以只有描述可用。
  const canParse =
    mode === "character" &&
    !parseBusy &&
    (description.trim().length > 0 || Boolean(assetUrl));

  useEffect(() => {
    if (!open) return;
    setName("");
    setDescription("");
    setFile(null);
    setAssetUrl(null);
    setLocalPreview(null);
    setBusy(false);
    setProfile(null);
  }, [open, mode, characterName]);

  useEffect(() => {
    return () => {
      if (localPreview) URL.revokeObjectURL(localPreview);
    };
  }, [localPreview]);

  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !busy && !parseBusy) onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open, busy, parseBusy, onClose]);

  if (!open) return null;

  const pickLocal = (picked: File | null) => {
    if (!picked) return;
    if (localPreview) URL.revokeObjectURL(localPreview);
    setLocalPreview(URL.createObjectURL(picked));
    setAssetUrl(null);
    setFile(picked);
    setProfile(null);
  };

  const clearImage = () => {
    if (localPreview) URL.revokeObjectURL(localPreview);
    setLocalPreview(null);
    setAssetUrl(null);
    setFile(null);
    setProfile(null);
  };

  const runParse = async () => {
    if (!canParse) return;
    const text = description.trim();
    try {
      const result = await parseProfile.mutateAsync({
        description: text || undefined,
        imageUrl: text ? undefined : (assetUrl ?? undefined),
      });
      setProfile(result);
      const suggested = result.profile.suggested_name?.trim();
      if (!name.trim() && suggested) setName(suggested);
      if (!text && result.profile.brief) setDescription(result.profile.brief);
      toast.success("角色档案已解析");
    } catch (error) {
      toast.error(
        error instanceof Error ? error.message : "角色档案解析失败",
      );
    }
  };

  const submit = async () => {
    const trimmed = name.trim();
    if (!trimmed) return;
    setBusy(true);
    try {
      const resolvedFile =
        file ?? (assetUrl ? await assetUrlToFile(assetUrl, trimmed) : null);
      if (mode === "character") {
        const fields = profile?.character_fields;
        await createCharacter.mutateAsync({
          name: trimmed,
          description: description.trim() || fields?.description || undefined,
          gender: fields?.gender || undefined,
          age_group: fields?.age_group || undefined,
          face_prompt: fields?.face_prompt || undefined,
          body_type: fields?.body_type || undefined,
        });
        if (resolvedFile) await uploadPortrait.mutateAsync(resolvedFile);
        toast.success("角色已创建");
      } else {
        await createIdentity.mutateAsync({
          identity_name: trimmed,
          appearance_details: description.trim() || undefined,
        });
        if (resolvedFile) {
          await uploadIdentityImage.mutateAsync({
            identityName: trimmed,
            file: resolvedFile,
          });
        }
        toast.success("造型已创建");
      }
      onCreated?.(trimmed);
      onClose();
    } catch (error) {
      toast.error(
        error instanceof Error
          ? error.message
          : mode === "character"
            ? "新建角色失败"
            : "新建造型失败",
      );
    } finally {
      setBusy(false);
    }
  };

  const title =
    mode === "character" ? "创建你的新角色" : `给「${characterName ?? "角色"}」新建造型`;

  return (
    <>
      <div
        className="fixed inset-0 z-[100] flex items-center justify-center bg-black/65 p-6 backdrop-blur-[2px]"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onClick={(event) => {
          // 这个浮层挂在角色库弹窗的根节点里；不拦住冒泡，点遮罩会把两层一起关掉。
          event.stopPropagation();
          if (!busy) onClose();
        }}
      >
        <div
          className="flex max-h-[88vh] w-[min(860px,94vw)] flex-col overflow-hidden rounded-[18px] border border-white/[0.12] bg-[#111215] text-white shadow-[0_30px_90px_rgba(0,0,0,0.65)]"
          onClick={(event) => event.stopPropagation()}
        >
          <header className="flex shrink-0 items-center justify-between border-b border-white/[0.07] px-6 py-4">
            <h2 className="text-[17px] font-semibold">{title}</h2>
            <button
              type="button"
              aria-label="关闭"
              disabled={busy || parseBusy}
              onClick={onClose}
              className="inline-flex size-8 items-center justify-center rounded-lg text-white/55 transition-colors hover:bg-white/10 hover:text-white disabled:opacity-40"
            >
              <X className="size-4" />
            </button>
          </header>

          <div className="grid min-h-0 grid-cols-[minmax(0,320px)_minmax(0,1fr)] gap-6 overflow-y-auto px-6 py-5">
            <div className="flex flex-col gap-3">
              <div
                className="relative flex aspect-[3/4] items-center justify-center overflow-hidden rounded-[14px] border border-dashed border-white/12 bg-[radial-gradient(circle,rgba(255,255,255,0.07)_1px,transparent_1px)] [background-size:18px_18px]"
                title={preview ? "参考图" : "还没有参考图"}
              >
                {preview ? (
                  <img src={preview} alt="参考图" className="size-full object-cover" />
                ) : (
                  <div className="flex flex-col items-center gap-3 text-white/22">
                    <UserRound className="size-16" strokeWidth={1} />
                    <span className="text-[11.5px]">上传或选择一张参考图</span>
                  </div>
                )}
                {preview ? (
                  <button
                    type="button"
                    onClick={clearImage}
                    aria-label="移除参考图"
                    className="absolute right-2 top-2 inline-flex size-7 items-center justify-center rounded-full bg-black/60 text-white/70 transition-colors hover:bg-black/80 hover:text-white"
                  >
                    <X className="size-3.5" />
                  </button>
                ) : null}
              </div>

              <div className="flex items-center gap-2">
                <input
                  ref={fileRef}
                  type="file"
                  accept="image/*"
                  className="hidden"
                  aria-label="从本地上传参考图"
                  onChange={(event) => {
                    pickLocal(event.target.files?.[0] ?? null);
                    event.target.value = "";
                  }}
                />
                <button
                  type="button"
                  onClick={() => fileRef.current?.click()}
                  className="inline-flex h-9 flex-1 items-center justify-center gap-1.5 rounded-lg border border-white/12 bg-white/[0.04] text-[12px] text-white/78 transition-colors hover:bg-white/10 hover:text-white"
                >
                  <Upload className="size-3.5" />
                  从本地上传
                </button>
                <button
                  type="button"
                  onClick={() => setAssetOpen(true)}
                  className="inline-flex h-9 flex-1 items-center justify-center gap-1.5 rounded-lg border border-white/12 bg-white/[0.04] text-[12px] text-white/78 transition-colors hover:bg-white/10 hover:text-white"
                >
                  <FolderOpen className="size-3.5" />
                  从我的资产选择
                </button>
              </div>
              <p className="text-[10.5px] leading-4 text-white/30">
                参考图会作为这个{mode === "character" ? "角色的肖像" : "造型的参考图"}保存；
                之后仍可在角色页替换或生成新图。
              </p>
            </div>

            <div className="flex min-w-0 flex-col gap-4">
              <label className="block text-[12px] text-white/55">
                {mode === "character" ? "角色名" : "造型名"}
                <input
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                  placeholder={mode === "character" ? "例如：孟祥树" : "例如：唐代常服"}
                  autoFocus
                  className="mt-1.5 h-10 w-full rounded-lg border border-white/12 bg-white/[0.04] px-3 text-[13px] text-white outline-none placeholder:text-white/25 focus:border-white/30"
                />
              </label>
              <label className="block flex-1 text-[12px] text-white/55">
                {mode === "character" ? "角色描述（可留空）" : "造型描述（可留空）"}
                <textarea
                  value={description}
                  onChange={(event) => setDescription(event.target.value)}
                  rows={7}
                  placeholder={
                    mode === "character"
                      ? "年龄、气质、外形要点；也可以直接描述这张参考图"
                      : "服装、发型、妆容、配饰"
                  }
                  className="mt-1.5 w-full resize-none rounded-lg border border-white/12 bg-white/[0.04] px-3 py-2 text-[13px] leading-6 text-white outline-none placeholder:text-white/25 focus:border-white/30"
                />
              </label>

              {mode === "character" ? (
                <div className="flex flex-col gap-2">
                  <div className="flex flex-wrap items-center gap-2">
                    <button
                      type="button"
                      disabled={!canParse}
                      onClick={() => void runParse()}
                      className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-white/12 bg-white/[0.05] px-3 text-[12px] text-white/80 transition-colors hover:bg-white/10 hover:text-white disabled:cursor-not-allowed disabled:opacity-40"
                    >
                      {parseBusy ? (
                        <Loader2 className="size-3.5 animate-spin" />
                      ) : (
                        <Sparkles className="size-3.5" />
                      )}
                      {parseBusy ? "解析中…" : "AI 解析档案"}
                    </button>
                    <span className="text-[10.5px] leading-4 text-white/30">
                      {description.trim()
                        ? "按这段描述补全年龄、面部锚点与造型"
                        : assetUrl
                          ? "按选中的参考图解析"
                          : "先写一句描述，或从「我的资产」选一张项目内图片"}
                    </span>
                  </div>

                  {profile ? (
                    <div
                      aria-label="解析出的角色档案"
                      className="max-h-[240px] overflow-y-auto rounded-xl border border-white/10 bg-white/[0.03] px-3 py-2.5"
                    >
                      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11.5px] text-white/55">
                        {profile.profile.suggested_name ? (
                          <span className="text-white/85">
                            建议名：{profile.profile.suggested_name}
                          </span>
                        ) : null}
                        {profile.profile.gender ? (
                          <span>{profile.profile.gender}</span>
                        ) : null}
                        {profile.profile.age_group ? (
                          <span>{profile.profile.age_group}</span>
                        ) : null}
                        {profile.profile.body_type ? (
                          <span>{profile.profile.body_type}</span>
                        ) : null}
                      </div>
                      {profile.profile.face_prompt ? (
                        <p className="mt-1.5 text-[11px] leading-[17px] text-white/60">
                          <span className="text-white/35">面部锚点：</span>
                          {profile.profile.face_prompt}
                        </p>
                      ) : null}
                      {profile.profile.appearance_details ? (
                        <p className="mt-1 text-[11px] leading-[17px] text-white/60">
                          <span className="text-white/35">造型：</span>
                          {profile.profile.appearance_details}
                        </p>
                      ) : null}
                      {profile.profile.signature_features?.length ? (
                        <p className="mt-1 text-[11px] leading-[17px] text-white/60">
                          <span className="text-white/35">签名特征：</span>
                          {profile.profile.signature_features
                            .slice(0, 3)
                            .map((item) => item.feature)
                            .filter(Boolean)
                            .join("、")}
                        </p>
                      ) : null}
                      <p className="mt-1.5 border-t border-white/[0.06] pt-1.5 text-[10.5px] leading-4 text-white/32">
                        创建时会把性别、年龄段、体型、面部锚点一起写进角色；
                        生图提示词的完整版本在角色页可再调。
                      </p>
                    </div>
                  ) : null}
                </div>
              ) : null}
            </div>
          </div>

          <footer className="flex shrink-0 items-center justify-end gap-2 border-t border-white/[0.07] px-6 py-4">
            <button
              type="button"
              disabled={busy || parseBusy}
              onClick={onClose}
              className="h-9 rounded-lg px-4 text-[12.5px] text-white/60 transition-colors hover:bg-white/8 hover:text-white disabled:opacity-40"
            >
              取消
            </button>
            <button
              type="button"
              disabled={busy || parseBusy || !name.trim()}
              onClick={() => void submit()}
              className="inline-flex h-9 items-center gap-2 rounded-lg bg-white px-5 text-[12.5px] font-medium text-black transition-opacity hover:opacity-90 disabled:opacity-40"
            >
              {busy ? <Loader2 className="size-3.5 animate-spin" /> : null}
              创建
            </button>
          </footer>
        </div>
      </div>

      <AssetLibraryModal
        open={assetOpen}
        project={project}
        allowedMedia={["image"]}
        maxSelectable={1}
        onClose={() => setAssetOpen(false)}
        onConfirm={(selections) => {
          const picked = selections[0];
          setAssetOpen(false);
          if (!picked) return;
          if (localPreview) URL.revokeObjectURL(localPreview);
          setLocalPreview(null);
          setFile(null);
          setAssetUrl(picked.url);
          setProfile(null);
        }}
      />
    </>
  );
}
