# `village-canvas task` —— 后台任务

权威文案以 `village-canvas task --help` 为准。

```bash
village-canvas task list [--include-runs]              # 任务列表（含运行）
village-canvas task inspect --task-type <t> --episode 0 [--beat-num N] [--scope …]
village-canvas task wait --task-type <t> --episode 0 [--timeout 300] [--interval 2.0]
village-canvas task result --task-type <t> --job-id <uuid>
village-canvas task stop --task-type <t> --episode 0 --yes   # 无 --yes 只回确认单
```

`task wait` 行为约定：轮询到终态即回；**任务不存在时立即返回**
`{"found": false, "polls": 1, …}`，不会空等满 timeout。拿到 `job_id` 后用
`task result` 取产出；结果未落盘时服务端回 `"job result not yet on disk"`，
稍后再取。
