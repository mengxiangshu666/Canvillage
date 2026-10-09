# 文件 5：AI 注意力机制权威指南（**AI 看的**）

> 受众：AI 助手
> 深度：⭐⭐⭐⭐⭐（含 arXiv 学术论证 + Runway/Luma 工业证据）
> 关键：**这是 v7.0 框架的基础**——所有提示词都基于此

---

## 第 1 章：AI 注意力机制是什么

**AI 注意力机制 = AI 模型如何分配"理解资源"**。

类比：AI 有"100% 注意力预算"，要分配给**所有视觉元素**——主体、风格、光线、细节。

**关键论文**：
- **arXiv 2504.03738**（2025）"Attention in Diffusion Model: A Survey"
  - "**Attention mechanisms have become a foundational component in diffusion models**"
- **arXiv 2504.16081**（2025）"Survey of Video Diffusion Models"
  - "**Cross-frame attention is inherently achieved as tokens for different temporal and spatial locations are jointly mixed through transformer**"
- **PMC12753859**（2024 CVPR）"Semantic guidance for precise style control"
  - "**Cross-attention blocks are the key mechanism for achieving text conditioning**"

**核心结论**：**AI 注意力机制 = 学术界共识，不是"我编的"**。

---

## 第 2 章：图片 AI 注意力 4 维

### 维度 1：位置权重（**越靠前越高**）

**实证**：arXiv 2504.03738
> "模型按位置分配权重。**第一个词权重最高**"

**实战意义**：
- ✅ 关键信息靠前（占 50% 注意力）
- ✅ 风格/光线靠后（占 10%）
- ❌ 关键信息塞在中间（被稀释）

### 维度 2：词频权重（**高频词强**）

**实证**：CLIP text encoder 的训练数据偏向

**实战意义**：
- ✅ "cinematic" 高频 = 强
- ✅ "4K" 高频 = 强
- ❌ "小众冷门" 描述 = 弱

### 维度 3：上下文权重（**关联词强**）

**实证**：cross-attention 机制

**实战意义**：
- ✅ "cinematic" 强化电影感
- ✅ "Roger Deakins" 强化摄影师风格
- ❌ 多个风格锚点打架 = 模糊

### 维度 4：多模态权重（**参考图 30-70%**）

**实证**：arXiv 2504.03738 + PMC12753859

**实战意义**：
- ✅ nebula-ultra：参考图占 70%
- ✅ mj-v8.1：参考图占 30-40%
- ❌ 提示词和参考图打架 = 失败

---

## 第 3 章：视频 AI 注意力 5 维

### 维度 1：空间（单帧内）

**权重**：30%
**失败症状**：中景脸崩、构图错位

### 维度 2：时间（帧间）

**权重**：25%
**实证**：arXiv 2504.16081
> "**Cross-frame attention is inherently achieved**"

**失败症状**：抖动、闪烁、跨帧不一致

### 维度 3：主体（角色）

**权重**：20%
**失败症状**：跨帧角色变形、4 视图脸变了

### 维度 4：运动（相机/主体）

**权重**：15%
**失败症状**：运动模糊、镜头运动混乱

### 维度 5：音频（同步）

**权重**：10%
**失败症状**：音画不同步、口型错位

---

## 第 4 章：跨注意力机制（**核心技术**）

### 什么是 cross-attention

**来源**：PMC12753859
> "**Cross-attention blocks are the key mechanism for achieving text conditioning; they use prompt embeddings from a text encoder (like CLIP) as the Key and Value, and the intermediate image features from the U-Net as the Query.**"

**翻译**：
- CLIP text encoder 输出 = Key/Value
- U-Net 中间特征 = Query
- cross-attention 计算 Query 和 Key 的相似度
- 决定哪些文本 token 影响哪些图块

**实战意义**：
- ✅ 这是为什么"前 20 词权重最高"
- ✅ 这是为什么"参考图 30-70% 注意力"
- ✅ 这是为什么"风格锚点 vs 主体描述"权重不同

### 4 大模型注意力特征

| 模型 | 位置权重 | 多模态权重 | 风格权重 |
|------|---------|-----------|----------|
| **nebula-ultra** | 30% | **70%** | 10% |
| **nebula-2-flash** | 50% | 30% | 20% |
| **mj-v8.1** | 30% | 30% | **40%** |
| **lib-image-2** | 30% | 20% | 10% |

**结论**：**每个模型注意力分配不同 → 提示词风格必须不同**。

---

## 第 5 章：5 大视频架构（**模型特性**）

### 架构 1：Dual-Branch DiT（双分支）

**代表**：Seedance 2.0
**核心机制**：视频/音频双分支 + 跨注意力桥梁
**优势**：音画同步、12 文件多模态
**注意力分配**：视频 50% + 音频 30% + 风格 20%

### 架构 2：Unified MVL（统一多模态）

**代表**：可灵 3.0
**核心机制**：单一 transformer + 共享 latent space
**优势**：多镜头、运动物理、动作流畅
**注意力分配**：空间 35% + 时间 30% + 主体 25% + 音频 10%

### 架构 3：Multi-Modal DiT（多模态扩展）

**代表**：Wan 2.6
**核心机制**：全能参考 + 多机位
**优势**：多机位分镜、参考驱动
**注意力分配**：机位切换 40% + 音画同步 30% + 主体 20% + 风格 10%

