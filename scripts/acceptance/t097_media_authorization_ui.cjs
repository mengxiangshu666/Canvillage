const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require("playwright");

const projectId = process.env.T097_PROJECT_ID;
const canvasId = process.env.T097_CANVAS_ID;
const runId = process.env.T097_RUN_ID;
const uiBase = process.env.T097_UI_BASE;
const apiBase = process.env.T097_API_BASE;
const statsUrl = process.env.T097_STATS_URL;
const evidencePath = path.resolve(process.env.T097_EVIDENCE_PATH);
const screenshotPath = path.resolve(process.env.T097_SCREENSHOT_PATH);
const authorizationStage =
  process.env.T097_AUTHORIZATION_STAGE || "storyboard_images";
const authorizationButtonName = authorizationStage === "shot_videos"
  ? "授权并恢复出视频"
  : authorizationStage === "final_film"
    ? "授权并继续合成"
    : "授权并恢复出图";
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

async function readRun() {
  return await readJson(
    `${apiBase}/projects/${projectId}/workflow-runs/${runId}`,
  );
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

function compactRun(run) {
  const storyboard = run.artifacts?.storyboard_images ?? {};
  const shotVideos = run.artifacts?.shot_videos ?? {};
  const finalFilm = run.artifacts?.final_film ?? {};
  return {
    status: run.status,
    revision: run.revision,
    event_seq: run.event_seq,
    next_action: run.next_action,
    error_code: run.error_code,
    step_attempt: run.step_states?.storyboard_images?.attempt ?? null,
    recovery_action: storyboard.recovery?.action ?? null,
    media_authorization_persisted: Boolean(storyboard.media_authorization),
    media_submission_started: storyboard.media_submission_started === true,
    completed_count: storyboard.completed_count ?? null,
    failed_count: storyboard.failed_count ?? null,
    artifact_status: storyboard.status ?? null,
    item_count: Object.keys(storyboard.item_states ?? {}).length,
    storyboard_images: {
      status: storyboard.status ?? null,
      completed_count: storyboard.completed_count ?? null,
      failed_count: storyboard.failed_count ?? null,
      media_authorization_persisted: Boolean(storyboard.media_authorization),
    },
    shot_videos: {
      status: shotVideos.status ?? null,
      completed_count: shotVideos.completed_count ?? null,
      failed_count: shotVideos.failed_count ?? null,
      error_code: shotVideos.error_code ?? null,
      recovery_action: shotVideos.recovery?.action ?? null,
      media_authorization_persisted: Boolean(shotVideos.media_authorization),
      media_submission_started: shotVideos.media_submission_started === true,
      item_count: Object.keys(shotVideos.item_states ?? {}).length,
      result_signature: shotVideos.result_signature ?? null,
      videos: shotVideos.videos ?? [],
    },
    final_film: {
      status: finalFilm.status ?? null,
      error_code: finalFilm.error_code ?? null,
      recovery_action: finalFilm.recovery?.action ?? null,
      compose_authorization_persisted: Boolean(finalFilm.compose_authorization),
      media_submission_started: finalFilm.media_submission_started === true,
    },
  };
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
    return {
      frame,
      payload: JSON.parse(match[1]),
    };
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

async function ensureAgentOpen(page, authorizationButton) {
  if (await authorizationButton.isVisible().catch(() => false)) return;
  const openAgent = page.locator('[aria-label="打开搭子创作台"]');
  if (
    (await openAgent.count()) > 0
    && (await openAgent.isVisible().catch(() => false))
  ) {
    await openAgent.click();
    await page.waitForTimeout(800);
  }
}

async function waitForAgentConnected(page) {
  await page.getByText("在线", { exact: true })
    .last()
    .waitFor({ state: "visible", timeout: 60_000 });
}

async function waitForRunStatus(predicate, timeoutMs = 60_000) {
  const deadline = Date.now() + timeoutMs;
  let current = null;
  while (Date.now() < deadline) {
    current = await readRun();
    if (predicate(current)) return current;
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(`workflow run timeout: ${JSON.stringify(compactRun(current))}`);
}

async function main() {
  const runBefore = await readRun();
  const runsBefore = await readRuns();
  const canvasBefore = await readCanvas();
  const statsBefore = await readStats();
  assert(runBefore.status === "failed", "seed run must be failed");
  if (authorizationStage === "storyboard_images") {
    assert(
      runBefore.artifacts?.storyboard_images?.recovery?.action
        === "request_media_authorization",
      "seed run must be at the storyboard paid media authorization gate",
    );
    assert(
      !runBefore.artifacts?.storyboard_images?.media_authorization,
      "seed run must not contain a storyboard paid media grant",
    );
  } else if (authorizationStage === "shot_videos") {
    const storyboardBefore = runBefore.artifacts?.storyboard_images ?? {};
    const shotVideosBefore = runBefore.artifacts?.shot_videos ?? {};
    assert(
      storyboardBefore.status === "completed"
        && storyboardBefore.completed_count === 1,
      "shot-video seed must contain one completed storyboard image",
    );
    assert(
      shotVideosBefore.status === "failed"
        && shotVideosBefore.recovery?.action === "request_media_authorization",
      "seed run must be at the shot-video paid media authorization gate",
    );
    assert(
      !shotVideosBefore.media_authorization,
      "seed run must not contain a shot-video paid media grant",
    );
  } else if (authorizationStage === "final_film") {
    const storyboardBefore = runBefore.artifacts?.storyboard_images ?? {};
    const shotVideosBefore = runBefore.artifacts?.shot_videos ?? {};
    const finalFilmBefore = runBefore.artifacts?.final_film ?? {};
    const authorizationRequest =
      finalFilmBefore.recovery?.authorization_request ?? {};
    assert(
      storyboardBefore.status === "completed"
        && storyboardBefore.completed_count === 1,
      "final-compose seed must contain one completed storyboard image",
    );
    assert(
      shotVideosBefore.status === "completed"
        && shotVideosBefore.completed_count === 1,
      "final-compose seed must contain one completed shot video",
    );
    assert(
      finalFilmBefore.status === "failed"
        && finalFilmBefore.recovery?.action === "request_compose_authorization",
      "seed run must be at the final-compose authorization gate",
    );
    assert(
      authorizationRequest.schema === "workflow_compose_authorization_request.v1"
        && authorizationRequest.run_id === runId
        && authorizationRequest.step_id === "final_film"
        && authorizationRequest.requires_user_action === true
        && sha256Pattern.test(
          String(authorizationRequest.source_result_signature ?? ""),
        ),
      "final-compose seed is missing a valid authorization request",
    );
    assert(
      !finalFilmBefore.compose_authorization,
      "seed run must not contain a final-compose ticket",
    );
    assert(
      finalFilmBefore.media_submission_started !== true,
      "seed run started final composition before its own authorization",
    );
  } else {
    throw new Error(`unknown authorization stage: ${authorizationStage}`);
  }
  assert(statsBefore.provider_calls === 0, "seed provider calls must be zero");

  const browser = await chromium.launch({
    headless: true,
    executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe",
  });
  const page = await browser.newPage({ viewport: { width: 1440, height: 960 } });
  const browserRequests = [];
  const browserErrors = [];
  const canvasWrites = [];
  const composeIssueRequests = [];
  const composeIssueResponses = [];
  activePage = page;

  page.on("websocket", (websocket) => {
    websocket.on("framesent", (frame) => {
      const parsed = parseCanvasRequest(frame.payload);
      if (parsed) browserRequests.push(parsed);
    });
  });
  page.on("pageerror", (error) => browserErrors.push(String(error)));
  page.on("request", (request) => {
    if (
      request.method() === "POST"
      && request.url().endsWith(`/workflow-runs/${runId}/compose-authorizations`)
    ) {
      composeIssueRequests.push({
        method: request.method(),
        url: request.url(),
      });
    }
    if (
      ["PUT", "POST", "PATCH", "DELETE"].includes(request.method())
      && request.url().includes(`/freezone/canvases/${canvasId}`)
    ) {
      canvasWrites.push(`${request.method()} ${request.url()}`);
    }
  });
  page.on("response", async (response) => {
    const request = response.request();
    if (
      request.method() !== "POST"
      || !request.url().endsWith(`/workflow-runs/${runId}/compose-authorizations`)
    ) {
      return;
    }
    let body = null;
    try {
      body = await response.json();
    } catch {
      body = null;
    }
    composeIssueResponses.push({
      status: response.status(),
      body,
    });
  });

  await page.goto(
    `${uiBase}/projects/${projectId}/freezone?canvas=${canvasId}`,
    { waitUntil: "domcontentloaded", timeout: 60_000 },
  );
  await page.waitForTimeout(2_000);
  await dismissReleaseNotes(page);
  const authorizationButton = page.getByRole("button", {
    name: authorizationButtonName,
  });
  await ensureAgentOpen(page, authorizationButton);
  await authorizationButton.waitFor({ state: "visible", timeout: 60_000 });
  await waitForAgentConnected(page);
  failureContext = {
    phase: "authorization-ready",
    browserRequests,
    browserErrors,
    canvasWrites,
  };

  await authorizationButton.dblclick({ delay: 30 });
  for (let index = 0; index < 40 && browserRequests.length === 0; index += 1) {
    await page.waitForTimeout(50);
  }
  assert(browserRequests.length > 0, "authorization click did not send a chat.message");
  if (authorizationStage === "storyboard_images") {
    await waitForRunStatus(
      (run) => {
        const storyboard = run.artifacts?.storyboard_images ?? {};
        return storyboard.status === "completed"
          && storyboard.completed_count === 1;
      },
    );
  } else if (authorizationStage === "shot_videos") {
    await waitForRunStatus(
      (run) => {
        const shotVideos = run.artifacts?.shot_videos ?? {};
        return shotVideos.status === "completed"
          && shotVideos.completed_count === 1
          && (shotVideos.videos ?? []).length === 1;
      },
    );
  } else {
    await waitForRunStatus(
      (run) => {
        const finalFilm = run.artifacts?.final_film ?? {};
        return run.status === "completed"
          && finalFilm.status === "completed"
          && Boolean(finalFilm.final_compose_artifact?.sha256);
      },
      120_000,
    );
  }
  await page.waitForTimeout(1_000);
  const runAfter = await readRun();
  const runsAfter = await readRuns();

  const stats = await readStats();
  const canvasAfter = await readCanvas();
  const writesAfter = canvasWrites.slice();
  const browserRequestPayloads = browserRequests.map((item) => item.payload);
  const clientAuthorizations = browserRequestPayloads.map(
    (payload) => payload.task_authorization ?? {},
  );
  const serverAuthorizations = (stats.requests ?? []).map(
    (request) => request.task_authorization ?? {},
  );
  const storyboardAfter = runAfter.artifacts?.storyboard_images ?? {};

  assert(browserRequests.length === 1, `expected one browser request, got ${browserRequests.length}`);
  assert(stats.structured_rounds === 1, "Agent stub must receive exactly one structured turn");
  assert(clientAuthorizations[0].scope === "current_turn", "browser scope mismatch");
  assert(clientAuthorizations[0].run_mode === "auto", "browser run_mode mismatch");
  assert(!clientAuthorizations[0].grant_id, "browser must not invent a grant id");
  assert(serverAuthorizations.length === 1, "expected one server-bound authorization");
  if (authorizationStage === "final_film") {
    assert(
      String(clientAuthorizations[0].compose_authorization_id ?? "")
        .startsWith("wca_"),
      "browser must carry one server-issued compose authorization id",
    );
    assert(
      serverAuthorizations[0].compose_authorization_id
        === clientAuthorizations[0].compose_authorization_id,
      "Agent stub did not receive the browser compose authorization id",
    );
  } else {
    assert(clientAuthorizations[0].allow_paid_media === true, "browser allow_paid_media mismatch");
    assert(clientAuthorizations[0].max_paid_starts === 1, "browser max_paid_starts mismatch");
    assert(
      String(serverAuthorizations[0].grant_id ?? "").startsWith("pmg_"),
      "server-bound grant id must be a pmg_ value",
    );
    assert(stats.provider_calls === 1, `expected one provider call, got ${stats.provider_calls}`);
  }
  assert(runAfter.id === runId, "workflow run id changed");
  const shotVideosAfter = runAfter.artifacts?.shot_videos ?? {};
  const finalFilmAfter = runAfter.artifacts?.final_film ?? {};
  if (authorizationStage === "storyboard_images") {
    assert(
      (stats.task_submissions ?? []).filter(
        (item) => item.task_type === "freezone_gen",
      ).length === 1,
      "expected exactly one freezone_gen task submission",
    );
    assert(
      runAfter.status === "failed",
      "run should stop at the next paid step after the one-step grant",
    );
    assert(
      runAfter.error_code === "workflow_shot_video_paid_media_not_authorized",
      "the next paid step was not stopped by its own authorization gate",
    );
    assert(
      runAfter.next_action
        === "recover:request_media_authorization:shot_videos",
      "the next paid step did not expose its own recovery authorization",
    );
    assert(storyboardAfter.status === "completed", "storyboard step did not complete");
    assert(storyboardAfter.completed_count === 1, "storyboard did not complete exactly one item");
    assert(!storyboardAfter.media_authorization, "media authorization marker was not cleared");
    assert(shotVideosAfter.status === "failed", "shot_videos should stop at its own gate");
    assert(
      shotVideosAfter.recovery?.action === "request_media_authorization",
      "shot_videos did not return a paid-media authorization request",
    );
    assert(
      shotVideosAfter.media_submission_started !== true,
      "shot_videos submitted media without a video-scoped grant",
    );
    assert(
      !shotVideosAfter.media_authorization,
      "storyboard grant leaked into the shot_videos step",
    );
  } else if (authorizationStage === "shot_videos") {
    assert(
      stats.image_provider_calls === 0,
      "shot-video authorization unexpectedly called the image provider",
    );
    assert(
      stats.video_provider_calls === 1,
      `expected one video provider call, got ${stats.video_provider_calls}`,
    );
    assert(
      (stats.task_submissions ?? []).filter(
        (item) => item.task_type === "freezone_video_gen",
      ).length === 1,
      "expected exactly one freezone_video_gen task submission",
    );
    assert(
      (stats.task_submissions ?? []).filter(
        (item) => item.task_type === "compose_episode",
      ).length === 0,
      "final composition must not start before its own authorization",
    );
    assert(storyboardAfter.status === "completed", "storyboard step is not complete");
    assert(storyboardAfter.completed_count === 1, "storyboard count changed");
    assert(shotVideosAfter.status === "completed", "shot_videos did not complete");
    assert(
      shotVideosAfter.completed_count === 1
        && (shotVideosAfter.videos ?? []).length === 1,
      "shot_videos did not produce exactly one video",
    );
    assert(
      !shotVideosAfter.media_authorization,
      "shot-video media authorization marker was not cleared",
    );
    const video = shotVideosAfter.videos?.[0] ?? {};
    assert(
      video.output_path && video.sha256 && video.width > 0
        && video.height > 0 && video.duration_seconds > 0,
      "shot video artifact is missing verifiable MP4 metadata",
    );
    assert(
      runAfter.status === "failed",
      "run should stop at the final-film authorization gate",
    );
    assert(
      runAfter.error_code === "workflow_final_film_not_authorized",
      "final film was not stopped by its own authorization gate",
    );
    assert(
      runAfter.next_action
        === "recover:request_compose_authorization:final_film",
      "final film did not expose its own compose authorization request",
    );
    assert(
      finalFilmAfter.recovery?.action === "request_compose_authorization",
      "final film did not expose a compose authorization recovery action",
    );
    assert(
      !finalFilmAfter.compose_authorization,
      "final film received an unexpected compose authorization",
    );
    assert(
      finalFilmAfter.media_submission_started !== true,
      "final composition started without its own authorization",
    );
  } else {
    const finalArtifact = finalFilmAfter.final_compose_artifact ?? {};
    const persistedComposeAuthorization =
      finalFilmAfter.compose_authorization ?? {};
    assert(
      stats.image_provider_calls === 0 && stats.video_provider_calls === 0,
      "final-compose authorization unexpectedly called a media provider",
    );
    assert(
      (stats.task_submissions ?? []).filter(
        (item) => item.task_type === "compose_episode",
      ).length === 1,
      "expected exactly one compose_episode task submission",
    );
    assert(
      (stats.task_submissions ?? []).filter(
        (item) => item.task_type === "freezone_gen"
          || item.task_type === "freezone_video_gen",
      ).length === 0,
      "final-compose recovery must not submit media generation tasks",
    );
    assert(storyboardAfter.status === "completed", "storyboard step is not complete");
    assert(shotVideosAfter.status === "completed", "shot_videos step is not complete");
    assert(
      shotVideosAfter.completed_count === 1
        && (shotVideosAfter.videos ?? []).length === 1,
      "shot_videos changed during final-compose recovery",
    );
    assert(
      runAfter.status === "completed",
      "run did not reach completed after final composition",
    );
    assert(
      finalFilmAfter.status === "completed",
      "final_film did not complete after consuming its ticket",
    );
    assert(
      finalArtifact.schema === "workflow_final_compose_artifact.v1"
        && finalArtifact.task_id
        && finalArtifact.path
        && sha256Pattern.test(String(finalArtifact.sha256 ?? ""))
        && finalArtifact.width > 0
        && finalArtifact.height > 0
        && finalArtifact.duration_seconds > 0,
      "final-compose artifact is missing verifiable MP4 metadata",
    );
    assert(
      persistedComposeAuthorization.authorization_id
        === clientAuthorizations[0].compose_authorization_id,
      "persisted compose authorization does not match the browser ticket",
    );
    assert(
      persistedComposeAuthorization.source_result_signature
        === shotVideosAfter.result_signature,
      "persisted compose authorization is not bound to the completed videos",
    );
    assert(
      composeIssueRequests.length === 1,
      `expected one compose authorization POST, got ${composeIssueRequests.length}`,
    );
    assert(
      composeIssueResponses.length === 1
        && composeIssueResponses[0].status === 200,
      `compose authorization issue failed: ${JSON.stringify(composeIssueResponses)}`,
    );
    const issuedTicket = (
      composeIssueResponses[0].body?.data
      ?? composeIssueResponses[0].body
      ?? {}
    );
    assert(
      issuedTicket.id === clientAuthorizations[0].compose_authorization_id
        && issuedTicket.run_id === runId
        && issuedTicket.step_id === "final_film"
        && issuedTicket.source_result_signature === shotVideosAfter.result_signature,
      "compose authorization response does not match the current run scope",
    );
  }
  assert(
    runsAfter.length === runsBefore.length,
    "media recovery created a fresh workflow run",
  );
  assert(canvasAfter.revision === canvasBefore.revision, "media authorization wrote the canvas");
  assert(writesAfter.length === 0, `unexpected canvas writes: ${JSON.stringify(writesAfter)}`);
  assert(browserErrors.length === 0, `browser errors: ${JSON.stringify(browserErrors)}`);

  await page.screenshot({ path: screenshotPath, fullPage: true });
  const evidenceSchema = authorizationStage === "storyboard_images"
    ? "t097_browser_media_authorization_recovery.v1"
    : authorizationStage === "shot_videos"
      ? "t098_browser_shot_video_authorization_recovery.v1"
      : "t099_browser_final_compose_authorization_recovery.v1";
  const evidence = {
    schema: evidenceSchema,
    authorization_stage: authorizationStage,
    ui_base_url: uiBase,
    api_base_url: apiBase,
    project_id: projectId,
    canvas_id: canvasId,
    run_id: runId,
    run_before: compactRun(runBefore),
    run_after: compactRun(runAfter),
    workflow_run_count_before: runsBefore.length,
    workflow_run_count_after: runsAfter.length,
    canvas_before_revision: canvasBefore.revision,
    canvas_after_revision: canvasAfter.revision,
    browser_structured_requests: browserRequestPayloads,
    server_bound_authorizations: serverAuthorizations,
    compose_authorization_issue_requests: composeIssueRequests,
    compose_authorization_issue_responses: composeIssueResponses,
    browser_request_count: browserRequests.length,
    server_structured_round_count: stats.structured_rounds,
    provider_calls: stats.provider_calls,
    provider_paths: stats.provider_paths,
    task_submissions: stats.task_submissions,
    agent_results: stats.agent_results,
    canvas_writes: writesAfter,
    browser_errors: browserErrors,
    screenshot_path: screenshotPath,
    agent_stub_boundary:
      "Browser/WebSocket/API/grant/plugin/WorkflowRuntime/task/provider are real; LLM decision is replaced by the deterministic acceptance stub.",
  };
  fs.mkdirSync(path.dirname(evidencePath), { recursive: true });
  fs.writeFileSync(evidencePath, `${JSON.stringify(evidence, null, 2)}\n`);
  console.log(JSON.stringify(evidence, null, 2));
  await browser.close();
}

main().catch(async (error) => {
  console.error(error);
  let pageText = "";
  if (activePage) {
    pageText = await activePage.locator("body").innerText().catch(() => "");
  }
  const stats = await readStats().catch(() => null);
  const failurePath = path.resolve(
    evidencePath.replace(/\.json$/i, "-failure.json"),
  );
  const failureSchema = authorizationStage === "storyboard_images"
    ? "t097_browser_media_authorization_failure.v1"
    : authorizationStage === "shot_videos"
      ? "t098_browser_shot_video_authorization_failure.v1"
      : "t099_browser_final_compose_authorization_failure.v1";
  fs.mkdirSync(path.dirname(failurePath), { recursive: true });
  fs.writeFileSync(
    failurePath,
    `${JSON.stringify(
      {
        schema: failureSchema,
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
