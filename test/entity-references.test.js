import { describe, expect, it } from "vitest";
import { findEntityReferences } from "../lib/entity-references.js";

const CONFIG_DIR = "/config";

/**
 * Minimal in-memory filesystem: a flat map of config-relative paths to file
 * contents. Directories are derived, so a test only declares the files it
 * cares about and the walk still behaves like a real directory tree.
 */
function fakeFs(files) {
  const tree = new Map();
  const dirOf = (path) => path.replace(/\/+$/, "").slice(0, path.replace(/\/+$/, "").lastIndexOf("/"));
  const nameOf = (path) => path.replace(/\/+$/, "").slice(path.replace(/\/+$/, "").lastIndexOf("/") + 1);
  const ensure = (dir) => {
    if (!tree.has(dir)) tree.set(dir, new Map());
    return tree.get(dir);
  };
  const link = (dir, name) => {
    ensure(dir);
    if (!ensure(dir).has(name)) ensure(dir).set(name, { isDir: true, content: null, size: 0 });
  };

  ensure(CONFIG_DIR);
  for (const [relativePath, content] of Object.entries(files)) {
    const parts = `${CONFIG_DIR}/${relativePath}`.slice(`${CONFIG_DIR}/`.length).split("/");
    let dir = CONFIG_DIR;
    for (const part of parts.slice(0, -1)) {
      link(dir, part);
      dir = `${dir}/${part}`;
    }
    ensure(dir).set(parts.at(-1), { isDir: false, content, size: content.length });
  }

  return {
    readdirSync(path) {
      const entries = tree.get(path.replace(/\/+$/, ""));
      if (!entries) throw new Error(`ENOENT: ${path}`);
      return [...entries].map(([name, meta]) => ({
        name,
        isDirectory: () => meta.isDir,
        isFile: () => !meta.isDir,
      }));
    },
    lstatSync(path) {
      const meta = tree.get(dirOf(path))?.get(nameOf(path));
      if (!meta) throw new Error(`ENOENT: ${path}`);
      return { isFile: () => !meta.isDir, isDirectory: () => meta.isDir, size: meta.size };
    },
    readFileSync(path) {
      const meta = tree.get(dirOf(path))?.get(nameOf(path));
      if (!meta || meta.isDir) throw new Error(`ENOENT: ${path}`);
      return meta.content;
    },
  };
}

const scan = (files, entityId = "lock.front_door") =>
  findEntityReferences(entityId, { fs: fakeFs(files), configDir: CONFIG_DIR });

describe("entity reference scanning", () => {
  it("reports the files and lines that reference an entity ID", () => {
    const result = scan({
      "automations.yaml": "id: '1'\ntrigger:\n  - platform: state\n    entity_id: lock.front_door\n",
      "dashboards/workshop.yaml": "cards:\n  - entity: lock.front_door\n  - entity: lock.front_door\n",
      "configuration.yaml": "homeassistant:\n  name: Home\n",
    });

    expect(result.scanned).toBe(true);
    expect(result.file_count).toBe(2);
    expect(result.total_matches).toBe(3);
    expect(result.files).toEqual([
      { path: "automations.yaml", count: 1, lines: [4] },
      { path: "dashboards/workshop.yaml", count: 2, lines: [2, 3] },
    ]);
    expect(result.truncated).toBe(false);
  });

  it("does not match a different domain or a longer object ID", () => {
    const result = scan(
      {
        "sensors.yaml": [
          "binary_sensor.front_door:",
          "  friendly_name: Front",
          "lock.front_door_extra:",
          "  friendly_name: Extra",
        ].join("\n"),
      },
      "sensor.front_door",
    );

    // `sensor.front_door` is a substring of `binary_sensor.front_door`, and
    // `lock.front_door_extra` starts with `lock.front_door`. Neither is a
    // reference to the entity being renamed.
    expect(result.file_count).toBe(0);
    expect(result.total_matches).toBe(0);
  });

  it("never reads secrets or descends into internal and managed directories", () => {
    const result = scan({
      "secrets.yaml": "lock: !secret lock_front_door\n",
      ".storage/core.entity_registry": '{"entity_id": "lock.front_door"}',
      "deps/frontend/lock.front_door.yaml": "entity_id: lock.front_door\n",
      "node_modules/pkg/notes.yaml": "entity_id: lock.front_door\n",
      "custom_components/battery_notes/manifest.json": '{"ref": "lock.front_door"}',
      "packages/locks.yaml": "entity_id: lock.front_door\n",
    });

    expect(result.files.map((file) => file.path)).toEqual(["packages/locks.yaml"]);
  });

  it("skips files that are not readable configuration", () => {
    const result = scan({
      "notes.md": "lock.front_door\n",
      "archive.yaml.bak": "entity_id: lock.front_door\n",
      "scripts.yaml": "sequence:\n  - entity_id: lock.front_door\n",
    });

    expect(result.files.map((file) => file.path)).toEqual(["scripts.yaml"]);
  });

  it("caps the reported file list and flags truncation", () => {
    const files = Object.fromEntries(
      Array.from({ length: 30 }, (_, index) => [`file${index}.yaml`, "entity_id: lock.front_door\n"]),
    );

    const result = scan(files);

    expect(result.files).toHaveLength(25);
    expect(result.truncated).toBe(true);
    expect(result.file_count).toBe(25);
    expect(result.total_matches).toBe(30);
  });

  it("caps reported line numbers without undercounting matches", () => {
    const result = scan({
      "many.yaml": Array.from({ length: 9 }, () => "  - entity_id: lock.front_door").join("\n"),
    });

    expect(result.files[0].lines).toHaveLength(5);
    expect(result.total_matches).toBe(9);
  });

  it("reports an unreadable configuration directory instead of throwing", () => {
    const result = findEntityReferences("lock.front_door", {
      fs: {
        readdirSync() {
          throw new Error("EACCES");
        },
        readFileSync() {
          throw new Error("EACCES");
        },
        lstatSync() {
          throw new Error("EACCES");
        },
      },
      configDir: CONFIG_DIR,
    });

    expect(result).toMatchObject({ scanned: false, file_count: 0, total_matches: 0, files: [] });
  });

  it("reports no filesystem access instead of throwing", () => {
    expect(findEntityReferences("lock.front_door", {})).toMatchObject({ scanned: false });
    expect(findEntityReferences("", { fs: fakeFs({}) })).toMatchObject({ scanned: false });
  });
});
