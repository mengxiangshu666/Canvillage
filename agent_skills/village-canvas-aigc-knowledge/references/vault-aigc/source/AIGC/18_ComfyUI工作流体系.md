# 18_ComfyUI工作流体系 v1.0

> 来源交叉验证：Runflow 20 Workflows (2026-05) + Atlas Cloud Integration Guide (2026-06) + AIToolRanked Beginner Guide (2026) + comfy.org + Community 实测

---

## 核心概念

ComfyUI 是一个**节点式工作流引擎**，AI 图像/视频/音频生成的全链路编排工具。

不是"又一个 AI 工具"——它是 AI 影像生产的 **工业级管线编排层**，相当于电影行业的 Nuke 或 Avid。

---

## 一、ComfyUI 工作流本质

### 1.1 什么是工作流

一个 ComfyUI 工作流 = 有向无环图（DAG）

```
[模型加载器] → [条件器] → [采样器] → [解码器] → [保存器]
     ↑           ↑
  [提示词]   [参考图]
```

每个节点接收输入、处理、输出到下一个节点。

### 1.2 序列化格式

工作流保存为两种 JSON：

| 格式 | 用途 | 特点 |
|------|------|------|
| `workflow.json` | 编辑器格式 | 含 UI 坐标、节点布局、组信息 |
| `workflow_api.json` | API 格式 | 精简版，POST 给 `/prompt` 端点用 |

**产出线团队两种都版本管理**：编辑器 JSON 供人审阅，API JSON 供服务器运行。

### 1.3 节点连接规则

```json
{
  "3": {
    "class_type": "KSampler",
    "inputs": {
      "model": ["4", 0],     // 从节点4的0号输出取model
      "positive": ["6", 0],  // 从节点6的0号输出取positive
      "seed": 42
    }
  }
}
```

`["4", 0]` = 节点 ID `4` 的 0 号输出槽。**读懂这个就能读懂任何工作流**。

---

## 二、20 个工作流阶梯（4 阶成熟度）

### 第 1 阶：基础生成（Workflows 1-5）

| # | 工作流 | 说明 | 关键节点 |
|---|--------|------|---------|
| 1 | Text-to-Image | 最简基线 | CLIPTextEncode + KSampler + VAE Decode |
| 2 | Image-to-Image | 带降噪控制 | LoadImage → VAEEncode → KSampler(denoise) |
| 3 | Inpainting | 带蒙版局部重绘 | VAEEncodeForInpaint + mask |
| 4 | Outpainting | 画布扩展 | PadImageForOutpainting → Inpaint |
| 5 | ControlNet | 条件控制 | 深度/边缘/姿态 -> ControlNetApply |

**生产注意事项：**
- Flux 和 SDXL 的采样器不同，不要盲目复制
- Denoise < 0.4 是微调，0.6-0.8 是变体甜区，1.0 = 文生图
- ControlNet 堆叠不要超过 2 个，第三个开始互斗
- Outpainting 每次不超过原图 30%，多用几次递进

### 第 2 阶：身份与风格一致（Workflows 6-10）

| # | 工作流 | 关键 | 强度建议 |
|---|--------|------|---------|
| 6 | LoRA 堆叠 | 2 个 LoRA 各 0.7 是上限 | 如果品牌+角色+风格都要 → 训练单一复合 LoRA |
| 7 | IP-Adapter 参考 | 参考图嵌入注入 | Face: 0.7-0.9 / Plus: 0.4-0.6 |
| 8 | InstantID 面部保留 | 专用面部编码器 | 加 3-5GB VRAM，加 30-50% 推理时间 |
| 9 | 风格迁移 | 结构参考 + 风格参考 | 风格参考选纹理重、主体轻的 |
| 10 | 多 LoRA 角色+风格 | 身份 0.7 + 风格 0.5 + ControlNet 姿态 | 训练 LoRA 时用风格中立数据 |

**关键原则：** LoRA 堆叠是乘法效应，不是加法。3 个各 0.8 = 实际上每个只有约 0.5。

### 第 3 阶：生产级管线（Workflows 11-15）

| # | 工作流 | 说明 | 为什么重要 |
|---|--------|------|-----------|
| 11 | 面部修复链 | GFPGAN + CodeFormer 级联 | 出片率提升 30-40% |
| 12 | 放大链 | Real-ESRGAN(2-4x) + SUPIR(细节增强) | SUPIR 会幻觉（造扣子/造文字） |
| 13 | 背景替换 | SAM分割 → Inpaint → 重打光 | 蒙版质量决定一切 |
| 14 | 质量评分+重试 | CLIP评分 + FaceMatch + LLM Judge | **Demo 和产品的分界线** |
| 15 | 批量处理 | 元数据传递 | 8-16 张以后分开队列 |

**Real-ESRGAN vs SUPIR 选择：**
- Real-ESRGAN → 忠实放大（产品目录、法律文档）
- SUPIR → 创意增强（英雄镜头、艺术输出）
- **绝对不要** 在电商/法律相关场景用 SUPIR

**质量门禁（Workflow 14）是产品化最关键的工作流。**
没有质量门禁，5-15% 的坏输出会变成退费工单。三阶段方案：
1. Router：根据输入动态制定评估计划
2. Preprocessors：并行提取结构化数据（分割、面部相似度、颜色检查）
3. LLM Judges：每维度独立评分，接受标准用文字描述（"衣服必须匹配参考缝线图案"）

