# AIGC 影视短剧全链路核心能力 — 学习路径与实践指南

> 整理日期：2026-06-21
> 数据来源：Tavily 搜索 + 2026 年业界最新动态
> 目标：从零到能独立产出 AIGC 短剧成片，并具备商业化能力

---

## 概览：能力金字塔

```
                  ┌──────────┐
                  │ 商业落地  │  ← 顶部：变现
                 ┌┴──────────┴┐
                 │  成片产出   │  ← 后期剪辑+音视频整合
                ┌┴────────────┴┐
                │  工程落地    │  ← 多工具协同+Agent自动化
               ┌┴──────────────┴┐
               │  AI 专业技能   │  ← 提示词+模型逻辑+瑕疵修复
              ┌┴────────────────┴┐
              │    美术设计      │  ← 角色概念+三视图+美学
             ┌┴──────────────────┴┐
             │    视听叙事        │  ← 编导+分镜+摄影
            ┌┴────────────────────┴┐
            │     文字创作         │  ← 编剧+剧本+台词（地基）
            └──────────────────────┘
```

---

## 第一层：文字创作能力（地基）

### 1.1 编剧 & 剧本架构

**核心知识点**：
- 三幕剧结构 / 短剧黄金节奏（每 3-5 秒一个钩子）
- 人物弧光设计：一个角色在故事中怎么变
- 冲突设计：人 vs 人 / 人 vs 环境 / 人 vs 自己
- 短剧特殊要求：每集 1-3 分钟，8-30 集连续，每集必须有"卡点"（cliffhanger）

**学习资源**：
- 书：《救猫咪》（Blake Snyder）— 节拍表方法论，编剧圣经
- 书：《故事》（Robert McKee）— 叙事原理
- 在线课：MasterClass — Aaron Sorkin 教编剧 / Shonda Rhimes 教电视剧写作
- 分析：每天拆解 1 部爆款短剧（抖音/快手），用表格列出每 30 秒的"钩子"和"情绪转折"

**实践任务**：
1. 写 1 个 30 集微短剧大纲（每集 200 字梗概）
2. 写 1 集完整剧本（含对话、动作描述、情绪标注）
3. 用 ChatGPT/Claude 辅助扩写，对比 AI 写 vs 自己写的差异

**短剧剧本黄金公式（2026 抖音/快手验证）**：
```
开头 3 秒 = 视觉冲击 + 悬念抛掷
每 30 秒 = 一个反转 / 一个冲突升级
每集结尾 = 强 cliffhanger（"下一秒会发生什么？"）
人物 = 2-3 个核心角色，避免群像
台词 = 短句为主，每句不超过 15 字
```

### 1.2 短视频脚本 & 台词写作

**核心知识点**：
- 竖屏叙事逻辑 vs 横屏电影逻辑
- 台词节奏：AI 短剧台词必须极简（画面 > 台词）
- Hook 设计：前 3 秒决定完播率

**学习资源**：
- 分析抖音爆款短剧的台词密度（用飞书妙记转文字分析）
- 研究 ReelShort / DramaBox 出海短剧的英文字幕节奏

**实践任务**：
1. 把一集剧本里的台词砍掉 50%，用画面替代
2. 写 5 种不同 Hook 开头（冲突型 / 悬念型 / 情感型 / 反转型 / 身份型）

---

## 第二层：视听叙事能力

### 2.1 编导统筹

**核心知识点**：
- 全片节奏控制：快慢交替，情绪曲线设计
- 叙事调度：什么时候给全景、什么时候给特写
- 情绪铺垫：音乐 + 画面 + 台词三层配合

**学习资源**：
- YouTube：Every Frame a Painting 系列（镜头语言分析经典）
- YouTube：Gabe Michael "AI FILMS: How to make a PRO SHORT FILM with AI (2026 Full Tutorial)"
- 书：《导演的摄影课》

**实践任务**：
1. 挑一部 3 分钟 AI 短剧，逐秒标注：这个镜头为什么存在？情绪是什么？
2. 画出你的短剧"情绪曲线图"：横轴=时间，纵轴=情绪强度

### 2.2 分镜设计

