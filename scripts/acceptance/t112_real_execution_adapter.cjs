const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require("playwright");

const projectId = process.env.T112_PROJECT_ID;
const canvasId = process.env.T112_CANVAS_ID;
const scriptNodeId = process.env.T112_SCRIPT_NODE_ID;
const uiBase = process.env.T112_UI_BASE;
const apiBase = process.env.T112_API_BASE;
const statsUrl = process.env.T112_STATS_URL;
const evidencePath = path.resolve(process.env.T112_EVIDENCE_PATH);
const screenshotPath = path.resolve(process.env.T112_SCREENSHOT_PATH);
const requestText = "从当前脚本节点继续到最终成片";
const fullChain = process.env.T112_FULL_CHAIN === "1";
const recoveryChain = process.env.T112_RECOVERY_CHAIN === "1";
const maxPaidStarts = Number(process.env.T112_MAX_PAID_STARTS ?? 4);
const expectedShotCount = Number(process.env.T112_SHOT_COUNT ?? 1);
const videoDurationSeconds = Number(
  process.env.T112_VIDEO_DURATION_SECONDS ?? 5,
);
const expectedDialogueText = String(process.env.T112_DIALOGUE_TEXT ?? "").trim();
const completedWaitMs = Number(
  process.env.T112_COMPLETED_WAIT_MS ?? 300_000,
);
const sha256Pattern = /^[0-9a-f]{64}$/;
const deliveryQcRequiredChecks = [
  "container_allowed",
  "video_stream_present",
  "audio_stream_present",
  "dimensions",
  "frame_rate",
  "duration",
  "black_frames",
  "freeze_frames",
  "av_sync",
  "audio_activity",
  "loudness",
  "true_peak",
  "subtitle_stream",
  "color_space",
  "bitrate",
  "file_readback",
  "sha256",
];
let failureContext = { phase: "bootstrap" };
let activePage = null;
const workflowRunExchanges = [];
const workflowRunExchangeTasks = [];

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function isImagePath(value) {
  return /\/images?(?:\/|$)/.test(String(value ?? ""));
}

function isVideoPath(value) {
  return /\/videos?(?:\/|$)|video_generation/.test(String(value ?? ""));
}

