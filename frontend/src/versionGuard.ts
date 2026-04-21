import { UI_VERSION } from "./uiVersion";

const RELOAD_MARKER_KEY = "catown:ui-reload-marker";
const VERSION_POLL_INTERVAL_MS = 15000;

type FrontendMeta = {
  ui_version?: string;
  build_id?: string;
  page?: string;
};

function clientSource() {
  if (typeof window === "undefined") return "unknown";
  const path = window.location.pathname.toLowerCase();
  if (path === "/monitor" || path === "/monitor/" || path.endsWith("/monitor.html")) {
    return "monitor";
  }
  return "home";
}

function currentBuildId() {
  if (typeof document === "undefined") return "unknown";
  const assets = Array.from(
    document.querySelectorAll('script[src*="/assets/"], link[href*="/assets/"]'),
  )
    .map((node) =>
      node instanceof HTMLScriptElement
        ? node.getAttribute("src") || ""
        : node instanceof HTMLLinkElement
          ? node.getAttribute("href") || ""
          : "",
    )
    .filter(Boolean)
    .sort();
  return assets.join("|") || "unknown";
}

function debugVersionGuard(event: string, details: Record<string, unknown>) {
  if (typeof window === "undefined") return;
  console.info(`[CatownVersionGuard] ${event}`, {
    uiVersion: UI_VERSION,
    buildId: currentBuildId(),
    source: clientSource(),
    origin: window.location.origin,
    path: window.location.pathname,
    ...details,
  });
}

function reloadMarker(target: { uiVersion?: string | null; buildId?: string | null }) {
  return `${target.uiVersion || "unknown"}|${target.buildId || "unknown"}|${clientSource()}`;
}

function maybeForceReload(
  target: { uiVersion?: string | null; buildId?: string | null },
  reason: string,
) {
  if (typeof window === "undefined") return false;
  const uiVersionMismatch = Boolean(target.uiVersion && target.uiVersion !== UI_VERSION);
  const buildIdMismatch = Boolean(target.buildId && target.buildId !== currentBuildId());
  if (!uiVersionMismatch && !buildIdMismatch) {
    try {
      window.sessionStorage.removeItem(RELOAD_MARKER_KEY);
    } catch {
      // Ignore session storage access failures.
    }
    return false;
  }

  const marker = reloadMarker(target);
  try {
    if (window.sessionStorage.getItem(RELOAD_MARKER_KEY) === marker) {
      debugVersionGuard("reload-skipped", {
        reason,
        targetUiVersion: target.uiVersion,
        targetBuildId: target.buildId,
      });
      return true;
    }
    window.sessionStorage.setItem(RELOAD_MARKER_KEY, marker);
  } catch {
    // Ignore session storage access failures.
  }

  debugVersionGuard("reload", {
    reason,
    targetUiVersion: target.uiVersion,
    targetBuildId: target.buildId,
  });
  window.location.reload();
  return true;
}

export function handleServerVersionHeaders(headers: Headers, reason: string) {
  const serverUiVersion = headers.get("X-Catown-Server-UI-Version");
  const serverBuildId = headers.get("X-Catown-Server-Build-Id");
  return maybeForceReload(
    {
      uiVersion: serverUiVersion,
      buildId: serverBuildId,
    },
    reason,
  );
}

async function fetchFrontendMeta(reason: string) {
  const response = await fetch("/api/frontend-meta", {
    cache: "no-store",
    headers: {
      "X-Catown-Client": clientSource(),
      "X-Catown-UI-Version": UI_VERSION,
    },
  });
  handleServerVersionHeaders(response.headers, `meta-header:${reason}`);
  if (!response.ok) return null;
  return (await response.json()) as FrontendMeta;
}

let guardStarted = false;

export function startVersionGuard() {
  if (typeof window === "undefined" || guardStarted) return;
  guardStarted = true;

  const runCheck = async (reason: string) => {
    try {
      const meta = await fetchFrontendMeta(reason);
      if (!meta) return;
      maybeForceReload(
        {
          uiVersion: meta.ui_version || null,
          buildId: meta.build_id || null,
        },
        `meta:${reason}`,
      );
      debugVersionGuard("checked", {
        reason,
        serverUiVersion: meta.ui_version || null,
        serverBuildId: meta.build_id || null,
      });
    } catch (error) {
      debugVersionGuard("check-failed", {
        reason,
        error: error instanceof Error ? error.message : String(error),
      });
    }
  };

  void runCheck("startup");

  const intervalId = window.setInterval(() => {
    void runCheck("interval");
  }, VERSION_POLL_INTERVAL_MS);

  window.addEventListener("focus", () => {
    void runCheck("focus");
  });

  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") {
      void runCheck("visible");
    }
  });

  window.addEventListener("beforeunload", () => {
    window.clearInterval(intervalId);
  });
}
