const HEARTBEAT_INTERVAL_MS = 20_000;
const SESSION_STORAGE_KEY = "village-canvas.localBrowserSessionId";

function sessionId(): string {
  const existing = window.sessionStorage.getItem(SESSION_STORAGE_KEY);
  if (existing && /^[A-Za-z0-9_-]{16,128}$/.test(existing)) return existing;
  const created = crypto.randomUUID().replace(/-/g, "");
  window.sessionStorage.setItem(SESSION_STORAGE_KEY, created);
  return created;
}

function endpoint(id: string, action: "heartbeat" | "leave"): string {
  return `/api/v1/runtime/browser-sessions/${id}/${action}`;
}

function send(id: string, action: "heartbeat" | "leave", keepalive = false): void {
  void fetch(endpoint(id, action), {
    method: "POST",
    credentials: "include",
    cache: "no-store",
    keepalive,
  }).catch(() => undefined);
}

/** Keep the bundled 8781 backend alive while at least one real browser tab exists. */
export function installLocalBrowserPresence(): () => void {
  const id = sessionId();
  const heartbeat = () => send(id, "heartbeat");
  const leave = () => send(id, "leave", true);
  const visibilityHeartbeat = () => {
    if (document.visibilityState === "visible") heartbeat();
  };

  heartbeat();
  const interval = window.setInterval(heartbeat, HEARTBEAT_INTERVAL_MS);
  window.addEventListener("pagehide", leave);
  document.addEventListener("visibilitychange", visibilityHeartbeat);

  return () => {
    window.clearInterval(interval);
    window.removeEventListener("pagehide", leave);
    document.removeEventListener("visibilitychange", visibilityHeartbeat);
  };
}
