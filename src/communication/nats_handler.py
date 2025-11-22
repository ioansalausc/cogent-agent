"""
NATS communication handler for Cogent Agent.

Provides:
- Request-Reply for commands
- Pub-Sub for events and progress
- JetStream for persistence and guaranteed delivery
- KV store for state management
"""

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Optional

import nats
from nats.aio.client import Client as NATSClient
from nats.aio.msg import Msg
from nats.js import JetStreamContext
from nats.js.api import ConsumerConfig, DeliverPolicy, RetentionPolicy, StreamConfig
from nats.js.kv import KeyValue
import structlog

from ..agent.core import AgentMessage, CogentAgent, TaskResult

logger = structlog.get_logger(__name__)


class MessageType(Enum):
    """Types of messages exchanged via NATS."""

    # Commands (request-reply)
    EXECUTE_TASK = "execute_task"
    CANCEL_TASK = "cancel_task"
    GET_STATUS = "get_status"

    # Events (pub-sub)
    TASK_STARTED = "task_started"
    TASK_PROGRESS = "task_progress"
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"
    TOOL_USE = "tool_use"
    AGENT_MESSAGE = "agent_message"

    # System
    HEARTBEAT = "heartbeat"
    SHUTDOWN = "shutdown"


@dataclass
class NATSMessage:
    """Standardized NATS message format."""

    type: MessageType
    agent_id: str
    payload: dict[str, Any]
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    correlation_id: Optional[str] = None

    def to_json(self) -> bytes:
        """Serialize to JSON bytes."""
        return json.dumps({
            "type": self.type.value,
            "agent_id": self.agent_id,
            "payload": self.payload,
            "timestamp": self.timestamp,
            "correlation_id": self.correlation_id,
        }).encode()

    @classmethod
    def from_json(cls, data: bytes) -> "NATSMessage":
        """Deserialize from JSON bytes."""
        obj = json.loads(data.decode())
        return cls(
            type=MessageType(obj["type"]),
            agent_id=obj["agent_id"],
            payload=obj["payload"],
            timestamp=obj.get("timestamp", ""),
            correlation_id=obj.get("correlation_id"),
        )


