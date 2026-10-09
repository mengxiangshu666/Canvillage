# 文件 1：LibTV 完整使用手册（**AI 看的**）

> 受众：AI 助手（不是人）
> 目的：让任何 AI 都能用 libtv CLI 完成 LibTV 画布全流程
> 深度等级：⭐⭐⭐⭐⭐（极其深入，含底层机制）

---

## 第 1 章：LibTV 哲学与架构

### 1.1 LibTV 是什么

**LibTV = 兄弟的"小社会"哲学在 AIGC 领域的实现**。

- **画布（project）** = 创作空间
- **项目（workspace）** = 装画布的容器
- **节点（node）** = 画布上的基本元素
- **边（edge）** = 节点之间的连接
- **组（group）** = 普通分组（不是节点类型，是组织方式）

**核心思维**：每个元素都有**独特价值**——这是兄弟的"小社会"哲学。

### 1.2 6 大节点类型（**画布添加节点**）

| UI 名称 | CLI 类型 | 用途 |
|--------|---------|------|
| **三 文体** | `text` | 剧本/对话生成 |
| **图片** | `image` | 14 个模型 |
| **视频** | `video` | 25+ 个模型 |
| **视频合成** | `video-combine` | 多段拼接 |
| **导演出图** | `storyboard` | 脚本→分镜图组 |
| **音频** | `audio` | 5 个模型 |
| **脚本** | `script` | 文本结构化（带 rows）|
| **素材库** | `asset` | 人物/场景资产库（NEW）|

### 1.3 节点类型 vs 模型（**AI 看的关键**）

```
节点类型 (--type) → 模型集合 (--search)
├─ text → aurora-3-prime / cvlm-5.5 / aurora-3-lite / qwen-3-vl-flash
├─ image → 14 个模型（见 1.4）
├─ video → 25+ 个模型（见 1.5）
├─ audio → 5 个模型（见 1.6）
├─ storyboard → 文本模型 + 图片模型（混合）
├─ script → 文本模型 + 图片模型（混合）
└─ custom → 不支持
```

---

## 第 2 章：CLI 命令完整参考

### 2.1 登录与账户

```bash
# 浏览器登录
libtv login web

# 手机验证码登录
libtv login phone

# 退出登录
libtv logout

# 账户信息
libtv account info
libtv account list
libtv account use <account_id>
```

### 2.2 项目（workspace = 工作区）

```bash
# 列工作区
libtv workspace list

# 创建工作区
libtv workspace create <name>

# 绑定到工作区（写入 .libtv/project.json）
libtv workspace use <workspace_id>
```

### 2.3 画布（project = 真正的画布文件）

```bash
# 列出当前工作区下的画布
libtv project list

# 创建画布
libtv project create <name>

# 绑定到画布（写入 .libtv/project.json）
libtv project use <project_uuid>

# 画布摘要（含团队空间）
libtv project <project_uuid>
```

### 2.4 节点（node）—— 画布核心操作

**节点状态**：每个节点有状态（`pending` / `running` / `succeeded` / `failed`）。

```bash
# 列出画布上的所有节点
libtv node list

# 在指定分组下列出
libtv node list -g <group>

# 创建节点（必填 -t 节点类型）
libtv node create <name> -t <type> \
  -s model=<model_name> \
  -s ratio=16:9 \
  -s count=2 \
  --prompt "<prompt_text>"

# 写提示词到节点（默认子命令）
libtv node <node_name> --prompt "<text>"

# 修改节点参数（-s 是生成器参数，影响模型怎么生成）
libtv node <node_name> -s model=<name> -s ratio=16:9

# 修改节点自身属性（-u 是节点属性，不影响模型）
libtv node <node_name> -u content='["Hello"]'

# 重命名节点
libtv node <node_name> --name "<new_name>"

# 运行节点（生成内容）
libtv node <node_name> -r

# 边操作
libtv node <node_name> --left <upstream>      # 入边（确保）
libtv node <node_name> --left-add <upstream>  # 入边（追加）
libtv node <node_name> --right <downstream>   # 出边（确保）

# 删除节点
libtv node delete <node_name>
```

**关键参数详解**：

| 参数 | 含义 | 例子 |
|------|------|------|
| `-s, --set` | 生成器参数（`data.params`）| `-s model=nebula-ultra` |
| `-u, --update` | 节点属性（`data` 顶层）| `-u content='[...]\'` |
| `--prompt` | 提示词（与 --set 同用时后写覆盖）| `--prompt "..."` |
| `--left` | 入边 | `--left node1` |
| `--right` | 出边 | `--right node2` |
| `-r, --run` | 生成 | `-r` |

