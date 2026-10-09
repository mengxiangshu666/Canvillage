# 村长 Agent 原生技能索引

这是 `agent_skills/` 的维护索引。运行时的唯一加载规则仍然是以技能目录名调用 `skill`：
目录名必须与 `SKILL.md` frontmatter 的 `name` 一致，并且全部使用
`village-canvas-*` 命名空间。

## 核心操作

| 技能 | 职责 |
|---|---|
| `village-canvas` | 村长 Agent 主规程、项目流水线、工具路由和身份边界 |
| `village-canvas-canvas-director` | 画布结构诊断、节点编排和可审计命令 |
| `village-canvas-canvas-commands` | `canvas_chat_commands.v1` 命令协议 |
| `village-canvas-canvas-ops` | 画布、项目、节点和素材的底层操作 |
| `village-canvas-agent-tool-surface` | 工具可用性与真实回执取证 |
| `village-canvas-template-director` | 工作流模板选择、插入和输入合同 |

## 故事与镜头

| 技能 | 职责 |
|---|---|
| `village-canvas-story-director` | 剧本结构、角色弧线、对话和潜台词 |
| `village-canvas-script-doctor` | 剧本前期诊断、授权改稿与版本锁定（SC/CR 编号、事实与推断分离） |
| `village-canvas-storyboard` | 分镜、机位、轴线、运镜和逐镜合同 |
| `village-canvas-character-workflow` | 角色设定表、参考图和身份一致性 |
| `village-canvas-character-consistency` | 角色资产一致性五模式作业流（身份账本/定脸/服装/角色表/换装） |
| `village-canvas-casting-design` | 选角判断、角色概念、Look Test、多视图和身份锚点 |
| `village-canvas-continuity` | 跨镜人物、场景、道具和方向连续性 |
| `village-canvas-expression-director` | 表演动机、表情、呼吸和动作设计 |
| `village-canvas-performance-craft` | 微表情、物理反馈、情绪转折和僵硬返修 |
| `village-canvas-performance-director` | 表演母版设计（目标/阻碍/节拍、身体任务、眼神生命、固定声音） |
| `village-canvas-combat-director` | 打斗/武戏的因果链、战损连续性、镜头预算与声明闸门 |
| `village-canvas-shotcraft` | 镜头运动、剪辑节奏、转场和声音节拍 |
| `village-canvas-cinematography` | 构图、灯光、焦段和景深体系 |
| `village-canvas-3d-scene-director` | 站位、机位、骨骼姿态和三维场景预演 |
| `village-canvas-3d-asset` | 角色、场景和道具的 3D 资产管线 |
| `village-canvas-coverage-planner` | 主镜、覆盖镜、B-roll、动作预演和剪接切点 |
| `village-canvas-script-integrity` | 剧本事实账本、角色知识、伏笔和跨集连续性 |

## 提示词与知识

| 技能 | 职责 |
|---|---|
| `village-canvas-aigc-knowledge` | AIGC 知识路由、模型适配和提示词编译 |
| `village-canvas-director-framework` | 从剧本到成片的导演通用框架 |
| `village-canvas-director-knowledge-base` | 电影语言、导演思维和提示词工程 |
| `village-canvas-prompt-director` | 提示词优化、覆盖预览和参数诊断 |
| `village-canvas-prompt-framework` | 提示词结构、铁律和决策矩阵 |
| `village-canvas-visual-style` | 画风、质感和可控制参数 |
| `village-canvas-style-lock` | 参考风格提取、不变项、跨镜漂移诊断和修复 |
| `village-canvas-knowledge-engine` | 注意力机制、上下文工程和声音设计 |
| `village-canvas-iron-rules` | 生成提示词前的硬性自检 |
| `village-canvas-practical-directing-lessons` | **实拍级实战补遗**：视觉事件铁律（有看头）、FACS 微表情、戏剧五力自检、锚定链（定妆主图+逐镜参考）、H3 台词逐字引用与时长匹配、首尾帧双锚定、尾段漂移与烧字幕规避、付费不可撤回纪律（2026-09-29 十九次真实付费实测沉淀）|
| `village-canvas-image-prompt-craft` | 单图生图提示词成图纪律（一个视觉中心、三类风格分流、参考图特征、修复表） |
| `village-canvas-lighting-director` | 光影专项（光源层级、八属性、光映射表面、跨镜头不变量、诊断表） |

## 声音与音乐

| 技能 | 职责 |
|---|---|
| `village-canvas-music-score` | 配乐 cue map、主题动机、歌曲结构、音画同步和 stem 交付 |

## 生产与工程

| 技能 | 职责 |
|---|---|
| `village-canvas-one-click-film` | 持久生产运行、自动推进和成片交付 |
| `village-canvas-delivery-qc` | 成片验收、交付质检和返工优先级 |
| `village-canvas-failure-rescue` | 失败分类、最低成本恢复和防重复付费 |
| `village-canvas-post-production` | 配音、口型、调色和后制 |
| `village-canvas-workflow-engineering` | ComfyUI、LoRA、质量门和生产时间线 |
| `village-canvas-lora-training` | 数据集、训练参数和模型微调 |
| `village-canvas-engineering-governance` | 小步实现、合同证据和运行态验收 |
| `village-canvas-method-distiller` | 从真实画布 / Run 提取方法并蒸馏为原生 Skill |
| `village-canvas-shortform-growth` | 短视频钩子、留存、字幕、CTA 和横竖屏重构 |

## 加载合同

```text
skill(action="list")
  -> 返回 name / description / source / version / bytes / sha256 和 activation 路由

skill(action="load", name="village-canvas-storyboard")
  -> 返回同一身份字段 + 对应 SKILL.md 的完整方法
```

技能目录是开发态真源；部署脚本同步到运行版 `agent_skills/`。历史框架归档不进入
运行时索引，也不作为模型上下文注入。
