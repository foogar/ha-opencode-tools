# Session Handover

Paste this into a new OpenCode session to restore context. Also saved here as
`HANDOVER.md`. Contains no secrets. Snapshot taken 2026-09-26.

## Who you're talking to

A Home Assistant user on a single supervised instance, security-conscious and
practical. They declined an upstream PR to a public repo — the real reason was
never established, and an earlier claim that they were uneasy about AI-derived
code was **wrong**; see the pinned note "Disclose AI authorship" — and declined a
startup hook running a root HTTP server on risk grounds. One active private repo:
`foogar/ha-opencode-tools`.

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

## ha_dev_tools — deployed and verified live

**Nothing is pending here. Do not schedule a restart to "finish" it.**

Deployment is complete and the tool answers live:

- `custom_components/ha_dev_tools/` — 5 files (including `const.py`), mode 0644,
  HACS components untouched. Byte-identical to `native/ha_dev_tools/` in the repo.
- `configuration.yaml` has `ha_dev_tools:` added and validated.
- `ha_dev_tools` **is** present in `get_config` → `components`.
- The tool is live as `ha_dev_tools_find_entity_references` on the
  `homeassistant_native` surface.

The Core restart that was needed has already happened. Verification, in the order
it was originally planned:

1. `ha_dev_tools` in `get_config` → `components` — **confirmed**.
2. Tool in the session catalog — **confirmed**. (The catalog is fixed at session
   start, so confirming it required a *new* session; that is why this handover
   existed.)
3. Agreement with the standalone script — **confirmed**. The tool's per-entity
   payload is identical to
   `node scripts/find-entity-references.mjs lock.front_door`: `files_scanned: 12`,
   `total_matches: 1`, `dashboards/workshop.yaml:22`, `truncated: false`. Only
   the envelope differs (the tool adds `not_scanned` / `affected_file_count`; the
   CLI adds `elapsed_ms`). Independently cross-checked with a raw grep.

`workshop.yaml:22` is the only live reference. Correctly *excluded*: a
`my-home.yaml.bak` file, a docstring example inside `scanner.py` itself, and the
entity registry.

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
  It carries the 16 Assist tools (9 of them `media_player`) **plus**
  `ha_dev_tools_find_entity_references`, for 17.
- Standing decision, recorded and **pinned**: dev/validation tools stay on the
  `homeassistant` MCP server. **Exception:** native `<integration>/llm.py`
  providers — `ha_dev_tools` among them — are exposed on `homeassistant_native`,
  and that is their only delivery path, not a misplaced tool. They register with
  Core's `llm` component, and the add-on's own MCP server has a separate tool
  registry that cannot reach them. **Do not try to relocate them** — an earlier
  version of this document said otherwise and was wrong. See decision note "Dev
  tools stay on the homeassistant MCP server; native llm.py providers excepted".

## Repositories

**`foogar/ha-opencode-tools`** — private, active. Authored tools, no upstream
relationship, therefore no rebase tax.

```
native/ha_dev_tools/scanner.py           pure scan logic, stdlib only  <- source of truth
native/ha_dev_tools/llm.py               thin Home Assistant layer
native/ha_dev_tools/__init__.py          TYPE_CHECKING import only
native/ha_dev_tools/const.py             DOMAIN constant
native/ha_dev_tools/manifest.json
native/run-tests.sh                      35 tests, no HA needed
native/tests/test_scanner.py             runtime tests for the scanner
native/tests/test_integration_contract.py  static AST checks on the HA-facing layer
lib/entity-references.js                 the JavaScript original
lib/entity-rename.js                     rename logic (no delivery path)
scripts/find-entity-references.mjs       CLI
test/*.test.js                           JavaScript test suite
```

Local: `/data/v2/cache/opencode/ha-opencode-tools`

The Python scanner was verified **byte-identical to the JavaScript one** across 7
entity IDs against the live configuration directory.

Testing: **35 tests, all passing.** `scanner.py` is stdlib-only and tested at
runtime. `llm.py` **cannot** be imported without the `homeassistant` package
(absent from the add-on container), so it is covered by static AST checks in
`test_integration_contract.py` instead — an earlier claim that it had no tests at
all was true when written and is now out of date. That contract suite is what
caught the `async_setup` signature bug; the fix is deployed.

**Snapshot:** the local clone is level with `origin/main` — 0 ahead / 0 behind,
working tree clean. The doc changes flagged in earlier revisions of this file
(AI-assistance disclosure, corrected delivery status) have since been
committed; `git status` in the repo remains the authority.

**`foogar/ha-mcp-server-dev`** — **archived on GitHub 2026-09-26.** Superseded
and README-marked beforehand. A local clone remains at
`/data/v2/cache/opencode/ha-mcp-server-dev`; it is clean, all 4 branches are
`0 ahead / 0 behind` origin, no stashes, `node_modules` gitignored. Nothing is
stranded there and nothing needs doing — it is ~105M of disk if you ever want it
gone.

It contains `rename_entity`, finished and tested, with **no working delivery
path**. All three routes fail, and the user has said not to spend more time on
it:

1. Image overlay → requires shadowing upstream's `index.js` forever, and the
   diff lands in the TOOLS array and dispatch switch where conflicts recur.
