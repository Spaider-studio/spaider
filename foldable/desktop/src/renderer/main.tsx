import React from "react";
import { createRoot } from "react-dom/client";
// Fonts are bundled from node_modules at build time (no network at runtime).
import "@fontsource-variable/inter/wght.css";
import "@fontsource-variable/jetbrains-mono/wght.css";
import "./styles.css";
import App from "./App";
import OverlayApp from "./OverlayApp";
import { installPerfHelper } from "./lib/perf";

// Dev / verification only: window.__spaiderPerf (frame-gap + latency samples).
installPerfHelper();

const container = document.getElementById("root");
if (!container) throw new Error("#root fehlt");

// The overlay window loads the same bundle with the hash route `#/overlay`.
const isOverlay = window.location.hash.replace(/^#\/?/, "").startsWith("overlay");
document.documentElement.dataset.window = isOverlay ? "overlay" : "main";

createRoot(container).render(
  <React.StrictMode>{isOverlay ? <OverlayApp /> : <App />}</React.StrictMode>,
);
