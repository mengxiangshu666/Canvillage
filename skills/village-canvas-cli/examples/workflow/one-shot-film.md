# 配方：一句话起跑一键成片（WorkflowRun）

**前置**：服务健康；已绑定画布；模型中心已配置 agent/text 模型（`model check --kind agent`）。
**会真实创建运行**：draft 模式会跑脚本阶段（消耗配置好的文本模型）；付费媒体
阶段由服务端授权门管辖，未授权时 fail-closed 停住。首次试跑建议在临时空画布上
（见 [temp-canvas-edit-loop.md](./temp-canvas-edit-loop.md) 第 0 步）。

```bash
# 1) 起跑：--request 就是一句自然语言目标（自由文本）
village-canvas workflow start --request "把画布上的脚本排成分镜并出 30 秒样片" \
  --run-mode draft [--wait --wait-seconds 30]
# → 返回 run id（wfr_…）与初始状态

# 2) 盯进度：阶段状态 + 逐步事件
village-canvas workflow status --run-id <wfr_…>
village-canvas workflow events --run-id <wfr_…> --after-seq 0

# 3) 到授权门时：付费媒体需要服务端授权，不要重试硬闯；
#    先看 status 里的阻塞原因与所需授权范围。

# 4) 改向（同一 Run 内一句话改需求）
village-canvas workflow control --run-id <wfr_…> --command steer \
  --direction "第二镜换成低机位" --yes

# 5) 失败恢复：只精确重试失败项，不扩成整步、不重开平行 Run
village-canvas workflow control --run-id <wfr_…> --command retry \
  --retry-scope failed_items_only --item-ids <item_id…> --yes

# 6) 收尾核对：status 全阶段 completed，最终成片回执带 SHA-256/尺寸/时长。
```

纪律：

- `--wait` 只适合短运行（默认 30s）；长运行用 `status` 轮询，别占着进程。
- 同一目标重试复用 `--idempotency-key`，服务端不会重复起 Run。
- 断点、暂停、恢复都是 Run 事件；恢复后从服务端持久化事实继续，不要重建。
