"""Home Assistant native LLM tool providers for developer tooling.

Contributes read-only tools to the native LLM API so they are served to clients
over ``/api/mcp/<API ID>``. Two tools: report which configuration files reference
a given entity ID, and report configuration references to entity IDs that do not
exist at all.

Home Assistant calls ``async_get_tools`` on the event loop, so the filesystem
walk is pushed to an executor rather than run inline. The walk is small and
bounded, but a blocking walk of a large configuration directory is still a
stalled event loop, and "it's probably fast" is how that rule gets broken later.
"""

from __future__ import annotations

import asyncio
from typing import override

import voluptuous as vol

import homeassistant.helpers.config_validation as cv
from homeassistant.components.llm import LLMTools
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.llm import (
    LLM_API_ASSIST,
    LLMContext,
    Tool,
    ToolInput,
)
from homeassistant.util.json import JsonObjectType

from .const import DOMAIN
from .references import find_dangling_references
from .scanner import (
    MAX_ENTITY_IDS,
    find_entity_references,
    is_valid_entity_id,
)


class FindEntityReferencesTool(Tool):
    """Report configuration files that reference an entity ID."""

    # The double underscore is required, not a typo. Home Assistant expects a
    # custom integration's LLM tools to be prefixed with "<domain>__" and logs
    # "This will stop working in Home Assistant 2027.3" for a single underscore.
    # Confirmed against Core 2026.9.3; see the test asserting it in
    # test_integration_contract.py before "correcting" this back.
    name = f"{DOMAIN}__find_entity_references"
    description = (
        "Report which Home Assistant configuration files reference the given entity "
        "IDs, with line numbers. Use this before renaming or deleting an entity, "
        "device or helper, to find the automations, scripts, scenes and dashboards "
        "that would be left pointing at an ID that no longer exists. Read-only: it "
        "changes nothing, and it never reads secrets.yaml, .storage, custom_components "
        "or dependency directories."
    )
    parameters = vol.Schema(
        {
            # A plain string is accepted too; cv.ensure_list wraps it. Both this and
            # [str] convert to a clean JSON Schema, which the platform docs require:
            # a validator the schema converter cannot introspect serialises to an
            # empty member and strict MCP clients then refuse to compile the tool.
            vol.Required("entity_ids"): vol.All(cv.ensure_list, [str]),
        }
    )

    @override
    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: ToolInput,
        llm_context: LLMContext,
    ) -> JsonObjectType:
        """Call the tool."""
        entity_ids = tool_input.tool_args["entity_ids"]

        if not entity_ids:
            raise HomeAssistantError("entity_ids must contain at least one entity ID")
        if len(entity_ids) > MAX_ENTITY_IDS:
            raise HomeAssistantError(
                f"Too many entity IDs: {len(entity_ids)}. "
                f"Ask about at most {MAX_ENTITY_IDS} at a time."
            )

        invalid = [entity_id for entity_id in entity_ids if not is_valid_entity_id(entity_id)]
        if invalid:
            raise HomeAssistantError(
                f"Not a valid entity ID: {', '.join(map(str, invalid))}. "
                "Use a form such as light.kitchen."
            )

        config_dir = hass.config.path()

        # One executor hop per ID. The scan is bounded, and a single combined walk
        # would be faster, but keeping one report per ID makes the results
        # independently verifiable against lib/entity-references.js.
        reports = await asyncio.gather(
            *(
                hass.async_add_executor_job(find_entity_references, entity_id, config_dir)
                for entity_id in entity_ids
            )
        )

        results = {
            entity_id: report for entity_id, report in zip(entity_ids, reports, strict=True)
        }
        affected = sorted(
            {
                file["path"]
                for report in reports
                for file in report["files"]  # type: ignore[union-attr]
            }
        )
        total = sum(int(report["total_matches"]) for report in reports)  # type: ignore[arg-type]
        unscanned = [entity_id for entity_id, report in results.items() if not report["scanned"]]

        return {
            "config_dir": config_dir,
            "entity_ids": len(entity_ids),
            "affected_files": affected,
            "affected_file_count": len(affected),
            "total_matches": total,
            "not_scanned": unscanned,
            "results": results,
        }


class FindDanglingReferencesTool(Tool):
    """Report configuration references to entity IDs that do not exist."""

    # Double underscore, as above: see FindEntityReferencesTool.name.
    name = f"{DOMAIN}__find_dangling_references"
    description = (
        "Report automations, scripts, scenes and dashboards that reference an "
        "entity ID which does not exist. Use this when a trigger never fires, a "
        "card is unavailable, or a rename is suspected of leaving something "
        "behind. Existence is checked against the live state machine, not the "
        "entity registry, because entities declared in YAML without a unique_id "
        "are absent from the registry while working perfectly. Service calls such "
        "as light.turn_on are excluded. Read-only: it changes nothing, and it "
        "never reads secrets.yaml, .storage, custom_components or dependency "
        "directories."
    )
    parameters = vol.Schema({})

    @override
    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: ToolInput,
        llm_context: LLMContext,
    ) -> JsonObjectType:
        """Call the tool."""
        # Existence comes from hass.states, never the entity registry. An entity
        # declared in configuration.yaml under a platform with no unique_id lives
        # only in the state machine: validating against the registry reports real,
        # working entities as missing, and acting on that report deletes working
        # configuration. That is not hypothetical - it happened on this install
        # with sensor.registry_absent_sensor, an MQTT sensor at 77%.
        existing = frozenset(hass.states.async_entity_ids())

        # Service names share the domain.object shape with entity IDs, so
        # light.turn_on would otherwise be indistinguishable from a missing
        # entity on form alone.
        service_names = frozenset(
            f"{domain}.{service}"
            for domain, services in hass.services.async_services().items()
            for service in services
        )

        report = await hass.async_add_executor_job(
            find_dangling_references,
            hass.config.path(),
            existing,
            service_names,
        )

        return {
            "config_dir": hass.config.path(),
            "validated_against": "hass.states",
            "live_entity_count": len(existing),
            "service_names_excluded": len(service_names),
            **report,
        }


@callback
def async_get_tools(
    hass: HomeAssistant,
    llm_context: LLMContext,
    api_id: str,
) -> LLMTools | None:
    """Return the developer tools for the APIs this integration supports."""
    if api_id != LLM_API_ASSIST:
        return None

    # The platform guidance is to gate on the assistant and filter entities with
    # async_should_expose. That gate exists so a conversation agent cannot reach
    # entities the user has not exposed. This tool takes entity IDs as text and
    # reads files; it never touches the state machine, so there is nothing to
    # expose or filter, and gating it would only make it vanish in the non-assist
    # contexts where it is still perfectly safe.
    return LLMTools(
        tools=[FindEntityReferencesTool(), FindDanglingReferencesTool()],
        prompt=(
            "Before renaming or deleting an entity, device or helper, call "
            f"{FindEntityReferencesTool.name} with its entity ID(s) to find the "
            "configuration that would be left pointing at nothing. When a trigger "
            "never fires or a dashboard card is unavailable, call "
            f"{FindDanglingReferencesTool.name} to find configuration that "
            "already points at nothing. Both are read-only."
        ),
    )
