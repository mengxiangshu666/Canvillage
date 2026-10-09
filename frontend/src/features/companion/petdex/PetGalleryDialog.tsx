// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useCallback, useEffect, useMemo, useRef, useState, type DragEvent } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import { CheckCircle2, FileJson, ImageUp, Loader2, Trash2, UploadCloud, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { useAppStore } from "@/stores/app-store";
import { SpritePetCompanion } from "@/features/companion/petdex/SpritePetCompanion";
import {
  fetchLocalPets,
  PETDEX_GRID_COLS,
  PETDEX_GRID_ROWS,
  type PetdexCatalogEntry,
} from "@/features/companion/petdex/petdex-pets";
import {
  deletePetRecord,
  loadImportedPets,
  savePetRecord,
} from "@/features/companion/petdex/petdex-storage";
import { PetSpriteThumbnail } from "@/features/companion/petdex/PetSpriteThumbnail";
import "./petdex-pet.css";

type ViewMode = "gallery" | "import";

const PRIMARY_ACTION_BUTTON_CLASS =
  "rounded-[9px] bg-primary text-primary-foreground shadow-none hover:bg-primary/90 active:bg-primary/80 disabled:bg-primary disabled:text-primary-foreground";

type PetGalleryDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  currentKind: string;
  currentPet: PetdexCatalogEntry | null;
  onConfirm: (pet: PetdexCatalogEntry) => void;
};

function slugify(name: string): string {
  const base = name
    .toLowerCase()
    .replace(/\.[^.]+$/, "")
    .replace(/[^a-z0-9-]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return base || `pet-${Date.now()}`;
}

function FileDrop({
  icon,
  title,
  hint,
  accept,
  file,
  onFile,
  matchFile,
  inputRef: externalInputRef,
  children,
}: {
  icon: React.ReactNode;
  title: string;
  hint: string;
  accept: string;
  file: File | null;
  onFile: (file: File | null) => void;
  matchFile: (file: File) => boolean;
  inputRef?: React.RefObject<HTMLInputElement | null>;
  children?: React.ReactNode;
}) {
  const { t } = useTranslation();
  const internalInputRef = useRef<HTMLInputElement | null>(null);
  const inputRef = externalInputRef ?? internalInputRef;
  const [dragOver, setDragOver] = useState(false);

  const handleDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setDragOver(false);
    const dropped = Array.from(event.dataTransfer.files).find(matchFile);
    if (dropped) onFile(dropped);
  };

  return (
    <div
      onDragOver={(event) => {
        event.preventDefault();
        setDragOver(true);
      }}
      onDragLeave={() => setDragOver(false)}
      onDrop={handleDrop}
      className={cn(
        "flex items-center gap-3 rounded-lg border border-dashed px-3 py-2.5 transition-colors",
        dragOver
          ? "border-[#d7ae5f] bg-[#d7ae5f]/10"
          : file
            ? "border-white/[0.14] bg-white/[0.055]"
            : "border-white/[0.1] bg-white/[0.025]",
      )}
    >
      <span className="flex size-9 shrink-0 items-center justify-center rounded-md bg-white/[0.06] text-text-muted">
        {children ?? icon}
      </span>
      <span className="flex min-w-0 flex-1 flex-col">
        <span className="truncate text-sm text-text-dark">{file ? file.name : title}</span>
        <span className="truncate text-[11px] text-text-muted">
          {file ? `${Math.round(file.size / 1024)} KB` : hint}
        </span>
      </span>
      {file && <CheckCircle2 className="size-4 shrink-0 text-emerald-400" />}
      <Button
        type="button"
        variant="outline"
        size="sm"
        className="h-8 shrink-0 rounded-full border-white/[0.12] bg-white/[0.04] text-xs"
        onClick={() => inputRef.current?.click()}
      >
        {file ? t("myBuddy.import.replace") : t("myBuddy.import.choose")}
      </Button>
      <input
        ref={inputRef}
        type="file"
        accept={accept}
        className="sr-only"
        onChange={(event) => onFile(event.target.files?.[0] ?? null)}
      />
    </div>
  );
}

