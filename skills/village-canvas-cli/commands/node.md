# `village-canvas node` —— 节点、连线与相机

权威文案以 `village-canvas node --help` 与各子命令 `--help` 为准。
所有写子命令共享 `--dry-run / --command-id / --source-turn-id / --expected-revision`。

## 读

```bash
village-canvas node list [--full]        # --full 返回完整 data（含 imageUrl 等产出）
village-canvas node inspect --node <id>  # 单节点
```

## 建（文本 / 图片 / 视频）

```bash
village-canvas node create-text  --text "角色设定" [--label 角色卡] [--x 0 --y 0]
village-canvas node create-image --prompt "…" --model <图片模型id> \
  [--aspect-ratio 1:1] [--size …] [--count 2] [--quality …] \
  [--mode text_to_image|image_to_image] [--generate-audio|--no-generate-audio]
village-canvas node create-video --prompt "…" --model <视频模型id> \
  [--duration 5] [--aspect-ratio 9:16] [--mode textToVideo]
```

图生图（`--mode image_to_image`）必须绑参考图，文生图不能带参考图，否则 CLI 自己
拒绝（`canvas_image_mode_reference_mismatch`）。模型 id 必须来自
`model capabilities --kind image|video` 的目录，填不存在的 id 会被能力校验挡下。

## 改

```bash
village-canvas node update --node <id> --prompt "新提示词" [--label …] [--data '{…}']
village-canvas node move   --node <id> --x 120 --y 80
village-canvas node connect --source <上游id> --target <下游id>
village-canvas node duplicate --node <id> [--created-node-id 指定副本id] --yes?
village-canvas node delete --node <id> --yes    # 无 --yes 只回确认单
```

## 相机参数（图片机身/镜头、视频运镜）

```bash
# 图片节点：JSON 对象，字段见相机目录
village-canvas node camera --node <id> \
  --camera '{"camera_body_id":"arri_alexa_35","lens_id":"cooke_s4i","focal_length_mm":35,"aperture":"f/2.8"}'
# 视频节点：运镜模板 id 是纯字符串，不是 JSON
village-canvas node camera --node <id> --camera-movement follow_tracking
# 清空已保存的相机参数
village-canvas node camera --node <id> --clear-camera
```

可选值目录：

- 机身/镜头/焦距/光圈：`village-canvas api get /projects/{project}/freezone/image/camera-options`
- 视频运镜模板：`village-canvas api get /projects/{project}/freezone/video/camera-templates`

`--camera-movement` 传 JSON 会报「视频运镜模板不在当前画布目录中」——它收的是纯
字符串模板 id。相机写回的回执带 `camera_applied` 与 `camera_verified_from_snapshot`，
两者都 `true` 才算写成功。
