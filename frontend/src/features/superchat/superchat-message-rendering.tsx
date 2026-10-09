// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { Wrench } from "lucide-react";
import type { ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkBreaks from "remark-breaks";
import remarkGfm from "remark-gfm";
import { AgentMark } from "@/features/superchat/freezone-canvas-agent-ui";
import type { ChatMessage } from "@/features/superchat/types";
import { cn } from "@/lib/utils";

function normalizeMessageText(text: string): string {
  return text.trim().replace(/\n{3,}/g, "\n\n");
}

function PlainMessageText({ text }: { text: string }) {
  const paragraphs = normalizeMessageText(text)
    .split(/\n{2}/)
    .map((paragraph) => paragraph.trim())
    .filter(Boolean);

  if (paragraphs.length === 0) return null;

  return (
    <div className="space-y-2 break-words leading-relaxed">
      {paragraphs.map((paragraph, index) => (
        <p key={`${index}-${paragraph.slice(0, 12)}`} className="whitespace-pre-wrap">
          {paragraph}
        </p>
      ))}
    </div>
  );
}

function MarkdownMessageText({ text }: { text: string }) {
  const normalized = normalizeMessageText(text);
  if (!normalized) return null;

  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm, remarkBreaks]}
      components={{
        h1: ({ children }) => <h1 className="mb-2 mt-3 text-lg font-semibold leading-7 first:mt-0">{children}</h1>,
        h2: ({ children }) => <h2 className="mb-2 mt-3 text-base font-semibold leading-6 first:mt-0">{children}</h2>,
        h3: ({ children }) => <h3 className="mb-1.5 mt-2.5 text-sm font-semibold leading-6 first:mt-0">{children}</h3>,
        p: ({ children }) => <p className="my-1.5 first:mt-0 last:mb-0">{children}</p>,
        ul: ({ children }) => <ul className="my-1.5 list-disc space-y-1 pl-5">{children}</ul>,
        ol: ({ children }) => <ol className="my-1.5 list-decimal space-y-1 pl-5">{children}</ol>,
        li: ({ children }) => <li className="pl-0.5">{children}</li>,
        strong: ({ children }) => <strong className="font-semibold text-foreground">{children}</strong>,
        em: ({ children }) => <em className="italic">{children}</em>,
        a: ({ children, href }) => (
          <a
            href={href}
            target="_blank"
            rel="noreferrer"
            className="text-primary underline underline-offset-2"
          >
            {children}
          </a>
        ),
        code: ({ children }) => (
          <code className="rounded bg-muted px-1 py-0.5 font-mono text-[0.92em]">{children}</code>
        ),
        pre: ({ children }) => (
          <pre className="my-2 max-w-full overflow-x-auto rounded-md border border-border/70 bg-muted/35 p-2 text-xs leading-5">
            {children}
          </pre>
        ),
        hr: () => <hr className="my-4 border-0 border-t border-white/[0.08]" />,
        blockquote: ({ children }) => (
          <blockquote className="my-2 border-l-2 border-border pl-3 text-muted-foreground">{children}</blockquote>
        ),
      }}
    >
      {normalized}
    </ReactMarkdown>
  );
}

export function MessageText({
  text,
  markdown = false,
}: {
  text: string;
  markdown?: boolean;
}) {
  return markdown
    ? <MarkdownMessageText text={text} />
    : <PlainMessageText text={text} />;
}

