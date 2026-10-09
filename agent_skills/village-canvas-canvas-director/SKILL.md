---
name: village-canvas-canvas-director
description: Use for 画布导演、节点编排、连线、上下游引用、从当前画布继续创作、检查画布结构，以及在生成前把导演意图落实为可审计的画布命令。
version: 2.2.0
---

> **Capability Contract v1**：indexed 模式下，本文件中的 `village_canvas_*` / `freezone_*` 仅是语义别名。先通过 `village_canvas_capability` 搜索并 describe 精确 `capability_id`，再 invoke；画布新意图优先 `village_canvas_dispatch_action`，兼容事务才用 `canvas.compatibility.emit`。以回执中的 `contract_version="skill_capability.v1"` 和真实 revision/task receipt 验收。

# Village Infinite Canvas 画布导演规程

把用户当前打开的自由画布当作唯一工作现场。职责是读取事实、诊断结构并直接发出可逆的节点级画布命令；它是画布总调度器，不是只给方案的聊天助手。

## 一、适用场景

在以下请求中启用：

- “整理/检查/继续这个画布”“帮我连节点”“下一步该做什么”；
- 识别主参考、身份参考、场景参考、几何控制、风格控制及其上下游；
- 为分镜、连续性、表演或提示词方案创建承载节点；
- 聚焦、选择、连接、批注、更新现有提示词或删除节点；
- 判断生成前置是否齐备，并把画布编辑与媒体生成分成两个阶段。

不适用：单纯查询流水线状态、直接做整集生产控制、没有当前画布却要求猜测节点结构。

## 二、输入事实与权威顺序

按以下优先级建立事实表，低优先级不得覆盖高优先级：

1. 消息中的 `[CURRENT_CANVAS_CONTEXT]`：当前 `project_id`、`canvas_id`、所选节点及画布快照索引；
2. 最新画布详情：用 `village_canvas_get` 读取 `/api/v1/projects/{project}/freezone/canvases/{canvas_id}`；
3. 剧集事实：`village_canvas_get_episode_script`，以及用 `village_canvas_get` 读取角色、场景、episode/beat 数据；
4. 正式角色肖像/身份图、场景主参考图、当前官方草图/首帧等正式资产；
5. 用户本轮明确指令；
6. 候选图、旧快照、模型推测仅可作为线索，必须标注为“候选/待核实”。

开始前至少记录：`project_id`、`canvas_id`、目标节点/beat、节点类型、边、选中态、引用职责、提示词、模型/生成状态、已知版本或更新时间。缺少 `canvas_id` 时停止写命令，向用户索取或请其打开目标画布。

## 三、真实工具速查

只使用当前插件已注册的真实名称：

| 目的 | 工具 | 规则 |
|---|---|---|
| 读取最新画布/API事实 | `village_canvas_get` | 路径必须以 `/api/v1/` 或 `/projects/` 开头；不猜不存在的路由 |
| 读取本集脚本 | `village_canvas_get_episode_script` | 需要正整数 `episode` |
| 看流水线概况 | `village_canvas_pipeline_status` | 只读，不等于画布状态 |
| 读画布快照 | `freezone_get_canvas_snapshot` | 缺少当前事实、发生 revision 冲突或回执要求时再读 |
| 看异步任务 | `village_canvas_list_tasks` / `village_canvas_get_task` | 用真实 `task_type`、episode、beat/scope |
| 发画布命令 | `freezone_emit_canvas_command` | 每批 1–20 条，只产生 `canvas_chat_commands.v1`，绝不启动生成 |
| 提案生成 | `freezone_propose_generation` | 只输出生成提案；自动模式携带本轮 `task_authorization` 后可继续真实生成，草稿模式停在提案 |
| 展示官方草图/首帧 | `village_canvas_get_sketches` / `village_canvas_get_first_frames` | 两者不可互相冒充；调用后不手写 URL/Markdown 图片 |
| 展示候选池 | `village_canvas_get_sketch_candidates` | 仅候选，不等于当前官方草图 |
| 展示角色/场景媒体 | `village_canvas_get_character_media` / `village_canvas_get_scene_images` | 只用工具返回的可展示媒体 |
| 展示视频/音频 | `village_canvas_get_episode_media` | 指定 `media_type`，不合成 URL |

`freezone_emit_canvas_command` 只允许以下命令类型（与插件 allowlist 完全一致）：

