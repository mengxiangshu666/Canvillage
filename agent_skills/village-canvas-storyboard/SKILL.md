---
name: village-canvas-storyboard
description: Use for 分镜、镜头设计、景别机位、轴线、运镜、节拍、镜头提示词、故事板检查，以及把逐镜方案安全落实到 Village Infinite Canvas 画布。
version: 2.0.0
---

# Village Infinite Canvas 分镜导演规程

目标是把剧本事实翻译成可拍、可生成、可连续验收的逐镜合同。输出的是镜头决策，不是“电影感、高级感”之类无法执行的形容词堆砌。

## 一、适用场景

- 从剧本、episode 或 beat 设计分镜；
- 修正景别、机位、轴线、视线、出入画和动作匹配；
- 设计首帧、尾帧意图、运镜路径、时长和声音节奏；
- 检查已有草图、首帧或视频的故事板质量；
- 在当前自由画布创建逐镜提示词节点、批注和连线；
- 生成前核对角色、身份、场景、道具和前后镜连续性。

不负责擅自改剧情，不在缺少剧本事实时凭空补关键事件，也不把画布命令当媒体生成。

## 二、输入事实与权威顺序

1. `village_canvas_get_episode_script` 返回的当前集脚本；
2. `village_canvas_get` 读取的 episode/beat、角色、场景和当前画布最新详情；
3. 正式角色肖像/身份图与场景主参考；
4. 当前官方草图 `village_canvas_get_sketches`、官方首帧 `village_canvas_get_first_frames`；
5. 用户明确的改编要求；
6. 候选池 `village_canvas_get_sketch_candidates` 仅作比较，不得冒充定稿。

最低输入集合：project、episode、目标 beat/剧情范围、画幅、交付类型（草图/首帧/视频）、已知角色和场景。缺失时建立“事实缺口”，关键剧情信息必须提问，非关键拍法可给带假设的 A/B 方案。

## 三、真实工具速查

| 目的 | 工具 | 注意 |
|---|---|---|
| 读脚本 | `village_canvas_get_episode_script` | 分镜叙事事实的首要来源 |
| 读 beat/角色/场景/画布 | `village_canvas_get` | 不猜 API 返回内容，不猜节点 ID |
| 看官方草图 | `village_canvas_get_sketches` | 只展示当前 `sketch_url` 媒体 |
| 看草图候选 | `village_canvas_get_sketch_candidates` | 仅一个 beat 的候选池 |
| 看官方首帧 | `village_canvas_get_first_frames` | 不能替代草图 |
| 看场景/角色参考 | `village_canvas_get_scene_images` / `village_canvas_get_character_media` | 返回媒体由后端展示，不手写 URL |
| 看已生成视频 | `village_canvas_get_episode_media` | `media_type="video"` |
| 读画布快照 | `freezone_get_canvas_snapshot` | 先读节点/边/选中/固定上下文，再规划 |
| 写画布意图 | `freezone_emit_canvas_command` | allowlist 结构命令；含 create_shot_sequence / create_video_prompt_node / move / duplicate / remove_edge / delete；**绝不生成媒体** |
| 提案生成 | `freezone_propose_generation` | 只提案；自动模式携带本轮 `task_authorization` 后继续正式 generation / production，草稿模式停在提案 |
| 生成草图 | `village_canvas_generate_sketches` | 异步 `sketch_generation`；默认会先分配颜色 |
| AI 检测草图身份 | `village_canvas_detect_sketch_identities` | 超时或 `retryable=false` 时同轮停止重试 |
| 生成首帧 | `village_canvas_render_first_frames` | 前置是草图；异步 `selected_regen` |
| 生成单 beat 视频 | `village_canvas_start_single_video` | 不传 prompt；使用 beat 已存 `video_prompt`，且首帧必须存在 |
| 查任务 | `village_canvas_get_task` / `village_canvas_list_tasks` | 使用真实 task_type 和 episode |

不要编造“storyboard_generate”“canvas_generate”“update_beat_prompt”等工具。若要更新画布 prompt，直接使用 `freezone_emit_canvas_command` 的 `update_node_prompt`；若要修改后端 beat 字段，本规程不猜路由，先查真实 API 或仅输出修订案。

## 四、不可伪造原则

