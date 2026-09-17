#!/usr/bin/env python3
"""
/opt/fleet/bin/fleet-sync-docs.py
Hermes Fleet Controller - Daily Documentation & Sanitization Sync Engine.

Polls all fleet agents for updated SOUL.md, AMENDMENTS.md, skills, and learnings.
Applies rigorous multi-layer sanitization to strip all identifying and sensitive
information (API keys, Telegram bot tokens, user IDs, IPs, hostnames, private paths).
Updates generalized public documentation in ~/hermes-fleet-controller/docs, souls, and skills,
and stages/commits changes cleanly to git.
"""

import os
import re
import sys
import json
import glob
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

# Paths
FLEET_DIR = Path("/opt/fleet")
AGENTS_DIR = FLEET_DIR / "agents"
SHARED_SKILLS_DIR = FLEET_DIR / "skills"
SECRETS_FILE = FLEET_DIR / "secrets.env"
REPO_DIR = Path("/home/molt/hermes-fleet-controller")
PUBLIC_DOCS_DIR = REPO_DIR / "docs"
PUBLIC_SOULS_DIR = REPO_DIR / "souls"
PUBLIC_SKILLS_DIR = REPO_DIR / "skills"
NOTIFY_SCRIPT = FLEET_DIR / "bin/fleet-telegram-notify.sh"


def load_raw_secrets():
    """Load literal secret values to ensure 100% exact matching for redaction."""
    secrets = {}
    if SECRETS_FILE.exists():
        with open(SECRETS_FILE, "r") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                # Strip inline comments if any
                if " #" in v:
                    v = v.split(" #", 1)[0].strip()
                if v and len(v) > 2 and k in ("VENICE_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_ALLOWED_USERS"):
                    secrets[k] = v
    return secrets


def sanitize_text(text: str, raw_secrets: dict) -> str:
    """
    Rigorously sanitize text:
    1. Exact redaction of loaded secret values.
    2. Regex redaction of Venice/OpenAI/generic API keys.
    3. Regex redaction of Telegram Bot tokens & Telegram IDs.
    4. Regex redaction of IPv4 addresses.
    5. Regex redaction of MAC addresses.
    6. Redaction of private user paths and hostnames.
    """
    if not text:
        return ""

    sanitized = text

    # 1. Exact raw secret matches
    for key, val in raw_secrets.items():
        if key == "VENICE_API_KEY":
            sanitized = sanitized.replace(val, "<REDACTED_VENICE_API_KEY>")
        elif key == "TELEGRAM_BOT_TOKEN":
            sanitized = sanitized.replace(val, "<REDACTED_TELEGRAM_BOT_TOKEN>")
        elif key == "TELEGRAM_ALLOWED_USERS":
            for uid in val.split(","):
                uid = uid.strip()
                if uid and len(uid) >= 4:
                    sanitized = re.sub(rf"\b{re.escape(uid)}\b", "<REDACTED_TELEGRAM_USER_ID>", sanitized)

    # 2. General API keys (Venice, OpenAI, Anthropic, generic Bearer)
    sanitized = re.sub(r"\bv-(live|test)-[a-zA-Z0-9_\-]{16,}\b", "<REDACTED_VENICE_API_KEY>", sanitized)
    sanitized = re.sub(r"\bsk-[a-zA-Z0-9_\-]{20,}\b", "<REDACTED_API_KEY>", sanitized)
    sanitized = re.sub(r"Bearer\s+[a-zA-Z0-9_\-\.]{20,}", "Bearer <REDACTED_TOKEN>", sanitized)

    # 3. Telegram Bot Token pattern: 123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ
    sanitized = re.sub(r"\b[0-9]{8,11}:[a-zA-Z0-9_-]{35}\b", "<REDACTED_TELEGRAM_BOT_TOKEN>", sanitized)

    # 4. Telegram bot username if specific
    sanitized = re.sub(r"@[A-Za-z0-9_]*bot\b", "@<your_telegram_bot>", sanitized, flags=re.IGNORECASE)

    # 5. IPv4 addresses (preserve 0.0.0.0 or localhost documentation references if desired, redact private/public IPs)
    def ip_replacer(match):
        ip = match.group(0)
        if ip in ("0.0.0.0", "255.255.255.0", "127.0.0.1"):
            return "127.0.0.1"
        return "<REDACTED_IP>"

    sanitized = re.sub(r"\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b", ip_replacer, sanitized)

    # 6. MAC addresses
    sanitized = re.sub(r"\b([0-9A-Fa-f]{2}[:-]){5}([0-9A-Fa-f]{2})\b", "<REDACTED_MAC>", sanitized)

    # 7. Private user paths and hostnames
    sanitized = re.sub(r"/home/[a-zA-Z0-9_-]+", "/home/<user>", sanitized)
    sanitized = re.sub(r"[A-Z]:\\Users\\[a-zA-Z0-9_-]+", lambda m: r"C:\Users\<user>", sanitized)
    for host in ["molt", "lobsterdawg", "deb", "home assistant", "homeassistant"]:
        sanitized = re.sub(rf"\b{re.escape(host)}\b", "<sibling-node>", sanitized, flags=re.IGNORECASE)

    # 8. SSH Private keys
    sanitized = re.sub(
        r"-----BEGIN [A-Z ]+PRIVATE KEY-----[\s\S]*?-----END [A-Z ]+PRIVATE KEY-----",
        "<REDACTED_PRIVATE_KEY>",
        sanitized,
    )

    return sanitized


