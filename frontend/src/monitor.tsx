import React from "react";
import ReactDOM from "react-dom/client";

import { MonitorTab } from "./components/MonitorTab";
import "./monitor.css";
import { startNetworkMonitor } from "./utils/networkMonitor";
import { startVersionGuard } from "./versionGuard";

startVersionGuard();
startNetworkMonitor();

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <MonitorTab />
  </React.StrictMode>,
);