function isAudioPath(value) {
  return /\/audio(?:\/|$)/.test(String(value ?? ""));
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

async function readTasks() {
  return await readJson(`${apiBase}/projects/${projectId}/tasks`);
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

async function waitForGate(timeoutMs = 150_000) {
  const deadline = Date.now() + timeoutMs;
  let runs = [];
  while (Date.now() < deadline) {
    runs = await readRuns();
    if (
      runs.length === 1
      && runs[0].workflow_id === "freezone-final-film"
      && runs[0].status === "failed"
      && runs[0].next_action
        === "recover:request_media_authorization:storyboard_images"
    ) {
      return runs[0];
    }
    await new Promise((resolve) => setTimeout(resolve, 300));
  }
  throw new Error(`paid-media authorization gate timeout: ${JSON.stringify(runs)}`);
}

async function waitForFailedVideoRun(timeoutMs = 180_000) {
  const deadline = Date.now() + timeoutMs;
  let runs = [];
  while (Date.now() < deadline) {
    runs = await readRuns();
    if (
      runs.length === 1
      && runs[0].workflow_id === "freezone-final-film"
      && runs[0].status === "failed"
      && runs[0].error_code === "workflow_shot_video_failed"
      && runs[0].next_action === "recover:retry_failed_items:shot_videos"
    ) {
      return runs[0];
    }
    await new Promise((resolve) => setTimeout(resolve, 300));
  }
  throw new Error(`failed-video recovery gate timeout: ${JSON.stringify(runs)}`);
}

async function waitForCompletedRun(
  timeoutMs = 300_000,
  expectedRunId = "",
  afterRevision = -1,
) {
  const deadline = Date.now() + timeoutMs;
  let runs = [];
  while (Date.now() < deadline) {
    runs = await readRuns();
    if (
      runs.length === 1
      && runs[0].status === "completed"
      && (!expectedRunId || runs[0].id === expectedRunId)
    ) {
      return runs[0];
    }
    if (runs.length > 1 || (expectedRunId && runs[0]?.id !== expectedRunId)) {
      throw new Error(`workflow recovery started a different run: ${JSON.stringify(runs)}`);
    }
    if (
      runs.length === 1
      && ["failed", "cancelled"].includes(runs[0].status)
      && (
        !expectedRunId
        || !Number.isFinite(Number(runs[0].revision))
        || Number(runs[0].revision) > Number(afterRevision)
      )
    ) {
      throw new Error(`workflow run stopped before delivery: ${JSON.stringify(runs[0])}`);
    }
    await new Promise((resolve) => setTimeout(resolve, 350));
  }
  throw new Error(`completed workflow run timeout: ${JSON.stringify(runs)}`);
}

function sha256File(filePath) {
  const digest = crypto.createHash("sha256");
  digest.update(fs.readFileSync(filePath));
  return digest.digest("hex");
}

function deliveryQcIsValid(value, { sha256, width, height }) {
  if (!value || value.schema !== "delivery_qc_contract.v1") return false;
  if (!value.checks || typeof value.checks !== "object" || Array.isArray(value.checks)) {
    return false;
  }
  const failed = [];
  const notRun = [];
  for (const name of deliveryQcRequiredChecks) {
    const check = value.checks[name];
    if (
      !check
      || check.name !== name
      || !["passed", "failed", "not_run"].includes(check.status)
      || check.evidence === undefined
      || (check.evidence === null && check.status !== "not_run")
    ) {
      return false;
    }
    if (check.status === "failed") failed.push(name);
    if (check.status === "not_run") notRun.push(name);
  }
  const expectedPassed = failed.length > 0 ? false : notRun.length > 0 ? null : true;
  if (
    value.passed !== expectedPassed
    || JSON.stringify(value.failed_checks ?? null) !== JSON.stringify(failed)
    || JSON.stringify(value.not_run_checks ?? null) !== JSON.stringify(notRun)
  ) {
    return false;
  }
  const shaEvidence = value.checks.sha256.evidence;
  const observedSha = typeof shaEvidence === "string"
    ? shaEvidence
    : shaEvidence?.sha256 ?? shaEvidence?.value;
  if (String(observedSha ?? "") !== sha256) return false;
  const dimensions = value.checks.dimensions.evidence;
  return (
    dimensions
    && typeof dimensions === "object"
    && Number(dimensions.width) === width
    && Number(dimensions.height) === height
  );
}

async function assertNoRecoveryButtons(page) {
  for (const name of [
    "授权并恢复出图",
    "授权并恢复出视频",
    "授权并继续合成",
  ]) {
    const visible = await page
      .getByRole("button", { name })
      .isVisible()
      .catch(() => false);
    assert(!visible, `completed film still exposes secondary authorization: ${name}`);
  }
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
      auto_generate_paid_media: run.inputs?.auto_generate_paid_media,
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
  assert(
    Number.isInteger(expectedShotCount)
      && expectedShotCount >= 1
      && expectedShotCount <= 12,
    `invalid expected shot count: ${expectedShotCount}`,
  );
  assert(
    Number.isInteger(videoDurationSeconds)
      && videoDurationSeconds >= 1
      && videoDurationSeconds <= 30,
    `invalid video duration: ${videoDurationSeconds}`,
  );
  let canvasBefore = await readCanvas();
  const statsBefore = await readStats();
  assert((await readRuns()).length === 0, "T-112 isolated run store must start empty");
  assert(statsBefore.upstream_media_requests.length === 0, "seed media request is not zero");

  const browser = await chromium.launch({
    headless: true,
    executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe",
  });
  const page = await browser.newPage({ viewport: { width: 1440, height: 960 } });
  await page.addInitScript((runMode) => {
    window.localStorage.setItem("village_canvas_agent_run_mode", runMode);
    window.localStorage.setItem(
      "village-canvas:release-notifications:muted",
      "true",
    );
  }, fullChain || recoveryChain ? "auto" : "draft");
  const browserRequests = [];
  const canvasWrites = [];
  const browserErrors = [];
  const consoleErrors = [];
  activePage = page;

  page.on("websocket", (websocket) => {
    websocket.on("framesent", (frame) => {
      const payload = parseCanvasRequest(frame.payload);
      if (payload) browserRequests.push(payload);
    });
  });
  page.on("pageerror", (error) => browserErrors.push(String(error)));
  page.on("console", (message) => {
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
      try {
        requestBody = response.request().postDataJSON();
      } catch {
        requestBody = response.request().postData() ?? null;
      }
      let responseBody = "";
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
  // The fixture can open with a selected asset overlay above the script node.
  // Force the semantic node click so the end-to-end run can continue while
  // preserving the overlay interception as a separately reported UI finding.
  await scriptNode.click({ position: { x: 80, y: 30 }, force: true });
  const composer = await ensureAgentOpen(page);
  await page.getByText("在线", { exact: true })
    .last()
    .waitFor({ state: "visible", timeout: 60_000 });

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
    consoleErrors,
    canvasWrites,
    workflowRunExchanges,
    canvas_revision_at_dispatch: canvasBefore.revision,
  };

  await composer.fill(requestText);
  await composer.press("Enter");
  let failedRun = null;
  let recoveryEvidence = null;
  let runAfter;
  if (recoveryChain) {
    failedRun = await waitForFailedVideoRun();
    const failedArtifact = failedRun.artifacts?.shot_videos ?? {};
    const failedItems = Array.isArray(failedArtifact.recovery?.item_ids)
      ? failedArtifact.recovery.item_ids.map(String)
      : [];
    const statsAtFailure = await readStats();
    const authorizationButton = page.getByRole("button", {
      name: "授权并重试失败视频（1）",
    });
    await authorizationButton.waitFor({ state: "visible", timeout: 30_000 });
    await authorizationButton.click();
    const recoveryRequestDeadline = Date.now() + 8_000;
    while (
      browserRequests.length < 2
      && Date.now() < recoveryRequestDeadline
    ) {
      await page.waitForTimeout(100);
    }
    if (browserRequests.length < 2) {
      const toastText = await page
        .locator("[data-sonner-toast]")
        .allInnerTexts()
        .catch(() => []);
      throw new Error(
        `recovery authorization click did not start a second browser turn: ${JSON.stringify(
          {
            toastText,
            consoleErrors,
            buttonDisabled: await authorizationButton.isDisabled().catch(() => null),
          },
        )}`,
      );
    }
    runAfter = await waitForCompletedRun(
      completedWaitMs,
      failedRun.id,
      failedRun.revision,
    );
    await page.waitForTimeout(1_000);
    recoveryEvidence = {
      failed_run: compactRun(failedRun),
      failed_item_ids: failedItems,
      authorization_button_clicked: true,
      same_run_id: runAfter.id === failedRun.id,
      run_id_before: failedRun.id,
      run_id_after: runAfter.id,
      stats_at_failure: {
        upstream_chat_requests: statsAtFailure.upstream_chat_requests,
        upstream_media_requests: statsAtFailure.upstream_media_requests,
      },
    };
  } else {
    runAfter = fullChain
      ? await waitForCompletedRun(completedWaitMs)
      : await waitForGate();
  }
  await page.waitForTimeout(1_000);
  const runsAfter = await readRuns();
  const canvasAfter = await readCanvas();
  const stats = await readStats();
  const tasks = await readTasks();
  const script = runAfter.artifacts?.script_contract ?? {};
  const scriptNodeBefore = canvasBefore.nodes.find((node) => node.id === scriptNodeId);
  const expectedFingerprint = scriptNodeBefore?.data?.scriptContractReport?.rows_fingerprint;

  const expectedBrowserRequests = recoveryChain ? 2 : 1;
  assert(
    browserRequests.length === expectedBrowserRequests,
    `expected ${expectedBrowserRequests} browser requests, got ${browserRequests.length}`,
  );
  assert(
    browserRequests[0].request === requestText,
    "browser did not send the current script-to-film request",
  );
  if (recoveryChain) {
    assert(failedRun, "recovery chain did not capture the failed run");
    assert(runAfter.id === failedRun.id, "recovery changed the workflow run id");
    assert(
      String(browserRequests[1].request ?? "").includes("已授权付费媒体"),
      "second browser turn is not the explicit paid-media recovery",
    );
    assert(
      browserRequests[1].workflow_runtime?.workflow_run_id === failedRun.id,
      "second browser turn did not bind the original workflow run",
    );
    assert(
      browserRequests[1].task_authorization?.max_paid_starts === 1,
      "second browser turn did not request one paid-media start",
    );
  }
  assert(runsAfter.length === 1, "T-112 created a parallel workflow run");
  assert(runAfter.workflow_id === "freezone-final-film", "wrong workflow selected");
  assert(
    runAfter.inputs?.target_strategy === "reuse_existing"
      && Array.isArray(runAfter.inputs?.target_node_ids)
      && runAfter.inputs.target_node_ids.includes(scriptNodeId),
    "workflow run did not bind the current script node",
  );
  assert(script.status === "completed", "script contract did not complete");
  assert(script.source === "canvas_script_node", "script did not come from the canvas node");
  assert(script.script_node_id === scriptNodeId, "script node identity changed");
  assert(script.canvas_revision === canvasBefore.revision, "script bound the wrong canvas revision");
  assert(script.rows_fingerprint === expectedFingerprint, "script rows fingerprint mismatch");
  assert(sha256Pattern.test(String(script.result_signature ?? "")), "missing script signature");
  assert(
    stats.upstream_chat_requests >= 2,
    `real Hermes did not complete its model/tool round: ${stats.upstream_chat_requests}`,
  );
  assert(canvasAfter.revision === canvasBefore.revision, "workflow wrote the canvas");
  assert(
    canvasWrites.length === canvasWriteBaseline,
    `workflow wrote the canvas: ${JSON.stringify(canvasWrites.slice(canvasWriteBaseline))}`,
  );
  assert(browserErrors.length === 0, `browser errors: ${JSON.stringify(browserErrors)}`);

  let modeEvidence;
  if (fullChain || recoveryChain) {
    const storyboard = runAfter.artifacts?.storyboard_images ?? {};
    const shotVideos = runAfter.artifacts?.shot_videos ?? {};
    const finalFilm = runAfter.artifacts?.final_film ?? {};
    const finalArtifact = finalFilm.final_compose_artifact ?? {};
    const finalPath = String(finalArtifact.path ?? "");
    const submittedTypes = (Array.isArray(tasks) ? tasks : []).map((item) => item.task_type);
    const mediaPaths = stats.upstream_media_requests.map((item) => item.path);
    const shotJobs = Array.isArray(shotVideos.jobs) ? shotVideos.jobs : [];
    const shotVideoItems = Array.isArray(shotVideos.videos) ? shotVideos.videos : [];
    const dialogueAudioReceipts = shotVideoItems
      .map((item) => item.dialogue_audio ?? {})
      .filter(
        (item) =>
          item
          && typeof item === "object"
          && Object.keys(item).length > 0,
      );
    const upstreamVideoDurations = stats.upstream_media_requests
      .filter((item) => item.method === "POST" && isVideoPath(item.path))
      .map((item) => Number(item.requested_duration_seconds));

    assert(browserRequests[0].run_mode === "auto", "full-chain browser run mode must be auto");
    assert(
      browserRequests[0].task_authorization?.allow_paid_media === true
        && Number(browserRequests[0].task_authorization?.max_paid_starts) > 0,
      "full-chain request did not carry usable bounded media authorization",
    );
    assert(runAfter.run_mode === "auto", "full-chain run mode changed");
    assert(runAfter.status === "completed", "full-chain workflow did not complete");
    assert(storyboard.status === "completed", "storyboard did not complete");
    assert(shotVideos.status === "completed", "shot videos did not complete");
    assert(finalFilm.status === "completed", "final film did not complete");
    assert(
      Number(storyboard.shot_count) === expectedShotCount
        && Number(storyboard.completed_count) === expectedShotCount,
      `storyboard shot count mismatch: expected=${expectedShotCount}, actual=${
        JSON.stringify({
          shot_count: storyboard.shot_count,
          completed_count: storyboard.completed_count,
        })
      }`,
    );
    assert(
      Number(shotVideos.shot_count) === expectedShotCount
        && Number(shotVideos.completed_count) === expectedShotCount,
      `shot video count mismatch: expected=${expectedShotCount}, actual=${
        JSON.stringify({
          shot_count: shotVideos.shot_count,
          completed_count: shotVideos.completed_count,
        })
      }`,
    );
    assert(
      shotVideoItems.length === expectedShotCount
        && shotJobs.length === expectedShotCount,
      "shot video artifact does not contain one verified item per requested shot",
    );
    assert(
      upstreamVideoDurations.length === expectedShotCount
        && upstreamVideoDurations.every(
          (value) => value === videoDurationSeconds,
        ),
      `upstream video requests did not expose their duration: ${JSON.stringify(upstreamVideoDurations)}`,
    );
    const shotDurationChecks = shotVideoItems.map((video) => {
      const job = shotJobs.find((item) => item.job_id === video.job_id) ?? {};
      const requested = Number(job.duration_seconds);
      const actual = Number(video.duration_seconds);
      const match = Number.isFinite(requested)
        && Number.isFinite(actual)
        && requested === videoDurationSeconds
        && Math.abs(actual - requested) <= 0.5;
      assert(
        match,
        `shot video duration mismatch: requested=${requested}, actual=${actual}`,
      );
      return {
        shot_id: video.shot_id,
        requested_seconds: requested,
        actual_seconds: actual,
        match,
      };
    });
    const firstFrameChecks = shotVideoItems.map((video) => {
      const similarity = video.first_frame_similarity ?? {};
      const threshold = Number(similarity.threshold);
      const ssim = Number(similarity.ssim);
      const sourceSha256 = String(similarity.source_image_sha256 ?? "");
      const videoSourceSha256 = String(video.source_image_sha256 ?? "");
      const sourceImagePath = String(video.source_image_path ?? "");
      const videoPath = String(video.output_path ?? "");
      const videoSha256 = String(video.sha256 ?? "");
      const videoWidth = Number(video.width);
      const videoHeight = Number(video.height);
      const match = similarity.schema === "video_first_frame_similarity.v1"
        && similarity.status === "passed"
        && Number.isFinite(threshold)
        && threshold === 0.72
        && Number.isFinite(ssim)
        && ssim >= threshold
        && sha256Pattern.test(sourceSha256)
        && sourceSha256 === videoSourceSha256
        && sourceImagePath.length > 0
        && videoPath.length > 0
        && sha256Pattern.test(videoSha256)
        && Number.isInteger(videoWidth)
        && videoWidth > 0
        && Number.isInteger(videoHeight)
        && videoHeight > 0;
      assert(
        match,
        `shot video first-frame QC mismatch: ${JSON.stringify({
          shot_id: video.shot_id,
          similarity,
          video_source_image_sha256: videoSourceSha256,
        })}`,
      );
      return {
        shot_id: video.shot_id,
        ssim,
        threshold,
        status: similarity.status,
        source_image_sha256: sourceSha256,
        source_image_path: sourceImagePath,
        video_path: videoPath,
        video_sha256: videoSha256,
        video_width: videoWidth,
        video_height: videoHeight,
        match,
      };
    });
    if (expectedShotCount > 1) {
      const uniqueSourceHashes = new Set(
        firstFrameChecks.map((check) => check.source_image_sha256),
      );
      assert(
        uniqueSourceHashes.size === expectedShotCount,
        `shot source images are not unique: ${JSON.stringify(uniqueSourceHashes)}`,
      );
    }
    assert(
      shotDurationChecks.some((check) =>
        upstreamVideoDurations.includes(check.requested_seconds)
      ),
      "upstream request duration and persisted shot duration did not agree",
    );
    assert(
      finalArtifact.schema === "workflow_final_compose_artifact.v1"
        && finalArtifact.task_id
        && sha256Pattern.test(String(finalArtifact.sha256 ?? ""))
        && Number(finalArtifact.width) > 0
        && Number(finalArtifact.height) > 0
        && Number(finalArtifact.duration_seconds) > 0,
      "final compose artifact metadata is incomplete",
    );
    const deliveryQc = finalFilm.delivery_qc;
    assert(
      deliveryQcIsValid(deliveryQc, {
        sha256: finalArtifact.sha256,
        width: Number(finalArtifact.width),
        height: Number(finalArtifact.height),
      }),
      `final film delivery QC receipt mismatch: ${JSON.stringify(deliveryQc)}`,
    );
    const releaseReadiness = runAfter.release_readiness ?? {};
    const releaseFailedChecks = Array.isArray(releaseReadiness.failed_checks)
      ? releaseReadiness.failed_checks
      : [];
    const releaseNotRunChecks = Array.isArray(releaseReadiness.not_run_checks)
      ? releaseReadiness.not_run_checks
      : [];
    const expectedReleaseStatus = releaseFailedChecks.length > 0
      ? "blocked"
      : releaseNotRunChecks.length > 0
        ? "unverified"
        : "ready";
    assert(
      releaseReadiness.schema === "release_readiness_contract.v1"
        && releaseReadiness.required === true
        && releaseReadiness.status === expectedReleaseStatus
        && releaseReadiness.can_publish === (expectedReleaseStatus === "ready"),
      `workflow release readiness mismatch: ${JSON.stringify(releaseReadiness)}`,
    );
    if (!releaseReadiness.can_publish) {
      const releaseNotice = page.locator("[data-workflow-release-readiness='v1']");
      await releaseNotice.waitFor({ state: "visible", timeout: 30_000 });
      assert(
        await releaseNotice.getAttribute("data-release-status")
          === releaseReadiness.status,
        "visible release gate does not match the WorkflowRun HTTP receipt",
      );
      assert(
        await releaseNotice.getAttribute("data-can-publish") === "false",
        "blocked or unverified release gate exposed a publish action",
      );
    }
    const expectedTotalDuration = expectedShotCount * videoDurationSeconds;
    assert(
      Math.abs(Number(finalArtifact.duration_seconds) - expectedTotalDuration) <= 0.5,
      `final film total duration mismatch: expected=${expectedTotalDuration}, actual=${
        finalArtifact.duration_seconds
      }`,
    );
    assert(fs.statSync(finalPath).size > 0, "final film file is empty");
    assert(sha256File(finalPath) === finalArtifact.sha256, "final film sha256 mismatch");
    assert(
      stats.upstream_media_requests.length >= 2
        && mediaPaths.some((item) => isImagePath(item))
        && mediaPaths.some((item) => isVideoPath(item)),
      `deterministic media endpoints were not exercised: ${JSON.stringify(mediaPaths)}`,
    );
    if (expectedDialogueText) {
      const audioRequests = stats.upstream_media_requests.filter(
        (item) => item.method === "POST" && isAudioPath(item.path),
      );
      assert(audioRequests.length === 1, "dialogue TTS endpoint was not called exactly once");
      assert(
        dialogueAudioReceipts.length === 1
          && dialogueAudioReceipts.every(
            (item) =>
              item.muxed === true
              && sha256Pattern.test(String(item.audio_sha256 ?? ""))
              && sha256Pattern.test(String(item.output_sha256 ?? "")),
          ),
        `shot videos are missing muxed dialogue receipts: ${JSON.stringify(
          dialogueAudioReceipts,
        )}`,
      );
      assert(
        finalFilm.delivery_qc?.checks?.audio_activity?.status === "passed",
        `final film audio_activity did not pass: ${JSON.stringify(
          finalFilm.delivery_qc?.checks?.audio_activity ?? null,
        )}`,
      );
    }
    for (const taskType of ["freezone_gen", "freezone_video_gen", "compose_episode"]) {
      assert(
        submittedTypes.includes(taskType),
        `missing real task submission: ${taskType}`,
      );
    }
    const taskList = Array.isArray(tasks) ? tasks : [];
    const imageTasks = taskList.filter((item) => item.task_type === "freezone_gen");
    const videoTasks = taskList.filter(
      (item) => item.task_type === "freezone_video_gen",
    );
    const imageReceipts = imageTasks.map(
      (item) => item.production_cost_receipt ?? {},
    );
    const videoReceipts = videoTasks.map(
      (item) => item.production_cost_receipt ?? {},
    );
    const imageReceipt = imageReceipts[0] ?? {};
    const videoReceipt = videoReceipts[0] ?? {};
    assert(
      imageReceipts.length > 0
        && imageReceipts.every(
          (receipt) =>
            Number(receipt.actual_cost?.credits) === 2
            && receipt.cost_source === "result.usage.cost",
        ),
      `image provider cost was not propagated to every image task: ${JSON.stringify(imageReceipts)}`,
    );
    assert(
      videoReceipts.length > 0
        && videoReceipts.every(
          (receipt) =>
            Number(receipt.actual_cost?.credits) === 5
            && receipt.cost_source === "result.usage.cost",
        ),
      `video provider cost was not propagated to every video task: ${JSON.stringify(videoReceipts)}`,
    );
    await assertNoRecoveryButtons(page);
    modeEvidence = {
      storyboard_images: {
        status: storyboard.status,
        shot_count: Number(storyboard.shot_count),
        completed_count: storyboard.completed_count,
        recovery_action: storyboard.recovery?.action ?? null,
      },
      shot_videos: {
        status: shotVideos.status,
        shot_count: Number(shotVideos.shot_count),
        completed_count: shotVideos.completed_count,
        recovery_action: shotVideos.recovery?.action ?? null,
        result_signature: shotVideos.result_signature,
        duration_checks: shotDurationChecks,
        first_frame_checks: firstFrameChecks,
        first_frame_min_ssim: Math.min(
          ...firstFrameChecks.map((check) => check.ssim),
        ),
        source_image_count: new Set(
          firstFrameChecks.map((check) => check.source_image_sha256),
        ).size,
        upstream_requested_durations: upstreamVideoDurations,
      },
      final_film: {
        status: finalFilm.status,
        recovery_action: finalFilm.recovery?.action ?? null,
        final_compose_artifact: {
          schema: finalArtifact.schema,
          task_id: finalArtifact.task_id,
          path: finalArtifact.path,
          sha256: finalArtifact.sha256,
          width: finalArtifact.width,
          height: finalArtifact.height,
          duration_seconds: finalArtifact.duration_seconds,
        },
        delivery_qc: deliveryQc,
        release_readiness: releaseReadiness,
      },
      requested_shot_count: expectedShotCount,
      requested_video_duration_seconds: videoDurationSeconds,
      expected_total_duration_seconds: expectedTotalDuration,
      upstream_media_paths: mediaPaths,
      task_types: submittedTypes,
      cost_receipts: {
        image_task_count: imageReceipts.length,
        freezone_gen: {
          actual_cost: imageReceipt.actual_cost,
          cost_source: imageReceipt.cost_source,
        },
        video_task_count: videoReceipts.length,
        freezone_video_gen: {
          actual_cost: videoReceipt.actual_cost,
          cost_source: videoReceipt.cost_source,
        },
      },
      secondary_authorization_buttons_visible: false,
      dialogue_audio: dialogueAudioReceipts,
    };
    if (recoveryChain) {
      const videoStarts = stats.upstream_media_requests.filter(
        (item) => item.method === "POST" && isVideoPath(item.path),
      );
      const videoStatuses = stats.upstream_media_requests
        .filter((item) => item.method === "POST" && isVideoPath(item.path))
        .map((item) => Number(item.status));
      assert(videoStarts.length === 2, "recovery did not start video exactly twice");
      assert(
        videoStatuses[0] === 502 && videoStatuses[1] === 200,
        `video recovery responses were not 502 then 200: ${JSON.stringify(videoStatuses)}`,
      );
      assert(
        stats.upstream_media_requests.filter(
          (item) => item.method === "POST" && isImagePath(item.path),
        ).length === 1,
        "recovery repeated the completed storyboard image",
      );
      modeEvidence.recovery = {
        same_run_id: runAfter.id === failedRun.id,
        failed_item_ids: recoveryEvidence?.failed_item_ids ?? [],
        video_start_statuses: videoStatuses,
        completed_retry_seq: shotVideoItems[0]?.retry_seq ?? null,
      };
    }
  } else {
    const storyboard = runAfter.artifacts?.storyboard_images ?? {};
    assert(browserRequests[0].run_mode === "draft", "browser run mode must be draft");
    assert(
      browserRequests[0].task_authorization?.allow_paid_media !== true,
      "draft request carried paid-media authorization",
    );
    assert(runAfter.run_mode === "draft", "workflow run mode changed");
    assert(
      runAfter.error_code === "workflow_storyboard_paid_media_not_authorized",
      "run did not stop at the first paid-media gate",
    );
    assert(
      storyboard.recovery?.action === "request_media_authorization",
      "storyboard did not expose its media authorization request",
    );
    assert(storyboard.media_submission_started !== true, "storyboard submitted media early");
    assert(!storyboard.media_authorization, "storyboard contains an unexpected media grant");
    assert(
      stats.upstream_media_requests.length === 0,
      `draft mode reached a media endpoint: ${JSON.stringify(stats.upstream_media_requests)}`,
    );
    assert(
      Array.isArray(tasks) && tasks.length === 0,
      `expected zero task submissions, got ${JSON.stringify(tasks)}`,
    );
    const authorizationButton = page.getByRole("button", { name: "授权并恢复出图" });
    await authorizationButton.waitFor({ state: "visible", timeout: 30_000 });
    modeEvidence = {
      storyboard_images: {
        status: storyboard.status,
        recovery_action: storyboard.recovery?.action,
        media_submission_started: storyboard.media_submission_started === true,
        media_authorization_persisted: Boolean(storyboard.media_authorization),
      },
      authorization_button_visible: true,
    };
  }

  await page.screenshot({ path: screenshotPath, fullPage: true });
  await Promise.allSettled(workflowRunExchangeTasks);
  const evidence = {
    schema: recoveryChain
      ? "t112_real_execution_adapter_recovery_chain.v1"
      : fullChain
      ? "t112_real_execution_adapter_full_chain.v1"
      : "t112_real_execution_adapter.v1",
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
    upstream_chat_requests: stats.upstream_chat_requests,
    upstream_media_requests: stats.upstream_media_requests,
    upstream_request_paths: stats.upstream_requests.map((item) => item.path),
    task_submissions: tasks,
    canvas_writes: canvasWrites,
    select_canvas_writes: selectCanvasWrites,
    workflow_canvas_writes: canvasWrites.slice(canvasWriteBaseline),
    workflow_run_exchanges: workflowRunExchanges,
    browser_errors: browserErrors,
    authorization_button_visible: !fullChain && !recoveryChain,
    screenshot_path: screenshotPath,
    executionAdapterConnected: true,
    paidProvidersConnected: false,
    providerCallsStarted: false,
    execution_adapter_boundary:
      "Browser/WebSocket/FastAPI/plugin/ActionRouter/Hermes ACP/WorkflowRun/generators/task backend are real. The LLM and media upstreams are local deterministic HTTP servers, and no paid provider was called.",
    mode_evidence: modeEvidence,
    ...(recoveryEvidence ? { recovery_chain: recoveryEvidence } : {}),
  };
  fs.mkdirSync(path.dirname(evidencePath), { recursive: true });
  fs.writeFileSync(evidencePath, `${JSON.stringify(evidence, null, 2)}\n`);
  console.log(JSON.stringify(evidence, null, 2));
  await browser.close();
}

main().catch(async (error) => {
  console.error(error);
  await Promise.allSettled(workflowRunExchangeTasks);
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
        schema: "t112_real_execution_adapter_failure.v1",
        error: String(error),
        ...failureContext,
        workflowRunExchanges,
        stats,
        page_text: pageText,
      },
      null,
      2,
    )}\n`,
  );
  process.exit(1);
});
