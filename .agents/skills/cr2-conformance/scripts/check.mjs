#!/usr/bin/env node
// cr2 conformance check (PK-17). Run from anywhere:
//   node .agents/skills/cr2-conformance/scripts/check.mjs
// Uses Node built-ins only (PK-3): no npm package, nothing from cr2's lib/, no network, no model.
// It checks the files that changed in the working tree, prints one line per failure and exits 1,
// or prints a one-line summary and exits 0. Missing required sections are warnings only (PK-17b).

import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { execFileSync } from "node:child_process";
import process from "node:process";

// The skill sits at <root>/.agents/skills/cr2-conformance/scripts/check.mjs.
const SCRIPT_DIR = import.meta.dirname ?? dirname(decodeURIComponent(new URL(import.meta.url).pathname));
const SKILL_DIR = resolve(SCRIPT_DIR, "..");
const ROOT = resolve(SKILL_DIR, "..", "..", "..");

const AUTHORITIES = ["descriptive", "prescriptive", "historical"];
const STATUSES = ["active", "draft", "done", "superseded"];
const CANONICAL_TYPES = [
  "readme", "changelog", "agents", "brief", "glossary", "conventions", "constraints", "schema", "arch",
  "infrastructure", "context", "decision", "spec", "plan", "remediation", "publication",
];
const CANONICAL_PANELS = [
  { id: "core", kind: "fixed", dir: "docs/context", slots: [] },
  { id: "decisions", kind: "collection", dir: "docs/decisions", label: "ADR", type: "decision", numbered: true, suffix: "ADR" },
  { id: "specs", kind: "collection", dir: "docs/specs", type: "spec", suffix: "SPEC", lifecycle: true },
  { id: "remediations", kind: "collection", dir: "docs/remediations", label: "REM", type: "remediation", numbered: true, suffix: "REMEDIATION", lifecycle: true },
  { id: "plans", kind: "collection", dir: "docs/plans", type: "plan", suffix: "PLAN", lifecycle: true },
  { id: "publish", kind: "collection", dir: "docs/publish", type: "publication", suffix: "PUB", lifecycle: false },
];
const ROOT_DOCUMENTS = ["README.md", "CHANGELOG.md"];
const NOT_DOCUMENTS = ["docs/attachments/", "docs/images/"]; // RL-6
const WALK_SKIP = new Set([".git", "node_modules", ".next"]);

const failures = [];
const warnings = [];
const skipped = [];

const fail = (file, rule, what) => failures.push(`${file}: ${rule} ${what}`);
const warn = (file, what) => warnings.push(`warning: ${file}: ${what}`);

// ---------- git ----------

function git(args, { allowFail = false } = {}) {
  try {
    return execFileSync("git", args, { cwd: ROOT, encoding: "utf8", stdio: ["ignore", "pipe", "ignore"], maxBuffer: 64 * 1024 * 1024 });
  } catch (err) {
    if (allowFail) return null;
    throw err;
  }
}

const zsplit = (out) => (out ?? "").split("\0").filter(Boolean);

const hasGit = git(["rev-parse", "--is-inside-work-tree"], { allowFail: true })?.trim() === "true";
const hasHead = hasGit && git(["rev-parse", "--verify", "--quiet", "HEAD"], { allowFail: true }) !== null;

// Paths that exist at HEAD, relative to ROOT (ls-tree lists the current directory's subtree).
const atHead = new Set(hasHead ? zsplit(git(["ls-tree", "-r", "-z", "--name-only", "HEAD"], { allowFail: true })) : []);

function headText(path) {
  return git(["show", `HEAD:./${path}`], { allowFail: true });
}

// ---------- the tree ----------

function walk(dir, out = []) {
  let entries;
  try { entries = readdirSync(join(ROOT, dir), { withFileTypes: true }); } catch { return out; }
  for (const e of entries) {
    if (WALK_SKIP.has(e.name)) continue;
    const rel = dir ? `${dir}/${e.name}` : e.name;
    if (e.isDirectory()) walk(rel, out);
    else if (e.isFile()) out.push(rel);
  }
  return out;
}

