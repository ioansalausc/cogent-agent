#!/bin/bash
# publish_event.sh - Publish Claude Code hook events to NATS
#
# This script is called by Claude Code hooks and publishes the event
# data to NATS for external consumption.
#
# Environment variables:
#   NATS_URL - NATS server URL (default: nats://localhost:4222)
#   AGENT_ID - Agent identifier (default: cogent-agent-001)
#
# Input: JSON event data via stdin
# Output: JSON response to stdout (if needed)

set -e

# Configuration
NATS_URL="${NATS_URL:-nats://localhost:4222}"
AGENT_ID="${AGENT_ID:-cogent-agent-001}"
HOOK_EVENT="${1:-unknown}"

# Read input from stdin
INPUT=$(cat)

# Build the NATS subject based on hook event
SUBJECT="agent.${AGENT_ID}.events.hook.${HOOK_EVENT}"

# Build the message payload
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
MESSAGE=$(jq -c --arg type "hook_${HOOK_EVENT}" \
               --arg agent_id "${AGENT_ID}" \
               --arg timestamp "${TIMESTAMP}" \
               '{
                 type: $type,
                 agent_id: $agent_id,
                 timestamp: $timestamp,
                 payload: .
               }' <<< "${INPUT}")

# Publish to NATS
if command -v nats &> /dev/null; then
    echo "${MESSAGE}" | nats pub "${SUBJECT}" --server="${NATS_URL}" 2>/dev/null || true
else
    # Fallback: log to stderr if NATS CLI not available
    echo "[HOOK] ${HOOK_EVENT}: ${MESSAGE}" >&2
fi

# Return success (exit 0 = non-blocking)
exit 0
