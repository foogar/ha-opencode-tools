# Session Handover

Paste this into a new OpenCode session to restore context. Also saved here as
`HANDOVER.md`. Contains no secrets.

## Who you're talking to

A Home Assistant user on a single supervised instance, security-conscious and
practical. They declined an upstream PR to a public repo (uneasy about AI-derived
code in public) and declined a startup hook running a root HTTP server on risk
grounds. Two repos, both private: `foogar/ha-opencode-tools` and
`foogar/ha-mcp-server-dev`.

Working style, from `AGENTS.local.md`: show the diff before writing, wait for
go-ahead, one change at a time, default to read-only when investigating. Back up
before significant changes. Capture durable "why" as decision notes — narrow
scope, not session logs.

## State of the front door lock — settled, do not revisit

A Nuki Smart Lock Ultra, serial `45A30BC7`, was represented **three times** in
Home Assistant:

1. **MQTT** — device "Front Door", area Front Porch, entity `lock.front_door`.
   **Authoritative.** 12 entities including battery %, battery type, last
   replaced, firmware 5.9.4, and four battery alerts.
2. **Matter** — device "Smart Lock Ultra", area Living Room. **Deliberately
   deleted** on 2026-09-26. It flapped offline twice within thirty minutes
   (`unavailable` 00:05, recovered 00:07, `unavailable` 00:29) while MQTT stayed
   steady, and exposed only 4 entities versus MQTT's 12. Removing it broke
   nothing: no YAML file referenced any Matter entity.
3. **iBeacon** — device `Nuki_45A30BC7` in Living Room, entities
   `device_tracker.nuki_45a30bc7` and `sensor.nuki_45a30bc7_estimated_distance`,
   both currently `unavailable`. **Leave it alone** — the user confirmed it is
   the lock's presence detection. The auto-unlock itself runs on the lock or in
   the Nuki app, **not** in Home Assistant.

Matter Server add-on left installed for a future revisit. Re-adding the lock to
Matter would need re-commissioning.

## Automations fixed 2026-09-26

The registered device is "Johns Phone", so the notify service is
`notify.mobile_app_johns_phone` (underscore after `johns`). Two automations used
`notify.mobile_app_johnsphone` and were failing silently —
`front_door_left_open` threw at 17:29 and nothing surfaced it until the editor
was opened. Both corrected in `automations.yaml` and reloaded. All 7 automations
and their `id`s intact.

## Native Home Assistant MCP — enabled and verified

- The `mcp_server` ("Model Context Protocol Server") integration is **added**.
  It has almost no configuration — only a "Control Home Assistant" boolean, and
  no LLM-API selector. The built-in Assist API is always served at
  `/api/mcp/assist`, so there is nothing to select.
- The add-on's **native MCP bridge is on** (`native_ha_mcp_enabled`), API ID
  `assist`. Verified: `enabled_and_reachable`, all endpoints HTTP 200,
  `home-assistant 1.26.0`, protocol `2025-11-25`, and real tool calls returning
  live data.
- **No credential is involved** — the bridge authenticates with the Supervisor
  token.
- A second MCP server `homeassistant_native` appears alongside `homeassistant`.
  It carries 16 Assist tools (9 of them `media_player`).
- Standing decision, recorded: dev/validation tools stay on the `homeassistant`
  MCP server. **Do not move them onto the Assist surface** — it is entity-scoped
  by design and `llm_context.assistant` / `async_should_expose` gating is aimed
  at entity tools. See decision note "Native HA MCP bridge is on by design".

## The pending task

**A Core restart is required** to finish the `ha_dev_tools` deployment.

Done already:
- `custom_components/ha_dev_tools/` deployed — 5 files, 0644, HACS components
  untouched.
- `configuration.yaml` has `ha_dev_tools:` added and **validated**.
- `reload_core_config` and `reload_all` both ran successfully and **neither
  loaded it**. In HA 2026.9.3 a newly added integration needs a Core restart.

After the restart, in this order:

1. Confirm `ha_dev_tools` appears in `get_config` → `components`.
2. Confirm the tool is in the session's catalog as
   `ha_dev_tools_find_entity_references` (catalog is fixed at session start, so
   a new session is required — that is why this handover exists).
