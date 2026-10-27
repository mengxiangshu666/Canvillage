# 小树 Agent 运行时（随产品分发）

这个目录是小树 Agent 的运行源代码，已经随产品打包，不再依赖外部 TapCanvas 检出目录。

- 启动入口：`dist/cli/index.js serve --host 127.0.0.1 --port 8790`
- 隔离补丁：`harness/tapcanvas.patch.yml`（启动前校验 shell/文件/网络/子代理工具全部禁用）
- 依赖：由 `scripts/install_agent_sidecar.py` 安装到 `runtime/agent_sidecar`
- 许可：MIT，原始版权与来源见 `LICENSE` 与 `UPSTREAM_PROVENANCE.json`

产品侧只通过 `src/novelvideo/chat/tapcanvas_sidecar.py` 调用它；它只能通过每回合的
MCP 门调用本产品原生工具，画布、任务和工作流的写入仍由本产品执行。
