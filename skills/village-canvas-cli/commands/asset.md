# `village-canvas asset` —— 素材上传

权威文案以 `village-canvas asset --help` 为准。

```bash
village-canvas asset upload --file ./refs/scene.png [--field file]
```

- multipart 上传到 `/projects/{project}/freezone/upload`，标准库实现，无第三方依赖。
- `--field` 是表单字段名，默认 `file`，服务端改过字段名时才需要传。
- 上传回执里带产物定位（asset id / url），拿它去绑 `node create-image` 的
  参考图或 `gen` 的 `--reference`。

大文件与批量上传没有断点续传；失败重传即可，不要并发轰同一端点。
