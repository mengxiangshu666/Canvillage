# 20_LoRA训练与模型微调 v1.0

> 来源：Sanj.dev LoRA Guide (2026-05) + LocalAIMaster (2026) + Kohya SS Docs + HuggingFace Diffusers + r/StableDiffusion Community Consensus
> LoRA = 训练一个 40-150MB 的小适配器文件，从 15-30 张图片中学到角色的特征，然后插入到任何大模型里用。
> 对 AIGC 导演来说，这是让角色真正"属于你"的技术。

---

## 一、核心概念

LoRA（Low-Rank Adaptation）不重训整个大模型，而是在大模型的关键层上叠加一个**小权重矩阵**。

- 训练时间：20-60 分钟（免费 Google Colab T4 GPU）
- 模型大小：40-150MB
- 推理时强度：0.6-1.0，可调

---

## 二、数据集准备（决定成败）

### 2.1 图片选择标准

| 标准 | 要求 | 为什么 |
|------|------|--------|
| 数量 | 15-30 张，20-25 最佳 | 不够泛化，太多过拟合 |
| 多样性 | 正面 + 45° + 侧面 + 不同表情 + 不同光线 | 每缺一个角度就是模型的一个盲区 |
| 质量 | 清晰、高分辨率、面部占 40-60% 画幅 | 太小学不到特征，太大裁切奇怪 |
| 背景 | 简单或去背景 | 模型要学的是脸，不是背景 |

**关键比例：** 至少 30% 是侧脸/3/4 角度（社区共识）

### 2.2 处理流程

```
1. 裁切面部居中 → 方块比例
2. 去背景（rembg）
3. 每张图配一个 .txt 标注文件
4. 按文件夹结构组织
```

标注文件格式（每张图一个 `.txt`）：
```
photo of [trigger], professional headshot, detailed face, studio lighting
```

- `trigger` = 你的触发词，比如 `sanj`、`老陈`、`charlie`
- 标注时一定要包含 trigger 词，告诉模型"这张图里这个特征叫这个"
- 自动标注工具（BLIP / WD14 tagger）会给初始标注，但**必须手动审核**

### 2.3 自动化处理命令

**裁切并居中：**
```
mogrify -resize 768x768^ -gravity center -extent 768x768 *.jpg
```

**去背景（批量）：**
```
rembg p *.jpg output/
```

---

## 三、训练工具选择

| 工具 | 适用模型 | VRAM | 场景 |
|------|---------|------|------|
| Google Colab | SD 1.5 / SDXL | 免费 T4 GPU | 没有本地 GPU |
| Kohya SS GUI | SD 1.5 / SDXL / FLUX | 8-24GB+ | 最全功能，行业标准 |
| FluxGym | FLUX | 12-20GB | FLUX 专用，低 VRAM 友好 |
| ComfyUI Flux Trainer | FLUX | 16-24GB | 已用 ComfyUI 的用户 |

### Kohya SS 参数速查

| 参数 | SD 1.5 | SDXL | FLUX |
|------|--------|------|------|
| 分辨率 | 512x512 | 1024x1024 | 1024x1024 |
| 最小 VRAM | 8GB | 12GB | 16GB |
| 推荐 VRAM | 12GB | 16GB+ | 24GB+ |
| Batch size | 1-2 | 1 | 1 |

---

## 四、训练参数

### 字符 LoRA 黄金参数

| 参数 | 值 | 说明 |
|------|-----|------|
| network_dim | 64 | 32-128 范围，64 为起始甜区 |
| network_alpha | 32 | dim 的一半 |
| learning_rate | 2e-4 | 用 cosine with restarts 调度器 |
| steps | 1500-2500 | 看预览图，停止改善就停 |
| batch_size | 1-4 | 8GB 用 1，12GB+ 用 2-4 |
| clip_skip | SD1.5=2, SDXL/FLUX=1 | 跳过最后 N 层 CLIP 输出 |
| regularization | 200-500 张 | 防止 trigger 词过拟合 |

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

每 100-200 steps 检查预览样本。当生成的样本不再改善、开始跟训练图片一模一样时——**立刻停**，不管跑了多少步。这就是最优点了。

---

## 五、推理时使用 LoRA

### 提示词结构

```
photo of [trigger word], [style], [setting], [lighting], [camera] <lora:filename:0.8>
```

### 强度控制

| 强度 | 效果 |
|------|------|
| 0.6-0.8 | 通用范围，角色保持 + 提示词遵从平衡 |
| 0.8-1.0 | 像真度更高，但提示词遵从降低 |
| 0.4-0.6 | 减轻过拟合时使用 |

### 否定提示词

```
deformed, distorted, disfigured, poorly drawn, bad anatomy, extra limb, mutation, blurry, out of focus
```

---

## 六、常见问题与修复

| 问题 | 原因 | 修复 |
|------|------|------|
| 生成不一致 | 数据集缺多样性 | 加 5-10 张不同角度和光线的，确保至少 30% 侧脸 |
| 过拟合 | 训练过度 | 降 steps 或 dim，加 200-500 张正则化图，推理时降强度到 0.5-0.6 |
| 脸崩 | 数据集中有坏图 | 一张裁切不对的图就能毁掉整个训练，逐张检查 |
| 模糊输出 | 基础模型不够好 | 换 SDXL/FLUX，检查训练图是否清晰无压缩 |
| LoRA 无效果 | 训练参数不对 | 检查 trigger 词是否在标注中，学习率是否太低 |

---

## 七、字符 LoRA 在 AIGC 电影中的用法

### 什么时候用 LoRA

1. **角色需要跨项目复用** → 训练一个 LoRA，以后任何项目直接调用
2. **角色细节要求极高** → 比如特定伤痕、独特发型、标志性配饰
3. **风格一致性要求高** → 训练风格 LoRA 锁定画风

### 什么时候不用 LoRA

1. 只需要 1-2 个镜头 → 直接用参考图 + 提示词
2. 模型本身已经能很好保持角色 → 先用 Seedance @CharacterName 试试
3. 没有条件训练（无 GPU / 时间不够） → 用 07 号定妆法 + 参考图

### 推荐工作流

```
1. 先用 07 号定妆法生成角色参考图库
2. 如果角色一致性不够 → 训练 LoRA
3. 推理时 LoRA 强度 0.7 + 参考图锁定
4. 多镜头用 Seedance/Kling 的参考系统加载 LoRA 输出作为关键帧
```

---

## 来源

- [Train Your Own Stable Diffusion LoRA in 2026: Complete Guide](https://sanj.dev/post/train-stable-diffusion-lora-self-portraits) — Sanj, May 2026
- [Train an Image LoRA Locally (2026): Kohya, SDXL & FLUX](https://localaimaster.com/blog/image-lora-training-local-guide) — LocalAIMaster, 2026
- [Kohya SS GitHub](https://github.com/bmaltais/kohya_ss) — Official Repo
- [FluxGym GitHub](https://github.com/cocktailpeanut/fluxgym) — Official Repo
- [ComfyUI Flux Trainer](https://github.com/kijai/ComfyUI-FluxTrainer) — Kijai
- [r/StableDiffusion Character LoRA Primer](https://www.reddit.com/r/StableDiffusion/comments/1qqqstw/a_primer_on_the_most_important_concepts_to_train/) — Reddit, Jan 2026
- [HuggingFace Diffusers LoRA Training](https://huggingface.co/docs/diffusers/training/lora) — Official Docs