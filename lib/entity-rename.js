const ENTITY_ID_PATTERN = /^[a-z][a-z0-9_]*\.[a-z0-9][a-z0-9_]*$/;

function requireEntityId(value, field) {
  if (typeof value !== "string" || value !== value.trim() || !ENTITY_ID_PATTERN.test(value)) {
    throw new Error(`Invalid ${field}; use a Home Assistant entity ID such as light.kitchen`);
  }
  return value;
}

export function entityRenameConfirmation(entityId, newEntityId) {
  return `RENAME ${entityId} -> ${newEntityId}`;
}

/**
 * A missing or failed scan is not evidence that nothing references the entity.
 * Absence of data is reported as "may dangle", never as "safe".
 */
function referencesMayDangle(changed, references) {
  if (!changed) return false;
  if (!references?.scanned) return true;
  return references.total_matches > 0;
}

function buildPlan({ entityId, newEntityId, entries, references }) {
  if (entityId.split(".")[0] !== newEntityId.split(".")[0]) {
    throw new Error("The new entity ID must keep the same domain as the current entity");
  }

  if (entityId !== newEntityId && entries.some((entry) => entry.entity_id === newEntityId)) {
    throw new Error(`An entity with ID '${newEntityId}' already exists`);
  }

  const changed = entityId !== newEntityId;
  return {
    entity_id: entityId,
    new_entity_id: newEntityId,
    changed,
    no_changes: !changed,
    ready_to_apply: changed,
    references_may_dangle: referencesMayDangle(changed, references),
    references,
    confirmation_required: changed ? entityRenameConfirmation(entityId, newEntityId) : null,
  };
}

/** Advisory only: a scanner that throws yields "not scanned", never a failed rename. */
async function collectReferences(scan, entityId) {
  if (typeof scan !== "function") return null;
  try {
    return await scan(entityId);
  } catch {
    return { scanned: false, reason: "reference scan failed" };
  }
}

export async function planEntityRename({ entityId, newEntityId }, command, scan) {
  requireEntityId(entityId, "entity ID");
  requireEntityId(newEntityId, "new entity ID");

  const entries = await command("config/entity_registry/list");
  if (!Array.isArray(entries)) {
    throw new Error("Home Assistant returned an invalid entity registry");
  }
  if (!entries.some((entry) => entry.entity_id === entityId)) {
    throw new Error(`Entity '${entityId}' was not found in Home Assistant's registry`);
  }

  const references = await collectReferences(scan, entityId);
  return buildPlan({ entityId, newEntityId, entries, references });
}

export async function applyEntityRename({ entityId, newEntityId, confirmation }, command, invalidate, scan) {
  const plan = await planEntityRename({ entityId, newEntityId }, command, scan);
  if (!plan.changed) return { ...plan, applied: false };

  if (confirmation !== plan.confirmation_required) {
    throw new Error(`Confirmation must be exactly "${plan.confirmation_required}"`);
  }

  const result = await command("config/entity_registry/update", {
    entity_id: plan.entity_id,
    new_entity_id: plan.new_entity_id,
  });
  const confirmed = result?.entity_entry;
  if (confirmed?.entity_id !== plan.new_entity_id) {
    throw new Error("Home Assistant did not confirm the requested entity ID");
  }

  invalidate?.("config/entity_registry/list");
  return { ...plan, applied: true, confirmed_entity_id: confirmed.entity_id };
}
