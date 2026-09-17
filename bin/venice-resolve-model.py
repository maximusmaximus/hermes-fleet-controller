#!/usr/bin/env python3
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
    # Filter text models
    text_models = [m for m in models if m.get("type") in ("text", "chat", None)]

    # Scoring bands
    high_candidates = []
    med_candidates = []
    low_candidates = []

    for m in text_models:
        mid = m.get("id", "").lower()
        created = m.get("created", 0)
        # Check privacy preference in model_spec
        spec = m.get("model_spec") or {}
        privacy = spec.get("privacy", "").lower()
        privacy_boost = 100000 if "private" in privacy else 0

        # Tier classification
        # High: kimi-k3, claude-opus, grok flagship, gpt-5, gpt-6, frontier
        if any(x in mid for x in ["kimi-k3", "claude-opus", "grok-4", "grok-code", "gpt-5", "gpt-6"]):
            high_candidates.append((created + privacy_boost, m["id"]))

        # Medium: deepseek, kimi-k2, glm, qwen-3-8, qwen3, minimax, venice-large, mistral
        elif any(x in mid for x in ["deepseek", "kimi-k2", "glm", "qwen-3-8", "qwen3-6", "qwen3-5", "qwen-3-7", "minimax", "venice-large", "gemini-3-flash"]):
            med_candidates.append((created + privacy_boost, m["id"]))

        # Low: llama-3.2-3b, qwen3-4b, venice-small, mercury, cheap/fast models
        elif any(x in mid for x in ["3b", "4b", "small", "mercury", "nano", "flash-heretic", "uncensored"]):
            low_candidates.append((created + privacy_boost, m["id"]))
        else:
            # Default fallbacks
            if "large" in mid or "70b" in mid:
                med_candidates.append((created + privacy_boost, m["id"]))
            else:
                low_candidates.append((created + privacy_boost, m["id"]))

    # Sort each tier by score (created timestamp + privacy boost) descending
    high_candidates.sort(key=lambda x: x[0], reverse=True)
    med_candidates.sort(key=lambda x: x[0], reverse=True)
    low_candidates.sort(key=lambda x: x[0], reverse=True)

    high_pick = high_candidates[0][1] if high_candidates else "kimi-k3"
    med_pick = med_candidates[0][1] if med_candidates else "qwen-3-8-flash"
    low_pick = low_candidates[0][1] if low_candidates else "llama-3.2-3b"

    return {
        "high": high_pick,
        "medium": med_pick,
        "low": low_pick,
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
    except Exception as e:
        # Fallback to cache
        if os.path.exists(CACHE_FILE):
            try:
                with open(CACHE_FILE) as f:
                    cached = json.load(f)
                    resolved = cached.get("tiers")
            except Exception:
                pass

    if not resolved:
        print("Error: Could not resolve models from Venice API and no valid cache found.", file=sys.stderr)
        sys.exit(1)

    if target_tier == "all":
        print(json.dumps(resolved, indent=2))
    elif target_tier == "refresh":
        print(f"Successfully refreshed Venice cache ({CACHE_FILE})")
    else:
        print(resolved.get(target_tier, "kimi-k3"))

if __name__ == "__main__":
    main()
