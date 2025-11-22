#!/bin/bash
# post_tool_use.sh - Hook for PostToolUse events
#
# This hook:
# - Logs completed tool operations
# - Can trigger follow-up actions
# - Publishes events for monitoring

set -e

# Read hook input
INPUT=$(cat)

# Publish event to NATS
echo "${INPUT}" | /app/hooks/publish_event.sh "post_tool_use"

# Extract tool information for logging
TOOL_NAME=$(echo "${INPUT}" | jq -r '.tool_name // "unknown"')

# Special handling for file operations (for git tracking)
case "${TOOL_NAME}" in
    "Write"|"Edit")
        FILE_PATH=$(echo "${INPUT}" | jq -r '.tool_input.file_path // ""')

        # Could trigger git add here for auto-staging
        # git add "${FILE_PATH}" 2>/dev/null || true
        ;;
esac

exit 0