### 2.5 分组（group）—— 节点组织

```bash
# 列出所有分组
libtv group list

# 创建分组
libtv group create <name>

# 绑定到分组（写入 .libtv/project.json）
libtv group use <group>

# 分组操作（需先 group use）
libtv group <group>
libtv group <group> --node-rm <node>  # 移除节点
libtv group <group> --run             # 运行分组
```

### 2.6 上传与下载

```bash
# 上传文件到节点
libtv upload <node> --file <path>

# 下载节点资源到本地
libtv download -n <node_name> -o <output_path>

# 批量下载（多文件输出 ZIP）
libtv download -n <group_name> -o <output.zip>
```

### 2.7 图片辅助（image）

```bash
# 列出图片类画布节点的快捷方式
libtv image shortcut list
libtv image shortcut <scene_label> -n <node>
```

### 2.8 脚本（script / storyboard）

```bash
# 故事版（从脚本节点生成分镜图组）
libtv script storyboard <node>
```

### 2.9 模型查询

```bash
# 按关键词检索
libtv model search qwen
libtv model search --type image qwen

# 列出某类型全部模型
libtv model search --type image
libtv model search --type video

# 全字匹配 modelKey（拉取完整 schema）
libtv model <modelKey>

# 串接 jq 速查 schema
libtv model nebula-ultra | jq '.schema.config.settings'
```

---

## 第 3 章：画布工作流（**最实用**）

### 3.1 4 步标准工作流

```
Step 1：登录 + 绑定画布
  libtv login web
  libtv workspace list
  libtv project use <project_uuid>

Step 2：创建节点（按依赖顺序）
  libtv node create "剧本" -t text
  libtv node create "分镜" -t storyboard --left "剧本"
  libtv node create "P 人物资产" -t image --left "分镜"
  ...

Step 3：写提示词 + 运行
  libtv node "P 人物资产" --prompt "..."
  libtv node "P 人物资产" -r

Step 4：下载产物
  libtv download -n "P 人物资产" -o <LOCAL_PATH>
```

### 3.2 兄弟《重启》实战案例

```bash
# Step 1：登录 + 绑定
libtv login web
libtv project list
libtv project use <restart_uuid>

# Step 2：创建节点（5 段 + 16 张资产）
# 5 段视频节点
for i in 1 2 3 4 5; do
  libtv node create "段${i}" -t video -s model=kling-v3-omni -s ratio=16:9
done

# P 人物 4 视图（一张正面 + 编辑生成其他 3 张）
libtv node create "P 人物正面" -t image -s model=nebula-ultra
libtv node create "P 人物左" -t image -s model=nebula-ultra --left "P 人物正面"
libtv node create "P 人物右" -t image -s model=nebula-ultra --left "P 人物正面"
libtv node create "P 人物背" -t image -s model=nebula-ultra --left "P 人物正面"

# A UI 4 状态
libtv node create "A UI 空" -t image -s model=nebula-2-flash
libtv node create "A UI 有文字" -t image -s model=nebula-2-flash
libtv node create "A UI 删除" -t image -s model=nebula-2-flash
libtv node create "A UI 最终" -t image -s model=nebula-2-flash

# 3 场景
libtv node create "工作室主场景" -t image -s model=mj-v8.1 -s ratio=16:9
libtv node create "窗边场景" -t image -s model=mj-v8.1 -s ratio=16:9
libtv node create "纯白重启" -t image -s model=mj-v8.1 -s ratio=16:9

# Step 3：写提示词
libtv node "P 人物正面" --prompt "(参考图: 26 岁中国男性极客, 黑眼圈...)"
libtv node "段1" --prompt "超广角夜景俯拍, 凌晨3点城市天际线..."

# Step 4：运行 + 下载
for i in 1 2 3 4 5; do
  libtv node "段${i}" -r
done
```

### 3.3 调试技巧

#### 问题 1：节点创建失败
```bash
# 检查 -t 必填
# 用 libtv model search --type <type> 看可用类型
# 注意 script / storyboard 实际是混合模态
```

#### 问题 2：模型不存在
```bash
# 用 libtv model search --type video 找可用的
# 兄弟的 4 个生图模型：lib-image-2 / nebula-ultra / nebula-2-flash / mj-v8.1
# 兄弟的常用视频：kling-v3-omni / star-video2-mini / MiniMax-Hailuo-2.3
```

#### 问题 3：边不生效
```bash
# 必须先 libtv project use 绑定画布
# 节点名要精确匹配（ID 优先）
# --left / --right 是确保边，--left-add / --right-add 是追加
```

