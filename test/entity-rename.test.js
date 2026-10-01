import { describe, expect, it, vi } from "vitest";
import {
  applyEntityRename,
  entityRenameConfirmation,
  planEntityRename,
} from "../lib/entity-rename.js";
// Note: the tool-profile gating test lives with the MCP server, which owns the
// profiles. This repository has no profiles, so there is nothing to assert here.

function registry(entries = [{ entity_id: "lock.smart_lock_ultra" }]) {
  return entries;
}

function commandFor(entries, updateResult) {
  return vi.fn(async (type, fields = {}) => {
    if (type === "config/entity_registry/list") return registry(entries);
    if (type === "config/entity_registry/update") {
      return updateResult ?? { entity_entry: { ...fields, entity_id: fields.new_entity_id } };
    }
    throw new Error(`Unexpected command: ${type}`);
  });
}

describe("entity rename planning", () => {
  it("returns a guarded plan for a valid rename", async () => {
    const command = commandFor([{ entity_id: "lock.smart_lock_ultra" }]);

    const result = await planEntityRename(
      { entityId: "lock.smart_lock_ultra", newEntityId: "lock.front_door" },
      command,
    );

    expect(result).toMatchObject({
      entity_id: "lock.smart_lock_ultra",
      new_entity_id: "lock.front_door",
      changed: true,
      ready_to_apply: true,
      confirmation_required: "RENAME lock.smart_lock_ultra -> lock.front_door",
    });
    expect(command).toHaveBeenCalledTimes(1);
    expect(command).toHaveBeenCalledWith("config/entity_registry/list");
  });

  it("rejects malformed and cross-domain entity IDs", async () => {
    const command = commandFor();

    await expect(
      planEntityRename({ entityId: "Lock.Bad", newEntityId: "lock.front_door" }, command),
    ).rejects.toThrow("Invalid entity ID");
    await expect(
      planEntityRename(
        { entityId: "lock.smart_lock_ultra", newEntityId: "switch.front_door" },
        command,
      ),
    ).rejects.toThrow("same domain");
  });

  it("rejects missing entities and ID collisions", async () => {
    const command = commandFor([
      { entity_id: "lock.smart_lock_ultra" },
      { entity_id: "lock.front_door" },
    ]);

    await expect(
      planEntityRename({ entityId: "lock.missing", newEntityId: "lock.new" }, command),
    ).rejects.toThrow("was not found");
    await expect(
      planEntityRename(
        { entityId: "lock.smart_lock_ultra", newEntityId: "lock.front_door" },
        command,
      ),
    ).rejects.toThrow("already exists");
  });

  it("treats an unchanged ID as a no-op", async () => {
    const command = commandFor([{ entity_id: "lock.front_door" }]);

    const result = await planEntityRename(
      { entityId: "lock.front_door", newEntityId: "lock.front_door" },
      command,
    );

    expect(result).toMatchObject({ changed: false, no_changes: true, ready_to_apply: false });
    expect(result.confirmation_required).toBeNull();
  });

  it("reports the configuration files that reference the current ID", async () => {
    const command = commandFor([{ entity_id: "lock.smart_lock_ultra" }]);
    const scan = vi.fn(async () => ({
      scanned: true,
      files_scanned: 12,
      file_count: 2,
      total_matches: 3,
      files: [{ path: "automations.yaml", count: 1, lines: [42] }],
      truncated: false,
    }));

    const result = await planEntityRename(
      { entityId: "lock.smart_lock_ultra", newEntityId: "lock.front_door" },
      command,
      scan,
    );

    expect(scan).toHaveBeenCalledWith("lock.smart_lock_ultra");
    expect(result.references_may_dangle).toBe(true);
    expect(result.references).toMatchObject({ scanned: true, file_count: 2, total_matches: 3 });
  });

  it("claims no dangling references only when a scan actually found none", async () => {
    const command = commandFor([{ entity_id: "lock.smart_lock_ultra" }]);
    const clean = vi.fn(async () => ({
      scanned: true,
      files_scanned: 12,
      file_count: 0,
      total_matches: 0,
      files: [],
      truncated: false,
    }));

    const cleanResult = await planEntityRename(
      { entityId: "lock.smart_lock_ultra", newEntityId: "lock.front_door" },
      command,
      clean,
    );
    expect(cleanResult.references_may_dangle).toBe(false);

    // No scanner, and a scanner that fails, are both "unknown" — never "safe".
    const unscannedResult = await planEntityRename(
      { entityId: "lock.smart_lock_ultra", newEntityId: "lock.front_door" },
      command,
    );
    expect(unscannedResult.references).toBeNull();
    expect(unscannedResult.references_may_dangle).toBe(true);

    const failedResult = await planEntityRename(
      { entityId: "lock.smart_lock_ultra", newEntityId: "lock.front_door" },
      command,
      async () => {
        throw new Error("disk on fire");
      },
    );
    expect(failedResult.references).toMatchObject({ scanned: false });
    expect(failedResult.references_may_dangle).toBe(true);
  });

  it("does not scan references for a no-op rename", async () => {
    const command = commandFor([{ entity_id: "lock.front_door" }]);
    const scan = vi.fn(async () => ({ scanned: true, file_count: 0, total_matches: 0, files: [] }));

    const result = await planEntityRename(
      { entityId: "lock.front_door", newEntityId: "lock.front_door" },
      command,
      scan,
    );

    expect(result.references_may_dangle).toBe(false);
  });
});

