// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { createFileRoute, Link } from "@tanstack/react-router";
import { Clapperboard, Film, Mic2, Pencil, Video } from "lucide-react";

import { useEpisodes, usePipelineStatus } from "@/lib/queries/episodes";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/_app/projects/$project/making")({
  component: MakingPage,
});

function MakingPage() {
  const { project } = Route.useParams();
  const episodes = useEpisodes(project);
  const pipeline = usePipelineStatus(project);
  const items = episodes.data?.data ?? [];
  const current = pipeline.data?.data;

  return (
    <main className="village-workflow-page -m-6 min-h-[calc(100%+3rem)] bg-[#0b0b0c] px-6 py-7 text-white sm:px-8">
      <div className="mx-auto w-full max-w-6xl space-y-6">
        <header>
          <p className="text-xs font-medium text-primary">村长工作流 · 制作</p>
          <h1 className="mt-2 text-2xl font-semibold tracking-tight">声音、视频与成片</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            选择一集进入真实制作页面。这里只负责导航和进度，不复制生成工具。
          </p>
        </header>

        {episodes.isLoading ? (
          <div className="h-32 animate-pulse rounded-2xl bg-white/[0.03]" />
        ) : items.length === 0 ? (
          <section className="rounded-2xl border border-dashed border-white/[0.1] p-8 text-center">
            <Clapperboard className="mx-auto size-6 text-muted-foreground" />
            <p className="mt-3 text-sm font-medium">先完成故事与分镜</p>
            <p className="mt-1 text-xs text-muted-foreground">有了正式分集后，制作入口会自动出现。</p>
            <Link
              className={cn(buttonVariants(), "mt-4")}
              to="/projects/$project/episodes"
              params={{ project }}
            >
              进入分镜
            </Link>
          </section>
        ) : (
          <section className="grid gap-3 sm:grid-cols-2">
            {items.map((episode) => {
              const isCurrent = current?.current_episode === episode.number;
              const status = isCurrent ? current?.episode_status : null;
              return (
                <article key={episode.number} className="rounded-2xl border border-white/[0.07] bg-white/[0.02] p-4">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <p className="text-xs text-muted-foreground">第 {episode.number} 集</p>
                      <h2 className="mt-1 truncate text-sm font-semibold">{episode.title || `第 ${episode.number} 集`}</h2>
                    </div>
                    {isCurrent ? <span className="rounded-full bg-primary/10 px-2 py-1 text-[10px] text-primary">当前</span> : null}
                  </div>
                  <div className="mt-4 grid grid-cols-4 gap-1.5 text-center text-[10px] text-muted-foreground">
                    <span className="rounded-lg bg-white/[0.035] px-1 py-2">画面 {status?.sketches ? "已就绪" : "待制作"}</span>
                    <span className="rounded-lg bg-white/[0.035] px-1 py-2">声音 {status?.tts ? "已就绪" : "待制作"}</span>
                    <span className="rounded-lg bg-white/[0.035] px-1 py-2">视频 {status?.video ? "已就绪" : "待制作"}</span>
                    <span className="rounded-lg bg-white/[0.035] px-1 py-2">成片 {current?.next_step === "done" && isCurrent ? "已就绪" : "待制作"}</span>
                  </div>
                  <div className="mt-4 flex flex-wrap gap-2">
                    <Link className={buttonVariants({ size: "sm", variant: "outline" })} to="/projects/$project/episodes/$episode/beats" params={{ project, episode: String(episode.number) }} search={{ sub: "sketch" } as never}><Pencil className="size-3.5" />画面</Link>
                    <Link className={buttonVariants({ size: "sm", variant: "outline" })} to="/projects/$project/episodes/$episode/beats" params={{ project, episode: String(episode.number) }} search={{ sub: "audio" } as never}><Mic2 className="size-3.5" />声音</Link>
                    <Link className={buttonVariants({ size: "sm", variant: "outline" })} to="/projects/$project/episodes/$episode/beats" params={{ project, episode: String(episode.number) }} search={{ sub: "video" } as never}><Video className="size-3.5" />视频</Link>
                    <Link className={buttonVariants({ size: "sm" })} to="/projects/$project/episodes/$episode/compose" params={{ project, episode: String(episode.number) }}><Film className="size-3.5" />合成</Link>
                  </div>
                </article>
              );
            })}
          </section>
        )}
      </div>
    </main>
  );
}
