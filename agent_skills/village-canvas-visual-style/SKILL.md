---
name: village-canvas-visual-style
description: 全画风参数体系——10大类60+画风、8层可控制参数、画风混合矩阵。来源：兄弟 AIGC 知识包文件12（2026-07-01）。AIGC导演的风格调色板。
triggers:
  - 选视觉风格
  - 选画风
  - 混合画风
  - 设计质感
  - 画风参数
---

# 全画风参数体系

> 来源：兄弟 AIGC 知识包·文件12 · 2026-07-01
> 关键：任何风格都能做的参数基础——AIGC导演的风格调色板

---

## 一、画风 8 层参数模型

每个画风 = 8 个可独立控制的参数层（NovelAI + Neolemon 研究）：

| 层 | 参数 | AIGC 关键词 |
|----|------|------------|
| 1. **画幅格式** | 插图/3D渲染/漫画格/海报/角色表 | illustration, 3d render, comic panel, poster, character sheet |
| 2. **媒介** | 水彩/油画/水墨/矢量/黏土/像素 | watercolor, oil painting, ink wash, vector, clay, pixel art |
| 3. **线稿** | 粗细/有无/笔触类型 | thick outline, thin pencil, brush pen, lineless |
| 4. **着色** | 平涂/渐变/排线/网点 | flat cel shading, soft painterly, crosshatching, halftone |
| 5. **色板** | 色域/饱和度/色彩数量 | pastel, muted earth tones, neon, limited 3-color, monochrome |
| 6. **材质** | 纸张/画布/噪点 | paper grain, canvas weave, screenprint noise, clean flat |
| 7. **光照** | 光源/氛围 | soft daylight, golden hour, moody neon, rim light |
| 8. **构图** | 布局/视角 | storybook framing, centered, dynamic action shot |

---

## 二、10 大类 60+ 画风

### 类别1：写实/纪实（Realism）

| 画风 | 关键词 | 适用模型 |
|------|--------|---------|
| **照片写实** | photorealistic, hyperrealistic, 8K, sharp focus, detailed skin texture | nebula-ultra, mj-v8.1 |
| **电影感纪实** | cinematic, ARRI Alexa, 35mm film grain, Kodak Vision3, Roger Deakins | mj-v8.1 |
| **纪录片风** | documentary style, available light, naturalistic, candid framing, handheld | mj-v8.1 |
| **街头摄影** | street photography, snapshot aesthetic, 28mm, high contrast, grainy | mj-v8.1 |
| **暗调人像** | low-key portrait, Rembrandt lighting, deep shadows, single light source | nebula-ultra |

### 类别2：动漫/日式（Anime）

| 画风 | 关键词 | 年代 |
|------|--------|------|
| **少年漫**（Shōnen）| shonen anime, dynamic action, bold outlines, speed lines, vibrant | 1990s-now |
| **少女漫**（Shōjo）| shojo manga, delicate linework, soft tones, sparkle effects, floral | 1970s-now |
| **吉卜力/宫崎骏** | painterly anime backgrounds, warm light, hand-painted texture, soft cel shading | — |
| **赛璐珞**（Cel Anime）| cel shaded anime, 1990s anime screencap, hand-painted cels | 1980s-90s |
| **新世纪福音战士** | Neon Genesis Evangelion style, psychological, angular faces, muted palettes | 1995 |
| **阿基拉风** | Akira style, detailed hand-drawn, cyberpunk, motion lines, gritty | 1988 |
| **Q版**（Chibi）| chibi, super deformed, oversized head, tiny body, cute | — |
| **机甲**（Mecha）| mecha anime, hard surface, detailed mechanical joints, metallic, sci-fi | — |

**动漫AIGC模板**：
```
[主体描述], [画风标签], [年代标签],
cel shaded, clean linework, flat colors,
[色板: pastel / vibrant / muted],
[Screencap / illustration / character sheet]
```

### 类别3：卡通/儿童（Cartoon）

| 画风 | 关键词 |
|------|--------|
| **皮克斯/迪士尼3D** | Pixar style, 3D render, soft shadows, warm lighting, rounded forms |
| **定格动画** | stop motion, claymation, puppet, textured surface |
| **连环画** | comic book style, bold colors, halftone dots, panel composition |
| **矢量扁平** | flat vector illustration, minimal, clean shapes, solid colors |
| **涂鸦风** | doodle style, sketch lines, playful, hand-drawn |

### 类别4：艺术/绘画（Fine Art）

| 画风 | 关键词 |
|------|--------|
| **油画** | oil painting, impasto, visible brushstrokes, classical |
| **水彩** | watercolor, soft edges, bleeding colors, paper texture |
| **水墨** | Chinese ink wash, sumi-e, brush and ink, minimalist |
| **素描** | graphite pencil, charcoal, crosshatching, black and white |
| **版画** | woodblock print, ukiyo-e, bold outlines, flat colors |
| **拼贴** | collage, mixed media, torn paper, layered textures |

### 类别5：科幻/未来（Sci-Fi）

| 画风 | 关键词 |
|------|--------|
| **赛博朋克** | cyberpunk, neon lights, rain-soaked streets, holographic, dark |
| **蒸汽朋克** | steampunk, brass gears, Victorian, steam-powered, copper tones |
| **太空歌剧** | space opera, epic scale, starfields, alien worlds, cinematic |
| **反乌托邦** | dystopian, oppressive architecture, muted colors, authoritarian |
| **赛博格** | cyborg, mechanical augmentation, chrome, red accents |
| **虚拟现实** | VR, digital grid, wireframe, glowing edges, sterile |

