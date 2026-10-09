# 补丁04 — AI 视频扩展 / 帧插值 / 慢动作

> 来源：Morphic Frame Interpolation Glossary (2026) + Topaz Labs AI (2026) + Data Science Collective Playbook (2026) + Morphic Seedance Guide (2026)
> 交叉验证：扩展 + 插值 + 慢动作 三技术分别来自不同来源，方案一致

---

## 一、超过模型最大时长怎么办？

当前模型上限：
- Seedance 2.0: 15s
- Seedance 2.5（预览）: 30s
- Kling 3.0: 10s
- Wan 2.6: 15s
- Veo 3.1: 8s

### 方案 A：视频扩展

Seedance 2.0 原生支持扩展：

**标准扩展**
```
Extend @Video 1 by 5 seconds. [新动作描述]
```

- 用前一段的最后一帧作为新扩展的起始帧
- 先在 Seedance 里用扩展功能拼接，不用自己手动裁切
- 画面中的场景灯光保持连续

**扩展 vs 重生成**

| | 扩展 | 重新生成 |
|--|------|---------|
| 动作连贯性 | ✅ 保持 | ❌ 断档 |
| 灯光一致性 | ✅ 保持 | ❌ 可能变 |
| 角色一致性 | ✅ 更好 | ⚠️ 不稳 |
| 灵活性 | ⚠️ 受前段限制 | ✅ 全新 |

**推荐流程：** 先用 Seedance 扩展搭长镜头框架，再用关键帧对齐拼接处。

### 方案 B：外部拼接

1. 生成多个 5-10 秒片段
2. 确保每个片段的最后一个字符位置匹配下个片段的第一帧
3. 剪辑软件中拼接
4. 拼接痕用转场（淡入淡出、交叉溶解）掩盖

对于长对话：正反打交替剪辑，比长镜头更容易保持一致性。

---

## 二、帧插值（Frame Interpolation）

### 什么是帧插值

生成现有帧之间的新帧，把 24fps 变成 48fps 或 60fps。

### 工具

| 工具 | 可达到帧率 | 说明 |
|------|----------|------|
| **Topaz Labs** | 最高 60fps | 专业级，AI 运动预测算法最好 |
| **DAIN / RIFE** | 可变 | 开源稳定，适合批量处理 |
| **Runway** | 最高 48fps | 内置在剪辑管线中 |
| **DaVinci Resolve** | 最高 60fps | Speed Warp 功能，免费版即可 |

### 效果

- 标准 24fps → 24fps（原生，最自然）
- 社交媒体 → 30fps（兼容性最好）
- 慢动作 → 60fps（插值后的慢动作更平滑）

---

## 三、AI 慢动作

### 生成慢动作的方法

**方法 1：提示词直接生成**
```
"Slow motion, 60fps equivalent, [动作描述], smooth ..."
```
大部分模型理解 slow motion，但实际效果取决于模型。
- Kling 3.0：慢动作能力最强
- Seedance 2.0：自然运动中的慢动作可以，大幅降速不稳

**方法 2：后期帧插值**
1. 正常速度生成 8s 视频
2. 用 Topaz Labs / DAIN 插帧到 60fps
3. 在剪辑软件里放慢到 50% 速度
4. 最终长度从 8s 变成 16s（60fps 插值后降速 50% 仍流畅）

**方法 3：高帧率生成 + 降速**
某些模型（Kling O3, Veo 3.1）可以生成接近 60fps 素材，直接放慢即可。

### 慢动作最佳场景

| 场景 | 适合方法 |
|------|---------|
| 水花/液体飞溅 | 后期插值（效果最稳定） |
| 人物奔跑/跳跃 | 提示词直接生成（Kling） |
| 微表情/眼神 | 高帧率原生（Veo） |
| 爆炸/火焰 | 后期插值（控制程度更高） |

---

## 四、来源

- [Frame Interpolation: How It Works in AI Video](https://morphic.com/ai-glossary/Frame-Interpolation) — Morphic, 2026
- [AI Frame Interpolation - Change Video FPS Online](https://www.topazlabs.com/tools/frame-interpolation) — Topaz Labs, 2026
- [The 2026 AI Video Production Playbook](https://medium.com/data-science-collective/the-2026-ai-video-production-playbook-bc683d5b85da) — Data Science Collective, 2026
- [Seedance 2.0 Complete Guide: Video Extension](https://morphic.com/resources/how-to/seedance-2-guide) — Morphic, 2026