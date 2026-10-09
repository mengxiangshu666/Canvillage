# `serve` / `mcp` —— 程序化通道

权威文案以 `village-canvas --help` 为准。适合不想解析 CLI 文本输出的程序。

## `village-canvas serve`（stdio JSON-RPC，便携 runtime 可跑）

每行一个 JSON-RPC 请求，逐行回 JSON；进程常驻，适合管道集成。

```json
{"jsonrpc":"2.0","id":1,"method":"canvas.context","params":{"project":"P","canvas":"C"}}
```

- `tools/list` 列出全部方法；方法名即 CLI 子命令的点号形式
  （`canvas.apply`、`node.create-image`、`workflow.start`、`api.get`、`task.wait`…）。
- `params` 就是该子命令的参数字典（snake_case），如
  `{"method":"node.move","params":{"node":"n1","x":10,"y":20}}`。
- `canvas.apply` 的 params 里直接给 `commands` 数组。

## `village-canvas mcp`（MCP 协议）

转发到 `chat/village_canvas_mcp.py`，需要完整 venv 里的 `mcp` 包——**便携
`runtime\python` 跑不起来**。MCP 客户端用它；脚本类集成用 `serve`。
