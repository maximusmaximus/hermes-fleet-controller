---
name: home-assistant-lighting
description: Use when controlling or restoring HA lights.
---

# <sibling-node> Lighting Operations

## Snapshot before bulk changes, verify after
1. Call `mcp__homeassistant__homeassistant__GetLiveContext` with `domain: 'light'` BEFORE any bulk action, and keep the full name/state/brightness list. It is the only way to restore afterwards.
2. Act via `mcp__homeassistant__intent__HassTurnOn` / `HassTurnOff`.
3. Re-run GetLiveContext and diff against the snapshot. An `action_done` / `success` response is NOT proof of a state change.

## Pitfall: brightness on a light GROUP re-lights its members
`HassLightSet` with `brightness` on a group entity (e.g. `light.all_presence_lights`) propagates brightness to every member AND TURNS THEM ON. A call meant to "put one light back to its old level" lit up 12 additional lights.

- Before calling HassLightSet on a name, decide whether that name is a group. If it is, and you only want to change the group's own level, set the member lights individually instead.
- If a group brightness set is unavoidable, snapshot first, then re-verify and turn off the members you did not intend to light.

## Pitfall: brightness units differ
State reports `attributes.brightness` as 0-255; `HassLightSet.brightness` takes 0-100 percent. Convert with `percent = round(value / 2.55)`. Expect about one unit of rounding drift (145 vs 146) — that is a faithful restore, not an error, so do not chase it with repeat calls.

## Pitfall: unavailable entities
An entity in state `unavailable` (offline or unpowered device) accepts commands and returns no `failed` entry, but never changes state. Never report these as "turned on". List them separately as unreachable and offer to watch for them coming back.

## Pitfall: bulk turn-on changes brightness of already-on lights
Turning on a whole domain can bump the brightness of lights that were already on (observed 146 -> 218). When restoring, compare brightness too, not just on/off state.

## Reporting
- Report counts as verified facts from the post-action read, not from command responses.
- Name which lights could not be reached.
- Flag anything that differs from the requested outcome instead of quietly claiming success.
