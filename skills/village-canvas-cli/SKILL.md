---
name: village-canvas-cli
description: >-
  村长无限画布便携 CLI（village-canvas）：在命令行里完整操作本机运行的画布。
  凡是与画布、项目、节点、模型、任务、工作流相关的操作，一律通过 village-canvas CLI
  完成，不要自己拼 HTTP 请求绕到别处。本 skill 是文档地图；子命令与选项的权威文案是
  `village-canvas --help` 与 `village-canvas <子命令> --help`，文档与实际输出不一致时，
  以 CLI 实际输出为准并修订本 skill。
---

# 村长无限画布 CLI（`village-canvas`）

业务真相是 REST API（默认 `http://127.0.0.1:8784/api/v1`），CLI 只是薄客户端。
完成与否只认服务端回执：`command_id`、`revision`、`applied_ops`、任务状态、
WorkflowRun 状态和真实媒体结果。自然语言回复、计划或前端动画都不是完成证据。

**先绑定，再干活。** 在画布所在的工作目录执行一次
`village-canvas --project <项目ID> canvas use <画布ID>`，绑定写进当前目录的
`.village-canvas.json`，之后所有子命令免传 `--project/--canvas`。查看绑定：
`canvas use`；解除：`canvas unuse`（`--canvas-only` 只解画布）。`use` 会先向
服务端确认画布真实存在再写盘。

## 固定闭环

```text
Observe -> Plan -> Dry Run -> Act -> Wait -> Verify
```

1. 先读状态：`canvas context`（或 `canvas viewport`）拿 revision、节点、连线；
   `task list` / `workflow runs` 拿在跑的东西。
2. 结构修改走 `node ...` / `canvas apply`，自动带 `command_id` 与
   `expected_revision`；先 `--dry-run` 看载荷，再真实执行；删除、恢复、停止
   必须显式 `--yes`。
3. 写入后回执会与权威画布对账 revision，`verified: true` 才能报告完成。
4. 多步骤、可恢复、媒体批次走 `workflow`；失败只按服务端 `failed_items_only`
   精确重试，不创建平行运行。
5. 媒体参数只信 `model capabilities` 返回的目录；不要给节点写模型不支持的参数。

## 文档地图

| 主题 | 文件 |
|---|---|
| 通用前置条件、环境变量与输出约定 | [examples/README.md](./examples/README.md) |
| project（项目） | [commands/project.md](./commands/project.md) |
| canvas（画布、绑定与快照） | [commands/canvas.md](./commands/canvas.md) |
| node（节点、连线与相机） | [commands/node.md](./commands/node.md) |
| model（模型目录） | [commands/model.md](./commands/model.md) |
| workflow（工作流运行） | [commands/workflow.md](./commands/workflow.md) |
| task（后台任务） | [commands/task.md](./commands/task.md) |
| gen（直接触发生成） | [commands/gen.md](./commands/gen.md) |
| api（通用 REST 透传） | [commands/api.md](./commands/api.md) |
| asset（素材上传） | [commands/asset.md](./commands/asset.md) |
| serve / mcp（程序化通道） | [commands/serve-and-mcp.md](./commands/serve-and-mcp.md) |
| 场景配方（可复制命令集） | [examples/workflow/](./examples/workflow/) |
| 回执与读数 schema | [schemas/](./schemas/) |

## 硬边界

- API Key 只由服务端模型中心读取；CLI 不接收、不打印、不落盘 Key。
- 付费媒体生成需要服务端授权；没有授权时请求 fail-closed 停住，不要重试硬闯。
- 不要直接写 SQLite、不要拼供应商请求、不要绕过 CLI 直改前端状态。