1. 不增加、删除或调换剧情事件，除非用户明确授权改编。
2. 不伪造对白、角色在场、道具状态、场景空间、时间或天气；缺失即标缺口。
3. 不凭“电影感”替代可测参数：必须给景别、机位、方位、焦段感、构图、时长和起止动作。
4. 不违反既定轴线和屏幕方向；若要越轴，必须设计中性镜头、穿轴运动或明确重建空间。
5. 不把身份图当姿态权威，不把风格图当角色权威，不让引用职责互相污染。
6. 不把生成任务“已启动”写成“已完成”；完成以 `village_canvas_get_task` 和正式媒体为准。
7. 不用本地路径、任务结果路径或合成 URL 展示图像。
8. 只按当前 V2 `task_authorization` 启动媒体：自动模式有效授权直接推进，草稿或费用保护不启动。

## 五、固定输出合同

每个镜头必须按以下字段输出，字段不得省略：

### 镜头合同
- **镜号 / beat / 对应画布节点**：
- **叙事目的**：本镜新增哪条剧情信息或情绪变化
- **主体与关系**：角色、场景、道具、前中后景层级
- **动作**：起点 → 过程 → 终点；动作匹配点
- **表演**：外显状态、潜台词、视线对象及转移时机
- **摄影**：景别、机位高度、水平/俯仰方位、焦段感、构图
- **空间连续性**：180°轴线、屏幕左右、出入画方向、视线匹配
- **光线与美术锚点**：时间、光向、色温、必须保留的场景/服装/道具
- **运镜**：路径、方向、速度、时长、开始画面、结束画面；静止也要写
- **声音**：对白/旁白、环境声、音效、重音、节奏点
- **引用职责**：身份 / 场景 / 几何 / 风格分别绑定什么正式资产
- **模型执行提示词**：只写可观察事实、动作和摄影约束
- **负向约束**：本镜最可能发生的漂移或错误
- **验收点**：3–7 条一眼可判定的通过条件

多镜输出后追加：

### 序列合同
- **节奏曲线**：每镜时长与信息密度
- **轴线地图**：人物位置、主轴、允许越轴点
- **连续性接力**：动作、视线、道具、光线、声音如何跨镜承接
- **画布执行单**：拟创建/更新/连接的节点与命令预览
- **生成批次**：草图、首帧、视频分别列范围、工具和本轮授权状态
- **任务授权**：`run_mode`、`allow_paid_media`、`max_paid_starts`

## 六、分阶段流程

### 阶段 0：锁定范围

确认 project、episode、beat 区间、画幅、目标时长和交付层级。长段落先按剧情转折切 beat，再在 beat 内决定单镜或多镜；不为了炫技滥拆镜。

### 阶段 1：建立事实账本

1. 读取剧本和目标 beats。
2. 列每个 beat 的角色、地点、时间、事件、对白、关键道具、前后状态。
3. 读取正式角色与场景媒体，标注身份和空间权威。
4. 如在画布工作，读取最新画布并映射节点 ID。
5. 将未知项分为“阻断生成”和“可带假设规划”。

### 阶段 2：设计序列骨架

1. 为每个镜头只指定一个主要叙事任务。
2. 先固定空间：主轴、角色左右、入口出口、主光方向、关键地标。
3. 再排景别变化：建立空间 → 传递信息 → 反应/转折 → 收束。
4. 给每镜时长预算；对白镜为表演留停顿，动作镜保留动作完成点。
5. 设计镜间连接：动作匹配、视线匹配、声音桥或构图呼应。

### 阶段 3：填写逐镜合同

按固定合同逐镜写实。摄影描述必须能变成画面；动作必须有起止；运镜必须写路径和速度。模型提示词只包含该镜事实，不塞入整段剧情和互相冲突的镜头指令。

### 阶段 4：连续性预检

逐对检查 N→N+1：

- 人物屏幕方向是否反转；
- 视线角度是否匹配；
- 动作切点是否前后重复或跳跃；
- 道具在哪只手、开合/破损/液位状态是否延续；
- 场景地标、光向、天气和时间是否一致；
- 情绪强度是否有原因地递进。

发现冲突时先改镜头合同，不急于生成。

### 阶段 5：画布预演与执行

1. 用节点级执行单展示拟创建的提示词节点、批注和 source→target 连线。
2. 如更新既有 prompt，展示完整替换文本。
3. 有明确偏好时遵循用户版本；无偏好时由 Agent 选取最符合叙事目标的单一方案并写明选择依据。
4. 用 `freezone_emit_canvas_command` 直接落图。其中 `create_image_prompt_node` 只建 prompt 节点，`update_node_prompt` 使用当前画布具体节点 ID。
5. 命令后重读画布，验证节点和边。

### 阶段 6：草图批次（按本轮授权）

