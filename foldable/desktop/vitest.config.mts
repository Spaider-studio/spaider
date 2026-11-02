import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import path from "node:path";

const desktopDir = import.meta.dirname;
const frontendSrc = path.resolve(desktopDir, "../../frontend/src");

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "@": frontendSrc },
    dedupe: ["react", "react-dom", "lucide-react", "@radix-ui/react-toast", "@radix-ui/react-dialog"],
  },
  test: {
    environment: "jsdom",
    include: ["tests/**/*.test.{ts,tsx}"],
    css: false,
  },
});
