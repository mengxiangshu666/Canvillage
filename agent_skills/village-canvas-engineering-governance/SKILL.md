---
name: village-canvas-engineering-governance
description: Use when changing Village Infinite Canvas Agent, canvas, workflow, model, memory, API, or delivery code and the change needs a small diff with contract and runtime evidence.
version: 1.0.0
metadata:
  origin: source-distillation
  sources:
    - repository: https://github.com/DietrichGebert/ponytail
      commit: 974d940a1c5344210874150b98ff0d2c861fab6a
      license: MIT
    - repository: https://github.com/affaan-m/ECC
      commit: e04ea0b9cc8248686edf5ac751cadff550e162b8
      license: MIT
---

# 村长工程治理

这是一套项目专用的工程方法，不是第二个 Agent、工作流、记忆库或安装器。它把
Ponytail 的最小必要原则和 ECC 的合同、回归、验证、上下文与记忆治理，落到村长
无限画布现有的 Village Harness、CanvasCommandGateway、WorkflowRun、Verifier、MCP
和模型中心边界中。

对应的上游方法名为 `contract-first`、`ai-regression-testing`、`verification-loop`、
`context-budget` 和 `unified-memory`；这里仅保留适用于村长边界的规则。

## 何时启用

- 修改 `src/novelvideo/chat/`、`workflow_runtime/`、`freezone/`、模型路由、MCP、前端 Agent 或相关 API；
- 新增跨层字段、命令、事件、任务状态、产物引用或模型能力；
- 修复一个已经发生的 Agent、画布、工作流、媒体或交付问题；
- 评估是否需要新增 Skill、依赖、抽象、缓存、服务或控制面。

## 唯一执行闭环

对非平凡改动按以下顺序推进，阶段名称也是交接和审查的最小记录：

1. **Observe**：先读 `AGENTS.md`、`PROJECT_LAYOUT.md`、`STATUS.md`、`MODIFICATIONS.md`，再运行 `scripts/ai_project_snapshot.py`。沿真实调用链确认当前源码、构建、运行版和数据边界；当前运行行为与 HTTP/OpenAPI 优先于旧文档和旧数字。
2. **Plan**：写出用户结果、模块 owner、唯一合同、受影响消费者、风险、回滚点和最小验证命令。若只是单模块原子改动，使用现有类型或测试即可，不新造合同框架。
3. **Dry Run**：删除、覆盖、迁移、付费媒体、模型配置和正式画布写入先走现有 dry-run、确认门、备份或 mock。预览状态不等于已写入，草稿不等于已生成。
4. **Act**：优先调用现有服务和入口：画布结构走 `CanvasCommandGateway`/`village_canvas_dispatch_action`，长任务走 `WorkflowRun`，能力走 registry/MCP，模型走 direct registry。保持 `project_id`、`canvas_id`、`revision`、幂等 `command_id`、`run_id` 和当前 turn 绑定。
5. **Wait**：异步动作等待真实终态、checkpoint、SSE 或任务回执；保留已成功产物和原始错误，按恢复合同做一次有界重试，不用盲目循环抵消失败。
6. **Verify**：按风险组合静态检查、定向回归、OpenAPI/序列化合同、运行版对账和真实回执。完成证据必须来自 `server_applied`、`revision`、`applied_ops`、`created_node_ids`、`task_key`、`job_id`、WorkflowRun 状态、verifier 事件或正式媒体结果。
7. **Writeback**：只有证据齐全后才更新 `STATUS.md`/`MODIFICATIONS.md`，记录实际改动、验证、未完成项和回滚路径。短期取舍用 `ponytail:` 注释写明上限与复查触发条件，必要时进入现有 `docs/TECHNICAL_DEBT.md`。

## 最小必要阶梯

每次新增前依次问：

1. 需求是否真实存在且属于本轮交付？否则跳过。
2. 当前项目是否已有同职责 helper、registry、服务、合同或测试？有就复用。
3. 标准库是否已覆盖？优先标准库。
4. 平台原生能力是否已覆盖？优先原生能力。
5. 已安装依赖是否已覆盖？不为几行逻辑引入新依赖。
6. 仍然需要时，写满足合同的最小实现。

