---
name: village-canvas-shotcraft
description: Use for 镜头工艺的镜头运动、剪辑节奏、转场、开场/收尾、SFX节拍和逐镜视频提示词增强。把 video-shotcraft 的镜头配方转译成 Village Infinite Canvas 现有 shot/video_prompt 合同；不改村长画布画布，不直接生成媒体，不默认引入 Remotion。
---

# Village Infinite Canvas Shotcraft 镜头工艺总监

## 目标与边界

本 Skill 只服务镜头工艺的剧情分镜、视频 Prompt、镜间衔接、节奏和声音设计。它将
`video-shotcraft` 的经过调校的镜头语言转译为 Village Infinite Canvas 现有字段和验收点，而不是
把 Web 产品宣传片模板原样搬进短剧。

- 不操作村长画布画布，不创建或修改画布节点。
- 不直接启动草图、首帧、视频、音频或合成任务。
- 不把 Remotion、React 组件、UI 截图和产品广告文案注入剧情流水线。
- 不要求每个 beat 都套镜头卡；没有明确叙事收益时使用普通稳定镜头。
- 用户要求生成媒体时，回到对应业务 Skill，并按当前 `task_authorization` 决定自动推进或停在草稿。

## 数据源

1. `references/upstream/catalog.json`：56 张剧情可迁移镜头卡的轻量索引；
2. `references/upstream/cards/<category>/<card>.md`：所选卡的完整配方；
3. `references/upstream/core/aesthetic-rules.md`：审美边界；
4. `references/upstream/core/music-beat-sync.md`：BGM 节拍与切点；
5. `references/upstream/core/sound-design.md`：SFX 句法与响度；
6. `references/adapter-contract.md`：Village Infinite Canvas 字段映射和固定输出合同。

只按任务读取必要文件：先查 catalog，最多选择一张主卡和一张辅助卡，再读对应全文；
不要一次性把全部卡片载入上下文。

## 选择流程

1. **读取事实**：目标 episode/beat、剧情任务、角色与场景、时长、画幅、已有
   `video_prompt`、前后镜头、音乐或声音要求。
2. **确定镜头职责**：本镜只能有一个主要任务，例如揭示、压迫、追随、转折、
   信息压缩或情绪收束。
3. **确定能量与时间**：低/中/高能量、动作落点、静止呼吸和镜间接力。
4. **检索 catalog**：按 `category`、`summary`、`use`、`energy`、`duration` 匹配；
   优先 `camera`，需要镜间连接时再用 `transition`，需要全段节奏时才用 `rhythm`。
5. **读取配方全文**：验证用途、参数、命门、已知坑和建议时长；卡片与剧情冲突时放弃。
6. **剧情化转译**：只继承运动语法、时序、缓动、节拍和声音句法；人物、场景、
   美术、色彩和材质始终来自当前项目。
7. **编译合同**：按 `adapter-contract.md` 输出，供 `village-canvas-storyboard` 或
   `village-canvas-aigc-knowledge` 合并到逐镜合同与 `video_prompt`。

## 使用优先级

| 目标 | 首选类别 | 使用规则 |
|---|---|---|
| 人物关系、揭示、压迫、追随 | `camera` | 一镜一个主运镜，明确起点、路径、速度、终点 |
| 动作高潮、节拍加速、蒙太奇 | `rhythm` | 先写能量曲线和切点，不把整段变成连续炫技 |
| 两镜空间或动作接力 | `transition` | 必须说明前镜出口和后镜入口，避免无因转场 |
| 单点强调、冲击或视觉反馈 | `effects` | 一镜最多一个主效果，效果服务剧情信息 |
| 章节开场与世界建立 | `opening` | 先建立时间、地点、主体和情绪，再释放标题/信息 |
| 章节结束与情绪收束 | `outro` | 关键动作完成后保留静止呼吸，不重复表达结论 |

## 硬规则

- 镜头卡不是风格预设，不覆盖风格模板选择、剧本风格和项目视觉快照。
- 不把卡名直接塞进模型 Prompt；必须翻译成可见动作和可执行摄影语言。
- 单个视频片段优先一个主要运镜和一条清晰动作链；避免同时推拉摇移环绕。
- BGM 已知时，先按 `music-beat-sync.md` 计算节拍，再决定切点；未知时不编造 BPM。
- SFX 必须对应真实动作或叙事落点；不把合成 UI 提示音当剧情万能音效。
- 配方建议时长只能作为预算；最终时长服从 beat 对白、表演和模型能力。
- 所有输出必须保留项目角色、身份、场景、道具、轴线和光线连续性。

## 完成验收

- [ ] 所选卡与本镜叙事任务有明确因果关系；
- [ ] 已读取所选卡全文，不是凭卡名猜实现；
- [ ] 只有一个主运镜，起点、路径、速度和终点完整；
- [ ] 动作落点、静止呼吸、转场入口和声音提示可观察；
- [ ] 项目视觉风格、身份、场景、轴线和光向没有被模板污染；
- [ ] 输出符合 Village Infinite Canvas 适配合同，未直接生成媒体或改动村长画布画布。

上游方法来源：`Vincentwei1021/video-shotcraft`，Apache-2.0；本地精简包不包含
Remotion 工程、产品截图、音频二进制或动态样片。