describe("entity rename application", () => {
  it("requires the exact confirmation and then updates the registry", async () => {
    const command = commandFor([{ entity_id: "lock.smart_lock_ultra" }]);
    const invalidate = vi.fn();
    const confirmation = entityRenameConfirmation("lock.smart_lock_ultra", "lock.front_door");

    await expect(
      applyEntityRename(
        {
          entityId: "lock.smart_lock_ultra",
          newEntityId: "lock.front_door",
          confirmation: "yes",
        },
        command,
        invalidate,
      ),
    ).rejects.toThrow("Confirmation must be exactly");

    const result = await applyEntityRename(
      {
        entityId: "lock.smart_lock_ultra",
        newEntityId: "lock.front_door",
        confirmation,
      },
      command,
      invalidate,
    );

    expect(result).toMatchObject({ applied: true, confirmed_entity_id: "lock.front_door" });
    expect(command).toHaveBeenCalledWith("config/entity_registry/update", {
      entity_id: "lock.smart_lock_ultra",
      new_entity_id: "lock.front_door",
    });
    expect(invalidate).toHaveBeenCalledWith("config/entity_registry/list");
  });

  it("does not write or require confirmation for a no-op", async () => {
    const command = commandFor([{ entity_id: "lock.front_door" }]);
    const invalidate = vi.fn();

    const result = await applyEntityRename(
      { entityId: "lock.front_door", newEntityId: "lock.front_door" },
      command,
      invalidate,
    );

    expect(result.applied).toBe(false);
    expect(command).toHaveBeenCalledTimes(1);
    expect(invalidate).not.toHaveBeenCalled();
  });

  it("rejects a response that does not confirm the new ID", async () => {
    const command = commandFor([{ entity_id: "lock.smart_lock_ultra" }], {
      entity_entry: { entity_id: "lock.wrong" },
    });
    const invalidate = vi.fn();

    await expect(
      applyEntityRename(
        {
          entityId: "lock.smart_lock_ultra",
          newEntityId: "lock.front_door",
          confirmation: entityRenameConfirmation("lock.smart_lock_ultra", "lock.front_door"),
        },
        command,
        invalidate,
      ),
    ).rejects.toThrow("did not confirm");
    expect(invalidate).not.toHaveBeenCalled();
  });

  it("carries the reference report through an applied rename", async () => {
    const command = commandFor([{ entity_id: "lock.smart_lock_ultra" }]);
    const scan = vi.fn(async () => ({
      scanned: true,
      files_scanned: 12,
      file_count: 1,
      total_matches: 1,
      files: [{ path: "automations.yaml", count: 1, lines: [42] }],
      truncated: false,
    }));

    const result = await applyEntityRename(
      {
        entityId: "lock.smart_lock_ultra",
        newEntityId: "lock.front_door",
        confirmation: entityRenameConfirmation("lock.smart_lock_ultra", "lock.front_door"),
      },
      command,
      vi.fn(),
      scan,
    );

    expect(result.applied).toBe(true);
    expect(result.references.files[0].path).toBe("automations.yaml");
  });
});
