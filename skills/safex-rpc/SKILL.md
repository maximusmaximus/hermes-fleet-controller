---
name: safex-rpc
description: SafeX blockchain operations via local daemon and wallet RPC. Send, receive, check balances, stake tokens, and manage wallets using shell scripts — no browser needed.
version: "1.1"
category: cryptocurrency
---

# SafeX RPC Skill

Interact with the SafeX blockchain directly via local `safexd` daemon and `safex-wallet-rpc` server. All operations use shell scripts that make JSON-RPC calls — no browser automation needed.

Also read repo root `AGENTS.md` for agent safety policy. Install path: `skill/README.md`. MCP alternative: `mcp/README.md` (Grok Bot / Cursor).

## Prerequisites
- `safexd` running and synced (systemd: `safexd.service`)
- `safex-wallet-rpc` running (systemd: `safex-wallet-rpc.service`)
- Scripts installed at `~/safexfantools/scripts/` or symlinked to PATH

## Sync Awareness

**Always check sync status before balance/send operations.** A wallet showing 0 balance may just be unsynced.

```bash
~/safexfantools/scripts/safex-status.sh
```

| Status | Meaning | Action |
|--------|---------|--------|
| ✅ Synced + wallet-rpc online | Ready | Proceed |
| ⏳ Syncing (85%) | Daemon catching up | Wait — balances wrong until synced |
| ❌ Wallet-RPC offline | Service down | `systemctl --user start safex-wallet-rpc` |
| ❌ Daemon unreachable | safexd down | `systemctl --user start safexd` |
| 0 balance after import | Wallet scanning blocks | Wait 1-2 min, re-check balance |
| "can't open" wallet | RPC busy or wallet missing | Check: `safex-wallet-list.sh` |

**After importing a wallet**, tell the user: *"Wallet imported — scanning blocks. Balance will update in about a minute."*

**After restarting wallet-rpc**, wait 3-5 seconds before running wallet commands.

## Danger gates (required)

| Action | Gate |
|--------|------|
| send / bulk-send / stake / unstake | `--dry-run` first; then `--confirm <confirm_token>` from the dry-run output. Do **not** use `--confirm YES` (opt-in only via `SAFEX_ALLOW_LEGACY_CONFIRM=1` for human operators). |
| wallet backup / export keys | **Blocked by default.** Only if user explicitly says **EXPORT KEYS**. Then `safex-wallet-backup.sh <name> --i-understand-secrets [--output PATH]` |
| mnemonic import | `--mnemonic-file PATH` or `--stdin` — **never** put mnemonic on argv |

There is **no default send-from wallet**. If the user does not specify a source wallet, ask which wallet to use. Do not assume `wallet1` / treasury.

## Single-flight wallet RPC

Only one wallet can be open at a time. Do not run wallet scripts in parallel. Prefer sequential commands; optional `with_wallet_lock` in `common.sh` for custom sequences.

## Prefer JSON

When available, pass `--json` (e.g. `safex-send.sh ... --json`) so results are machine-parseable. Summarize for the user in plain language.

## Quick Status Check
```bash
~/safexfantools/scripts/safex-status.sh
```

## Natural Language → Command Mapping

### Balance & Status
| User Says | Command |
|-----------|---------|
| "check balance of wallet 3" | `safex-balance.sh wallet3` |
| "check all balances" | `safex-balance.sh all` |
| "what's my wallet 1 address?" | `safex-receive.sh wallet1` |
| "daemon status" | `safex-status.sh` |
| "show mempool" | `safex-mempool.sh` |
| "show transaction history for wallet 2" | `safex-history.sh wallet2` |
| "show last 20 transactions" | `safex-history.sh wallet1 20` |

### Sending
| User Says | Command |
|-----------|---------|
| "send 1000 SFX from wallet 1 to wallet 6" | Look up wallet6 address with `safex-receive.sh wallet6`, then `safex-send.sh wallet1 <addr> 1000 sfx --dry-run`, after user OK: re-run with `--confirm <confirm_token>` from the dry-run |
| "send 500 SFT from wallet 2 to wallet 3" | Resolve address, `--dry-run`, then `--confirm <confirm_token>` with the **user-specified** source wallet |
| "send 1000 SFX to each of wallets 6-10 from wallet 1" | `safex-bulk-send.sh wallet1 1000 sfx wallet6,wallet7,wallet8,wallet9,wallet10 --dry-run` then `--confirm <confirm_token>` |

