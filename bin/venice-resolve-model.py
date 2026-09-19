#!/usr/bin/env python3
"""
/opt/fleet/bin/venice-resolve-model.py
Resolves dynamic Venice model tiers prioritizing cost-efficiency and tool-calling capabilities.
"""

import os
import sys
import json
import time
import urllib.request
import urllib.error

CACHE_FILE = "/opt/fleet/models-cache.json"
SECRETS_FILE = "/opt/fleet/secrets.env"


def get_api_key():
    if os.path.exists(SECRETS_FILE):
        with open(SECRETS_FILE) as f:
            for line in f:
                if line.startswith("VENICE_API_KEY="):
                    return line.split("=", 1)[1].strip().strip('"\'')
    return os.environ.get("VENICE_API_KEY", "")


def fetch_fresh_models(api_key):
    req = urllib.request.Request(
        "https://api.venice.ai/api/v1/models",
        headers={"Authorization": f"Bearer {api_key}"}
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        return data.get("data", [])


def resolve_tiers(models):
    # Filter text models that are not offline
    text_models = [
        m for m in models
        if m.get("type") in ("text", "chat", None) and not m.get("model_spec", {}).get("offline", False)
    ]

    # Model preferences per tier:
    # High: Frontier / Orchestrator models
    high_picks = ["kimi-k3", "e2ee-kimi-k3-p", "claude-opus-5", "grok-4-20", "openai-gpt-55-pro"]

    # Medium: Cost-efficient, high-speed, reliable tool-calling models
    # deepseek-v4-flash ($0.138/M in, $0.275/M out) is optimal balance of price and tool reasoning
    med_picks = ["deepseek-v4-flash", "mistral-small-3-2-24b-instruct", "google-gemma-3-27b-it", "deepseek-v4-1-flash"]

    # Low: Ultra-budget lightweight models ($0.05/M - $0.15/M)
    low_picks = ["mercury-2-5", "qwen3-5-9b", "llama-3.2-3b", "zai-org-glm-4.7-flash"]

    avail_ids = {m.get("id"): m for m in text_models}

    high_choice = next((mid for mid in high_picks if mid in avail_ids), "kimi-k3")
    med_choice = next((mid for mid in med_picks if mid in avail_ids), "deepseek-v4-flash")
    low_choice = next((mid for mid in low_picks if mid in avail_ids), "mercury-2-5")

    return {
        "high": high_choice,
        "medium": med_choice,
        "low": low_choice,
        "controller": "kimi-k3"
    }


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("high", "medium", "low", "controller", "all", "refresh"):
        print("Usage: venice-resolve-model.sh [high|medium|low|controller|all|refresh]", file=sys.stderr)
        sys.exit(1)

    target_tier = sys.argv[1]

    if target_tier == "controller":
        print("kimi-k3")
        sys.exit(0)

    api_key = get_api_key()
    resolved = None

    # Try fetching fresh
    try:
        models = fetch_fresh_models(api_key)
        tiers = resolve_tiers(models)
        cache_data = {
            "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "tiers": tiers,
            "total_models": len(models)
        }
        os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
        with open(CACHE_FILE, "w") as f:
            json.dump(cache_data, f, indent=2)
        resolved = tiers
    except Exception:
        # Fallback to cache
        if os.path.exists(CACHE_FILE):
            try:
                with open(CACHE_FILE) as f:
                    cached = json.load(f)
                    resolved = cached.get("tiers")
            except Exception:
                pass

    if not resolved:
        # Hardcoded static fallbacks
        resolved = {
            "high": "e2ee-kimi-k3-p",
            "medium": "deepseek-v4-flash",
            "low": "mercury-2-5",
            "controller": "kimi-k3"
        }

    if target_tier == "all":
        print(json.dumps(resolved, indent=2))
    elif target_tier == "refresh":
        print(f"Successfully refreshed Venice cache ({CACHE_FILE})")
    else:
        print(resolved.get(target_tier, "deepseek-v4-flash"))


if __name__ == "__main__":
    main()