function treeFiles() {
  if (hasGit) {
    const listed = zsplit(git(["ls-files", "-z", "--cached", "--others", "--exclude-standard"]));
    const deleted = new Set(zsplit(git(["ls-files", "-z", "--deleted"])));
    return listed.filter((p) => !deleted.has(p));
  }
  return walk("");
}

const isFile = (path) => { try { return statSync(join(ROOT, path)).isFile(); } catch { return false; } };
const read = (path) => readFileSync(join(ROOT, path), "utf8");

// ---------- configuration ----------

function loadConfig() {
  const path = join(ROOT, ".cr2", "config.json");
  if (!existsSync(path)) return { panels: CANONICAL_PANELS, types: null, fromFile: false };
  let config;
  try { config = JSON.parse(readFileSync(path, "utf8")); } catch (err) {
    fail(".cr2/config.json", "PK-17", `cannot be read as JSON: ${err.message}`);
    return { panels: CANONICAL_PANELS, types: null, fromFile: false };
  }
  const panels = Array.isArray(config.panels) && config.panels.length ? config.panels : CANONICAL_PANELS;
  const types = config.types && typeof config.types === "object" ? config.types : null;
  return { panels, types, fromFile: true };
}

const config = loadConfig();
const collections = config.panels.filter((p) => p && p.kind === "collection" && typeof p.dir === "string")
  .map((p) => ({ ...p, dir: p.dir.replace(/^\.\/+/, "").replace(/\/+$/, "") }));
const slotPaths = new Set(config.panels.flatMap((p) => (Array.isArray(p?.slots) ? p.slots : []).map((s) => s?.path)).filter(Boolean));
const decisionDirs = collections.filter((p) => p.type === "decision").map((p) => p.dir);
if (!decisionDirs.length) decisionDirs.push("docs/decisions");
const configuredTypes = config.types ? Object.keys(config.types) : CANONICAL_TYPES;

// ---------- frontmatter ----------

