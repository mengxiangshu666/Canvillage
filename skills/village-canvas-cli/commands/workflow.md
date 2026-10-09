# `village-canvas workflow` —— 工作流运行（WorkflowRun）

权威文案以 `village-canvas workflow --help` 为准。多步骤、可恢复、媒体批次的
活走 workflow；单节点小改动走 `node`，不要为改一个标签起 Run。

```bash
village-canvas workflow list                        # 可用工作流定义
village-canvas workflow runs [--limit 20]           # 本画布最近的 Run
village-canvas workflow start --request "把这份脚本排成 30 秒 1v1 打斗短片：…" \
  [--workflow-id custom-canvas-workflow] [--run-mode draft|auto] \
  [--idempotency-key …] [--wait] [--wait-seconds 30]
village-canvas workflow status --run-id <wfr_…>
village-canvas workflow events --run-id <wfr_…> [--after-seq 0] [--limit 200]
village-canvas workflow control --run-id <wfr_…> --command pause|resume|cancel|retry|steer \
  [--step-id …] [--direction "第二镜改低机位"] [--retry-scope failed_items_only] [--item-ids …] --yes?
```

`--request` 收**一句自然语言目标**（自由文本）；CLI 自动补画布快照 revision、
幂等键和来源标记。`--run-mode draft` 是默认（便宜的预览路径）；`auto` 会一路
走到交付，付费媒体阶段仍受服务端授权门管辖。

流程惯例：

1. `start` 返回 `wfr_…` 运行 id 后，用 `status` 轮询阶段，`events` 看逐步事件。
2. `--wait` 让 CLI 自己等一小段（默认 30s），适合短运行；长运行别占着等。
3. 失败恢复用 `control --command retry --retry-scope failed_items_only --item-ids …`
   精确重试；不扩成整步、不重开平行 Run。
4. `steer` 用 `--direction` 一句话改向；改向也是 Run 事件，不是新 Run。
