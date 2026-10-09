---
name: village-canvas-canvas-ops
description: LibTV CLI完整操作手册——workspace/project/node/group的增删改查、素材上传下载、模型查询、画布全流程命令。Village Infinite Canvas画布的底层操作基座。来源：兄弟AIGC知识包01号文件（2026-07）。
triggers:
  - LibTV画布操作
  - 创建节点
  - 上传下载素材
  - 查询模型
  - workspace管理
  - project管理
  - group管理
---

# LibTV 完整操作手册

## 架构哲学

**LibTV = 兄弟的"小社会"哲学在AIGC领域的实现**

- 画布（project）= 创作空间
- 项目（workspace）= 装画布的容器
- 节点（node）= 画布上的基本元素
- 边（edge）= 节点之间的连接
- 组（group）= 普通分组（不是节点类型，是组织方式）

**核心思维**：每个元素都有独特价值——小社会哲学。

---

## 6大节点类型

| UI名称 | CLI类型 | 用途 |
|--------|---------|------|
| 三文体 | `text` | 剧本/对话生成 |
| 图片 | `image` | 14个模型 |
| 视频 | `video` | 25+个模型 |
| 视频合成 | `video-combine` | 多段拼接 |
| 导演出图 | `storyboard` | 脚本→分镜图组 |
| 音频 | `audio` | 5个模型 |
| 脚本 | `script` | 文本结构化（带rows）|
| 素材库 | `asset` | 人物/场景资产库 |

---

## CLI命令完整参考

### 登录与账户

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

### workspace（工作区）

```bash
# 列出工作区
libtv workspace list
# 创建工作区
libtv workspace create <name>
# 绑定到工作区（写入 .libtv/project.json）
libtv workspace use <workspace_id>
```

### project（画布）

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

### node（节点）—— 画布核心操作

**节点状态**：pending / running / succeeded / failed

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
| `-s, --set` | 生成器参数（data.params） | `-s model=nebula-ultra` |
| `-u, --update` | 节点属性（data 顶层） | `-u content='[...]'` |
| `--prompt` | 提示词（与 --set 同用时后写覆盖） | `--prompt "..."` |
| `--left` | 入边 | `--left node1` |
| `--right` | 出边 | `--right node2` |
| `-r, --run` | 生成 | `-r` |

### 分组（group）

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

### 上传与下载

```bash
# 上传文件到节点
libtv upload <node> --file <path>
# 下载节点资源到本地
libtv download -n <node_name> -o <output_path>
# 批量下载（多文件输出ZIP）
libtv download -n <group_name> -o <output.zip>
```

### 脚本（script / storyboard）

```bash
# 故事版（从脚本节点生成分镜图组）
libtv script storyboard <node>
```

### 模型查询

```bash
# 列出图片类画布节点的快捷方式
libtv image shortcut list
libtv image shortcut <scene_label> -n <node>
# 模型搜索
libtv model search --type image
libtv model search --type video
```

---

## 节点类型 → 模型集合

```
节点类型 (--type) → 模型集合 (--search)
├─ text → aurora-3-prime / cvlm-5.5 / aurora-3-lite / qwen-3-vl-flash
├─ image → 14个模型
├─ video → 25+个模型
├─ audio → 5个模型
├─ storyboard → 文本模型 + 图片模型（混合）
├─ script → 文本模型 + 图片模型（混合）
└─ custom → 不支持
```
