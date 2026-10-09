// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab

import { History, Trash2 } from "lucide-react";
import { useState } from "react";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import type { ChatConversation } from "@/features/superchat/chat-conversations";
import { cn } from "@/lib/utils";

interface ConversationHistoryMenuProps {
  conversations: ChatConversation[];
  activeConversationId: string;
  loading: boolean;
  busy: boolean;
  deletingId: string | null;
  onRefresh: () => void | Promise<unknown>;
  onSwitch: (conversationId: string) => boolean;
  onDelete: (conversationId: string) => Promise<boolean>;
}

export function ConversationHistoryMenu({
  conversations,
  activeConversationId,
  loading,
  busy,
  deletingId,
  onRefresh,
  onSwitch,
  onDelete,
}: ConversationHistoryMenuProps) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<ChatConversation | null>(null);
  const [deleteFailed, setDeleteFailed] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const deleteInFlight = submitting || deletingId !== null;

  const confirmDelete = async () => {
    if (!pendingDelete || deleteInFlight) return;
    setDeleteFailed(false);
    setSubmitting(true);
    try {
      const deleted = await onDelete(pendingDelete.id);
      if (deleted) {
        setPendingDelete(null);
        return;
      }
      setDeleteFailed(true);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <>
      <DropdownMenu
        open={menuOpen}
        onOpenChange={(open) => {
          setMenuOpen(open);
          if (open) void onRefresh();
        }}
      >
        <DropdownMenuTrigger
          render={
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              aria-label="历史对话"
              title="历史对话"
              className="village-agent-header-history rounded-full border border-transparent text-white/40 hover:border-white/[0.08] hover:bg-white/[0.06] hover:text-[#f7f7f7]"
            />
          }
        >
          <History className="size-4" aria-hidden />
        </DropdownMenuTrigger>
        <DropdownMenuContent
          side="bottom"
          align="end"
          sideOffset={8}
          className="village-agent-history-menu w-72"
        >
          <div className="px-2 pb-1.5 pt-1 text-[11px] font-semibold text-white/72">
            历史对话
          </div>
          {loading && conversations.length === 0 ? (
            <div className="px-2 py-3 text-center text-[11px] text-white/35">正在读取…</div>
          ) : conversations.length === 0 ? (
            <div className="px-2 py-3 text-center text-[11px] text-white/35">还没有对话</div>
          ) : conversations.map((conversation) => {
            const isActive = activeConversationId === conversation.id;
            const deleteDisabled = deleteInFlight || (busy && isActive);
            const title = conversation.title || "新对话";
            return (
              <div
                key={conversation.id}
                className="village-agent-history-row group/history relative"
              >
                <DropdownMenuItem
                  closeOnClick
                  disabled={busy}
                  onClick={() => onSwitch(conversation.id)}
                  className="village-agent-history-item min-h-12 items-start px-2.5 py-2 pr-10"
                >
                  <span
                    className={cn(
                      "mt-1.5 size-1.5 shrink-0 rounded-full bg-white/18",
                      isActive && "bg-emerald-300",
                    )}
                  />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-[12px] font-medium text-white/84">
                      {title}
                    </span>
                    <span className="mt-0.5 block truncate text-[10px] text-white/34">
                      {conversation.preview || `${conversation.message_count} 条消息`}
                    </span>
                  </span>
                </DropdownMenuItem>
                <button
                  type="button"
                  disabled={deleteDisabled}
                  aria-label={`删除对话：${title}`}
                  title={busy && isActive ? "当前会话执行结束后可删除" : "删除对话"}
                  className="village-agent-history-delete"
                  onClick={(event) => {
                    event.preventDefault();
                    event.stopPropagation();
                    if (deleteDisabled) return;
                    setDeleteFailed(false);
                    setMenuOpen(false);
                    setPendingDelete(conversation);
                  }}
                >
                  <Trash2 className="size-3.5" aria-hidden />
                </button>
              </div>
            );
          })}
        </DropdownMenuContent>
      </DropdownMenu>

      <AlertDialog
        open={pendingDelete !== null}
        onOpenChange={(open) => {
          if (!open && !deleteInFlight) {
            setPendingDelete(null);
            setDeleteFailed(false);
          }
        }}
      >
        <AlertDialogContent size="sm" className="village-agent-history-confirm">
          <AlertDialogHeader>
            <AlertDialogTitle>删除这个历史会话？</AlertDialogTitle>
            <AlertDialogDescription>
              会话消息和执行记录会一起删除。
            </AlertDialogDescription>
            {deleteFailed && (
              <p role="alert" className="text-xs text-red-300/90">
                删除失败，请根据提示处理后重试。
              </p>
            )}
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deleteInFlight}>取消</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              disabled={deleteInFlight}
              onClick={() => void confirmDelete()}
            >
              {deleteInFlight ? "正在删除…" : "删除"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
