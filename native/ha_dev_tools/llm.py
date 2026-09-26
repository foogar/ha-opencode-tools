"""Home Assistant native LLM tool providers for developer tooling.

Contributes read-only tools to the native LLM API so they are served to clients
over ``/api/mcp/<API ID>``. Currently one tool: report which configuration files
reference a given entity ID.

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
from .scanner import (
    MAX_ENTITY_IDS,
    find_entity_references,
    is_valid_entity_id,
)


class FindEntityReferencesTool(Tool):
    """Report configuration files that reference an entity ID."""

    name = f"{DOMAIN}_find_entity_references"
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
        tools=[FindEntityReferencesTool()],
        prompt=(
            "Before renaming or deleting an entity, device or helper, call "
            f"{FindEntityReferencesTool.name} with its entity ID(s) to find the "
            "configuration that would be left pointing at nothing. It is read-only."
        ),
    )
