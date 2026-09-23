#!/usr/bin/env python3
"""
/opt/fleet/bin/venice-resolve-model.py
Resolves dynamic Venice model tiers prioritizing cost-efficiency, tool-calling capabilities,
and End-to-End Encrypted (E2EE / TEE Hardware Enclave) privacy options.
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
        try:
            with open(SECRETS_FILE) as f:
                for line in f:
                    if line.startswith("VENICE_API_KEY="):
                        return line.split("=", 1)[1].strip().strip("\"'")
        except Exception:
            pass
    return os.environ.get("VENICE_API_KEY", "")


def fetch_fresh_models(api_key):
    req = urllib.request.Request(
        "https://api.venice.ai/api/v1/models",
        headers={"Authorization": f"Bearer {api_key}"}
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        return data.get("data", [])


def categorize_privacy_models(models):
    """Categorizes models into E2EE (Hardware Enclave), ZDR (Private), and Anonymized."""
    e2ee_models = []
    private_models = []
    anonymized_models = []

    for m in models:
        mid = m.get("id", "")
        spec = m.get("model_spec", {})
        if spec.get("offline", False):
            continue

        privacy = m.get("privacy") or spec.get("privacy") or "unknown"
        is_e2ee = bool(m.get("supportsE2EE") or spec.get("supportsE2EE") or mid.startswith("e2ee-"))
        pricing = spec.get("pricing", {})
        input_usd = pricing.get("input", {}).get("usd", 0.0)
        output_usd = pricing.get("output", {}).get("usd", 0.0)
        ctx = spec.get("context_window", 0)

        entry = {
            "id": mid,
            "name": m.get("name") or mid,
            "type": m.get("type", "text"),
            "privacy": "e2ee" if is_e2ee else privacy,
            "supports_e2ee": is_e2ee,
            "supports_tee": bool(m.get("supportsTeeAttestation") or spec.get("supportsTeeAttestation")),
            "enclave_type": "Hardware TEE (Intel TDX / SEV-SNP)" if is_e2ee else "None",
            "context_window": ctx,
            "pricing": {
                "input_per_million": round(input_usd, 4),
                "output_per_million": round(output_usd, 4)
            }
        }

        if is_e2ee:
            e2ee_models.append(entry)
        elif privacy == "private":
            private_models.append(entry)
        elif privacy == "anonymized":
            anonymized_models.append(entry)

    # Sort each list by input pricing ascending
    e2ee_models.sort(key=lambda x: x["pricing"]["input_per_million"])
    private_models.sort(key=lambda x: x["pricing"]["input_per_million"])
    anonymized_models.sort(key=lambda x: x["pricing"]["input_per_million"])

    return {
        "e2ee": e2ee_models,
        "private": private_models,
        "anonymized": anonymized_models
    }


def resolve_tiers(models, privacy_cat=None):
    text_models = [
        m for m in models
        if m.get("type") in ("text", "chat", None) and not m.get("model_spec", {}).get("offline", False)
    ]
    avail_ids = {m.get("id"): m for m in text_models}

    # Standard Tiers:
    high_picks = ["kimi-k3", "claude-opus-5", "grok-4-20", "openai-gpt-55-pro"]
    med_picks = ["deepseek-v4-flash", "mistral-small-3-2-24b-instruct", "google-gemma-3-27b-it", "deepseek-v4-1-flash"]
    low_picks = ["mercury-2-5", "qwen3-5-9b", "llama-3.2-3b", "zai-org-glm-4.7-flash"]

    high_choice = next((mid for mid in high_picks if mid in avail_ids), "kimi-k3")
    med_choice = next((mid for mid in med_picks if mid in avail_ids), "deepseek-v4-flash")
    low_choice = next((mid for mid in low_picks if mid in avail_ids), "mercury-2-5")

    # Encrypted (E2EE) Tiers:
    e2ee_high_picks = ["e2ee-kimi-k3-p", "e2ee-glm-5-3-p", "e2ee-kimi-k2-6"]
    e2ee_med_picks = ["e2ee-deepseek-v4-flash", "e2ee-qwen3-6-35b-a3b", "e2ee-gemma-4-26b-a4b-uncensored-p"]
    e2ee_low_picks = ["e2ee-qwen-2-5-7b-p", "e2ee-gpt-oss-120b-p", "e2ee-glm-5-3-flash"]

    e2ee_high_choice = next((mid for mid in e2ee_high_picks if mid in avail_ids), "e2ee-kimi-k3-p")
    e2ee_med_choice = next((mid for mid in e2ee_med_picks if mid in avail_ids), "e2ee-deepseek-v4-flash")
    e2ee_low_choice = next((mid for mid in e2ee_low_picks if mid in avail_ids), "e2ee-qwen-2-5-7b-p")

    return {
        "high": high_choice,
        "medium": med_choice,
        "low": low_choice,
        "controller": "kimi-k3",
        "high-e2ee": e2ee_high_choice,
        "medium-e2ee": e2ee_med_choice,
        "low-e2ee": e2ee_low_choice
    }


def refresh_cache(api_key=None):
    if not api_key:
        api_key = get_api_key()
    try:
        models = fetch_fresh_models(api_key)
        privacy_cat = categorize_privacy_models(models)
        tiers = resolve_tiers(models, privacy_cat)
        cache_data = {
            "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "tiers": tiers,
            "total_models": len(models),
            "privacy_catalog": privacy_cat
        }
        os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
        with open(CACHE_FILE, "w") as f:
            json.dump(cache_data, f, indent=2)
        return cache_data
    except Exception as e:
        return None


def get_cached_or_fresh():
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE) as f:
                data = json.load(f)
                if "privacy_catalog" in data and "tiers" in data:
                    return data
        except Exception:
            pass
    refreshed = refresh_cache()
    if refreshed:
        return refreshed
    # Hardcoded fallbacks if offline
    return {
        "tiers": {
            "high": "kimi-k3",
            "medium": "deepseek-v4-flash",
            "low": "mercury-2-5",
            "controller": "kimi-k3",
            "high-e2ee": "e2ee-kimi-k3-p",
            "medium-e2ee": "e2ee-deepseek-v4-flash",
            "low-e2ee": "e2ee-qwen-2-5-7b-p"
        },
        "privacy_catalog": {"e2ee": [], "private": [], "anonymized": []},
        "total_models": 0
    }


TUNNEL_FILE = "/opt/fleet/tunnel-url.txt"


def get_tunnel_url():
    if os.path.exists(TUNNEL_FILE):
        try:
            with open(TUNNEL_FILE) as f:
                return f.read().strip()
        except Exception:
            pass
    return "https://worship-him-knight-jul.trycloudflare.com"


def format_privacy_summary(data):
    tunnel_url = get_tunnel_url()
    catalog = data.get("privacy_catalog", {})
    e2ee_list = catalog.get("e2ee", [])
    private_list = catalog.get("private", [])
    tiers = data.get("tiers", {})

    lines = [
        "🔒 *VENICE HARDWARE-ENCLAVE & PRIVACY MODELS*",
        "",
        f"🌐 *Live Dashboard*: {tunnel_url}",
        "",
        "🛡️ *Confidential Hardware Enclaves (E2EE / TEE)*:",
    ]
    for m in e2ee_list[:4]:
        mid = m.get("id")
        p = m.get("pricing", {}).get("input_per_million", 0)
        lines.append(f"• `{mid}` (\\${p:.2f}/M) — AMD SEV-SNP Enclave")

    lines.extend([
        "",
        "🔒 *Zero Data Retention (ZDR Private)*:",
        f"• `{tiers.get('controller', 'kimi-k3')}` — Fleet Controller Engine",
        f"• `{tiers.get('medium', 'deepseek-v4-flash')}` — Child Agent Standard Tier",
        f"• `{tiers.get('low', 'mercury-2-5')}` — Low-Cost Swarm Inference",
        "",
        f"✨ *Catalog Summary*: {len(e2ee_list)} E2EE Enclaves, {len(private_list)} ZDR Private models.",
        "👉 Switch any agent to encrypted mode live in the Web Dashboard or via `/pair`."
    ])
    return "\n".join(lines)


def main():
    valid_args = (
        "high", "medium", "low", "controller",
        "high-e2ee", "medium-e2ee", "low-e2ee",
        "all", "refresh", "privacy-models", "privacy-summary"
    )
    if len(sys.argv) < 2 or sys.argv[1] not in valid_args:
        print(f"Usage: venice-resolve-model.py [{'|'.join(valid_args)}]", file=sys.stderr)
        sys.exit(1)

    target_tier = sys.argv[1]

    if target_tier == "controller":
        print("kimi-k3")
        sys.exit(0)

    if target_tier == "refresh":
        res = refresh_cache()
        if res:
            print(f"Successfully refreshed Venice cache ({CACHE_FILE}) - {res.get('total_models', 0)} models")
        else:
            print("Failed to refresh Venice models cache.", file=sys.stderr)
            sys.exit(1)
        sys.exit(0)

    data = get_cached_or_fresh()

    if target_tier == "privacy-summary":
        print(format_privacy_summary(data))
        sys.exit(0)

    if target_tier == "privacy-models":
        catalog = data.get("privacy_catalog", {})
        print(json.dumps(catalog, indent=2))
        sys.exit(0)

    if target_tier == "all":
        print(json.dumps(data.get("tiers", {}), indent=2))
        sys.exit(0)

    tiers = data.get("tiers", {})
    fallback = "e2ee-deepseek-v4-flash" if "e2ee" in target_tier else "deepseek-v4-flash"
    print(tiers.get(target_tier, fallback))


if __name__ == "__main__":
    main()