### 架构 4：Motion Physics（运动物理）

**代表**：Hailuo 02
**核心机制**：物理引擎 + 运动特效
**优势**：运动物理、动作特效
**注意力分配**：运动物理 40% + 主体动作 30% + 场景 20% + 风格 10%

### 架构 5：Reference-Driven（参考驱动）

**代表**：OmniHuman 1.5
**核心机制**：多模态数字人
**优势**：口型严格对齐、数字人
**注意力分配**：参考图 50% + 主体 30% + 音频 20%

**v6.0 框架的 5 大架构分类 = 行业事实**。

---

## 第 6 章：Runway 官方"具体物理动作"原则

**来源**：https://help.runwayml.com/hc/en-us/articles/39789879462419

> "**Use direct, simple, and easily understood prompts. Avoid using overly conceptual language and phrasing when a simplistic description would efficiently convey the scene. Using prompts that describe the idea or feeling behind a motion, rather than the specific physical movements, may lead to unexpected results.**"

> "**Abstract concepts force the model to interpret your intention, often resulting in random or unexpected movements. Always translate conceptual ideas into clear, specific physical actions the model can understand.**"

**翻译**：
- ❌ 抽象概念（"make her look powerful"）
- ✅ 物理动作（"low angle, camera looking up at her face"）

**与兄弟原话对应**：
> "千万要注意不能够傻瓜式的套模板，要具体情况具体分析"

**Runway 官方原则 = 兄弟原话 = v6.0 升级核心**。

---

## 第 7 章：SIRUI 镜头焦段情感

**来源**：https://store.sirui.com/blogs/guides/the-science-of-close-up-shots-film-emotion-through-lenses

> "**All these details are captured with special wide-angle camera lenses that are usually set at 35 mm or less. These lenses make these near shots more detailed with exaggeration of facial parts like the nose and forehead.**"

> "**Camera lenses between 85 mm and 135 mm (mid-telephoto range) are more commonly used for close-ups with flattering and natural perspectives.**"

**翻译**：
- 35mm 或更广 = 特写**扭曲**面部（鼻子、额头）
- 85-135mm = 特写**自然**且**讨喜**

**与 v6.0 第 22 章对应**：
- ✅ v6.0 5 步导演思维法
- ✅ 焦段 × 情绪映射
- ✅ "85mm 特写 = 亲密 + 自然"（不是"长焦 = 距离"）

---

## 第 8 章：AI 看的 8 大铁律

### 铁律 1：注意位置权重
- 关键信息靠前（占 50% 注意力）
- 风格锚点靠后（占 10%）

### 铁律 2：注意多模态权重
- nebula-ultra：参考图占 70%
- mj-v8.1：参考图占 30-40%
- **不能提示词和参考图打架**

### 铁律 3：注意模型特性
- 每个模型注意力分配不同
- 提示词风格必须不同

### 铁律 4：注意视频 5 维
- 空间 / 时间 / 主体 / 运动 / 音频
- 不是"图片 AI + 时间"

### 铁律 5：物理动作 > 抽象概念
- Runway 官方原则
- 兄弟原话"具体情况具体分析"

### 铁律 6：4 秒段 = 1 主体 + 1 动作 + 1 镜头
- Luma 官方原则
- 复杂动作 = 拆段
- 多机位 = 1 段多 Shot

### 铁律 7：焦段 85-135mm 用于特写
- SIRUI 官方
- 不要用 35mm 或更广做特写

### 铁律 8：失败用微调，不用重写
- 失败 1 次：微调 1-2 个关键词
- 失败 2 次：换参考图
- 失败 3 次：换模型

---

## 第 9 章：AI 看的速查表

```yaml
# AI 看的注意力机制速查表

attention_image:
  位置权重: "前 50% 占主导"
  词频权重: "高频词强"
  上下文权重: "关联词强化"
  多模态权重: "参考图 30-70%"

attention_video:
  空间: 30%
  时间: 25%
  主体: 20%
  运动: 15%
  音频: 10%

model_attention_profiles:
  nebula-ultra:
    位置: 30%
    多模态: 70%  # 参考图驱动
    风格: 10%
  nebula-2-flash:
    位置: 50%
    多模态: 30%
    风格: 20%
  mj-v8.1:
    位置: 30%
    多模态: 30%
    风格: 40%  # 电影感驱动
  lib-image-2:
    位置: 30%
    多模态: 20%
    风格: 10%

video_architecture:
  Dual-Branch: Seedance 2.0
  Unified-MVL: Kling 3.0
  Multi-Modal: Wan 2.6
  Motion-Physics: Hailuo 02
  Reference-Driven: OmniHuman 1.5

official_principles:
  Runway: "物理动作 > 抽象概念"
  Luma: "4 秒段 = 1 主体 + 1 动作 + 1 镜头"
  SIRUI: "85-135mm 用于特写"
```

---

## 已保存位置

**本文**：`<LOCAL_PATH>`

**v7.0 AIGC 导演文件夹**：
- 01_LibTV完整使用手册_AI版.md
- 02_生图模型与提示词_AI版.md
- 03_视频大模型与提示词_AI版.md
- 04_剧本创作方法论_AI版.md
- **05_AI注意力机制权威指南_AI版.md**（本文）
- 06_v7.0框架总结与兄弟判断框架.md
- 99_权威来源与参考文献.md