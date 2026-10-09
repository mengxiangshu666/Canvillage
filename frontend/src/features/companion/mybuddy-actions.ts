// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
/**
 * 搭子动作词表。
 *
 * 2026-09-16 起只保留「任务驱动」的四态：形象是 petdex 精灵图，9 个标准状态里真正
 * 用得上的是 Running / Waving / Failed / Idle；原先给 Piko 像素小人用的十几个闲置
 * 巡逻动作（count-stars / sleep / walk-by / dive …）随 Piko 一并删掉。
 */
export const MYBUDDY_ACTIONS = [
  { id: "idle", labelKey: "myBuddy.actions.idle" },
  { id: "typing", labelKey: "myBuddy.actions.typing" },
  { id: "flag", labelKey: "myBuddy.actions.flag" },
  { id: "repair", labelKey: "myBuddy.actions.repair" },
] as const;

export type MyBuddyAction = (typeof MYBUDDY_ACTIONS)[number]["id"];

export const DEFAULT_MYBUDDY_ACTION: MyBuddyAction = "idle";
