#!/bin/bash
# pre_tool_use.sh - Hook for PreToolUse events
#
# This hook can:
# - Log tool usage
# - Block dangerous operations
# - Modify tool inputs
#
# Exit codes:
#   0 - Allow the operation (with optional JSON output)
#   2 - Block the operation (stderr contains reason)

set -e

# Read hook input
INPUT=$(cat)

# Extract tool information
TOOL_NAME=$(echo "${INPUT}" | jq -r '.tool_name // "unknown"')
TOOL_INPUT=$(echo "${INPUT}" | jq -c '.tool_input // {}')

# Publish event to NATS
echo "${INPUT}" | /app/hooks/publish_event.sh "pre_tool_use"

# Security checks for dangerous operations
case "${TOOL_NAME}" in
    "Bash")
        COMMAND=$(echo "${TOOL_INPUT}" | jq -r '.command // ""')

        # Block dangerous commands
        if echo "${COMMAND}" | grep -qE '(rm\s+-rf\s+/|mkfs|dd\s+if=|chmod\s+777\s+/)'; then
            echo "Blocked dangerous command: ${COMMAND}" >&2
            exit 2
        fi

        # Block secrets exposure
        if echo "${COMMAND}" | grep -qE '(cat\s+.*\.env|echo\s+.*TOKEN|echo\s+.*KEY)'; then
            echo "Blocked potential secrets exposure" >&2
            exit 2
        fi
        ;;

    "Write"|"Edit")
        FILE_PATH=$(echo "${TOOL_INPUT}" | jq -r '.file_path // ""')

        # Block writes to sensitive files
        if echo "${FILE_PATH}" | grep -qE '(/etc/passwd|/etc/shadow|\.ssh/|\.env$)'; then
            echo "Blocked write to sensitive file: ${FILE_PATH}" >&2
            exit 2
        fi
        ;;
esac

# Allow the operation
exit 0
