# Village Infinite Canvas Shotcraft 适配合同

## 字段映射

| Shotcraft 信息 | Village Infinite Canvas 输出 |
|---|---|
| `summary` / `use` / `intention` | `narrative_task`：本镜唯一叙事职责 |
| `duration` | `duration_budget`：建议时长与静止呼吸 |
| `energy` | `energy_curve`：入镜、推进、落点、收束 |
| 卡片运动与时序 | `camera_motion`：起点、路径、速度、终点 |
| 卡片已知坑 | `negative_constraints`：只保留本镜高概率失败项 |
| 前后镜衔接 | `transition_contract`：前镜出口、后镜入口、切点 |
| 声音句法 | `sound_cue`：动作源、帧/节拍锚点、尾音 |
| 剧情化编译结果 | `video_prompt_append`：可合入现有 `video_prompt` 的短段 |

## 固定输出

```text
Shotcraft 主卡：<category/card-name；无适配卡则写 conventional>
辅助卡：<可选，最多一张>
叙事任务：<唯一职责>
时长预算：<总时长 / 动作段 / 静止呼吸>
能量曲线：<进入 → 推进 → 落点 → 收束>
摄影合同：<景别、机位、焦段感、构图>
运镜合同：<起点、方向/路径、速度、终点；一个主运镜>
表演与动作：<起点、身体动作、重心、终点、可切点>
转场合同：<前镜出口 → 切点 → 后镜入口；无则写硬切>
声音提示：<真实声源 / 节拍或动作锚点 / 尾音>
Video Prompt 增量：<只补镜头运动与时间逻辑，不重复人物/场景/风格正文>
负向边界：<本镜高概率失败项>
验收点：<3–7 条可观察条件>
```

## 编译约束

- `video_prompt_append` 不得覆盖已存角色、场景、服装、道具和项目风格模板。
- 卡片名只用于溯源，不传给视频模型。
- UI 元素、网页截图、产品指标、按钮和品牌模板必须剥离；剧情里真实存在的屏幕或
  界面另按剧本事实处理。
- 一张主卡已经覆盖运动与节奏时，不再叠加相同职责的辅助卡。
- 卡片无法适配目标模型、片段时长或首帧事实时，输出 `conventional` 并使用稳定镜头。