### 第 4 阶：视频与多模型（Workflows 16-20）

| # | 工作流 | 模型 | VRAM 需求 |
|---|--------|------|-----------|
| 16 | Image-to-Video | Wan 2.2 / Kling API / Hunyuan | 24-40GB |
| 17 | AnimateDiff | 风格化动画（非写实） | 适中 |
| 18 | 多模型路由 | Switch 节点 + 编排层 | 实际路由在编排层做 |
| 19 | Audio-to-Video | 口型同步（LatentSync/MuseTalk） | 音频需要降噪预处理 |
| 20 | Agent 工作流 | LLM 控制子工作流选择和参数 | 测试阶段已可行，调试还在痛点 |

**多模型路由（Workflow 18）的实际模式：**
```
GPU 自动升配：RTX 4090 → OOM → L40S → OOM → A100 → H100
工作级别持久化 GPU 层级，下次跳过试错
```

---

## 三、生产级部署三件套

### 3.1 版本固定

在 ComfyUI 部署中比代码本身还重要。每周都有更新。

**必须固定：**
- ComfyUI 版本（具体 Git commit）
- 自定义节点版本（具体 release）
- 模型文件（具体 hash）

> "ComfyUI 在代码部署领域以难以投产而臭名昭著" — SimpliSmart, Cerebrium, BentoML, 及多个 GitHub 讨论

### 3.2 暖 Worker 池

冷启动 60-120 秒，模型加载占 30-60 秒。
生产方式：保持 N 个暖 Worker 按工作流类型分布。

### 3.3 质量门禁

见 Workflow 14。不设质量门禁 = 无法产品化。

---

## 四、ComfyUI 与视频模型集成

### 4.1 本地 vs 云端

| | 本地 ComfyUI | 云端 API（Atlas Cloud 等） |
|--|-------------|--------------------------|
| 视频模型 | ❌ 不支持（闭源/仅API） | ✅ Seedance/Kling/Wan/Vidu/Hailuo |
| 图像模型 | ✅ Stable Diffusion/Flux | ✅ 同上 |
| 成本 | GPU 电费 + 硬件 | 按秒计费 |
| 集成 | 统一节点 | 统一 OpenAI 兼容 API |

### 4.2 典型集成工作流

```
一个文本节点 → 路由到 Wan 2.7 快速概念草图 ($0.1/s)
  ↓ 最佳帧传给
Kling 3.0 Pro 图生视频 更高保真 ($0.095/s)
  ↓
可并行分支 →
同一提示词同时发给 Seedance 2.0 → 人工评审前比对输出
```

### 4.3 Seedance 2.0 ComfyUI 自定义节点

社区有 `seedance2-comfyui` 自定义节点包，支持：
- Text-to-Video
- Image-to-Video
- Consistent Character
- Video Extend

通过 `muapi.ai` 中转 API 调用。

---

## 五、工作流查找与版本管理

### 5.1 去哪里找工作流

| 来源 | 质量 | 说明 |
|------|------|------|
| comfy.org/workflows | ⭐⭐⭐⭐⭐ | 官方策展，最可靠 |
| comfyworkflows.com | ⭐⭐⭐ | 量最大，质量参差 |
| openart.ai | ⭐⭐⭐⭐ | 搜索过滤好 |
| civitai.com | ⭐⭐⭐ | 适合找特定模型绑定的工作流 |
| github.com/ComfyUI_examples | ⭐⭐⭐⭐⭐ | 官方示例，永远最新 |

### 5.2 版本管理最佳实践

1. 保存 `workflow.json` + `workflow_api.json` 到 Git
2. 语义版本号：`face-restoration-v2.3.1.json`
   - Major：接口破坏性变更
   - Minor：新增可选输入
   - Patch：参数微调
3. Normalize JSON（排序 key、规范化缩进）后再提交 → `git diff` 才有意义
4. 提交前钩子自动 normalize

---

## 六、全套工作流参考

### 提示词结构建议

```
[场景设置] → [主体描述] → [动作/运动] → [技术参数] → [风格/情绪]
```

### 视频生成参数模板

```
Subject: [年龄+性别+外貌+服装+表情]
Environment: [地点+天气+建筑+氛围]
Lighting: [光源+方向+色温+强度]
Camera: [景别+运动+角度]
Motion: [强度0-3 + 速度描述]
Sound: [台词/音效/BGM + 优先层级]
Negative: [--no 列表]
```

---

## 来源

- [20 ComfyUI Workflows for Production in 2026](https://www.runflow.io/blog/comfyui-workflows-production-ready) — Runflow, May 2026
- [ComfyUI Integration for Video Models](https://www.atlascloud.ai/zh/blog/guides/comfyui-integration-ai-video-models-seedance-kling) — Atlas Cloud, Jun 2026
- [ComfyUI Tutorial 2026: Beginner Setup](https://aitoolranked.com/blog/comfyui-tutorial-beginners-2026-complete-guide) — AIToolRanked, 2026
- [seedance2-comfyui GitHub](https://github.com/Anil-matcha/seedance2-comfyui) — Anil-matcha
- [Kling 3.0 Prompt Complete Guide](https://seavidgen.com/blog/kling-3-0-prompt-complete-guide) — Seavid AI, Mar 2026