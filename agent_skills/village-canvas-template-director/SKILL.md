---
name: village-canvas-template-director
description: Use for product showcase and budget-conscious product or fashion advertisement planning (低预算产品广告、服装品牌宣传、素材复用与镜头模板), first-last-frame transitions, motion redraw, multi-reference continuity, storyboard, and choosing or inserting a canvas workflow template.
version: 2.1.0
---

# Village Infinite Canvas 镜头模板导演规程

把模板当作可编辑的节点结构合同，不是黑盒生成按钮。只使用请求内
`workflow_manifest.starter_workflows` 返回的目录；其中
`required_inputs`、`required_roles`、`outputs`、`does_not_produce` 和
`quality_gates` 是选择与验收依据，模板标题和节点外观不是完成证据。

indexed 模式先用 `village_canvas_capability` 搜索并 describe
`canvas.snapshot`、`canvas.compatibility.emit` 和需要的媒体能力，再 invoke；
不要猜造工具名。

## 工作流程

1. 读取当前画布事实与 `workflow_manifest.starter_workflows`；目录为空时只按
   当前节点和模型能力规划，不编造模板。
2. 先按导演合同筛选 `delivery_level`，再逐项核对 `required_inputs` 和
   `required_roles`。缺输入时列缺口，不用空上传节点假装资产已存在。
3. 只保留能够真实产生所需 `outputs` 的候选；若目标出现在
   `does_not_produce`，该模板只能作为局部骨架，不能作为完成方案。
4. 需要落图时，通过 `canvas.compatibility.emit` 发出一个
   `insert_starter_workflow(workflow_id)`；不要手工复刻目录中已经存在的节点图。
5. 只有 `canvas_command_receipt.v2` 中 `server_applied=true`，且 revision、
   created_node_ids、applied_ops 与合同一致，才报告结构已落图。
6. 模板落图和媒体执行分开验收。`media_draft` 使用真实节点/任务回执；
   `final_film` 交给 `production.run.start`，最终只认 `production.final_video`。

## 模板契约

- 产品展示：一张产品主图，图生视频；保留产品外形、材质、比例和品牌元素。
- 首尾帧转场：两张关键帧，首尾帧模式；只允许一条可解释的运动或变化。
- 源视频动作重绘：恰好一条源视频，`videoEdit`；不得切换到全能参考或文生视频。
- 多参考一致性：人物、造型、动作职责分槽；素材角色不明确时先批注，不混作单一参考。
- 分镜到视频：先输出可编辑镜头结构，再提案逐镜生成。
- 最终成片：起步模板只提供节点骨架；音频、字幕、逐镜视频和合成由
  ProductionControl 推进，必须取得正式 final video。

## 低预算产品与服装广告

仅在产品广告或服装品牌宣传规划时使用本节；已有生产任务恢复交给
`village-canvas-one-click-film`，已有成片正式质检交给 `village-canvas-delivery-qc`。

- 按用户的用途、时长、画幅、镜数、场景和声音要求规划。未指定时提出可改的最小方案，
  不强制20秒、4镜、三选一场景或默认BGM；“低预算”不等于付费授权。
- 核对产品/服装参考与人物身份参考的真实节点、资产和角色槽。分别记录必须保留的
  外形、材质、标识、服装结构和面部特征；人物不是必需时不额外造人。
  缺输入先给可执行的镜头草案和素材缺口，不宣称已有素材或可直接生成。
- 每镜列出：用途、所示卖点、参考职责、简单动作/主运镜、时长、复用或新增、验收条件。
  可选用途包括整体轮廓与环境、结构/材质细节、人物情绪、产品定格收尾；按卖点取舍合并，
  它们不是必须各生成一次的四个镜头。同一镜头不塞多个互相竞争的动作。
- 优先复用已通过的素材；封面可从保留镜头取帧，额外海报、备选镜头和多版本不默认生成。
  复用/裁切须保留卖点、主体、画幅与分辨率要求，无法满足时如实列缺口。
- 成本只采用当前能力返回的有效报价与 `task_authorization`；没有报价记“未知”，
  不把任务次数换算成虚构金额。分别列必要新生成、可选项和授权内的返工余量。
  预计超额时先减可选镜头或复用素材；不能满足交付要求时说明差额，不默默降低要求。
- 对已有素材记录问题时间段和最小修复：短暂首尾不稳可裁掉；稳定帧可用于用户允许的
  定格收尾。修后复核时长、节奏和声音同步；不通过裁切掩盖错误产品或人物。
  持续变形、身份/服装/标识错误应局部重生成，只替换受影响的镜头并保留合格素材。
- 重生成前核对真实任务状态、剩余额度与授权；取回已完成结果不等于新提交。
  新的收费重试必须在授权额度内，次数或金额未知时不自行许诺“免费重试”或无限重拍。
- 交付先区分镜头计划、已插入结构与真实媒体；沿用上面的执行回执和最终成片规则。

## 禁止事项

- 模板不携带远端媒体 URL、付费任务或隐藏模型覆盖。
- 不把“推荐模板”说成“已插入画布”。
- 不使用目录外模板 ID 或 allowlist 之外的画布命令。
- 不因模板匹配而自动启动图片、视频或音频生成。
- 不把 `compose_node`、queued/running 任务或 `does_not_produce` 中的目标说成已产出。
