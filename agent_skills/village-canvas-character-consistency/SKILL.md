---
name: village-canvas-character-consistency
description: 角色资产一致性作业流——身份账本→定脸→服装→角色表→特写/换装，五模式带依赖顺序与逐步确认。覆盖：文字规格化身份、共享不变量与探索轴的区分、参考图驱动、Flat Grade 无影棚光照、"只有出现结果资产才能说已生成"的反编造纪律。方法论来源：libtv Cast Builder 角色资产创建（1021 次使用）与他人在用 2728 次的《电影级 AI 分镜导演》的全局连续性锁。
triggers:
  - 角色一致性
  - 角色资产
  - 角色设定表
  - 角色身份卡
  - 定脸
  - 服装定妆
  - 角色参考图
  - 身份卡
  - 换装
  - 三视图角色
---

# 角色资产一致性作业流

同一个角色在整片里必须长得一样。做法不是"多写几遍长相描述"，而是**建一张权威身份卡，之后所有图都从它派生**。

> 前置：`village-canvas-character-workflow` 讲的是"先单图试探再批量"省积分，
> 本技能讲的是**怎么保证是同一个人**。两者互补，预算敏感时先读前者。

## 模型不许凭空写死

**只准用注册表里真实存在的模型。** 本技能里的模型名是能力档名，调用时必须先在当前
可用的图像模型目录里核对显示名。

历史上这里写过 `Nebula Pro`——我们产品里没有这个模型，agent 按它去调只会失败或编造。
**任何时候不确定模型叫什么，先查目录，不要凭印象写。**

当前可用的图像能力档（按能力档名匹配，用正则匹配实际模型 ref）：

| 能力档 | 用途 | 支持图生图 |
|---|---|---|
| `gpt-image` | 通用出图与身份卡 | 是 |
| `multimodal-image-edit` | 参考图驱动、以图生图为主 | 是 |
| `midjourney` / `midjourney-niji` | 风格化出图 | 否 |
| `dall-e-3` / `dall-e-2` | 备选 | 视档位 |

## 五个模式与依赖顺序

```text
文字规格 → Mode 0 定脸 → Mode 1 服装定妆 →（Mode 1B 造型细节，按需）
        → Mode 2 角色表 →（Mode 3 面部特写 / Mode 4 换装，按需）
```

已有稳定参考图可以跳过 Mode 0，但**必须先确认身份**。

| 模式 | 产出 | 谁可以进 |
|---|---|---|
| Mode 0 定脸 | 唯一权威身份卡 | 只有用户选定的那一张 |
| Mode 1 服装定妆 | 单张全身服装基准 | 以 Mode 0 卡为参考 |
| Mode 2 角色表 | 三格角色表 | 以 Mode 0 / Mode 1 为参考 |
| Mode 3 面部特写 | 脸 / 肩上 / 胸像 | 以 Mode 0 卡为参考 |
| Mode 4 换装 | 两参考图换装 | 第一张给服装姿势，第二张给身份 |

**候选图不能进入 Mode 1 / 2。** 只有用户批准的唯一权威身份卡可以继续。

## 1. 先建身份账本

状态不明时问一句就够：**"这个角色已经有稳定参考图，还是从零建立？"**
已说明则不要重复问。

新角色先把下面这些**用文字敲定并存进角色节点**，再进 Mode 0：

```text
脸部骨相 / 眼形眼色 / 眉鼻唇 / 肤色肤质 / 头发 / 体型比例 /
默认表情 / 永久纹身及位置 / 疤痕 / 美人痣 / 固定穿孔
```

妆面、美甲、可替换首饰在 Mode 1 锁定；标志性常戴配饰在 Mode 0 登记、Mode 1 渲染锁定。

**只写可见信息，不猜测、不用姓名代替外貌、不写真实品牌或具体年龄**（可写 `adult`）。

### 共享不变量 vs 探索轴

不确定的脸部项标成**探索轴**，其余是**共享不变量**：

- **共享不变量**：已确认、跨图必须不变的部分
- **探索轴**：一到两个有界、可见的方向（如"下颌线锐利 vs 圆润"）

