// Every page's own .css/.js link carries ?v=<first 8 of the file's SHA-1>, and it is current.
// A stale one means a phone can pair a new page with an old cached file (asset_versions.py).
// Fix: python3 asset_versions.py, then commit the pages with the changed file.
const fs = require("fs");
const crypto = require("crypto");
const { execFileSync } = require("child_process");

let fails = 0;
const ok = (c, m) => { if (!c) { fails++; console.log("FAIL " + m); } };
const pages = execFileSync("git", ["ls-files", "*.html"], { encoding: "utf8" }).split("\n").filter(p => p && !p.includes("/"));
const LINK = /\b(?:src|href)="([a-z][a-z0-9_-]*\.(?:js|css))(?:\?v=([0-9a-f]*))?"/g;
let links = 0;
for (const page of pages) {
  for (const [, name, have] of fs.readFileSync(page, "utf8").matchAll(LINK)) {
    if (!fs.existsSync(name)) continue;
    links++;
    const want = crypto.createHash("sha1").update(fs.readFileSync(name)).digest("hex").slice(0, 8);
    ok(have === want, `${page}: ${name} has ?v=${have || "(none)"}, wants ?v=${want} (run python3 asset_versions.py)`);
  }
}
ok(links > 40, `found ${links} page links to fingerprint`);
if (fails) process.exit(1);
console.log(`all ${links} asset fingerprints current`);
