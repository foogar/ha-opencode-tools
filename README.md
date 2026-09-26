# ha-opencode-tools

Authored Home Assistant tools for OpenCode.

> **Written with AI assistance.** The code in this repository was authored with
> OpenCode, an AI coding agent, and is disclosed as such. The maintainer reviews
> everything committed here and owns it. Disclosure is deliberate — you should
> know before you run it, not afterwards.

This repository exists because authored code and mirrored upstream code have
opposite requirements. A mirror is valuable precisely while it is byte-identical
to upstream — that is what makes it trustworthy to diff and to copy from. The
moment you add your own tool to it, you pay the cost of a mirror (every re-sync
must work out whether a difference is "upstream moved" or "we did this") without
the benefit. So this repo has **no upstream relationship and no rebase tax**.

An earlier mirror of the OpenCode add-on's MCP server lived in
`ha-mcp-server-dev`. That repository is **archived** as of 2026-09-26; its
`rename_entity` work is described below for the record and is not being
continued.

## What is here

| Path | Purpose |
|---|---|
| `native/ha_dev_tools/scanner.py` | Reference scanner. Pure logic, stdlib only — **source of truth** |
| `native/ha_dev_tools/references.py` | Dangling-reference scanner. Pure logic, stdlib only |
| `native/ha_dev_tools/llm.py` | Thin Home Assistant layer: `async_get_tools`, the tool classes |
| `native/ha_dev_tools/__init__.py` | `TYPE_CHECKING` import only |
| `native/ha_dev_tools/const.py` | `DOMAIN` constant |
| `native/ha_dev_tools/manifest.json` | Integration manifest |
| `native/run-tests.sh` | 78 tests, no Home Assistant needed |
| `native/tests/` | `unittest` for the pure logic, static AST checks for `llm.py` |
| `lib/entity-references.js` | The JavaScript original the Python was verified against |
| `lib/entity-rename.js` | Plan/apply logic for a guarded entity-registry rename |
| `scripts/find-entity-references.mjs` | CLI over the scanner |
| `test/` | Vitest coverage for the JavaScript |
| `RESEARCH-device-duplicate-detection.md` | Abandoned approach, with the measurements that killed it |

```sh
npm test                                # 28 Vitest tests
./native/run-tests.sh                   # 78 unittest tests
node scripts/find-entity-references.mjs lock.front_door
```

## The two tools

Both are read-only and served over Home Assistant's native LLM platform.

**`ha_dev_tools__find_entity_references`** — given entity IDs, reports which
configuration files name them, with line numbers. Use before a rename or delete.

**`ha_dev_tools__find_dangling_references`** — reports configuration that points
at entity IDs which do not exist: triggers that can never fire, cards that are
permanently unavailable, hand-typed typos. Use when something mysteriously does
not work.

They answer disjoint questions and neither substitutes for the other. A rename
leaves working references behind; a typo leaves a reference with nothing behind
it, and the configuration still validates, so Home Assistant loads it silently.

The second tool validates existence against **`hass.states`, never the entity
registry**. An entity declared in `configuration.yaml` under a platform with no
`unique_id` — an MQTT sensor, for instance — is in the state machine and in no
registry at all. Validating against the registry reports working entities as
missing, and acting on that deletes working configuration. That is not
hypothetical: it happened here with `sensor.proxmox_battery_level`, which is now
a regression fixture. See `RESEARCH-device-duplicate-detection.md`.

Service names share the `domain.object` shape with entity IDs, so service calls
are excluded explicitly. `light.turn_on` is a service, not a missing entity.

Neither scanner reads `secrets.yaml`, `.storage`, `deps`, `node_modules` or
`custom_components`, matches whole entity IDs rather than fragments, and caps its
own output. They need no credentials and never call Home Assistant.

That exclusion list is a **deliberate design property of a shipped tool**, not an
oversight to fix. It is separate from the maintainer's own rule that internal
directories are read-safe when investigating by hand — an assistant grepping a
config directory is a different act from a tool scanning one, and the tool ships
to anyone who installs it.

## Delivery status, stated honestly

| Tool | Status |
|---|---|
| `find_entity_references` | **Shipped and live.** Deployed as the Home Assistant native provider `ha_dev_tools__find_entity_references` and served on `/api/mcp/assist`. The CLI is the standalone equivalent and needs no install. |
| `find_dangling_references` | **Built and deployed, pending a Core restart.** The second tool under the `ha_dev_tools` umbrella. Pure logic in `references.py`, 27 new tests plus contract checks for module isolation. Not yet live: `llm.py` changed, so the LLM platform must re-register. |
| `rename_entity` | **No working delivery path.** The logic is finished, reviewed and tested, but every route to a tool catalog is currently blocked. |
| *(device duplicate detection)* | **Abandoned after measurement.** See `RESEARCH-device-duplicate-detection.md`. Zero shared identifiers across 63 devices made the intended confidence model unusable. |

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

## Native `llm.py` — shipped 2026-09-26

`find_entity_references` was the right first tool to port, because it is
read-only and its output shape was already proven. The route, and its verified
outcome:

1. `custom_components/ha_dev_tools/llm.py` exposing
   `async_get_tools(hass, llm_context, api_id)`, returning tools for
   `LLM_API_ASSIST`.
2. The tools then appear on `/api/mcp/assist`, which the OpenCode add-on already
   bridges — no new credential, no image change, no `chmod` problem.
3. Loading a newly added integration needs a **Core restart**, not a config
   reload: `reload_core_config` and `reload_all` both succeed and neither loads
   it. A new OpenCode session is then required to pick up the tool.

All three steps are done. Verified against the running instance: the integration
is present in `get_config` → `components`, the tool is in the session catalog,
and its output is identical to the standalone CLI's
(`files_scanned: 12`, `total_matches: 1`, `dashboards/workshop.yaml:22`).

**Source of truth lives here, not in `custom_components/`.** `/homeassistant` is
not a git repository, so a custom integration written directly there would have
no history, no review and no diff. Keeping the source here and syncing it in
preserves that. Nothing enforces the sync — after changing anything under
`native/ha_dev_tools/`, copy it across by hand and diff to confirm.

On the platform docs' exposure gating (`llm_context.assistant`,
`async_should_expose`): those are aimed at tools that act on entities. A tool
whose input is a file path does not touch entities, and it is served on the
Assist endpoint without issue. Note that a native `llm.py` provider is exposed
through Home Assistant's *native* MCP surface and **not** through the add-on's
`homeassistant` MCP server, which has a separate tool registry and cannot reach
it. That is expected, not a misconfiguration.

### Testing the HA-facing layer

`llm.py` cannot be imported without the `homeassistant` package, which the
add-on container does not have, so it is covered by static AST checks in
`native/tests/test_integration_contract.py` rather than at runtime. That suite is
worth keeping: it is what caught an `async_setup` signature bug that would have
prevented the integration loading at all.

## Conventions

- Read-only by default. A tool that mutates must be preview-first, require an
  exact confirmation, and verify the result Home Assistant returned.
- Keep filesystem access injected so the logic is testable without a real
  configuration directory.
- Never read secrets or Home Assistant internals, even for a match count.