### 类别6：奇幻/魔幻（Fantasy）

| 画风 | 关键词 |
|------|--------|
| **暗黑奇幻** | dark fantasy, gothic, ominous, deep shadows, medieval |
| **剑与魔法** | swords and sorcery, vibrant fantasy, heroic, magical glow |
| **精灵风** | elven, ethereal, flowing robes, nature magic, serene |
| **龙与地下城** | D&D style, character sheet, parchment, fantasy map |
| **童话风** | fairy tale, storybook illustration, soft colors, whimsical |

### 类别7：历史/古典（Historical）

| 画风 | 关键词 |
|------|--------|
| **文艺复兴** | Renaissance painting, classical proportions, religious iconography, oil |
| **巴洛克** | Baroque, dramatic lighting, rich colors, ornate, dynamic composition |
| **浮世绘** | ukiyo-e, Japanese woodblock print, flat colors, bold outlines |
| **古埃及** | ancient Egyptian, hieroglyphics, gold, papyrus texture, flat figures |
| **维多利亚** | Victorian era, sepia tones, ornate details, gaslight |
| **民国风** | 1930s China, vintage Shanghai, art deco, faded photograph |

### 类别8：数字艺术/当代（Digital & Contemporary）

| 画风 | 关键词 |
|------|--------|
| **概念艺术** | concept art, matte painting, film production, highly detailed |
| **数字绘画** | digital painting, wacom, painterly, soft light |
| **故障艺术** | glitch art, digital corruption, scan lines, RGB split |
| **像素艺术** | pixel art, 8-bit, retro game, limited colors |
| **酸性艺术** | acid graphics, y2k, metallic, holographic, cyber aesthetic |
| **极简主义** | minimalist, clean, stark, geometric, limited palette |

### 类别9：建筑/室内（Architecture & Interior）

| 画风 | 关键词 |
|------|--------|
| **建筑摄影** | architectural photography, clean lines, geometric, perfect symmetry |
| **室内设计** | interior design, cozy, warm lighting, lifestyle |
| **包豪斯** | Bauhaus, geometric, primary colors, functional |
| **后现代建筑** | postmodern architecture, curves, deconstructivist, bold forms |
| **废墟** | abandoned, ruins, overgrown, decayed, post-apocalyptic |

### 类别10：人像/时尚（Portrait & Fashion）

| 画风 | 关键词 |
|------|--------|
| **时尚杂志** | fashion editorial, high contrast, studio lighting, magazine cover |
| **复古海报** | vintage poster, Art Deco, typography, bold geometric |
| **宝丽来** | Polaroid, light leak, film grain, faded colors, instant camera |
| **黑白肖像** | black and white portrait, high key, dramatic shadows, timeless |
| **水下人像** | underwater portrait, blue tones, floaty hair, ethereal light |

---

## 三、画风混合矩阵

**原则**：选择1个主画风 + 1个次画风点缀，不要超过2种画风混合

| 主画风 | 配 | 效果 |
|--------|----|------|
| 照片写实 | + 吉卜力 | 宫崎骏式真实场景 |
| 赛璐珞动漫 | + 油画 | 古典动漫人物 |
| 赛博朋克 | + 浮世绘 | 霓虹浮世绘 |
| 概念艺术 | + 水墨 | 东方科幻 |
| 宝丽来 | + 街头摄影 | 复古街拍 |
| 油画 | + 暗黑奇幻 | 古典暗黑 |

---

## 四、质感与瑕疵参数

### 胶片质感
| 质感 | 关键词 |
|------|--------|
| 35mm电影 | `35mm film, cinema grain, Kodak Vision3` |
| 16mm纪实 | `16mm film, verite, handheld feel` |
| 8mm怀旧 | `8mm film, lo-fi, vignette, light leaks` |
| 过期胶片 | `expired film, color shift, grain, faded` |
| 数码电影 | `digital cinema, ARRI Alexa, clean` |

### 瑕疵美（大黑耗子）
> "完美是AI的弱点，不完美才是电影的优势"

| 瑕疵 | 关键词 | 适用 |
|------|--------|------|
| 漏光 | `light leak, film light leak` | 怀旧/梦境 |
| 暗角 | `vignette, dark corners` | 聚焦/压迫 |
| 颗粒 | `film grain, ISO 3200` | 真实/纪实 |
| 划痕 | `film scratches, damaged film` | 老档案/噩梦 |
| 偏色 | `color cast, warm tone` | 情绪氛围 |

---

## 五、画风选择决策树

```
1. 写实/纪实？
   → Yes: 照片写实 / 电影感纪实 / 纪录片风
   → No: ↓

2. 有角色？
   → Yes: 动漫 / 卡通 / 人像
   → No: ↓

3. 奇幻/科幻元素？
   → Yes: 奇幻 / 科幻 / 数字艺术
   → No: ↓

4. 艺术/古典？
   → Yes: 艺术绘画 / 历史 / 建筑
   → No: ↓

5. 创意/实验？
   → Yes: 故障艺术 / 像素 / 酸性
   → No: 概念艺术
```

