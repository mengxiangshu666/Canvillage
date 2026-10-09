# 补丁02 — 多角色控制 + 提示词迭代方法论

> 来源：Atlas Cloud Seedance 2.5 (2026-06) + Medium multiple character consistency (2026) + Digital Applied Prompt Engineering (2026)
> 当前知识库的"单角色一致性"已覆盖，但多角色交叉和迭代方法论是空白

---

## 一、多角色群戏控制

### 问题本质

AI 视频模型在单角色场景中已经能保持 80-90% 一致性。但在**两三个人同时出现在同一画面**时，会出现：

- 角色特征互相污染（A 的衣服跑到 B 身上）
- 表情和动作不可控
- 眼神和互动方向随机

### 解决方案

**方案 A：Seedance 2.5 的 50 个参考输入（推荐）**

等 2.5 公测后，50 个参考输入的核心价值就在这里：
- 角色 A 分配 10-15 张参考图（不同角度、表情、服装）
- 角色 B 分配 10-15 张参考图
- 场景/环境 5-10 张参考图
- 提示词中明确描述每个角色的动作和互动

**提示词结构：**
```
@CharacterA in [环境], [动作描述] while @CharacterB [反应/动作],
[镜头控制], [灯光], [情绪]
```

**方案 B：单角色逐帧 + 拼接（当前可用）**

1. 先单独生成角色 A 的镜头（只有 A 在画面中）
2. 再单独生成角色 B 的镜头（只有 B 在画面中）
3. 在后期合成时，用绿幕/蒙版将两个角色合到同一画面
4. 或者用快切/交叉剪辑模拟互动（不要求两人同时入镜）

**有时不用群戏更好：** 如果故事不需要两人同时出现，用镜头交替剪辑（shot/reverse shot）效果更好，也避免了 AI 群戏的崩塌风险。

### 提示词具体写法

两人对话场景，优先用正反打（交替特写）：
```
Shot 1: Character A looking slightly right, talking to someone off-screen
Shot 2: Character B listening, slight smile, glancing left
```

需要两人同框时，用参考图锁定 + 明确的空间关系：
```
Character A (left) talking to Character B (right),
both sitting at a wooden table, medium shot,
natural lighting, A gestures with hand
```

---

## 二、提示词迭代方法论

### 标准三遍迭代法

| 轮次 | 干什么 | 调什么 | 预期 |
|------|--------|--------|------|
| 第 1 次 | 按模板写初版提示词 | — | 出片看一眼，大概率崩 |
| 第 2 次 | 分析失败原因 | 调 1-2 个词 | 看看能不能用 |
| 第 3 次 | 换参考图 | 换参考图不动提示词 | 看看谁的问题 |
| 第 4 次 | 换模型 | 换 Seedance/Kling 试 | 看看是不是模型不对 |

### 失败分类

| 现象 | 原因 | 修法 |
|------|------|------|
| 手崩 | 手部超出模型能力 | 避免手部特写，遮挡手部 |
| 脸崩 | 参考图不够/角度受限 | 加正面参考图，去背景 |
| 文字错 | 模型不擅长生成文字 | 后期加，不在提示词里写 |
| 跨帧不一致 | 运动太快 | 降运动强度，加参考帧 |
| 环境漂移 | 提示词不够具体 | 加环境颜色/材质/光源描述 |

### 测试集概念

准备一个 5 镜头的标准测试集：
- 1 个近景面部（检查脸）
- 1 个中景动作（检查运动）
- 1 个远景（检查环境）
- 1 个对白/口型（检查音画同步）
- 1 个多角色/多人（检查一致性）

每次换模型/换参数，先用测试集跑一遍，不用直接上正式镜头。

---

## 来源

- [Seedance 2.5 vs Seedance 2: Multi-Character Reference Capacity](https://www.atlascloud.ai/blog/guides/seedance-2-5-vs-seedance-2) — Atlas Cloud, Jun 2026
- [How to Create Multiple Consistent Character AI Videos](https://www.youtube.com/watch?v=ko9bk9rkGKw) — YouTube, 2026
- [How to Keep Your Character Consistent Across 5 Different AI Video Clips](https://james-palm.medium.com/how-to-keep-your-character-consistent-across-5-different-ai-video-clips-the-2026-guide-nobodys-bd39a5234ecf) — Medium, 2026
- [Prompt Engineering Advanced Techniques 2026](https://www.digitalapplied.com/blog/prompt-engineering-advanced-techniques-2026) — Digital Applied, 2026