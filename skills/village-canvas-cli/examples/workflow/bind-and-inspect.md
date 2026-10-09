# 配方：绑定工作目录，摸清画布现状

**前置**：服务健康（见 [../README.md](../README.md)）；已知项目 id。
**全程只读，不改任何数据。**

```bash
# 1) 绑定（写一次，之后免传 id）；use 会先向服务端确认画布存在
village-canvas --project <项目ID> canvas use <画布ID>

# 2) 看画布摘要与视口（revision、节点数、连线数）
village-canvas canvas context
village-canvas canvas viewport

# 3) 看完整节点数据（imageUrl 等产出字段回来，无 800 字截断）
village-canvas canvas context --full
village-canvas node list --full
village-canvas node inspect --node <节点id>

# 4) 看历史快照与在跑的东西
village-canvas canvas history
village-canvas task list
village-canvas workflow runs

# 5) 程序化读取（单行 JSON 信封）
village-canvas --json canvas viewport
```

拿到 `revision` 后，写命令可带 `--expected-revision <revision>` 做乐观锁；
不带时服务端按最新 revision 受理并在回执里对账。
