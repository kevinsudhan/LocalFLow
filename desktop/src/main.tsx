import React from "react";
import ReactDOM from "react-dom/client";

import { App } from "./App";
import { Hud } from "./pages/Hud";
import "./styles/index.css";

/**
 * Two windows share one bundle. The HUD route is decided before React mounts
 * so the overlay never pays for the settings UI's imports or state.
 */
const isHud = window.location.hash.startsWith("#/hud");

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>{isHud ? <Hud /> : <App />}</React.StrictMode>,
);
