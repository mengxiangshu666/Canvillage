const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require("playwright");

function required(name) {
  const value = String(process.env[name] ?? "").trim();
  if (!value) throw new Error(`missing ${name}`);
  return value;
}

const projectId = required("T113_PROJECT_ID");
const canvasId = required("T113_CANVAS_ID");
const scriptNodeId = required("T113_SCRIPT_NODE_ID");
const uiBase = required("T113_UI_BASE").replace(/\/+$/, "");
const apiBase = required("T113_API_BASE").replace(/\/+$/, "");
const chromePath = required("T113_CHROME_PATH");
const evidencePath = path.resolve(required("T113_EVIDENCE_PATH"));
const screenshotPath = path.resolve(required("T113_SCREENSHOT_PATH"));
const requestText =
  String(process.env.T113_REQUEST_TEXT ?? "").trim()
  || (
    "从当前脚本节点继续到最终成片。只执行镜 1、5 秒；"
    + "分镜图固定 16:9、1K；视频使用 MiniMax-H3、768p、16:9、5 秒，"
    + "并以该镜分镜图作为首帧。不要追问，直接执行。"
  );
const completedWaitMs = Number(
  process.env.T113_COMPLETED_WAIT_MS ?? 900_000,
);

const browserRequests = [];
const websocketFrames = [];
const websocketLifecycle = [];
const canvasWrites = [];
const pageErrors = [];
const consoleErrors = [];
const consoleMessages = [];
const workflowRunExchanges = [];
const workflowRunExchangeTasks = [];

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

async function readJson(url) {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`GET ${url} -> ${response.status}: ${await response.text()}`);
  }
  return (await response.json()).data;
}

function readRuns() {
  return readJson(
    `${apiBase}/projects/${projectId}/workflow-runs?canvas_id=${encodeURIComponent(canvasId)}`,
  );
}

function readCanvas() {
  return readJson(
    `${apiBase}/projects/${projectId}/freezone/canvases/${canvasId}`,
  );
}

function readTasks() {
  return readJson(`${apiBase}/projects/${projectId}/tasks`);
}

function parseCanvasRequest(framePayload) {
  const text = typeof framePayload === "string"
    ? framePayload
    : Buffer.from(framePayload).toString("utf8");
  let frame;
  try {
    frame = JSON.parse(text);
  } catch {
    return null;
  }
  if (frame?.type !== "chat.message") return null;
  const match = /\[CANVAS_AGENT_REQUEST_V2\]\s*([\s\S]*?)\s*\[\/CANVAS_AGENT_REQUEST_V2\]/i
    .exec(String(frame.text ?? ""));
  if (!match) return null;
  try {
    return JSON.parse(match[1]);
  } catch {
    return null;
  }
}

function frameText(framePayload) {
  return typeof framePayload === "string"
    ? framePayload
    : Buffer.from(framePayload).toString("utf8");
}

function frameSummary(framePayload) {
  const text = frameText(framePayload);
  try {
    const parsed = JSON.parse(text);
    return {
      type: typeof parsed?.type === "string" ? parsed.type : "",
      turn_id: typeof parsed?.turn_id === "string" ? parsed.turn_id : "",
      scope: parsed?.scope ?? null,
    };
  } catch {
    return { type: "", turn_id: "", scope: null };
  }
}

function recordWebsocketFrame(websocket, direction, framePayload) {
  const text = frameText(framePayload);
  websocketFrames.push({
    at: new Date().toISOString(),
    websocket_url: websocket.url(),
    direction,
    ...frameSummary(framePayload),
    payload: text.slice(0, 60_000),
  });
}

function activeAgentTurnFinished() {
  return websocketFrames.some((frame) => {
    if (frame.direction !== "received" || frame.type !== "chat.done") {
      return false;
    }
    const scope = frame.scope;
    return (
      scope?.kind === "project"
      && scope.id === projectId
      && scope.canvas_id === canvasId
    );
  });
}

function activeAgentTurnFailure() {
  const activeTurn = websocketFrames.find((frame) => {
    if (frame.direction !== "received" || frame.type !== "thread.started") {
      return false;
    }
    const scope = frame.scope;
    return (
      scope?.kind === "project"
      && scope.id === projectId
      && scope.canvas_id === canvasId
    );
  });
  const activeTurnId = String(activeTurn?.turn_id ?? "");
  for (const frame of websocketFrames) {
    if (frame.direction !== "received") continue;
    if (activeTurnId && frame.turn_id && frame.turn_id !== activeTurnId) continue;
    let payload;
    try {
      payload = JSON.parse(frame.payload);
    } catch {
      continue;
    }
    const eventType = String(payload?.type ?? "");
    const agentEventType = String(payload?.agent_event?.type ?? "");
    if (
      eventType === "chat.recoverable"
      || eventType === "error"
      || agentEventType === "run.failed"
    ) {
      return {
        type: agentEventType || eventType,
        message: String(payload?.message ?? payload?.agent_event?.payload?.message ?? ""),
      };
    }
  }
  return null;
}

