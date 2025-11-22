#!/bin/bash
# notification.sh - Hook for Notification events
#
# This hook:
# - Publishes notifications to NATS for external handling
# - Can integrate with Slack, Discord, etc.

set -e

# Read hook input
INPUT=$(cat)

# Publish event to NATS
echo "${INPUT}" | /app/hooks/publish_event.sh "notification"

# Extract notification details
MESSAGE=$(echo "${INPUT}" | jq -r '.message // ""')
NOTIFICATION_TYPE=$(echo "${INPUT}" | jq -r '.notification_type // "info"')

# Log notification
echo "[NOTIFICATION] ${NOTIFICATION_TYPE}: ${MESSAGE}" >&2

exit 0
