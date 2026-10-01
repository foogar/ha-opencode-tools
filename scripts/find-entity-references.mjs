#!/usr/bin/env node
/**
 * Report every configuration file that references given Home Assistant entity IDs.
 *
 * This is the ad-hoc form of `lib/entity-references.js` — the same tested
 * scanner, wrapped in a CLI. It exists because "what breaks if I remove or
 * rename this?" is a question that comes up constantly, and answering it by
 * hand means grepping the config and remembering which entity IDs belong to
 * which device.
 *
 * It is read-only. It never writes, never calls Home Assistant, and needs no
 * credentials: you pass entity IDs, it reports where they are referenced.
 *
 * Usage:
 *   node scripts/find-entity-references.mjs lock.front_door
 *   node scripts/find-entity-references.mjs --format json binary_sensor.front_door_door_sensor
 *   node scripts/find-entity-references.mjs --config-dir /homeassistant lock.front_door sensor.front_door_battery
 *
 * Exits 0 when the scan ran, 1 when the configuration directory is unreadable,
 * 2 on bad arguments. A missing reference is a normal result, not a failure.
 */

import { readdirSync, readFileSync, lstatSync, statSync } from "node:fs";
import { findEntityReferences } from "../lib/entity-references.js";

const DEFAULT_CONFIG_DIR = "/homeassistant";
const fs = { readdirSync, readFileSync, lstatSync };

function parseArgs(argv) {
  const options = { configDir: DEFAULT_CONFIG_DIR, format: "text", entityIds: [] };

  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === "--config-dir") {
      options.configDir = argv[++index];
      if (!options.configDir) throw new Error("--config-dir needs a path");
    } else if (arg === "--format") {
      options.format = argv[++index];
      if (!["text", "json"].includes(options.format)) {
        throw new Error("--format must be 'text' or 'json'");
      }
    } else if (arg === "--help" || arg === "-h") {
      options.help = true;
    } else if (arg.startsWith("--")) {
      throw new Error(`Unknown option: ${arg}`);
    } else {
      options.entityIds.push(arg);
    }
  }

  if (!options.help && options.entityIds.length === 0) {
    throw new Error("Pass at least one entity ID, or --help");
  }
  return options;
}

const USAGE = `Usage: find-entity-references.mjs [options] <entity_id>...

Reports which configuration files reference each entity ID, with line numbers.

Options:
  --config-dir <path>   Configuration directory to scan (default: ${DEFAULT_CONFIG_DIR})
  --format <text|json>  Output format (default: text)
  -h, --help            Show this help

Exits 0 when the scan ran. A missing reference is a result, not a failure.`;

/** Group the per-ID results so the summary can be computed without a second walk. */
function scanAll(entityIds, configDir) {
  const started = process.hrtime.bigint();
  const results = entityIds.map((entityId) => {
    const found = findEntityReferences(entityId, { fs, configDir });
    return { entity_id: entityId, ...found };
  });
  const elapsedMs = Number(process.hrtime.bigint() - started) / 1e6;

  const referenced = results.filter((result) => result.total_matches > 0);
  const files = new Set();
  for (const result of referenced) {
    for (const file of result.files) files.add(file.path);
  }

  return {
    config_dir: configDir,
    scanned: results.every((result) => result.scanned),
    entity_ids: results.length,
    with_references: referenced.length,
    total_matches: referenced.reduce((sum, result) => sum + result.total_matches, 0),
    affected_files: files.size,
    elapsed_ms: Number(elapsedMs.toFixed(1)),
    results,
  };
}

function renderText(report) {
  const lines = [];

  for (const result of report.results) {
    if (!result.scanned) {
      lines.push(`${result.entity_id}`);
      lines.push(`  NOT SCANNED — ${result.reason}`);
      lines.push("");
      continue;
    }
    if (result.total_matches === 0) {
      lines.push(`${result.entity_id}`);
      lines.push("  no references");
      lines.push("");
      continue;
    }
    const plural = result.total_matches === 1 ? "reference" : "references";
    lines.push(`${result.entity_id}`);
    lines.push(`  ${result.total_matches} ${plural} in ${result.file_count} file${result.file_count === 1 ? "" : "s"}`);
    for (const file of result.files) {
      const shown = file.lines.slice(0, 8).join(", ");
      const more = file.lines.length > 8 ? `, +${file.lines.length - 8} more` : "";
      lines.push(`    ${file.path}:${shown}${more}`);
    }
    if (result.truncated) lines.push("    (results truncated — raise the caps in lib/entity-references.js if needed)");
    lines.push("");
  }

  lines.push("---");
  if (!report.scanned) {
    lines.push(`Scan failed for ${report.config_dir} — is that the right path?`);
  } else {
    lines.push(
      `${report.with_references} of ${report.entity_ids} entity ID${report.entity_ids === 1 ? "" : "s"} referenced, ` +
        `across ${report.affected_files} file${report.affected_files === 1 ? "" : "s"} ` +
        `(${report.total_matches} total, ${report.elapsed_ms} ms)`,
    );
    if (report.with_references > 0) {
      lines.push("These are the only places that would break. Everything else is untouched.");
    }
  }

  return lines.join("\n");
}

function main() {
  let options;
  try {
    options = parseArgs(process.argv.slice(2));
  } catch (error) {
    process.stderr.write(`error: ${error.message}\n\n${USAGE}\n`);
    process.exit(2);
  }

  if (options.help) {
    process.stdout.write(`${USAGE}\n`);
    return;
  }

  try {
    statSync(options.configDir);
  } catch {
    process.stderr.write(`error: cannot read configuration directory: ${options.configDir}\n`);
    process.exit(1);
  }

  const report = scanAll(options.entityIds, options.configDir);
  process.stdout.write(options.format === "json" ? `${JSON.stringify(report, null, 2)}\n` : `${renderText(report)}\n`);
  process.exit(report.scanned ? 0 : 1);
}

main();
