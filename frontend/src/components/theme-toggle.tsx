// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { Check, Contrast, Moon, Sun } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useAppStore, type Theme } from "@/stores/app-store";

const OPTIONS: Array<{ value: Theme; i18nKey: string; Icon: typeof Sun }> = [
  { value: "light", i18nKey: "theme.white", Icon: Sun },
  { value: "gray", i18nKey: "theme.gray", Icon: Contrast },
  { value: "dark", i18nKey: "theme.black", Icon: Moon },
];

export function ThemeToggle() {
  const { t } = useTranslation();
  const theme = useAppStore((s) => s.theme);
  const setTheme = useAppStore((s) => s.setTheme);
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label={t("theme.toggle")}
            title={t("theme.toggle")}
            className="size-8 text-sidebar-foreground/80 hover:bg-muted hover:text-foreground"
          />
        }
      >
        <Contrast className="size-4" />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        {OPTIONS.map(({ value, i18nKey, Icon }) => (
          <DropdownMenuItem
            key={value}
            onClick={() => setTheme(value)}
            data-active={theme === value}
            className="data-[active=true]:bg-accent data-[active=true]:text-accent-foreground"
          >
            <Icon className="size-4" />
            {t(i18nKey)}
            {theme === value ? <Check className="ml-auto size-4" /> : null}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