### Wallet Management
| User Says | Command |
|-----------|---------|
| "create a new wallet" | `safex-wallet-create.sh <name>` |
| "create 5 wallets" / "make 10 fund wallets" | `safex-wallet-batch-create.sh <prefix> <count> [start]` e.g. `safex-wallet-batch-create.sh fund 5` → fund-1 through fund-5 |
| "rename wallet X to Y" | `safex-wallet-rename.sh <old_name> <new_name>` (handles wallet-rpc stop/start) |
| "import wallet from mnemonic" | Write seed to 0600 temp file, then `safex-wallet-import.sh <name> --mnemonic-file /path [restore_height]`. Script handles CLI restore + wallet-rpc restart. |
| "list all wallets" | `safex-wallet-list.sh` |
| "list wallets with addresses" | `safex-wallet-list.sh --addresses` |
| "backup wallet 3" / "export keys" | Only if user said **EXPORT KEYS**: `safex-wallet-backup.sh <name> --i-understand-secrets --output ~/secure-path.txt` |
| "import all wallets from backup" | `safex-import-all.sh ~/safex-wallets-backup.txt` |
| "send 100 SFX to each of fund-1 through fund-5" | `safex-bulk-send.sh <from> 100 sfx fund-1,fund-2,fund-3,fund-4,fund-5 --dry-run` then `--confirm <confirm_token>` |
| "manage wallets" / "wallet menu" | Show wallet list, then `safex-tg-buttons.sh wallets` |

### Staking
| User Says | Command |
|-----------|---------|
| "stake 5000 SFT from wallet 1" | `safex-stake.sh wallet1 5000 --dry-run` then `--confirm <confirm_token>` |
| "unstake 2000 SFT from wallet 1" | `safex-unstake.sh wallet1 2000 --dry-run` then `--confirm <confirm_token>` |
| "show staking status" | `safex-staking-status.sh all` |

### Market & Portfolio
| User Says | Command |
|-----------|---------|
| "what's the SFX price?" / "price check" | `safex-price.sh` |
| "portfolio summary" / "show everything" | `safex-summary.sh` |
| "daily report" / "status report" | `safex-report.sh` |
| "send report to Telegram" | `safex-report.sh --telegram` |
| "network info" / "hashrate" | `safex-network.sh` |

### Contacts & Address Book
| User Says | Command |
|-----------|---------|
| "add contact bob" | `safex-contacts.sh add bob Safex...` |
| "list contacts" | `safex-contacts.sh list` |
| "what's bob's address?" | `safex-contacts.sh lookup bob` |
| "remove contact bob" | `safex-contacts.sh remove bob` |
| "send 100 SFX to bob from wallet1" | Resolve: `safex-contacts.sh resolve bob` → get address, then `safex-send.sh wallet1 <addr> 100 sfx --dry-run` |

### Monitoring
| User Says | Command |
|-----------|---------|
| "any balance changes?" / "watch" | `safex-watch.sh` |
| "tx receipt for \<hash\>" | `safex-receipt.sh <tx_hash> [wallet_name]` |

## Address Resolution

When the user references a wallet by number (e.g., "send to wallet 6"), resolve the address:
```bash
ADDRESS=$(~/safexfantools/scripts/safex-receive.sh wallet6 | grep "Address:" | awk '{print $2}')
```

When the user references a contact name (e.g., "send to bob"), resolve via contacts:
```bash
ADDRESS=$(~/safexfantools/scripts/safex-contacts.sh resolve bob)
```

## Default Behaviors

- **Source wallet**: No default — ask if unspecified
- **Default token**: If user doesn't specify SFX or SFT, default to SFX
- **Default send delay**: Bulk sends wait 5 seconds between each (configurable via DELAY env var)
- **Mutations**: Always dry-run → user OK → `--confirm <confirm_token>` (never invent a token; never default to `--confirm YES`)

## Telegram Response Formatting

When responding on Telegram, keep output **clean and scannable**:

