# `village-canvas project` —— 项目

权威文案以 `village-canvas project --help` 为准。

```bash
village-canvas project list                # 项目列表（含 owner/role/status）
village-canvas project inspect             # 单项目详情（需 --project 或绑定）
```

项目的 id 是 UUID 形态（如 `01M3HMPWA7PXVG27E199GZPX72`），画布 id 是短横线名
（如 `user_local_17cvc3s`、`blank_6184d684`）。拿到项目 id 后用
`canvas use <画布ID> --project <项目ID>` 绑定工作目录，详见
[canvas.md](./canvas.md)。

不要对项目级资源使用 `api delete` 之外的方式做删除；项目删除/purge 是永久性的，
CLI 不提供项目删除入口。