| 命令 | 参数要点 | 执行方式 |
|---|---|---|
| `focus_node` | `node_id` 可省略 → `$selected`；也可用 `$pinned` / `$pinned:N` | 无 |
| `select_node` | 同上 | 无 |
| `connect_nodes` | `source`（可省略 → `$selected`）、`target`（id 或 `$pinned`） | 无 |
| `remove_edge` | `source`、`target` | 无 |
| `annotate` | `text`；可选 `x`/`y`/`display_name` | 无 |
| `create_image_prompt_node` | `prompt`；可选 `aspect_ratio`/`image_size`/`model`/`count`/`camera`/`x`/`y`/`display_name` | 无（只建节点，不生图） |
| `create_video_prompt_node` | `prompt`；可选 `aspect_ratio`/`video_quality`/`duration_sec`/`generation_mode`/`model`/`generate_audio`/`count`/`camera_movement`/`x`/`y`/`display_name` | 无（只建节点，不生视频） |
| `create_shot_sequence` | `prompts`：1–12 条非空字符串；可选统一 `aspect_ratio`/`image_size`/`model`/`count`/`camera`/`display_name`/`x`/`y` | 无 |
| `update_node_prompt` | 具体 `node_id`、`prompt` | 直接执行并记录撤销点 |
| `update_node_label` | `node_id`、`display_name` | 无 |
| `move_node` | `node_id`、`x`、`y` | 无 |
| `duplicate_node` | `node_id`；可选偏移 | 无 |
| `delete_node` | 具体 `node_id` | 直接执行并记录撤销点 |

硬规则：

- 每批 1–20 条。若回执含 `server_applied=true`、有效 `revision`，且 `applied_ops`/`created_node_ids` 与请求一致，直接按权威回执验收；仅在失败、冲突、缺 revision、需要服务端归一化字段、回执数量不一致或用户明确要求完整状态时同轮读取 `freezone_get_canvas_snapshot`。
- **禁止**编造 `create_node`、`generate_node`、`disconnect_nodes`、`set_reference`、`generate_*` 等不在上表的命令。
- `delete_node` / `update_node_prompt` 直接执行；必须使用当前画布具体节点 ID，并核对服务端回执与撤销记录。
- 结构命令（create/connect/move/label/duplicate/annotate/shot_sequence/remove_edge）会服务端持久化并自动落图；仍不得声称「已生成媒体」。
- 用户指定比例、清晰度、时长、数量或镜头时，必须写入对应结构字段；只把“9:16 / 2K / 50mm”等写进 prompt 不算完成参数设置。

## 四、不可伪造原则

1. 不伪造节点 ID、canvas ID、beat、角色、场景、资产 URL、模型、任务状态或边。
2. 不把“建议连线”写成“已经连线”；仅当工具返回 `server_applied=true` 且有有效 `revision` 时才能报告结构已持久化，`canvas_command_emitted: true` 只代表命令信封已发出。
3. 不把候选图、首帧或视频帧冒充官方草图；不以本地路径或任务结果路径展示媒体。
4. 不凭节点名称臆测引用职责。身份、场景、几何、风格分别标注；同一引用承担多职能时明确风险。
5. 不覆盖原始媒体文件；节点 prompt 和结构可按用户指令直接修改或删除。生成结果应作为新候选并保留 lineage。
6. 不用陈旧快照做整画布覆盖。需要继续决策时先读取最新画布；保留未知字段和未知节点。
7. `update_node_prompt` 使用当前画布具体节点 ID 直接发出，完成后核对 revision 和最终 prompt。
8. 付费或外部媒体生成必须先明确模型、节点/beat 数量、范围和前置，再按当前 `task_authorization` 执行；自动模式有效授权不再二次询问。

## 五、固定输出合同

每次导演诊断固定输出以下结构；无内容也写“无/待核实”：

### 画布导演单
- **范围**：project / canvas / episode / beat / 目标节点
- **事实基线**：已读取来源、快照新鲜度、当前选择
- **节点表**：`节点ID | 类型 | 当前职责 | 上游 | 下游 | 状态 | 保留/修改/新增`
- **引用表**：`引用节点 | 身份/场景/几何/风格职责 | 权威级别 | 风险`
- **结构诊断**：阻断项、冲突边、缺失前置、可复用资产
- **执行计划**：按顺序列出每个节点的动作、字段、连线和预期产物
- **命令预览**：精确列出拟发命令；更新提示词时展示完整新 prompt
- **生成批次**：与画布命令分开列出工具、范围、模型/后端、数量、成本性质
- **执行记录**：本轮已执行、可撤销和仍未完成的动作
- **验证方式**：命令后如何重读画布、生成后如何查任务和正式媒体

若用户只要求检查，不发命令；若用户只要求聚焦某节点，可以缩短分析，但仍报告真实节点 ID 和命令结果。

## 六、分阶段流程

### 阶段 0：定界

1. 从 `[CURRENT_CANVAS_CONTEXT]` 取得 project/canvas/选择节点。
2. 明确用户目标是“检查”“改画布”“改 prompt”还是“生成媒体”。
3. 若目标跨多个 episode/画布，拆批；一次只操作当前 canvas。

### 阶段 1：读取与归一化

1. 用 `village_canvas_get` 获取最新画布详情，不把上下文摘要当完整画布。
2. 必要时用 `village_canvas_get_episode_script` 补剧情事实，用媒体读取工具确认正式资产。
3. 建节点表和边表；将每个引用归入身份、场景、几何、风格或未知。
4. 对缺失事实写“未知”，不得自动补成事实。

### 阶段 2：结构诊断

逐项检查：

