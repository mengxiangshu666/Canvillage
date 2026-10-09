---
name: village-canvas-lora-training
description: LoRA训练与模型微调完整指南。覆盖：LoRA核心原理/数据集准备标准/训练工具选择/Kohya SS参数/推理使用/常见问题修复。来源：兄弟AIGC知识包20号文件（2026-07）。
triggers:
  - LoRA训练
  - 模型微调
  - 数据集准备
  - 角色LoRA
  - 风格LoRA
---

# LoRA训练与模型微调

## 核心概念

LoRA（Low-Rank Adaptation）不重训整个大模型，而是大模型关键层上叠加一个小权重矩阵。

- 训练时间：20-60分钟（免费Google Colab T4 GPU）
- 模型大小：40-150MB
- 推理时强度：0.6-1.0，可调

---

## 数据集准备（决定成败）

### 图片选择标准

| 标准 | 要求 | 为什么 |
|------|------|--------|
| 数量 | 15-30张，20-25最佳 | 不够泛化，太多过拟合 |
| 多样性 | 正面+45°+侧面+不同表情+不同光线 | 每缺一个角度就是模型的一个盲区 |
| 质量 | 清晰、高分辨率、面部占40-60%画幅 | 太小学不到特征，太大裁切奇怪 |
| 背景 | 简单或去背景 | 模型要学的是脸，不是背景 |

**关键比例**：至少30%是侧脸/3/4角度

### 处理流程

```
1. 裁切面部居中 → 方块比例
2. 去背景（rembg）
3. 每张图配一个 .txt 标注文件
4. 按文件夹结构组织
```

### 标注文件格式

```
photo of [trigger], professional headshot, detailed face, studio lighting
```

- `trigger` = 触发词，比如 `sanj`、`老陈`
- 标注时一定要包含trigger词
- 自动标注工具（BLIP/WD14 tagger）给初始标注，但**必须手动审核**

### 批量处理命令

```bash
# 裁切并居中
mogrify -resize 768x768^ -gravity center -extent 768x768 *.jpg

# 去背景（批量）
rembg p *.jpg output/
```

---

## 训练工具选择

| 工具 | 适用模型 | VRAM | 场景 |
|------|---------|------|------|
| Google Colab | SD 1.5 / SDXL | 免费T4 GPU | 没有本地GPU |
| Kohya SS GUI | SD 1.5 / SDXL / FLUX | 8-24GB+ | 最全功能，行业标准 |
| FluxGym | FLUX | 12-20GB | FLUX专用，低VRAM友好 |
| ComfyUI Flux Trainer | FLUX | 16-24GB | 已用ComfyUI的用户 |

### Kohya SS参数速查

| 参数 | SD 1.5 | SDXL | FLUX |
|------|--------|------|------|
| 分辨率 | 512x512 | 1024x1024 | 1024x1024 |
| 最小VRAM | 8GB | 12GB | 16GB |
| 推荐VRAM | 12GB | 16GB+ | 24GB+ |
| Batch size | 1-2 | 1 | 1 |

---

## 训练参数

### 字符LoRA黄金参数

| 参数 | 值 | 说明 |
|------|-----|------|
| network_dim | 64 | 32-128范围，64为起始甜区 |
| network_alpha | 32 | dim的一半 |
| learning_rate | 2e-4 | 用cosine with restarts调度器 |
| steps | 1500-2500 | 看预览图，停止改善就停 |
| batch_size | 1-4 | 8GB用1，12GB+用2-4 |
| clip_skip | SD1.5=2, SDXL/FLUX=1 | 跳过最后N层CLIP输出 |
| regularization | 200-500张 | 防止trigger词过拟合 |

### 命令行示例（Kohya SS）

```bash
accelerate launch --num_cpu_threads_per_process 8 train_network.py \
  --pretrained_model_name_or_path="runwayml/stable-diffusion-v1-5" \
  --train_data_dir="./training_images" \
  --output_dir="./output" \
  --caption_extension=".txt" \
  --max_train_epochs=15 \
  --learning_rate=2e-4 \
  --lr_scheduler="cosine_with_restarts" \
  --network_module="networks.lora" \
  --network_dim=64 \
  --network_alpha=32
```

### 训练监控

每100-200 steps检查预览样本。当生成的样本不再改善、开始跟训练图片一模一样时——**立刻停**，不管跑了多少步。这就是最优点了。

---

## 推理使用

### 提示词结构

```
photo of [trigger word], [style], [setting], [lighting], [camera] <lora:filename:0.8>
```

### 强度控制

| 强度 | 效果 |
|------|------|
| 0.6-0.8 | 通用范围，角色保持+提示词遵从平衡 |
| 0.8-1.0 | 像真度更高，但提示词遵从降低 |
| 0.4-0.6 | 减轻过拟合时使用 |

### 否定提示词

```
deformed, distorted, disfigured, poorly drawn, bad anatomy, extra limb, mutation, blurry, out of focus
```

---

## 常见问题与修复

| 问题 | 原因 | 修复 |
|------|------|------|
| 生成不一致 | 数据集缺多样性 | 加5-10张不同角度和光线的，确保至少30%侧脸 |
| 过拟合 | 训练过度 | 降steps或dim，加正则化图，推理时降强度到0.5-0.6 |
| 脸崩 | 数据集中有坏图 | 一张裁切不对的图就能毁掉整个训练，逐张检查 |
