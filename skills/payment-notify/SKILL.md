---
name: payment-notify
description: Automatic Telegram notifications when a wallet receives incoming payments
category: monitoring
version: "1.0"
---

# Payment Notification Skill

Automatically detects incoming balance changes and sends Telegram alerts.

## How It Works

The `safex-payment-notify.sh` script runs every 5 minutes via the watchdog timer. It:

1. Reads `~/.safex/watch/notify-config.json` to see which wallets have notifications enabled
2. Compares current balance snapshot against the last-notified snapshot
3. If any opted-in wallet's SFX or SFT balance **increased**, sends a Telegram notification:
   ```
   💰 Payment received!
   <b>wallet-name</b>: +50.0000 SFX
   New balance: 151.0000 SFX
   ```
4. Saves the current snapshot for next comparison

## Configuration

Per-wallet notification preferences are stored in `~/.safex/watch/notify-config.json`:

```json
{
  "wallets": {
    "bear": true,
    "wolf": false,
    "donations": true
  }
}
```

- `true` = notifications ON for this wallet
- `false` = notifications OFF
- Missing wallet = no notifications (opt-in)

Users configure this through the Dashboard → Wallets → Import/Create flow (checkbox).

## Agent Behavior

When users ask about payment notifications or received payments:
- Check `~/.safex/watch/notify-config.json` to see which wallets have notifications enabled
- Run `safex-watch.sh` to show recent balance changes
- Inform them that automatic Telegram alerts are sent every 5 minutes for opted-in wallets

## Dependencies

- `common.sh` (provides `tg_send`, `from_atomic`, env vars)
- `jq` for JSON parsing
- `SAFEX_TG_BOT_TOKEN` and `SAFEX_TG_CHAT_ID` must be set in `safex.env`
- Balance snapshots at `~/.safex/watch/balance-snapshot.json` (maintained by `safex-watch.sh`)
