"""
Health check module for Cogent Agent.

Provides health check functionality for Docker container health monitoring.
"""

import os
from dataclasses import dataclass
from typing import Optional

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class HealthStatus:
    """Health check status."""

    healthy: bool
    message: str
    details: dict


async def check_health() -> HealthStatus:
    """
    Perform health check for the agent.

    Checks:
    - Authentication credentials present
    - NATS connectivity (if configured)
    - Workspace directory accessible

    Returns:
        HealthStatus indicating overall health.
    """
    checks = {}
    all_healthy = True

    # Check authentication
    has_oauth = bool(os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"))
    has_api_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    auth_ok = has_oauth or has_api_key

    checks["auth"] = {
        "healthy": auth_ok,
        "message": "Authentication configured" if auth_ok else "No auth credentials",
        "method": "oauth" if has_oauth else ("api_key" if has_api_key else "none"),
    }

    if not auth_ok:
        all_healthy = False

    # Check workspace directory
    workspace_dir = os.environ.get("WORKSPACE_DIR", "/workspace")
    workspace_exists = os.path.isdir(workspace_dir)
    workspace_writable = workspace_exists and os.access(workspace_dir, os.W_OK)

    checks["workspace"] = {
        "healthy": workspace_writable,
        "message": "Workspace accessible" if workspace_writable else "Workspace not accessible",
        "path": workspace_dir,
    }

    if not workspace_writable:
        all_healthy = False

    # Check NATS connectivity (non-blocking, optional)
    nats_url = os.environ.get("NATS_URL")
    if nats_url:
        try:
            import nats

            nc = await nats.connect(nats_url, connect_timeout=2)
            await nc.close()
            checks["nats"] = {
                "healthy": True,
                "message": "NATS connected",
                "url": nats_url,
            }
        except Exception as e:
            checks["nats"] = {
                "healthy": False,
                "message": f"NATS connection failed: {e}",
                "url": nats_url,
            }
            # NATS failure is not fatal for basic health
            # all_healthy = False

    return HealthStatus(
        healthy=all_healthy,
        message="Agent healthy" if all_healthy else "Agent unhealthy",
        details=checks,
    )


async def check_nats_health(nats_url: str) -> bool:
    """Check NATS server connectivity."""
    try:
        import nats

        nc = await nats.connect(nats_url, connect_timeout=5)
        await nc.close()
        return True
    except Exception:
        return False
