const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require("playwright");

const projectId = process.env.T100_PROJECT_ID;
const canvasId = process.env.T100_CANVAS_ID;
const scriptNodeId = process.env.T100_SCRIPT_NODE_ID;
const uiBase = process.env.T100_UI_BASE;
const apiBase = process.env.T100_API_BASE;
const statsUrl = process.env.T100_STATS_URL;
const evidencePath = path.resolve(process.env.T100_EVIDENCE_PATH);
const screenshotPath = path.resolve(process.env.T100_SCREENSHOT_PATH);
const requestText = "从当前脚本节点继续到最终成片";
const sha256Pattern = /^[0-9a-f]{64}$/;
let failureContext = { phase: "bootstrap" };
let activePage = null;

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

async function readRuns() {
  return await readJson(
    `${apiBase}/projects/${projectId}/workflow-runs?canvas_id=${encodeURIComponent(canvasId)}`,
  );
}

async function readCanvas() {
  return await readJson(
    `${apiBase}/projects/${projectId}/freezone/canvases/${canvasId}`,
  );
}

async function readStats() {
  const response = await fetch(statsUrl);
  if (!response.ok) {
    throw new Error(`GET ${statsUrl} -> ${response.status}`);
  }
  return await response.json();
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

async function dismissReleaseNotes(page) {
  const known = page.getByRole("button", { name: "我知道了" });
  try {
    await known.waitFor({ state: "visible", timeout: 8_000 });
    await known.click();
    await known.waitFor({ state: "hidden", timeout: 8_000 });
  } catch {
    // Fresh browser profiles only.
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

async function waitForSingleRun(predicate, timeoutMs = 90_000) {
  const deadline = Date.now() + timeoutMs;
  let runs = [];
  while (Date.now() < deadline) {
    runs = await readRuns();
    if (runs.length === 1 && predicate(runs[0])) return runs[0];
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(`workflow run timeout: ${JSON.stringify(runs)}`);
}

function compactRun(run) {
  const script = run.artifacts?.script_contract ?? {};
  const storyboard = run.artifacts?.storyboard_images ?? {};
  return {
    id: run.id,
    workflow_id: run.workflow_id,
    run_mode: run.run_mode,
    status: run.status,
    revision: run.revision,
    event_seq: run.event_seq,
    error_code: run.error_code,
    next_action: run.next_action,
    inputs: {
      request: run.inputs?.request,
      target_strategy: run.inputs?.target_strategy,
      target_node_ids: run.inputs?.target_node_ids,
    },
    script_contract: {
      status: script.status,
      source: script.source,
      script_node_id: script.script_node_id,
      canvas_revision: script.canvas_revision,
      rows_fingerprint: script.rows_fingerprint,
      result_signature: script.result_signature,
    },
    storyboard_images: {
      status: storyboard.status,
      recovery_action: storyboard.recovery?.action,
      media_submission_started: storyboard.media_submission_started === true,
      media_authorization_persisted: Boolean(storyboard.media_authorization),
    },
  };
}

async function main() {
  let canvasBefore = await readCanvas();
  const statsBefore = await readStats();
  assert((await readRuns()).length === 0, "T-100 isolated run store must start empty");
  assert(statsBefore.provider_calls === 0, "seed provider calls must be zero");

  const browser = await chromium.launch({
    headless: true,
    executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe",
  });
  const page = await browser.newPage({ viewport: { width: 1440, height: 960 } });
  await page.addInitScript(() => {
    window.localStorage.setItem("village_canvas_agent_run_mode", "draft");
  });
  const browserRequests = [];
  const canvasWrites = [];
  const browserErrors = [];
  activePage = page;

  page.on("websocket", (websocket) => {
    websocket.on("framesent", (frame) => {
      const payload = parseCanvasRequest(frame.payload);
      if (payload) browserRequests.push(payload);
    });
  });
  page.on("pageerror", (error) => browserErrors.push(String(error)));
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
  await page.getByText("在线", { exact: true })
    .last()
    .waitFor({ state: "visible", timeout: 60_000 });

  // Selecting a node persists it into the canvas document, so the click above
  // bumps the revision. That write is user interaction, not the workflow, and
  // the Agent is handed whatever revision the server holds when the request
  // arrives. Re-anchor the baseline there so the remaining assertions still
  // prove the real invariants: the run binds the authoritative revision and the
  // workflow itself writes nothing.
  for (let attempt = 0; attempt < 6; attempt += 1) {
    await page.waitForTimeout(400);
    const settled = await readCanvas();
    if (settled.revision === canvasBefore.revision) break;
    canvasBefore = settled;
  }
  const selectCanvasWrites = canvasWrites.slice();
  const canvasWriteBaseline = canvasWrites.length;

  failureContext = {
    phase: "script-node-selected",
    browserRequests,
    browserErrors,
    canvasWrites,
    canvas_revision_at_dispatch: canvasBefore.revision,
  };

  await composer.fill(requestText);
  await composer.press("Enter");
  const runAfter = await waitForSingleRun(
    (run) => run.workflow_id === "freezone-final-film"
      && run.status === "failed"
      && run.next_action
        === "recover:request_media_authorization:storyboard_images",
  );
  await page.waitForTimeout(1_000);
  const runsAfter = await readRuns();
  const canvasAfter = await readCanvas();
  const stats = await readStats();
  const script = runAfter.artifacts?.script_contract ?? {};
  const storyboard = runAfter.artifacts?.storyboard_images ?? {};
  const scriptNodeBefore = canvasBefore.nodes.find((node) => node.id === scriptNodeId);
  const expectedFingerprint = scriptNodeBefore?.data?.scriptContractReport?.rows_fingerprint;

  assert(browserRequests.length === 1, `expected one browser request, got ${browserRequests.length}`);
  assert(stats.structured_rounds === 1, "Agent stub must receive exactly one structured turn");
  assert(
    browserRequests[0].request === requestText,
    "browser did not send the natural-language script-to-film request",
  );
  assert(browserRequests[0].run_mode === "draft", "browser run mode must be draft");
  assert(
    browserRequests[0].task_authorization?.allow_paid_media !== true,
    "draft request must not carry paid-media authorization",
  );
  assert(runAfter.workflow_id === "freezone-final-film", "wrong workflow selected");
  assert(runAfter.run_mode === "draft", "workflow run mode changed");
  assert(
    runAfter.inputs?.target_strategy === "reuse_existing"
      && Array.isArray(runAfter.inputs?.target_node_ids)
      && runAfter.inputs.target_node_ids.includes(scriptNodeId),
    "workflow run did not bind the current script node as a reused target",
  );
  assert(script.status === "completed", "script contract did not complete");
  assert(script.source === "canvas_script_node", "script did not come from the canvas node");
  assert(script.script_node_id === scriptNodeId, "script node identity changed");
  assert(script.canvas_revision === canvasBefore.revision, "script bound the wrong canvas revision");
  assert(script.rows_fingerprint === expectedFingerprint, "script rows fingerprint mismatch");
  assert(sha256Pattern.test(String(script.result_signature ?? "")), "missing stable script signature");
  assert(
    runAfter.error_code === "workflow_storyboard_paid_media_not_authorized",
    "run did not stop at the first paid-media gate",
  );
  assert(
    storyboard.recovery?.action === "request_media_authorization",
    "storyboard did not expose its media authorization request",
  );
  assert(storyboard.media_submission_started !== true, "storyboard submitted media before authorization");
  assert(!storyboard.media_authorization, "storyboard contains an unexpected media grant");
  assert(
    (stats.task_submissions ?? []).filter(
      (item) => item.task_type === "freezone_story_script",
    ).length === 0,
    "canvas script reuse submitted a duplicate script task",
  );
  assert(
    (stats.task_submissions ?? []).length === 0,
    `expected zero task submissions, got ${JSON.stringify(stats.task_submissions)}`,
  );
  assert(stats.provider_calls === 0, "script reuse called a provider");
  assert(runsAfter.length === 1, "script reuse created a parallel workflow run");
  assert(canvasAfter.revision === canvasBefore.revision, "script reuse wrote the canvas");
  assert(
    canvasWrites.length === canvasWriteBaseline,
    `script reuse wrote the canvas: ${JSON.stringify(canvasWrites.slice(canvasWriteBaseline))}`,
  );
  assert(browserErrors.length === 0, `browser errors: ${JSON.stringify(browserErrors)}`);

  await page.screenshot({ path: screenshotPath, fullPage: true });
  const evidence = {
    schema: "t100_canvas_script_reuse_ui.v1",
    ui_base_url: uiBase,
    api_base_url: apiBase,
    project_id: projectId,
    canvas_id: canvasId,
    script_node_id: scriptNodeId,
    request_text: requestText,
    run_before_count: 0,
    run_after_count: runsAfter.length,
    canvas_before_revision: canvasBefore.revision,
    canvas_after_revision: canvasAfter.revision,
    browser_structured_requests: browserRequests,
    run_after: compactRun(runAfter),
    structured_rounds: stats.structured_rounds,
    agent_results: stats.agent_results,
    provider_calls: stats.provider_calls,
    provider_paths: stats.provider_paths,
    task_submissions: stats.task_submissions,
    canvas_writes: canvasWrites,
    select_canvas_writes: selectCanvasWrites,
    workflow_canvas_writes: canvasWrites.slice(canvasWriteBaseline),
    browser_errors: browserErrors,
    screenshot_path: screenshotPath,
    agent_stub_boundary:
      "Browser/WebSocket/API/plugin/ActionRouter/WorkflowRuntime/task backend are real; only the LLM decision is replaced by the deterministic acceptance stub.",
  };
  fs.mkdirSync(path.dirname(evidencePath), { recursive: true });
  fs.writeFileSync(evidencePath, `${JSON.stringify(evidence, null, 2)}\n`);
  console.log(JSON.stringify(evidence, null, 2));
  await browser.close();
}

main().catch(async (error) => {
  console.error(error);
  const pageText = activePage
    ? await activePage.locator("body").innerText().catch(() => "")
    : "";
  const stats = await readStats().catch(() => null);
  const failurePath = path.resolve(evidencePath.replace(/\.json$/i, "-failure.json"));
  fs.mkdirSync(path.dirname(failurePath), { recursive: true });
  fs.writeFileSync(
    failurePath,
    `${JSON.stringify(
      {
        schema: "t100_canvas_script_reuse_ui_failure.v1",
        error: String(error),
        ...failureContext,
        stats,
        page_text: pageText,
      },
      null,
      2,
    )}\n`,
  );
  process.exit(1);
});
