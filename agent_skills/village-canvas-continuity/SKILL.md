---
name: village-canvas-continuity
description: Use for 角色一致性、身份漂移、服装发型、场景道具、左右关系、跨镜连续性、引用绑定检查，以及在 Village Infinite Canvas 画布上规划最低成本修复。
version: 2.0.0
---

> **Capability Contract v1**：indexed 模式下先用 `village_canvas_capability` 搜索/describe 精确能力，再 invoke；本文件中的旧式工具名只做语义映射。读取类能力使用 `media.*` / `task.*` / `script.get`，角色和场景生成使用 `creative.*`，所有媒体动作仍受当前回合授权与真实任务回执约束。

# Village Infinite Canvas 一致性监制规程

职责是建立权威锚点、逐镜找漂移、给出最小修复路径并验证修复，而不是凭印象把所有候选统一成“更好看”。连续性服务剧情事实和正式资产，不反向篡改角色设定。

## 一、适用场景

- 同一角色跨图/跨镜脸型、年龄、发型、体型、服装或伤痕漂移；
- 身份图、场景图、构图图、风格图引用混用；
- 场景布局、光向、时间、天气、道具状态不连续；
- 左右关系、视线、出入画、动作接力或轴线错误；
- 草图、首帧、视频候选之间需要分级 QC；
- 在当前画布上标出证据节点、修复提示词和安全连线方案。

不负责无依据地重做全部素材；不将审美偏好冒充连续性错误。

## 二、输入事实与权威层级

### 2.1 身份权威

1. 正式角色肖像和正式 identity image；
2. 角色数据中的姓名、身份、描述、`face_prompt` 等正式字段；
3. 用户明确批准的定稿资产；
4. 草图/首帧/视频只能作为待检对象，不能反向定义角色。

### 2.2 场景权威

1. 场景 master/reverse master（若存在）；
2. 剧本与场景数据中的地点、时间、天气、空间事实；
3. 用户明确批准的场景定稿；
4. 单张候选图不自动升级为场景权威。

### 2.3 叙事与时序权威

1. `village_canvas_get_episode_script`；
2. beat 顺序及其正式字段；
3. 已批准分镜合同；
4. 用户本轮明确修订。

开始检查前记录 project、episode、beat 范围、canvas_id（如有）、待检媒体层级、每类正式锚点。没有正式锚点时只能报告“相互不一致”，不能宣布哪一张正确。

## 三、真实工具速查

| 目的 | 工具 | 用法边界 |
|---|---|---|
| 读脚本 | `village_canvas_get_episode_script` | 获取角色在场、动作、道具和时序事实 |
| 读角色/场景/beat/画布 | `village_canvas_get` | 只读真实 API 数据；不猜字段或节点 |
| 展示角色权威媒体 | `village_canvas_get_character_media` | 可筛 portrait/identity；不手写 URL |
| 展示场景权威媒体 | `village_canvas_get_scene_images` | 可含 master/reverse/pano/custom；职责需分清 |
| 展示官方草图 | `village_canvas_get_sketches` | 不用候选或首帧替代 |
| 展示草图候选 | `village_canvas_get_sketch_candidates` | 只用于候选比较 |
| 展示首帧 | `village_canvas_get_first_frames` | 仅首帧 |
| 展示视频/音频 | `village_canvas_get_episode_media` | 视频连续性检查用 `media_type="video"` |
| 草图身份检测 | `village_canvas_detect_sketch_identities` | 自动检测不是最终视觉判决；超时同轮停止 |
| 看任务状态 | `village_canvas_get_task` / `village_canvas_list_tasks` | 不把排队/运行中当完成 |
| 读画布快照 | `freezone_get_canvas_snapshot` | 先读节点/引用/选中再审计 |
| 画布标注/连线/prompt | `freezone_emit_canvas_command` | 只发 allowlist 命令，绝不生成 |
| 提案生成 | `freezone_propose_generation` | 返修生成先提案；自动模式携带本轮授权继续，草稿模式停在提案 |
| 修复角色 face_prompt | `village_canvas_update_character_face_prompt` | 仅写正式角色脸部字段，基于真实身份锚点直接修复；face_prompt 不写服装 |
| 生成角色正式资产 | `village_canvas_generate_portrait` / `village_canvas_generate_identity_image` | 按当前 `task_authorization` 执行 |
| 生成场景正式资产 | `village_canvas_generate_scene_master` / `village_canvas_generate_scene_reverse` | 按当前 `task_authorization` 执行 |
| 返修草图/首帧/视频 | `village_canvas_generate_sketches` / `village_canvas_render_first_frames` / `village_canvas_start_single_video` | 按真实前置和最小范围执行 |