2. External local MCP (`external_mcp_config`) → **blocked by an upstream bug.**
   `init-opencode` runs `chmod 700 /data/.config/opencode` on *every* start, and
   a local MCP server runs as uid 61000 and must exec under that directory, so
   the spawn fails `EACCES` even though config validation passes. `0711` works
   but is reset on the next restart.
3. Startup hook → declined on risk grounds.

Finding 2 is still worth reporting upstream: no Home Assistant install with a
0700 `/data/.config/opencode` can use the documented `type: "local"` path.

Upstream clone: `/data/v2/cache/opencode/opencode-upstream` (magnusoverli/opencode,
add-on at `ha_opencode/`, MCP server at `ha_opencode/rootfs/opt/ha-mcp-server/`).

## Pitfalls — do not rediscover these

- **`homeassistant.reload` does not exist** in 2026.9.3; it returns 400. The real
  services are `homeassistant.reload_core_config`, `homeassistant.reload_all`,
  `homeassistant.restart`.
- **Newly added integrations need a Core restart**, not a config reload.
  `reload_core_config` and `reload_all` both succeed and neither loads them.
- **`hab backup create` is broken** — fails with `required key not provided at
  'agent_ids'` regardless of arguments, and the help exposes no such flag.
  Independently visible in the Core log.
- **The Core log/journal is stale** — newest entry was hours behind, so
  `get_error_log` / `get_support_logs` cannot be trusted for recent events.
- **`/homeassistant` is not a git repo.** `custom_components/` is unversioned,
  which is why native integration source lives in `ha-opencode-tools` and is
  copied in. Re-copy after changing the repo source; nothing enforces this.
- **`get_config` dumps ~280 components** — it is the only reliable way to check
  whether an integration is loaded, but it is a very large payload.
- Two tools return "no references" as a **result**, not a failure, and a failed
  scan reports `scanned: false` rather than raising. Absence of data is never
  reported as safety.
- **`python3` has no `yaml` module** in the add-on container. To check that
  `decisions.yaml` still parses, use `recall_decisions` — it parses the file, so
  a successful return is the parse test.
- **The add-on's `env_vars` field is an allowlist, not an environment
  passthrough.** Only names matching `*_API_KEY` (plus `PPQ_API_KEY`) are
  forwarded; anything else is accepted, saved to `options.json`, silently
  dropped, and merely produces a startup warning — "Some env_vars are not
  forwarded to the V2 backend". The filter is
  `/opt/opencode-v2-homeassistant/user-config.js:112-118`. Verified 2026-09-26
  after a full HA restart: `GIT_CONFIG_GLOBAL=/data/gitconfig` passes the UI and
  lands in `options.json`, but is absent from the container —
  `/run/s6/container_environment` holds no `GIT_*` — so **no restart can
  inject it**. Consequence: `git config --global` hard-fails, because
  `HOME=/run/opencode-v2/home` and `XDG_CONFIG_HOME=/run/opencode-v2/config` are
  on the container overlay and are wiped on add-on restart. Per-repo
  `.git/config` under `/data` is the durable path and authenticates fine
  (`ls-remote` proven). A **new clone** has no per-repo config, so set
  `user.name`, `user.email` and
  `credential.helper=store --file=/data/.git-credentials` after cloning.
  Pinned decision note.
- **Add-on options cannot be changed from the agent.** `hab` has no
  add-on/options command and `hassio` exposes no options service (only
  `addon_start/stop/restart/stdin`, backups, restore, `mount_reload`). The
  Supervisor API returns 401 because `SUPERVISOR_TOKEN` / `HASSIO_TOKEN` live
  in the s6 container environment and are deliberately **not** inherited by the
  agent shell — do not scrape them out of
  `/run/s6/container_environment/SUPERVISOR_TOKEN` to get around this. The user
  must edit options in the add-on UI; hand-editing `/data/options.json`
  desyncs from Supervisor's authoritative copy and is likely to be reverted.
  The inert `GIT_CONFIG_GLOBAL` row is still present — Configuration →
  Environment variables → delete it to silence the warning.

## Standing constraints to keep honouring

- Never read or print `secrets.yaml`, tokens, or the GitHub credential at
  `/data/.git-credentials`. Tokens are installed by the user via a hidden
  `read -r -s` prompt in the add-on's Terminal view, never pasted into chat.
- HA internal directories — `.storage/`, `.cloud/`, `deps/`, `tts/`,
  `home-assistant_v2.db`, `home-assistant.log` — are **read-safe, never
  write-safe**. Routine greps and reads that surface their content are fine and
  often useful (the entity registry settles platform and `unique_id` questions
  faster than any MCP tool). Never edit, write, move or delete inside them; use
  MCP tools or `hab` to change state. Pinned decision note.
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
- **Disclose AI assistance.** Code in these repos is AI-written and says so in
  the README. Do not withhold code from a public repo *because* it is
  AI-generated — that is not the user's view, and disclosure is the point.
- Git identity is `foogar <foogar@gmail.com>`, set **per-repo** in each repo's
  `.git/config`. Commits authored before 2026-09-26 were attributed to
  `root@<container-id>` because no identity was configured; left as-is rather
  than rewritten. `/data/gitconfig` holds the same values and is **not** in
  effect — see the pitfall above.
