---
name: telegram-interface
description: Best practices for Telegram bot interactions, including mobile-optimized data formatting, emoji visual anchors, card structures, persistent bottom menus ("kitchen sink" reply keyboards), and interactive inline buttons using the clarify tool or telegram-menu helper.
---

# Telegram Interface & Interaction Skill

Use this skill whenever communicating with users over Telegram or generating responses destined for Telegram delivery.

---

## 1. Telegram Message Presentation Best Practices

Telegram messages are primarily viewed on mobile devices. Long walls of unformatted plain text are difficult to parse and navigate. Follow these presentation standards:

### A. Visual Hierarchy & Formatting
- **Emoji Visual Anchors:** Use intuitive emoji at the start of sections and items:
  - 💡 Lights & Fixtures | ⚡ Power & Switches | 🌡️ Temperature | 💧 Humidity
  - 🛋️ Living Room | 🍳 Kitchen | 🛏️ Bedroom | 🚿 Bathroom | 🚗 ParkingLot
  - 🟢 Active / On | ⭕ Inactive / Off | ⚠️ Alert / Warning | 📊 Status / Summary
- **Card-Style Layouts:** Group related data into short, 2–4 line "cards" separated by clean whitespace:
  ```markdown
  🍳 *Kitchen Zone*
  • *Lights:* `on` (23% brightness)
  • *Stove Light:* `on` (Task mode)
  • *Undercabinet:* `on` (Cync Full Color)
  ```
- **Key-Value Metrics:** Format readings cleanly:
  - `• *Temperature:* 72.3 °F`
  - `• *Humidity:* 54.0 %`
  - `• *Status:* Normal`
- **Markdown Discipline:** Use bold (`*text*`), monospace (` `code` `), and bullet points (`•`). Keep messages under 2,000 characters for optimal readability.

### B. Clean Line Breaks & Real Newlines (Strict Escape Rule)
- **Zero Literal Slashes (`\n`):** NEVER output literal `\n`, `\r`, or `\t` escape characters in Telegram text, card bodies, subtitles, or `clarify` prompts.
  - ❌ **Forbidden:** `"Current status: 3 fixtures active\n\nTap a quick action button below to adjust:"` (displays literal `\n\n` characters on user mobile screens).
  - ✅ **Required:** Use genuine multi-line formatting with actual line breaks:
    ```markdown
    Current status: 3 fixtures active (Stove, Main, Undercabinet)

    Tap a quick action button below to adjust:
    ```
- **Shell & Dispatcher Safety:** The `telegram-menu.py` helper utility automatically unescapes any shell-encoded `\n` sequences into genuine line breaks before transmitting payloads to the Telegram Bot API, guaranteeing clean paragraph rendering even when called from CLI or shell environments.

---

## 2. Interactive Buttons for Options (The Button-First Rule)

**Never force users to type out entity names, zone names, or commands on a mobile keyboard when choices can be presented as clickable buttons.**

Whenever the user:
1. Asks an ambiguous or broad question (e.g. *"What lights can I change?"*, *"Show me zones"*, *"Check status"*).
2. Needs to select a specific zone, room, or device.
3. Needs to choose an action (Turn On, Turn Off, Set Brightness, Change Scene).
4. Needs to confirm a change or decide between options.

### How to Render Buttons: The `clarify` Tool
Hermes translates the `clarify` tool directly into Telegram **Inline Keyboards (clickable buttons)** when choices are provided.

#### Calling Syntax:
```json
{
  "questions": [
    {
      "question": "Which zone would you like to inspect or control?",
      "choices": [
        "🍳 Kitchen",
        "🛏️ Bedroom",
        "🚿 Bathroom",
        "🛋️ Living Room",
        "🚗 ParkingLot",
        "📊 Full Home Status"
      ],
      "multi_select": false
    }
  ]
}
```

#### In Telegram, This Renders As:
```
Which zone would you like to inspect or control?
[ 🍳 Kitchen ]  [ 🛏️ Bedroom ]
[ 🚿 Bathroom ] [ 🛋️ Living Room ]
[ 🚗 ParkingLot ] [ 📊 Full Home Status ]
[ ✏️ Other (type answer) ]
```
When the user taps a button, Telegram automatically submits their selection back to the agent without requiring typing.

---

## 3. The Persistent "Kitchen Sink" Bottom Navigation Menu

To provide instant, 1-tap access to all primary <sibling-node> MCP capabilities without scrolling through conversation history, agents configure a persistent bottom Reply Keyboard (`ReplyKeyboardMarkup`, `is_persistent: true`).