探索轴**可以连同连接词整段省略，不得伪装成已确认事实**。

## 2. 写 Prompt 前核对事实与已有授权

写完整 Prompt 前逐项确认：

- **参考图**：逐张列出职责；没有就写"无，纯文字构建"
- **身份与本次要锁定的 Look**
- **背景**：默认 18% 中性灰；候选脸用 neutral mid-gray
- **构图**：仅在非默认时列

用户已经要求写 Prompt 或给出本次角色/造型要求时，直接给可审阅文本，不再问
“可以开始写吗”。未知身份细节标为缺口或探索轴，不写成锁定事实。
没有选定权威身份图时，只制作候选或草案，不擅自将候选升级为正式锚点。
新角色、新 Look、新模式先核对是否在已有指令范围内；缺少影响结果的关键选择才提问。
写 Prompt 不代表获准生成媒体；调用模型仍需有效 `task_authorization`。

## 3. 每次调模型前给路由卡

```text
当前模式与步骤：Mode 0／第一步试脸
模型与参考图：<精确显示名>；逐张职责，或「无，纯文字构建」
本步产物：候选 / 权威身份卡 / 服装基准 / 角色表 / 细节卡 / 换装结果
```

## 4. 反编造纪律（不可省）

- **不能绑定或核对模型时，停止自动执行，只交付 Prompt，并写"尚未调用模型"。**
- **每次只推进已确认的步骤，不预建后续节点。**
- **只有出现结果资产，才能说"已生成"。**
- 没有服务端回执就不要声称写入成功。

## 5. Flat Grade 光照块（Mode 0 / 1 / 2 原样展开）

```text
Background is an even 18% neutral gray seamless, one completely flat uniform value
corner to corner, with no seam line, gradient, hotspot, vignette, or falloff.
Relight from scratch with completely flat shadowless illumination: one enormous
soft frontal source at camera position and matched equal fill from camera-left,
camera-right, above, and below, so both sides of the face and body read at
identical brightness. No key-to-fill ratio, shadow side, rim light, hair light,
kicker, or specular hotspot. Zero shadow cast onto the background, no contact
shadow beneath the feet or hem, and no ambient occlusion. Extremely low
contrast, even, milky, catalogue-flat. Skin and fabric remain matte and render
at their true natural tones, never washed out or cool-shifted by the
background. Real fine even pore texture, peach fuzz, subtle subsurface
scattering, strand-level hair, real fabric weave and drape, soft natural film
grain. Fine, flattering, photographed realism — never plastic, waxy,
glass-skin, harsh, or clinically detailed. Photographed on a clean
50mm-equivalent prime with even sharpness. Photographed, not generated.
```

白底例外只把首句换成
`Pure white seamless studio background, perfectly even, with no gradient or seam line.`，其余条款不变。

方括号是内部参数槽，**交付前必须替换或删除**。

## 6. 角色表布局

```text
左侧 1/3：角色正面高保真面部近景（头顶到锁骨），居中对齐，聚焦五官与微神态
右侧 2/3：全身三视图（正面、侧面、背面并排站立），头身比严格一致，像原地匀速转身，
          三视图之间留清晰间隔、互不遮挡
背景：浅灰 #E8E8E8 均匀无影
```

**角色表只保留一个清楚的人脸锚点**，不得在背面格再塞第二张脸。
一张图含所有角度，之后所有镜头只引用这一张。

## 7. 通用 Prompt 规则

- 中文交互，英文 Prompt
- 不写数值画幅，只写 `full body`、`chest-up` 这类构图词
- 默认中性闭唇；不输出独立 Negative Prompt
- 每个 Prompt 一个代码块；批量逐项编号
- **不添加用户未确认的首饰、纹身、妆面或服装细节**
- 除 Mode 4 编号外，只用职责描述指代参考图（如 `the attached character reference`）

## 8. 落到本产品

1. 身份账本写进角色节点的文字字段（`character` 节点）
2. 每一步的产出图落成画布节点，`reference` 指向上一张权威卡的节点
3. 后续镜头提示词里引用**权威卡的节点**，不要复述外貌文字
4. 参考图改用图生图能力档，不要指望纯文字能锁住脸
