# `village-canvas canvas` —— 画布、绑定与快照

权威文案以 `village-canvas canvas --help` 与各子命令 `--help` 为准。

## 目录绑定（先做一次，后续命令免传 id）

```bash
# 绑定项目+画布（会先向服务端确认画布存在，fail-closed 防错字）
village-canvas --project <项目ID> canvas use <画布ID>
# 只换画布（项目沿用既有绑定或环境变量）
village-canvas canvas use <画布ID>
# 只绑项目；项目切换时旧画布绑定一并清掉
village-canvas --project <项目ID> canvas use
# 查看当前绑定
village-canvas canvas use
# 解绑：默认全清；--canvas-only 只清画布、--project-only 只清项目
village-canvas canvas unuse [--canvas-only|--project-only]
```

绑定落在当前目录 `.village-canvas.json`；解析顺序是
`--project/--canvas` > 环境变量 `VILLAGE_CANVAS_PROJECT_ID`/`VILLAGE_CANVAS_CANVAS_ID`
> 绑定文件。

## 读面（只读，放心批量跑）

```bash
village-canvas project list                 # 项目列表
village-canvas canvas list                  # 项目下的画布列表
village-canvas canvas inspect               # 单张画布全文
village-canvas canvas context [--full]      # 摘要视图；--full 关掉瘦身与 800 字截断
village-canvas canvas viewport              # 视口、revision、节点/边计数
village-canvas canvas history               # 历史快照列表
village-canvas node list [--full]           # 节点列表
```

`--full` 会返回未裁剪的节点 data（含 `imageUrl` 等产出字段）。默认输出是人读缩进
JSON；给程序/agent 读一律加全局 `--json`（单行 `{"ok":true,"data":…}` 信封）。

## 写面（先 dry-run，确认后真跑）

```bash
# 从预设建画布（scope: beat/blank/…；blank 适合临时试验）
village-canvas canvas create --scope blank [--canvas-id demo-canvas]

# 批量命令：从 JSON 文件提交命令数组，逐条带 command_id
village-canvas canvas apply --file commands.json [--dry-run] [--expected-revision N]

# 历史恢复 / 删除画布：破坏性，必须 --yes；无 --yes 只回确认单，不发请求
village-canvas canvas restore --history-id <id> [--base-revision N] --yes
village-canvas canvas delete --yes
```

`canvas create` 建出的临时画布用完 `canvas delete --yes` 删除，不要把试验垃圾
留在用户画布里。

## 通用写参数（所有会改画布的子命令共享）

| 参数 | 作用 |
|---|---|
| `--dry-run` | 只回命令信封不提交 |
| `--command-id` | 幂等键；不传自动生成 `cli-command:<uuid>` |
| `--source-turn-id` | 标记来源回合，默认 `village-canvas-cli` |
| `--expected-revision` | 乐观锁；服务端 revision 不符即拒 |
