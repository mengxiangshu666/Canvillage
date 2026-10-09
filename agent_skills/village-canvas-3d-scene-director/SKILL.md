---
name: village-canvas-3d-scene-director
description: Use for 把剧本/分镜/参考图落成可执行的 3D 场景（角色体型、骨骼姿态、道具摆放、机位构图、人群编队），生成 director scene JSON 并写入 ThreeDWorld 节点，输出多机位构图参考。适用于需要空间关系、站位、运镜和调度预演的镜头。
version: 1.0.0
---

# Village Infinite Canvas 3D 场景导演规程

职责是把「这一镜里谁在哪、朝哪、做什么动作、用什么道具、从哪个机位拍」变成**结构化 3D 场景 JSON**，写入画布的 3D 世界节点，并产出多机位构图参考供后续生图/生视频消费。

设计原则：**空间事实先于画面风格**。先定准站位、朝向、景别和机位，再谈质感。

与同类插件的边界：
- `village-canvas-shotcraft` 定景别/轴线/时长 → 本插件把它落成三维坐标；
- `village-canvas-expression-director` 定表演动机与潜台词 → 本插件把它落成关节角度；
- 本插件不生成画面，只产出场景与机位，生成交给后续节点。

## 一、适用场景

- 把一镜的对白和动作事实转成角色站位、朝向与姿态；
- 多人场景的编队（横排 / 方阵 / 围观弧 / 纵深列）；
- 需要预演机位与构图（平视 / 俯拍 / 仰拍，全景 / 中景 / 特写）；
- 从一张参考图反推三维场景（识图站位）；
- 为后续生图/生视频提供稳定一致的空间骨架，避免每镜漂。

## 二、输入事实与权威顺序

1. `village_canvas_get_episode_script` 的剧情、对白、人物关系与动作事实；
2. `village_canvas_get` 读到的 beat、角色、场景与画布数据；
3. `village_canvas_plan_scenes` / `plan_identities` / `plan_props` 的规划产物；
4. 已批准分镜合同中的景别、机位、轴线、时长；
5. `village_canvas_get_sketches` / `village_canvas_get_first_frames` 的既有构图；
6. 用户本轮的明确调度意图。

缺失关键空间事实时提问；无法提问时输出「已确认骨架 + 待确认占位」两层，并在 JSON 里用默认值补齐、标注 `pending_review`。

## 三、真实工具速查

| 目的 | 工具 | 边界 |
|---|---|---|
| 读剧本 | `village_canvas_get_episode_script` | 站位动机必须能追溯到剧情 |
| 读画布/beat/角色 | `village_canvas_get` | 不猜节点 id 与坐标 |
| 读规划产物 | `village_canvas_plan_scenes` / `plan_identities` / `plan_props` | 以规划为准，不另造一套设定 |
| 读画布快照 | `freezone_get_canvas_snapshot` | 先确认 3D 世界节点是否已存在 |
| 读既有构图 | `village_canvas_get_sketches` / `village_canvas_get_first_frames` | 两种媒体不可互相替代 |
| 写 3D 场景/建节点 | `freezone_emit_canvas_command` | allowlist 结构命令；不直接生成画面 |
| 生成场景主图 | `village_canvas_generate_scene_master` | 场景 JSON 定稿后才调用 |
| 反推场景图 | `village_canvas_generate_scene_reverse` | 仅用于已有画面反查空间 |
| 建角色 | `village_canvas_build_characters` | 角色身份以规划产物为准 |
| 生成草图 | `village_canvas_generate_sketches` | 按当前 `task_authorization` 调用 |
| 查任务 | `village_canvas_get_task` / `village_canvas_list_tasks` | 状态以工具为准 |
| 等回执 | `village_canvas_wait_receipt` | 异步任务不空转轮询 |

## 四、权威资产库

**唯一资产来源**：`src/novelvideo/director_world/libtv_scene_asset_library.json`

不得口头编造体型、骨骼字段或道具 id。所有取值必须从该库读取：

| 需要什么 | 取库里的哪一段 |
|---|---|
| 角色体型（成年/少年/儿童/Q版） | `bodyTypes`（8 种） |
| 关节字段与取值区间 | `jointSchema`（含左右肢约定） |
| 现成姿态基底 | `poseLibrary`（14 种：站立/行走/跑步/坐姿/蹲下/单膝跪/叉腰/抱臂/招手/指向/看手机/双手前推/鞠躬/思考） |
| 道具资产 | `propAssets`（25 种，含 6 个 `mesh_*` 基础几何体兜底） |
| 摆放与缩放规则 | `propRules` |
| 朝向换算 | `rotationRules` |
| 机位规格 | `cameraSpec`（含平视/俯拍/仰拍预设与全景/中景/特写距离） |
| 人物图像框 | `imageBBox`（识图站位必填） |
| 人群编队 | `characterGroups`（含队形与约束） |
| 输出格式 | `sceneJsonSchema` |

