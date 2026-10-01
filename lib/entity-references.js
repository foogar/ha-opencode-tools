/**
 * Bounded, read-only search for configuration references to an entity ID.
 *
 * A registry-only rename changes the entity's ID and nothing else, so every
 * automation, script, scene, and dashboard that names the old ID is left
 * pointing at something that no longer exists. Telling the caller *which files*
 * name it is the difference between a rename that is planned and one that is
 * discovered to be broken three days later.
 *
 * This is advisory: it reports what it found, it never edits anything, and it
 * is not allowed to fail the operation it annotates. A configuration directory
 * that cannot be read is a missing convenience, not a reason to refuse a
 * rename the user already approved.
 *
 * Filesystem access is injected so the scanner is testable without a real
 * Home Assistant configuration directory.
 */

/**
 * Where Home Assistant mounts the configuration directory inside the add-on.
 *
 * Declared here rather than imported. Inside the MCP server this module borrowed
 * the constant from `home-facts.js`; standalone, that would mean vendoring
 * several hundred lines of an unrelated scanner to obtain one string.
 */
export const DEFAULT_CONFIG_DIR = "/homeassistant";

/** Pathological-file guard: no configuration file needs more than this. */
const MAX_SCAN_BYTES = 8 * 1024 * 1024;

/** Deep enough for dashboards/, packages/, and esphome/; shallow enough to stay cheap. */
const MAX_DEPTH = 4;

/** Caps that keep the tool result readable no matter how large the config is. */
const MAX_FILES_SCANNED = 750;
const MAX_REPORTED_FILES = 25;
const MAX_REPORTED_LINES = 5;

/** Formats an entity ID can legitimately appear in inside YAML/JSON configuration. */
const SCANNED_EXTENSIONS = new Set([".yaml", ".yml", ".json"]);

/**
 * Never read: credential files, Home Assistant internals, dependency trees, and
 * HACS-managed code the user has explicitly asked not to be touched.
 */
const EXCLUDED_DIRECTORIES = new Set([
  ".storage",
  ".cloud",
  ".git",
  "deps",
  "tts",
  "node_modules",
  "__pycache__",
  "custom_components",
  "www",
]);

/** `secrets.yaml` is excluded on principle — matching counts never justify reading it. */
const EXCLUDED_FILES = new Set(["secrets.yaml"]);

function escapeRegExp(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/**
 * Match a whole entity ID, not a fragment of one.
 *
 * `sensor.front_door` occurs inside `binary_sensor.front_door`, and
 * `lock.front_door` occurs inside `lock.front_door_extra`. Both would be
 * reported as dangling references for an entity that is not actually
 * referenced, which trains the reader to ignore the list.
 */
function referencePattern(entityId) {
  return new RegExp(`(?<![A-Za-z0-9_.])${escapeRegExp(entityId)}(?![A-Za-z0-9_])`, "g");
}

function safeReaddir(fs, path) {
  try {
    return fs.readdirSync(path, { withFileTypes: true });
  } catch {
    return null;
  }
}

function safeLstat(fs, path) {
  try {
    return fs.lstatSync(path);
  } catch {
    return null;
  }
}

function safeReadFile(fs, path) {
  try {
    return fs.readFileSync(path, "utf8");
  } catch {
    return null;
  }
}

function isScannableFile(name) {
  if (EXCLUDED_FILES.has(name)) return false;
  if (name.startsWith(".")) return false;
  const dot = name.lastIndexOf(".");
  return dot > 0 && SCANNED_EXTENSIONS.has(name.slice(dot).toLowerCase());
}

function isExcludedDirectory(name) {
  return name.startsWith(".") || EXCLUDED_DIRECTORIES.has(name);
}

/** Line numbers where the entity ID appears, capped so one file cannot flood the result. */
function matchingLines(text, pattern) {
  const lines = [];
  let count = 0;
  text.split("\n").forEach((line, index) => {
    pattern.lastIndex = 0;
    if (!pattern.test(line)) return;
    count += line.match(pattern)?.length ?? 0;
    if (lines.length < MAX_REPORTED_LINES) lines.push(index + 1);
  });
  return { count, lines };
}

function unscanned(reason) {
  return { scanned: false, reason, files_scanned: 0, file_count: 0, total_matches: 0, files: [], truncated: false };
}

/**
 * Report the configuration files that name `entityId`.
 *
 * @returns {Promise<object>} always an object; never throws.
 */
export function findEntityReferences(entityId, { fs, configDir = DEFAULT_CONFIG_DIR, pattern } = {}) {
  if (!fs || typeof entityId !== "string" || !entityId) return unscanned("no filesystem access");

  let matcher;
  try {
    matcher = pattern ?? referencePattern(entityId);
  } catch {
    return unscanned("invalid entity ID");
  }

  const rootEntries = safeReaddir(fs, configDir);
  if (!rootEntries) return unscanned("configuration directory is not readable");

  const matches = [];
  let filesScanned = 0;
  let totalMatches = 0;
  let truncated = false;

  const visit = (directory, depth) => {
    if (filesScanned >= MAX_FILES_SCANNED) {
      truncated = true;
      return;
    }
    const entries = safeReaddir(fs, directory);
    if (!entries) return;
    // Deterministic order so the same configuration always reports the same list.
    for (const entry of [...entries].sort((a, b) => a.name.localeCompare(b.name))) {
      const name = entry.name;
      const path = `${directory}/${name}`;

      if (entry.isDirectory?.()) {
        if (depth < MAX_DEPTH && !isExcludedDirectory(name)) visit(path, depth + 1);
        continue;
      }
      if (!entry.isFile?.() || !isScannableFile(name)) continue;

      // lstat, not the dirent: a symlink must not pull a file in from outside
      // the configuration directory, and the size guard needs the real length.
      const stat = safeLstat(fs, path);
      if (!stat?.isFile?.() || stat.size > MAX_SCAN_BYTES) continue;

      filesScanned += 1;
      if (filesScanned > MAX_FILES_SCANNED) {
        truncated = true;
        return;
      }

      const text = safeReadFile(fs, path);
      if (text === null) continue;
      const { count, lines } = matchingLines(text, matcher);
      if (!count) continue;

      totalMatches += count;
      if (matches.length < MAX_REPORTED_FILES) {
        matches.push({ path: path.slice(configDir.length + 1), count, lines });
      } else {
        truncated = true;
      }
    }
  };

  try {
    visit(configDir, 0);
  } catch {
    // A walk failure must not fail the rename; report what was collected.
  }

  return {
    scanned: true,
    files_scanned: filesScanned,
    file_count: matches.length,
    total_matches: totalMatches,
    files: matches,
    truncated,
  };
}