1. 明确 episode、beat 范围、画幅和模型；说明 `village_canvas_generate_sketches` 是异步生成。
2. `task_authorization.allow_paid_media=true` 时直接调用真实工具；否则保留完整批次计划，不启动。
3. 用 `village_canvas_get_task(task_type="sketch_generation", episode=N)` 轮询，不用聊天猜状态。
4. 完成后用 `village_canvas_get_sketches` 展示官方草图；需要比较候选才调用 `village_canvas_get_sketch_candidates`。
5. 按镜头验收点审查，未过镜只提出局部返修，不全量重跑。

### 阶段 7：首帧与视频（同一任务授权）

1. 首帧必须在草图通过后执行，使用 `village_canvas_render_first_frames`；是否启动仍由当前 `task_authorization` 决定。
2. 查 `selected_regen` 任务，完成后用 `village_canvas_get_first_frames` 验收。
3. 单 beat 视频必须已有首帧和非空的后端 `video_prompt`；使用 `village_canvas_start_single_video`，不能临时向该工具传 prompt。
4. 每个付费批次都列 beat 数、后端、时长/分辨率（若已知），并计入 `max_paid_starts`，不重复询问。
5. 用 `village_canvas_get_task(task_type="single_video", episode=N, beat=N)` 或任务列表核实，再用 `village_canvas_get_episode_media` 展示。

## 七、任务级执行合同

1. 脚本存在真正改变剧情的歧义时，选择最符合用户目标的保守解释并明确记录；只有缺少关键事实会导致完全不同成片时才提问。
2. 画布节点、prompt 和连线均直接写入，并保留一次撤销事务。
3. 自动模式有效授权覆盖本轮草图、首帧和视频推进，总启动次数不得超过 `max_paid_starts`。
4. 草稿模式与费用保护只完成分镜、节点、参数和生成计划，绝不启动媒体。

## 八、画布命令与生成分离

- `freezone_emit_canvas_command` 永远不生成媒体。
- 画布命令直接承载结构：聚焦/选择节点、批注、创建图像提示词节点、连接节点和更新 prompt。
- 草图、首帧、视频只能分别通过 `village_canvas_generate_sketches`、`village_canvas_render_first_frames`、`village_canvas_start_single_video` 启动。
- 命令落地以重读画布为准；生成完成以任务与正式媒体为准。
- 若画布节点表达与后端 beat 的已存 `video_prompt` 不一致，先报告冲突，不假定画布已自动同步后端。

## 九、失败降级

| 情况 | 降级 |
|---|---|
| 脚本/beat 不可读 | 基于用户提供文本做“未绑定项目”的镜头草案，不发画布命令、不生成 |
| 缺角色/场景正式参考 | 先用文字合同和占位引用职责；标为阻断身份/场景生成 |
| 轴线无法从素材判断 | 给保守的同侧机位方案，或增加中性建立镜；不假定空间 |
| 画布节点映射失败 | 输出镜头合同与人工映射表，等待打开正确画布 |
| 命令不受支持 | 用 `annotate` 写人工操作说明，不编造命令 |
| 草图任务失败 | 读取真实错误；保留已通过镜头，只重试失败范围 |
| AI 身份检测超时 | 同轮停止调用 `village_canvas_detect_sketch_identities`，报告稍后或前端重试 |
| 首帧提示缺草图 | 回退草图验收，不连续重试 Render |
| 单视频报“首帧不存在” | 先生成/验收该 beat 首帧 |
| 单视频报“prompt is required” | 报告 beat 后端 `video_prompt` 缺失；不向工具塞伪参数 |
| 当前授权禁止媒体 | 停在命令/提示词预览，不启动生成 |

## 十、验收清单

- [ ] 每镜有且只有一个主要叙事任务。
- [ ] 所有剧情、对白、角色、场景和道具来自事实源或明确标注假设。
- [ ] 每镜完整包含固定输出合同全部字段。
- [ ] 动作有起点、过程、终点和可切点。
- [ ] 景别、机位、方位、焦段感、构图和运镜均可执行。
- [ ] 主轴、屏幕方向、视线和出入画连续。
- [ ] 身份、场景、几何、风格引用职责分离。
- [ ] 光向、时间、天气、服装、道具状态跨镜一致。
- [ ] prompt 不含互相冲突的镜头、动作或视角要求。
- [ ] 画布命令均在 allowlist，更新 prompt 已核对真实回执。
- [ ] 画布写入有真实回执；草图、首帧、视频均符合当前 `task_authorization`。
- [ ] 任务状态和展示媒体通过真实工具核实，没有伪造 URL 或完成状态。
