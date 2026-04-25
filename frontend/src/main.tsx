import React from "react";
import ReactDOM from "react-dom/client";

import App from "./App";
import "./styles.css";
import { startNetworkMonitor } from "./utils/networkMonitor";
import { startVersionGuard } from "./versionGuard";

startVersionGuard();
startNetworkMonitor();

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
