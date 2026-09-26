# ha-opencode-tools

Authored Home Assistant tools for OpenCode.

This repository exists because authored code and mirrored upstream code have
opposite requirements. A mirror is valuable precisely while it is byte-identical
to upstream — that is what makes it trustworthy to diff and to copy from. The
moment you add your own tool to it, you pay the cost of a mirror (every re-sync
must work out whether a difference is "upstream moved" or "we did this") without
the benefit. So this repo has **no upstream relationship and no rebase tax**.

The mirror of the OpenCode add-on's MCP server lives in `ha-mcp-server-dev`.

## What is here

| Path | Purpose |
|---|---|
| `lib/entity-references.js` | Bounded, read-only scan of the configuration directory for references to an entity ID |
| `lib/entity-rename.js` | Plan/apply logic for a guarded entity-registry rename |
| `scripts/find-entity-references.mjs` | CLI over the scanner |
| `test/` | Vitest coverage for all three |

```sh
npm test
node scripts/find-entity-references.mjs lock.front_door binary_sensor.front_door_door_sensor
```

The scanner never reads `secrets.yaml`, `.storage`, `deps`, `node_modules` or
`custom_components`, matches whole entity IDs rather than fragments, and caps its
own output. It needs no credentials and never calls Home Assistant.

## Delivery status, stated honestly

| Tool | Status |
|---|---|
| `find_entity_references` | **Working.** Runs as a script. Being ported to Home Assistant's native `llm.py` platform, which would make it a permanent tool. |
| `rename_entity` | **No working delivery path.** The logic is finished, reviewed and tested, but every route to a tool catalog is currently blocked. |

### Why `rename_entity` cannot ship

Three routes were explored and all three fail:

1. **Patch the built-in MCP server** — requires editing `index.js` inside a
   published image, which means shadowing that file forever. Upstream adds tools
   constantly, and the change lands in the `TOOLS` array and dispatch switch,
   exactly where conflicts occur.
2. **External local MCP server** (`external_mcp_config`) — **blocked by an
   upstream bug.** The add-on's `init-opencode` runs `chmod 700
   /data/.config/opencode` on *every* start, while a local MCP server runs as
   uid 61000 and must exec an executable under `/data/.config/opencode/bin/`.
   The child cannot traverse a 0700 parent, so the spawn fails with `EACCES`
   even though configuration validation passes. Loosening the directory to 0711
   works, but the mode is reset on the next restart, so it is a recurring
   papercut rather than a fix.
3. **Startup hook** running a root HTTP server — declined on risk grounds, and
   the `user_hooks_enabled` blast radius is documented as wide.

This is worth reporting upstream: any Home Assistant install with a 0700
`/data/.config/opencode` cannot use the documented `type: "local"` external MCP
path at all.

## Planned: native `llm.py`

`find_entity_references` is the right first tool to port, because it is
read-only and its output shape is already proven.

The route, verified against the running instance:

1. `custom_components/ha_dev_tools/llm.py` exposing
   `async_get_tools(hass, llm_context, api_id)`, returning tools for
   `LLM_API_ASSIST`.
2. The tools then appear on `/api/mcp/assist`, which the OpenCode add-on already
   bridges — no new credential, no image change, no `chmod` problem.
3. Requires an HA restart to load the integration, and a new OpenCode session to
   pick up the tools.

**Source of truth lives here, not in `custom_components/`.** `/homeassistant` is
not a git repository, so a custom integration written directly there would have
no history, no review and no diff. Keeping the source here and syncing it in
preserves that.

Note that the exposure gating in the platform docs — `llm_context.assistant` and
`async_should_expose` — is aimed at tools that act on entities. A tool whose
input is a file path does not touch entities, so that gate does not apply to it.

## Conventions

- Read-only by default. A tool that mutates must be preview-first, require an
  exact confirmation, and verify the result Home Assistant returned.
- Keep filesystem access injected so the logic is testable without a real
  configuration directory.
- Never read secrets or Home Assistant internals, even for a match count.