function unquote(value) {
  const v = value.trim();
  if ((v.startsWith('"') && v.endsWith('"')) || (v.startsWith("'") && v.endsWith("'"))) return v.slice(1, -1);
  return v.replace(/\s+#.*$/, "");
}

// Reads the flat keys the checks need. Scalars, block lists and flow lists of scalars; anything nested
// (a provenance entry, the steps map) is kept only as "present".
function frontmatter(text) {
  const src = text.replace(/^﻿/, "").replace(/\r\n/g, "\n");
  if (!src.startsWith("---\n")) return null;
  const end = src.indexOf("\n---", 3);
  if (end < 0) return null;
  const lines = src.slice(4, end).split("\n");
  const body = src.slice(src.indexOf("\n", end + 1) + 1);
  const fields = {};
  let key = null;
  for (const line of lines) {
    if (!line.trim() || line.trimStart().startsWith("#")) continue;
    const top = /^([A-Za-z0-9_-]+):(?:\s+(.*))?$/.exec(line);
    if (top) {
      key = top[1];
      const value = top[2]?.trim() ?? "";
      if (!value) fields[key] = [];
      else if (value.startsWith("[") && value.endsWith("]")) fields[key] = value.slice(1, -1).split(",").map(unquote).filter(Boolean);
      else fields[key] = unquote(value);
      continue;
    }
    const item = /^\s+-\s+(.*)$/.exec(line);
    if (item && key && Array.isArray(fields[key])) {
      const value = item[1].trim();
      fields[key].push(/^[A-Za-z0-9_-]+:\s/.test(value) ? { entry: value } : unquote(value));
    }
  }
  return { fields, body };
}

// ---------- which files changed ----------

function changedFiles() {
  if (!hasGit) {
    skipped.push("checks 3 and 4 need git");
    const docs = walk("docs").filter((p) => p.endsWith(".md"));
    const extra = [...ROOT_DOCUMENTS, "AGENTS.md", ...slotPaths].filter(isFile);
    return [...new Set([...docs, ...extra])];
  }
  const set = new Set();
  if (hasHead) {
    for (const p of zsplit(git(["diff", "--name-only", "--no-renames", "--relative", "-z", "HEAD"]))) set.add(p);
  } else {
    skipped.push("check 4 needs a commit");
    for (const p of zsplit(git(["ls-files", "-z", "--cached"]))) set.add(p);
  }
  for (const p of zsplit(git(["diff", "--name-only", "--no-renames", "--relative", "-z", "--cached"], { allowFail: true }))) set.add(p);
  for (const p of zsplit(git(["ls-files", "-z", "--others", "--exclude-standard"]))) set.add(p);
  return [...set];
}

const changed = changedFiles();

// ---------- what a path is ----------

const under = (path, dir) => path === dir || path.startsWith(`${dir}/`);
const notDocument = (path) => NOT_DOCUMENTS.some((d) => path.startsWith(d));
const collectionOf = (path) => collections.find((p) => under(path, p.dir));
const isPublish = (panel) => panel.id === "publish" || String(panel.type ?? "").startsWith("publication");

function isManagedMarkdown(path) {
  if (!path.endsWith(".md") || notDocument(path)) return false;
  if (ROOT_DOCUMENTS.includes(path)) return true;
  if (path.startsWith("docs/")) return true;
  return slotPaths.has(path) || Boolean(collectionOf(path));
}

// ---------- check 1: frontmatter (PK-17.1) and the section warnings (PK-17b) ----------

const documents = []; // { path, fields, body, type }

for (const path of changed) {
  if (!isFile(path) || !isManagedMarkdown(path) || path === "AGENTS.md" || path === "CLAUDE.md") continue;
  const fm = frontmatter(read(path));
  if (!fm) { fail(path, "PK-17.1", "has no frontmatter"); continue; }
  const f = fm.fields;
  for (const field of ["type", "title", "authority", "status"]) {
    if (typeof f[field] !== "string" || !f[field].trim()) fail(path, "PK-17.1", `missing "${field}"`);
  }
  if (typeof f.authority === "string" && f.authority && !AUTHORITIES.includes(f.authority)) fail(path, "PK-17.1", `authority "${f.authority}" is not allowed`);
  if (typeof f.status === "string" && f.status && !STATUSES.includes(f.status)) fail(path, "PK-17.1", `status "${f.status}" is not allowed`);
  if (typeof f.type === "string" && f.type && !configuredTypes.includes(f.type) && !/^publication:[a-z0-9-]+$/.test(f.type)) {
    fail(path, "PK-17.1", `type "${f.type}" is not a configured type`);
  }
  documents.push({ path, fields: f, body: fm.body, type: typeof f.type === "string" ? f.type : null });
}

if (changed.includes("AGENTS.md") && isFile("AGENTS.md")) {
  documents.push({ path: "AGENTS.md", fields: {}, body: read("AGENTS.md"), type: "agents" });
}

const normalize = (s) => s.toLowerCase().replace(/[`*_[\]]/g, "").replace(/^\d+(\.\d+)*\.?\s+/, "").replace(/[\s:.,;]+$/, "").replace(/\s+/g, " ").trim();

function requiredSections(type) {
  const own = config.types?.[type]?.sections;
  if (Array.isArray(own)) return own;
  const base = type.includes(":") ? config.types?.[type.split(":")[0]]?.sections : null;
  if (Array.isArray(base)) return base;
  return null; // references/<type>.md are prose guides with no section list
}

const sectionsSkipped = new Set();
for (const doc of documents) {
  if (!doc.type) continue;
  const sections = requiredSections(doc.type);
  if (!sections) { sectionsSkipped.add(doc.type); continue; }
  const headings = [...doc.body.matchAll(/^##\s+(.+?)\s*#*\s*$/gm)].map((m) => normalize(m[1]));
  for (const section of sections) {
    const want = normalize(section);
    if (/summary under the title/.test(want)) {
      const intro = doc.body.split(/^##\s/m)[0].replace(/^#\s.*$/m, "").replace(/<!--[\s\S]*?-->/g, "").trim();
      if (!intro) warn(doc.path, `missing section "${section}"`);
      continue;
    }
    if (!headings.some((h) => h === want || h.startsWith(want) || want.startsWith(h))) warn(doc.path, `missing section "${section}"`);
  }
}
if (sectionsSkipped.size) skipped.push(`section warnings for ${[...sectionsSkipped].join(", ")} (no sections configured)`);

// ---------- check 2: names per collection (PK-17.2) ----------

const escape = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

for (const path of changed) {
  if (!isFile(path) || notDocument(path)) continue;
  const panel = collectionOf(path);
  if (!panel) continue;
  const publish = isPublish(panel);
  if (!path.endsWith(".md") && !(publish && path.endsWith(".html"))) continue;
  const parts = path.slice(panel.dir.length + 1).split("/");
  const name = parts.pop();
  const isRem = panel.label === "REM" || panel.id === "remediations";
  const folders = panel.lifecycle ? ["active", "archive", ...(isRem ? ["drafts"] : [])] : [];
  if (parts.length > 1 || (parts.length === 1 && !folders.includes(parts[0]))) {
    const allowed = folders.length ? `${panel.dir}/ or ${folders.map((f) => `${panel.dir}/${f}/`).join(", ")}` : `${panel.dir}/ (flat)`;
    fail(path, "PK-17.2", `is not in an allowed folder: use ${allowed}`);
    continue;
  }
  const number = panel.numbered ? (isRem ? "REM-\\d{4,}-" : "\\d{4,}-") : "";
  const suffix = panel.suffix ? `-${escape(panel.suffix)}` : "";
  const ext = publish ? "\\.(md|html)" : "\\.md";
  const pattern = new RegExp(`^${number}[a-z0-9]+(?:-[a-z0-9]+)*${suffix}${ext}$`);
  if (!pattern.test(name)) {
    const shown = `${panel.numbered ? (isRem ? "REM-NNNN-" : "NNNN-") : ""}<slug>${panel.suffix ? `-${panel.suffix}` : ""}.md`;
    fail(path, "PK-17.2", `name does not match ${shown}`);
  }
}

// ---------- check 3: run records (PK-17.3) ----------

const RECORD = /^(\d{4,})-(.+)\.(prompt|response)\.md$/;

if (hasGit) {
  const folders = new Map(); // folder -> names on disk
  const namesIn = (folder) => {
    if (!folders.has(folder)) {
      let names = [];
      try { names = readdirSync(join(ROOT, folder)); } catch { /* folder removed */ }
      folders.set(folder, names);
    }
    return folders.get(folder);
  };
  for (const path of changed) {
    const parts = path.split("/");
    if (parts.length !== 3 || parts[0] !== "prompts" || parts[1] === "builtin") continue;
    const m = RECORD.exec(parts[2]);
    if (!m) continue;
    const [, number, slug, kind] = m;
    const folder = `prompts/${parts[1]}`;
    if (atHead.has(path)) {
      fail(path, "PK-17.3", isFile(path) ? "run record changed after it was committed" : "run record deleted after it was committed");
      continue;
    }
    if (!isFile(path)) continue;
    const stem = `${number}-${slug}`;
    const names = namesIn(folder);
    const other = names.find((n) => n.startsWith(`${number}-`) && !n.startsWith(`${stem}.`));
    if (other) fail(path, "PK-17.3", `number ${number} is already used by ${folder}/${other}`);
    if (kind === "response") {
      if (!names.includes(`${stem}.prompt.md`)) fail(path, "PK-17.3", `response has no ${stem}.prompt.md beside it`);
      continue;
    }
    if (names.includes(`${stem}.response.md`)) continue;
    const fm = frontmatter(read(path))?.fields ?? {};
    // A saved prompt has no response by design, and a run cr2 has open has none yet (RL-9a).
    if (typeof fm.saved === "string" && fm.saved) continue;
    if (typeof fm.run === "string" && fm.outcome === "running") continue;
    fail(path, "PK-17.3", `run record has no ${stem}.response.md beside it`);
  }
}

// ---------- check 4: historical documents (PK-17.4) ----------

function changelogSections(text) {
  const fm = frontmatter(text);
  const body = (fm ? fm.body : text).replace(/\r\n/g, "\n");
  const out = new Map();
  const chunks = body.split(/^(?=## )/m).filter((c) => c.startsWith("## "));
  for (const chunk of chunks) {
    const heading = chunk.slice(0, chunk.indexOf("\n") < 0 ? chunk.length : chunk.indexOf("\n")).trim();
    if (/^##\s+\[?unreleased\]?/i.test(heading)) continue;
    out.set(heading, chunk.replace(/\s+$/, ""));
  }
  return out;
}

if (hasHead) {
  for (const path of changed) {
    if (!decisionDirs.some((d) => under(path, d)) || !atHead.has(path)) continue;
    fail(path, "PK-17.4", isFile(path)
      ? "decision record changed after it was committed: supersede it with a new, higher-numbered one"
      : "decision record deleted after it was committed: supersede it with a new, higher-numbered one");
  }
  if (changed.includes("CHANGELOG.md") && atHead.has("CHANGELOG.md")) {
    const before = changelogSections(headText("CHANGELOG.md") ?? "");
    const now = isFile("CHANGELOG.md") ? changelogSections(read("CHANGELOG.md")) : new Map();
    for (const [heading, text] of before) {
      if (!now.has(heading)) fail("CHANGELOG.md", "PK-17.4", `released section "${heading}" was removed or renamed`);
      else if (now.get(heading) !== text) fail("CHANGELOG.md", "PK-17.4", `released section "${heading}" differs from HEAD: new work goes under ## Unreleased`);
    }
  }
}