**核心知识点**：
- 景别：远景 / 全景 / 中景 / 近景 / 特写 / 大特写
- 运镜：推 / 拉 / 摇 / 移 / 跟 / 升降 / 手持
- 机位角度：平视 / 俯拍 / 仰拍 / 过肩 / POV
- 构图法则：三分法 / 引导线 / 对称 / 留白

**分镜脚本模板**：
```
| 镜号 | 时长 | 景别 | 运镜 | 画面描述 | 台词/旁白 | 音效/BGM | 备注 |
|------|------|------|------|---------|----------|---------|------|
| 001  | 3s   | 中景 | 固定 | 阿强推门进来，脸上有血 | "出事了" | 门吱呀声 | 情绪紧张 |
```

**AI 分镜工具**：
- **Storyboarder.ai**：脚本→分镜自动生成，支持 3D 机位控制
- **Higgsfield Cinema Studio 2.0**：Hero Frame 锚定 + 镜头运动控制
- **Saga**：AI 编剧+分镜一体化
- **Studiovity**：AI 分镜 + 角色一致性锁定
- **Leonardo.Ai**：角色参考 + 场景分镜生成

**实践任务**：
1. 用以上工具之一，把一集剧本转化为 15-20 个分镜
2. 手绘 5 个关键分镜（不需美术功底，火柴人即可），标注景别和运镜方向

### 2.3 摄影摄像 & 镜头语言

**核心知识点**：
- 布光：三点布光（主光/辅光/轮廓光）、自然光、霓虹光
- 色彩体系：互补色 / 类似色 / 冷暖对比
- 焦距：广角（戏剧化）/ 标准（自然）/ 长焦（压缩空间）
- 景深：浅景深（人物突出）/ 深景深（环境叙事）

**AI 生成时的摄影参数规范**：
```
# 摄影机规格模板
Camera: [ARRI Alexa 35 / Sony Venice / RED Komodo]
Lens: [24mm / 35mm / 50mm / 85mm]
Aperture: [f/1.4 浅景深 / f/8 深景深]
Lighting: [golden hour / neon noir / overcast natural / studio softbox]
Color Grade: [teal & orange / desaturated cold / warm vintage]
Film Stock: [Kodak Portra 400 / Fuji Superia / digital clean]
```

**学习资源**：
- YouTube：StudioBinder 频道（摄影基础全系列）
- 书：《电影镜头设计》（Steven Katz）

**实践任务**：
1. 同一场景用 3 种不同摄影参数生成（纪实 / 电影 / 新闻），对比差异
2. 建立你自己的"摄影参数库"：10 种常用光影+镜头组合模板

---

## 第三层：美术设计能力

### 3.1 角色概念设计 & 三视图

**核心知识点**：
- 人物设定的完整要素：外貌 / 体型 / 标志性着装 / 配饰 / 气质 / 行为习惯
- 三视图规范：正面 / 侧面 / 背面 / 3/4 视角
- 角色参考库建立：每个角色一套固定 prompt + 参考图

**角色设定模板**：
```
【角色名】阿强
【年龄/性别】28 岁男性
【身高体型】178cm，偏瘦但精壮，略有驼背
【面部特征】国字脸，单眼皮，左眉有一道 1cm 旧疤，嘴唇偏薄
【发型】黑色短发，略凌乱，右侧有一缕白发（染的）
【标志着装】深灰色连帽卫衣（右袖口磨损），黑色工装裤，左脚白色右脚黑色运动鞋
【配饰】左手戴一串菩提子手串，右耳单只银色耳钉
【气质】阴沉但眼神有温度，走路时习惯低头看地面
【行为习惯】紧张时下意识摸左手手串
```

**角色一致性核心工具（2026）**：
| 工具 | 最佳用途 | 月费 | 一致性评级 |
|------|---------|------|-----------|
| Neolemon | 卡通/插画角色 | $29 | Tier A (95%) |
| Midjourney V7 + --cref | 写实角色 | $30 | Tier B (85%) |
| Leonardo AI | 预算/游戏资产 | $12 | Tier A (80%) |
| Ideogram Char Ref | 写实+文字 | $8+ | Tier A |
| Runway Gen-4 | 视频+分镜 | $15+ | Tier A |
| Nano Banana 2 | 写实角色表 | 免费 | 极高 |

