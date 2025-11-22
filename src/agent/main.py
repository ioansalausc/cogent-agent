"""
Main entry point for Cogent Agent.

Initializes and runs the agent with NATS communication.
"""

import asyncio
import signal
import sys
from typing import Optional

import structlog

from .auth import get_auth_manager
from .config import get_config
from .core import CogentAgent

# Configure structured logging
structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger(__name__)


class AgentRunner:
    """
    Main runner for the Cogent Agent.

    Handles lifecycle, signal handling, and NATS integration.
    """

    def __init__(self):
        self.config = get_config()
        self.agent: Optional[CogentAgent] = None
        self.nats_handler = None  # Will be set when NATS module is imported
        self._shutdown_event = asyncio.Event()

    async def start(self) -> None:
        """Start the agent and all services."""
        logger.info(
            "Starting Cogent Agent",
            agent_id=self.config.agent_id,
            workspace=str(self.config.workspace_dir),
        )

        # Initialize agent
        self.agent = CogentAgent(config=self.config)
        auth_status = await self.agent.initialize()

        if not auth_status.is_valid:
            logger.error("Authentication failed", message=auth_status.message)
            sys.exit(1)

        logger.info(
            "Agent authenticated",
            method=auth_status.method.value,
        )

        # Initialize NATS communication
        try:
            from ..communication.nats_handler import NATSHandler

            self.nats_handler = NATSHandler(
                agent=self.agent,
                nats_url=self.config.nats.url,
            )
            await self.nats_handler.connect()
            await self.nats_handler.start_listening()

            logger.info(
                "NATS communication established",
                url=self.config.nats.url,
                agent_id=self.config.agent_id,
            )

        except ImportError:
            logger.warning("NATS handler not available, running in standalone mode")
        except Exception as e:
            logger.error("Failed to connect to NATS", error=str(e))
            # Continue without NATS for development

        # Wait for shutdown signal
        await self._shutdown_event.wait()

    async def stop(self) -> None:
        """Stop the agent gracefully."""
        logger.info("Shutting down agent")

        if self.nats_handler:
            await self.nats_handler.disconnect()

        if self.agent:
            await self.agent.shutdown()

        self._shutdown_event.set()

    def request_shutdown(self) -> None:
        """Request graceful shutdown."""
        asyncio.create_task(self.stop())


def setup_signal_handlers(runner: AgentRunner) -> None:
    """Setup signal handlers for graceful shutdown."""
    loop = asyncio.get_event_loop()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, runner.request_shutdown)


async def main() -> None:
    """Main entry point."""
    runner = AgentRunner()

    # Setup signal handlers
    try:
        setup_signal_handlers(runner)
    except NotImplementedError:
        # Signal handlers not available on Windows
        pass

    try:
        await runner.start()
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt")
    except Exception as e:
        logger.exception("Agent crashed")
        sys.exit(1)
    finally:
        await runner.stop()


if __name__ == "__main__":
    asyncio.run(main())
