import { cp, copyFile, mkdir, rm } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../", import.meta.url));
const staticDir = path.join(root, "static");
const outputDir = path.join(root, "dist");

await rm(outputDir, { recursive: true, force: true });
await mkdir(outputDir, { recursive: true });
await cp(staticDir, path.join(outputDir, "static"), { recursive: true });

// Keep the same public routes as the FastAPI app: landing page, dashboard, admin.
await copyFile(path.join(staticDir, "landing.html"), path.join(outputDir, "index.html"));
await mkdir(path.join(outputDir, "app"), { recursive: true });
await copyFile(path.join(staticDir, "index.html"), path.join(outputDir, "app", "index.html"));
await mkdir(path.join(outputDir, "admin"), { recursive: true });
await copyFile(path.join(staticDir, "admin.html"), path.join(outputDir, "admin", "index.html"));

console.log("Built Cloudflare Pages frontend in dist/ (FastAPI API is hosted separately).");
