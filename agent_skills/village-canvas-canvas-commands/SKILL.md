---
name: village-canvas-canvas-commands
description: 村长无限画布命令协议（canvas_chat_commands.v1）完整操作手册——13 种命令类型、$selected/$pinned 引用、server_apply 收据语义、原子撤销和常见画布工作流模式。
triggers:
  - 画布操作
  - 创建节点
  - 分镜序列
  - 连接节点
  - 移动节点
  - 删除节点
  - 画布命令
  - emit_canvas_command
---

# 村长无限画布命令协议（canvas_chat_commands.v1）

## 核心铁律

1. **操纵画布只准用 `freezone_emit_canvas_command`**，绝不使用 python / search / navigate / browser 工具改画布。
2. `freezone_emit_canvas_command` 只做**结构操作**（建节点/连线/移动/标注），**绝不触发媒体生成**（图片/视频/音频用 `freezone_propose_generation` 提案）。
3. 用真实 ID（来自画布快照），收据完整就**不要**再读快照（省一次往返）。

## 工具签名

```
freezone_emit_canvas_command(
  project_id: str,      # 默认取 VILLAGE_CANVAS_PROJECT_ID
  canvas_id: str,       # 当前画布 ID（来自 CURRENT_CANVAS_CONTEXT）
  command_id: str,      # 批次幂等 ID（同一次多操作共用一个）
  commands: [           # 1–20 个操作
    {
      type: <13种命令之一>,
      node_id: "<具体ID 或 $selected / $pinned / $pinned:N>",
      source: "<边起点，省略→$selected>",
      target: "<边终点，具体ID 或 $pinned>",
      text: str, prompt: str, prompts: [str],
      display_name: str, x: number, y: number
    }, ...
  ]
)
```

## 13 种命令类型

| 命令 | 作用 | 关键参数 | 执行方式 |
|---|---|---|---|
| `create_shot_sequence` | 批量创建分镜节点（1–12 个） | `prompts: [str]`，每项非空 ≤50k | ✅ 服务端直写 |
| `create_image_prompt_node` | 创建图片提示节点 | `prompt` 必填 | ✅ 服务端直写 |
| `create_video_prompt_node` | 创建视频提示节点 | `prompt` 必填 | ✅ 服务端直写 |
| `update_node_label` | 改节点标签 | `node_id`/`$selected` + `display_name` | ✅ 服务端直写 |
| `move_node` | 移动节点 | `node_id`/`$selected` + `x` `y` | ✅ 服务端直写 |
| `duplicate_node` | 复制节点 | `node_id`/`$selected` | ✅ 服务端直写 |
| `focus_node` | 聚焦节点 | `node_id`/`$selected` | ✅ |
| `select_node` | 选择节点 | `node_id`/`$selected` | ✅ |
| `connect_nodes` | 连接节点（建边） | `source`(缺省$selected) + `target`(ID 或 $pinned) | ✅ 服务端直写 |
| `remove_edge` | 移除边 | `source` + `target` | ✅ 服务端直写 |
| `annotate` | 标注节点 | `node_id`/`$selected` + `text` | ✅ |
| `update_node_prompt` | 改节点提示词 | 具体 `node_id` + `prompt` | ✅ 服务端直写 + 原子撤销 |
| `delete_node` | 删除节点 | 具体 `node_id` | ✅ 服务端直写 + 原子撤销 |

## $selected / $pinned 引用系统

- `$selected`：当前 UI 选中节点（省略 node_id 时默认）。
- `$pinned`：固定参考节点；`$pinned:N` 指第 N 个固定节点。
- **别名只在 UI 层解析**，服务端直写前必须解析为具体 ID（先读快照确认）。

## server_apply 收据（必读）

调用后检查返回：

```
server_applied: bool    # 是否已服务端持久化
revision: int           # 画布新版本号（>0 且 applied 才算权威）
applied_ops: int        # 实际应用的操作数
created_node_ids: [str] # 新建节点 ID 列表
structure_status:       # server_applied_verified / server_applied_noop / emit_only
handoff_level:          # L1=已落库无需再读快照；L0=需自动恢复或报告失败
snapshot_required: bool # true 才需要重读快照，false 直接信任收据
error_code:             # FZ_EMIT_NOOP / FZ_SERVER_APPLY_FAILED / FZ_EMIT_UNVERIFIED
```

规则：
1. `server_applied=true` + 有效 revision + (created_ids 或 applied_ops>0) → **L1 完成，不再读快照**。
2. `snapshot_required=true`（失败/不完整/冲突）→ 调 `freezone_get_canvas_snapshot` 重读。
3. `update_node_prompt` / `delete_node` 使用具体节点 ID，直接持久化并与同批其他命令组成一个撤销事务。
4. 同 `command_id` 重放 = 幂等，不会重复修改、删除或建节点。

## 常见工作流模式

### 模式 1：分镜流水线（剧情→画布）
```
1. freezone_get_canvas_snapshot  → 拿 canvas_id + 现有节点
2. freezone_emit_canvas_command:
   commands=[{type:create_shot_sequence, prompts:[分镜1,分镜2,...]}]
3. 用返回 created_node_ids 引用新节点
4. freezone_propose_generation  → 对分镜节点提案生成
```

### 模式 2：图片节点 + 连接
```
1. 创建 create_image_prompt_node（prompt=角色设定）
2. 创建 create_image_prompt_node（prompt=场景设定）
3. connect_nodes(source=$selected, target=$pinned) 建边
4. move_node 调整布局（x,y）
```

### 模式 3：直接改稿
```
1. 从当前 V2 canvas 取得具体节点 ID
2. update_node_prompt(node_id=具体ID, prompt=新提示词)
3. 核对 server_applied / revision / applied_ops
```

## 负面约束

- 不要在一次 emit 里混入媒体生成请求。
- delete/update 必须使用当前项目、当前画布中的具体节点 ID；禁止继承旧会话 ID。
- 收据 `structure_status=server_applied_verified` 后不要画蛇添足再查快照。
- commands 一次最多 20 个操作，prompt 单条 ≤50k 字符。
