import { UI_VERSION } from "../uiVersion";

type NetworkEventPayload = {
  category: "frontend_backend" | "frontend_other";
  source: "frontend";
  protocol: string;
  from_entity: string;
  to_entity: string;
  request_direction?: string;
  response_direction?: string;
  method: string;
  url: string;
  host: string;
  path: string;
  status_code?: number;
  success?: boolean;
  request_bytes?: number;
  response_bytes?: number;
  total_bytes?: number;
  duration_ms?: number;
  content_type?: string;
  preview?: string;
  error?: string;
  client_source?: string;
  metadata?: Record<string, unknown>;
};

declare global {
  interface Window {
    __catownNetworkMonitorInstalled?: boolean;
  }
}

function getClientSource() {
  const path = window.location.pathname.toLowerCase();
  if (path === "/monitor" || path === "/monitor/" || path.endsWith("/monitor.html")) {
    return "monitor";
  }
  return "home";
}

function estimateBytes(value: BodyInit | null | undefined) {
  if (!value) return 0;
  if (typeof value === "string") return new TextEncoder().encode(value).length;
  if (value instanceof Blob) return value.size;
  if (value instanceof URLSearchParams) return new TextEncoder().encode(value.toString()).length;
  if (value instanceof FormData) {
    let total = 0;
    for (const [key, entry] of value.entries()) {
      total += new TextEncoder().encode(key).length;
      if (typeof entry === "string") {
        total += new TextEncoder().encode(entry).length;
      } else {
        total += entry.size;
      }
    }
    return total;
  }
  if (value instanceof ArrayBuffer) return value.byteLength;
  if (ArrayBuffer.isView(value)) return value.byteLength;
  return 0;
}

function compactPreview(value: string, limit = 220) {
  return value.replace(/\s+/g, " ").trim().slice(0, limit);
}

function buildEvent(urlValue: string, method: string, requestBytes: number, startedAt: number): NetworkEventPayload {
  const url = new URL(urlValue, window.location.origin);
  const sameOrigin = url.origin === window.location.origin;
  const category = sameOrigin && url.pathname.startsWith("/api/") ? "frontend_backend" : "frontend_other";
  return {
    category,
    source: "frontend",
    protocol: url.protocol.replace(":", "").toUpperCase(),
    from_entity: `Frontend (${getClientSource()})`,
    to_entity: category === "frontend_backend" ? "Backend API" : (url.host || url.origin),
    request_direction: `Frontend (${getClientSource()}) -> ${category === "frontend_backend" ? "Backend API" : (url.host || url.origin)}`,
    response_direction: `${category === "frontend_backend" ? "Backend API" : (url.host || url.origin)} -> Frontend (${getClientSource()})`,
    method,
    url: url.toString(),
    host: url.host,
    path: url.pathname,
    request_bytes: requestBytes,
    duration_ms: Math.max(Math.round(performance.now() - startedAt), 0),
    client_source: getClientSource(),
    metadata: {
      ui_version: UI_VERSION,
    },
  };
}

function postNetworkEvent(originalFetch: typeof window.fetch, event: NetworkEventPayload) {
  if (event.path === "/api/monitor/network/ingest") return;
  void originalFetch("/api/monitor/network/ingest", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Catown-Client": getClientSource(),
      "X-Catown-UI-Version": UI_VERSION,
      "X-Catown-Network-Reporter": "1",
    },
    body: JSON.stringify(event),
    keepalive: true,
  }).catch(() => {
    // Never block the app if the monitor ingest path is unavailable.
  });
}

export function startNetworkMonitor() {
  if (typeof window === "undefined") return;
  if (window.__catownNetworkMonitorInstalled) return;
  window.__catownNetworkMonitorInstalled = true;

  const originalFetch = window.fetch.bind(window);

  window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = input instanceof Request ? input : null;
    const urlValue = request?.url ?? String(input);
    const method = (init?.method || request?.method || "GET").toUpperCase();
    const requestBytes = estimateBytes(init?.body ?? undefined);
    const startedAt = performance.now();

    if (urlValue.includes("/api/monitor/network/ingest")) {
      return originalFetch(input, init);
    }

    try {
      const response = await originalFetch(input, init);
      const responseLength = Number.parseInt(response.headers.get("content-length") || "0", 10);
      const event = buildEvent(urlValue, method, requestBytes, startedAt);
      event.status_code = response.status;
      event.success = response.ok;
      event.response_bytes = Number.isFinite(responseLength) ? Math.max(responseLength, 0) : 0;
      event.total_bytes = (event.request_bytes || 0) + (event.response_bytes || 0);
      event.content_type = response.headers.get("content-type") || "";
      event.preview = compactPreview(`${method} ${new URL(urlValue, window.location.origin).pathname}`);
      if (event.category === "frontend_other") {
        postNetworkEvent(originalFetch, event);
      }
      return response;
    } catch (error) {
      const event = buildEvent(urlValue, method, requestBytes, startedAt);
      event.success = false;
      event.error = error instanceof Error ? error.message : String(error);
      event.preview = compactPreview(`${method} ${new URL(urlValue, window.location.origin).pathname}`);
      if (event.category === "frontend_other") {
        postNetworkEvent(originalFetch, event);
      }
      throw error;
    }
  };
}
