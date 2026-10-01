# ha-opencode-tools

Read-only Home Assistant tools for AI coding agents, shipped as a native
`llm.py` integration. Two working tools, one parked, and the reasoning behind
each.

> **Written with AI assistance.** The code in this repository was authored with
> OpenCode, an AI coding agent, and is disclosed as such. Disclosure is
> deliberate — you should know before you run it, not afterwards.

This repo also records one approach that did not ship, and why. The reasoning
travels with the code on purpose: if you are considering the same three delivery
routes, you should not have to rediscover why two of them fail.

## Why these two tools

Both exist to answer a question Home Assistant's own tooling will not.

A reference to an entity that no longer exists does not produce an error. The
YAML still validates, Home Assistant still loads it, an automation trigger
simply never fires, and a dashboard card stays permanently unavailable. Nothing
in the UI says so. These tools find that, read-only, before you spend an
afternoon wondering why a light never comes on.

The second one is the more interesting half, and the reason to read this
README before using it: **how existence is checked decides whether the tool is
safe.** See the warning below — getting it wrong deletes working configuration,
and it will not look like an error while it happens.

## Install

Copy `native/ha_dev_tools/` into your Home Assistant `config/custom_components/`
directory, then add to `configuration.yaml`:

```yaml
ha_dev_tools:
```

Then **restart Core**. A config reload will not load a newly added integration —
`homeassistant.reload_all` and `reload_core_config` both succeed and neither
picks it up. After the restart the tools appear on `/api/mcp/assist`; a new
agent session is needed to see them.

Verify:

- `ha_dev_tools` is listed in `get_config` → `components`
- the tools are in the agent's session tool catalog
- calling either tool gives the same answer as the standalone CLI below

Requires Home Assistant 2026.8 or newer for the native LLM platform. Standard
library only — `requirements` is empty in the manifest, there is nothing to
install, and no HACS is involved.

Prefer the CLI? It needs no install at all:

```sh
node scripts/find-entity-references.mjs lock.front_door
```

## What is here

| Path | Purpose |
|---|---|
| `native/ha_dev_tools/scanner.py` | Reference scanner. Pure logic, stdlib only — **source of truth** |
| `native/ha_dev_tools/references.py` | Dangling-reference scanner. Pure logic, stdlib only |
| `native/ha_dev_tools/llm.py` | Thin Home Assistant layer: `async_get_tools`, the tool classes |
| `native/ha_dev_tools/__init__.py` | `TYPE_CHECKING` import only |
| `native/ha_dev_tools/const.py` | `DOMAIN` constant |
| `native/ha_dev_tools/manifest.json` | Integration manifest |
| `native/run-tests.sh` | 80 tests, no Home Assistant needed |
| `native/tests/` | `unittest` for the pure logic, static AST checks for `llm.py` |
| `lib/entity-references.js` | The JavaScript original the Python was verified against |
| `lib/entity-rename.js` | Plan/apply logic for a guarded entity-registry rename |
| `scripts/find-entity-references.mjs` | CLI over the scanner |
| `test/` | Vitest coverage for the JavaScript |

