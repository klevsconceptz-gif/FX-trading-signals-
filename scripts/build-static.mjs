import { createHash } from "node:crypto";
import { cp, copyFile, mkdir, readFile, readdir, rm, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { transform } from "esbuild";

const root = fileURLToPath(new URL("../", import.meta.url));
const staticDir = path.join(root, "static");
const outputDir = path.join(root, "dist");

// Files that are fingerprinted (name carries a content hash) and cached for a year.
// Everything else under static/ keeps its name and gets a short cache.
const FINGERPRINTED = new Set([".css", ".js", ".woff2", ".woff"]);
const MINIFIED = { ".css": "css", ".js": "js" };

async function listFiles(dir, base = dir) {
  const out = [];
  for (const entry of await readdir(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) out.push(...(await listFiles(full, base)));
    else out.push(path.relative(base, full).split(path.sep).join("/"));
  }
  return out.sort();
}

async function listHtml(dir) {
  return (await listFiles(dir, dir)).filter((rel) => rel.endsWith(".html")).map((rel) => path.join(dir, rel));
}

await rm(outputDir, { recursive: true, force: true });
await mkdir(outputDir, { recursive: true });
await cp(staticDir, path.join(outputDir, "static"), { recursive: true });

// The landing page is served only at /. Its source stays in static/ (the Python dev server and
// the mobile bundle read it), but the copy under /static/ would duplicate the home page, so drop it.
await rm(path.join(outputDir, "static", "landing.html"));

// Keep the public app routes: landing page, dashboard, admin.
await copyFile(path.join(staticDir, "landing.html"), path.join(outputDir, "index.html"));
await mkdir(path.join(outputDir, "app"), { recursive: true });
await copyFile(path.join(staticDir, "index.html"), path.join(outputDir, "app", "index.html"));
await mkdir(path.join(outputDir, "admin"), { recursive: true });
await copyFile(path.join(staticDir, "admin.html"), path.join(outputDir, "admin", "index.html"));

// Crawler files must sit at the site root.
await copyFile(path.join(staticDir, "robots.txt"), path.join(outputDir, "robots.txt"));
await copyFile(path.join(staticDir, "sitemap.xml"), path.join(outputDir, "sitemap.xml"));
// Pages reads response headers from a _headers file at the site root.
await copyFile(path.join(staticDir, "_headers"), path.join(outputDir, "_headers"));
// Pages reads redirects from a _redirects file at the site root. Keep it out of the public /static/ copy.
await copyFile(path.join(staticDir, "_redirects"), path.join(outputDir, "_redirects"));
await rm(path.join(outputDir, "static", "_redirects"));

// Fingerprint CSS, JS and fonts: minify the text ones, hash the final bytes, and write them to
// dist/assets/ under a name that changes whenever the content does. References are rewritten to match.
const fingerprinted = (await listFiles(staticDir)).filter((rel) => FINGERPRINTED.has(path.extname(rel)));
// Fonts and other binaries first, so the stylesheets that reference them can be rewritten.
fingerprinted.sort((a, b) => Number(!!MINIFIED[path.extname(a)]) - Number(!!MINIFIED[path.extname(b)]));

const urlFor = new Map(); // "fonts/x.woff2" -> "/assets/fonts/x.<hash>.woff2"

function rewriteReferences(text) {
  if (urlFor.size === 0) return text;
  const names = [...urlFor.keys()].sort((a, b) => b.length - a.length).map((n) => n.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  const pattern = new RegExp(`/static/(${names.join("|")})(?![A-Za-z0-9_./-])`, "g");
  return text.replace(pattern, (_, rel) => urlFor.get(rel));
}

let bytesBefore = 0;
let bytesAfter = 0;
for (const rel of fingerprinted) {
  const ext = path.extname(rel);
  const original = await readFile(path.join(staticDir, rel));
  bytesBefore += original.length;

  let content = original;
  if (MINIFIED[ext]) {
    const source = rewriteReferences(original.toString("utf8"));
    const result = await transform(source, {
      loader: MINIFIED[ext],
      minify: true,
      target: "es2018",
    });
    content = Buffer.from(result.code, "utf8");
  }

  const hash = createHash("sha256").update(content).digest("hex").slice(0, 10);
  const assetRel = rel.slice(0, -ext.length) + `.${hash}${ext}`;
  const target = path.join(outputDir, "assets", assetRel);
  await mkdir(path.dirname(target), { recursive: true });
  await writeFile(target, content);
  await rm(path.join(outputDir, "static", rel));
  urlFor.set(rel, `/assets/${assetRel}`);
  bytesAfter += content.length;
}

// Point every HTML page at the fingerprinted files.
for (const file of await listHtml(outputDir)) {
  await writeFile(file, rewriteReferences(await readFile(file, "utf8")));
}

console.log(
  `Fingerprinted ${fingerprinted.length} CSS, JS and font files (${bytesBefore} -> ${bytesAfter} bytes).`,
);
console.log("Built Cloudflare Pages frontend in dist/ (API routes proxy to the Cloudflare Worker).");
