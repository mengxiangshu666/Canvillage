# `village-canvas gen` —— 直接触发生成（图 / 视频 / 音）

权威文案以 `village-canvas gen --help` 为准。**不带 `--dry-run` 的 gen 是真实
付费生成**：先 dry-run 看信封，确认目标端点与 body 后再真跑；批量跑前先读
`model capabilities` 确认模型合同。

```bash
# 图：/projects/{project}/freezone/gen
village-canvas gen image --prompt "…" [--node <节点id>] [--model <id>] \
  [--aspect-ratio 1:1] [--size …] [--quality …] [--style …] [--reference … --reference …] \
  [--character …] [--dry-run]

# 视频：/projects/{project}/freezone/video/gen
village-canvas gen video --prompt "…" [--node <id>] [--model <id>] \
  [--duration 5] [--aspect-ratio 9:16] [--mode textToVideo] [--camera-template …] \
  [--reference …] [--generate-audio|--no-generate-audio] [--dry-run]

# 音：/projects/{project}/freezone/audio/speech
village-canvas gen audio --text "台词" [--speaker …] [--voice-ref …] [--emotion …] \
  [--dialogue …] [--target-episode 0 --target-beat 1] [--dry-run]
```

生成提交后是后台任务：按提交回执里的任务信息，用 `task wait`（如
`--task-type freezone_gen --episode 0`）等终态，再用 `task result --job-id <uuid>`
取产出。没有 `--node` 时不会写画布节点，只出任务与产物。
