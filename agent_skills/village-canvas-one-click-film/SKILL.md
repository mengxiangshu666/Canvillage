---
name: village-canvas-one-click-film
description: Use for 一键成片、自动驾驶、整集制作、从当前进度跑到成片。必须通过 durable production control 驱动，不在聊天回合里手搓整条流水线。
activation:
  workflow: one-click-film
  agents:
    - village-canvas-storyboard
    - village-canvas-continuity
    - village-canvas-delivery-qc
    - village-canvas-failure-rescue
  flags:
    - paid_media_requires_task_authorization
    - durable_production_run_only
  fence:
    - 不得把 queued / running / paused / waiting_confirmation 写成完成
    - 不得手工串联底层图片或视频工具冒充一键成片
    - 不得丢弃已成功阶段另开新 production run
    - 不得在没有本轮 task_authorization 时启动或恢复付费媒体阶段
---

> **Capability Contract v1**：indexed 模式下将 `village_canvas_pipeline_status`、`village_canvas_get_production_control`、`village_canvas_start_production_run`、`village_canvas_command_production_run`、`village_canvas_get_final_video` 分别映射到 `pipeline.status`、`production.control.get`、`production.run.start`、`production.run.command`、`production.final_video`。先 search/describe，再 invoke；不得猜造能力名或重复创建 run。

# 一键成片导演

## 基础设定

**目标**：从项目当前真实进度推进到可交付视频，同时保留阶段状态、任务 ID、失败恢复点和本轮授权记录。

**运行权威**：`production/control` 的 durable run 是唯一执行权威。聊天文字、旧消息、节点标题和“看起来已经有图”都不能代替运行状态与正式产物。

**执行原则**：先读状态，后计划；按当前 V2 `task_authorization` 推进；启动后不在一个聊天回合里长轮询；只有正式成片接口返回媒体才可宣布完成。

## 工具速查

- `village_canvas_pipeline_status`：读取主流水线事实和前置缺口。
- `village_canvas_get_production_control`：读取 active/latest run、阶段、blocker、授权需求和任务事实。
- `village_canvas_start_production_run`：创建或复用 durable run。
- `village_canvas_command_production_run`：pause / resume / retry / cancel 等受控命令；按工具真实 schema 调用。
- `village_canvas_get_final_video`：读取正式交付媒体。
- `village_canvas_list_tasks`：核对异步任务，不替代 production run 状态。
- `freezone_get_canvas_snapshot` / `freezone_emit_canvas_command`：仅当用户要求同步调整画布结构时使用；不替代 production run。
- `freezone_propose_generation`：画布单节点媒体提案；整集成片仍走 production control。

禁止把多个底层图片/视频工具手工串成“伪一键成片”。

## 启动前输入合同

每次启动前必须明确：

1. `project_id` 与目标集数/范围；
2. 当前 pipeline 与 production control 快照；
3. 已存在的正式角色、场景、分镜、配音和视频资产；
4. 缺失前置与阻断项；
5. 运行模式，默认 `best`；
6. 将触发的图片、视频、语音等付费媒体范围；
7. 当前 V2 `task_authorization` 的运行模式、媒体权限与最大启动次数。

信息不足时只问阻断执行的关键问题，不重复询问工具已经能读到的事实。

## 固定计划格式

```text
生产目标：<集数/交付规格>
当前状态：<run id / stage / status>
已完成：<真实阶段与产物>
缺失前置：<无则写无>
执行计划：
1. <免费检查或阶段>
2. <媒体阶段，注明是否付费>
3. <合成与QC>
暂停点：<授权禁止、预算耗尽、前置缺失或失败恢复点>
完成证据：<final video + run status + task/artifact ids>
```

## 本轮任务授权

- `task_authorization.run_mode=auto`、`allow_paid_media=true` 且 `max_paid_starts>0`：用户发送本轮任务即完成任务级授权。启动时传 `auto_generate_paid_media=true` 并原样携带 `task_authorization`；resume/retry 同样携带，不再逐阶段询问。
- `run_mode=draft`、`allow_paid_media=false` 或 `max_paid_starts=0`：只启动/复用不触发媒体的安全阶段，或停在完整结构与计划；不得启动、恢复或重试媒体任务。
- 授权只属于当前回合，不复用旧回合，不扩大数量，不超过 `max_paid_starts`。
- 费用保护、模型配置、失败诊断类任务即使选择自动模式，也以当前合同中的 `allow_paid_media=false` 为准。

## 分阶段执行

### 1. 体检

调用 `village_canvas_pipeline_status` 和 `village_canvas_get_production_control`。若已有 active run，优先复用，不创建重复运行。输出真实缺口和预计阶段。

### 2. 免费推进

无媒体授权时用 `village_canvas_start_production_run(mode="best", auto_generate_paid_media=false)` 只推进安全阶段。记录返回的 run id、当前阶段和授权阻断点。

### 3. 授权推进

本轮自动授权有效时，使用真实 command schema 启动或恢复同一个 run，并原样携带 `task_authorization`。不得丢弃已成功阶段另开新 run。响应 queued/running 只表示已受理，不能说“已经生成”。

### 4. 失败恢复

先重读 production control 与 task。区分：

- 前置缺失：补事实/资产后 resume；
- 可重试外部错误：在不重复扣费的前提下 retry；
- 内容安全或参数错误：修输入后从最低失败阶段恢复；
- 不可恢复：保留成功资产与 lineage，报告 blocker，不伪造成功。

### 5. 交付

只有 `village_canvas_get_final_video` 返回正式媒体，且 production run 到达完成态，才宣布成片完成。报告 URL/资产标识、规格、run id 和遗留警告。

## 不可伪造与边界

- 不把 queued、running、paused、waiting_confirmation 写成完成。
- 不根据聊天等待或猜测进度；下一轮重读状态。
- 不删除用户素材，不覆盖原始剧本和生成结果。
- 不让 Agent 服务端直接重写整张画布。
- 画布结构建议另走 `freezone_emit_canvas_command`；生产运行不伪装成 canvas patch。
- 用户取消时说明已完成和可能已计费阶段，不承诺无法撤回的外部费用。

## 完成验收

- [ ] run id、目标范围、模式清楚；
- [ ] 本轮 task_authorization 与实际命令、启动次数一致；
- [ ] 每个阶段状态来自真实工具；
- [ ] 失败时有错误原文和恢复点；
- [ ] 正式成片接口返回媒体；
- [ ] 最终回复区分已完成、警告和未完成项。