**角色一致性关键技巧**：
1. 先生成角色参考表（正面+侧面+3/4+背面，全身+面部特写）
2. 这个参考表作为后续所有生成的 `--cref` 或参考图
3. 固定 prompt 中角色描述块的词序，永远不改
4. 用 `--cref` 参数（Midjourney）或 Image Reference（Runway/Leonardo）
5. 对于长项目，训练 LoRA（15-20 张角色图微调）

**实践任务**：
1. 设计 3 个角色（主角/反派/配角），各出一套完整三视图
2. 建一个"角色库"文档，每个角色保存参考图 + 固定 prompt 块
3. 测试：用同一套 prompt 在不同场景（白天/夜晚/室内/室外）生成角色，检查一致性

### 3.2 影视美学 & 风格把控

**核心知识点**：
- 纪实新闻风：手持镜头、自然光、低饱和度、略有过曝
- 现实犯罪风：暗调、高对比、青橙色调、大面积阴影
- 写实人像风：柔光、浅景深、暖色调、胶片颗粒
- 电影质感：宽银幕画幅（2.39:1）、精准布光、色彩分级
- 复古黑白：高对比黑白、胶片刮痕、暗角

**风格锁定 prompt 模板**：
```
# 纪实新闻风
documentary style, handheld camera, natural available light,
slightly desaturated, photojournalism, CineStill 800T,
grainy texture, unposed moment

# 现实犯罪风
crime thriller aesthetic, low-key lighting, heavy shadows,
teal and orange color grade, anamorphic lens flare,
rain-slicked streets, neon reflections, ARRI Alexa

# 写实人像风
natural window light, soft shadows, 85mm portrait lens,
f/1.4 shallow depth of field, Kodak Portra 400 film stock,
warm skin tones, minimal retouching
```

**学习资源**：
- 网站：Shotdeck.com（电影截图数据库，按色彩/光影/构图搜索）
- 网站：Film-Grab.com（电影剧照画廊）
- YouTube：The Beauty Of 系列（各电影视觉风格分析）

**实践任务**：
1. 收集 50 张你喜欢的影视截图，建立"视觉参考板"（Pinterest/Milanote）
2. 同一画面用 4 种不同风格生成，标注每种风格的核心参数

---

## 第四层：AI 专业技能

### 4.1 顶级提示词工程

**核心知识点**：
- 结构化提示词：JSON 格式 / 模板化 / 参数化
- 负面词（Negative Prompt）：精确剔除 AI 常见瑕疵
- Chain of Thought Prompting：复杂场景分步推理
- 元提示词（Meta Prompting）：用 LLM 生成提示词
- Few-shot Prompting：给 AI 看参考案例
- 提示词版本控制：像代码一样管理 prompt

**结构化提示词模板（JSON 格式，2026 业界标准）**：
```json
{
  "scene_id": "S01E03_SHOT_005",
  "character": {
    "name": "陈警官",
    "ref_id": "CHAR_002",
    "outfit": "dark blue police uniform, badge number 0427",
    "expression": "tense, furrowed brow, lips pressed tight"
  },
  "environment": {
    "location": "interrogation room",
    "time": "night, 11PM",
    "details": "concrete walls, single overhead fluorescent light flickering, metal table, two chairs"
  },
  "camera": {
    "shot_type": "medium close-up",
    "angle": "eye level, slight dutch tilt (3°)",
    "lens": "50mm prime",
    "aperture": "f/2.8",
    "movement": "static, subtle handheld micro-shake"
  },
  "lighting": {
    "style": "single source top light, harsh shadows on face",
    "color_temp": "cool fluorescent 4000K",
    "contrast": "high, chiaroscuro"
  },
  "style": {
    "genre": "crime thriller",
    "color_grade": "desaturated blue-green",
    "film_stock": "Kodak Vision3 500T",
    "aspect_ratio": "2.39:1"
  },
  "negative_prompt": "soft lighting, smiling, warm colors, overexposed, cartoon, anime, 3D render, plastic skin, extra fingers, deformed hands, blurry, text, watermark"
}
```

