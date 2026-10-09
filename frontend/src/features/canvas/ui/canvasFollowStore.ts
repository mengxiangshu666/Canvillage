// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { create } from 'zustand';

/**
 * 「跟随模式」：视口锁定在某个节点上，它被拖动 / 被上游重算时相机跟着走。
 *
 * 单独一个 store 而不是塞进 `canvasStore`：这是一条**视图**状态（不参与画布
 * 持久化、不进 undo 栈、不影响导出），和画布数据放一起只会让撤销栈和保存
 * 逻辑多一份要排除的东西。
 *
 * 与「聚焦」的区别：聚焦是一次性的相机动画，跟随时持续生效直到用户取消。
 */
interface CanvasFollowState {
  /** 正在跟随的节点 id；null 表示未开启。 */
  followedNodeId: string | null;
  /** 展示用的节点名，跟随条上要写「正在跟随 XXX」。 */
  followedLabel: string | null;
  startFollow: (nodeId: string, label: string) => void;
  stopFollow: () => void;
}

export const useCanvasFollowStore = create<CanvasFollowState>((set) => ({
  followedNodeId: null,
  followedLabel: null,
  startFollow: (nodeId, label) => set({ followedNodeId: nodeId, followedLabel: label }),
  stopFollow: () => {
    // 已经是空的时候不写：避免 ESC 在什么都没跟随时也推一次订阅回调。
    set((state) =>
      state.followedNodeId === null ? state : { followedNodeId: null, followedLabel: null },
    );
  },
}));
