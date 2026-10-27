---
name: village-canvas
description: >-
  操作本机的村长无限画布。凡是读画布、建节点、改提示词、连线、应用运镜/特效/风格、
  跑图片视频任务或工作流，都用这个插件的 village_canvas 工具。开始动手前先读画布。
---

# 村长无限画布

业务真相是本机 REST API（默认 `http://127.0.0.1:8784/api/v1`）。MCP 工具只是
`village-canvas` CLI 的薄桥。完成与否只认服务端回执：`command_id`、`revision`、
`applied_ops`、任务状态和真实媒体结果；聊天文字、计划或前端动画都不算完成。

## 先绑定，再干活

1. `health` 确认本机服务在线。
2. `project.list` 找项目；`canvas.list` 找画布。
3. `canvas.use` 绑定项目/画布（写入当前工作目录的 `.village-canvas.json`）。
4. `canvas.context` 读 revision、节点、连线和视口。之后每次写入都用这次读到的
   `revision` 作为 `expected_revision`。

## 固定闭环

```text
Observe -> Plan -> Dry Run -> Act -> Wait -> Verify
```

1. 先观察：`canvas.context`、`node.inspect`、`task.list`、`workflow.status`。
2. 结构修改：`canvas.apply` 批量提交 `commands`，或 `node.create-image` /
   `node.create-text` / `node.create-video` / `node.update` / `node.connect` /
   `node.move` / `node.delete`。
3. 复杂或批量操作先 `canvas.apply` 带 `dry_run=true` 看载荷，再真实执行。
4. 删除、恢复、停止必须显式确认；不要自动重试付费生成。
5. 写入后核对返回的 `revision` 和 `applied_ops`；不一致先 `canvas.context` 对账。
6. 媒体任务用 `task.wait` / `workflow.status` 等终态，拿到真实任务回执和产物。

## 工具分组

| 需要 | 工具 |
|---|---|
| 连通与绑定 | `health`、`project.list`、`project.inspect`、`canvas.list`、`canvas.use`、`canvas.context` |
| 读画布 | `canvas.inspect`、`node.list`、`node.inspect`、`canvas.history` |
| 写画布 | `canvas.apply`、`node.create-image`、`node.create-text`、`node.create-video`、`node.update`、`node.move`、`node.connect`、`node.delete`、`node.duplicate` |
| 相机与运镜 | `node.camera`、`model.capabilities` |
| 模型与参数 | `model.list`、`model.capabilities`、`model.check`、`model.config` |
| 生成 | `gen.image`、`gen.video`、`gen.audio`（可能收费，先确认授权） |
| 任务 | `task.list`、`task.inspect`、`task.wait`、`task.stop`、`task.result` |
| 工作流 | `workflow.list`、`workflow.start`、`workflow.status`、`workflow.runs`、`workflow.events`、`workflow.control` |
| 兜底 | `api.get`、`api.post`、`api.patch`、`api.delete`、`asset.upload` |

参数不确定时调用 `village_canvas_help`，例如：

```json
{"tool": "canvas.apply"}
```

它会返回对应 CLI 子命令的完整参数说明。

## 画布写入的口径

- 一次请求尽量用一条 `canvas.apply` 提交多条命令，减少 revision 冲突。
- `commands` 里每条命令必须可独立理解；不要伪造服务端未注册的命令名。
- 写提示词不要覆盖用户原意；只改需要改的那一段。
- 参考图和角色绑定用真实节点 ID / asset ID，不要按文件名或画布位置猜。
- 生成媒体前先查 `model.capabilities`；不要把模型不支持的参数写进节点。
- 付费生成需要授权；没有授权就停在计划/干跑，不重试硬闯。

## 完成判据

- 结构修改：`revision` 前进且 `applied_ops` 覆盖本次写入。
- 任务：终态 `completed`，并有真实产物 URL / 任务回执。
- 工作流：终态回执与步骤状态一致；失败只按 `failed_items_only` 精确重试。
- 没有回执时只报告「已提交、待确认」，不要报告完成。
