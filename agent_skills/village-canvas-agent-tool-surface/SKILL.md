---
name: village-canvas-agent-tool-surface
description: Use when a canvas tool return must be verified, not faked.
---

# Agent 工具面与能力回执

## 任务定义

把“某个工具返回了什么”这类取证请求，变成可核对的真实回执，或者一份说明真实缺口的报告。
两种错误都不能犯：把没发生的调用写成回执，或把“本会话没挂载该工具”说成“工具坏了”。

## 铁律

1. **当前会话的工具表是唯一权威。** 系统提示的函数清单里没有该工具，本轮就调不到它，
   无论插件是否安装、历史会话是否调用成功过。不靠工具名相似、记忆或日志推断可用性。
2. **没发起调用就不得报告条数/状态。** 明确写“未发起调用 ≠ 返回 0 条”。用 `terminal`、
   通用 HTTP 或直接跑插件函数去模拟返回值属于伪造回执；`TOOLS.md` 使用纪律也禁止旁路项目 API。
3. **区分“会话工具面差异”和“产品缺陷”。** 缺工具时只描述本会话事实与恢复路径，
   不要写成“该工具不可用/已损坏”这类长期结论。
4. **回报结论先行**：一句结论 + 已核实证据 + 两条可选修复，不要长篇解释。

## 取证顺序

1. 读当前会话工具表，确认目标工具在不在。不在就停止，不要用 `terminal` 模拟。
2. 确认工具在原生 Agent 里存在（区分安装问题 vs 注册问题）：
   - `src/novelvideo/agent_tools/village_canvas.py`：`native_tool_entries()`、
     `INDEXED_CANVAS_AGENT_TOOL_NAMES`、`CAPABILITY_BROKER_TOOL_NAME`；
   - `src/novelvideo/agent_tools/native_registry.py`：`build_native_registry()` 的
     `tool_names`；
   - `agent_skills/`：运行时按需加载的方法与 SOP，不冒充工具回执。
3. 确认会话形态：`项目资产/runtime/agent.log` 里
   `village agent turn` / tool lifecycle 记录中的 `tools=N`，`N` 是该会话注册的工具数。
   画布会话通常 `tools=3`，与 indexed 必需集（`village_canvas_apply_commands`、
   `village_canvas_capability`、`village_canvas_dispatch_action`）一致；
   通用外部 Agent/ACP 会话带另一套通用工具，不含画布工具。
4. 工具确实在表里时，才发起调用，并只按真实回执报告（条数、`ok`、`revision`、`task` 等原字段）。

## 常见误判

- 日志里出现过成功的 `village_canvas_*` 调用 → 那是别的会话，不代表本会话可用。
- 插件已安装 ≠ 当前会话已挂载；indexed 与 full 是两套工具暴露模式。
- 上一轮 checkpoint 里的 `terminal: 执行失败` → 先分清是工具面缺失还是命令本身错，
  不要把两者归成一条结论。

## 回报模板

```text
结论：本轮拿不到 <工具> 的返回（<一句话原因>）。
已核实：<工具在产品中存在 + 证据文件>；<当前会话无该工具 + 证据>。
未发生：未发起调用，因此“返回 0 条”不成立。
修复：1) 在画布会话内重发该请求；2) 给当前会话挂载 village-canvas-indexed toolset。
```

## 验收

- [ ] 结论第一句就说明是否拿到真实回执；
- [ ] 没有把未调用的工具写成有返回值；
- [ ] 证据落到具体文件/日志字段，不是“我记得”；
- [ ] 给了可执行的恢复路径；
- [ ] 没有对本轮无写入的事实做任何画布/媒体完成的暗示。
