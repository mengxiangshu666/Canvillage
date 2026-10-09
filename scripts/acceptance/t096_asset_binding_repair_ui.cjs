const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require("playwright");

const projectId = "01M2TNN3XXK2D5CRXDBKTTFM4W";
const canvasId = "ui_smoke_canvas";
const runId = "wfr_94698b0b6eca4085aff7bab39e5b7a88";
const uiBaseUrl =
  `http://127.0.0.1:5175/projects/${projectId}/freezone?canvas=${canvasId}`;
const apiBase = "http://127.0.0.1:8785/api/v1";
const evidencePath = path.resolve(
  "workspace/ui-smoke-t096/t096-browser-evidence.json",
);
const screenshotPath = path.resolve(
  "workspace/ui-smoke-t096/t096-authorization-ready.png",
);

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

async function readJson(url) {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`GET ${url} -> ${response.status}`);
  }
  return (await response.json()).data;
}

async function readRun() {
  return await readJson(
    `${apiBase}/projects/${projectId}/workflow-runs/${runId}`,
  );
}

async function readCanvas() {
  return await readJson(
    `${apiBase}/projects/${projectId}/freezone/canvases/${canvasId}`,
  );
}

function nodeById(canvas, nodeId) {
  return (canvas.nodes ?? []).find((node) => node.id === nodeId) ?? null;
}

function compactRun(run) {
  const storyboard = run.artifacts?.storyboard_images;
  return {
    status: run.status,
    revision: run.revision,
    event_seq: run.event_seq,
    next_action: run.next_action,
    error_code: run.error_code,
    step_attempt: run.step_states?.storyboard_images?.attempt ?? null,
    recovery_action: storyboard?.recovery?.action ?? null,
    readiness_ready: storyboard?.readiness?.ready ?? null,
    media_authorization_persisted: Boolean(storyboard?.media_authorization),
    media_submission_started:
      storyboard?.media_submission_started === true,
  };
}

function compactCanvas(canvas) {
  const assetA = nodeById(canvas, "asset-a");
  const assetB = nodeById(canvas, "asset-b");
  return {
    revision: canvas.revision,
    node_ids: (canvas.nodes ?? []).map((node) => node.id),
    asset_a_binding: assetA?.data?.scriptAssetId ?? null,
    asset_b_binding: assetB?.data?.scriptAssetId ?? null,
    asset_b_present: Boolean(assetB),
    asset_b_generation_error: assetB?.data?.generationError ?? null,
  };
}

async function dismissReleaseNotes(page) {
  const known = page.getByRole("button", { name: "我知道了" });
  try {
    await known.waitFor({ state: "visible", timeout: 10_000 });
    await known.click();
    await known.waitFor({ state: "hidden", timeout: 10_000 });
  } catch {
    // The release dialog only appears on a fresh browser profile.
  }
}

async function ensureAgentOpen(page, repairButton) {
  if (await repairButton.isVisible().catch(() => false)) return;
  const openAgent = page.locator('[aria-label="打开搭子创作台"]');
  if (
    (await openAgent.count()) > 0
    && (await openAgent.isVisible().catch(() => false))
  ) {
    await openAgent.click();
    await page.waitForTimeout(800);
  }
}

async function selectedNodeIds(page) {
  return await page.evaluate(() =>
    [...document.querySelectorAll(".react-flow__node.selected")]
      .map((node) => node.getAttribute("data-id"))
      .filter(Boolean),
  );
}

