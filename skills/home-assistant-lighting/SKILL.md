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

## Pitfall: no transition/duration parameter exists
`HassLightSet` accepts only `brightness` (0-100 percent), `color`, and `temperature` — there is NO `transition`/duration argument. Nothing sent through this tool can fade on its own; a timed fade must be built from repeated writes, or configured HA-side (script/automation with `transition:`), which this tool surface cannot create.

## Smoothing an abrupt fade (staged descent)
- Write to the individual FIXTURES, never the group. A group brightness write propagates to every member in a single write, so all members snap on the same clock (and already-off members get re-lit).
- Staircase down, e.g. 100 -> 75 -> 50 -> 35 -> 25 -> target. Pacing comes from MCP call latency (~1-3 s per call), so expect tens of seconds, not sub-second smoothness.
- Stop at the target level; do not ramp to 1% then `turn_off`. On/off is binary and, because brightness maps to 0-255 (1% = 3), the bottom steps are the perceptually largest jump — the tail is where fades look broken.
- Budget ~6 calls per fixture against cron's 3-minute hard interrupt; a 3-fixture kitchen staircase fits, a whole-house one does not.
- Diagnosing an existing fade: sample GetLiveContext every few seconds while it runs and record the brightness sequence to find which step actually snaps, instead of guessing.

## Reporting
- Report counts as verified facts from the post-action read, not from command responses.
- Name which lights could not be reached.
- Flag anything that differs from the requested outcome instead of quietly claiming success.
