// Builds the web bundle that the Android and iOS shells (Capacitor) load from inside the app.
//
// The bundle is the same static/ source as the web build, with two differences:
//   - index.html is the member dashboard and admin.html is the operator console, both at the
//     bundle root, so the native shells can reach them without a server.
//   - static/runtime.js gets the Worker origin, so /api calls go straight to the Worker.
//
// KLEVS_API_ORIGIN overrides the Worker origin. It must be an https origin with no path.
import { cp, copyFile, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../", import.meta.url));
const staticDir = path.join(root, "static");
const outputDir = path.join(root, "mobile", "www");
const PLACEHOLDER = "__KLEVS_API_ORIGIN__";
const DEFAULT_API_ORIGIN = "https://klv-fx-trading-signals.klevco.workers.dev";

const origin = (process.env.KLEVS_API_ORIGIN || DEFAULT_API_ORIGIN).trim().replace(/\/+$/, "");
if (!/^https:\/\/[a-z0-9.-]+(:\d{1,5})?$/i.test(origin)) {
  throw new Error(`KLEVS_API_ORIGIN must be an https origin without a path, got '${origin}'`);
}

await rm(outputDir, { recursive: true, force: true });
await mkdir(outputDir, { recursive: true });
await cp(staticDir, path.join(outputDir, "static"), { recursive: true });
await copyFile(path.join(staticDir, "index.html"), path.join(outputDir, "index.html"));
await copyFile(path.join(staticDir, "admin.html"), path.join(outputDir, "admin.html"));

const runtimePath = path.join(outputDir, "static", "runtime.js");
const runtime = await readFile(runtimePath, "utf8");
if (!runtime.includes(PLACEHOLDER)) throw new Error("static/runtime.js is missing the API origin placeholder");
await writeFile(runtimePath, runtime.replace(PLACEHOLDER, origin));

console.log(`Built the mobile bundle in mobile/www/ (API origin ${origin}).`);
