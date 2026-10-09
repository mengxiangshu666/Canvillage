# 配方：临时画布上的编辑试验循环（建→改→删）

**前置**：服务健康；已 `project list` 拿到项目 id。
**会真实写画布**：全部写在一张临时画布上，跑完即删，不碰用户画布。

```bash
# 0) 保险丝：先绑定到一张专用空画布
village-canvas --project <项目ID> canvas create --scope blank --canvas-id blank-demo
village-canvas canvas use blank-demo

# 1) 建节点（真实写入；想先看载荷就加 --dry-run）
village-canvas node create-text --text "角色设定：村长，四十岁，糙汉" --label 角色卡
village-canvas node create-image --prompt "村长在麦田里，暖光" --model <图片模型id>

# 2) 改与连
village-canvas node update --node <文本节点id> --prompt "改后的提示词"
village-canvas node connect --source <文本节点id> --target <图片节点id>

# 3) 相机（可选）
village-canvas node camera --node <图片节点id> --camera '{"camera_body_id":"arri_alexa_35"}'

# 4) 每一步的回执都核对三件事
#    ok=true / canvas_revision 递增 / verified=true（与权威画布对账过）

# 5) 跑完即删，不把试验垃圾留给用户
village-canvas canvas delete --yes
village-canvas canvas unuse
```

要点：

- 每个 `node ...` 回执带 `command_id` 与 `affected_node_ids`，可与节点列表对账。
- 建图/视频节点时模型 id 必须来自 `model capabilities` 目录，否则被 CLI 能力
  校验挡下。
- 想把多步合成一次提交，把命令对象写进 JSON 文件走 `canvas apply --file`，
  服务端逐条受理、逐条回执。