type CompanionCardProps = {
  title: string;
  selected: boolean;
  onSelect: () => void;
  children: React.ReactNode;
  onDelete?: () => void;
  contentClassName?: string;
};

function CompanionCard({
  title,
  selected,
  onSelect,
  children,
  onDelete,
  contentClassName,
}: CompanionCardProps) {
  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onSelect}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onSelect();
        }
      }}
      className={cn(
        "group relative flex h-[132px] min-w-0 cursor-pointer flex-col overflow-hidden rounded-[8px] border bg-white/[0.045] p-3 text-left transition-colors",
        selected
          ? "border-[#d7ae5f]/70 bg-[#d7ae5f]/[0.055]"
          : "border-white/[0.1] hover:border-white/[0.18] hover:bg-white/[0.06]",
      )}
      aria-pressed={selected}
    >
      <div className="flex min-w-0 items-center justify-between gap-2">
        <div className="min-w-0 truncate text-[13px] font-semibold leading-6 text-text-dark">{title}</div>
        <div className="flex shrink-0 items-center gap-1 overflow-visible">
          {onDelete && (
            <button
              type="button"
              onClick={(event) => {
                event.stopPropagation();
                onDelete();
              }}
              className="flex h-7 w-7 items-center justify-center rounded-full text-text-muted opacity-0 transition-colors hover:bg-white/[0.08] hover:text-rose-300 group-hover:opacity-100"
              title="删除"
            >
              <Trash2 className="size-3.5" />
            </button>
          )}
        </div>
      </div>
      <div className={cn("flex min-h-0 flex-1 items-center justify-center pt-4", contentClassName)}>
        {children}
      </div>
    </div>
  );
}

