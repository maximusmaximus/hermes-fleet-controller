---
name: home-assistant-mcp
description: Interface with <sibling-node> smart home devices, sensors, lights, switches, media players, and todo lists via the official Model Context Protocol (MCP) Server integration, strictly scoped to the local site instance.
---

# <sibling-node> MCP Integration Skill

Use this skill when interacting with, inspecting, or controlling devices, sensors, and automations in <sibling-node> via the official Model Context Protocol (MCP) Server integration (`/api/mcp`).

## Architecture & Communication
<sibling-node> exposes its Assist LLM API over Model Context Protocol using the Streamable HTTP transport.
- **Endpoint**: `http://<<sibling-node>-ip>:8123/api/mcp`
- **Protocol**: MCP JSON-RPC 2.0 over Streamable HTTP (SSE)
- **Authentication**: Bearer token via `Authorization: Bearer ${MCP_HOMEASSISTANT_API_KEY}`

## Multi-Location Isolation & Anti-Bleed Architecture
In environments where cloud integrations (e.g. Tuya, Wyze, Ring) link accounts shared across multiple physical facilities or homes:
1. **Local Site Boundary**: This instance is strictly scoped to the local physical site (**Magnolia**).
2. **Server-Side Hardening**: The <sibling-node> MCP server at `/api/mcp` is hardened via `exposed_entities` configuration (`expose_new: false`) so only local site entities are served to MCP.
3. **Agent Scope Enforcement**:
   - The agent must NEVER actuate, target, or query devices belonging to external sites .
   - Broad commands (such as "turn on all lights" or "turn off the lights") must ALWAYS be resolved exclusively to local site groups (e.g., `All Presence Lights`, `Bath Lights`, `Bedroom Lights`, `Kitchen Lights`, `Couch Lights`, `Stove Lights`).
   - Never issue global unconstrained entity calls.

## Core Operational Methodology: Context-First
Always use a **Context-First** workflow:
1. **Discover Current State**: Do not guess entity IDs, names, or device states. Call `homeassistant__GetLiveContext` to get a real-time snapshot of the home's devices, domains, areas, and current sensor readings.
2. **Filter & Match**: Search the context snapshot for the user's target device or area (e.g. `McFridge Temperature`, `ParkingLot Humidity`, `Kitchen Lights`, `Bedroom Lights`).
3. **Site Verification**: Verify the candidate device is an authorized local site device before executing actions.
4. **Execute Action**: Call the appropriate tool (`intent__HassTurnOn`, `intent__HassTurnOff`, `light__HassLightSet`, etc.) with the exact name or area from the context.
5. **Confirm & Report**: State clearly what action was taken or report the requested telemetry concisely with units (e.g. `°F`, `%`).

## Available Tools

### Context & Information
- `homeassistant__GetLiveContext`: Provides real-time information about the CURRENT state, value, or mode of devices, sensors, entities, or areas. Optional filters: `name`, `domain`, `area`.
- `llm__GetDateTime`: Provides the current date and time from the <sibling-node> server.

### Device Control
- `intent__HassTurnOn`: Turns on, opens, or activates a device or entity. (For locks, performs a 'lock' action). Arguments: `name` (string), `area` (string), `device_class` (string).
- `intent__HassTurnOff`: Turns off, closes, or deactivates a device or entity. (For locks, performs an 'unlock' action). Arguments: `name` (string), `area` (string), `device_class` (string).
- `light__HassLightSet`: Sets brightness percentage (0-100) or color of a light. Arguments: `name`, `brightness`, `color`.

### Media & Entertainment
- `media_player__HassMediaPause` / `media_player__HassMediaUnpause`
- `media_player__HassMediaNext` / `media_player__HassMediaPrevious`
- `media_player__HassSetVolume` / `media_player__HassSetVolumeRelative`
- `media_player__HassMediaPlayerMute` / `media_player__HassMediaPlayerUnmute`
- `media_player__HassMediaSearchAndPlay`

### Productivity & Timers
- `todo__get_items`: Query items on a to-do list (e.g. `Shopping list`).
- `todo__HassListAddItem`: Add an item to a to-do list.
- `todo__HassListCompleteItem`: Mark an item completed.
- `todo__HassListRemoveItem`: Remove items from a to-do list.
- `intent__HassCancelAllTimers`: Cancels all active timers.

### Notifications
- `assist_satellite__HassBroadcast`: Broadcast a spoken message through satellite speakers in the home.

## Safety Directives
- Never toggle security or lock devices unless explicitly commanded.
- Never repeatedly spam command endpoints if a device reports `unavailable`.
- Always report temperature and humidity telemetry with their respective measurement units.
- Never manipulate devices at external facilities.