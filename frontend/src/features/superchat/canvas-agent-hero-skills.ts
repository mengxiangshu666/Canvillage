// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab

import type { CanvasAgentIntentId } from "./canvas-agent-intelligence";

/**
 * Runtime contracts for the three high-value canvas skills.
 *
 * The catalog description explains what a skill is. This contract explains
 * how the Agent must execute it in the current turn and what proves completion.
 * Keeping it data-driven prevents each model/provider from inventing a new loop.
 */
export type CanvasAgentHeroSkillId = "storyboard" | "identity-continuity" | "failure-rescue";

export type CanvasAgentHeroSkillRuntimeContract = {
  id: CanvasAgentHeroSkillId;
  label: string;
  objective: string;
  inputs: readonly string[];
  observe: readonly string[];
  write: readonly string[];
  commandPolicy: readonly string[];
  output: readonly string[];
  qualityGate: readonly string[];
  completion: string;
  failureStop: readonly string[];
};

export const CANVAS_AGENT_HERO_SKILL_CONTRACTS: Readonly<Record<
  CanvasAgentHeroSkillId,
  CanvasAgentHeroSkillRuntimeContract
>> = {
  storyboard: {
    id: "storyboard",
    label: "分镜大师",
    objective: "把故事需求编译成可编辑、可继续生成、可验收的镜头节点链。",
    inputs: ["user_request", "canvas_context", "selected_node", "pinned_nodes", "optional_script_or_beat"],
    observe: [
      "优先使用当前请求中的 canvas_context、selected_node 和 pins；事实缺失时才按 node_ids 定向读取快照。",
      "先确定镜头数量和镜头之间的叙事依赖，不为已存在的事实重复读取整张画布。",
      "涉及参考图时先读取 canvas.director_state.reference_manifest；图片N只做展示标签，绑定必须使用 manifest 中的 node_id 或 asset_id。",
    ],
    write: ["freezone_emit_canvas_command"],
    commandPolicy: [
      "主路径用 create_shot_sequence，一次提交完整 prompts；镜头不超过 12 个。",
      "单镜用 create_image_prompt_node 或 create_video_prompt_node；需要关系时同批 connect_nodes。",
      "每个 prompt 必须包含主体、动作起点、过程、终点、景别/机位、运镜和可切点。",
    ],
    output: ["shot_contracts", "created_node_ids", "revision", "applied_ops"],
    qualityGate: [
      "每镜只有一个主要叙事任务，镜头之间有明确承接。",
      "人物、场景、道具引用职责分开；没有权威素材时标记待补，不编造 ID。",
      "参考图顺序与 UI referenceOrder、提交顺序一致；不唯一的显示名或自然语言引用必须暂停确认。",
      "草稿模式只落结构和提示词，不触发媒体生成。",
    ],
    completion: "server_applied=true 且 revision>0、applied_ops>=计划写入数、created_node_ids 覆盖全部新镜头。",
    failureStop: [
      "缺少可执行故事事实时先落一份带假设标记的镜头草案，不伪造项目事实。",
      "回执缺 revision 或 applied_ops 时只做一次定向快照核对，随后停止循环。",
    ],
  },
  "identity-continuity": {
    id: "identity-continuity",
    label: "角色一致性",
    objective: "只修复真实漂移的身份与连续性字段，保留已通过资产和用户原意。",
    inputs: ["canvas_context", "selected_node", "pinned_nodes", "identity_or_scene_anchors", "current_prompt"],
    observe: [
      "先读取目标节点、固定引用和必要上游；没有权威锚点时记录待裁决，不把候选图升级为真相。",
      "将身份、服装、场景光线、道具、空间方向、动作情绪分层比对，只改发生漂移的层。",
      "角色图、场景图和分镜图按 reference_manifest 的 role、node_id、asset_id 对齐，不按文件名或画布位置猜。",
    ],
    write: ["freezone_emit_canvas_command"],
    commandPolicy: [
      "有具体目标节点时用 update_node_prompt 写回完整优化稿；一次只修一个高影响变量。",
      "没有可写目标但需要保留结论时用 annotate；引用关系只用真实 connect_nodes 表达。",
      "不编造 bind_identity、replace_reference 或其他未注册命令，不直接启动生成。",
    ],
    output: ["continuity_audit", "prompt_patch", "target_node_ids", "revision", "applied_ops"],
    qualityGate: [
      "脸部身份、服装状态和表演状态分开，face_prompt 不混入服装或情绪。",
      "保留既有成功资产；没有锚点的冲突必须明确标记待用户裁决。",
      "回写后以具体节点 ID、revision 和 applied_ops 验收。",
    ],
    completion: "目标节点实际写入 prompt 或审计备注，并拿到有效 revision 与 applied_ops；仅聊天分析不算完成。",
    failureStop: [
      "没有权威锚点时停止自动修复，只输出证据和待裁决项。",
      "一次修复后若仍失败，先保留旧资产并等待新证据，不连续改写同一节点。",
    ],
  },
  "failure-rescue": {
    id: "failure-rescue",
    label: "失败救援",
    objective: "从真实失败任务恢复到最近可继续节点，避免重复扣费和整链重跑。",
    inputs: ["recent_task_or_run", "error_text", "canvas_context", "failed_node", "successful_assets"],
    observe: [
      "先查真实任务/运行状态、错误原文、失败节点输入和已成功产物；不知道 ID 时按当前项目范围列出最近任务。",
      "根因只归类为输入缺失、引用错误、能力不兼容、内容安全、外部瞬态、权限/额度、内部合同或质量失败。",
    ],
    write: ["freezone_emit_canvas_command", "freezone_run_node", "freezone_retry_node", "freezone_stop_task"],
    commandPolicy: [
      "参数/引用问题先 update_node_prompt 或 annotate，只改一个高影响变量。",
      "瞬态失败只有在确认旧任务已终止且不会重复计费后才允许 retry；durable run 从失败阶段续跑。",
      "媒体失败先保留成功资产，停止失控任务后只用 freezone_retry_node 重跑失败节点。",
    ],
    output: ["failure_evidence", "root_cause", "recovery_point", "preserved_assets", "retryable", "revision_or_task_id"],
    qualityGate: [
      "错误分类必须带真实任务或运行证据，不能把所有错误归为网络问题。",
      "已成功资产和 lineage 不被覆盖；恢复从最低失败点开始。",
      "queued/running/accepted 不得报告为完成，生成结果只认真实任务回执。",
    ],
    completion: "拿到真实错误分类和可执行恢复点；若已修画布，还必须核对 revision/applied_ops。",
    failureStop: [
      "同一失败最多一次有效重试；没有新证据就停止，不重复提交。",
      "缺少任务 ID、失败节点或授权时停在取证报告，不编造恢复成功。",
    ],
  },
} as const;

