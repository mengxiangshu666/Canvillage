---
name: village-canvas-aigc-knowledge
description: Use for AIGC 提示词编译、模型适配、画风选择、镜头设计、角色一致性、场景与道具锁定、视频动作链、声音设计和从本地 xiaoshu-brain 知识库检索导演规则。
---

# AIGC 导演知识层

目标：把本地 AIGC 导演知识变成可选择、可解释、可验收的生成合同。这个 Skill 负责知识路由和 Prompt 编译，不直接启动付费媒体生成。

## 1. 知识来源优先级

按以下顺序裁决冲突：

1. 当前项目正式资产、beat、画布快照和目标模型的真实能力；
2. 当前 Skill 的任务合同；
3. `references/` 中对应的 AIGC 规则；
4. `references/vault-aigc/INDEX.md` 中经过公开范围筛选的 AIGC 方法论；
5. 通用提示词经验。

不要把整个 vault 全文塞入上下文。先按任务类型选择 1–2 个 reference，再读取必要的原始笔记片段。

## 2. 任务路由

| 用户目标 | 先读 | 主要输出 |
|---|---|---|
| 生图、改图、画风 | `references/prompt-patterns.md`、`references/model-routing.md` | 主体/风格/光影/构图/负面 Prompt |
| 角色、身份、三视图 | `references/continuity.md` | 角色 DNA、参考图职责、一致性锁 |
| 场景、道具、空间 | `references/continuity.md` | 场景/道具身份、空间关系、引用顺序 |
| 分镜、镜头、运镜 | `references/cinematography.md`；需要配方卡、转场或节奏时叠加 `village-canvas-shotcraft` | 镜头合同、景别、机位、焦段感、运镜和验收点 |
| 图生视频、首帧视频 | `references/video-motion.md` | 起始帧、动作因果链、时间节奏、镜头终点 |
| 声音、对白、音效 | `references/audio.md` | 台词表演、声线、环境音、SFX、BGM |
| 失败修复 | `references/failure-repair.md` | 根因分类、最小改动、重试或换模型建议 |

## 3. Prompt 编译流程

1. 提取用户目标：主体、动作、场景、风格、画幅、时长、模型和输出层级。
2. 读取项目事实：角色、identity、场景、道具、前后 beat、正式参考资产和当前模型。
3. 选择任务 reference；不要把无关的摄影、声音或模型规则混入。
4. 按模块编译：主体 → 表演/动作 → 摄影 → 场景/材质 → 风格/光线 → 连续性 → 引用职责 → 负向约束。
5. 做模型适配：区分自然语言模型、标签权重模型、图像编辑模型和视频模型；不编造目标模型不存在的参数。
6. 做可执行性检查：把抽象词翻译成可观察的物理事实，把反向动作、场景漂移和引用职责冲突标出。
7. 输出完整 Prompt、修改依据、未知项和验收点。优化本身不触发生成；若用户要生成，交给对应生产 Skill。

## 4. 硬规则

- 重要身份/类型/主体放前；关键保持项和镜头终点放后。
- 角色用正式资产名或 identity，不用“男人/女人/孩子”等泛称替代已知角色。
- 参考图必须说明职责：身份、服装、姿态、几何、场景、构图或风格，禁止互相污染。
- 视频从可见起始帧开始，只写向前发生的动作；动作必须有身体部位、方向、重心和结束位置。
- 每个片段控制动作数量；连续镜头优先一个主要运镜和一个清晰动作链。
- 负面约束只针对高概率失败，不堆互相冲突的“不要”。
- 生成前先输出可验收条件；生成后按条件检查主体、空间、动作、光线、连续性和文字。

## 5. 与其他村长 Agent Skill 的关系

- 只做 Prompt 预览和模型适配：叠加 `village-canvas-prompt-director`。
- 做逐镜分镜合同：叠加 `village-canvas-storyboard`。
- 做运镜配方、镜间转场、蒙太奇节奏、BGM 卡点或 SFX 落点：叠加 `village-canvas-shotcraft`；只读取一张主卡和至多一张辅助卡，不导入产品 UI 或 Remotion 模板。
- 做角色/场景连续性检查：叠加 `village-canvas-continuity`。
- 出现真实任务错误：切换 `village-canvas-failure-rescue`。
- 需要生成或生产 Run：回到对应业务 Skill，按当前 `task_authorization` 与启动预算执行。

详细规则按需读取 `references/`，不要在主 Skill 中重复整套模型手册。

## 6. 公开 AIGC 方法论包

`references/vault-aigc/INDEX.md` 只索引显式 allowlist 中的公共 AIGC 方法论，
`references/vault-aigc/source/` 不收录个人档案、项目私有资料、每日记录、Agent memory、
远程运维、机器路径、凭据或大型二进制。`manifest.json` 保存 `schema_version`、
`scope`、`owner`、`source_hash`、分类、文件哈希、脱敏计数和匿名排除审计。
先按类别查索引，再读取 1–2 份必要原文；不要把整个 bundle 一次性加载。

刷新原文包时运行：

```text
.venv\Scripts\python.exe scripts/build_vault_bundle.py --vault 项目资产\import\aigc-vault --output agent_skills\village-canvas-aigc-knowledge\references\vault-aigc
```

把待导入知识先放进项目内 `项目资产/import/aigc-vault`。构建器默认拒绝未列入范围的文件，只复制小型文本，并将本机路径替换为公共占位符；
排除项只在 manifest 中留下不可逆路径哈希和原因，不公开被排除文件名。
