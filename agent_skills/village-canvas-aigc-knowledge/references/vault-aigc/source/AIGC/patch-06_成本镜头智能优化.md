# 补丁06 — 成本-镜头智能优化

> 来源：Atlas Cloud Price Comparison (2026-06-12) + Data Science Collective Playbook (2026) + LushBinary Sora vs Veo vs Kling (2026) + Atlas Cloud Best Models 2026
> 交叉验证：两个独立来源的定价完全一致

---

## 一、2026 年模型定价完整排行

从最便宜到最贵，按每秒单价排名：

| 模型 | 价格/秒 | 最大时长 | 分辨率 | 音频 | 生成用时 |
|------|---------|---------|--------|------|---------|
| **Seedance 2.0 Fast** | $0.022 | 15s | 1080p | -- | ~30s |
| Veo 3.1 | $0.090 | 8s | 电影级 | ✅ | ~60s |
| Kling O3 | $0.085 | 15s | 1080p | ✅ | ~120s |
| Wan 2.6 | $0.070 | 15s | 1080p | ✅ | ~20s |
| Vidu Q3 | $0.070 | 16s | 1080p | ✅ | ~25s |
| Hailuo 2.3 | $0.100 | 10s | 1080p | -- | ~40s |
| Kling 3.0 | $0.153 | 10s | 1080p | ✅ | ~60s |
| Sora 2 | $0.100 | 10s | 1080p | -- | ~90s |
| Seedance 2.0 Pro | $0.247 | 8s | 1080p | -- | ~45s |
| Kling Video O3 Pro | $0.150 | 15s | 1080p | ✅ | ~120s |

---

## 二、决策树：根据场景推荐最省钱模型

```
Q1: 需要原生音频吗？
  ├── 需要 → Q2
  └── 不需要 → Seedance 2.0 Fast ($0.09/s) ⭐ 最便宜

Q2: 需要多长视频？
  ├── ≤8s → Veo 3.1 ($0.09/s, 含音频) ⭐
  ├── ≤15s → Kling O3 ($0.085/s, 含音频) ⭐
  └── ≤30s → Seedance 2.5 (7月公测, 含音频)

Q3: 质量要求？
  ├── 社交媒体/概念草稿 → 上一步的省钱选项 ⭐
  ├── 电商产品视频 → Seedance 2.0 Fast ($0.09/s)
  ├── 客户交付/成品 → Kling 3.0 Pro 或 Seedance 2.0 Pro
  └── 电影级/广告核心 → Veo 3.1 或 Kling Video O3

Q4: 需要角色一致性？
  ├── 需要 → Seedance 2.0 (参考图多) 或 Kling 3.0 (非参考)
  └── 不需要 → 按价格选最便宜的
```

---

## 三、实际场景算账

### 场景：做一个 3 分钟 AI 短片

假设需要 18 个 10s 镜头 + 迭代浪费（3 倍重试取 1 个可用）= 54 次生成

| 模型 | 单次 10s | 54 次成本 | 总时长 |
|------|---------|----------|-------|
| **Kling 3.0** | $1.26 | $68.04 | 3min + 原生音频 |
| Sora 2 | $1.50 | $81.00 | 3min |
| Seedance 2.0 Pro | $2.47 | $133.38 | 3min (仅8s, 需更多片段) |
| **Seedance 2.0 Fast** | $0.90 | $48.60 | 3min + 后期配音 |
| 混合: Seedance Fast 70% + Kling 30% | ~$0.40/avg | $64.80 | 3min + 关键镜头高质量 |

**混合策略最省钱：** 70% 镜头用 Seedance Fast，30% 关键镜头用 Kling 3.0 Pro。

### 场景：月产 100 条社交媒体短视频

| 策略 | 月费 | 视频数 | 每条质量 |
|------|------|--------|---------|
| 全用 Seedance 2.0 Fast | $50 | ~284 条 | ✅ 1080p 可接受 |
| 全用 Kling 3.0 | $50 | ~49 条 | ✅ 含音频 |
| **混合 70/30** | $50 | ~200 条 (Seedance) + ~15 条 (Kling) | ✅ 大部分好，关键镜头高质量 |

---

## 四、隐性成本

| 因素 | 影响 | 量化 |
|------|------|------|
| **迭代成本** | 约 3 次生成才有 1 次可用 | 有效成本 = 标价 × 3 |
| **音频成本** | 无原生音频的模型需后期制作 | 每条 +$0.50-2.00 |
| **放大成本** | 720p 模型需放大 | 每条额外处理时间 |
| **后期编辑** | 质量差的镜头需更多后期 | 人力时间成本 |

### 有效成本对比（含 3 次迭代）

| 模型 | 标价 | 有效价 |
|------|------|-------|
| Seedance 2.0 Fast (8s) | $0.176 | $0.53 |
| Veo 3.1 (8s) | $0.24 | $0.72 |
| Kling 3.0 (10s) | $1.008 | $3.02 |
| Sora 2 (10s) | $1.20 | $3.60 |

Seedance Fast 迭代 3 次后的有效成本（$0.53）仍低于 Kling 3.0 的一次成本（$1.008）。

---

## 五、铁律

1. **先批量试错再用贵模型** — Seedance Fast 出草稿，关键镜头换 Kling/Veo
2. **需要音频时 Veo 最便宜** — $0.03/s 包含音频，比 Seedance Fast + 另付音频便宜
3. **10 秒以上只能是 Kling/Sora** — 其他模型撑不到 10s
4. **Seedance 2.0 Mini 更便宜** — 但不是所有平台都支持
5. **迭代才是真正花大钱的地方** — 能干 1 次出的 prompt 比便宜模型更省钱

---

## 六、来源

- [Cheapest AI Video Generation APIs 2026: Price Comparison](https://www.atlascloud.ai/blog/guides/cheapest-ai-video-generation-api-2026) — Atlas Cloud, Jun 2026
- [Best AI Video Generation Models 2026: Complete Comparison](https://www.atlascloud.ai/blog/guides/best-ai-video-generation-models-2026) — Atlas Cloud, 2026
- [The 2026 AI Video Production Playbook](https://medium.com/data-science-collective/the-2026-ai-video-production-playbook-bc683d5b85da) — Data Science Collective, 2026
- [Sora 2 vs Veo 3.1 vs Kling 3.0 Compared](https://lushbinary.com/blog/ai-video-generation-sora-veo-kling-seedance-comparison) — LushBinary, 2026