#!/bin/bash
# session_start.sh - Hook for SessionStart events
#
# This hook:
# - Initializes session state
# - Loads project context
# - Sets up environment variables

set -e

# Read hook input
INPUT=$(cat)

# Publish event to NATS
echo "${INPUT}" | /app/hooks/publish_event.sh "session_start"

# Get session info
SESSION_ID=$(echo "${INPUT}" | jq -r '.session_id // ""')
SOURCE=$(echo "${INPUT}" | jq -r '.source // "startup"')

# If this is a new session, initialize state
if [ "${SOURCE}" = "startup" ]; then
    # Could initialize project-specific context here
    :
fi

# Write environment variables to persist (SessionStart special feature)
if [ -n "${CLAUDE_ENV_FILE}" ]; then
    echo "SESSION_STARTED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "${CLAUDE_ENV_FILE}"
fi

exit 0