```
┌─────────────────────────────────────────────────────────────┐
│ 💡 Lights & Zones          │ 🌡️ Climate & Sensors           │
├────────────────────────────┼────────────────────────────────┤
│ ⚡ Power & Switches         │ 📋 Shopping List & To-Do       │
├────────────────────────────┼────────────────────────────────┤
│ 🎬 Scenes & Presets        │ 🛡️ MCP Security & Health        │
└─────────────────────────────────────────────────────────────┘
```

### Feature Pillars & Interactive Sub-Menus

When the user taps any persistent button, the agent responds with a formatted status card and dedicated inline action buttons:

#### 1. 💡 Lights & Zones
- **Underlying MCP Tools:** `homeassistant__GetLiveContext`, `light__HassLightSet`, `intent__HassTurnOn`, `intent__HassTurnOff`.
- **Card:** Displays current power and brightness states of all presence groups and fixtures.
- **Inline Action Buttons:**
  - `[ 🍳 Kitchen ] [ 🛏️ Bedroom ]`
  - `[ 🚿 Bathroom ] [ 🛋️ Living Room ]`
  - `[ 🚗 ParkingLot ] [ 💡 All Lights ON ]`
  - `[ 🔆 Dim All: 30% ] [ ⭕ All Lights OFF ]`

#### 2. 🌡️ Climate & Sensors
- **Underlying MCP Tools:** `homeassistant__GetLiveContext`.
- **Card:** Real-time readings from BLE host bridge and climate sensors (e.g. McFridge: `37.3 °F`, ParkingLot: `81.6 °F`, W100: `72.3 °F`).
- **Inline Action Buttons:**
  - `[ 🔄 Refresh Telemetry ] [ ❄️ McFridge Detail ]`
  - `[ 🚗 ParkingLot Detail ] [ 🏠 Indoor Climate ]`

#### 3. ⚡ Power & Switches
- **Underlying MCP Tools:** `homeassistant__GetLiveContext`, `intent__HassTurnOn`, `intent__HassTurnOff`.
- **Card:** Real-time state of physical switches (`switch.bed`, `switch.monitor`).
- **Inline Action Buttons:**
  - `[ 🛏️ Toggle Bed Switch ] [ 🖥️ Toggle Monitor ]`
  - `[ ⚡ Turn Both ON ] [ 💤 Turn Both OFF ]`

#### 4. 📋 Shopping List & To-Do
- **Underlying MCP Tools:** `todo__get_items`, `todo__HassListAddItem`, `todo__HassListCompleteItem`.
- **Card:** Current uncompleted items on `todo.shopping_list`.
- **Inline Action Buttons:**
  - `[ ➕ Add Item ] [ 📋 View Full List ]`
  - `[ ✔️ Complete Items ] [ 🧹 Clear Completed ]`

#### 5. 🎬 Scenes & Presets
- **Underlying MCP Tools:** `homeassistant__GetLiveContext`, scene activation.
- **Card:** Active presets and presence lighting modes.
- **Inline Action Buttons:**
  - `[ 🟢 Green Lights State ] [ 🔴 Red Night Mode ]`
  - `[ 🚨 Kill Switch State ] [ ☀️ Neutral White (100%) ]`

#### 6. 🛡️ MCP Security & Health
- **Underlying MCP Tools:** Isolation Gatekeeper inspection.
- **Card:** Active site isolation verification (32 Magnolia entities exposed, 58 external devices blocked, `expose_new: false`, VM and BLE bridge operational).
- **Inline Action Buttons:**
  - `[ 🔍 Audit Roster ] [ 🔄 Sync Entities ]`
  - `[ 📖 Scope Policy ] [ 🤖 Fleet Status ]`

---

## 4. Dispatching Menus via Helper Utility (`telegram-menu.py`)

Every agent container has access to the menu dispatcher utility at `/opt/fleet/skills/telegram-interface/telegram-menu.py`.

### A. Pinned Persistent Navigation Bar
```bash
python3 /opt/fleet/skills/telegram-interface/telegram-menu.py \
  --chat-id "<chat_id>" \
  --title "🌿 *<sibling-node> Magnolia — Master Control Panel*" \
  --subtitle "Tap any button below for instant 1-tap access to core features:" \
  --persistent
```

### B. Contextual Inline Button Cards
```bash
python3 /opt/fleet/skills/telegram-interface/telegram-menu.py \
  --chat-id "<chat_id>" \
  --title "🍳 *Kitchen Zone — Device Controls*" \
  --subtitle "Select an action to apply to the Kitchen fixtures:" \
  --button "💡 Turn On (100%)" \
  --button "🔆 Dim to 30%" \
  --button "🔴 Night Red" \
  --button "⭕ Turn Off" \
  --button "🔙 Back to Zones"
```
