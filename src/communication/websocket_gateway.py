"""
WebSocket Gateway for multi-client support.

Bridges WebSocket clients to NATS, enabling:
- Web browser clients
- Mobile apps
- Slack/Discord bots
- Any WebSocket-capable client
"""

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Optional, Set

import structlog
import websockets
from websockets.server import WebSocketServerProtocol

import nats
from nats.aio.client import Client as NATSClient

logger = structlog.get_logger(__name__)


class ClientType(Enum):
    """Types of connected clients."""

    WEB = "web"
    MOBILE = "mobile"
    CLI = "cli"
    BOT = "bot"
    UNKNOWN = "unknown"


@dataclass
class ClientSession:
    """A connected client session."""

    session_id: str
    websocket: WebSocketServerProtocol
    client_type: ClientType
    agent_id: Optional[str] = None  # Subscribed agent
    user_id: Optional[str] = None
    authenticated: bool = False
    connected_at: datetime = field(default_factory=datetime.utcnow)
    last_activity: datetime = field(default_factory=datetime.utcnow)
    subscriptions: Set[str] = field(default_factory=set)


class WebSocketGateway:
    """
    WebSocket to NATS gateway.

    Features:
    - Multiple client connections
    - Authentication support
    - NATS subject routing
    - Streaming responses
    """

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 8080,
        nats_url: str = "nats://localhost:4222",
    ):
        """
        Initialize the gateway.

        Args:
            host: WebSocket server host.
            port: WebSocket server port.
            nats_url: NATS server URL.
        """
        self.host = host
        self.port = port
        self.nats_url = nats_url

        # Connected clients
        self._clients: dict[str, ClientSession] = {}

        # NATS connection
        self._nc: Optional[NATSClient] = None
        self._nats_subscriptions: list = []

        # Server
        self._server = None
        self._running = False

        # Authentication callback (can be customized)
        self._auth_callback: Optional[Callable] = None

    async def start(self) -> None:
        """Start the WebSocket gateway."""
        logger.info("Starting WebSocket gateway", host=self.host, port=self.port)

        # Connect to NATS
        self._nc = await nats.connect(
            self.nats_url,
            reconnect_time_wait=2,
            max_reconnect_attempts=60,
        )

        # Subscribe to agent events for broadcasting
        sub = await self._nc.subscribe(
            "agent.*.events.>",
            cb=self._handle_nats_event,
        )
        self._nats_subscriptions.append(sub)

        # Start WebSocket server
        self._server = await websockets.serve(
            self._handle_client,
            self.host,
            self.port,
        )

        self._running = True
        logger.info("WebSocket gateway started", url=f"ws://{self.host}:{self.port}")

    async def stop(self) -> None:
        """Stop the gateway."""
        logger.info("Stopping WebSocket gateway")
        self._running = False

        # Close all client connections
        for session in list(self._clients.values()):
            await session.websocket.close()

        # Unsubscribe from NATS
        for sub in self._nats_subscriptions:
            await sub.unsubscribe()

        # Close NATS connection
        if self._nc:
            await self._nc.drain()
            await self._nc.close()

        # Stop WebSocket server
        if self._server:
            self._server.close()
            await self._server.wait_closed()

        logger.info("WebSocket gateway stopped")

    def set_auth_callback(self, callback: Callable) -> None:
        """Set custom authentication callback."""
        self._auth_callback = callback

    async def _handle_client(self, websocket: WebSocketServerProtocol) -> None:
        """Handle a new WebSocket client connection."""
        session_id = str(uuid.uuid4())
        session = ClientSession(
            session_id=session_id,
            websocket=websocket,
            client_type=ClientType.UNKNOWN,
        )

        self._clients[session_id] = session
        logger.info("Client connected", session_id=session_id)

        try:
            # Send welcome message
            await self._send_to_client(session, {
                "type": "welcome",
                "session_id": session_id,
                "message": "Connected to Cogent Agent Gateway",
            })

            # Handle messages
            async for message in websocket:
                try:
                    data = json.loads(message)
                    await self._handle_client_message(session, data)
                except json.JSONDecodeError:
                    await self._send_error(session, "Invalid JSON")
                except Exception as e:
                    logger.exception("Error handling message")
                    await self._send_error(session, str(e))

        except websockets.exceptions.ConnectionClosed:
            logger.info("Client disconnected", session_id=session_id)
        finally:
            del self._clients[session_id]

    async def _handle_client_message(
        self,
        session: ClientSession,
        data: dict[str, Any],
    ) -> None:
        """Handle a message from a client."""
        msg_type = data.get("type", "")
        session.last_activity = datetime.utcnow()

        if msg_type == "authenticate":
            await self._handle_authenticate(session, data)

        elif msg_type == "subscribe":
            await self._handle_subscribe(session, data)

        elif msg_type == "unsubscribe":
            await self._handle_unsubscribe(session, data)

        elif msg_type == "command":
            await self._handle_command(session, data)

        elif msg_type == "ping":
            await self._send_to_client(session, {"type": "pong"})

        else:
            await self._send_error(session, f"Unknown message type: {msg_type}")

    async def _handle_authenticate(
        self,
        session: ClientSession,
        data: dict[str, Any],
    ) -> None:
        """Handle authentication request."""
        token = data.get("token")
        client_type = data.get("client_type", "unknown")
        user_id = data.get("user_id")

        # Custom auth callback or simple token validation
        if self._auth_callback:
            authenticated = await self._auth_callback(token, user_id)
        else:
            # Default: accept any token (for development)
            authenticated = bool(token)

        if authenticated:
            session.authenticated = True
            session.user_id = user_id
            session.client_type = ClientType(client_type) if client_type in [e.value for e in ClientType] else ClientType.UNKNOWN

            await self._send_to_client(session, {
                "type": "authenticated",
                "success": True,
                "user_id": user_id,
            })
            logger.info("Client authenticated", session_id=session.session_id, user_id=user_id)
        else:
            await self._send_to_client(session, {
                "type": "authenticated",
                "success": False,
                "error": "Authentication failed",
            })

    async def _handle_subscribe(
        self,
        session: ClientSession,
        data: dict[str, Any],
    ) -> None:
        """Handle subscription request."""
        agent_id = data.get("agent_id")

        if not agent_id:
            await self._send_error(session, "agent_id required")
            return

        session.agent_id = agent_id
        session.subscriptions.add(f"agent.{agent_id}.events")

        await self._send_to_client(session, {
            "type": "subscribed",
            "agent_id": agent_id,
        })

        logger.info(
            "Client subscribed",
            session_id=session.session_id,
            agent_id=agent_id,
        )

    async def _handle_unsubscribe(
        self,
        session: ClientSession,
        data: dict[str, Any],
    ) -> None:
        """Handle unsubscription request."""
        agent_id = data.get("agent_id")

        if agent_id:
            session.subscriptions.discard(f"agent.{agent_id}.events")
            if session.agent_id == agent_id:
                session.agent_id = None

        await self._send_to_client(session, {
            "type": "unsubscribed",
            "agent_id": agent_id,
        })

    async def _handle_command(
        self,
        session: ClientSession,
        data: dict[str, Any],
    ) -> None:
        """Handle command request (forward to NATS)."""
        agent_id = data.get("agent_id") or session.agent_id
        command = data.get("command")
        payload = data.get("payload", {})
        correlation_id = data.get("correlation_id") or str(uuid.uuid4())

        if not agent_id:
            await self._send_error(session, "agent_id required")
            return

        if not command:
            await self._send_error(session, "command required")
            return

        # Forward to NATS
        nats_subject = f"agent.{agent_id}.command"
        nats_message = {
            "type": command,
            "agent_id": session.session_id,
            "payload": payload,
            "correlation_id": correlation_id,
        }

        try:
            # Request-reply with timeout
            response = await self._nc.request(
                nats_subject,
                json.dumps(nats_message).encode(),
                timeout=30,
            )

            response_data = json.loads(response.data.decode())

            await self._send_to_client(session, {
                "type": "command_response",
                "correlation_id": correlation_id,
                "response": response_data,
            })

        except nats.errors.TimeoutError:
            await self._send_to_client(session, {
                "type": "command_response",
                "correlation_id": correlation_id,
                "error": "Request timed out",
            })

    async def _handle_nats_event(self, msg) -> None:
        """Handle NATS events and broadcast to subscribed clients."""
        try:
            subject = msg.subject
            data = json.loads(msg.data.decode())

            # Find clients subscribed to this subject
            for session in self._clients.values():
                # Check if any subscription matches
                for sub in session.subscriptions:
                    if subject.startswith(sub) or self._subject_matches(sub, subject):
                        await self._send_to_client(session, {
                            "type": "event",
                            "subject": subject,
                            "data": data,
                        })
                        break

        except Exception as e:
            logger.error("Error broadcasting event", error=str(e))

    def _subject_matches(self, pattern: str, subject: str) -> bool:
        """Check if a NATS subject matches a pattern."""
        # Simple matching - could be enhanced with wildcards
        return subject.startswith(pattern.rstrip(".>.*"))

    async def _send_to_client(
        self,
        session: ClientSession,
        data: dict[str, Any],
    ) -> None:
        """Send a message to a client."""
        try:
            await session.websocket.send(json.dumps(data))
        except Exception as e:
            logger.error(
                "Failed to send to client",
                session_id=session.session_id,
                error=str(e),
            )

    async def _send_error(self, session: ClientSession, error: str) -> None:
        """Send an error message to a client."""
        await self._send_to_client(session, {
            "type": "error",
            "error": error,
        })

    # Statistics

    def get_stats(self) -> dict[str, Any]:
        """Get gateway statistics."""
        return {
            "connected_clients": len(self._clients),
            "clients_by_type": {
                t.value: sum(1 for c in self._clients.values() if c.client_type == t)
                for t in ClientType
            },
            "authenticated_clients": sum(1 for c in self._clients.values() if c.authenticated),
        }


async def run_gateway(
    host: str = "0.0.0.0",
    port: int = 8080,
    nats_url: str = "nats://localhost:4222",
) -> None:
    """Run the WebSocket gateway as a standalone service."""
    gateway = WebSocketGateway(host=host, port=port, nats_url=nats_url)

    await gateway.start()

    try:
        # Keep running
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        pass
    finally:
        await gateway.stop()


if __name__ == "__main__":
    asyncio.run(run_gateway())
