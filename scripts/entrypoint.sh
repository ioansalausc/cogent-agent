#!/bin/bash
# entrypoint.sh - Container entrypoint script
#
# Sets up the environment and starts the Cogent Agent

set -e

# Create necessary directories
mkdir -p /home/cogent/.claude/commands
mkdir -p /home/cogent/.claude/skills

# Copy Claude Code settings if not already present
if [ ! -f /home/cogent/.claude/settings.json ]; then
    cp /app/config/claude_settings.json /home/cogent/.claude/settings.json
    echo "Initialized Claude Code settings"
fi

# Link assets directory content
if [ -d /assets/skills ]; then
    for skill_dir in /assets/skills/*/; do
        if [ -d "$skill_dir" ]; then
            skill_name=$(basename "$skill_dir")
            ln -sf "$skill_dir" "/home/cogent/.claude/skills/${skill_name}" 2>/dev/null || true
        fi
    done
fi

if [ -d /assets/commands ]; then
    for cmd_file in /assets/commands/*.md; do
        if [ -f "$cmd_file" ]; then
            ln -sf "$cmd_file" "/home/cogent/.claude/commands/" 2>/dev/null || true
        fi
    done
fi

# Copy global CLAUDE.md if present
if [ -f /assets/CLAUDE.md ]; then
    ln -sf /assets/CLAUDE.md /home/cogent/.claude/CLAUDE.md 2>/dev/null || true
fi

# Configure Git
if [ -n "${GIT_AUTHOR_NAME}" ]; then
    git config --global user.name "${GIT_AUTHOR_NAME}"
fi
if [ -n "${GIT_AUTHOR_EMAIL}" ]; then
    git config --global user.email "${GIT_AUTHOR_EMAIL}"
fi

# Configure GitHub CLI if token provided
if [ -n "${GH_TOKEN}" ]; then
    echo "${GH_TOKEN}" | gh auth login --with-token 2>/dev/null || true
fi

# Make hook scripts executable
chmod +x /app/hooks/*.sh 2>/dev/null || true

# Print startup info
echo "==========================================="
echo "Cogent Agent Starting"
echo "==========================================="
echo "Agent ID: ${AGENT_ID:-cogent-agent-001}"
echo "NATS URL: ${NATS_URL:-nats://localhost:4222}"
echo "Workspace: ${WORKSPACE_DIR:-/workspace}"
echo "Assets: ${ASSETS_DIR:-/assets}"
echo ""
echo "Authentication:"
if [ -n "${CLAUDE_CODE_OAUTH_TOKEN}" ]; then
    echo "  - OAuth Token: configured"
elif [ -n "${ANTHROPIC_API_KEY}" ]; then
    echo "  - API Key: configured"
else
    echo "  - WARNING: No authentication configured!"
fi
if [ -n "${GH_TOKEN}" ]; then
    echo "  - GitHub Token: configured"
fi
echo "==========================================="

# Execute the main command
exec "$@"
