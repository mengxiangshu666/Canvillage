// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { Link } from "@tanstack/react-router";
import { BookOpenCheck, Lightbulb, Upload } from "lucide-react";
import { useTranslation } from "react-i18next";

import { cn } from "@/lib/utils";

export type ProjectStoryView = "create" | "import" | "library";

const STORY_VIEWS = [
  {
    id: "create",
    labelKey: "storyWorkspace.create",
    icon: Lightbulb,
    to: "/projects/$project/story-lab",
  },
  {
    id: "import",
    labelKey: "storyWorkspace.import",
    icon: Upload,
    to: "/projects/$project/ingest",
    search: { view: "import" },
  },
  {
    id: "library",
    labelKey: "storyWorkspace.library",
    icon: BookOpenCheck,
    to: "/projects/$project/ingest",
    search: { view: "library" },
  },
] as const;

export function ProjectStoryNavigation({
  project,
  active,
  clientNavigation = false,
}: {
  project: string;
  active: ProjectStoryView;
  clientNavigation?: boolean;
}) {
  const { t } = useTranslation();

  return (
    <nav
      aria-label={t("storyWorkspace.navigationLabel")}
      className="flex min-h-11 items-center gap-1 overflow-x-auto rounded-xl border border-white/[0.08] bg-white/[0.025] p-1.5"
    >
      {STORY_VIEWS.map((item) => {
        const Icon = item.icon;
        const selected = item.id === active;
        const className = cn(
          "inline-flex h-8 shrink-0 items-center gap-1.5 rounded-lg px-3 text-xs font-medium transition-colors duration-150",
          selected
            ? "bg-foreground text-background"
            : "text-muted-foreground hover:bg-white/[0.05] hover:text-foreground",
        );
        const content = (
          <>
            <Icon className="size-3.5" aria-hidden />
            {t(item.labelKey)}
          </>
        );
        const href = `/projects/${encodeURIComponent(project)}${
          item.id === "create" ? "/story-lab" : "/ingest"
        }${"search" in item ? `?view=${item.search.view}` : ""}`;

        if (clientNavigation) {
          return (
            <Link
              key={item.id}
              to={item.to}
              params={{ project }}
              search={"search" in item ? item.search : undefined}
              aria-current={selected ? "page" : undefined}
              className={className}
            >
              {content}
            </Link>
          );
        }
        return (
          <a
            key={item.id}
            href={href}
            aria-current={selected ? "page" : undefined}
            className={className}
          >
            {content}
          </a>
        );
      })}
    </nav>
  );
}
