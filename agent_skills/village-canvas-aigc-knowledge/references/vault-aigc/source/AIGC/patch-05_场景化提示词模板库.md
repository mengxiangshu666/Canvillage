# 补丁05 — 场景化提示词模板库

> 来源：LOTW 120 Prompts Library (2026-01) + AI Academy 30 Best Video Prompts (2026) + Evolink Seedance Prompts (2026) + Tao Prompts Chase Scene Tutorial (2026)
> 使用方法：直接复制对应场景的提示词，替换角色名/地点名等变量

---

## 一、通用提示词模板

```
[格式 16:9/9:16] [时长] [场景/环境] 中 [主体] [动作描述]，
[镜头景别+运动]，[灯光+色调]，[情绪/风格]，[声音方向]
```

---

## 二、场景化模板

### B1 雨夜霓虹街

```
16:9, 8 seconds. A lone figure walking through a neon-lit street in the rain,
reflections on wet pavement, slow dolly-in, cinematic lighting,
realistic rain motion, subtle film grain. Low ambient city hum.
```

变体：单人漫步 / 奔跑追逐 / 站着等车

### B2 赛博朋克追逐

```
9:16, 7 seconds. Futuristic street with holographic signs, rain,
fast push-in, vibrant neon reflections, high energy.
Swift footsteps splashing on wet pavement, distant police siren.
```

变体：第一人称奔跑 / 第三人称追 / 车辆追逐

### B3 医院重症监护室

```
16:9, 10 seconds. Inside a quiet hospital ICU at night, dimmed monitors,
slow pan across bed rails, beeping equipment in background,
cold fluorescent lighting, sterile clinical atmosphere.
Heart monitor beeping steady, faint mechanical ventilation.
```

变体：走廊空镜 / 护士查房 / 病人苏醒

### B4 雨中分手

```
16:9, 8 seconds. Medium shot, two people standing in the rain facing each other,
umbrella dropped on the ground, water dripping down faces,
moody teal/blue color grade, emotional tension.
Raindrops hitting pavement, melancholic piano note.
```

变体：转身离开 / 最后拥抱 / 隔着窗户

### B5 复古 diner 场景

```
16:9, 10 seconds. Inside a retro diner at night, warm tungsten lights,
slow pan across booths, steam from coffee,
realistic reflections on chrome, cinematic tone.
Jukebox playing faintly in background.
```

变体：窗边独坐 / 吧台对话 / 服务员擦杯子

### B6 雾中森林

```
16:9, 8 seconds. Camera glides through a foggy pine forest at dawn,
sun rays piercing mist, smooth steadycam,
soft shadows, atmospheric cinematic look.
Birdsong, wind rustling leaves.
```

变体：奔跑穿过 / 静静站着 / 迷雾散去

### B7 卧室晨光

```
16:9, 8 seconds. Sunbeams through half-open curtains into a messy bedroom,
dust particles floating, slow camera drift,
warm golden hour lighting, intimate and quiet.
Faint traffic from outside.
```

变体：醒来伸懒腰 / 床边独坐 / 收拾行李

### B8 厨房烹饪

```
16:9, 6 seconds. Macro shot of a wok flame rising as vegetables toss,
steam, heat shimmer, close-up, cinematic food film style.
Sizzling sounds, knife chopping wood.
```

变体：切菜特写 / 汤汁沸腾 / 餐桌摆盘

### B9 科幻走廊

```
16:9, 8 seconds. A futuristic corridor with flickering lights,
slow tracking shot, volumetric haze,
reflective metal surfaces, cinematic sci-fi.
Hum of machinery, electrical buzz.
```

变体：空旷 / 有人跑过 / 门打开

### B10 黄昏海滩

```
16:9, 8 seconds. Waves crashing on shore at golden hour,
a silhouette walking on the wet sand, wide shot,
soft warm light, peaceful.
Ocean waves, distant seagulls.
```

变体：两人并肩 / 日落延时 / 冲向海水

---

## 三、广告场景

| 场景 | 提示词示例 |
|------|----------|
| 产品旋转展示 | "1:1 square, 6 seconds. A soda can rotating on a turntable, softbox highlights, clean reflections, perfectly consistent logo, premium commercial style, loopable." |
| 第一人称开箱 | "Vertical 9:16, 9 seconds. Hands unbox a premium gadget on a wooden desk, clean top-down shot, crisp packaging sounds implied, smooth pacing, product centered." |
| 慢动作倒水 | "Vertical 9:16, 7 seconds. A slow-motion pour of sparkling water into a glass with ice, crisp bubbles, realistic refraction, soft studio backlight, clean background, smooth camera push-in." |
| 化妆特写 | "Vertical 9:16, 7 seconds. Close-up eyeliner application, crisp details, soft ring light reflection, beauty influencer vibe." |
| 食品特写 | "Macro product photography of a lipstick with creamy texture visible, shallow depth of field, glossy highlights, luxury beauty ad lighting, clean background, no clutter." |

---

## 四、使用建议

1. **每次替换** `[主体]`、`[地点]` 等变量
2. **根据模型调整**：
   - Seedance：加音频分层 `"Dialogue clean and prominent"`
   - Kling：明确 `"Single shot, no cuts"`
   - Veo：加 `"Physics cues: shadows, reflections, motion blur"`
3. **同一场景多次生成**：每次只改一个词，看出片差异
4. **合成场景**：把多个模板组合成一段视频的分镜

---

## 来源

- [120 Copy-Paste Prompts for AI Image + Video 2026](https://www.lordofthewix.com/post/image-video-prompt-library-2026-120-copy-paste-prompt-for-ai-image-video-runway-veo-sora-style) — Lord of the Wix, Jan 2026
- [30 Best AI Video Prompts 2026](https://academy.techpresso.co/prompts/ai-video-prompts) — AI Academy, 2026
- [Seedance 2.0 Prompts Video Examples](https://evolink.ai/seedance-2-0-prompts) — Evolink, 2026
- [How to Create an Epic Chase Scene with AI](https://www.youtube.com/watch?v=A5QVQEaia9k) — Tao Prompts, Mar 2026