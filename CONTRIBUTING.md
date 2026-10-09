# 贡献指南

感谢你考虑为 Canvillage（Infinite Canvas for Village Chiefs，村长无限画布）贡献。无论是修 bug、补文档还是加功能，都欢迎。

## 开始之前

- 请先读 [行为准则](CODE_OF_CONDUCT.md)。
- 关于许可：本项目采用 [Elastic License 2.0](LICENSES/Elastic-2.0.txt)（source available）。**提交贡献前，请读许可证正文与下方的[贡献者协议](#贡献者协议)。**
- 安全问题请勿走公开 issue，见 [SECURITY.md](SECURITY.md)。

## 报告 Bug / 提功能建议

- **Bug**：在 [Canvillage 的 Issues](https://github.com/mengxiangshu666/Canvillage/issues) 中附复现步骤、运行环境和脱敏日志。
- **功能建议**：在同一仓库的 Issues 中说明场景与动机。
- 不确定从哪入手？先看仓库中的现有问题及 `good first issue` 标签。

## 提交 PR

### 本地开发

从实际发布仓库检出源码后，按 [README 的源码安装步骤](README.md#源码与运行包) 安装依赖和构建前端。源码目录不包含便携运行时或你的模型凭据。

### 流程

1. Fork 仓库，从 `main` 切一个主题分支；
2. 改动 + 自测（`uv run pytest`）；
3. 提交信息清晰（建议 [Conventional Commits](https://www.conventionalcommits.org/)），并对**每个 commit** 用 `git commit -s` 附上 DCO 签署（见 [开发者原产证书](#开发者原产证书dco)）；
4. 开 PR，关联对应 issue，简述改动与验证方式。

## 贡献者协议

向本项目提交贡献，即表示你同意：

a. 维护方可按需调整本项目所采用的许可证（更严格或更宽松）；
b. 你贡献的代码可用于商业用途，包括但不限于 Canvillage 的云 / 托管业务运营。

> 这一条让 Canvillage 能在 source-available 许可下，把社区贡献也用于官方托管/商业版本——这是「同一套代码、社区与商业不分叉」得以成立的前提。提交贡献即视为接受，无需另外签署。

## 开发者原产证书（DCO）

本项目要求所有贡献通过 DCO（Developer Certificate of Origin 1.1，全文见 [`DCO`](./DCO)）签署 —— **每个 commit 都必须带 `Signed-off-by` 行**，以此声明你有权按本项目许可提交该贡献。

最简单的方式是提交时加 `-s`，git 会用你的 `user.name` / `user.email` 自动补上签署行：

```bash
git commit -s -m "fix: ……"
# 自动生成：Signed-off-by: Your Name <you@example.com>
```

补签历史 commit：`git rebase --signoff <base>`。提交 PR 前请核对每个 commit 的签署。

## 获取帮助

使用实际发布仓库启用的 Issues 或 Discussions。
