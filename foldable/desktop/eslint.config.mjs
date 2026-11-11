import js from "@eslint/js";
import tseslint from "typescript-eslint";
import reactHooks from "eslint-plugin-react-hooks";
import globals from "globals";
import { defineConfig } from "eslint/config";

export default defineConfig([
  { ignores: ["dist/**", "out/**", "node_modules/**", "dev-data/**", "assets/**"] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ["src/renderer/**/*.{ts,tsx}", "tests/**/*.{ts,tsx}"],
    plugins: { "react-hooks": reactHooks },
    rules: {
      "react-hooks/rules-of-hooks": "error",
      "react-hooks/exhaustive-deps": "warn",
    },
    languageOptions: { globals: { ...globals.browser } },
  },
  {
    files: ["src/main/**/*.ts", "src/preload/**/*.ts", "scripts/**/*.mjs", "mock/**/*.mjs", "*.mts", "*.mjs"],
    languageOptions: { globals: { ...globals.node } },
  },
  {
    rules: {
      "@typescript-eslint/no-unused-vars": ["error", { argsIgnorePattern: "^_", varsIgnorePattern: "^_" }],
    },
  },
]);