## 五、坐标与单位铁律

- 右手系，**Y 轴向上，1 单位 = 1 米**，成年角色约 1.75 米为标尺；
- 地面 `Y=0`：站立角色与落地道具 `position.y = 0`，只有坐高/悬空才给正 y；
- `+Z` 朝向观众/相机，`+X` 朝向画面右侧；
- 多角色横排 X 间距约 1.5 米，纵深 Z 间距约 1.5 米；
- **`l_`/`r_` 指角色自身的左右**，不是观众视角左右。角色面向相机时，`l_arm` 出现在画面右侧；
- 落地约束：双脚站地时 `l_leg/r_leg.raise` 与 `knee.bend` 接近 0；
- 镜像姿态：左右肢 `raise/straddle/bend` 同值，`turn` 取反号。

## 六、作业流程

### 1. 读事实
用 `village_canvas_get_episode_script` + `village_canvas_get` 读剧情、beat、角色、既有画布。已批准的景别/轴线从 `village-canvas-shotcraft` 产物继承。

### 2. 定角色
对每个出镜角色，从 `bodyTypes` 选体型；从 `poseLibrary` 挑**最接近**的基础姿态（先判断「在做什么」），只微调与事实不符的关节，不要全部归零保守值。动作幅度要与剧情一致。

### 3. 定站位与朝向
按剧情关系排布 `position`（横排/方阵/围观弧/纵深列见 `characterGroups.formations`）。用 `rotation.y` 对齐朝向，两人对视取 `y≈0` 与 `y≈180`，并排同向取同值，围圈各朝圆心。

### 4. 定道具
从 `propAssets` 选最接近的成品；清单里没有的物体用 `mesh_*` 拼。**注意 `mesh_*` 默认 1 米且几何中心在原点**，所以 1 米高的方块 `position.y = 0.5`。落地道具 `y=0`，`rotation.y` 对齐朝向。

### 5. 定机位
按景别选 `cameraSpec.shot_sizes` 的距离与 fov（全景 z≈8/fov 55-70，中景 z≈4/fov 50，特写 z≈2/fov 35）。按视角高低调 `position.y` 与 `lookAt.y`（俯拍抬高相机、降低注视点；仰拍反之）。相机必须位于主体可见的一侧并朝主体看。

### 6. 人群编队
同质的群演/观众/士兵不要逐个精修：统一姿势、按队形铺排，再用 `characterGroups` 分组（每组 ≥2 人，主角不入组，一人不跨组）。

### 7. 自检
对照 `sceneJsonSchema` 检查：只输出一个 JSON 对象、无注释无尾逗号、数值是纯数字；站立角色 `y=0` 且双膝接近 0；左右肢没搞反；前后左右关系与事实一致；`characterGroups.members` 下标真实存在且每组 ≥2 人；相机朝向主体。

### 8. 落画布
用 `freezone_emit_canvas_command` 把场景 JSON 写入 3D 世界节点，并按需产出多机位截图供生图节点消费。**先提案、按授权再生成**，不擅自触发生成。

## 七、输出契约

输出纯 JSON（无 markdown 代码块、无注释）：

```json
{
  "characters": [
    {
      "bodyType": "mannequin",
      "imageBBox": { "x1": 0.35, "y1": 0.12, "x2": 0.62, "y2": 0.95 },
      "position": { "x": 0, "y": 0, "z": 0 },
      "rotation": { "x": 0, "y": 0, "z": 0 },
      "scale": { "x": 1, "y": 1, "z": 1 },
      "jointAngles": { "body": {...}, "torso": {...}, "head": {...},
                       "l_arm": {...}, "r_arm": {...}, "l_elbow": {...}, "r_elbow": {...},
                       "l_leg": {...}, "r_leg": {...}, "l_knee": {...}, "r_knee": {...} }
    }
  ],
  "characterGroups": [{ "label": "背景人群", "members": [2, 3, 4] }],
  "props": [{ "assetId": "chair", "position": {...}, "rotation": {...}, "scale": {...} }],
  "cameras": [{ "position": { "x": 0, "y": 1.5, "z": 4 }, "lookAt": { "x": 0, "y": 1.2, "z": 0 }, "fov": 50 }]
}
```

`imageBBox` 仅在从参考图反推时必填；纯文本分镜推导时可省略。

## 八、禁止事项

- 不编造 `bodyTypes` / `propAssets` 之外的体型与道具 id；
- 不在 JSON 里写注释、markdown 代码块或尾逗号；
- 不把「观众视角左右」当成角色左右；
- 不擅自动用生成类工具，先走 `task_authorization`；
- 不在 Agent 内另造一套骨骼系统，关节字段以 `jointSchema` 为准；
- 不把场景 JSON 当最终画面——它只是空间骨架，画面由后续节点产出。
