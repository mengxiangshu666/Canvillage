# `village-canvas api` —— 通用 REST 透传

权威文案以 `village-canvas api --help` 为准。CLI 的专用子命令没覆盖到的操作，
从这里走全部 4xx 个 REST 操作；能走专用子命令时优先专用子命令（它多做对账）。

```bash
village-canvas api get  <相对路径> [--query k=v --query k2=v2]
village-canvas api post <相对路径> --body '{}'        # JSON 字面量或 @文件路径
village-canvas api patch <相对路径> --body @patch.json
village-canvas api put  <相对路径> --body '{…}'
village-canvas api delete <相对路径> --yes            # 无 --yes 只回确认单，不发请求
```

路径规则：

- 只接受**相对 API 路径**，绝对 URL 直接拒收。
- 支持 `{project}` / `{canvas_id}`（或 `{canvas}`）占位符，自动按绑定解析。
- 写 `/api/v1/…` 也能通：前缀会被剥掉，等价于不带前缀。

```bash
# 示例：列画布（占位符）
village-canvas api get /projects/{project}/freezone/canvases
# 示例：读投影状态（安全的 POST，不改数据）
village-canvas api post /projects/{project}/freezone/canvases/{canvas_id}/projections:status --body '{}'
# 示例：发现画布能做什么（92 张能力卡，支持 ?id= 与 ?query=）
village-canvas api get /village-canvas/capabilities
```

返回是服务端原始 JSON；HTTP 非 2xx 时 CLI 报 `HTTP <code>` 并带出服务端
`detail`。CLI 的 base 恒带 `/api/v1`，够不着服务根路径：要查全量端点清单，用
`curl http://127.0.0.1:8784/openapi.json`（不走 CLI）。
