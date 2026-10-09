# LibTV 模型参数速查表（地毯级）

> 获取时间：2026-07-03 06:00
> 来源：`libtv model search --type <type>` + `libtv model <modelKey>`
> 用途：任何时候选模型/设参数，先查这张表

---

## 一、生图模型（共14个）

| modelName | modelKey | 适用场景 | vip |
|----------|---------|---------|-----|
| **Lib Image** | lib-image-2 | 极简概念图、快速出图 | ❌ |
| **Lib Navo Pro** | nebula-ultra | ⭐角色定妆（4视图）、一致性最强 | ❌ |
| **Lib Navo 2** | nebula-2-flash | ⭐UI/文字渲染、海报、平面设计 | ❌ |
| **悠船 V8.1** | mj-v8.1 | ⭐电影感场景、艺术风格 | ❌ |
| 悠船 V7 | mj-v7 | 次选场景模型 | ✅ |
| 悠船 Niji 7 | mj-niji7 | ⭐动漫风格 | ✅ |
| Seedream 4.6 | jimeng-4.6 | 人像一致性、平面设计 | ✅ |
| Seedream 5.0 Lite | seedream-5 | ⭐中式风格（修仙/古风） | ✅ |
| Seedream 4.5 | seedream-4.5 | 多角色一致性 | ✅ |
| Z-image Turbo | z-image | 极速真实感 | ❌ |
| Lib Navo | nebula-core | 图像编辑 | ❌ |
| Qwen Image | qwen | 文字排版 | ❌ |
| Qwen Edit | qwen-edit | 精细编辑 | ❌ |
| Seedream 4.0 | seedream-4 | 极速出图·中文文字 | ✅ |

### 生图参数参考
- **modelName** = `-s model=<modelName>`（CLI用这个）
- **ratio** = `-s ratio=16:9`
- **count** = `-s count=1`（生成数量）
- **prompt** = `--prompt "..."`

---

## 二、视频模型（共14个常用）

| modelName | modelKey | 适用场景 | 最大时长 | 音画同步 | vip |
|----------|---------|---------|---------|---------|-----|
| **Seedance 2.0 VIP** | star-video2 | ⭐极致画质 | 15s | ✅ | ✅ |
| **Seedance 2.0 Fast VIP** | star-video2-fast | 快速版 | 15s | ✅ | ✅ |
| **Seedance 2.0 Mini** | star-video2-mini | ⭐性价比首选·修仙场景 | 15s | ✅ | ✅ |
| **Happy Horse 1.1** | happy-horse-1.1 | 阿里·一致性强 | — | — | ✅ |
| **Happy Horse 1.0** | happy-horse-1 | 阿里·可多参 | — | — | ✅ |
| **Kling O3** | kling-v3-omni | ⭐真人感·多镜头·编辑用 | 15s | ✅ | ✅ |
| **Kling 3.0 Turbo** | kling-v3-turbo | 快速版 | 10s | — | ✅ |
| **Kling 3.0** | kling-video-o3 | 标准版 | 10s | ✅ | ✅ |
| **Wan 2.7** | wanx2.7-video | 全能参考·编辑 | 15s | — | ❌ |
| **Wan 2.6** | wanxiang-v2-6 | ⭐多机位·最长15s | 15s | ✅ | ❌ |
| **Hailuo 2.3 Fast** | MiniMax-Hailuo-2.3-Fast | 快速·动作 | — | — | ❌ |
| **Hailuo 2.3** | MiniMax-Hailuo-2.3 | 动作/物理 | — | — | ❌ |
| **Vidu Q3 Pro** | viduq3-pro | 主体参考 | — | — | ✅ |
| **MJ Video** | midjourney-video | ⭐图生视频稳 | — | — | ✅ |

### 视频参数参考
- **ratio** = 16:9 / 21:9 / 9:16 等
- **时长** = 各模型不同，最大15s
- **Kling O3 格式**：`第X个机位(X秒): [运镜+画面]`
- **Seedance 格式**：`@Image` + `[STYLE LOCK]` + T0-Ts

---

## 三、音频模型（共5个）

| modelName | modelKey | 用途 |
|----------|---------|------|
| **Minimax-speech-2.8-hd** | speech-2.8-hd | ⭐配音·情绪渲染 |
| Minimax-speech-2.8-turbo | speech-2.8-turbo | 快速配音 |
| **Eleven V3** | vocal-v3 | ⭐音色克隆 |
| **Eleven Music V3** | vocal-music | ⭐配乐生成 |
| **Mureka V8** | mureka-8 | 音乐生成 |

---

## 四、文本模型（共4个）

| modelName | modelKey | 用途 |
|----------|---------|------|
| **GVLM 3.1** | aurora-3-prime | 多模态文本Pro |
| **CVLM 5.5** | cvlm-5.5 | ⭐超智能LLM |
| GVLM 3.1 Flash | aurora-3-lite | 轻量版 |
| Qwen 3 VL Flash | qwen-3-vl-flash | 快速版 |

---

## 五、模型选型对照表

### 生图选型

| 要生成什么 | 用哪个模型 | 为什么 |
|----------|----------|--------|
| 角色4视图/定妆 | **Lib Navo Pro** | 70%参考图权重·一致性最强 |
| UI/文字/海报 | **Lib Navo 2** | 文字渲染最准确 |
| 电影感场景 | **悠船 V8.1** | 40%风格权重·美学最强 |
| 修仙/古风/中式 | **Seedream 5.0 Lite** | 最擅长中式审美 |
| 极速概念图 | **Z-image Turbo** | 快 |
| 动漫角色 | **悠船 Niji 7** | 动漫专用 |

### 视频选型

| 要生成什么 | 用哪个模型 | 时长限制 |
|----------|----------|---------|
| 真人脸·表情·现实房间 | **Kling O3** | 15s（多机位） |
| 修仙世界·特效·性价比 | **Seedance 2.0 Mini** | 15s |
| 动作/战戏 | **Hailuo 2.3** 或 **Wan 2.6** | — |
| 图生视频稳 | **MJ Video** | — |
| 极致画质 | **Seedance 2.0 VIP** | 15s |

---

## 六、CLI 铁律操作速查

```bash
# 创建节点（只填参数，不点生成）
libtv node create "节点名" -t video -s model="Seedance 2.0 Mini" -s ratio=16:9
libtv node create "角色定妆" -t image -s model="Lib Navo Pro" -s ratio=3:4

# 写提示词
libtv node "节点名" --prompt "提示词内容 --no subtitle text"

# 修改参数
libtv node "节点名" -s model="Kling O3" -s ratio=16:9

# 连入边（数据流）
libtv node "段1" --left "角色定妆"

# 连出边
libtv node "角色定妆" --right "段1"

# 查看节点
libtv node list
libtv node "节点名"

# 搜模型
libtv model search --type video
libtv model search --type image qwen

# 分组
libtv group create "组名"
libtv group use "组名"     # 设为默认
libtv group list

# ⛔ 绝对不用的
# libtv node "X" -r
# libtv node create "X" --run
```