禁止编造 `continuity_check`、`bind_identity`、`replace_reference`、`regenerate_canvas_node` 等工具或命令。引用绑定若不能由 allowlist 精确表达，只输出连线方案或 `annotate` 人工说明。

## 四、不可伪造原则

1. 不因两张图不同就武断指定其中一张为权威；权威必须来自正式资产或用户批准。
2. 不伪造身份 ID、scene name、beat、节点 ID、资产来源、任务状态或引用边。
3. 不把风格变化判为身份漂移，除非它改变了稳定身份特征；不把剧情允许的换装判错。
4. 不用服装或情绪词污染 `face_prompt`；脸部身份、服装状态、表演状态分层修复。
5. 不把 reverse master 当新的场景，不把候选草图当官方草图，不把首帧当角色肖像。
6. 不以本地文件路径、任务输出路径或自造 URL 展示证据。
7. 不覆盖原素材；修复生成应产生新候选并保留 lineage，由用户选定。
8. 画布 prompt 可按任务直接更新；角色正式字段和付费生成仍按各自运行授权执行。
9. 自动身份检测结果只是证据之一；若与正式资产或肉眼可见事实冲突，标记待人工裁决。

## 五、固定输出合同

### 一致性审计单
- **范围**：project / canvas / episode / beats / 媒体层级
- **权威锚点**：`类别 | 正式资产/字段 | ID或名称 | 负责锁定的属性`
- **连续性状态表**：`镜号/节点 | 身份 | 发型服装 | 场景光线 | 道具 | 空间方向 | 动作情绪 | 结论`
- **问题清单**：每项严格使用下列格式

#### 问题 C-001
- **等级**：阻断 / 严重 / 轻微
- **证据**：beat、节点、媒体类型及可观察差异
- **漂移字段**：只列发生变化的具体属性
- **正确锚点**：正式资产/脚本字段；没有则写“待用户裁决”
- **影响**：身份误认、空间跳跃、动作断裂或轻微美术差异
- **最低成本修复**：保留项、修改项、引用职责、返修范围
- **修复提示词补丁**：仅包含需锁定/纠正的字段和负向约束
- **验证方式**：修复后如何复查

审计末尾固定追加：

- **修复优先级**：先阻断，再严重，最后轻微；同级按影响镜数排序
- **画布命令预览**：focus/select/annotate/create prompt/connect/update prompt 的精确清单
- **生成修复批次**：工具、episode/beat、数量、前置、本轮授权状态
- **保留资产**：明确哪些镜头无需重做
- **授权合同**：锚点来源、`run_mode`、媒体权限和最大启动次数

### 分级标准

- **阻断**：角色被误认、关键道具状态反转导致剧情错误、严重越轴使空间关系相反、错误场景/身份引用污染整批。
- **严重**：稳定脸部特征、年龄、服装连续状态、主光方向、出入画或动作接力明显跳变，但单镜仍可理解。
- **轻微**：不影响身份与叙事的细节变化，如不关键褶皱、微小饰品角度或背景群众变化。

## 六、分阶段流程

### 阶段 0：定界与冻结

1. 明确检查的是草图、首帧、视频还是画布结构。
2. 冻结目标 beat 范围，避免边查边扩大到整集。
3. 若任务正在生成，先查状态；不要对尚未完成的媒体下最终结论。

### 阶段 1：建立锚点账本

1. 读取脚本、角色、场景和 beat 事实。
2. 用 `village_canvas_get_character_media` 找正式肖像/身份图。
3. 用 `village_canvas_get_scene_images` 找 master/reverse 等正式场景参考。
4. 如有画布，读取最新画布并识别各引用节点职责。
5. 将稳定属性拆成：脸部身份、发型、体型、服装层次、饰品/伤痕、场景布局、光线、道具、空间方向、动作/情绪。

### 阶段 2：逐镜观测

对每个待检镜头只写可观察事实：

- 脸型、眼鼻嘴比例、年龄感、肤色；
- 发型轮廓、分缝、长度、颜色；
- 身高体型、服装件数、内外层、颜色、饰品、伤痕；
- 场景地标位置、门窗、家具、天气、时间、主光方向；
- 道具归属、左右手、朝向、开合、破损、液位；
- 人物左右、视线、出入画、动作完成度；
- 表情强度与上一镜结束状态。

不先解释原因，先完成观测表。

### 阶段 3：锚点比对与分级

1. 将每项差异与权威账本比对。
2. 判断是剧情允许的状态变化、镜头角度造成的可接受差异，还是实际漂移。
3. 逐项分级并给证据。
4. 没有锚点的冲突进入“待用户裁决”，不得自动选最好看的一张。

### 阶段 4：最低成本修复设计

修复顺序：

1. 纠正错误引用职责或连线；
2. 为目标节点增加明确的身份/场景/几何/风格分层批注或 prompt 节点；
3. 仅修订发生漂移的 prompt 片段；
4. 只返修受影响 beat；
5. 只有系统性污染时才建议整批重做。

