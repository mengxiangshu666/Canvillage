# 公开源码候选包

主名称为 **Canvillage**，英文全称为 **Infinite Canvas for Village Chiefs**，中文名为 **村长无限画布**。新的源码仓库为 [mengxiangshu666/Canvillage](https://github.com/mengxiangshu666/Canvillage)，先以私密状态建立，完成公开前检查后再决定可见性。

本项目的开发工作区、私人 Git 历史、公开源码和便携运行包分开保存。

运行 `scripts/prepare_public_source.py` 会按 Git 的 `export-ignore` 属性，导出当前工作树中已跟踪的文件及该导出器自己的新增文件。候选包不带 `.git`，不会继承旧提交、旧标签或旧构建归档；它也不会改动现有私有远端。

```powershell
.venv\Scripts\python.exe scripts\prepare_public_source.py
```

产物位于项目内 `workspace/artifacts/`，输出目录或同名归档已存在时拒绝覆盖。程序源码、前端资源、产品回归测试、Agent 技能及必要版权声明保留；内部交接、研究和状态记录，以及只检查这些记录的维护测试不随候选包导出。尚未复核的旧安装/服务器说明不带入候选包，源码安装以 README 为准，中英文许可说明保留。

导出器会为候选包重新过滤文件许可清单和 SPDX 文件关系，并移除密钥扫描的整提交放行规则。候选包必须再接受独立密钥扫描及文件完整性检查，才能进入发布评审。

现有许可仍为 `LICENSES/Elastic-2.0.txt` 所述 ELv2，必要的上游版权、商标和第三方声明保留。准备源码包不会赋予更换整个代码库许可证的权利，也不表示所有第三方素材已完成授权审查。

## 素材来源与发布范围

2026-10-09，仓库所有者确认：相机/镜头图片、运镜演示视频、登录背景以及小树知识包文章，均由其自行制作或已有公开分发授权。对应范围为：

- `frontend/public/images/camera/`、`frontend/public/images/lens/`。
- `frontend/public/video/camera-presets/`。
- `src/novelvideo/assets/login_bg_v1.mp4`、`login_bg_v2.mp4`、`login_bg_v3.mp4`。
- `agent_skills/village-canvas-aigc-knowledge/references/vault-aigc/source/`。

此记录是作者确认，未独立查验原授权合同，也不扩大任何第三方许可；文件内原有作者和许可声明优先。知识包 manifest 中的分类、路径脱敏和哈希只证明导出范围及完整性，本身不授予版权许可。

公开包排除闲置表情缩略图、伙伴图片、旧背景音乐及许可未核明的钉钉字体。本地保留这些文件，登录标题使用已有 Inter 和系统字体。ICT-FaceKit 头部模型保留 MIT 许可，Shotcraft 原文保留 Apache-2.0 许可；本地适配文件与上游原文分别记录。

公开前必须核对所有可达提交，不能只在最新版本删除素材。现有私有 Git Bundle、旧 Actions 归档和私人运行数据不得上传到新仓库。旧仓曾出现的凭据是否撤销，是旧仓独立安全待办；新仓不包含这些凭据或旧 Git 历史，不能用新仓扫描结果证明旧凭据已失效。