async function dismissReleaseNotes(page) {
  const known = page.getByRole("button", { name: "我知道了" });
  try {
    await known.waitFor({ state: "visible", timeout: 8_000 });
    await known.click();
    await known.waitFor({ state: "hidden", timeout: 8_000 });
  } catch {
    // A fresh browser profile normally has no release-note dialog.
  }
}

async function ensureAgentOpen(page) {
  const composer = page.locator("textarea.neo-agent-textarea");
  if (await composer.isVisible().catch(() => false)) return composer;
  const openAgent = page.locator('[aria-label="打开搭子创作台"]');
  if ((await openAgent.count()) > 0 && await openAgent.isVisible().catch(() => false)) {
    await openAgent.click();
    await page.waitForTimeout(800);
  }
  await composer.waitFor({ state: "visible", timeout: 60_000 });
  return composer;
}

async function waitForTerminalRun(expectedRunId = "", timeoutMs = completedWaitMs) {
  const deadline = Date.now() + timeoutMs;
  let runs = [];
  while (Date.now() < deadline) {
    runs = await readRuns();
    if (runs.length > 1) {
      throw new Error(`production run created parallel runs: ${JSON.stringify(runs)}`);
    }
    if (
      runs.length === 1
      && (!expectedRunId || runs[0].id === expectedRunId)
      && ["completed", "failed", "cancelled"].includes(runs[0].status)
    ) {
      return runs[0];
    }
    const agentFailure = activeAgentTurnFailure();
    if (runs.length === 0 && agentFailure) {
      throw new Error(
        `agent turn failed before creating a WorkflowRun: `
        + `${agentFailure.type}: ${agentFailure.message}`,
      );
    }
    if (runs.length === 0 && activeAgentTurnFinished()) {
      await new Promise((resolve) => setTimeout(resolve, 1_500));
      runs = await readRuns();
      if (runs.length === 0) {
        throw new Error(
          "agent turn completed without creating a WorkflowRun; "
          + "the structured production request failed before durable execution",
        );
      }
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error(`production run timeout: ${JSON.stringify(runs)}`);
}

function sha256File(filePath) {
  const digest = crypto.createHash("sha256");
  digest.update(fs.readFileSync(filePath));
  return digest.digest("hex");
}

function writeEvidence(payload) {
  fs.mkdirSync(path.dirname(evidencePath), { recursive: true });
  fs.writeFileSync(evidencePath, JSON.stringify(payload, null, 2) + "\n", "utf8");
}

async function main() {
  assert(
    Number.isFinite(completedWaitMs) && completedWaitMs > 0,
    `invalid completed wait: ${completedWaitMs}`,
  );
  const runsBefore = await readRuns();
  assert(runsBefore.length === 0, "temporary production project must start without runs");
  let canvasBefore = await readCanvas();

  const browser = await chromium.launch({
    headless: true,
    executablePath: chromePath,
  });
  const page = await browser.newPage({ viewport: { width: 1440, height: 960 } });
  let evidence = {
    schema: "t113_production_browser_evidence.v1",
    project_id: projectId,
    canvas_id: canvasId,
    script_node_id: scriptNodeId,
    ui_base_url: uiBase,
    api_base_url: apiBase,
    request_text: requestText,
    started_at: new Date().toISOString(),
    runs_before: runsBefore,
    canvas_before: canvasBefore,
  };

  try {
    await page.addInitScript(() => {
      window.localStorage.setItem("village_canvas_agent_run_mode", "auto");
      window.localStorage.setItem(
        "village-canvas:release-notifications:muted",
        "true",
      );
    });
    page.on("websocket", (websocket) => {
      websocketLifecycle.push({
        at: new Date().toISOString(),
        event: "created",
        websocket_url: websocket.url(),
      });
      websocket.on("framesent", (frame) => {
        recordWebsocketFrame(websocket, "sent", frame.payload);
        const payload = parseCanvasRequest(frame.payload);
        if (payload) browserRequests.push(payload);
      });
      websocket.on("framereceived", (frame) => {
        recordWebsocketFrame(websocket, "received", frame.payload);
      });
      websocket.on("close", () => {
        websocketLifecycle.push({
          at: new Date().toISOString(),
          event: "closed",
          websocket_url: websocket.url(),
        });
      });
      websocket.on("socketerror", (error) => {
        websocketLifecycle.push({
          at: new Date().toISOString(),
          event: "error",
          websocket_url: websocket.url(),
          message: String(error),
        });
      });
    });
    page.on("pageerror", (error) => pageErrors.push(String(error)));
    page.on("console", (message) => {
      consoleMessages.push({
        at: new Date().toISOString(),
        type: message.type(),
        text: message.text(),
      });
      if (message.type() === "error") consoleErrors.push(message.text());
    });
    page.on("response", (response) => {
      if (
        response.request().method() !== "POST"
        || !response.url().includes("/workflow-runs")
      ) {
        return;
      }
      const task = (async () => {
        let requestBody = null;
        let responseBody = "";
        try {
          requestBody = response.request().postDataJSON();
        } catch {
          requestBody = response.request().postData() ?? null;
        }
        try {
          responseBody = await response.text();
        } catch (error) {
          responseBody = `<unreadable: ${String(error)}>`;
        }
        workflowRunExchanges.push({
          url: response.url(),
          status: response.status(),
          request_body: requestBody,
          response_body: responseBody.slice(0, 40_000),
        });
      })();
      workflowRunExchangeTasks.push(task);
    });
    page.on("request", (request) => {
      if (
        ["PUT", "POST", "PATCH", "DELETE"].includes(request.method())
        && request.url().includes(`/freezone/canvases/${canvasId}`)
      ) {
        canvasWrites.push(`${request.method()} ${request.url()}`);
      }
    });

    await page.goto(
      `${uiBase}/projects/${projectId}/freezone?canvas=${canvasId}`,
      { waitUntil: "domcontentloaded", timeout: 60_000 },
    );
    await page.waitForTimeout(2_000);
    await dismissReleaseNotes(page);
    const scriptNode = page.locator(`.react-flow__node[data-id="${scriptNodeId}"]`);
    await scriptNode.waitFor({ state: "visible", timeout: 60_000 });
    await scriptNode.click({ position: { x: 80, y: 30 } });
    const composer = await ensureAgentOpen(page);
    await page
      .getByText("在线", { exact: true })
      .last()
      .waitFor({ state: "visible", timeout: 60_000 });

    for (let attempt = 0; attempt < 6; attempt += 1) {
      await page.waitForTimeout(400);
      const settled = await readCanvas();
      if (settled.revision === canvasBefore.revision) break;
      canvasBefore = settled;
    }
    const canvasWriteBaseline = canvasWrites.length;

    await composer.fill(requestText);
    await composer.press("Enter");
    const run = await waitForTerminalRun();
    await Promise.allSettled(workflowRunExchangeTasks);
    await page.waitForTimeout(1_000);

    const runsAfter = await readRuns();
    const canvasAfter = await readCanvas();
    const tasks = await readTasks();
    await page.screenshot({ path: screenshotPath, fullPage: true });
    const finalArtifact = run.artifacts?.final_film?.final_compose_artifact ?? {};
    const finalPath = String(finalArtifact.path ?? "");
    let finalSha256 = "";
    if (finalPath && fs.existsSync(finalPath)) {
      finalSha256 = sha256File(finalPath);
    }

    evidence = {
      ...evidence,
      completed_at: new Date().toISOString(),
      browser_requests: browserRequests,
      websocket_frames: websocketFrames,
      websocket_lifecycle: websocketLifecycle,
      workflow_run_exchanges: workflowRunExchanges,
      canvas_writes: canvasWrites,
      canvas_write_baseline: canvasWriteBaseline,
      page_errors: pageErrors,
      console_errors: consoleErrors,
      console_messages: consoleMessages,
      canvas_after: canvasAfter,
      runs_after: runsAfter,
      run,
      tasks,
      final_artifact_path: finalPath,
      final_artifact_observed_sha256: finalSha256,
      screenshot_path: screenshotPath,
    };
    writeEvidence(evidence);
    if (run.status !== "completed") {
      throw new Error(`production run stopped: ${JSON.stringify({
        id: run.id,
        status: run.status,
        error_code: run.error_code,
        next_action: run.next_action,
      })}`);
    }
  } catch (error) {
    evidence = {
      ...evidence,
      completed_at: new Date().toISOString(),
      browser_requests: browserRequests,
      websocket_frames: websocketFrames,
      websocket_lifecycle: websocketLifecycle,
      workflow_run_exchanges: workflowRunExchanges,
      canvas_writes: canvasWrites,
      page_errors: pageErrors,
      console_errors: consoleErrors,
      console_messages: consoleMessages,
      screenshot_path: screenshotPath,
      failure: {
        type: error?.name ?? "Error",
        message: String(error?.message ?? error),
        stack: String(error?.stack ?? "").slice(0, 8_000),
      },
    };
    try {
      evidence.canvas_after = await readCanvas();
      evidence.runs_after = await readRuns();
      evidence.tasks = await readTasks();
      const latest = evidence.runs_after.at(-1);
      if (latest) {
        evidence.run = latest;
        const finalArtifact =
          latest.artifacts?.final_film?.final_compose_artifact ?? {};
        const finalPath = String(finalArtifact.path ?? "");
        evidence.final_artifact_path = finalPath;
        if (finalPath && fs.existsSync(finalPath)) {
          evidence.final_artifact_observed_sha256 = sha256File(finalPath);
        }
      }
    } catch (readError) {
      evidence.readback_failure = String(readError?.message ?? readError);
    }
    try {
      await page.screenshot({ path: screenshotPath, fullPage: true });
    } catch (screenshotError) {
      evidence.screenshot_failure = String(
        screenshotError?.message ?? screenshotError,
      );
    }
    writeEvidence(evidence);
    throw error;
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error?.stack ?? String(error));
  process.exitCode = 2;
});