class NATSHandler:
    """
    Handles all NATS communication for the agent.

    Subject naming convention:
    - agent.{agent_id}.command     - Request-Reply for commands
    - agent.{agent_id}.events      - Pub-Sub for events
    - agent.{agent_id}.status      - Health/status updates
    - agent.shared.broadcast       - Cross-agent broadcasts
    """

    def __init__(
        self,
        agent: CogentAgent,
        nats_url: str = "nats://localhost:4222",
    ):
        """
        Initialize the NATS handler.

        Args:
            agent: The CogentAgent instance to handle commands for.
            nats_url: NATS server URL.
        """
        self.agent = agent
        self.nats_url = nats_url
        self.agent_id = agent.agent_id

        # NATS clients
        self._nc: Optional[NATSClient] = None
        self._js: Optional[JetStreamContext] = None
        self._kv: Optional[KeyValue] = None

        # Subject names
        self._command_subject = f"agent.{self.agent_id}.command"
        self._events_subject = f"agent.{self.agent_id}.events"
        self._status_subject = f"agent.{self.agent_id}.status"
        self._broadcast_subject = "agent.shared.broadcast"

        # Subscriptions
        self._subscriptions: list = []

        # Command handlers
        self._command_handlers: dict[MessageType, Callable] = {
            MessageType.EXECUTE_TASK: self._handle_execute_task,
            MessageType.CANCEL_TASK: self._handle_cancel_task,
            MessageType.GET_STATUS: self._handle_get_status,
        }

        # Task tracking
        self._active_task: Optional[asyncio.Task] = None
        self._task_cancelled = asyncio.Event()

        # Heartbeat
        self._heartbeat_task: Optional[asyncio.Task] = None

    @property
    def is_connected(self) -> bool:
        """Check if connected to NATS."""
        return self._nc is not None and self._nc.is_connected

    async def connect(self) -> None:
        """Connect to NATS server and setup JetStream."""
        logger.info("Connecting to NATS", url=self.nats_url)

        # Connect to NATS
        self._nc = await nats.connect(
            self.nats_url,
            reconnect_time_wait=2,
            max_reconnect_attempts=60,
            error_cb=self._error_callback,
            disconnected_cb=self._disconnected_callback,
            reconnected_cb=self._reconnected_callback,
        )

        # Setup JetStream
        self._js = self._nc.jetstream()

        # Create stream for agent events (if not exists)
        await self._setup_jetstream()

        # Setup KV store for state
        await self._setup_kv_store()

        logger.info("Connected to NATS", agent_id=self.agent_id)

    async def _setup_jetstream(self) -> None:
        """Setup JetStream streams for persistent messaging."""
        stream_name = f"COGENT_AGENT_{self.agent_id.replace('-', '_').upper()}"

        try:
            # Try to get existing stream
            await self._js.stream_info(stream_name)
            logger.debug("JetStream stream exists", stream=stream_name)
        except nats.js.errors.NotFoundError:
            # Create new stream
            await self._js.add_stream(
                StreamConfig(
                    name=stream_name,
                    subjects=[
                        f"agent.{self.agent_id}.events.*",
                        f"agent.{self.agent_id}.tasks.*",
                    ],
                    retention=RetentionPolicy.LIMITS,
                    max_msgs=10000,
                    max_bytes=100 * 1024 * 1024,  # 100MB
                    max_age=7 * 24 * 60 * 60,  # 7 days in seconds
                )
            )
            logger.info("Created JetStream stream", stream=stream_name)

    async def _setup_kv_store(self) -> None:
        """Setup KV store for agent state."""
        bucket_name = f"cogent_agent_{self.agent_id.replace('-', '_')}"

        try:
            self._kv = await self._js.key_value(bucket_name)
            logger.debug("KV bucket exists", bucket=bucket_name)
        except nats.js.errors.BucketNotFoundError:
            self._kv = await self._js.create_key_value(
                bucket=bucket_name,
                history=5,
                ttl=0,  # No TTL
            )
            logger.info("Created KV bucket", bucket=bucket_name)

    async def start_listening(self) -> None:
        """Start listening for commands and subscriptions."""
        if not self.is_connected:
            raise RuntimeError("Not connected to NATS")

        # Subscribe to command subject (request-reply)
        sub = await self._nc.subscribe(
            self._command_subject,
            cb=self._handle_command,
        )
        self._subscriptions.append(sub)

        # Subscribe to broadcast subject
        sub = await self._nc.subscribe(
            self._broadcast_subject,
            cb=self._handle_broadcast,
        )
        self._subscriptions.append(sub)

        # Start heartbeat
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

        # Publish agent ready event
        await self.publish_event(
            MessageType.HEARTBEAT,
            {"status": "ready", "state": self.agent.state.value},
        )

        logger.info(
            "Started listening",
            command_subject=self._command_subject,
            broadcast_subject=self._broadcast_subject,
        )

    async def disconnect(self) -> None:
        """Disconnect from NATS."""
        # Stop heartbeat
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass

        # Cancel active task
        if self._active_task:
            self._task_cancelled.set()
            self._active_task.cancel()

        # Unsubscribe all (with error handling)
        for sub in self._subscriptions:
            try:
                await sub.unsubscribe()
            except Exception:
                pass  # Connection might already be closed

        # Close connection
        if self._nc and self._nc.is_connected:
            try:
                await self._nc.drain()
                await self._nc.close()
            except Exception:
                pass  # Connection might already be closed

        logger.info("Disconnected from NATS")

    async def publish_event(
        self,
        event_type: MessageType,
        payload: dict[str, Any],
        correlation_id: Optional[str] = None,
    ) -> None:
        """
        Publish an event to the events subject.

        Args:
            event_type: Type of event.
            payload: Event payload.
            correlation_id: Optional correlation ID for tracking.
        """
        if not self.is_connected:
            logger.warning("Cannot publish event, not connected")
            return

        message = NATSMessage(
            type=event_type,
            agent_id=self.agent_id,
            payload=payload,
            correlation_id=correlation_id,
        )

        subject = f"{self._events_subject}.{event_type.value}"
        await self._nc.publish(subject, message.to_json())

        logger.debug("Published event", type=event_type.value, subject=subject)

    async def _handle_command(self, msg: Msg) -> None:
        """Handle incoming command messages."""
        try:
            nats_msg = NATSMessage.from_json(msg.data)
            logger.info(
                "Received command",
                type=nats_msg.type.value,
                correlation_id=nats_msg.correlation_id,
            )

            # Get handler
            handler = self._command_handlers.get(nats_msg.type)
            if handler:
                response = await handler(nats_msg)
            else:
                response = {
                    "success": False,
                    "error": f"Unknown command type: {nats_msg.type.value}",
                }

            # Send reply
            if msg.reply:
                reply_msg = NATSMessage(
                    type=nats_msg.type,
                    agent_id=self.agent_id,
                    payload=response,
                    correlation_id=nats_msg.correlation_id,
                )
                await self._nc.publish(msg.reply, reply_msg.to_json())

        except Exception as e:
            logger.exception("Error handling command")
            if msg.reply:
                error_response = NATSMessage(
                    type=MessageType.TASK_FAILED,
                    agent_id=self.agent_id,
                    payload={"success": False, "error": str(e)},
                )
                await self._nc.publish(msg.reply, error_response.to_json())

    async def _handle_execute_task(self, msg: NATSMessage) -> dict[str, Any]:
        """Handle execute_task command."""
        prompt = msg.payload.get("prompt", "")
        working_dir = msg.payload.get("working_dir")
        system_prompt = msg.payload.get("system_prompt")
        correlation_id = msg.correlation_id

        if not prompt:
            return {"success": False, "error": "No prompt provided"}

        # Check if already processing
        if self._active_task and not self._active_task.done():
            return {"success": False, "error": "Agent is busy processing another task"}

        # Publish task started event
        await self.publish_event(
            MessageType.TASK_STARTED,
            {"prompt": prompt[:200]},  # Truncate for event
            correlation_id=correlation_id,
        )

        # Reset cancellation flag
        self._task_cancelled.clear()

        # Execute task with streaming progress
        async def run_task():
            try:
                async for message in self.agent.stream_task(
                    prompt=prompt,
                    working_dir=working_dir,
                    system_prompt=system_prompt,
                ):
                    if self._task_cancelled.is_set():
                        return TaskResult(
                            success=False,
                            output="",
                            error="Task cancelled",
                        )

                    # Publish progress
                    await self.publish_event(
                        MessageType.TASK_PROGRESS,
                        {
                            "role": message.role,
                            "content": message.content,
                            "metadata": message.metadata,
                        },
                        correlation_id=correlation_id,
                    )

                # Task completed successfully
                await self.publish_event(
                    MessageType.TASK_COMPLETED,
                    {"prompt": prompt[:200]},
                    correlation_id=correlation_id,
                )

                return {"success": True}

            except Exception as e:
                await self.publish_event(
                    MessageType.TASK_FAILED,
                    {"error": str(e)},
                    correlation_id=correlation_id,
                )
                return {"success": False, "error": str(e)}

        # Start task in background
        self._active_task = asyncio.create_task(run_task())

        return {
            "success": True,
            "message": "Task started",
            "correlation_id": correlation_id,
        }

    async def _handle_cancel_task(self, msg: NATSMessage) -> dict[str, Any]:
        """Handle cancel_task command."""
        if self._active_task and not self._active_task.done():
            self._task_cancelled.set()
            self._active_task.cancel()
            return {"success": True, "message": "Task cancellation requested"}
        return {"success": False, "error": "No active task to cancel"}

    async def _handle_get_status(self, msg: NATSMessage) -> dict[str, Any]:
        """Handle get_status command."""
        return {
            "success": True,
            "agent_id": self.agent_id,
            "state": self.agent.state.value,
            "is_ready": self.agent.is_ready,
            "has_active_task": self._active_task is not None and not self._active_task.done(),
        }

    async def _handle_broadcast(self, msg: Msg) -> None:
        """Handle broadcast messages."""
        try:
            nats_msg = NATSMessage.from_json(msg.data)

            # Ignore own broadcasts
            if nats_msg.agent_id == self.agent_id:
                return

            logger.info(
                "Received broadcast",
                from_agent=nats_msg.agent_id,
                type=nats_msg.type.value,
            )

            # Handle shutdown broadcast
            if nats_msg.type == MessageType.SHUTDOWN:
                logger.info("Received shutdown broadcast")
                # Signal shutdown (handled by main loop)

        except Exception as e:
            logger.exception("Error handling broadcast")

    async def _heartbeat_loop(self) -> None:
        """Send periodic heartbeat messages."""
        while True:
            try:
                await asyncio.sleep(30)  # Every 30 seconds
                await self.publish_event(
                    MessageType.HEARTBEAT,
                    {
                        "status": "alive",
                        "state": self.agent.state.value,
                        "has_active_task": self._active_task is not None and not self._active_task.done(),
                    },
                )
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Heartbeat failed", error=str(e))

    # State management via KV store

    async def save_state(self, key: str, value: dict[str, Any]) -> None:
        """Save state to KV store."""
        if self._kv:
            await self._kv.put(key, json.dumps(value).encode())

    async def load_state(self, key: str) -> Optional[dict[str, Any]]:
        """Load state from KV store."""
        if self._kv:
            try:
                entry = await self._kv.get(key)
                return json.loads(entry.value.decode())
            except nats.js.errors.KeyNotFoundError:
                return None
        return None

    async def delete_state(self, key: str) -> None:
        """Delete state from KV store."""
        if self._kv:
            try:
                await self._kv.delete(key)
            except nats.js.errors.KeyNotFoundError:
                pass

    # Callbacks for NATS events

    async def _error_callback(self, e: Exception) -> None:
        """Handle NATS errors."""
        logger.error("NATS error", error=str(e))

    async def _disconnected_callback(self) -> None:
        """Handle NATS disconnection."""
        logger.warning("Disconnected from NATS")

    async def _reconnected_callback(self) -> None:
        """Handle NATS reconnection."""
        logger.info("Reconnected to NATS")
