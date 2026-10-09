# 补丁03 — Agentic AI 视频制作

> 来源交叉验证：Vizard Agent Blog (2026-06-29) + Hedra 10 Best AI Agents (2026-03) + Runflow ComfyUI Workflows (2026-05) + Data Science Collective Playbook (2026)
> 2026 年 AIGC 最关键的范式转变：从"生成"到"代理"

---

## 一、AI Tool vs AI Agent

| | AI 工具 | AI Agent |
|--|--------|---------|
| 交互 | 单次输入 → 单次输出 | 设定目标 → 多步自动执行 |
| 决策 | 用户控制每一步 | 自主推理和调整 |
| 范围 | 一次做一件事 | 跨工具协调、跨平台发布 |
| 学习 | 静态，同样输入同样输出 | 随时间改进 |

**核心区别：** 工具等你给指令，agent 你给目标它自己想办法。

---

## 二、Agentic Video 架构

Agentic Video 的本质不是一个更好的视频模型，而是一个 **LLM 作为创意导演** 的编排层，协调多个模型和工具完成全流程。

### 典型架构

```
用户输入（想法/剧本）
  ↓
LLM "创意导演"
  ├── 分析需求 → 制定计划
  ├── 调用图像模型（Flux/Seedream）→ 生成定妆照
  ├── 调用视频模型（Seedance/Kling）→ 生成镜头
  ├── 调用音频（原生 / ElevenLabs）→ 配音+BGM
  ├── 剪辑编排 → 合成输出
  └── 发布（可选）
```

### Vizard Agent 的 8 角色模拟

| 角色 | 职责 | AI 能力 |
|------|------|---------|
| 执行制片 | 分析简报和受众，定义方向 | LLM 推理 |
| 文案 | 写剧本、多版本开头 | 文本生成 |
| 分镜师 | 规划每个视觉节拍、计算镜头频率 | LLM + 图像模型 |
| 资产管理 | 协调 Kling/Seedance/Flux，搜索全球素材库 | 多模型编排 |
| 剪辑师 | 组装镜头、时间线控制 | 视频编排 |
| 动效师 | 动态文字叠加、电影调色 | 后期处理 |
| 音效师 | 音频母带、音效同步 | 音频处理 |
| 配音员 | 多语言旁白、声音克隆 | TTS |

每角色不是一个人——是一个 AI agent 在干。

---

## 三、ComfyUI Agentic Workflow（LLM 控制节点选择）

Runflow 的 20 个工作流中，Workflow 20 就是 Agentic：

```
LLM 节点（Claude/GPT/本地模型）
  读取用户输入
  决定运行哪个子工作流
  设置什么参数
  评估输出并决定下一步
```

LLM 作为路由节点，根据输入内容动态选择：
- "做个产品展示" → 调用产品展示工作流
- "做一个电影场景" → 调用电影镜头工作流
- "太暗了重做" → 调亮参数重跑

---

## 四、Hedra — 统一多模态 agent

Hedra 是目前最接近"全能 agent"的平台之一：

| 能力 | 实现方式 |
|------|---------|
| 统一工作空间 | 研究、生成、迭代都在一个地方 |
| 品牌智能 | 从上传的资产和品牌指南中学习 DNA |
| 前沿模型 | Hedra Omnia + Veo + Kling + Flux |
| 声音克隆 + TTS | 克隆声音或生成语音 |
| API 访问 | 集成到现有工作流 |

**核心卖点：** 学习品牌，越用越懂，不是一次性生成器。

---

## 五、对 AIGC 导演的意义

### 这意味着什么

1. **不用手动拼工作流**——agent 替你规划每一步
2. **多模型自动编排**——不再需要手动在 Seedance / Kling / Flux 之间切换
3. **越用越懂你**——agent 学习你的风格偏好

### 局限

1. 目前 agentic 更适合**营销短视频**（标准格式），不适合**叙事短片**（需要精确控制）
2. 对"创造力"的掌控还很基础——难以替代人对叙事的判断
3. 多角色交互还是弱项（agent 也受限于底层模型能力）

### 推荐策略

```
当前项目中：
  叙事短片 → 自己手动编排（知识库全链路支持）
  营销/科普内容 → 可以试试 Vizard Agent / Hedra

未来趋势（2026 Q4-2027）：
  叙事短片也会被 agent 接管
  现在理解架构是为未来做准备
```

---

## 来源

- [From Generative to Agentic: Agent Video Is Reshaping How Videos Get Made](https://vizard.ai/blog/from-generative-to-agentic-agent-video-is-reshaping-how-videos-get-made) — Vizard, Jun 2026
- [10 Best AI Agents for Content Creation in 2026 (Compared)](https://www.hedra.com/blog/best-ai-agents-content-creation) — Hedra, Mar 2026
- [20 ComfyUI Workflows for Production in 2026](https://www.runflow.io/blog/comfyui-workflows-production-ready) — Runflow, May 2026 (Workflow 20: Agentic)
- [The 2026 AI Video Production Playbook](https://medium.com/data-science-collective/the-2026-ai-video-production-playbook-bc683d5b85da) — Data Science Collective, 2026