# 常见报错与处理

全部来自真机实际撞过的坑，按报错原文检索。

| 报错 / 现象 | 原因 | 处理 |
|---|---|---|
| `需要 --project、VILLAGE_CANVAS_PROJECT_ID，或先执行 canvas use 绑定` | 上下文三路全空 | `canvas use <画布ID> --project <项目ID>` 绑定一次 |
| `只接受相对 API 路径，不接受绝对 URL` | 给 `api get` 传了完整 URL | 只传 `/projects/…` 形态的相对路径 |
| Git Bash 下 404，curl 同路径却 200 | Git Bash 把 `/projects/…` 当 POSIX 路径改写 | `export MSYS_NO_PATHCONV=1` 或改用 PowerShell |
| `confirmation_required: true` | 破坏性命令没带 `--yes`，**未执行** | 确认后补 `--yes` 重发 |
| 「视频运镜模板不在当前画布目录中」 | `--camera-movement` 传了 JSON | 它收纯字符串模板 id，如 `follow_tracking` |
| 「图生图节点必须绑定至少一张参考图」/「文生图节点不能携带参考图」 | `--mode` 与参考图矛盾 | 图生图先绑参考图；文生图去掉 `--reference` |
| 「文生图/视频模型不在当前画布目录」 | 模型 id 是编的 | 用 `model capabilities` 目录里的真实 id |
| `task wait` 秒回 `found: false` | 任务不存在（不空等） | 确认 `--task-type/--episode`；先 `task list` |
| `job result not yet on disk` | 结果还没落盘 | 稍后重取 `task result` |
| HTTP 422 且 detail 提 revision | `--expected-revision` 与服务端不符 | 重读 `canvas viewport` 拿最新 revision 再提交 |
| 管道截断时报 `OSError: [Errno 22] Invalid argument` | Windows 上 `… \| head` 提前关管道 | 输出重定向到文件再翻，或加 `--json` 取单行 |