const ASSISTANT_ERROR_TEXT_PATTERNS: RegExp[] = [ // 纯展示层，只决定气泡配色，禁止驱动状态机
  /模型内容安全过滤拦截/u,
  /Render 任务没有生成可用图片/u,
  /错误原因：.+/u,
  /生成.+失败/u,
  /任务.+失败/u,
  /没有成功启动/u,
  /请先根据返回的错误/u,
  /content filter triggered/i,
  /finish reason:\s*['"]?content_filter/i,
];

export function isAssistantErrorReply(message: ChatMessage): boolean {
  if (message.role !== "assistant") return false;
  const text = message.text.trim();
  if (!text) return false;
  return ASSISTANT_ERROR_TEXT_PATTERNS.some((pattern) => pattern.test(text)); // 仅决定样式
}

export function isAssistantCompletionNotice(message: ChatMessage): boolean {
  if (message.role !== "assistant") return false;
  return /^✅ .+已完成。/u.test(message.text.trim()); // 仅决定样式
}

function errorTextRanges(text: string): Array<[number, number]> {
  const ranges: Array<[number, number]> = [];
  for (const pattern of ASSISTANT_ERROR_TEXT_PATTERNS) {
    const match = pattern.exec(text);
    if (!match || match.index < 0) continue;
    const start = match.index;
    let end = start + match[0].length;
    while (end < text.length && !/[。！？\n]/u.test(text[end])) {
      end += 1;
    }
    if (end < text.length && /[。！？]/u.test(text[end])) {
      end += 1;
    }
    ranges.push([start, end]);
  }
  return ranges.sort((a, b) => a[0] - b[0]);
}

export function HighlightedErrorText({ text }: { text: string }) {
  const ranges = errorTextRanges(text);
  if (ranges.length === 0) return <MessageText text={text} markdown />;

  const nodes: ReactNode[] = [];
  let cursor = 0;
  ranges.forEach(([start, end], index) => {
    if (start > cursor) {
      nodes.push(<MessageText key={`normal-${index}`} text={text.slice(cursor, start)} markdown />);
    }
    nodes.push(
      <span key={`error-${index}`} className="text-red-300">
        {text.slice(start, end)}
      </span>,
    );
    cursor = Math.max(cursor, end);
  });
  if (cursor < text.length) {
    nodes.push(<MessageText key="normal-tail" text={text.slice(cursor)} markdown />);
  }
  return <div className="space-y-1.5">{nodes}</div>;
}

export function HighlightedCompletionText({ text }: { text: string }) {
  const match = /^✅ .+?已完成。/u.exec(text);
  if (!match) return <MessageText text={text} markdown />;
  const end = match[0].length;
  return (
    <div className="break-words leading-relaxed whitespace-pre-wrap">
      <span className="text-emerald-300">{text.slice(0, end)}</span>
      <span>{text.slice(end)}</span>
    </div>
  );
}

export function DotsIndicator({ label, dotClassName = "size-1.5" }: { label?: string; dotClassName?: string }) {
  return (
    <div className="flex items-center gap-2" aria-live="polite" aria-label={label}>
      <span className="flex items-center gap-1">
        <span className={cn(dotClassName, "rounded-full bg-muted-foreground/50 animate-bounce [animation-delay:0ms]")} />
        <span className={cn(dotClassName, "rounded-full bg-muted-foreground/50 animate-bounce [animation-delay:150ms]")} />
        <span className={cn(dotClassName, "rounded-full bg-muted-foreground/50 animate-bounce [animation-delay:300ms]")} />
      </span>
      {label && <span className="sr-only">{label}</span>}
    </div>
  );
}

export function ChatAvatarFrame({
  role,
  label,
  streaming = false,
}: {
  role: ChatMessage["role"];
  label?: string;
  streaming?: boolean;
}) {
  const isAssistant = role === "assistant";
  const isTool = role === "tool";
  const initial = label?.trim().charAt(0).toUpperCase() || (isAssistant ? "虾" : isTool ? "" : "U");

  // Assistant uses the same neutral Agent mark as the drawer header.
  if (isAssistant) {
    return (
      <div
        className="pointer-events-none relative flex size-11 shrink-0 select-none items-center justify-center"
        aria-hidden="true"
        title={undefined}
      >
        <AgentMark size="xl" className={cn(streaming && "agent-mark--pulse")} />
      </div>
    );
  }

  return (
    <div
      className={cn(
        "pointer-events-none relative flex size-10 shrink-0 select-none items-center justify-center overflow-hidden rounded-full border text-xs font-medium shadow-sm",
        isTool
          ? "border-amber-500/30 bg-amber-500/10 text-amber-500"
          : "border-primary/20 bg-primary text-primary-foreground",
      )}
      aria-hidden="true"
      title={undefined}
    >
      {isTool ? <Wrench className="size-4" /> : initial}
    </div>
  );
}
