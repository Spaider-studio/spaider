import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "node:path";

const desktopDir = import.meta.dirname;
// Reuse the SpAIder frontend design system (shared components, cn()).
const frontendSrc = path.resolve(desktopDir, "../../frontend/src");

/**
 * The production CSP (index.html) is strict. The Vite dev server needs the
 * HMR websocket and the inline React-refresh preamble, so only in `vite dev`
 * the meta tag is widened. The built index.html keeps the strict policy.
 */
function devCsp(): Plugin {
  return {
    name: "spaider-dev-csp",
    apply: "serve",
    transformIndexHtml(html) {
      return html.replace(
        /content="default-src 'self';[^"]*"/,
        `content="default-src 'self'; script-src 'self' 'unsafe-inline'; connect-src 'self' http://127.0.0.1:* ws://localhost:5173 http://localhost:5173; img-src 'self' data:; style-src 'self' 'unsafe-inline'; font-src 'self' data:"`,
      );
    },
  };
}

export default defineConfig({
  base: "./",
  plugins: [react(), tailwindcss(), devCsp()],
  resolve: {
    alias: { "@": frontendSrc },
    // Files imported from ../../frontend/src must resolve their bare imports
    // from this package's node_modules (one React copy).
    dedupe: ["react", "react-dom", "lucide-react", "@radix-ui/react-toast", "@radix-ui/react-dialog"],
  },
  server: {
    port: 5173,
    strictPort: true,
    fs: { allow: [desktopDir, frontendSrc] },
  },
  build: {
    outDir: "dist/renderer",
    emptyOutDir: true,
    target: "chrome140",
    sourcemap: false,
    // Single bundle is intended (loaded from file://, no HTTP caching to win).
    chunkSizeWarningLimit: 1200,
  },
});