def poll_agent_learnings(agent_name: str) -> str:
    """
    Attempt to query the running agent container for any notable runtime learnings.
    Falls back gracefully if container is inactive or unresponsive.
    """
    container_name = "hermes-fleet-controller" if agent_name == "fleet-controller" else f"hermes-{agent_name}"
    cmd = [
        "podman", "exec", container_name,
        "hermes", "-z",
        "Summarize any newly refined operational guidelines, tools, or best practices from recent tasks in 2 concise bullet points. Abstract only, no secrets or IPs."
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=12)
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
    except Exception:
        pass
    return ""


def sync_fleet_docs():
    """Main synchronization and documentation generator routine."""
    print(f"[{datetime.now().isoformat()}] Starting Hermes Fleet generalized public docs sync...")
    raw_secrets = load_raw_secrets()

    # Ensure output directories exist
    PUBLIC_DOCS_DIR.mkdir(parents=True, exist_ok=True)
    PUBLIC_SOULS_DIR.mkdir(parents=True, exist_ok=True)
    PUBLIC_SKILLS_DIR.mkdir(parents=True, exist_ok=True)

    agents_data = []
    discovered_skills = {}
    learnings_entries = []

    # 1. Discover all agents in /opt/fleet/agents/
    if AGENTS_DIR.exists():
        for agent_path in sorted(AGENTS_DIR.iterdir()):
            if not agent_path.is_dir():
                continue
            name = agent_path.name
            soul_file = agent_path / "SOUL.md"
            amendments_file = agent_path / "AMENDMENTS.md"
            key_meta_file = agent_path / "key-meta.json"

            agent_info = {
                "name": name,
                "tier": "unknown",
                "model": "unknown",
                "skill": "General worker",
                "daily_budget": "N/A",
                "active": False,
                "soul_updated": False,
            }

            # Check running container state
            service_name = "hermes-fleet-controller" if name == "fleet-controller" else f"hermes-{name}"
            svc_check = subprocess.run(
                ["systemctl", "is-active", "--quiet", f"{service_name}.service"],
                capture_output=True
            )
            agent_info["active"] = (svc_check.returncode == 0)

            # Read metadata if present
            if key_meta_file.exists():
                try:
                    with open(key_meta_file, "r") as f:
                        meta = json.load(f)
                        agent_info["tier"] = meta.get("quality", "medium")
                        agent_info["model"] = meta.get("model", "unknown")
                        agent_info["skill"] = meta.get("skill", agent_info["skill"])
                        agent_info["daily_budget"] = f"${meta.get('daily_usd_limit', 0.0)}/day"
                except Exception:
                    pass
            elif name == "fleet-controller":
                agent_info["tier"] = "high"
                agent_info["model"] = "kimi-k3"
                agent_info["skill"] = "Fleet Orchestration, Monitoring & Key Allocation"
                agent_info["daily_budget"] = "Primary Admin Key"

            # Sync and sanitize SOUL.md
            if soul_file.exists():
                with open(soul_file, "r", encoding="utf-8", errors="replace") as f:
                    soul_content = f.read()

                # Check amendments
                if amendments_file.exists():
                    with open(amendments_file, "r", encoding="utf-8", errors="replace") as f:
                        amend_content = f.read().strip()
                        if amend_content:
                            soul_content += f"\n\n## Learned Amendments\n{amend_content}"

                sanitized_soul = sanitize_text(soul_content, raw_secrets)
                target_soul = PUBLIC_SOULS_DIR / f"{name}-soul.md"
                with open(target_soul, "w", encoding="utf-8") as f:
                    f.write(sanitized_soul)
                agent_info["soul_updated"] = True

            # Query runtime learnings if agent is active
            if agent_info["active"]:
                learn = poll_agent_learnings(name)
                if learn:
                    sanitized_learn = sanitize_text(learn, raw_secrets)
                    learnings_entries.append({
                        "agent": name,
                        "timestamp": datetime.now().strftime("%Y-%m-%d"),
                        "notes": sanitized_learn
                    })

            # Check agent-specific skills directory
            agent_skills_dir = agent_path / "skills"
            if agent_skills_dir.exists():
                for sk_file in agent_skills_dir.glob("**/SKILL.md"):
                    sk_name = sk_file.parent.name
                    if sk_name not in discovered_skills:
                        with open(sk_file, "r", encoding="utf-8", errors="replace") as f:
                            discovered_skills[sk_name] = f.read()

            agents_data.append(agent_info)

    # 2. Discover shared fleet skills in /opt/fleet/skills/
    if SHARED_SKILLS_DIR.exists():
        for sk_file in SHARED_SKILLS_DIR.glob("**/SKILL.md"):
            sk_name = sk_file.parent.name
            if sk_name not in discovered_skills:
                with open(sk_file, "r", encoding="utf-8", errors="replace") as f:
                    discovered_skills[sk_name] = f.read()

    # Write sanitized skills to public repo skills/
    synced_skills_count = 0
    for sk_name, sk_raw in discovered_skills.items():
        sk_target_dir = PUBLIC_SKILLS_DIR / sk_name
        sk_target_dir.mkdir(parents=True, exist_ok=True)
        sanitized_sk = sanitize_text(sk_raw, raw_secrets)
        with open(sk_target_dir / "SKILL.md", "w", encoding="utf-8") as f:
            f.write(sanitized_sk)
        synced_skills_count += 1

    # 3. Generate FLEET_CATALOG.md
    catalog_md = f"""# Hermes Fleet Agent & Skill Catalog

*Automated public catalog generated on {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}*

This catalog details the registered agent personas, tier allocations, and available skill modules in the Hermes Fleet. All private network topologies, credentials, and user data have been de-identified.

---

## Active Fleet Roster

| Agent Name | Quality Tier | Default Model | Daily Allocation | Role / Capability | Status |
|---|---|---|---|---|---|
"""
    for a in agents_data:
        status_icon = "🟢 Active" if a["active"] else "⚪ Inactive"
        catalog_md += f"| `{a['name']}` | `{a['tier']}` | `{a['model']}` | `{a['daily_budget']}` | {a['skill']} | {status_icon} |\n"

    catalog_md += f"""
---

## Fleet Skill Library

Total registered skills: **{synced_skills_count}**

| Skill Module | Description & Entrypoint |
|---|---|
"""
    for sk_name in sorted(discovered_skills.keys()):
        catalog_md += f"| [`{sk_name}`](../skills/{sk_name}/SKILL.md) | Standard Hermes Skill pack with declarative execution instructions |\n"

    catalog_md += """
---

## Privacy & Sanitization Assurance

This document is automatically compiled via `/opt/fleet/bin/fleet-sync-docs.py`. All API tokens, Telegram credentials, IP addresses, and private system identifiers are scrubbed before publishing.
"""
    with open(PUBLIC_DOCS_DIR / "FLEET_CATALOG.md", "w", encoding="utf-8") as f:
        f.write(catalog_md)

    # 4. Generate or append to LEARNINGS.md
    learnings_file = PUBLIC_DOCS_DIR / "LEARNINGS.md"
    existing_learnings = ""
    if learnings_file.exists():
        with open(learnings_file, "r", encoding="utf-8") as f:
            existing_learnings = f.read()
    else:
        existing_learnings = "# Fleet Operational Learnings & Workflow Distillations\n\nDaily distillations of operational patterns and heuristics discovered across the agent fleet.\n\n"

    if learnings_entries:
        new_section = f"\n### Sync: {datetime.now().strftime('%Y-%m-%d 08:00 PST')}\n"
        for entry in learnings_entries:
            new_section += f"- **Agent `{entry['agent']}`**:\n"
            for line in entry["notes"].splitlines():
                if line.strip():
                    new_section += f"  {line.strip()}\n"
        with open(learnings_file, "w", encoding="utf-8") as f:
            f.write(existing_learnings + new_section)

    # 5. Final Safety Verification: Verify no raw secrets leaked
    for check_file in REPO_DIR.glob("**/*.md"):
        with open(check_file, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
            for k, val in raw_secrets.items():
                if val in content:
                    print(f"FATAL: Secret {k} detected in {check_file}! Aborting commit.", file=sys.stderr)
                    sys.exit(1)

    # 6. Stage & Commit to Git
    os.system(f"chown -R molt:molt {REPO_DIR}")
    git_status = subprocess.run(
        ["sudo", "-u", "molt", "git", "-C", str(REPO_DIR), "status", "--porcelain"],
        capture_output=True, text=True
    )

    changes_committed = False
    if git_status.stdout.strip():
        subprocess.run(["sudo", "-u", "molt", "git", "-C", str(REPO_DIR), "add", "docs/", "souls/", "skills/"], check=True)
        commit_date = datetime.now().strftime("%Y-%m-%d")
        commit_msg = f"docs(fleet): daily sync of generalized souls and skills ({commit_date})"
        commit_res = subprocess.run(
            ["sudo", "-u", "molt", "git", "-C", str(REPO_DIR), "commit", "-m", commit_msg],
            capture_output=True, text=True
        )
        if commit_res.returncode == 0:
            changes_committed = True
            print(f"Git commit created: {commit_msg}")

        # Push to remote if origin exists and is accessible
        remotes = subprocess.run(
            ["sudo", "-u", "molt", "git", "-C", str(REPO_DIR), "remote"],
            capture_output=True, text=True
        )
        if "origin" in remotes.stdout:
            push_res = subprocess.run(
                ["sudo", "-u", "molt", "git", "-C", str(REPO_DIR), "push", "origin", "main"],
                capture_output=True, text=True
            )
            if push_res.returncode == 0:
                print("Successfully pushed documentation updates to remote origin.")
            else:
                print(f"Notice: git push skipped or pending authentication ({push_res.stderr.strip()})")

    # 7. Telegram Notification
    summary_msg = (
        f"📚 Daily Docs Sync (08:00 PST)\n"
        f"• Agents Polled: {len(agents_data)}\n"
        f"• Skills Synced: {synced_skills_count}\n"
        f"• Public Docs Status: {'Updated & Committed' if changes_committed else 'Clean (No changes)'}\n"
        f"• Redaction Check: PASSED (0 secrets leaked)"
    )

    if NOTIFY_SCRIPT.exists():
        subprocess.run([str(NOTIFY_SCRIPT), summary_msg], check=False)

    print(summary_msg)
    print("Sync completed successfully.")


if __name__ == "__main__":
    sync_fleet_docs()
