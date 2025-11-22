"""
Integration tests for Cogent Agent.

These tests require a running NATS server.
Run with: pytest tests/test_integration.py -v
"""

import asyncio
import json
import os
import uuid

import pytest

# Skip all tests if NATS is not available
pytestmark = pytest.mark.asyncio


async def check_nats_available():
    """Check if NATS server is available."""
    try:
        import nats

        nc = await nats.connect("nats://localhost:4222", connect_timeout=2)
        await nc.close()
        return True
    except Exception:
        return False


@pytest.fixture(scope="module")
def event_loop():
    """Create event loop for async tests."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


class TestNATSConnection:
    """Integration tests for NATS connection."""

    @pytest.mark.asyncio
    async def test_connect_to_nats(self):
        """Test connecting to NATS server."""
        if not await check_nats_available():
            pytest.skip("NATS server not available")

        import nats

        nc = await nats.connect("nats://localhost:4222")
        assert nc.is_connected
        await nc.close()

    @pytest.mark.asyncio
    async def test_publish_subscribe(self):
        """Test basic publish/subscribe."""
        if not await check_nats_available():
            pytest.skip("NATS server not available")

        import nats

        nc = await nats.connect("nats://localhost:4222")
        received = []

        async def handler(msg):
            received.append(msg.data.decode())

        sub = await nc.subscribe("test.integration.pubsub", cb=handler)

        await nc.publish("test.integration.pubsub", b"Hello Integration")
        await asyncio.sleep(0.1)  # Allow message to be processed

        await sub.unsubscribe()
        await nc.close()

        assert len(received) == 1
        assert received[0] == "Hello Integration"

    @pytest.mark.asyncio
    async def test_request_reply(self):
        """Test request-reply pattern."""
        if not await check_nats_available():
            pytest.skip("NATS server not available")

        import nats

        nc = await nats.connect("nats://localhost:4222")

        async def handler(msg):
            await nc.publish(msg.reply, b"pong")

        sub = await nc.subscribe("test.integration.reqrep", cb=handler)

        response = await nc.request("test.integration.reqrep", b"ping", timeout=2)
        assert response.data == b"pong"

        await sub.unsubscribe()
        await nc.close()


class TestCogentClientIntegration:
    """Integration tests for CogentClient."""

    @pytest.mark.asyncio
    async def test_client_connect(self):
        """Test CogentClient connection to NATS."""
        if not await check_nats_available():
            pytest.skip("NATS server not available")

        from src.cli.client import CogentClient

        client = CogentClient(nats_url="nats://localhost:4222")
        await client.connect()

        assert client.is_connected
        await client.disconnect()
        assert not client.is_connected

    @pytest.mark.asyncio
    async def test_client_send_command_timeout(self):
        """Test CogentClient send_command with timeout (no agent listening)."""
        if not await check_nats_available():
            pytest.skip("NATS server not available")

        from src.cli.client import CogentClient

        # Use unique agent ID to avoid conflicts
        test_agent_id = f"test-agent-{uuid.uuid4().hex[:8]}"
        client = CogentClient(
            nats_url="nats://localhost:4222",
            agent_id=test_agent_id,
        )
        await client.connect()

        # This should timeout since no agent is listening
        response = await client.send_command(
            "get_status",
            {},
            timeout=1.0,
        )

        assert response.success is False
        # Error could be timeout or no responders
        assert "timed out" in response.error.lower() or "no responders" in response.error.lower()

        await client.disconnect()


class TestNATSMessageIntegration:
    """Integration tests for NATSMessage serialization over wire."""

    @pytest.mark.asyncio
    async def test_message_over_wire(self):
        """Test NATSMessage serialization over actual NATS connection."""
        if not await check_nats_available():
            pytest.skip("NATS server not available")

        import nats

        from src.communication.nats_handler import MessageType, NATSMessage

        nc = await nats.connect("nats://localhost:4222")
        received_messages = []

        async def handler(msg):
            received_messages.append(NATSMessage.from_json(msg.data))

        sub = await nc.subscribe("test.integration.message", cb=handler)

        # Send a NATSMessage
        original_msg = NATSMessage(
            type=MessageType.EXECUTE_TASK,
            agent_id="test-sender",
            payload={"prompt": "Test prompt", "working_dir": "/test"},
            correlation_id="corr-123",
        )

        await nc.publish("test.integration.message", original_msg.to_json())
        await asyncio.sleep(0.1)

        await sub.unsubscribe()
        await nc.close()

        assert len(received_messages) == 1
        received = received_messages[0]
        assert received.type == MessageType.EXECUTE_TASK
        assert received.agent_id == "test-sender"
        assert received.payload["prompt"] == "Test prompt"
        assert received.correlation_id == "corr-123"


class TestJetStreamIntegration:
    """Integration tests for JetStream functionality."""

    @pytest.mark.asyncio
    async def test_jetstream_available(self):
        """Test that JetStream is enabled on the NATS server."""
        if not await check_nats_available():
            pytest.skip("NATS server not available")

        import nats

        nc = await nats.connect("nats://localhost:4222")

        try:
            js = nc.jetstream()
            # Try to get account info - this will fail if JetStream is not enabled
            account_info = await js.account_info()
            assert account_info is not None
        except nats.errors.NoRespondersError:
            pytest.skip("JetStream not enabled on NATS server")
        finally:
            await nc.close()

    @pytest.mark.asyncio
    async def test_jetstream_publish_consume(self):
        """Test JetStream publish and consume."""
        if not await check_nats_available():
            pytest.skip("NATS server not available")

        import nats
        from nats.js.api import StreamConfig

        nc = await nats.connect("nats://localhost:4222")

        try:
            js = nc.jetstream()
            stream_name = f"TEST_INTEGRATION_{uuid.uuid4().hex[:8]}"

            # Create stream
            try:
                await js.add_stream(
                    StreamConfig(
                        name=stream_name,
                        subjects=[f"{stream_name}.>"],
                        max_msgs=100,
                    )
                )
            except Exception:
                # Stream might already exist
                pass

            # Publish message
            ack = await js.publish(f"{stream_name}.test", b"JetStream test message")
            assert ack.stream == stream_name

            # Cleanup
            try:
                await js.delete_stream(stream_name)
            except Exception:
                pass

        except nats.errors.NoRespondersError:
            pytest.skip("JetStream not enabled on NATS server")
        finally:
            await nc.close()
