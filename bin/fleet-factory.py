#!/usr/bin/env python3
"""
/opt/fleet/bin/fleet-factory.py
AI Agent Factory powered by Venice AI (kimi-k3).
Synthesizes custom SOUL.md personas and SKILL.md workflows from natural language prompts,
configures E2EE confidential inference, validates Telegram bot token collisions,
mints isolated sub-keys ($0.50/day default), and deploys agents into 500MB containers.
"""

import os
import sys
import json
import glob
import re
import urllib.request
import urllib.error
import subprocess

FLEET_DIR = "/opt/fleet"
SECRETS_FILE = os.path.join(FLEET_DIR, "secrets.env")
SOULS_DIR = os.path.join(FLEET_DIR, "souls")
SKILLS_DIR = os.path.join(FLEET_DIR, "skills")
AGENTS_DIR = os.path.join(FLEET_DIR, "agents")


def get_controller_key():
    if os.path.exists(SECRETS_FILE):
        try:
            with open(SECRETS_FILE) as f:
                for line in f:
                    if line.startswith("VENICE_API_KEY="):
                        return line.split("=", 1)[1].strip().strip("\"'")
        except Exception:
            pass
    return os.environ.get("VENICE_API_KEY", "")


def check_telegram_token_collision(token, target_agent=""):
    """Safeguard 5: Validates token is not in use by any other agent."""
    if not token or not token.strip():
        return True, ""
    clean_token = token.strip()

    # Check /opt/fleet/secrets.env (controller token)
    if os.path.exists(SECRETS_FILE) and target_agent != "fleet-controller":
        with open(SECRETS_FILE) as f:
            for line in f:
                if line.startswith("TELEGRAM_BOT_TOKEN="):
                    ctoken = line.split("=", 1)[1].strip().strip("\"'")
                    if ctoken == clean_token:
                        return False, "Token is already assigned to 'fleet-controller'."

    # Check child agents .env files
    env_files = glob.glob(os.path.join(AGENTS_DIR, "*", ".env"))
    for ef in env_files:
        agent_name = os.path.basename(os.path.dirname(ef))
        if agent_name == target_agent:
            continue
        try:
            with open(ef) as f:
                for line in f:
                    if line.startswith("TELEGRAM_BOT_TOKEN="):
                        val = line.split("=", 1)[1].strip().strip("\"'")
                        if val == clean_token:
                            return False, f"Token is already in use by agent '{agent_name}'."
        except Exception:
            pass

    return True, ""


def synthesize_soul_and_skill(name, prompt, encrypted=False):
    """Uses Venice frontier kimi-k3 to author specialized SOUL.md and SKILL.md."""
    api_key = get_controller_key()
    if not api_key:
        raise ValueError("Venice API key not configured in secrets.env.")

    enc_note = (
        "This agent will run in an End-to-End Encrypted (E2EE) hardware enclave. "
        "Emphasize strict data privacy, confidentiality, and zero external leaks."
        if encrypted else "Standard confidential swarm agent."
    )

    system_prompt = (
        "You are an elite autonomous agent architect specializing in the Hermes Agent framework.\n"
        "Given the user's agent name and purpose, generate two distinct files in JSON format:\n"
        "1. 'soul': A complete, production-ready SOUL.md including:\n"
        "   - Agent Identity, Role, and Persona\n"
        "   - Core Mission and Primary Objectives\n"
        "   - Operating Principles, Tool Usage Rules, and Failure Recovery Protocols\n"
        "   - Output Formatting Instructions\n"
        "2. 'skill': A specialized SKILL.md including:\n"
        "   - YAML frontmatter (name, description, required_tools)\n"
        "   - Step-by-step operational workflows and command execution guides\n\n"
        "Return ONLY a valid JSON object with keys 'soul' and 'skill'."
    )

    user_msg = (
        f"Agent Name: {name}\n"
        f"Purpose / Specification: {prompt}\n"
        f"Confidentiality Context: {enc_note}\n\n"
        "Generate the complete SOUL.md and SKILL.md now."
    )

    payload = {
        "model": "kimi-k3",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_msg}
        ],
        "temperature": 0.4,
        "max_tokens": 4096,
        "response_format": {"type": "json_object"}
    }

    req = urllib.request.Request(
        "https://api.venice.ai/api/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
    )

    with urllib.request.urlopen(req, timeout=45) as resp:
        res_data = json.loads(resp.read().decode("utf-8"))
        content_str = res_data["choices"][0]["message"]["content"]
        result = json.loads(content_str)
        return result.get("soul", ""), result.get("skill", "")