// ---------- check 5: describes globs (PK-17.5) ----------

function globToRegExp(glob) {
  let re = "";
  for (let i = 0; i < glob.length; i++) {
    const c = glob[i];
    if (c === "*" && glob[i + 1] === "*") {
      i++;
      if (glob[i + 1] === "/") { i++; re += "(?:.*/)?"; } else re += ".*";
    } else if (c === "*") re += "[^/]*";
    else if (c === "?") re += "[^/]";
    else if (c === "{") {
      const close = glob.indexOf("}", i);
      if (close < 0) { re += "\\{"; continue; }
      re += `(?:${glob.slice(i + 1, close).split(",").map(escape).join("|")})`;
      i = close;
    } else re += escape(c);
  }
  return new RegExp(`^${re}$`);
}

const withDescribes = documents.filter((d) => Array.isArray(d.fields.describes) && d.fields.describes.length);
if (withDescribes.length) {
  const files = treeFiles();
  const paths = new Set(files);
  for (const f of files) {
    const parts = f.split("/");
    for (let i = 1; i < parts.length; i++) paths.add(parts.slice(0, i).join("/"));
  }
  const all = [...paths];
  for (const doc of withDescribes) {
    for (const entry of doc.fields.describes) {
      if (typeof entry !== "string" || !entry.trim()) continue;
      const glob = entry.trim().replace(/^\.\/+/, "").replace(/\/+$/, "");
      const re = globToRegExp(glob);
      if (!all.some((p) => re.test(p))) fail(doc.path, "PK-17.5", `describes "${entry}" matches no file`);
    }
  }
}

// ---------- report ----------

for (const line of warnings) console.log(line);
for (const line of failures) console.log(line);
const skippedText = skipped.length ? skipped.join("; ") : "none";
if (failures.length) {
  if (skipped.length) console.log(`skipped: ${skippedText}`);
  process.exit(1);
}
console.log(`ok: ${changed.length} changed files checked, ${warnings.length} warnings, skipped: ${skippedText}`);
process.exit(0);
