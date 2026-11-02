import React from "react";
import { createRoot } from "react-dom/client";
// Fonts are bundled from node_modules at build time (no network at runtime).
import "@fontsource-variable/inter/wght.css";
import "@fontsource-variable/jetbrains-mono/wght.css";
import "./styles.css";
import App from "./App";

const container = document.getElementById("root");
if (!container) throw new Error("#root fehlt");

createRoot(container).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
