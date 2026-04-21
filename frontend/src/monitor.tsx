import React from "react";
import ReactDOM from "react-dom/client";

import { MonitorTab } from "./components/MonitorTab";
import "./monitor.css";
import { startVersionGuard } from "./versionGuard";

startVersionGuard();

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <MonitorTab />
  </React.StrictMode>,
);