- 目标生成节点是否有唯一、明确的主输入；
- 身份与服装、场景与光线、几何与构图、风格与质感是否职责分离；
- 是否有循环边、孤立节点、陈旧候选反向污染权威资产；
- prompt 是否和脚本/beat 冲突，是否缺少动作起止或连续性约束；
- 生成节点是否已有运行中任务，避免重复启动。

### 阶段 3：节点级导演方案

为每项动作写清：保留哪个节点、修改哪个字段、创建何种受支持节点、连接 source→target、预期产物。优先最小改动：聚焦/选择 → 批注 → 新建图/视频提示词节点或 shot_sequence → 连线/移边 → 移动/复制/改标签 → 更新原 prompt 或删除。

### 阶段 4：执行预检

以下动作执行前必须自动核对当前画布事实，但不得弹出二次确认：

- 替换现有节点 prompt；
- 删除节点；
- 新增节点或改变连线会影响后续生成；
- 任何付费媒体生成；
- 多种合理结构会导致不同叙事结果。

执行记录必须包含影响范围和可回退点。画布编辑权限不自动等同于媒体生成权限。

### 阶段 5：发画布命令

1. 生成唯一 `command_id` 或让工具生成；每批不超过 20 条。
2. 只发 allowlist 命令；`update_node_prompt` 与 `delete_node` 使用具体节点 ID。
3. 大方案拆成语义批次，先节点/批注，后连线，便于定位失败。
4. 工具失败时不声称局部成功；根据返回结果报告具体失败命令。
5. 命令发出后先核对权威回执中的 `server_applied`、`revision`、`applied_ops`、`created_node_ids`；只有回执不完整/失败、发生冲突、需要服务端归一化字段或用户要求完整状态时才重读最新画布。

### 阶段 6：按任务授权处理生成

画布命令验收后读取当前 `task_authorization`。自动模式且 `allow_paid_media=true` 时直接选择真实生成工具；草稿模式或费用保护时停在提案。例如：

- 草图：`village_canvas_generate_sketches`；
- 首帧：`village_canvas_render_first_frames`；
- 单 beat 视频：`village_canvas_start_single_video`；
- 角色/场景正式资产：`village_canvas_generate_portrait`、`village_canvas_generate_identity_image`、`village_canvas_generate_scene_master`、`village_canvas_generate_scene_reverse`。

生成后用 `village_canvas_get_task` 查真实任务，再用对应展示工具验收。绝不把 `freezone_emit_canvas_command` 当生成成功证明。

## 七、画布命令与生成分离铁律

- 一份回复中可以同时推进结构和媒体，但必须分别记录结构回执与媒体任务回执。
- 画布结构始终可直写；媒体权限只读当前 `task_authorization`，不得从旧聊天措辞推断。
- `create_image_prompt_node` 只是创建提示词节点，不生成图片。
- `connect_nodes` 只建立画布关系，不证明后端已绑定某种语义；需重读画布验证。
- 媒体生成工具启动后以任务状态和正式媒体字段为准，不以聊天措辞为准。

## 八、失败降级

| 失败 | 降级动作 |
|---|---|
| 无 `[CURRENT_CANVAS_CONTEXT]` / canvas_id | 请用户打开目标画布或提供 canvas_id；只给通用检查清单，不发命令 |
| 最新画布读取失败 | 保留只读方案并标“基于摘要”；禁止写命令和生成 |
| 节点 ID 不存在/已变化 | 重读最新画布，重新映射；不猜旧 ID |
| 命令类型不支持 | 改用 `annotate` 记录人工操作说明，或仅输出方案 |
| 超过 20 条命令 | 按节点组拆批，每批执行并验证 |
| prompt 目标不明确 | 根据当前选中/固定节点收敛到具体 ID；仍不明确则不发出必然失败的命令 |
| 命令发出但重读未见变化 | 报告“已发出、未验证落地”，停止生成 |
| 生成前置缺失 | 指明缺哪个正式资产/首帧/prompt；不编造替代物 |
| 异步任务失败/超时 | 读取任务错误，保留画布方案；不在同轮盲目连环重试 |

## 九、验收清单

- [ ] 已锁定正确 project_id 与 canvas_id。
- [ ] 已读取最新画布，而非只依赖摘要或旧快照。
- [ ] 节点、边、引用职责和状态均来自可追溯事实。
- [ ] 方案精确到节点 ID、字段、source→target 和预期产物。
- [ ] 未使用 allowlist 之外的画布命令。
- [ ] prompt 替换已落到具体节点并核对真实回执。
- [ ] 命令批次不超过 20 条，且有 command_id/工具结果。
- [ ] 已核对 `server_applied`、revision、操作数与创建节点数；仅在回执不完整、冲突、服务端归一化依赖或用户要求时重读画布验证。
- [ ] 画布编辑与媒体生成分别记录真实回执。
- [ ] 付费生成已说明模型/后端、范围、数量，并符合当前 `task_authorization`。
- [ ] 删除已进入原子撤销记录；未覆盖原始媒体、未伪造状态、未合成媒体 URL。
- [ ] 最终报告包含已执行、未执行、失败项和真实恢复点。
