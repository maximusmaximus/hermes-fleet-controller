#!/usr/bin/env bash
# /opt/fleet/bin/fleet-publish-gh.sh
# Automated GitHub publishing utility for Hermes Fleet Controller.
# Verifies repository sanitization, checks GitHub CLI authentication,
# creates remote repository if needed, and pushes branch main cleanly.

set -euo pipefail

REPO_DIR="/home/molt/hermes-fleet-controller"
SECRETS_FILE="/opt/fleet/secrets.env"
AGENTS_DIR="/opt/fleet/agents"
REPO_NAME="hermes-fleet-controller"
VISIBILITY="--public"
GH_TOKEN=""
DRY_RUN=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo)
      REPO_NAME="$2"
      shift 2
      ;;
    --public)
      VISIBILITY="--public"
      shift
      ;;
    --private)
      VISIBILITY="--private"
      shift
      ;;
    --token)
      GH_TOKEN="$2"
      shift 2
      ;;
    --dry-run)
      DRY_RUN=true
      shift
      ;;
    -h|--help)
      echo "Usage: fleet-publish-gh.sh [OPTIONS]"
      echo ""
      echo "Options:"
      echo "  --repo <name>       Repository name (default: hermes-fleet-controller)"
      echo "  --public            Create as public repository (default)"
      echo "  --private           Create as private repository"
      echo "  --token <token>     GitHub Personal Access Token for headless authentication"
      echo "  --dry-run           Perform sanitization and git status check without pushing"
      echo "  -h, --help          Show this help message"
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
  esac
done

echo "============================================================"
echo "    🚀 Hermes Fleet Controller — GitHub Publishing Tool"
echo "============================================================"
echo ""

# 1. Verify directory
if [ ! -d "$REPO_DIR/.git" ]; then
  echo "[-] Error: Git repository not found at $REPO_DIR" >&2
  exit 1
fi

cd "$REPO_DIR"

# 2. Verify clean working directory
echo "[*] Checking git repository status..."
git branch -M main

if [ -n "$(git status --porcelain)" ]; then
  echo "[*] Unstaged changes detected. Staging and committing before publish..."
  git add .
  git commit -m "chore: prepare repository for GitHub publish" || true
fi
echo "[✓] Git working tree is clean on branch main."

# 3. Pre-flight Zero-Leak Safety Verification
echo "[*] Running zero-leak credential sanitization scan..."
LEAK_FOUND=0
CHECK_COUNT=0

scan_secret_file() {
  local target_file="$1"
  [ -f "$target_file" ] || return 0

  # Read file content (using sudo if permission denied)
  local file_content
  if [ -r "$target_file" ]; then
    file_content=$(cat "$target_file")
  else
    file_content=$(sudo cat "$target_file" 2>/dev/null || true)
  fi

  [ -z "$file_content" ] && return 0

  while IFS= read -r line || [ -n "$line" ]; do
    [[ "$line" =~ ^#.*$ ]] && continue
    [[ "$line" != *"="* ]] && continue

    local key="${line%%=*}"
    local val="${line#*=}"

    # Trim whitespace and quotes
    key=$(echo "$key" | xargs)
    val=$(echo "$val" | sed -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//" | xargs)

    [ -z "$val" ] && continue
    # Ignore placeholder tokens
    [[ "$val" =~ ^(xxxxxxx|placeholder|none|null|example)$ ]] && continue

    # Scan sensitive variables
    local key_upper
    key_upper=$(echo "$key" | tr '[:lower:]' '[:upper:]')
    if [[ "$key_upper" =~ (KEY|TOKEN|SECRET|PASSWORD) ]] && [ "${#val}" -ge 8 ]; then
      CHECK_COUNT=$((CHECK_COUNT + 1))
      if git grep -F -q "$val" 2>/dev/null; then
        echo "[-] CRITICAL: Secret value for $key detected in repository! Refusing to publish." >&2
        LEAK_FOUND=1
      fi
    fi
  done <<< "$file_content"
}

# Scan secrets.env
scan_secret_file "$SECRETS_FILE"

# Scan agent .env files if present
if [ -d "$AGENTS_DIR" ]; then
  for agent_env in "$AGENTS_DIR"/*/.env; do
    [ -f "$agent_env" ] && scan_secret_file "$agent_env"
  done
  for key_meta in "$AGENTS_DIR"/*/key-meta.json; do
    if [ -f "$key_meta" ]; then
      api_key=$(sudo jq -r '.apiKey // empty' "$key_meta" 2>/dev/null || true)
      if [ -n "$api_key" ] && [ "${#api_key}" -ge 8 ]; then
        CHECK_COUNT=$((CHECK_COUNT + 1))
        if git grep -F -q "$api_key" 2>/dev/null; then
          echo "[-] CRITICAL: Dedicated API key in $key_meta detected in repository! Refusing to publish." >&2
          LEAK_FOUND=1
        fi
      fi
    fi
  done
fi

if [ "$LEAK_FOUND" -ne 0 ]; then
  echo "[-] Safety check FAILED. Please remove sensitive secrets before publishing." >&2
  exit 1
fi
echo "[✓] Safety check PASSED: Scanned $CHECK_COUNT secrets. Zero leaks detected."

if [ "$DRY_RUN" = true ]; then
  echo ""
  echo "[✓] Dry-run complete. Repository is verified and ready for publish."
  exit 0
fi

# 4. Authenticate with GitHub CLI if token provided
if [ -n "$GH_TOKEN" ]; then
  echo "[*] Authenticating with GitHub using provided token..."
  echo "$GH_TOKEN" | gh auth login --with-token
fi

# 5. Check GitHub CLI Authentication Status
echo "[*] Checking GitHub CLI authentication status..."
if ! gh auth status >/dev/null 2>&1; then
  echo ""
  echo "[!] GitHub CLI is not authenticated."
  if [ -t 0 ]; then
    echo "Starting interactive GitHub login (gh auth login)..."
    gh auth login -h github.com -p https -w
  else
    echo "[-] Headless environment detected. Please authenticate by running:" >&2
    echo "      gh auth login" >&2
    echo "    Or pass a token:" >&2
    echo "      fleet-publish-gh.sh --token <GITHUB_PERSONAL_ACCESS_TOKEN>" >&2
    exit 1
  fi
fi

CURRENT_USER=$(gh api user --jq '.login' 2>/dev/null || echo "")
echo "[✓] Authenticated as GitHub user: $CURRENT_USER"

# 6. Check remote origin and push
HAS_ORIGIN=$(git remote | grep -w "origin" || true)

if [ -z "$HAS_ORIGIN" ]; then
  echo "[*] Creating remote repository '$REPO_NAME' ($VISIBILITY)..."
  gh repo create "$REPO_NAME" $VISIBILITY --source=. --remote=origin --push
else
  echo "[*] Remote 'origin' already configured ($(git remote get-url origin)). Pushing main..."
  git push -u origin main
fi

REPO_URL=$(git remote get-url origin | sed 's/\.git$//' | sed 's|git@github.com:|https://github.com/|')

echo ""
echo "============================================================"
echo "    🎉 SUCCESS: Repository Published to GitHub!"
echo "============================================================"
echo "Repository URL: $REPO_URL"
echo "Active Branch : main"
echo ""