```sh
npm test                                # 28 Vitest tests
./native/run-tests.sh                   # 80 unittest tests
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

### Validate against `hass.states`, never the registry

This is the part worth reading twice, because getting it wrong is destructive
and does not announce itself.

An entity declared in `configuration.yaml` under a platform with no `unique_id`
— an MQTT sensor, for instance — is in the state machine and in **no registry at
all**. The registry is not a list of what exists; it is a list of what has been
*registered*, and those are different sets.

So a tool that checks the registry reports working entities as missing. Acting
on that report deletes working configuration — an automation and a dashboard
card that were both fine a minute earlier. A `grep` for a broken entity and a
registry lookup can agree perfectly and both be wrong, because they answer an
easier question than the one you asked.

That is not hypothetical here: it happened during development, and the entity
involved is now a regression fixture in `native/tests/test_references.py`. If
that test ever fails, the tool is dangerous rather than merely wrong.

The general rule, which applies to any check you write: **before you trust an
absence, confirm the source was capable of showing you the thing.** "Not found"
from a partial index means "not in the index." Nothing in the output tells you
which one you got.

Service names share the `domain.object` shape with entity IDs, so service calls
are excluded explicitly. `light.turn_on` is a service, not a missing entity.

Neither scanner reads `secrets.yaml`, `.storage`, `.cloud`, `deps`, `tts`,
`node_modules` or `custom_components`, matches whole entity IDs rather than
fragments, and caps its own output. They need no credentials and never call Home
Assistant. Absence of data is always reported as a result, never as safety — a
failed scan says `scanned: false` rather than returning an empty list that reads
like a clean bill of health.

That exclusion list is a **deliberate design property**, not an oversight to fix.
An agent grepping a config directory while investigating is a different act from
a tool that scans one on every run for anyone who installs it, and the tool is
held to the stricter standard.

## Delivery status, stated honestly

| Tool | Status |
|---|---|
| `find_entity_references` | **Shipped.** Installs with the integration above and is served on `/api/mcp/assist`. The CLI is the standalone equivalent and needs no install. |
| `find_dangling_references` | **Shipped.** The second tool under the `ha_dev_tools` umbrella. Pure logic in `references.py`, covered by the same suites plus contract checks for module isolation. |
| `rename_entity` | **Finished, not shipped.** The logic is complete, reviewed and tested, but no delivery route reached a tool catalog. See below — this is parked, not abandoned. |
| *(device duplicate detection)* | **Abandoned after measurement.** Not every device registry carries a strong enough signal to group devices by identity; on a well-kept install the true-positive rate is worse than the false-positive traps. Registry *anomalies* — null manufacturer/model, missing area, phantom vendor entries — are the better target. |

### Why `rename_entity` is not shipped

Three routes were explored. Two fail on a blocker; the third was declined on
risk grounds. None is a defect in the logic.

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
3. **Startup hook** running a root HTTP server — **declined deliberately.**
   Not a blocker: a judgement call. Running an unauthenticated listener as
   root on every start is a large blast radius for a convenience tool, and it
   was declined rather than attempted. This one would unblock the moment that
   trade was judged acceptable.

Route 2 is worth reporting upstream: any Home Assistant install with a 0700
`/data/.config/opencode` cannot use the documented `type: "local"` external MCP
path at all. The tool is blocked on someone else's bug, and that is the piece
most likely to change.

## How the native `llm.py` integration works

The install steps above assume this, so here is the short version. A native
`llm.py` provider exposes tools by implementing
`async_get_tools(hass, llm_context, api_id)` and returning tools for
`LLM_API_ASSIST`. They then appear on `/api/mcp/assist`, which any MCP client
bridges — no new credential, no image change, no extra service to run.

Two things that surprise people:

**A Core restart is mandatory, and a reload will lie to you.** Adding a new
integration does not load on `reload_all` or `reload_core_config` — both succeed
and neither loads it. This is the single most common reason a correctly installed
tool appears to be missing.

**The tool will not appear on every MCP surface, and that is not a
misconfiguration.** A native `llm.py` provider is served through Home Assistant's
*native* MCP endpoint. An add-on's own MCP server keeps a separate tool registry
and cannot reach it. Seeing the tool on one surface and not another is expected.

On the platform docs' exposure gating (`llm_context.assistant`,
`async_should_expose`): those are aimed at tools that act on entities. A tool
whose input is a file path does not touch entities, and it is served on the
Assist endpoint without issue.

**Keep the source out of `custom_components/`.** A Home Assistant config
directory is usually not a git repository, so an integration written directly
there has no history, no review and no diff. Keep the source in version control
and copy it in. Nothing enforces that copy — if you edit this repo, copy
`native/ha_dev_tools/` across and diff to confirm, or your installed copy and
this repo will silently diverge.

### Testing the HA-facing layer

`llm.py` cannot be imported without the `homeassistant` package installed, so it
is covered by static AST checks in `native/tests/test_integration_contract.py`
rather than at runtime. That suite is worth keeping: it is what caught an
`async_setup` signature bug that would have stopped the integration loading at
all — a failure mode with no runtime error to debug.

## Licence

MIT — see [`LICENSE`](LICENSE). Copy `references.py` or `scanner.py` into your
own project if that is easier than installing the integration; attribution in a
comment is all that is required.

## Contributing

- Read-only by default. A tool that mutates must be preview-first, require an
  exact confirmation, and verify the result Home Assistant returned.
- Keep filesystem access injected so the logic is testable without a real
  configuration directory.
- Never read secrets or Home Assistant internals, even for a match count.
- Test fixtures use invented entity IDs. A real one leaks the install it came
  from, and a fixture that reads like someone's actual house invites someone to
  treat it as real.
- A failure to scan is reported as `scanned: false`. Never return an empty list
  for a scan that did not complete — a tool that cannot tell "nothing found" from
  "did not look" will be trusted when it should not be.
