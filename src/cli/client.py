"""
NATS client for CLI communication with Cogent Agent.

Provides request-reply and subscription capabilities.
"""

import asyncio
import json
import uuid
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable, Optional

import nats
from nats.aio.client import Client as NATSClient
from nats.aio.msg import Msg


@dataclass
class AgentResponse:
    """Response from the agent."""

    success: bool
    data: dict[str, Any]
    error: Optional[str] = None


class CogentClient:
    """
    Client for communicating with Cogent Agent via NATS.

    Supports:
    - Sending commands (request-reply)
    - Subscribing to events (pub-sub)
    - Streaming responses
    """

    def __init__(
        self,
        nats_url: str = "nats://localhost:4222",
        agent_id: str = "cogent-agent-001",
    ):
        """
        Initialize the client.

        Args:
            nats_url: NATS server URL.
            agent_id: Target agent identifier.
        """
        self.nats_url = nats_url
        self.agent_id = agent_id

        # NATS client
        self._nc: Optional[NATSClient] = None

        # Subject names
        self._command_subject = f"agent.{agent_id}.command"
        self._events_subject = f"agent.{agent_id}.events"

        # Subscriptions
        self._event_sub = None
        self._event_callbacks: list[Callable] = []

    @property
    def is_connected(self) -> bool:
        """Check if connected to NATS."""
        return self._nc is not None and self._nc.is_connected

    async def connect(self) -> None:
        """Connect to NATS server."""
        self._nc = await nats.connect(
            self.nats_url,
            connect_timeout=10,
            reconnect_time_wait=2,
            max_reconnect_attempts=5,
        )

    async def disconnect(self) -> None:
        """Disconnect from NATS server."""
        if self._event_sub:
            await self._event_sub.unsubscribe()

        if self._nc:
            await self._nc.drain()
            await self._nc.close()

    async def send_command(
        self,
        command_type: str,
        payload: dict[str, Any],
        timeout: float = 30.0,
    ) -> AgentResponse:
        """
        Send a command to the agent and wait for response.

        Args:
            command_type: Type of command (e.g., "execute_task").
            payload: Command payload.
            timeout: Response timeout in seconds.

        Returns:
            AgentResponse with the result.
        """
        if not self.is_connected:
            raise RuntimeError("Not connected to NATS")

        correlation_id = str(uuid.uuid4())

        message = {
            "type": command_type,
            "agent_id": "cli",
            "payload": payload,
            "correlation_id": correlation_id,
        }

        try:
            response = await self._nc.request(
                self._command_subject,
                json.dumps(message).encode(),
                timeout=timeout,
            )

            data = json.loads(response.data.decode())
            return AgentResponse(
                success=data.get("payload", {}).get("success", False),
                data=data.get("payload", {}),
                error=data.get("payload", {}).get("error"),
            )

        except nats.errors.TimeoutError:
            return AgentResponse(
                success=False,
                data={},
                error="Request timed out",
            )
        except Exception as e:
            return AgentResponse(
                success=False,
                data={},
                error=str(e),
            )

    async def execute_task(
        self,
        prompt: str,
        working_dir: Optional[str] = None,
        system_prompt: Optional[str] = None,
    ) -> AgentResponse:
        """
        Execute a task on the agent.

        Args:
            prompt: Task prompt/instructions.
            working_dir: Optional working directory.
            system_prompt: Optional system prompt.

        Returns:
            AgentResponse indicating task was started.
        """
        return await self.send_command(
            "execute_task",
            {
                "prompt": prompt,
                "working_dir": working_dir,
                "system_prompt": system_prompt,
            },
        )

    async def get_status(self) -> AgentResponse:
        """Get agent status."""
        return await self.send_command("get_status", {})

    async def cancel_task(self) -> AgentResponse:
        """Cancel the currently running task."""
        return await self.send_command("cancel_task", {})

    async def subscribe_events(
        self,
        callback: Callable[[dict[str, Any]], None],
    ) -> None:
        """
        Subscribe to agent events.

        Args:
            callback: Function to call when events are received.
        """
        if not self.is_connected:
            raise RuntimeError("Not connected to NATS")

        self._event_callbacks.append(callback)

        if not self._event_sub:
            self._event_sub = await self._nc.subscribe(
                f"{self._events_subject}.>",
                cb=self._handle_event,
            )

    async def _handle_event(self, msg: Msg) -> None:
        """Handle incoming event messages."""
        try:
            data = json.loads(msg.data.decode())
            for callback in self._event_callbacks:
                if asyncio.iscoroutinefunction(callback):
                    await callback(data)
                else:
                    callback(data)
        except Exception:
            pass  # Ignore malformed events

    async def stream_events(self) -> AsyncIterator[dict[str, Any]]:
        """
        Stream events as an async iterator.

        Yields:
            Event dictionaries as they arrive.
        """
        if not self.is_connected:
            raise RuntimeError("Not connected to NATS")

        queue: asyncio.Queue = asyncio.Queue()

        async def enqueue(data: dict):
            await queue.put(data)

        await self.subscribe_events(enqueue)

        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=1.0)
                yield event
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