#### 问题 4：生成失败
```bash
# 查看节点状态
libtv node <node_name>
# 检查提示词长度（不能太长）
# 检查 -s 参数（model / ratio / count）
```

---

## 第 4 章：AI 看的 8 大铁律

### 铁律 1：先项目后节点
- ❌ 不绑定画布就操作节点
- ✅ `libtv project use` 再操作

### 铁律 2：模型名用 modelName，不用 modelKey
- ❌ `libtv node -s model=star-video2-mini`
- ✅ `libtv node -s model=Seedance 2.0 Mini`（modelName）

### 铁律 3：--set 是生成器参数
- `model` / `ratio` / `count` / `settings.*` 都用 -s
- 例：`-s model=Kling O3 -s ratio=16:9 -s count=2`

### 铁律 4：--update 是节点属性
- `content`（文本节点）/ `rows`（脚本节点）都用 -u
- 例：`-u content='[...]\'`

### 铁律 5：边是单向数据流
- `--left` = 上游（输入）
- `--right` = 下游（输出）
- 例：剧本 → 视频（剧本是 left，视频是 right）

### 铁律 6：节点名要唯一
- 创建时用描述性名称（"段1" 而非 "node1"）
- 改用 `--name "<新名>"`

### 铁律 7：生成用 -r（run）
- 不加 -r = 只创建节点
- 加 -r = 触发生成
- 例：`libtv node "段1" --prompt "..." -r`

### 铁律 8：下载用 -n（name）
- `libtv download -n <name> -o <path>`
- 多文件输出 ZIP

---

## 第 5 章：兄弟的 6 个核心模型（实战表）

### 5.1 生图（4 个模型）

| modelKey | modelName | 角色 |
|----------|-----------|------|
| `nebula-ultra` | Lib Navo Pro | 人物 4 视图 |
| `nebula-2-flash` | Lib Navo 2 | UI 带文字 |
| `mj-v8.1` | 悠船 V8.1 | 电影感场景 |
| `lib-image-2` | Lib Image | 极简概念 |

### 5.2 视频（4 个常用模型）

| modelKey | modelName | 角色 |
|----------|-----------|------|
| `kling-v3-omni` | Kling O3 | 真人感+多镜头 |
| `star-video2-mini` | Seedance 2.0 Mini | 性价比 |
| `MiniMax-Hailuo-2.3-Fast` | Hailuo 2.3 Fast | 快速 |
| `wanxiang-v2-6` | Wan 2.6 | 多机位 15s |

### 5.3 音频（2 个常用模型）

| modelKey | modelName | 角色 |
|----------|-----------|------|
| `speech-2.8-hd` | Minimax-speech-2.8-hd | 配音 |
| `vocal-v3` | Eleven V3 | 音色克隆 |

---

## 第 6 章：兄弟的 7 大隐藏坑

### 坑 1：模型名是 modelName 不是 modelKey
- `libtv node -s model=Kling O3` ✅
- `libtv node -s model=kling-v3-omni` ❌（会被报错）

### 坑 2：--set 后写覆盖
- `libtv node X -s model=Y --prompt "Z"` 中 prompt 是后写
- 后写会覆盖同名 -s 参数

### 坑 3：script/storyboard 实际是混合
- `script` / `storyboard` 既能调文本模型也能调图片模型
- 不是单一类型

### 坑 4：custom 类型不支持
- `--type custom` 会报错
- 必须用 text/image/video/audio/script/storyboard

### 坑 5：-t 和 --set 顺序
- `-t` 必填，位置参数
- `-s` 是设置参数
- 例：`libtv node create "name" -t image -s model=Kling O3`

### 坑 6：--left 和 --left-add 不可同用
- 同时用会报错
- --left 是"确保"，--left-add 是"追加"

### 坑 7：-r 不能后台运行
- `-r` 阻塞等待终态
- 不要在管道中用

---

## 第 7 章：完整工作流模板（AI 直接复制）

