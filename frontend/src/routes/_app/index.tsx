// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import {
  ArrowDown,
  ArrowRight,
  BookOpenText,
  Clapperboard,
  Film,
  Code2,
  Image as ImageIcon,
  Maximize2,
  Minimize2,
  MonitorPlay,
  Music4,
  Sparkles,
  UsersRound,
} from "lucide-react";
import { useState } from "react";
import type { LucideIcon } from "lucide-react";

import { HomeAtmosphere } from "@/components/home/HomeAtmosphere";
import { requestHomeView } from "@/components/layout/app-sidebar";
import { ThemeToggle } from "@/components/theme-toggle";
import { appBrandName } from "@/lib/product-mode";
import "@/portal-home.css";

export const Route = createFileRoute("/_app/")({
  component: PortalHome,
});

const PRODUCTION_STEPS: Array<{
  index: string;
  title: string;
  detail: string;
  icon: LucideIcon;
}> = [
  {
    index: "01",
    title: "剧本创作",
    detail: "小树理解创意与真实项目上下文，完成故事结构、章节与可执行剧本。",
    icon: BookOpenText,
  },
  {
    index: "02",
    title: "美术设计",
    detail: "建立角色、场景、道具与风格资产，统一整部作品的视觉标准。",
    icon: UsersRound,
  },
  {
    index: "03",
    title: "分镜设计",
    detail: "自动拆解景别、机位、动作、对白与连续性，组织成可生产镜头。",
    icon: Clapperboard,
  },
  {
    index: "04",
    title: "短片合成",
    detail: "逐镜生成并自动完成剪辑、声音与成片，人只负责审美判断和最终标准。",
    icon: Film,
  },
];

const CAPABILITY_GROUPS: Array<{
  title: string;
  detail: string;
  items: string[];
  icon: LucideIcon;
}> = [
  {
    title: "创作 Agent",
    detail: "理解内容并端到端执行",
    items: ["剧本续写", "章节拆解", "素材提取", "分镜生产", "连续性管理"],
    icon: Sparkles,
  },
  {
    title: "图片能力",
    detail: "角色、场景与关键帧",
    items: ["文图生图", "智能修图", "高清放大", "扩图裁剪", "姿势编辑"],
    icon: ImageIcon,
  },
  {
    title: "视频能力",
    detail: "镜头生成与整片合成",
    items: ["文生视频", "首尾帧视频", "多图参考", "视频超分", "镜头合成"],
    icon: MonitorPlay,
  },
  {
    title: "音频能力",
    detail: "对白、声音与成片音轨",
    items: ["预设音色", "声音定制", "对白配音", "环境音效", "音轨合成"],
    icon: Music4,
  },
];

