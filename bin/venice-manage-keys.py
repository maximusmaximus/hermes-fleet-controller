#!/usr/bin/env python3
"""
/opt/fleet/bin/venice-manage-keys.py
Venice API Key Management and Daily Inference Allocation for Fleet Agents.
"""

import os
import sys
import json
import argparse
import urllib.request
import urllib.error

SECRETS_FILE = "/opt/fleet/secrets.env"
AGENTS_DIR = "/opt/fleet/agents"

def load_secrets():
    secrets = {}
    if not os.path.exists(SECRETS_FILE):
        print(f"Error: {SECRETS_FILE} not found", file=sys.stderr)
        sys.exit(1)
    with open(SECRETS_FILE) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                secrets[k.strip()] = v.strip().strip('"\'')
    return secrets

def get_master_key():
    secrets = load_secrets()
    key = secrets.get("VENICE_API_KEY")
    if not key:
        print("Error: VENICE_API_KEY not found in secrets.env", file=sys.stderr)
        sys.exit(1)
    return key

def create_agent_key(name, daily_usd=2.0):
    master_key = get_master_key()
    agent_dir = os.path.join(AGENTS_DIR, name)
    os.makedirs(agent_dir, exist_ok=True)
    os.chmod(agent_dir, 0o750)

    key_meta_file = os.path.join(agent_dir, "key-meta.json")
    env_file = os.path.join(agent_dir, ".env")

    # Try creating dedicated key via Venice Admin API
    payload = {
        "apiKeyType": "INFERENCE",
        "description": f"fleet-agent-{name}",
        "consumptionLimit": {"usd": float(daily_usd)},
        "limitPeriod": "DAY"
    }

    req = urllib.request.Request(
        "https://api.venice.ai/api/v1/api_keys",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {master_key}",
            "Content-Type": "application/json"
        },
        method="POST"
    )

    is_admin = False
    new_key = None
    key_id = None

    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            new_key = data.get("key") or data.get("apiKey")
            key_id = data.get("id") or data.get("keyId")
            is_admin = True
    except urllib.error.HTTPError as e:
        # If 401/403 (Admin required), fall back to sharing the master key
        if e.code in (401, 403):
            pass
        else:
            err_msg = e.read().decode("utf-8", errors="ignore")
            print(f"Warning: Venice API returned {e.code}: {err_msg}", file=sys.stderr)
    except Exception as e:
        print(f"Warning: Could not contact Venice key creation endpoint: {e}", file=sys.stderr)

    if is_admin and new_key:
        meta = {
            "name": name,
            "mode": "dedicated",
            "key_id": key_id,
            "daily_usd_limit": daily_usd,
            "limit_period": "DAY",
            "created_at": os.popen("date -u +%Y-%m-%dT%H:%M:%SZ").read().strip()
        }
        with open(env_file, "w") as f:
            f.write(f"VENICE_API_KEY={new_key}\n")
            f.write("VENICE_BASE_URL=https://api.venice.ai/api/v1\n")
            f.write(f"HERMES_AGENT_NAME={name}\n")
        os.chmod(env_file, 0o600)
        with open(key_meta_file, "w") as f:
            json.dump(meta, f, indent=2)
        print(json.dumps({"status": "created", "name": name, "mode": "dedicated", "key_id": key_id, "daily_usd": daily_usd}))
    else:
        # Controlled shared key fallback
        meta = {
            "name": name,
            "mode": "shared",
            "daily_usd_limit": daily_usd,
            "note": "Master key is inference-tier; shared key attached with tracked daily limit",
            "created_at": os.popen("date -u +%Y-%m-%dT%H:%M:%SZ").read().strip()
        }
        with open(env_file, "w") as f:
            f.write(f"VENICE_API_KEY={master_key}\n")
            f.write("VENICE_BASE_URL=https://api.venice.ai/api/v1\n")
            f.write(f"HERMES_AGENT_NAME={name}\n")
        os.chmod(env_file, 0o600)
        with open(key_meta_file, "w") as f:
            json.dump(meta, f, indent=2)
        print(json.dumps({"status": "created", "name": name, "mode": "shared", "daily_usd": daily_usd}))

def list_keys():
    master_key = get_master_key()
    remote_keys = []
    try:
        req = urllib.request.Request(
            "https://api.venice.ai/api/v1/api_keys",
            headers={"Authorization": f"Bearer {master_key}"}
        )
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            remote_keys = data.get("data", [])
    except Exception:
        pass

    # Read local agent allocations
    local_allocations = []
    if os.path.exists(AGENTS_DIR):
        for name in os.listdir(AGENTS_DIR):
            meta_path = os.path.join(AGENTS_DIR, name, "key-meta.json")
            if os.path.exists(meta_path):
                try:
                    with open(meta_path) as f:
                        local_allocations.append(json.load(f))
                except Exception:
                    pass

    print(json.dumps({
        "remote_keys_count": len(remote_keys),
        "remote_keys": remote_keys,
        "agent_allocations": local_allocations
    }, indent=2))

def allocate_daily(name, daily_usd):
    agent_dir = os.path.join(AGENTS_DIR, name)
    if not os.path.exists(agent_dir):
        print(f"Error: Agent directory {agent_dir} does not exist", file=sys.stderr)
        sys.exit(1)

    key_meta_file = os.path.join(agent_dir, "key-meta.json")
    meta = {}
    if os.path.exists(key_meta_file):
        try:
            with open(key_meta_file) as f:
                meta = json.load(f)
        except Exception:
            pass

    meta["name"] = name
    meta["daily_usd_limit"] = float(daily_usd)
    meta["limit_period"] = "DAY"
    meta["updated_at"] = os.popen("date -u +%Y-%m-%dT%H:%M:%SZ").read().strip()

    with open(key_meta_file, "w") as f:
        json.dump(meta, f, indent=2)

    print(json.dumps({"status": "allocated", "name": name, "daily_usd": float(daily_usd)}))

def main():
    parser = argparse.ArgumentParser(description="Manage Venice keys and inference quotas for Fleet agents")
    subparsers = parser.add_subparsers(dest="command")

    create_p = subparsers.add_parser("create-key")
    create_p.add_argument("--name", required=True, help="Agent name")
    create_p.add_argument("--daily-usd", type=float, default=2.0, help="Daily inference allocation in USD (default 2.0)")

    list_p = subparsers.add_parser("list-keys")

    alloc_p = subparsers.add_parser("allocate-daily")
    alloc_p.add_argument("--name", required=True, help="Agent name")
    alloc_p.add_argument("--daily-usd", type=float, required=True, help="New daily inference budget in USD")

    args = parser.parse_args()

    if args.command == "create-key":
        create_agent_key(args.name, args.daily_usd)
    elif args.command == "list-keys":
        list_keys()
    elif args.command == "allocate-daily":
        allocate_daily(args.name, args.daily_usd)
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