3. Call it on a real entity and check it agrees with the script:
   `node /data/v2/cache/opencode/ha-opencode-tools/scripts/find-entity-references.mjs lock.front_door`
   should report `dashboards/workshop.yaml:22`.
4. If it does not appear, the failure will be in the Core log — an import error
   in `llm.py`. There are no unit tests for `llm.py` (no `homeassistant` package
   in the add-on container); only `scanner.py` is covered.

## Repositories

**`foogar/ha-opencode-tools`** — private, active. Authored tools, no upstream
relationship, therefore no rebase tax.

```
native/ha_dev_tools/scanner.py    pure scan logic, stdlib only  <- source of truth
native/ha_dev_tools/llm.py        thin Home Assistant layer
native/ha_dev_tools/__init__.py   TYPE_CHECKING import only
native/ha_dev_tools/manifest.json
native/run-tests.sh               20 tests, no HA needed
lib/entity-references.js          the JavaScript original
lib/entity-rename.js              rename logic (no delivery path)
scripts/find-entity-references.mjs CLI
```

Local: `/data/v2/cache/opencode/ha-opencode-tools`

The Python scanner was verified **byte-identical to the JavaScript one** across 7
entity IDs against the live configuration directory.

**`foogar/ha-mcp-server-dev`** — superseded, README marked. Awaiting manual
archive (Settings → General → Danger Zone); the token lacks repo administration
permission. Contains `rename_entity`, which is finished and tested but has **no
working delivery path** — all three routes fail:

1. Image overlay → requires shadowing upstream's `index.js` forever, and the
   diff lands in the TOOLS array and dispatch switch where conflicts recur.
2. External local MCP (`external_mcp_config`) → **blocked by an upstream bug.**
   `init-opencode` runs `chmod 700 /data/.config/opencode` on *every* start, and
   a local MCP server runs as uid 61000 and must exec under that directory, so
   the spawn fails `EACCES` even though config validation passes. `0711` works
   but is reset on the next restart.
3. Startup hook → declined on risk grounds.

Finding 2 is worth reporting upstream: no Home Assistant install with a 0700
`/data/.config/opencode` can use the documented `type: "local"` path.

Upstream clone: `/data/v2/cache/opencode/opencode-upstream` (magnusoverli/opencode,
add-on at `ha_opencode/`, MCP server at `ha_opencode/rootfs/opt/ha-mcp-server/`).

## Pitfalls — do not rediscover these

- **`homeassistant.reload` does not exist** in 2026.9.3; it returns 400. The real
  services are `homeassistant.reload_core_config`, `homeassistant.reload_all`,
  `homeassistant.restart`.
- **Newly added integrations need a Core restart**, not a config reload.
- **`hab backup create` is broken** — fails with `required key not provided at
  'agent_ids'` regardless of arguments, and the help exposes no such flag.
  Independently visible in the Core log.
- **The Core log/journal is stale** — newest entry was hours behind, so
  `get_error_log` / `get_support_logs` cannot be trusted for recent events.
- **`/homeassistant` is not a git repo.** `custom_components/` is unversioned,
  which is why native integration source lives in `ha-opencode-tools` and is
  copied in.
- **`get_config` dumps ~280 components** — it is the only reliable way to check
  whether an integration is loaded, but it is a very large payload. `mcp_server`
  appearing there while `ha_dev_tools` does not is how we proved the reload
  didn't work.
- Two tools return "no references" as a **result**, not a failure, and a failed
  scan reports `scanned: false` rather than raising. Absence of data is never
  reported as safety.

## Standing constraints to keep honouring

- Never read or print `secrets.yaml`, tokens, or the GitHub credential at
  `/data/.git-credentials`. Tokens are installed by the user via a hidden
  `read -r -s` prompt in the add-on's Terminal view, never pasted into chat.
- Do not touch the four HACS components in `custom_components/` (`battery_notes`,
  `googlefindmy`, `hacs`, `kleenex_pollenradar`).
- Do not troubleshoot `media_player.guest_bedroom` or
  `media_player.onn_streaming_device_4k_pro` — deliberately unplugged.
- Deliberately removed Z-Wave nodes stay removed.
- Automation reload stops running automation actions; say so before reloading.
- Prefer `packages/` for new configuration rather than growing
  `configuration.yaml`. (The `ha_dev_tools:` key went in `configuration.yaml`
  because packages are for reusable config fragments and forcing an integration
  to load is not one.)