**各场景专属提示词范式**：
1. **角色三视图**：`character design sheet, turnaround, front view + side profile + back view + 3/4 view, full body + face close-up, neutral studio lighting, plain white/grey background, consistent proportions`
2. **写实人像**：`cinematic portrait, [character description], natural window light, 85mm f/1.4, shallow depth of field, Kodak Portra 400, subtle film grain, minimal retouching, authentic skin texture`
3. **纪实新闻**：`documentary photojournalism, [scene], available light, handheld, CineStill 800T pushed, slight underexposure, grainy, unposed, vérité style`
4. **影视镜头**：`cinematic shot from [movie title] style, [scene], ARRI Alexa 35, anamorphic 2.39:1, [lens], [lighting], [color grade], film grain, 24fps motion blur`

**学习资源**：
- YouTube：Prompt Engineering in 2026 — What Still Matters (And What Doesn't)
- 社区：r/PromptEngineering (Reddit)
- 工具：PromptBase（买卖提示词的市场，观察顶级 prompt 怎么写）

**实践任务**：
1. 建一个"提示词武器库"文档，分场景/分模型/分风格存放
2. 每条提示词记录：生成结果截图 + 效果评分 + 改进方向
3. 每周迭代优化 5 条核心提示词

### 4.2 AI 视频大模型底层思维

**核心知识点**：
- 扩散模型原理：从噪声逐步去噪生成画面
- 文本→视频 vs 图像→视频的差异：图生视频更可控
- 镜头连贯性原理：为什么 AI 角色会"变脸"（每帧独立采样）
- 时序一致性（Temporal Consistency）：模型无记忆，每帧重新诠释角色
- 各模型特性对比：

| 模型 | 优势 | 劣势 | 最佳场景 |
|------|------|------|---------|
| Runway Gen-4 | 角色一致性最佳、Multi-Motion Brush | 生成速度慢 | 叙事短片 |
| Kling AI 3.0 | 写实运动、面部细节 | 复杂场景漂移 | 写实人物 |
| Seedance 2.0 | 角色数字身份绑定（跨集一致） | 生态封闭 | 系列短剧 |
| Veo 3.1 (Google) | 光影真实、物理模拟 | 限流严格 | 高质感镜头 |
| Sora 2 | 画质最高、时长最长 | 已关停 (2026.3) | - |
| Jimeng AI | 字节生态、创作者激励 | 需抖音账号 | 抖音短剧 |
| Pika / Luma | 单镜头效果好 | 连续性差 | 短 GIF/广告 |

**2026 行业关键数据**：
- 中国短剧市场：2025 年 $140 亿 → 2026 年预计 $165 亿，超过中国电影总票房
- Q1 2026：约 128,000 部微短剧上线，其中 95% 是 AI 生成
- AI 短剧制作成本降至传统拍摄的 1/10
- 制作周期从 3 个月压缩到 1 个月（AI 工作流占预算 30%）
- AI 生成素材可用率已超 90%

**学习资源**：
- 论文：Vibe AIGC: A New Paradigm for Content Generation via Agentic Orchestration (arXiv 2602.04575)
- YouTube：各模型官方教程频道
- 社区：AI Filmmaking Discord / Reddit

**实践任务**：
1. 同一个 prompt 分别在 Kling AI / Runway / Veo 3 各生成一次，对比输出差异
2. 写一份"AI 视频模型选型决策表"：什么场景用什么模型
3. 建立"模型特性笔记"：每次使用后记录模型表现

### 4.3 AI 缺陷修正与容错思维

**AI 短剧 7 大常见缺陷 & 修复方案**：

| 缺陷 | 表现 | 根因 | 修复方案 |
|------|------|------|---------|
| 角色崩坏（Melting Face） | 五官逐渐变形 | 模型每帧重新诠释角色 | ①图像参考锚定 ②3-5 张多角度参考图 ③prompt 加 "same character throughout" |
| 手部畸形（Hand Horror） | 手指数量不对/融合 | 手部几何复杂 | ①构图避开手 ②慢动作模式 ③Topaz Video AI 后期修复 |
| 时序闪烁（Temporal Flicker） | 背景闪烁/纹理抖动 | 帧间光照计算不一致 | ①加 "ultra-stable, zero flicker, strong temporal consistency" ②DaVinci Resolve 去闪烁 ③缩短单次生成时长 |
| 塑料皮肤（Plastic Skin） | 皮肤过光滑不真实 | 模型训练数据偏光滑 | ①加 "authentic skin texture, visible pores, micro-imperfections" ②后期加胶片颗粒 |
| 文本/Logo 扭曲 | 文字弯曲/消失 | 模型不擅长文字渲染 | ①避免 AI 生成文字 ②后期加字幕/Logo |
| 肤色/光影跳变 | 连续镜头间色调不一致 | 每段独立生成 | ①锁定参考图 ②DaVinci Resolve 色彩匹配 ③统一 prompt 色温参数 |
| 衣物/背景细节漂移 | 服装图案变化/背景元素增减 | 模型无持久记忆 | ①固定 prompt 衣物描述 ②128 字以上详细环境描述 ③LoRA 微调 |

**修复工具箱**：
- **Topaz Video AI**：AI 视频增强/去闪烁/超分辨率
- **DaVinci Resolve**：色彩匹配/胶片颗粒/稳定/去闪烁（免费版足够）
- **Runway**：Inpainting 修复局部
- **Magnific AI**：图像超分辨率增强

**预防式 Prompt（Anti-Flicker）**：
```
"Ultra-stable video. Zero flicker. Strong temporal consistency.
No frame jitter, no morphing, no detail popping.
Consistent lighting throughout. Character features remain identical every frame.
No face deformation, no hand mutation, no background shift."
```

**实践任务**：
1. 收集你生成过的所有失败案例，归类到缺陷表格中
2. 每个缺陷写 3 种修复方案，实测出最有效的
3. 建立"AI 缺陷修复 SOP"文档

---

## 第五层：工程落地能力

### 5.1 多模态工具协同工作流

**标准 AIGC 短剧生产线（2026 业界最佳实践）**：

```
阶段一：策划 (1-2天)
├── LLM (ChatGPT/Claude) → 剧本大纲 + 人物设定
├── LLM → 分集梗概
└── LLM → 每集完整剧本

阶段二：视觉开发 (2-3天)
├── Midjourney/Nano Banana 2 → 角色三视图参考表
├── Midjourney → 场景/道具概念图
├── Leonardo AI → 分镜生成
└── 建立角色锚定图库（每角色 5-8 张多角度图）

阶段三：镜头生成 (3-5天) ← 最耗时
├── Runway Gen-4 / Kling 3.0 → 图生视频（主角镜头）
├── Seedance 2.0 → 角色一致性锁定镜头
├── Veo 3.1 → 高质感环境镜头
└── Pika/Luma → 过渡镜头/GIF

阶段四：音频制作 (1-2天)
├── ElevenLabs → AI 配音（多角色）
├── Suno/Udio → 背景音乐生成
├── ElevenLabs SFX / Kling → 音效生成
└── 音频混音

阶段五：后期整合 (2-3天)
├── CapCut/DaVinci Resolve → 剪辑拼接
├── DaVinci Resolve → 色彩统一调色
├── CapCut → 字幕排版 + 竖屏适配
├── Topaz Video AI → 去闪烁/超分
└── 成片渲染输出

阶段六：发布优化 (1天)
├── 多个 Hook 版本（前 3 秒变体）
├── 多平台适配（抖音竖屏 / B站横屏 / YouTube）
├── 封面图 AI 生成
└── 标题 A/B 测试
```

**工具链关键组合（2026 验证）**：
- 入门组合：Nano Banana 2（角色）+ Kling AI（视频）+ CapCut（剪辑）+ ElevenLabs（配音）
- 专业组合：Midjourney V7（角色）+ Runway Gen-4（视频）+ DaVinci Resolve（后期）+ ElevenLabs Pro（配音）
- 出海组合：Seedance 2.0（角色锁定）+ Kling 3.0（视频）+ CapCut Pro（剪辑）+ HeyGen（多语言口型同步）

### 5.2 Agent 自动化搭建

**可自动化的环节**：
1. 批量提示词生成：用 LLM API 自动生成每镜头的结构化 prompt
2. 角色参考图管理：建数据库自动匹配场景→角色→参考图
3. 素材管理：自动编号/标注/分类生成素材
4. 质量检查：自动检测手部崩坏/角色变脸/闪烁→自动重新生成

**实践任务**：
1. 用 Python 写一个"批量 prompt 生成器"：输入剧本，输出每镜头 JSON prompt
2. 建一个素材管理文件夹结构规范
3. 设计 AIGC 小树 Agent 的完整工作流架构图

---

## 第六层：成片产出能力

### 6.1 后期剪辑 & 视听整合

**核心工具**：

| 工具 | 定位 | 价格 | 核心 AI 功能 |
|------|------|------|-------------|
| DaVinci Resolve | 专业全流程 | 免费 / $295 买断 | Magic Mask、语音隔离、智能场景检测、AI 调色 |
| CapCut Pro | 短内容快剪 | $7.99/月 | 自动字幕、背景移除、AI 风格迁移、模板 |
| Premiere Pro | 行业标准 | $22.99/月 | AI 跟踪遮罩、语音转文字、自动色彩匹配 |
| Topaz Video AI | AI 增强 | $299 买断 | 超分辨率、去闪烁、帧插值、降噪 |
| Runway | AI 特效 | $15+/月 | 视频 Inpainting、绿幕、运动追踪 |

**剪辑工作流 SOP**：
1. 导入所有 AI 生成镜头（按镜号命名：`S01E01_SHOT_001.mp4`）
2. 粗剪：按分镜脚本排列，删掉失败镜头
3. 精剪：调整每个镜头的入点/出点，控制节奏
4. 转场：硬切为主（电影感），慎用花哨转场
5. 调色：DaVinci Resolve 全局风格统一 + 镜头间色彩匹配
6. 字幕：CapCut 自动生成 + 手动校对 + 动态字幕排版
7. 音频：配音 + BGM + 音效三层混音
8. 输出：竖屏 1080x1920 / 横屏 1920x1080，H.265 编码

### 6.2 音频制作

**配音工具**：
- ElevenLabs（最佳中文语音，多角色支持）
- Respeecher（高端语音克隆）
- HeyGen（口型同步 + 多语言）

**配乐工具**：
- Suno AI（歌曲生成）
- Udio（背景音乐）
- Mubert（免版权 BGM）

**音效工具**：
- ElevenLabs SFX
- Adobe Firefly AI SFX
- Kling AI 内置音效

**实践任务**：
1. 用 ElevenLabs 生成 3 个角色配音（不同声线）
2. 用 Suno 生成一段 30 秒紧张氛围 BGM
3. 把配音+BGM+画面合成一个 60 秒片段

---

## 第七层：商业落地能力

### 7.1 短剧流量逻辑（2026 最新数据）

**中国市场**：
- 短剧市场 2025 年 $140 亿 → 2026 年预计 $165 亿
- 6.6 亿中国用户日常观看短剧
- 抖音单月新增 50,000 集短剧
- Q1 2026 上线 128,000 部微短剧，95% AI 生成
- 1 月单月新增 14,600+ 部 AI 短剧（每天约 470 部）

**盈利能力剧变**：
- ⚠️ 抖音红果短剧分成从 30-100 元/万有效播放 → 降至 5-10 元（下跌 90%+）
- ⚠️ 2026 年 5 月起，AI 拟人剧本取消保底收益，转为纯分成（20%）
- ⚠️ 128,000 部中，只有 0.117% 破亿播放
- ✅ 出海市场爆发：TikTok 短剧激励最高 20 倍流量
- ✅ 出海 Q1 2026 创作者分成超 $2400 万，AI 短剧月增 300%+

**爆款公式（2026）**：
```
爆款 = 强钩子（前3秒完播率 > 60%）
      × 高密度反转（每30秒一次）
      × 情感共鸣（代入感 > 视觉质量）
      × 正确分发（平台算法匹配）
```

### 7.2 合规 & 内容尺度

**国内平台红线**：
- 禁止：暴力血腥、色情低俗、政治敏感、赌博诈骗
- 限制：恐怖惊悚（需标注）、负面价值观引导
- 备案：网络微短剧需取得《网络剧片发行许可证》或平台备案号（2025 年新规）

**海外平台规则**：
- TikTok：社区准则 + 短剧专门内容政策
- YouTube：AdSense 友好内容指南
- ReelShort/DramaBox：平台自有审核标准

### 7.3 创业商业化路径

**变现模式**：
1. 平台分成：抖音/快手/TikTok 播放分成（利润已大幅压缩）
2. 品牌定制：为企业拍摄 AI 短剧广告（单集 ¥5,000-50,000）
3. IP 授权：爆款角色/IP 授权衍生
4. 出海分发：国内制作 → 翻译 → 海外平台分发（当前最大机会）
5. 工具+服务：AI 短剧制作全流程服务/咨询

**关键建议**：
- 国内短剧分成已近天花板，出海是 2026-2027 最大机会
- 建立差异化：不做流水线 AI 短剧（每天 470 部竞争），做有辨识度的风格/IP
- 成本优势是短期壁垒，IP/品牌才是长期壁垒

---

## 30 天学习路径（建议执行顺序）

### 第 1 周：地基搭建
- Day 1-2：学习编剧基础（三幕剧/节拍表），分析 10 部爆款短剧结构
- Day 3-4：学习镜头语言（景别/运镜/构图），建视觉参考库
- Day 5-6：学习分镜设计，手绘 + AI 工具各画 10 个分镜
- Day 7：设计你的第一个角色（完整人物设定+三视图 prompt）

### 第 2 周：AI 工具上手
- Day 8-9：Midjourney/Nano Banana 角色生成 + 一致性测试
- Day 10-11：Kling AI/Runway 视频生成 + 提示词工程
- Day 12-13：ElevenLabs 配音 + Suno 配乐
- Day 14：CapCut/DaVinci Resolve 基础剪辑

### 第 3 周：全流程实战
- Day 15-17：写一个 5 集微短剧完整剧本
- Day 18-20：生成所有镜头素材（角色+场景+视频）
- Day 21：后期整合（配音+剪辑+调色+字幕）

### 第 4 周：打磨 & 商业化
- Day 22-23：修复 AI 瑕疵，优化视觉效果
- Day 24-25：制作多个 Hook 版本，准备分发
- Day 26-27：研究平台规则和变现路径
- Day 28-30：复盘总结，迭代工作流，规划下一个项目

---

## 持续学习资源总汇

### 网站/平台
- Shotdeck.com（电影截图数据库）
- Film-Grab.com（电影剧照）
- StudioBinder（摄影教程）
- No Film School（独立电影制作）
- MCPlato（AI 创作工具分析）

### YouTube 频道
- Gabe Michael（AI 电影制作全流程）
- AI Samson（AI 角色一致性技巧）
- Tao Prompts（提示词工程）
- Higgsfield AI（AI 视频工作流）
- Every Frame a Painting（镜头语言经典）

### 书单
- 《救猫咪》— Blake Snyder（编剧圣经）
- 《故事》— Robert McKee（叙事原理）
- 《电影镜头设计》— Steven Katz
- 《导演的摄影课》

### 社区
- r/PromptEngineering (Reddit)
- r/aivideo (Reddit)
- AI Filmmaking Discord
- 即刻/小红书 AIGC 创作者社群

---

## 关键认知总结

1. **AI 是工具，不是创作者**。好故事 > 好技术。所有 AI 能力服务于叙事，反过来就本末倒置。

2. **角色一致性是 AIGC 短剧的"第一性原理"**。解决了角色锁定，就解决了 80% 的产出质量。

3. **工作流 > 单个工具**。没有一个模型能打全场。赢在"用什么工具解决什么问题"的判断力。

4. **后期是 AI 短剧的"最后一公里"**。AI 产出碎片化素材，靠剪辑和调色拼成完整作品。

5. **国内分成见顶，出海是 2026-2027 最大窗口期**。TikTok 短剧生态正在复制抖音 2023-2024 的红利期。

6. **工业化思维**：从"做一个作品"升级到"建一条产线"。模板化、流程化、自动化。

---

> 小树注：此文档为学习路径指南，具体执行根据实际情况调整。各项工具和市场价格随时间变化，以实际使用时的最新信息为准。
> 更新于 2026-06-26（添加交叉引用）

---

## 📚 相关文档

- [[AIGC知识体系-总索引]] — 全部 AIGC 知识导航
- [[AIGC导演视听语言框架]] — 核心导演框架 v3.1
- [[AIGC提示词翻译引擎规则库]] — 意图→prompt 转化规则
- [[AIGC画布深度学习-终极大总结]] — 238 项目 7 个核心发现
- [[AIGC提示词模式库-22584条分析]] — 22,584 条统计分析
