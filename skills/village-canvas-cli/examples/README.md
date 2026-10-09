# 通用前置条件与输出约定

所有配方共享这些前提；单条配方的额外前置写在自己头部。

## 前置

1. 服务在跑：`curl http://127.0.0.1:8784/healthz` 返回 `{"status":"ok"}`，或
   `village-canvas health`。
2. CLI 可调用（任选其一）：
   - 开发机：`.venv\Scripts\python.exe src\novelvideo\canvas_cli.py`
   - 便携 runtime：`runtime\python\python.exe src\novelvideo\canvas_cli.py`
   - 打包环境里叫 `village-canvas.bat`。
3. 工作目录已绑定：`village-canvas --project <项目ID> canvas use <画布ID>`；
   之后所有命令免传 `--project/--canvas`。

## 环境变量（可选，优先级低于命令行参数、高于绑定文件）

| 变量 | 作用 |
|---|---|
| `VILLAGE_CANVAS_API_BASE_URL` / `VILLAGE_CANVAS_API_URL` | 覆盖服务地址 |
| `VILLAGE_CANVAS_PROJECT_ID` / `VILLAGE_CANVAS_CANVAS_ID` | 兜底项目/画布 |
| `VILLAGE_CANVAS_AGENT_TOKEN` | 服务端开启鉴权时的会话令牌 |

## 输出约定

- 给程序/agent 读：一律加全局 `--json`，输出单行
  `{"ok": true, "data": …}`；失败是 `{"ok": false, "error": "…"}`，退出码 1。
- 人看：默认输出多行缩进 JSON + 少量摘要行。
- 所有破坏性命令（`delete` / `restore` / `stop` / `unuse` 之外）无 `--yes`
  时只回 `confirmation_required` 确认单，**不发请求**——见到它不代表执行了。

## Windows shell 提醒

Git Bash 会把以 `/` 开头的参数当 POSIX 路径改写（`/projects/…` 会被换成
`C:/Program Files/Git/projects/…`），导致 404。在 Git Bash 里跑带路径参数的
命令前设 `export MSYS_NO_PATHCONV=1`，或用 PowerShell/cmd。
