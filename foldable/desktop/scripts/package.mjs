/**
 * Packages the desktop shell with @electron/packager into out/SpAIder-win32-x64/.
 * Only dist/ (compiled main, preload, renderer), package.json and assets/ end
 * up inside app.asar; node_modules is excluded entirely because the renderer
 * is bundled by Vite and the main process only uses Node/Electron built-ins.
 * Assembling the portable ZIP (core exe, llama runtime) is a separate step.
 */
import { packager } from "@electron/packager";
import fs from "node:fs";
import path from "node:path";

const desktopDir = path.resolve(import.meta.dirname, "..");
const pkg = JSON.parse(fs.readFileSync(path.join(desktopDir, "package.json"), "utf8"));
const electronVersion = JSON.parse(
  fs.readFileSync(path.join(desktopDir, "node_modules", "electron", "package.json"), "utf8"),
).version;

for (const required of ["dist/main/index.js", "dist/preload/index.js", "dist/renderer/index.html"]) {
  if (!fs.existsSync(path.join(desktopDir, required))) {
    console.error(`missing ${required} - run "npm run build" first`);
    process.exit(1);
  }
}

const icon = path.join(desktopDir, "assets", "icon.ico");
const ignore = [
  /^\/src($|\/)/,
  /^\/mock($|\/)/,
  /^\/scripts($|\/)/,
  /^\/tests($|\/)/,
  /^\/dev-data($|\/)/,
  /^\/out($|\/)/,
  /^\/node_modules($|\/)/,
  /^\/\.gitignore$/,
  /^\/tsconfig[^/]*\.json$/,
  /^\/vite\.config\.mts$/,
  /^\/vitest\.config\.mts$/,
  /^\/eslint\.config\.mjs$/,
  /^\/package-lock\.json$/,
  /^\/index\.html$/,
  /^\/NOTES\.md$/,
  /^\/README\.md$/,
];

const base = {
  dir: desktopDir,
  out: path.join(desktopDir, "out"),
  platform: "win32",
  arch: "x64",
  electronVersion,
  name: "SpAIder",
  executableName: "SpAIder",
  appVersion: pkg.version,
  appCopyright: "SpAIder - AGPL-3.0-only",
  asar: true,
  overwrite: true,
  prune: true,
  ignore,
};

const withMetadata = {
  ...base,
  icon: fs.existsSync(icon) ? icon : undefined,
  win32metadata: {
    CompanyName: "SpAIder",
    ProductName: "SpAIder Foldable",
    FileDescription: "SpAIder Foldable",
    InternalName: "SpAIder",
    OriginalFilename: "SpAIder.exe",
  },
};

let outPaths;
try {
  outPaths = await packager(withMetadata);
} catch (err) {
  // rcedit (icon / version resource editing) can fail on locked-down machines.
  // Fall back to a build without exe metadata so the package still exists.
  console.warn(`packaging with icon/metadata failed: ${err?.message ?? err}`);
  console.warn("retrying without icon and win32metadata (documented in NOTES.md)");
  outPaths = await packager(base);
}

const exe = path.join(outPaths[0], "SpAIder.exe");
if (!fs.existsSync(exe)) {
  console.error(`expected executable not found: ${exe}`);
  process.exit(1);
}
console.log(`packaged: ${exe}`);