def create_agent(
    name,
    prompt,
    tier="medium",
    encrypted=False,
    quota_usd=0.50,
    key_strategy="unique",
    telegram_token="",
    telegram_name="",
    custom_soul=None,
    custom_skill=None
):
    # 1. Validation
    clean_name = re.sub(r"[^a-zA-Z0-9_-]", "", name.strip().lower())
    if not clean_name:
        return {"success": False, "error": "Invalid agent name."}

    # Safeguard 5 check
    ok, err_msg = check_telegram_token_collision(telegram_token, target_agent=clean_name)
    if not ok:
        return {"success": False, "error": err_msg}

    # 2. Synthesize Soul and Skill if not provided
    soul_content = custom_soul
    skill_content = custom_skill
    if not soul_content or not skill_content:
        try:
            gen_soul, gen_skill = synthesize_soul_and_skill(clean_name, prompt, encrypted)
            if not soul_content:
                soul_content = gen_soul
            if not skill_content:
                skill_content = gen_skill
        except Exception as e:
            return {"success": False, "error": f"Venice synthesis failed: {str(e)}"}

    # Write SOUL.md
    os.makedirs(SOULS_DIR, exist_ok=True)
    soul_file = os.path.join(SOULS_DIR, f"{clean_name}.md")
    with open(soul_file, "w") as f:
        f.write(soul_content)

    # Write SKILL.md
    if skill_content and skill_content.strip():
        agent_skill_dir = os.path.join(SKILLS_DIR, clean_name)
        os.makedirs(agent_skill_dir, exist_ok=True)
        with open(os.path.join(agent_skill_dir, "SKILL.md"), "w") as f:
            f.write(skill_content)

    # 3. Model Resolution
    res_cmd = ["/opt/fleet/bin/venice-resolve-model.sh"]
    if encrypted:
        res_cmd.append(f"{tier}-e2ee")
    else:
        res_cmd.append(tier)

    try:
        resolved_model = subprocess.check_output(res_cmd).decode("utf-8").strip()
    except Exception:
        resolved_model = "e2ee-deepseek-v4-flash" if encrypted else "deepseek-v4-flash"

    # 4. Venice Key Provisioning
    agent_key = ""
    if key_strategy == "unique":
        try:
            out = subprocess.check_output([
                "/usr/bin/python3",
                "/opt/fleet/bin/venice-manage-keys.py",
                "create",
                clean_name,
                str(quota_usd),
                "--epoch"
            ]).decode("utf-8")
            # Parse key from output
            for line in out.splitlines():
                if "Key:" in line:
                    agent_key = line.split("Key:", 1)[1].strip()
        except Exception as e:
            return {"success": False, "error": f"Failed to mint Venice key: {str(e)}"}
    else:
        agent_key = get_controller_key()

    # 5. Invoke spawn-agent.sh
    spawn_cmd = [
        "/opt/fleet/bin/spawn-agent.sh",
        clean_name,
        tier,
        f"Venice Factory: {prompt[:40]}",
        agent_key
    ]

    try:
        sp_out = subprocess.check_output(spawn_cmd, stderr=subprocess.STDOUT).decode("utf-8")
    except subprocess.CalledProcessError as e:
        return {"success": False, "error": f"spawn-agent failed: {e.output.decode('utf-8')}"}

    # 6. Apply Telegram Token if configured
    if telegram_token and telegram_token.strip():
        subprocess.call([
            "/opt/fleet/bin/fleet-attach-agent-tg.sh",
            clean_name,
            telegram_token.strip(),
            telegram_name.strip()
        ])

    return {
        "success": True,
        "name": clean_name,
        "model": resolved_model,
        "encrypted": encrypted,
        "quota_usd": quota_usd,
        "key_strategy": key_strategy,
        "soul_file": soul_file
    }


def main():
    if len(sys.argv) < 2:
        print("Usage: fleet-factory.py generate <name> <prompt> [--encrypted] [--tier high|med|low] [--quota 0.50]")
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "generate":
        if len(sys.argv) < 4:
            print("Usage: fleet-factory.py generate <name> <prompt>")
            sys.exit(1)
        name = sys.argv[2]
        prompt = sys.argv[3]
        encrypted = "--encrypted" in sys.argv
        soul, skill = synthesize_soul_and_skill(name, prompt, encrypted)
        print(json.dumps({"soul": soul, "skill": skill}, indent=2))
        sys.exit(0)

    if cmd == "spawn":
        name = sys.argv[2]
        prompt = sys.argv[3]
        encrypted = "--encrypted" in sys.argv
        tier = "high" if "high" in sys.argv else ("low" if "low" in sys.argv else "medium")
        res = create_agent(name, prompt, tier=tier, encrypted=encrypted)
        print(json.dumps(res, indent=2))
        sys.exit(0 if res.get("success") else 1)


if __name__ == "__main__":
    main()
