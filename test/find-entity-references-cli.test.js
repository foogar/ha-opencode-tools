import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { execFile } from "node:child_process";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";

const run = promisify(execFile);
const SCRIPT = join(dirname(fileURLToPath(import.meta.url)), "..", "scripts", "find-entity-references.mjs");

/** Run the CLI and capture stdout, stderr and exit code without throwing. */
async function cli(args) {
  try {
    const { stdout, stderr } = await run(process.execPath, [SCRIPT, ...args], { cwd: join(dirname(SCRIPT), "..") });
    return { code: 0, stdout, stderr };
  } catch (error) {
    return { code: error.code ?? 1, stdout: error.stdout ?? "", stderr: error.stderr ?? "" };
  }
}

let configDir;

beforeAll(() => {
  configDir = mkdtempSync(join(tmpdir(), "fer-"));
  writeFileSync(
    join(configDir, "automations.yaml"),
    [
      "alias: Front door left open",
      "triggers:",
      "  - trigger: state",
      "    entity_id: binary_sensor.front_door_door_sensor",
      "  - trigger: state",
      "    entity_id: binary_sensor.front_door_door_sensor",
      "actions: []",
    ].join("\n"),
  );
  mkdirSync(join(configDir, "dashboards"));
  writeFileSync(
    join(configDir, "dashboards", "workshop.yaml"),
    ["cards:", "  - entity: lock.front_door", "  - entity: binary_sensor.front_door_door_sensor", "  - entity: lock.front_door_extra"].join("\n"),
  );
  // Must be ignored: a secret file and an internal directory.
  writeFileSync(join(configDir, "secrets.yaml"), "token: lock.front_door\n");
  mkdirSync(join(configDir, ".storage"));
  writeFileSync(join(configDir, ".storage", "core.restore_state"), '{"entity_id": "lock.front_door"}');
});

afterAll(() => {
  rmSync(configDir, { recursive: true, force: true });
});

describe("find-entity-references CLI", () => {
  it("reports files and line numbers for a referenced entity", async () => {
    const { code, stdout } = await cli(["--config-dir", configDir, "lock.front_door"]);

    expect(code).toBe(0);
    expect(stdout).toContain("lock.front_door");
    expect(stdout).toContain("dashboards/workshop.yaml:2");
    // `lock.front_door_extra` is a different entity and must not be counted.
    expect(stdout).not.toContain("workshop.yaml:4");
  });

  it("counts every occurrence across files", async () => {
    const { code, stdout } = await cli([
      "--config-dir",
      configDir,
      "binary_sensor.front_door_door_sensor",
    ]);

    expect(code).toBe(0);
    expect(stdout).toContain("3 references in 2 files");
    expect(stdout).toContain("automations.yaml:4, 6");
    expect(stdout).toContain("dashboards/workshop.yaml:3");
  });

  it("reports an unreferenced entity without treating it as a failure", async () => {
    const { code, stdout } = await cli(["--config-dir", configDir, "sensor.nothing_here"]);

    expect(code).toBe(0);
    expect(stdout).toContain("no references");
    expect(stdout).toContain("0 of 1 entity ID");
  });

  it("never reads secrets.yaml or internal directories", async () => {
    const { stdout } = await cli(["--config-dir", configDir, "lock.front_door"]);
    expect(stdout).not.toContain("secrets.yaml");
    expect(stdout).not.toContain(".storage");
  });

  it("emits machine-readable JSON with a stable shape", async () => {
    const { code, stdout } = await cli(["--config-dir", configDir, "--format", "json", "lock.front_door"]);

    expect(code).toBe(0);
    const report = JSON.parse(stdout);
    expect(report).toMatchObject({ scanned: true, entity_ids: 1, with_references: 1, total_matches: 1, affected_files: 1 });
    expect(report.results[0].files[0]).toMatchObject({ path: "dashboards/workshop.yaml", count: 1, lines: [2] });
  });

  it("summarises a mixed set of referenced and unreferenced IDs", async () => {
    const { code, stdout } = await cli([
      "--config-dir",
      configDir,
      "lock.front_door",
      "sensor.nothing_here",
      "binary_sensor.front_door_door_sensor",
    ]);

    expect(code).toBe(0);
    expect(stdout).toContain("2 of 3 entity IDs referenced, across 2 files");
  });

  it("exits 1 for an unreadable configuration directory", async () => {
    const { code, stderr } = await cli(["--config-dir", "/definitely/not/here", "lock.front_door"]);

    expect(code).toBe(1);
    expect(stderr).toContain("cannot read configuration directory");
  });

  it("exits 2 on bad arguments", async () => {
    expect((await cli(["--nope", "lock.front_door"])).code).toBe(2);
    expect((await cli([])).code).toBe(2);
    expect((await cli(["--format", "yaml", "lock.front_door"])).code).toBe(2);
  });

  it("prints usage for --help without scanning", async () => {
    const { code, stdout } = await cli(["--help"]);

    expect(code).toBe(0);
    expect(stdout).toContain("Usage: find-entity-references.mjs");
  });
});
