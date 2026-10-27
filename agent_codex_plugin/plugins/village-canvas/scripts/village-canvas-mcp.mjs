#!/usr/bin/env node
// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
//
// Minimal MCP stdio bridge for the portable village-canvas CLI.
//
// It exposes the CLI's `serve` methods as MCP tools. Using `serve` keeps the
// plugin independent of the Python `mcp` package, so the bundled
// `runtime/python/python.exe` is enough.

import { spawn } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { createInterface } from "node:readline";

const HERE = dirname(fileURLToPath(import.meta.url));
const PLUGIN_ROOT = resolve(HERE, "..");
const VERSION = "1.0.0";

function log(message) {
  process.stderr.write(`[village-canvas] ${message}\n`);
}

function readHomeFile() {
  const file = join(homedir(), ".village-canvas", "home");
  try {
    if (!existsSync(file)) return "";
    return readFileSync(file, "utf8").trim();
  } catch {
    return "";
  }
}

function normalizePath(value) {
  return String(value || "").replace(/^\\\\\?\\/, "").trim();
}

// A local Codex marketplace install copies the plugin into a cache directory,
// so the relative checkout path is gone. Codex still records the marketplace
// source in its config; that source lives inside the product checkout.
function readCodexMarketplaceSource() {
  const codexHome = process.env.CODEX_HOME || join(homedir(), ".codex");
  const configFile = join(codexHome, "config.toml");
  try {
    if (!existsSync(configFile)) return "";
    const text = readFileSync(configFile, "utf8");
    const section = text.match(/\[marketplaces\.village-canvas\]([\s\S]*?)(?=\r?\n\[|$)/);
    if (!section) return "";
    const quoted = section[1].match(/source\s*=\s*(?:'([^']+)'|"([^"]+)")/);
    return normalizePath(quoted?.[1] || quoted?.[2] || "");
  } catch {
    return "";
  }
}

function looksLikeRepo(candidate) {
  return Boolean(candidate) && existsSync(join(candidate, "village_canvas_cli.py"));
}

function resolveRepoRoot() {
  const explicit = (process.env.VILLAGE_CANVAS_HOME || "").trim();
  if (looksLikeRepo(explicit)) return resolve(explicit);
  const saved = readHomeFile();
  if (looksLikeRepo(saved)) return resolve(saved);
  let marketplace = readCodexMarketplaceSource();
  if (marketplace) {
    marketplace = resolve(marketplace);
    for (let depth = 0; depth < 6; depth += 1) {
      if (looksLikeRepo(marketplace)) return marketplace;
      const parent = dirname(marketplace);
      if (parent === marketplace) break;
      marketplace = parent;
    }
  }
  let current = PLUGIN_ROOT;
  for (let depth = 0; depth < 8; depth += 1) {
    if (looksLikeRepo(current)) return current;
    const parent = dirname(current);
    if (parent === current) break;
    current = parent;
  }
  return "";
}

const REPO_ROOT = resolveRepoRoot();
if (!REPO_ROOT) {
  log(
    "cannot find the village-canvas checkout. Set VILLAGE_CANVAS_HOME or write " +
      `${join(homedir(), ".village-canvas", "home")} with the project root.`,
  );
  process.exit(1);
}

function resolvePython() {
  const candidates = [
    join(REPO_ROOT, "runtime", "python", "python.exe"),
    join(REPO_ROOT, ".venv", "Scripts", "python.exe"),
    join(REPO_ROOT, ".venv", "bin", "python"),
  ];
  for (const candidate of candidates) {
    if (existsSync(candidate)) return candidate;
  }
  return process.platform === "win32" ? "python.exe" : "python3";
}

const PYTHON = resolvePython();
const CLI = join(REPO_ROOT, "village_canvas_cli.py");
const WORKDIR = (process.env.VILLAGE_CANVAS_WORKDIR || REPO_ROOT).trim() || REPO_ROOT;

let serve = null;
let serveReady = null;
let nextId = 1;
const pending = new Map();

function startServe() {
  if (serve && !serve.killed) return serve;
  serve = spawn(PYTHON, [CLI, "serve"], {
    cwd: WORKDIR,
    env: { ...process.env, PYTHONIOENCODING: "utf-8" },
    windowsHide: true,
  });
  const reader = createInterface({ input: serve.stdout });
  reader.on("line", (line) => {
    const text = line.trim();
    if (!text.startsWith("{")) return;
    let payload;
    try {
      payload = JSON.parse(text);
    } catch {
      return;
    }
    if (!payload || payload.id === undefined) return;
    const waiter = pending.get(payload.id);
    if (!waiter) return;
    pending.delete(payload.id);
    if (payload.error) {
      waiter.reject(new Error(payload.error.message || JSON.stringify(payload.error)));
    } else {
      waiter.resolve(payload.result);
    }
  });
  serve.stderr.on("data", (chunk) => {
    const text = chunk.toString("utf8").trim();
    if (text) log(text);
  });
  serve.on("exit", (code) => {
    const error = new Error(`village-canvas serve exited (${code ?? "unknown"})`);
    for (const waiter of pending.values()) waiter.reject(error);
    pending.clear();
    serve = null;
    serveReady = null;
  });
  serveReady = new Promise((resolveReady, rejectReady) => {
    const timer = setTimeout(() => resolveReady(), 300);
    serve.once("error", (error) => {
      clearTimeout(timer);
      rejectReady(error);
    });
  });
  return serve;
}

async function callServe(method, params = {}) {
  startServe();
  await serveReady;
  const id = nextId++;
  return new Promise((resolveCall, rejectCall) => {
    pending.set(id, { resolve: resolveCall, reject: rejectCall });
    const payload = JSON.stringify({ jsonrpc: "2.0", id, method, params }) + "\n";
    serve.stdin.write(payload, "utf8", (error) => {
      if (!error) return;
      pending.delete(id);
      rejectCall(error);
    });
  });
}

function runHelp(tool) {
  const method = String(tool || "").trim();
  if (!method) return Promise.reject(new Error("tool is required"));
  const args = method.includes(".") ? method.split(".") : [method];
  return new Promise((resolveHelp, rejectHelp) => {
    const child = spawn(PYTHON, [CLI, ...args, "--help"], {
      cwd: WORKDIR,
      env: { ...process.env, PYTHONIOENCODING: "utf-8" },
      windowsHide: true,
    });
    let output = "";
    child.stdout.on("data", (chunk) => {
      output += chunk.toString("utf8");
    });
    child.stderr.on("data", (chunk) => {
      output += chunk.toString("utf8");
    });
    child.on("error", rejectHelp);
    child.on("exit", () => resolveHelp(output.trim() || "(no help output)"));
  });
}

const DESCRIPTIONS = {
  health: "检查村长无限画布本机服务（默认 127.0.0.1:8784）是否在线。",
  "project.list": "列出本机项目。",
  "project.inspect": "读取一个项目的详情。",
  "canvas.list": "列出项目下的画布。",
  "canvas.use": "绑定当前工作目录到某个项目/画布，写入 .village-canvas.json。",
  "canvas.context": "读取画布的 revision、节点、连线和视口，是最常用的观察入口。",
  "canvas.inspect": "读取画布的结构快照与统计。",
  "canvas.apply": "批量执行画布命令（commands 数组），带 command_id 与 expected_revision，返回回执。",
  "canvas.create": "新建画布。",
  "canvas.delete": "删除画布（需要显式确认）。",
  "node.list": "列出画布节点。",
  "node.inspect": "读取单个节点的完整数据。",
  "node.create-image": "创建图片生成节点。",
  "node.create-text": "创建文本节点。",
  "node.create-video": "创建视频生成节点。",
  "node.update": "更新节点字段（提示词、参数、名称等）。",
  "node.move": "移动节点位置。",
  "node.connect": "连接两个节点。",
  "node.delete": "删除节点（需要显式确认）。",
  "node.duplicate": "复制节点。",
  "model.list": "列出可用模型。",
  "model.capabilities": "读取模型支持的参数目录；写媒体参数前先查这里。",
  "task.list": "列出后台任务。",
  "task.inspect": "读取任务状态和回执。",
  "task.wait": "等待任务到达终态。",
  "task.stop": "停止任务。",
  "workflow.list": "列出工作流定义。",
  "workflow.start": "启动工作流运行。",
  "workflow.status": "读取工作流运行状态。",
  "workflow.runs": "列出工作流运行。",
  "gen.image": "直接触发生成图片（可能产生费用，先确认授权）。",
  "gen.video": "直接触发生成视频（可能产生费用，先确认授权）。",
  "gen.audio": "直接触发生成音频（可能产生费用，先确认授权）。",
};

function describeTool(name) {
  return DESCRIPTIONS[name] || `执行 village-canvas 的 ${name} 操作。参数可用 village_canvas_help 查询。`;
}

function toolSchema(name) {
  if (name === "canvas.apply") {
    return {
      type: "object",
      properties: {
        commands: {
          type: "array",
          description: "画布命令数组；每条命令带 type 和字段。",
          items: { type: "object", additionalProperties: true },
        },
        expected_revision: { type: "number", description: "预期画布 revision，用于冲突保护。" },
        dry_run: { type: "boolean", description: "只校验不落库。" },
      },
      required: ["commands"],
      additionalProperties: true,
    };
  }
  return {
    type: "object",
    properties: {},
    additionalProperties: true,
    description: "参数与 village-canvas CLI 子命令一致；用 village_canvas_help 查询具体字段。",
  };
}

const HELP_TOOL = {
  name: "village_canvas_help",
  description: "查看某个画布工具的参数说明，例如 tool=canvas.apply。",
  inputSchema: {
    type: "object",
    properties: {
      tool: { type: "string", description: "工具名，如 canvas.apply、node.create-image。" },
    },
    required: ["tool"],
    additionalProperties: false,
  },
};

async function listTools() {
  const result = await callServe("tools/list", {});
  const names = Array.isArray(result?.tools) ? result.tools.map((item) => item.name) : [];
  const tools = names
    .filter((name) => typeof name === "string" && name.length > 0)
    .sort()
    .map((name) => ({
      name,
      description: describeTool(name),
      inputSchema: toolSchema(name),
    }));
  tools.unshift(HELP_TOOL);
  return tools;
}

async function callTool(name, args) {
  if (name === HELP_TOOL.name) {
    return runHelp(args?.tool);
  }
  return callServe(name, args || {});
}

function writeMessage(message) {
  process.stdout.write(JSON.stringify(message) + "\n");
}

async function handleRequest(message) {
  const { id, method, params } = message;
  if (method === "initialize") {
    writeMessage({
      jsonrpc: "2.0",
      id,
      result: {
        protocolVersion: params?.protocolVersion || "2024-11-05",
        capabilities: { tools: {}, resources: {}, prompts: {} },
        serverInfo: { name: "village-canvas", version: VERSION },
      },
    });
    return;
  }
  if (method === "notifications/initialized") return;
  if (method === "ping") {
    writeMessage({ jsonrpc: "2.0", id, result: {} });
    return;
  }
  if (method === "tools/list") {
    const tools = await listTools();
    writeMessage({ jsonrpc: "2.0", id, result: { tools } });
    return;
  }
  if (method === "tools/call") {
    const name = String(params?.name || "");
    try {
      const result = await callTool(name, params?.arguments || {});
      const text = typeof result === "string" ? result : JSON.stringify(result, null, 2);
      writeMessage({
        jsonrpc: "2.0",
        id,
        result: { content: [{ type: "text", text: text || "(empty result)" }], isError: false },
      });
    } catch (error) {
      writeMessage({
        jsonrpc: "2.0",
        id,
        result: {
          content: [{ type: "text", text: `Error: ${error instanceof Error ? error.message : String(error)}` }],
          isError: true,
        },
      });
    }
    return;
  }
  if (method === "resources/list") {
    writeMessage({ jsonrpc: "2.0", id, result: { resources: [] } });
    return;
  }
  if (method === "prompts/list") {
    writeMessage({ jsonrpc: "2.0", id, result: { prompts: [] } });
    return;
  }
  writeMessage({
    jsonrpc: "2.0",
    id,
    error: { code: -32601, message: `Method not found: ${method}` },
  });
}

// stdio framing: MCP normally uses newline-delimited JSON; also accept the
// Content-Length header form used by some legacy clients.
let buffer = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => {
  buffer += chunk;
  for (;;) {
    const headerMatch = buffer.match(/^Content-Length:\s*(\d+)\r?\n\r?\n/i);
    if (headerMatch) {
      const headerLength = headerMatch[0].length;
      const bodyLength = Number(headerMatch[1]);
      if (buffer.length < headerLength + bodyLength) return;
      const body = buffer.slice(headerLength, headerLength + bodyLength);
      buffer = buffer.slice(headerLength + bodyLength);
      dispatch(body);
      continue;
    }
    const newline = buffer.indexOf("\n");
    if (newline < 0) return;
    const line = buffer.slice(0, newline).trim();
    buffer = buffer.slice(newline + 1);
    if (line) dispatch(line);
  }
});

function dispatch(raw) {
  let message;
  try {
    message = JSON.parse(raw);
  } catch {
    return;
  }
  if (!message || typeof message !== "object") return;
  void handleRequest(message).catch((error) => {
    if (message.id === undefined) return;
    writeMessage({
      jsonrpc: "2.0",
      id: message.id,
      error: { code: -32603, message: error instanceof Error ? error.message : String(error) },
    });
  });
}

process.stdin.on("end", () => {
  if (serve && !serve.killed) serve.kill();
});