1. **Don't paraphrase script output** — show the script's table output directly, it's already formatted for Telegram
2. **Don't add bullet points or extra prose** around balance/history data — the tables speak for themselves
3. **Keep your commentary minimal** — one short sentence before or after the data, not both
4. **After every balance, portfolio, or status response**, send interactive buttons:
   ```bash
   ~/safexfantools/scripts/safex-tg-buttons.sh balance
   ```
5. **After history responses**, send history buttons:
   ```bash
   ~/safexfantools/scripts/safex-tg-buttons.sh history
   ```
6. **When the user first messages or says "menu"**, send the main menu:
   ```bash
   ~/safexfantools/scripts/safex-tg-buttons.sh main
   ```

### Button Presets
| Preset | When to use |
|--------|-------------|
| `main` | First message, "menu", "help", "what can you do" |
| `balance` | After any balance/portfolio/wallet list response |
| `history` | After showing transaction history |
| `post` | After sends, stakes, imports, or any mutation |
| `wallets` | After "manage wallets", batch create, rename, or wallet menu |

## Script Locations

All scripts are at: `~/safexfantools/scripts/`

Quick reference:
```
safex-status.sh          # Daemon health + sync status
safex-balance.sh         # Balance check (single or all)
safex-send.sh            # Send SFX or SFT (--confirm / --dry-run / --json)
safex-bulk-send.sh       # Send to multiple wallets (--confirm / --dry-run)
safex-receive.sh         # Get wallet address
safex-history.sh         # Transaction history
safex-mempool.sh         # Mempool status
safex-stake.sh           # Stake SFT (--confirm / --dry-run)
safex-unstake.sh         # Unstake SFT (--confirm / --dry-run)
safex-staking-status.sh  # Staking info
safex-wallet-create.sh   # Create new wallet
safex-wallet-batch-create.sh # Create multiple wallets (prefix + count)
safex-wallet-rename.sh   # Rename a wallet
safex-wallet-import.sh   # Import from mnemonic file/stdin
safex-wallet-list.sh     # List all wallets
safex-wallet-backup.sh   # Export credentials (SECRET; gated)
safex-import-all.sh      # Batch import from backup file
safex-wait-tx.sh         # Wait for tx confirmation
safex-price.sh           # SFX/SFT market prices (CoinGecko)
safex-summary.sh         # Portfolio summary (balances + staking + prices)
safex-report.sh          # Daily report (--telegram to push to TG)
safex-watch.sh           # Balance change notifier (run via cron)
safex-network.sh         # Network info (hashrate, peers, chain)
safex-contacts.sh        # Address book (add/remove/lookup/resolve)
safex-receipt.sh         # Transaction receipt from tx hash
safex-tg-buttons.sh      # Telegram inline keyboard buttons (main/balance/history/post)
```

## Important Notes

1. **Use terminal tool, NOT browser tool** — All operations are shell commands
2. **Scripts handle wallet open/close** — No need to manually open/close wallets
3. **Atomic units** — Scripts handle conversion automatically (10^10 divisor)
4. **Error handling** — Scripts check for RPC errors and print clear messages
5. **Wallet names** — Wallets are named `wallet1` through `wallet10` (or any custom name)
6. **Address safety** — Always resolve addresses via scripts, never type manually
7. **See also** — `references/rpc-notes.md` and repo `AGENTS.md`

## Troubleshooting

| Issue | Fix |
|-------|-----|
| "Connection refused" | Check safexd: `systemctl --user status safexd` |
| "Wallet not found" | Import it: `safex-wallet-import.sh <name> --mnemonic-file <path> [height]` |
| "Daemon not synced" | `safex-status.sh` — wait for ✅ Synced |
| "Insufficient balance" | Check unlocked: `safex-balance.sh <wallet>` |
| "Refusing without --confirm" / legacy YES disabled | Show dry-run first, re-run with `--confirm <confirm_token>` from that dry-run |
| "Transfer error" | Wait for pending txs, retry |
| 0 balance after import | Wallet scanning blocks — wait 1-2 min, re-check |
| "can't open" wallet | wallet-rpc busy — wait a few seconds, retry |
| Wallet-RPC offline | `systemctl --user start safex-wallet-rpc` |
| Import takes too long | Use a later `restore_height` to skip old blocks |
| Services won't start | Check logs: `journalctl --user -u safexd -n 20` |
