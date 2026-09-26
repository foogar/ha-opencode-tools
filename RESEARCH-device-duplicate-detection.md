# Research: detecting one physical device exposed by several integrations

**Status: abandoned as designed. Kept so a future attempt starts from evidence
rather than from the hypothesis.** Written 2026-09-26.

The question was worth asking. This installation has `matter`, `zha` and
`zwave_js` stacks, and a device that appears as three separate devices is a
real failure mode: diagnose the wrong one and you are reading stale data while
believing you are reading the truth. It happened here — a Nuki lock was
represented three times (MQTT authoritative, Matter duplicating it, iBeacon
tracking presence), and the Matter copy flapped while MQTT stayed steady.

The idea was a tool that groups devices likely to be one physical thing, ranked
by confidence, showing its evidence. It is not viable here. Below is why, so the
next attempt does not repeat the work.

## What was measured

The device registry was read directly: 63 devices, every field available on a
device record — `identifiers`, `connections`, `via_device_id`, `serial_number`,
`manufacturer`, `model`, `area_id`, `config_entries`.

Note on method: the registry is a *partial* view. Entities declared in
`configuration.yaml` under a platform with no `unique_id` (an MQTT sensor, for
example) never appear in it at all. For device-level questions the registry was
sufficient, but that gap matters enormously for entity-level ones — see the last
section, where it nearly caused a mistake.

## Finding 1 — the strongest signal does not exist here

**Zero pairs of devices share an identifier value.** Across all 63 devices, no
MAC, serial, or `connections` tuple is shared between any two of them, whether
across integrations or within one.

This kills the design. The intended high-confidence tier was "two devices
sharing an identifier are certainly the same hardware", and on this installation
that tier is empty. Every judgement would have rested on name and model
comparison, which is the weakest possible evidence.

Identifiers still have one use: proving two devices are *different*. Each of the
six dimmers below carries a distinct serial. They never link anything.

## Finding 2 — all three predicted duplicates were wrong

| Predicted | Reality |
|---|---|
| `Kleenex Pollen Radar` (MarcoGos) + `Kleenex Pollen Radar (Gulfport MS)` (Kleenex / Scottex) | Not a duplicate. The second device's platform is `hacs` — a phantom device entry created by HACS, carrying 2 entities against the real radar's 13. It is a registry artefact, not a second radar. |
| `Xbox ` (Microsoft / Xbox Network) + `Xbox Series X` (Microsoft / Xbox Series X) | Genuine, but a different category: **one** integration (`xbox`) creating two devices for one console. Not cross-integration duplication at all. |
| `Johns Phone` (OnePlus CPH2583, `mobile_app`) + `OnePlus 12` (Google / Find My Device, `googlefindmy`) | Not a duplicate. `googlefindmy` supplies location, `mobile_app` supplies presence and 128 device sensors. Complementary, exactly like the Nuki lock's MQTT + iBeacon split. The names share nothing, so no name-based method would ever have paired them. |

The third is the instructive one. It looks like a duplicate in a device list and
is not one. Any tool reporting it would have been wrong about something real.

## Finding 3 — the false-positive traps are severe

| Devices | Why matching is wrong |
|---|---|
| 6 × `con ZBT-DIMLight-D0113` (con / ZBT-DIMLight-D0113; 4 front porch, 2 hallway) | Identical name, manufacturer and model. Six distinct devices, four of them in one room. |
| 4 × `Bedroom Bulb 1-4` (Nanoleaf / Nanoleaf Wi-Fi A19/A60) | One fixture, four bulbs, same model. |
| 2 × `Home Assistant Connect ZBT-2` (Nabu Casa) | **Identical manufacturer and model, genuinely different hardware** — one is the Zigbee coordinator, the other the Thread adapter. Only the MAC suffix in one name and the entity counts (2 vs 47) distinguish them. |

The ZBT-2 pair is the trap that matters. Same model, same domain, different
physical devices, and the difference is a hardware fact recorded nowhere in the
device record. `AGENTS.local.md` states it explicitly: Thread and Zigbee are not
one adapter. A tool reading only the registry would contradict a documented fact
about the house.

Score: roughly **1 true positive against 12 false-positive traps**. A tool with
that ratio teaches its reader to ignore it, which is worse than not having it.

## What is actually worth building instead

The genuinely useful output of this exercise was not duplication — it was
**registry anomalies**, which had a far better hit rate:

- Devices with **null `manufacturer` and `model`**: `Nuki_45A30BC7`,
  `S41cafb1ca5899a54C 7884`, `BLESmart_00000152005FBF0FC13E`,
  `Sb25d62177bb7514cC 6513`
- Devices with **no `area_id`** while their siblings have one: `Xbox Series X`,
  `OnePlus 12`
- **Phantom HACS devices** — platform `hacs` entries masquerading as hardware
- **One integration creating several devices** for one thing (the `xbox` pair)

Caveat on value: on a well-kept install these are few, and each is a
ten-minute manual fix. That is a poor return for a maintained tool, and it is
the reason this is parked rather than queued.

## The transferable lesson

**A registry is a partial view, and a partial view invites confident wrong
answers.** During this investigation a scan for broken configuration references
validated every reference against the entity registry and reported
`sensor.proxmox_battery_level` as pointing at nothing. It is a working MQTT
sensor, reporting 77% on a five-minute cycle, driving a live automation and a
live dashboard tile. It is absent from the registry because it has no
`unique_id`.

Acting on that report would have deleted a working automation and a working
dashboard card, and the reason it nearly happened is the same reason this file
exists: a plausible mechanism, checked against a source that could not answer
the question, and reported with the same confidence either way.

`ha_dev_tools__find_dangling_references` now validates against `hass.states`, and
carries `sensor.proxmox_battery_level` as a regression fixture in
`native/tests/test_references.py`. If that test ever fails, the tool is dangerous
rather than merely wrong.

Generalisation: before trusting an absence, confirm the source is capable of
showing the thing. "Not found" from a partial index means "not in the index".
