# StoryAI 3D Director Desk

> 来源：https://github.com/jiguang132/storyai-3d-director-desk
> 安装日期：2026-07-06
> 安装路径：`<LOCAL_PATH>`
> License：MIT

## 一句话

浏览器端 3D 分镜导演台——用 Three.js 在浏览器里搭场景、摆机位、做预演。

## 核心功能

- 导演视角 / 机位视角切换
- 8 种内置人物 + 20 种姿势预设
- 角色、群演、基础几何体、机位快速添加
- 本地 FBX / OBJ 模型导入
- 群众阵列（无限人数）
- 全景图导入与背景调节
- 机位拍摄、截图记录、镜头管理
- 视口比例框、九宫格、平移/旋转/缩放
- 本地 localStorage 持久化
- 导出/导入工程 JSON

## 技术栈

React 18 + Vite 6 + TypeScript + Three.js + React Three Fiber + Zustand

## 使用方式

```bash
cd <LOCAL_PATH>
npm run dev      # 开发 → http://<HOST>:5173
npm run build    # 构建
npm run preview  # 预览生产包
```

## 在 AIGC 导演流程中的位置

**预演阶段**：在生成 AI 视频之前，用这个工具：
1. 搭建 3D 场景（角色站位、道具位置）
2. 摆放虚拟机位（镜头角度、焦段）
3. 导出分镜截图作为 Seedance 图生视频的参考图
4. 记录每个镜头的机位参数

**桥接 AI 生成**：3D 预演截图 → Seedance/Kling 图生视频 → 剪映合成

## 快捷操作

- `Ctrl+C/V` 复制粘贴
- `Ctrl+Z` 撤销
- `Delete` 删除对象
- 顶部切换导演视角/机位视角