function uniqueHeroSkillIds(ids: readonly CanvasAgentHeroSkillId[]): CanvasAgentHeroSkillId[] {
  return [...new Set(ids)];
}

export function heroSkillIdsForIntent(intentId: CanvasAgentIntentId): CanvasAgentHeroSkillId[] {
  if (intentId === "storyboard" || intentId === "one_click_film" || intentId === "workflow_build") {
    return ["storyboard"];
  }
  if (intentId === "identity_continuity" || intentId === "expression_directing" || intentId === "prompt_polish") {
    return ["identity-continuity"];
  }
  if (intentId === "failure_rescue") return ["failure-rescue"];
  return [];
}

export function heroSkillContractsFor(input: {
  intentId: CanvasAgentIntentId;
  skillIds: readonly string[];
}): CanvasAgentHeroSkillRuntimeContract[] {
  const requested = input.skillIds
    .map((id) => id.trim())
    .filter((id): id is CanvasAgentHeroSkillId => id in CANVAS_AGENT_HERO_SKILL_CONTRACTS);
  const ids = uniqueHeroSkillIds([...heroSkillIdsForIntent(input.intentId), ...requested]);
  return ids.map((id) => CANVAS_AGENT_HERO_SKILL_CONTRACTS[id]);
}

export function compactHeroSkillContract(
  contract: CanvasAgentHeroSkillRuntimeContract,
): Record<string, unknown> {
  return {
    id: contract.id,
    label: contract.label,
    objective: contract.objective,
    inputs: contract.inputs,
    observe: contract.observe,
    write: contract.write,
    command_policy: contract.commandPolicy,
    output: contract.output,
    quality_gate: contract.qualityGate,
    completion: contract.completion,
    failure_stop: contract.failureStop,
  };
}