async function main() {
  const canvasBefore = await readCanvas();
  const runBefore = await readRun();
  const before = {
    canvas: compactCanvas(canvasBefore),
    run: compactRun(runBefore),
  };

  assert(runBefore.status === "failed", "seed run must be failed");
  assert(
    runBefore.artifacts?.storyboard_images?.recovery?.action
      === "repair_canvas_asset_binding",
    "seed run must be at the asset-binding repair gate",
  );
  assert(canvasBefore.revision === 9, "seed canvas must start at revision 9");
  assert(
    nodeById(canvasBefore, "asset-a")?.data?.scriptAssetId === "scene:darkroom",
    "asset-a seed binding mismatch",
  );
  assert(
    nodeById(canvasBefore, "asset-b")?.data?.scriptAssetId === "scene:darkroom",
    "asset-b seed binding mismatch",
  );

  const browser = await chromium.launch({
    headless: true,
    executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe",
  });
  const page = await browser.newPage({ viewport: { width: 1440, height: 960 } });
  const writes = [];
  const repairRequests = [];
  const revalidateRequests = [];
  const providerRequests = [];
  let canvasRevisionBeforeRepair = null;
  let canvasRevisionAfterFlush = null;

  page.on("request", (request) => {
    const method = request.method();
    const url = request.url();
    if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
      writes.push(`${method} ${url}`);
    }
    if (url.includes("/canvas-asset-binding-repair")) {
      repairRequests.push({
        method,
        url,
        body: request.postDataJSON?.() ?? null,
      });
    }
    if (url.includes("/canvas-asset-binding-revalidate")) {
      revalidateRequests.push({
        method,
        url,
        body: request.postDataJSON?.() ?? null,
      });
    }
    if (
      !["GET", "HEAD", "OPTIONS"].includes(method)
      && (
        /\/freezone\/(gen|video|audio)/.test(url)
        || /\/workflow-runs\/[^/]+\/(command|advance|resume|retry|authorize)/.test(url)
        || /\/media-authorizations?/.test(url)
      )
    ) {
      providerRequests.push(`${method} ${url}`);
    }
  });

  page.on("response", async (response) => {
    const request = response.request();
    if (
      request.method() === "PUT"
      && response.url().includes(`/freezone/canvases/${canvasId}`)
      && response.ok()
    ) {
      try {
        const body = await response.json();
        canvasRevisionAfterFlush = body?.data?.revision ?? null;
      } catch {
        canvasRevisionAfterFlush = null;
      }
    }
  });

  await page.route("**/canvas-asset-binding-repair", async (route) => {
    if (canvasRevisionBeforeRepair === null) {
      const snapshot = await readCanvas();
      canvasRevisionBeforeRepair = snapshot.revision;
    }
    await route.continue();
  });

  let repairButton;
  try {
    await page.goto(uiBaseUrl, {
      waitUntil: "domcontentloaded",
      timeout: 60_000,
    });
    await page.waitForTimeout(2_000);
    await dismissReleaseNotes(page);
    repairButton = page.locator('[data-workflow-recovery-repair="v1"]');
    await ensureAgentOpen(page, repairButton);
    await repairButton.waitFor({ state: "visible", timeout: 60_000 });
  } catch (error) {
    const diagnostic = await page.evaluate(() => ({
      url: location.href,
      title: document.title,
      text: document.body.innerText.slice(0, 4_000),
      buttons: [...document.querySelectorAll("button")].map((button) => ({
        text: button.innerText,
        ariaLabel: button.getAttribute("aria-label"),
        repair: button.getAttribute("data-workflow-recovery-repair"),
      })),
    }));
    await page.screenshot({
      path: path.resolve("workspace/ui-smoke-t096/t096-diagnostic.png"),
      fullPage: true,
    });
    throw new Error(
      `${error.message}\n${JSON.stringify(diagnostic, null, 2)}`,
    );
  }

  const selectedBefore = await selectedNodeIds(page);
  const repairResponsePromise = page.waitForResponse(
    (response) =>
      response.url().includes("/canvas-asset-binding-repair")
      && response.request().method() === "POST",
    { timeout: 60_000 },
  );
  const revalidateResponsePromise = page.waitForResponse(
    (response) =>
      response.url().includes("/canvas-asset-binding-revalidate")
      && response.request().method() === "POST",
    { timeout: 60_000 },
  );

  await repairButton.click();
  const [repairResponse, revalidateResponse] = await Promise.all([
    repairResponsePromise,
    revalidateResponsePromise,
  ]);
  assert(repairResponse.ok(), "repair endpoint response was not OK");
  assert(revalidateResponse.ok(), "revalidate endpoint response was not OK");

  const authorizationButton = page.getByRole("button", {
    name: "授权并恢复出图",
  });
  await authorizationButton.waitFor({ state: "visible", timeout: 30_000 });
  await page.waitForTimeout(500);

  const selectedAfter = await selectedNodeIds(page);
  const canvasAfter = await readCanvas();
  const runAfter = await readRun();
  const after = {
    canvas: compactCanvas(canvasAfter),
    run: compactRun(runAfter),
    selected_before: selectedBefore,
    selected_after: selectedAfter,
    authorization_button_visible: await authorizationButton.isVisible(),
  };

  const repair = repairRequests[0]?.body ?? null;
  const revalidate = revalidateRequests[0]?.body ?? null;
  assert(repairRequests.length === 1, "expected exactly one repair request");
  assert(
    revalidateRequests.length === 1,
    "expected exactly one revalidate request",
  );
  assert(
    repair?.canvas_id === canvasId
      && repair?.step_id === "storyboard_images"
      && repair?.expected_run_revision === runBefore.revision,
    "repair payload contract mismatch",
  );
  assert(
    revalidate?.canvas_id === canvasId
      && revalidate?.step_id === "storyboard_images"
      && revalidate?.expected_run_revision === runBefore.revision,
    "revalidate payload contract mismatch",
  );
  assert(
    repair.command_id
      && revalidate.command_id
      && repair.command_id !== revalidate.command_id,
    "repair and revalidate command ids must be present and distinct",
  );

  assert(
    canvasRevisionBeforeRepair !== null,
    "repair request snapshot did not capture a canvas revision",
  );
  assert(
    canvasAfter.revision === canvasRevisionBeforeRepair + 1,
    `repair must advance canvas exactly once: ${
      canvasRevisionBeforeRepair
    } -> ${canvasAfter.revision}`,
  );
  assert(
    nodeById(canvasAfter, "asset-a")?.data?.scriptAssetId === "scene:darkroom",
    "asset-a must keep the binding",
  );
  assert(
    nodeById(canvasAfter, "asset-b")?.data?.scriptAssetId == null,
    "asset-b must lose the duplicate binding",
  );
  assert(
    nodeById(canvasAfter, "asset-b") !== null,
    "asset-b must remain on the canvas",
  );
  assert(
    nodeById(canvasAfter, "asset-b")?.data?.generationError
      === "T-093 isolated duplicate without usable image",
    "asset-b generation error must be preserved",
  );

  assert(runAfter.status === "failed", "run must remain failed at the paid gate");
  assert(
    runAfter.revision === runBefore.revision + 1,
    "run must advance exactly once",
  );
  assert(
    runAfter.step_states?.storyboard_images?.attempt
      === runBefore.step_states?.storyboard_images?.attempt,
    "repair must not consume a new step attempt",
  );
  assert(
    runAfter.artifacts?.storyboard_images?.recovery?.action
      === "request_media_authorization",
    "run must hand off to the media authorization gate",
  );
  assert(
    runAfter.artifacts?.storyboard_images?.readiness?.ready === true,
    "revalidated storyboard assets must be ready",
  );
  assert(
    !runAfter.artifacts?.storyboard_images?.media_authorization,
    "repair must not persist a media authorization grant",
  );
  assert(
    runAfter.artifacts?.storyboard_images?.media_submission_started !== true,
    "repair must not submit provider media",
  );
  assert(providerRequests.length === 0, "provider media request detected");
  assert(
    selectedAfter.includes("asset-a") && !selectedAfter.includes("asset-b"),
    `focus mismatch: ${JSON.stringify(selectedAfter)}`,
  );

  await page.screenshot({ path: screenshotPath, fullPage: true });
  const evidence = {
    schema: "t096_asset_binding_repair_ui_evidence.v1",
    ui_base_url: uiBaseUrl,
    api_base_url: apiBase,
    project_id: projectId,
    canvas_id: canvasId,
    run_id: runId,
    before,
    canvas_revision_after_flush: canvasRevisionAfterFlush,
    canvas_revision_before_repair: canvasRevisionBeforeRepair,
    after,
    repair_request: repair,
    revalidate_request: revalidate,
    writes,
    provider_requests: providerRequests,
    provider_media_submissions: 0,
    paid_grants_consumed: 0,
    screenshot_path: screenshotPath,
  };
  fs.mkdirSync(path.dirname(evidencePath), { recursive: true });
  fs.writeFileSync(evidencePath, `${JSON.stringify(evidence, null, 2)}\n`);
  console.log(JSON.stringify(evidence, null, 2));
  await browser.close();
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
