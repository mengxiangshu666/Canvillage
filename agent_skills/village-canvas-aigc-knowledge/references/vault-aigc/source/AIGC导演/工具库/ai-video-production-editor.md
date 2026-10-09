# AI Video Production Editor (LudwigKienle)

> 来源：https://github.com/LudwigKienle/ai-video-production-editor
> 安装日期：2026-07-07
> 安装路径：`<LOCAL_PATH>`
> License：GPL-3.0-or-later

## 一句话

开源本地优先的 AI 电影制作工作站——从剧本到导演处理、分镜、AI 拍摄、连续性审查、剪辑、导出的完整桌面应用。

## 核心工作流

```
Script → Director pass → Concepts → Storyboard → AI filming
       → Continuity review → Re-film queue → Timeline/edit → Export
```

## 功能亮点

- **Electron 桌面应用**：React + Electron，本地优先
- **全流程覆盖**：剧本 → 导演处理 → 分镜 → AI 拍摄 → 连续性审查 → 重拍队列 → 时间线/剪辑 → 导出
- **节点式管线**：Node Space 图形化创作管线
- **AI 连续性**：Gemini 连续性审查、漂移评分、连续性提示词优化
- **多模型支持**：Seedance、Kling、Veo、WAN、LTX、Happy Horse、GPT Image、Nano Banana、Flux
- **多供应商**：Gemini、FAL、Replicate、xAI、ElevenLabs、Sonauto
- **自带 API Key**：本地存储，不锁平台
- **故事规划**：故事圣经、世界观构建、场景墙、置景设计、情绪板

## 在 AIGC 导演流程中的位置

**核心工作站**：这个工具可以直接替代目前分散的多个工具：
- 剧本 → 代替手动写 Markdown
- 分镜 → 代替纯文本分镜表
- AI 拍摄 → 统一管理 Seedance/Kling 调用
- 连续性审查 → 自动检测角色/场景一致性
- 剪辑 → 内置时间线

## 使用方式

```bash
cd <LOCAL_PATH>
npm install
npm run dev          # 浏览器模式
npm run electron:dev # Electron 桌面应用
```

## 项目结构

| 路径 | 用途 |
|------|------|
| `src/workspaces` | 主要工作室界面 |
| `src/services` | 供应商适配器、项目服务 |
| `src/components` | 共享 React UI 组件 |
| `electron` | 桌面壳、预加载桥 |
| `api` | Vercel 风格托管端点 |
| `packages/storyboard-embed-sdk` | 可嵌入故事/项目 SDK |