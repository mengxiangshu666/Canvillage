# AI Cinema Studio Engine (PopTech Studio)

> 来源：https://github.com/poptechstudio/ai-cinema-studio-engine
> 安装日期：2026-07-07
> 安装路径：`<LOCAL_PATH>`
> License：MIT

## 一句话

自托管 RAG 驱动的电影级 AI 制片引擎——5 层架构、1645 个摄影预设、5 个 N8N 工作流、Remotion 合成、21 LUT 调色、15 模型唇形同步。

## 五层架构

```
Layer 5 — 分发          自动多平台发布（YouTube/Shorts/Reels/TikTok/LinkedIn）
Layer 4 — 后期制作      Remotion 合成 + FFmpeg 21 LUT 调色 + Topaz 超分
Layer 3 — 音频制作      语音合成 + SFX + 音乐 + 唇形同步
Layer 2 — 虚拟制片      RAG 组装摄影机 + 灯光 + 特效指令
Layer 1 — AI 生成        多模型视频/图像/虚拟人
Layer 0 — 编排           N8N 工作流 + Qdrant RAG + Notion + 品牌配置
```

## 核心差异化

**每次视频生成调用都会从向量数据库组装摄影提示词：**
- 摄影机机身 + 镜头 + 焦距 + 运动 + 灯光设置 + 特效/风格
- 1645 个预设分布在 8 个 Qdrant 集合中
- "电影级推轨镜头 黄金时刻"不只是模糊提示词，而是精确的技术指令

## 5 个 N8N 工作流（67 节点）

| 工作流 | 节点 | 触发 |
|--------|------|------|
| 电影广告制作 | 20 | Notion 简报 |
| 纪录片制作 | 14 | Notion 简报 |
| 动漫/风格化 | 13 | Notion 简报 |
| 日更内容套件 | 12 | Cron 每日 9AM |
| 视频发布器 | 8 | 下游调用 |

## 21 个 LUT 调色文件

- 11 个胶片模拟（Kodak Vision3 500T/250D、Fuji Eterna 等）
- 8 个创意风格（漂白、交叉冲洗、青橙调、日拍夜等）
- 标准完成链：normalize → film stock → creative grade → grain(0.12) → vignette(0.25)

## 15 模型唇形同步排行

| 层级 | 模型 | 质量 |
|------|------|------|
| 1 | Wav2Lip | 基础 |
| 2 | SadTalker | 良好 |
| 3 | LivePortrait | 更好 |
| 4 | LatentSync 1.6 | 电影级 |
| 5 | Wan 2.6 原生 | 最高 |

## 基础设施要求

| 组件 | 最低 | 推荐 |
|------|------|------|
| VPS | 2CPU, 4GB RAM | 4CPU, 8GB RAM |
| 本地 | 16GB RAM | 32GB RAM + GPU |
| 存储 | 50GB | 200GB+ |

## 安装（10 步）

```bash
cd <LOCAL_PATH>
cp .env.example .env
pip install qdrant-client openai requests python-dotenv
docker run -d --name qdrant -p 6333:6333 -p 6334:6334 qdrant/qdrant:latest
python tools/populate_camera_presets.py --generate
python tools/populate_camera_presets.py --upload
# ... 继续 lighting/effects presets
cd remotion && npm install
```

## 在 AIGC 导演流程中的位置

**完整制片管线**：这个引擎覆盖了从创意简报到多平台发布的全部 26 步 SOP。是整个流程的"自动化骨架"——n8n 编排、RAG 摄影知识、Remotion 合成、FFmpeg 调色。