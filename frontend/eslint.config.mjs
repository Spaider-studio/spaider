import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

export default defineConfig([
  ...nextVitals,
  ...nextTs,
  {
    // Style and pattern guidance, reported but not blocking. `npm run lint` fails
    // when the number of warnings grows (see --max-warnings in package.json), so
    // the count can only go down.
    rules: {
      "@typescript-eslint/no-explicit-any": "warn",
      "react-hooks/set-state-in-effect": "warn",
      "react-hooks/refs": "warn",
    },
  },
  globalIgnores([".next/**", "node_modules/**", "out/**", "test-results/**", "playwright-report/**", "next-env.d.ts"]),
]);
