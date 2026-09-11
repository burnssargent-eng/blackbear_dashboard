#!/usr/bin/env node
//
// Runs every front-end suite in this directory and exits non-zero if any fails,
// so it can be wired into CI later the way check_town_mismatches.py already is.
//
//   node tests/run_all.js
//
// These test the SHIPPED code: each suite pulls the real functions and template
// strings out of the .html files and evaluates them against the real committed
// JSON. They are not copies of the logic, so they fail when the page changes.

const { execFileSync } = require("child_process");
const fs = require("fs");
const path = require("path");

process.chdir(path.join(__dirname, ".."));

const suites = fs
  .readdirSync(__dirname)
  .filter(f => f.endsWith(".js") && f !== path.basename(__filename))
  .sort();

let failed = 0;

for (const suite of suites) {
  const label = suite.replace(/\.js$/, "");
  try {
    const out = execFileSync("node", [path.join("tests", suite)], {
      encoding: "utf8"
    });
    const last = out.trim().split("\n").pop();
    console.log(`  PASS  ${label.padEnd(24)} ${last}`);
  } catch (err) {
    failed++;
    const out = `${err.stdout || ""}${err.stderr || ""}`.trim();
    console.log(`  FAIL  ${label}`);
    // Only the failing lines, so a broken suite is readable at a glance.
    out
      .split("\n")
      .filter(l => /FAIL|Error|error/.test(l))
      .slice(0, 10)
      .forEach(l => console.log(`          ${l.trim()}`));
  }
}

console.log(
  `\n${suites.length - failed} of ${suites.length} suites passed.` +
    (failed ? ` ${failed} FAILED.` : "")
);
process.exit(failed ? 1 : 0);
