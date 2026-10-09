# `village-canvas model` —— 模型目录

权威文案以 `village-canvas model --help` 为准。模型能力是唯一参数合同来源；
不要凭记忆给节点或生成命令写模型参数。

```bash
village-canvas model list                        # 全量模型目录（不含任何 Key）
village-canvas model config                      # 模型中心配置读数
village-canvas model capabilities --kind image   # 按 kind 过滤：agent/text/vision/image/embedding/audio/video
village-canvas model capabilities --kind video --model-id <id>   # 单模型详情：尺寸/时长/声音/参考素材合同
village-canvas model check --kind agent          # 运行就绪检查
```

读数里的 `apiKeyPreview` 只是掩码预览；CLI 不接收、不打印、不落盘完整 Key。
给视频节点写参数前，先读 `model capabilities --kind video --model-id <id>`，
只写它声明支持的尺寸、时长、声音与参考素材字段。
