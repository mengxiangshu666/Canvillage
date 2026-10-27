# 村长无限画布 · Codex 插件

让 Codex 读取和修改本机运行的村长无限画布：项目、画布、节点、连线、提示词、
模型任务和工作流。

## 安装

在村长无限画布项目根目录执行：

```powershell
codex plugin marketplace add ".\agent_codex_plugin"
codex plugin add village-canvas@village-canvas
```

查看状态：

```powershell
codex plugin list
```

如果把这个目录推到 Git 仓库，也可以直接用仓库地址：

```powershell
codex plugin marketplace add https://github.com/<owner>/<repo> --ref main
codex plugin add village-canvas@village-canvas
```

## 连接

1. 确认本机 8784 正在运行：`curl http://127.0.0.1:8784/healthz`。
2. 插件通过本机 `local` 账户读取画布，不需要额外填 Key。
3. 如果 Codex 把插件复制到缓存目录，插件找不到项目时，二选一：
   - 设置环境变量 `VILLAGE_CANVAS_HOME=<村长无限画布项目根目录>`
   - 写入 `%USERPROFILE%\.village-canvas\home`，内容为项目根目录
4. 可选：设置 `VILLAGE_CANVAS_WORKDIR` 指定 `.village-canvas.json` 所在目录
   （默认是项目根目录）。

## 插件做什么

MCP 服务把 `village-canvas serve` 的 49 个方法暴露成工具，包括：

- 观察：`health`、`project.list`、`canvas.list`、`canvas.context`、`node.inspect`
- 写画布：`canvas.apply`、`node.create-image`、`node.create-text`、
  `node.create-video`、`node.update`、`node.move`、`node.connect`、`node.delete`
- 模型：`model.list`、`model.capabilities`、`model.check`
- 生成：`gen.image`、`gen.video`、`gen.audio`（可能产生费用）
- 任务：`task.list`、`task.wait`、`task.stop`
- 工作流：`workflow.start`、`workflow.status`、`workflow.runs`
- 兜底：`api.get`、`api.post`、`asset.upload`
- 参数查询：`village_canvas_help`

## 使用约定

- 先 `canvas.context` 读 revision，再用 `expected_revision` 写入。
- 批量修改用一条 `canvas.apply` 提交，减少 revision 冲突。
- 删除、停止、恢复需要显式确认。
- 付费媒体生成需要服务端授权；没有授权就停在干跑，不重试硬闯。
- 完成只认服务端回执（`revision`、`applied_ops`、任务终态和真实产物）。

## 目录

```text
.agents/plugins/marketplace.json         # 本地插件市场
plugins/village-canvas/
  .codex-plugin/plugin.json              # 插件清单
  .mcp.json                              # MCP 启动配置
  scripts/village-canvas-mcp.mjs         # MCP stdio 桥
  skills/village-canvas/SKILL.md         # Agent 使用说明
```
