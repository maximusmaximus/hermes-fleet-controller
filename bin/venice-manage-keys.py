#!/usr/bin/env python3
"""
/opt/fleet/bin/venice-manage-keys.py
Venice API Key Management and Daily Inference Allocation for Fleet Agents.
Enforces dedicated sub-keys with $2/day limits (limitPeriod: "EPOCH").
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


def revoke_remote_key(key_id):
    """Revoke/delete an existing Venice key by ID."""
    if not key_id:
        return False
    master_key = get_master_key()
    try:
        req = urllib.request.Request(
            f"https://api.venice.ai/api/v1/api_keys?id={key_id}",
            headers={"Authorization": f"Bearer {master_key}"},
            method="DELETE"
        )
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("success", False)
    except Exception as e:
        print(f"Notice: Could not revoke remote key {key_id}: {e}", file=sys.stderr)
        return False


def update_env_file(env_file, new_key, agent_name):
    """Update or append VENICE_API_KEY in .env while preserving other variables."""
    env_vars = {}
    if os.path.exists(env_file):
        with open(env_file, "r") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env_vars[k.strip()] = v.strip()

    env_vars["VENICE_API_KEY"] = new_key
    env_vars["VENICE_BASE_URL"] = "https://api.venice.ai/api/v1"
    env_vars["HERMES_AGENT_NAME"] = agent_name

    with open(env_file, "w") as f:
        for k, v in env_vars.items():
            f.write(f"{k}={v}\n")
    os.chmod(env_file, 0o600)


def create_agent_key(name, daily_usd=2.0):
    master_key = get_master_key()
    agent_dir = os.path.join(AGENTS_DIR, name)
    os.makedirs(agent_dir, exist_ok=True)
    os.chmod(agent_dir, 0o750)

    key_meta_file = os.path.join(agent_dir, "key-meta.json")
    env_file = os.path.join(agent_dir, ".env")

    # If an old dedicated key existed, revoke it on Venice to avoid leaks
    old_key_id = None
    if os.path.exists(key_meta_file):
        try:
            with open(key_meta_file) as f:
                old_meta = json.load(f)
                old_key_id = old_meta.get("key_id")
        except Exception:
            pass

    if old_key_id:
        revoke_remote_key(old_key_id)

    # Create dedicated key via Venice Admin API with EPOCH daily budget
    payload = {
        "apiKeyType": "INFERENCE",
        "description": f"fleet-agent-{name}",
        "consumptionLimit": {"usd": float(daily_usd)},
        "limitPeriod": "EPOCH"
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

    new_key = None
    key_id = None

    try:
        with urllib.request.urlopen(req) as resp:
            resp_data = json.loads(resp.read().decode("utf-8"))
            kdata = resp_data.get("data", resp_data)
            new_key = kdata.get("apiKey") or kdata.get("key")
            key_id = kdata.get("id") or kdata.get("keyId")
    except urllib.error.HTTPError as e:
        err_msg = e.read().decode("utf-8", errors="ignore")
        print(f"Error: Venice API returned {e.code}: {err_msg}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: Could not contact Venice key creation endpoint: {e}", file=sys.stderr)
        sys.exit(1)

    if not new_key:
        print("Error: Venice did not return a valid apiKey in response", file=sys.stderr)
        sys.exit(1)

    meta = {}
    if os.path.exists(key_meta_file):
        try:
            with open(key_meta_file) as f:
                meta = json.load(f)
        except Exception:
            pass

    meta.update({
        "name": name,
        "mode": "dedicated",
        "key_id": key_id,
        "daily_usd_limit": float(daily_usd),
        "limit_period": "EPOCH",
        "created_at": os.popen("date -u +%Y-%m-%dT%H:%M:%SZ").read().strip()
    })

    # Update agent .env safely preserving other variables
    update_env_file(env_file, new_key, name)

    with open(key_meta_file, "w") as f:
        json.dump(meta, f, indent=2)

    result = {
        "status": "created",
        "name": name,
        "mode": "dedicated",
        "key_id": key_id,
        "daily_usd": float(daily_usd),
        "limit_period": "EPOCH"
    }
    print(json.dumps(result))
    return result


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
    except Exception as e:
        print(f"Warning: Could not list remote keys: {e}", file=sys.stderr)

    # Read local agent allocations
    local_allocations = []
    if os.path.exists(AGENTS_DIR):
        for name in sorted(os.listdir(AGENTS_DIR)):
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

    key_id = meta.get("key_id")
    # If dedicated key exists on Venice, update its consumptionLimit
    if key_id:
        master_key = get_master_key()
        try:
            payload = {
                "id": key_id,
                "consumptionLimit": {"usd": float(daily_usd)},
                "limitPeriod": "EPOCH"
            }
            req = urllib.request.Request(
                "https://api.venice.ai/api/v1/api_keys",
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {master_key}",
                    "Content-Type": "application/json"
                },
                method="PATCH"
            )
            with urllib.request.urlopen(req) as resp:
                pass
        except Exception as e:
            print(f"Warning: Could not patch limit on Venice remote key: {e}", file=sys.stderr)

    meta["name"] = name
    meta["daily_usd_limit"] = float(daily_usd)
    meta["limit_period"] = "EPOCH"
    meta["updated_at"] = os.popen("date -u +%Y-%m-%dT%H:%M:%SZ").read().strip()

    with open(key_meta_file, "w") as f:
        json.dump(meta, f, indent=2)

    print(json.dumps({"status": "allocated", "name": name, "daily_usd": float(daily_usd), "limit_period": "EPOCH"}))


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

    delete_p = subparsers.add_parser("revoke-key")
    delete_p.add_argument("--id", required=True, help="Venice Key ID to delete")

    args = parser.parse_args()

    if args.command == "create-key":
        create_agent_key(args.name, args.daily_usd)
    elif args.command == "list-keys":
        list_keys()
    elif args.command == "allocate-daily":
        allocate_daily(args.name, args.daily_usd)
    elif args.command == "revoke-key":
        ok = revoke_remote_key(args.id)
        print(json.dumps({"status": "revoked" if ok else "failed", "key_id": args.id}))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