提示词补丁遵循：

- **身份层**：稳定脸型、五官比例、年龄、肤色、发型；
- **服装状态层**：本镜允许变化的服装/饰品/伤痕；
- **场景层**：布局、地标、时间、天气、主光方向；
- **几何层**：姿态、构图、人物左右和道具位置；
- **风格层**：媒介和质感，不得改身份。

### 阶段 5：锚点决策与画布修复

Agent 先依据正式资产、当前固定节点和 lineage 选择可信度最高的锚点；没有唯一可信锚点时保留候选并标注依据。已有节点 prompt、角色正式 `face_prompt` 和引用连线可直接修复并保留撤销点；媒体重生成只读当前 `task_authorization`。

画布动作仅通过 `freezone_emit_canvas_command` 发出。可先 `focus_node`/`select_node`，再 `annotate` 或 `create_image_prompt_node`，需要时 `connect_nodes`。`update_node_prompt` 使用具体节点 ID 直接执行。命令后按回执决定是否重读画布。

### 阶段 6：生成修复（按本轮授权）

1. 把“画布修复已完成”和“媒体重生成待开始”分开报告。
2. `task_authorization.allow_paid_media=true` 时调用真实生成工具；否则停在完整返修计划。
3. 角色锚点缺失时可分别使用 `village_canvas_generate_portrait` 或 `village_canvas_generate_identity_image`；场景锚点缺失时可使用 `village_canvas_generate_scene_master` / `village_canvas_generate_scene_reverse`。
4. 草图、首帧、视频返修按依赖顺序执行，且只跑受影响 beat。
5. 用 `village_canvas_get_task` 查真实结果，再用对应媒体展示工具复验。
6. 新候选通过前不宣布旧资产被替换。

## 七、画布命令与生成分离

- `freezone_emit_canvas_command` 只改变当前 UI 画布意图，不触发后端媒体生成。
- `connect_nodes` 只表示画布关系；是否成为身份/场景权威要以节点语义和重读结果验证。
- `create_image_prompt_node` 只创建修复 prompt，不产图。
- 正式角色/场景资产生成与草图/首帧/视频返修使用各自真实工具，并统一服从当前任务授权。
- 用户批准“修复连线”不等于批准重生成；批准“返修 Beat 3”不等于批准整集重跑。

## 八、失败降级

| 失败 | 降级动作 |
|---|---|
| 找不到正式身份锚点 | 报告“无权威锚点”，展示可选候选，请用户裁决；不生成统一结论 |
| 找不到场景 master | 以脚本空间事实做文字锚点，场景视觉一致性标待建基准 |
| 媒体不可展示 | 报告缺失字段/工具错误，不使用本地路径或伪 URL |
| 当前画布不可读 | 交付只读审计单和人工节点映射要求，不发命令 |
| 引用修复无法用 allowlist 表达 | 用 `annotate` 写明确人工操作，禁止编造 `set_reference` |
| 自动身份检测超时 | 同轮停止重试，保留人工审计结果，建议稍后或前端重试 |
| `face_prompt` 缺失导致肖像失败 | 基于真实身份锚点写纯脸部字段并更新；媒体生成按当前任务授权继续 |
| Render 缺草图 | 回退草图前置，不连续重试首帧 |
| 单视频缺首帧或 video_prompt | 精确报告缺失前置，不向工具传不存在的 prompt 参数 |
| 多镜系统性漂移但预算不足 | 先修权威引用与最高风险镜，低风险镜留待后续 |
| 新候选仍漂移 | 保留旧资产与问题编号，缩小补丁，不盲目整批重跑 |

## 九、验收清单

- [ ] 已区分正式权威、用户定稿、当前官方媒体和普通候选。
- [ ] 每个问题都有具体 beat/节点/媒体证据，而非抽象评价。
- [ ] 身份、服装、场景、道具、空间、动作/情绪均已逐项检查。
- [ ] 剧情允许的换装、受伤、时间变化没有被误判。
- [ ] 所有问题按阻断/严重/轻微分级。
- [ ] 每项包含漂移字段、正确锚点、最低成本修复和验证方式。
- [ ] 修复 prompt 按身份/服装/场景/几何/风格分层，无职责污染。
- [ ] 无锚点冲突已交用户裁决，没有擅自指定“最好看”的候选。
- [ ] 画布命令均真实受支持，prompt 更新已验证落地。
- [ ] 画布修复与媒体任务分别记录，生成严格符合当前 `task_authorization`。
- [ ] 只返修受影响范围，保留通过镜头和原始 lineage。
- [ ] 完成状态与媒体通过真实任务/展示工具核实，没有伪造 URL。