```bash
# 完整模板：兄弟《重启》实战
# 1. 登录
libtv login web

# 2. 进入工作区
libtv workspace list
WORKSPACE_ID="<from_list>"
libtv workspace use $WORKSPACE_ID

# 3. 创建或进入画布
libtv project list
PROJECT_ID="<from_list>"
libtv project use $PROJECT_ID

# 4. 创建节点（按依赖顺序）
# 剧本
libtv node create "剧本" -t text -s model=CVLM 5.5
# P 人物资产
libtv node create "P 人物正面" -t image -s model=Lib Navo Pro -s ratio=3:4
# A UI 资产
libtv node create "A UI 4 状态" -t image -s model=Lib Navo 2
# 场景
libtv node create "工作室" -t image -s model=悠船 V8.1 -s ratio=16:9
libtv node create "窗边" -t image -s model=悠船 V8.1 -s ratio=16:9
libtv node create "纯白重启" -t image -s model=悠船 V8.1 -s ratio=16:9
# 视频
for i in 1 2 3 4 5; do
  libtv node create "段${i}" -t video -s model=Kling O3 -s ratio=16:9
done
# 视频合成
libtv node create "完整短片" -t video-combine --left "段1" --left-add "段2" --left-add "段3" --left-add "段4" --left-add "段5"

# 5. 写提示词
libtv node "剧本" --prompt "<完整剧本内容>"
libtv node "P 人物正面" --prompt "(参考图: 26 岁中国男性极客...)"
libtv node "段1" --prompt "超广角夜景俯拍, 凌晨3点城市天际线..."

# 6. 运行
for i in 1 2 3 4 5; do
  libtv node "段${i}" -r
done

# 7. 下载
libtv download -n "完整短片" -o <LOCAL_PATH>
```

---

## 第 8 章：AI 看的元规则

### 元规则 1：模型名 = modelName
- `nebula-ultra` 不是 `lib-image-2`（不一样）
- 永远是 -s model=<modelName>

### 元规则 2：参数含义
- `-s` = 模型参数（model / ratio / count / settings.*）
- `-u` = 节点属性（content / rows）
- `--prompt` = 提示词（与 -s 同用时后写）

### 元规则 3：节点是图
- 节点之间用边连接
- 边方向 = 数据流
- 左 → 右 = 上游 → 下游

### 元规则 4：状态是异步
- 节点有状态（pending / running / succeeded / failed）
- `-r` 阻塞等待终态
- 失败可重试

---

## 第 9 章：兄弟《重启》画布结构

```
重启画布
├── 剧本（text, CVLM 5.5）
├── P 人物资产组
│   ├── P 人物正面（image, Lib Navo Pro）
│   ├── P 人物左（image, Lib Navo Pro）--left P 人物正面
│   ├── P 人物右（image, Lib Navo Pro）--left P 人物正面
│   └── P 人物背（image, Lib Navo Pro）--left P 人物正面
├── A UI 资产组
│   ├── A UI 空（image, Lib Navo 2）
│   ├── A UI 有文字（image, Lib Navo 2）
│   ├── A UI 删除（image, Lib Navo 2）
│   └── A UI 最终（image, Lib Navo 2）
├── 场景资产组
│   ├── 工作室主场景（image, 悠船 V8.1）
│   ├── 窗边场景（image, 悠船 V8.1）
│   └── 纯白重启（image, 悠船 V8.1）
├── 视频组
│   ├── 段1 视频（video, Kling O3, 15s）--left P 人物 --left 工作室
│   ├── 段2 视频（video, Kling O3, 15s）--left P 人物 --left 工作室
│   ├── 段3 视频（video, Kling O3, 15s）--left P 人物 --left 工作室
│   ├── 段4 视频（video, Kling O3, 15s）--left P 人物 --left 窗边
│   └── 段5 视频（video, Kling O3, 15s）--left P 人物 --left 纯白重启
└── 完整短片（video-combine）--left 段1 段2 段3 段4 段5
```

---

## 已保存位置

**本文**：`<LOCAL_PATH>`

**兄弟的方法论 3 大文件**：
1. **本文** - LibTV 完整使用手册
2. **文件 2** - 生图模型选型 + 提示词（人物/场景/物品）
3. **文件 3** - 视频大模型选型 + 提示词

**v7.0 框架新增第 32-34 章** = AIGC 导演 3 大核心文件

---

## 第 10 章：权威来源（**AI 必读**）

### 等级 A：学术共识

| 来源 | 关键论证 |
|------|----------|
| **arXiv 2504.03738** | "Attention mechanisms are foundational in diffusion models" |
| **PMC12753859** | "Cross-attention is the key mechanism for text conditioning" |

### 等级 B：工业共识

| 来源 | 关键论证 |
|------|----------|
| **LibTV 官方文档** | 6 节点类型 + CLI 命令（兄弟 v3.1 实战验证）|
| **Runway Gen-4 官方** | "物理动作 > 抽象概念"原则 |

### 兄弟实战验证

- 兄弟 v3.1《<PROJECT_CASE>》段 1-7 实战（5-15s 段长）
- 兄弟《重启》5 段完整剧本

**详见**：`99_权威来源与参考文献.md`