// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useQueryClient } from "@tanstack/react-query";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { subscribeOpenVersionUpdateDialog } from "@/features/version-update/version-update-events";
import {
  ensureReleaseNotifications,
  normalizeReleaseLocale,
  useReleaseNotifications,
} from "@/lib/queries/release-notifications";
import {
  markCurrentReleaseSeen,
  shouldAutoShowCurrentRelease,
} from "@/lib/release-notification-state";

export function VersionUpdateDialog() {
  const { t, i18n } = useTranslation();
  const queryClient = useQueryClient();
  const locale = normalizeReleaseLocale(i18n.resolvedLanguage ?? i18n.language);
  const releaseNotifications = useReleaseNotifications(locale);
  const feed = releaseNotifications.data?.data;
  const items = feed?.current_items ?? [];
  const [open, setOpen] = useState(false);
  const autoOpenedTagRef = useRef<string | null>(null);

  useEffect(() => {
    const tag = feed?.current_tag ?? null;
    if (!tag || autoOpenedTagRef.current === tag || !shouldAutoShowCurrentRelease(feed)) {
      return;
    }
    autoOpenedTagRef.current = tag;
    markCurrentReleaseSeen(tag);
    setOpen(true);
  }, [feed]);

  useEffect(
    () =>
      subscribeOpenVersionUpdateDialog(() => {
        void ensureReleaseNotifications(queryClient, locale).finally(() => setOpen(true));
      }),
    [locale, queryClient],
  );

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent
        showCloseButton={false}
        overlayClassName="bg-black/56 backdrop-blur-md supports-backdrop-filter:backdrop-blur-md"
        className="max-h-[min(84dvh,560px)] w-[min(calc(100vw-32px),360px)] gap-0 overflow-hidden rounded-[14px] border-0 bg-white p-0 text-slate-950 shadow-[0_16px_48px_rgba(0,0,0,0.26)] ring-0 sm:max-w-[360px]"
      >
        <div className="p-2">
          <div className="relative flex aspect-[2/1] items-center justify-center overflow-hidden rounded-[12px] bg-[radial-gradient(circle_at_50%_20%,rgba(138,96,255,0.34),transparent_44%),linear-gradient(145deg,#0b1220,#111827_58%,#07131a)]">
            <img
              className="h-[58%] w-auto object-contain drop-shadow-[0_16px_30px_rgba(138,96,255,0.28)]"
              src="/brand/village-canvas-mark-light.png"
              alt=""
              aria-hidden="true"
            />
          </div>
        </div>

        <div className="px-4.5 pb-5 pt-3.5 sm:px-5">
          <DialogTitle className="text-[17px] font-medium leading-tight tracking-normal text-slate-950 sm:text-[18px]">
            {t("app.versionUpdate.title")}
          </DialogTitle>
          <div className="mt-4 max-h-[138px] space-y-4 overflow-y-auto pr-2 text-[12.5px] leading-6 text-slate-700 [scrollbar-gutter:stable] sm:text-[13.5px]">
            {items.length > 0 ? (
              items.map((item, index) => (
                <p key={item.id} className="m-0">
                  {index + 1}. {item.title}
                  {item.body ? `: ${item.body}` : ""}
                </p>
              ))
            ) : (
              <p className="m-0">{t("app.versionUpdate.empty")}</p>
            )}
          </div>
          <Button
            type="button"
            className="mt-7 h-10 w-full rounded-[8px] bg-neutral-950 text-[14px] font-medium text-white shadow-none transition-[background,box-shadow,filter] duration-200 ease-out hover:bg-[#171717] hover:shadow-[0_0_0_1px_rgba(255,255,255,0.08)_inset,0_7px_18px_rgba(0,0,0,0.16)] hover:brightness-110 focus-visible:border-transparent focus-visible:ring-0 active:bg-neutral-950 active:shadow-[0_0_0_1px_rgba(255,255,255,0.05)_inset]"
            onClick={() => setOpen(false)}
          >
            {t("app.versionUpdate.confirm")}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