理解问题和真实调用链应完整保留。最小 diff 不得削弱输入校验、错误恢复、数据一致性、
权限/确认门、可访问性、revision 校验、receipt、checkpoint、verifier 或可观测性。

复杂度审查可使用这些标签：`delete`、`stdlib`、`native`、`yagni`、`shrink`。标签只
描述可删减的复杂度，不把正确性、可靠性和生产保护当作冗余。抽象至少应满足以下一项：
第二个真实消费者、跨层合同、独立生命周期或已测量的性能/可靠性收益。

## 合同优先

- HTTP 以 OpenAPI 为权威；独立事件/回执以现有 JSON Schema 或结构化合同为权威；同一字段不在文档、mock、前端类型和后端 serializer 中各维护一份。
- 合同先于实现：先确认消费者实际需要的字段、空值、枚举、错误和版本兼容，再改 provider/consumer，并验证成功、空集合、错误和 mock/生产分支。
- Agent 专家交接使用 `agent_specialist_result.v1` 和 `agent_artifact.v1`；稳定 ID/哈希、source refs、producer/consumer 和真实回执优先于口头结论、长提示词或文件路径。
- 模型能力使用 direct registry 与已有 `model-capabilities.schema.json`；URL、Key、Cookie、Bearer Token 和完整 provider payload 不进入事件、黑板、checkpoint 或记忆。
- 新增命令、事件、任务或能力前，先寻找现有 `village_canvas_*`、`WorkflowRun`、`CanvasCommandGateway` 和 verifier 入口，避免平行控制面。

## AI 回归规则

每个已发现的 bug 至少留下一个能复现原失败的最小测试，测试行为而不是实现细节：

- 同时覆盖生产/mock、成功/错误、空值/边界和功能开关路径；前后端响应字段保持一致；
- 画布写入断言真实 `revision`、命令回执和回读状态；长任务断言 `run_id`、终态和 checkpoint/verifier；
- 失败路径断言成功产物保留、错误可见、回滚有效且重试有上限；
- 质量门禁以项目现有 manifest/runner 为准，禁止用空测试通过选项掩盖未发现测试；
- 修复前先跑能失败的回归，修复后再跑同一回归和相邻合同测试。

## 上下文与记忆

- 复用 `context_budget.py`、Village Harness 的有界回放和 `execution_checkpoint.py`；大文档按需引用，阶段边界再压缩，不把完整数据库历史塞进每轮上下文。
- 复用 `memory_compiler.py`、`memory_hooks.py`、`memory_index.py` 和现有成长记忆流程；不安装或维护第二套 memory vault。
- 记忆只保存非敏感、可追溯、可复核的摘要和稳定引用；原始 prompt、凭据、私密媒体和未经验证的模型推断不进入 durable memory。
- 历史记忆、生成产物和助手文字都是待核对上下文，提升为规则前先回到当前源码、运行态、合同或测试证据。

## 完成门

交付说明按四类证据分开写：

| 类别 | 最小证据 |
| --- | --- |
| 源码 | 影响闭包、owner、diff 和静态检查 |
| 合同/测试 | 目标 schema/OpenAPI、定向回归和测试结果 |
| 运行版 | health、Build ID、OpenAPI、目标 API 或受约束 Agent smoke |
| 副作用 | dry-run/确认、真实回执、任务终态、回滚材料 |

自然语言“已完成”、UI 动画、关键词命中、queued/running 状态、占位 URL 和“代码看起来正确”
都不构成完成证据。没有某类证据时，明确标记为未验证，并留下最低成本的下一检查。

## 明确不引入

本 Skill 不复制 Ponytail 的根级 `AGENTS.md`、生命周期 hooks、全局模式控制或 benchmark，
也不复制 ECC 的大规模 Agent/Skill/Command 控制面、Claude 安装器、第二套记忆库、通用 hooks
或项目状态数据库。外部仓库只作为方法来源，运行时继续使用村长现有边界。
