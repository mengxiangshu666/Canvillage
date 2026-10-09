import { useTranslation } from "react-i18next";

import { LightboxImage } from "@/components/lightbox-image";
import { resolveMediaUrl } from "@/lib/media-url";
import type { Character } from "@/types/character";

export function CharacterAssetGroup({ character }: { character: Character }) {
  const { t } = useTranslation();
  return (
    <div className="flex w-full max-w-[180px] flex-col gap-2" aria-label={t("characters.assetGroup.title")}>
      <p className="text-xs font-medium">{t("characters.assetGroup.title")}</p>
      {[
        { url: character.portrait_url, label: t("characters.assetGroup.front"), ratio: "aspect-[3/4]" },
        { url: character.four_view_url, label: t("characters.assetGroup.fourView"), ratio: "aspect-[4/3]" },
      ].map(({ url, label, ratio }) => (
        <div key={label} className="flex w-full flex-col gap-1">
          <p className="text-xs text-muted-foreground">{label}</p>
          {url ? (
            <LightboxImage src={resolveMediaUrl(url) ?? ""} alt={`${character.name} · ${label}`}
              fit="contain" blurBackdrop={false} className={`${ratio} w-full rounded-[8px]`} />
          ) : (
            <div className={`flex ${ratio} w-full items-center justify-center rounded-[8px] border border-dashed border-border bg-background/40`}>
              <span className="text-xs text-muted-foreground">{t("characters.assetGroup.missing")}</span>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