function PortalHome() {
  const navigate = useNavigate();
  const [immersive, setImmersive] = useState(false);

  const openStudio = () => {
    void navigate({ to: "/studio" });
  };

  const openLibrary = () => {
    // 复用工作台已有的「全部项目」意图通道：跨路由时意图会先存成待办，
    // 工作台挂载后自己取走并展开项目库。
    requestHomeView("all");
    void navigate({ to: "/studio" });
  };

  const scrollTo = (id: string) => {
    document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  return (
    <div className={`village-portal${immersive ? " is-immersive" : ""}`}>
      <HomeAtmosphere />

      {!immersive ? (
        <header className="village-portal-header" aria-label="主导航">
          <button type="button" className="village-portal-brand" onClick={() => scrollTo("village-portal-top")}>
            <img src="/brand/village-canvas-mark-light.png" alt="" aria-hidden="true" />
            <span>{appBrandName}</span>
          </button>
          <nav className="village-portal-nav">
            <button type="button" className="is-active" onClick={() => scrollTo("village-portal-top")}>
              首页
            </button>
            <button type="button" onClick={openStudio}>
              工作台
            </button>
            <button type="button" onClick={openLibrary}>
              项目库
            </button>
          </nav>
          <div className="village-portal-actions">
            <ThemeToggle />
            <button type="button" className="village-portal-enter" onClick={openStudio}>
              进入工作台
            </button>
          </div>
        </header>
      ) : null}

      <div className="village-portal-scroll" id="village-portal-top">
        <section className="village-portal-hero" aria-labelledby="village-portal-title">
          <div className="village-portal-hero__media" aria-hidden="true">
            <div className="village-portal-hero__scrim" />
          </div>

          <button
            type="button"
            className="village-portal-hero__immersive"
            aria-label={immersive ? "退出沉浸模式" : "进入沉浸模式"}
            onClick={() => setImmersive((value) => !value)}
          >
            {immersive ? <Minimize2 size={17} /> : <Maximize2 size={17} />}
          </button>

          <div className="village-portal-hero__content">
            <h1 className="village-portal-hero__title" id="village-portal-title">
              把想法推进到成片
            </h1>
            <p className="village-portal-hero__statement">
              <button type="button" onClick={() => scrollTo("village-portal-capabilities")}>
                底层能力
              </button>
              <span>覆盖</span>
              <button type="button" onClick={() => scrollTo("village-portal-workflow")}>
                全链路
              </button>
            </p>
            <div className="village-portal-hero__actions">
              <button type="button" className="village-portal-hero__primary" onClick={openStudio}>
                立即创作
                <ArrowRight size={22} />
              </button>
            </div>
          </div>

          <div className="village-portal-hero__credit">
            <span>一键成片，由小树完成</span>
            <span>@{appBrandName}</span>
          </div>

          <button
            type="button"
            className="village-portal-hero__scroll"
            aria-label="查看下一部分"
            onClick={() => scrollTo("village-portal-workflow")}
          >
            <ArrowDown size={22} />
          </button>
        </section>

        <section className="village-portal-section" id="village-portal-workflow">
          <div className="village-portal-heading">
            <span className="village-portal-heading__index">Production</span>
            <h2>短片全链路生产</h2>
            <p>小树负责从创意到成片的自动化执行，人负责传递美学和标准。</p>
          </div>
          <div className="village-portal-timeline">
            {PRODUCTION_STEPS.map((step, index) => {
              const Icon = step.icon;
              return (
                <article
                  key={step.index}
                  className={`village-portal-step${index % 2 === 0 ? " is-left" : " is-right"}`}
                >
                  <div className="village-portal-step__copy">
                    <span className="village-portal-step__index">STEP {step.index}</span>
                    <h3>{step.title}</h3>
                    <p>{step.detail}</p>
                  </div>
                  <div className="village-portal-step__media" aria-hidden="true">
                    <Icon className="village-portal-step__media-icon" size={44} />
                    <span className="village-portal-step__media-mark">{step.index}</span>
                  </div>
                </article>
              );
            })}
          </div>
        </section>

        <section className="village-portal-section" id="village-portal-capabilities">
          <div className="village-portal-heading">
            <span className="village-portal-heading__index">Foundation</span>
            <h2>底层原子能力</h2>
            <p>主流多模态生成能力统一进入一块画布，由小树按真实项目上下文调用。</p>
          </div>
          <div className="village-portal-capabilities">
            {CAPABILITY_GROUPS.map((group) => {
              const Icon = group.icon;
              return (
                <article key={group.title} className="village-portal-capability">
                  <Icon className="village-portal-capability__icon" size={22} />
                  <h3>{group.title}</h3>
                  <p>{group.detail}</p>
                  <ul>
                    {group.items.map((item) => (
                      <li key={item}>{item}</li>
                    ))}
                  </ul>
                </article>
              );
            })}
          </div>
        </section>

        <section className="village-portal-section" id="village-portal-works">
          <div className="village-portal-works-head">
            <div>
              <span className="village-portal-heading__index">TcTv</span>
              <h2>正在发生的作品</h2>
            </div>
            <button type="button" className="village-portal-works-more" onClick={openStudio}>
              进入工作台
              <ArrowRight size={15} />
            </button>
          </div>
          <div className="village-portal-works-empty">当前还没有公开作品</div>
        </section>

        <section className="village-portal-section village-portal-community">
          <div className="village-portal-heading">
            <span className="village-portal-heading__index">Community</span>
            <h2>和创作者保持连接</h2>
          </div>
          <div className="village-portal-community-grid">
            <a
              className="village-portal-community-card"
              href="https://github.com/mengxiangshu666/Canvillage"
              target="_blank"
              rel="noreferrer"
            >
              <Code2 size={22} />
              <strong>源码仓库</strong>
              <span>Canvillage · 持续更新的画布源码</span>
            </a>
            <button type="button" className="village-portal-community-card" onClick={openStudio}>
              <Sparkles size={22} />
              <strong>我的工作台</strong>
              <span>继续手上的项目与任务</span>
            </button>
          </div>
        </section>

        <footer className="village-portal-footer">
          <span>
            {appBrandName} · 本地创作工作台
          </span>
          <span>© 2026 {appBrandName}</span>
        </footer>
      </div>
    </div>
  );
}