export function PetGalleryDialog({
  open,
  onOpenChange,
  currentKind,
  currentPet,
  onConfirm,
}: PetGalleryDialogProps) {
  const { t } = useTranslation();
  const companionHidden = useAppStore((state) => state.companionHidden);
  const setCompanionHidden = useAppStore((state) => state.setCompanionHidden);
  const [mode, setMode] = useState<ViewMode>("gallery");
  const [localPets, setLocalPets] = useState<PetdexCatalogEntry[]>([]);
  const [importedPets, setImportedPets] = useState<PetdexCatalogEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [draftPet, setDraftPet] = useState<PetdexCatalogEntry | null>(null);
  const importedUrlsRef = useRef<string[]>([]);
  const spriteInputRef = useRef<HTMLInputElement | null>(null);

  const [spriteFile, setSpriteFile] = useState<File | null>(null);
  const [jsonFile, setJsonFile] = useState<File | null>(null);
  const [importError, setImportError] = useState<string | null>(null);
  const [importBusy, setImportBusy] = useState(false);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [spriteDims, setSpriteDims] = useState<{ w: number; h: number } | null>(null);
  const missingSpriteMessage = t("myBuddy.import.missingSpritesheet");

  const handleSpriteFile = useCallback((file: File | null) => {
    setSpriteFile(file);
    if (file) setImportError(null);
  }, []);

  const applyImported = useCallback((next: PetdexCatalogEntry[]) => {
    const previous = importedUrlsRef.current;
    importedUrlsRef.current = next.map((p) => p.spritesheetUrl);
    setImportedPets(next);
    previous.forEach((url) => URL.revokeObjectURL(url));
  }, []);

  useEffect(() => {
    if (!open) return;
    setMode("gallery");
    setDraftPet(currentKind && currentPet && currentPet.slug === currentKind ? currentPet : null);
  }, [open, currentKind, currentPet]);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    const controller = new AbortController();
    setLoading(true);
    Promise.all([loadImportedPets(), fetchLocalPets(controller.signal)])
      .then(([imported, local]) => {
        if (cancelled) {
          imported.forEach((p) => URL.revokeObjectURL(p.spritesheetUrl));
          return;
        }
        applyImported(imported);
        setLocalPets(local);
      })
      .catch(() => {
        if (cancelled) return;
        applyImported([]);
        setLocalPets([]);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [open, applyImported]);

  useEffect(
    () => () => {
      importedUrlsRef.current.forEach((url) => URL.revokeObjectURL(url));
    },
    [],
  );

  useEffect(() => {
    if (!spriteFile) {
      setPreviewUrl(null);
      setSpriteDims(null);
      return;
    }
    const url = URL.createObjectURL(spriteFile);
    setPreviewUrl(url);
    setSpriteDims(null);
    const image = new Image();
    image.onload = () => setSpriteDims({ w: image.naturalWidth, h: image.naturalHeight });
    image.src = url;
    return () => URL.revokeObjectURL(url);
  }, [spriteFile]);

  const resetImport = useCallback(() => {
    setSpriteFile(null);
    setJsonFile(null);
    setImportError(null);
  }, []);

  const handleRequestClose = useCallback(() => {
    if (mode === "import") {
      resetImport();
      setMode("gallery");
      return;
    }
    onOpenChange(false);
  }, [mode, onOpenChange, resetImport]);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") handleRequestClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, handleRequestClose]);

  const handleImport = async () => {
    if (!spriteFile) {
      setImportError(missingSpriteMessage);
      spriteInputRef.current?.click();
      return;
    }
    setImportBusy(true);
    setImportError(null);
    try {
      let displayName = spriteFile.name.replace(/\.[^.]+$/, "");
      let slug = slugify(spriteFile.name);
      let submittedBy: string | undefined;
      let cols: number | undefined;
      let rows: number | undefined;
      if (jsonFile) {
        const root = JSON.parse(await jsonFile.text()) as Record<string, unknown>;
        const nestedMeta =
          root.meta && typeof root.meta === "object" && !Array.isArray(root.meta)
            ? (root.meta as Record<string, unknown>)
            : {};
        const id = root.id ?? root.slug ?? nestedMeta.id ?? nestedMeta.slug ?? nestedMeta.name;
        if (typeof id === "string" && id.trim()) slug = slugify(id);
        const dn = root.displayName ?? root.name ?? nestedMeta.displayName ?? nestedMeta.name;
        if (typeof dn === "string" && dn.trim()) displayName = dn.trim();
        const author = root.submittedBy ?? nestedMeta.submittedBy;
        if (typeof author === "string") submittedBy = author;
        const configuredCols = root.cols ?? nestedMeta.cols;
        const configuredRows = root.rows ?? nestedMeta.rows;
        if (typeof configuredCols === "number") cols = configuredCols;
        if (typeof configuredRows === "number") rows = configuredRows;
      }
      const gridCols = cols ?? PETDEX_GRID_COLS;
      const gridRows = rows ?? PETDEX_GRID_ROWS;
      if (spriteDims && (spriteDims.w % gridCols !== 0 || spriteDims.h % gridRows !== 0)) {
        setImportError(
          t("myBuddy.import.gridError", {
            cols: gridCols,
            rows: gridRows,
            w: spriteDims.w,
            h: spriteDims.h,
          }),
        );
        setImportBusy(false);
        return;
      }
      await savePetRecord({ slug, displayName, submittedBy, cols, rows, blob: spriteFile, addedAt: Date.now() });
      const next = await loadImportedPets();
      applyImported(next);
      const importedPet = next.find((pet) => pet.slug === slug);
      if (importedPet) setDraftPet(importedPet);
      window.dispatchEvent(new Event("mybuddy-imported-pets-changed"));
      resetImport();
      setMode("gallery");
    } catch (error) {
      setImportError(error instanceof Error ? error.message : String(error));
    } finally {
      setImportBusy(false);
    }
  };

  const handleDelete = useCallback(
    async (slug: string) => {
      await deletePetRecord(slug);
      window.dispatchEvent(new Event("mybuddy-imported-pets-changed"));
      const next = await loadImportedPets();
      applyImported(next);
      if (draftPet?.slug === slug) setDraftPet(null);
    },
    [applyImported, draftPet],
  );

  const pets = useMemo(() => [...localPets, ...importedPets], [localPets, importedPets]);

  if (!open) return null;

  const content = (
    <div
      className="fixed inset-0 z-[100] flex items-center justify-center bg-black/48 p-6 backdrop-blur-md"
      onClick={handleRequestClose}
    >
      <div
        className="relative flex max-h-[88vh] w-full max-w-[760px] flex-col rounded-[12px] border border-white/[0.08] bg-[#121212]/82 shadow-[0_18px_58px_rgba(0,0,0,0.56)] backdrop-blur-2xl"
        onClick={(event) => event.stopPropagation()}
      >
        {mode === "gallery" ? (
          <div className="petdex-gallery-title-badge pointer-events-none absolute left-[14px] -top-[56px] z-10 h-[119px] w-[238px] -rotate-5">
            <img
              src="/images/companion-title-ok-buddy.png"
              alt={t("myBuddy.companion.titleBadgeAlt")}
              className="petdex-gallery-title-badge__image h-full w-full object-contain object-left drop-shadow-[0_12px_18px_rgba(0,0,0,0.45)]"
              draggable={false}
            />
          </div>
        ) : null}
        <header
          className={cn(
            "flex items-start justify-between pl-5 pr-3",
            mode === "gallery" ? "pb-1 pt-2" : "pb-3 pt-4",
          )}
        >
          {mode === "gallery" ? <span aria-hidden="true" /> : (
            <h2 className="pt-1 text-[15px] font-semibold text-text-dark">
              {t("myBuddy.import.title")}
            </h2>
          )}
          <div className="flex translate-x-1 translate-y-[2px] items-center gap-2 pt-2">
            {mode === "gallery" ? (
              <button
                type="button"
                role="switch"
                aria-checked={!companionHidden}
                onClick={() => setCompanionHidden(!companionHidden)}
                title={t("myBuddy.companion.toggleVisibility")}
                className="flex h-7 items-center gap-2 rounded-full px-2.5 text-[11px] text-text-dark/88 transition-colors hover:bg-white/[0.08] hover:text-white"
              >
                <span>{t("myBuddy.companion.toggleVisibility")}</span>
                <span
                  className={cn(
                    "relative inline-flex h-3.5 w-6 shrink-0 items-center rounded-full transition-colors",
                    companionHidden ? "bg-white/20" : "bg-[rgb(var(--accent-rgb))]",
                  )}
                >
                  <span
                    className={cn(
                      "absolute h-2.5 w-2.5 rounded-full bg-white transition-transform",
                      companionHidden ? "translate-x-0.5" : "translate-x-[13px]",
                    )}
                  />
                </span>
              </button>
            ) : null}
            {mode === "gallery" ? (
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="h-7 rounded-full !border-white/[0.16] !bg-white/[0.035] px-2.5 text-[11px] !text-text-dark/88 shadow-none hover:!border-white/[0.28] hover:!bg-white/[0.08] hover:!text-white"
                onClick={() => setMode("import")}
              >
                {t("myBuddy.companion.importCta")}
              </Button>
            ) : null}
            <button
              type="button"
              onClick={handleRequestClose}
              className="flex h-7 w-7 items-center justify-center rounded-md text-text-dark/70 transition-colors hover:bg-white/[0.08] hover:text-text-dark"
              title={t("common.close")}
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        </header>

        {mode === "gallery" ? (
          <div className="ui-scrollbar min-h-0 overflow-y-auto px-5 pb-4 pt-4">
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
              {loading && (
                <div className="col-span-full flex items-center justify-center gap-2 py-12 text-[13px] text-text-muted">
                  <Loader2 className="h-4 w-4 animate-spin" /> {t("myBuddy.gallery.loading")}
                </div>
              )}

              {!loading &&
                pets.map((pet) => (
                  <CompanionCard
                    key={pet.slug}
                    title={pet.displayName}
                    selected={draftPet?.slug === pet.slug}
                    onSelect={() => setDraftPet(pet)}
                    onDelete={pet.imported ? () => void handleDelete(pet.slug) : undefined}
                  >
                    <SpritePetCompanion
                      pet={pet}
                      action="idle"
                      style={{
                        position: "relative",
                        top: "auto",
                        left: "auto",
                        transform: "translateY(-2px) scale(0.86)",
                        transformOrigin: "center center",
                      }}
                    />
                  </CompanionCard>
                ))}

              {!loading && pets.length === 0 && (
                <div className="col-span-full flex items-center justify-center py-12 text-center text-[13px] text-text-muted">
                  {t("myBuddy.gallery.empty")}
                </div>
              )}
            </div>
          </div>
        ) : (
          <div className="flex min-h-0 flex-1 flex-col gap-4 px-5 pb-3">
            <p className="max-w-[560px] text-[12px] leading-relaxed text-text-muted">
              {t("myBuddy.import.desc")}
            </p>
            <div className="flex flex-col gap-2.5">
              <FileDrop
                icon={<ImageUp className="size-4" />}
                title={t("myBuddy.import.spritesheetTitle")}
                hint={t("myBuddy.import.spritesheetHint")}
                accept=".webp,.png,image/webp,image/png"
                file={spriteFile}
                onFile={handleSpriteFile}
                matchFile={(file) => /\.(webp|png)$/i.test(file.name) || file.type.startsWith("image/")}
                inputRef={spriteInputRef}
              >
                {previewUrl ? (
                  <PetSpriteThumbnail url={previewUrl} className="size-9 rounded-md" />
                ) : undefined}
              </FileDrop>
              <FileDrop
                icon={<FileJson className="size-4" />}
                title={t("myBuddy.import.jsonTitle")}
                hint={t("myBuddy.import.jsonHint")}
                accept=".json,application/json"
                file={jsonFile}
                onFile={setJsonFile}
                matchFile={(file) => /\.json$/i.test(file.name) || file.type === "application/json"}
              />
              {!spriteFile && jsonFile && !importError && (
                <p className="text-xs text-amber-300/90" role="status">
                  {missingSpriteMessage}
                </p>
              )}
              {importError && <p className="text-xs text-destructive">{importError}</p>}
              <p className="flex items-start gap-1.5 text-[11px] leading-relaxed text-text-muted">
                <UploadCloud className="mt-0.5 size-3.5 shrink-0" />
                {t("myBuddy.import.hint")}
              </p>
            </div>
          </div>
        )}

        <footer
          className={cn(
            "flex shrink-0 items-center justify-end gap-2 bg-transparent px-5",
            mode === "gallery" ? "pb-3 pt-2" : "py-3",
          )}
        >
          {mode === "import" ? (
            <>
              <Button
                variant="ghost"
                onClick={() => {
                  resetImport();
                  setMode("gallery");
                }}
              >
                {t("common.cancel")}
              </Button>
              <Button
                className={PRIMARY_ACTION_BUTTON_CLASS}
                disabled={importBusy}
                data-pet-import-ready={spriteFile ? "true" : "false"}
                onClick={handleImport}
              >
                {t("myBuddy.import.confirm")}
              </Button>
            </>
          ) : (
            <>
              <Button variant="ghost" onClick={() => onOpenChange(false)}>
                {t("common.cancel")}
              </Button>
              <Button
                className={PRIMARY_ACTION_BUTTON_CLASS}
                onClick={() => {
                  if (draftPet) onConfirm(draftPet);
                  onOpenChange(false);
                }}
              >
                {t("common.confirm")}
              </Button>
            </>
          )}
        </footer>
      </div>
    </div>
  );

  return createPortal(content, document.body);
}
