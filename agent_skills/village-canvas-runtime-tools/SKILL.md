---
name: village-canvas-runtime-tools
description: Use for any 小树 canvas request that reads or changes the canvas, starts images/video/audio, manages tasks, or runs a workflow (画布、节点、图片、视频、音频、任务、工作流、分镜、成片). It is the authoritative list of tools this product actually exposes and the scope rules that keep simple requests simple.
---

# 小树运行时工具面（本产品唯一权威）

## 先判断：这次到底要不要工作流

- 用户只要 N 个资产（N 张人物卡、N 个场景、N 张道具图、N 段视频）：**只建 N 个节点**，不铺分镜、不建占位节点、不启动工作流。
- 用户明确说“工作流、分镜、镜头、整片、成片、一集、连续多镜”：才允许工作流或分镜结构。
- 用户没说，但任务确实需要多步：先用文字说明计划，等用户确认，不要自作主张扩成制作流水线。
- 不要为了“更完整”额外添加用户没要的节点、连线、空分镜或备用版本。

## 工具表（只允许这些，其他 `tapcanvas_*` 工具不存在）

| 工具 | 用途 | 注意 |
|---|---|---|
| `village_canvas_read_compact` | 读当前画布节点、连线、任务和提示词摘要 | 每次写入前后各读一次，作为回执依据 |
| `village_canvas_dispatch_action` | 编译后的动作入口：创建/修改节点、启动生成 | 必须带 `task` 事实和 `commands`；简单资产批次会直接走画布，不启动工作流 |
| `village_canvas_apply_commands` | 直接应用一批画布命令 | 只用于明确的节点/连线修改，必须带 `command_id` |
| `freezone_run_node` | 启动一个已存在节点的生成任务 | 一次一个节点；付费前确认节点和参数 |
| `freezone_stop_task` | 停止任务 | 只停本轮真实启动的任务 |
| `village_canvas_start_workflow_run` | 启动持久工作流 | 只有用户明确要工作流/成片时使用 |
| `village_canvas_command_workflow_run` | 暂停/恢复/取消/重试工作流 | 先读运行状态，再下命令 |
| `village_canvas_get_workflow_run` | 读工作流运行与检查点 | 恢复前必须先读 |
| `village_canvas_list_tasks` | 列出任务 | 找已启动任务时用 |
| `village_canvas_get_task` | 读单个任务状态/产物 | 轮询用，不要重复提交 |

`record_user_intent`、`report_delivery`、`get_delivery_evidence` 是运行时的内部交付流程，
由系统自动处理；不要在回复里复述它们、合同 JSON 或证据 ID。

## 写入纪律

1. 每个写操作带稳定的 `command_id`（同一意图重试时复用，不要带时间戳或随机数）。
2. 写入前读一次画布，写入后读一次回执；只按真实回执说“已写入”。
3. 回执里的 `revision`、`applied_ops`、`readback_verified` 是唯一完成证据；缺一项就说明未验证。
4. 用户要 2 个资产就只建 2 个节点；不要建“占位分镜”“备用镜头”“之后再用”的节点。
5. 删除节点前先读画布确认节点存在且属于本轮；不要删用户已有内容。
6. 同一目标已经有 `paused`/`running` 的工作流时：先读它，取消或继续；不要同时直接建节点。发现重复节点先查来源，不要默默再建一份。

## 生成与轮询

- 启动生成后，每 8–10 秒查一次任务；不要连续狂刷。
- 连续 3 分钟没有结果：说明“正在生成，可稍后查看”，停止轮询，不要重复提交。
- 只有真实产物 URL / 任务完成回执才算完成；`accepted`、`queued`、`running` 都不是完成。
- 付费任务失败后不要原样重试同一请求；先按错误类型处理，必要时问用户。

## 错误处置

- `GENERATION_TRANSIENT` / 明确的上游繁忙：最多原样重试一次。
- `INVALID_PARAMS`：先修参数，再重试。
- `UNKNOWN`：先只读核查任务与画布，不要假设“没发生”，也不要盲目重提。
- 余额、鉴权、内容安全、模型不支持：停止重试，直接说明卡点和可选路径。

## 回复纪律

- 先给结果，再给必要证据；用中文短句。
- 不复述内部工具名、合同 JSON、证据 ID 或执行过程，除非用户明确要看步骤。
- 没做的事、没验证的事直说，不用“已保存”“已恢复”“下次继续”掩盖。
