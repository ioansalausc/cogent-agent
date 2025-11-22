"""
Unit tests for the NATS handler module.
"""

import json
from datetime import datetime

import pytest

from src.communication.nats_handler import MessageType, NATSMessage


class TestMessageType:
    """Tests for MessageType enum."""

    def test_command_types(self):
        """Test command message types."""
        assert MessageType.EXECUTE_TASK.value == "execute_task"
        assert MessageType.CANCEL_TASK.value == "cancel_task"
        assert MessageType.GET_STATUS.value == "get_status"

    def test_event_types(self):
        """Test event message types."""
        assert MessageType.TASK_STARTED.value == "task_started"
        assert MessageType.TASK_PROGRESS.value == "task_progress"
        assert MessageType.TASK_COMPLETED.value == "task_completed"
        assert MessageType.TASK_FAILED.value == "task_failed"
        assert MessageType.TOOL_USE.value == "tool_use"
        assert MessageType.AGENT_MESSAGE.value == "agent_message"

    def test_system_types(self):
        """Test system message types."""
        assert MessageType.HEARTBEAT.value == "heartbeat"
        assert MessageType.SHUTDOWN.value == "shutdown"

    def test_all_message_types_count(self):
        """Test total number of message types."""
        all_types = list(MessageType)
        assert len(all_types) == 11


class TestNATSMessage:
    """Tests for NATSMessage dataclass."""

    def test_message_creation(self):
        """Test NATSMessage creation."""
        msg = NATSMessage(
            type=MessageType.EXECUTE_TASK,
            agent_id="test-agent",
            payload={"prompt": "Hello"},
        )
        assert msg.type == MessageType.EXECUTE_TASK
        assert msg.agent_id == "test-agent"
        assert msg.payload == {"prompt": "Hello"}
        assert msg.timestamp is not None
        assert msg.correlation_id is None

    def test_message_with_correlation_id(self):
        """Test NATSMessage with correlation_id."""
        msg = NATSMessage(
            type=MessageType.TASK_STARTED,
            agent_id="test-agent",
            payload={"task_id": "123"},
            correlation_id="corr-456",
        )
        assert msg.correlation_id == "corr-456"

    def test_message_to_json(self):
        """Test NATSMessage serialization to JSON."""
        msg = NATSMessage(
            type=MessageType.GET_STATUS,
            agent_id="test-agent",
            payload={"key": "value"},
            correlation_id="test-corr",
        )
        json_bytes = msg.to_json()
        assert isinstance(json_bytes, bytes)

        data = json.loads(json_bytes.decode())
        assert data["type"] == "get_status"
        assert data["agent_id"] == "test-agent"
        assert data["payload"] == {"key": "value"}
        assert data["correlation_id"] == "test-corr"
        assert "timestamp" in data

    def test_message_from_json(self):
        """Test NATSMessage deserialization from JSON."""
        json_data = {
            "type": "execute_task",
            "agent_id": "source-agent",
            "payload": {"prompt": "Do something"},
            "timestamp": "2024-01-01T00:00:00",
            "correlation_id": "corr-789",
        }
        json_bytes = json.dumps(json_data).encode()

        msg = NATSMessage.from_json(json_bytes)
        assert msg.type == MessageType.EXECUTE_TASK
        assert msg.agent_id == "source-agent"
        assert msg.payload == {"prompt": "Do something"}
        assert msg.timestamp == "2024-01-01T00:00:00"
        assert msg.correlation_id == "corr-789"

    def test_message_roundtrip(self):
        """Test NATSMessage serialization roundtrip."""
        original = NATSMessage(
            type=MessageType.TASK_COMPLETED,
            agent_id="roundtrip-agent",
            payload={"result": "success", "data": [1, 2, 3]},
            correlation_id="round-123",
        )

        json_bytes = original.to_json()
        restored = NATSMessage.from_json(json_bytes)

        assert restored.type == original.type
        assert restored.agent_id == original.agent_id
        assert restored.payload == original.payload
        assert restored.correlation_id == original.correlation_id

    def test_message_from_json_without_optional_fields(self):
        """Test NATSMessage deserialization without optional fields."""
        json_data = {
            "type": "heartbeat",
            "agent_id": "health-agent",
            "payload": {"status": "alive"},
        }
        json_bytes = json.dumps(json_data).encode()

        msg = NATSMessage.from_json(json_bytes)
        assert msg.type == MessageType.HEARTBEAT
        assert msg.timestamp == ""
        assert msg.correlation_id is None

    def test_message_complex_payload(self):
        """Test NATSMessage with complex nested payload."""
        complex_payload = {
            "task": {
                "prompt": "Complex task",
                "options": {
                    "max_turns": 10,
                    "tools": ["Read", "Write", "Bash"],
                },
            },
            "metadata": {
                "user_id": "user-123",
                "session_id": "sess-456",
            },
            "arrays": [1, 2, {"nested": True}],
        }
        msg = NATSMessage(
            type=MessageType.EXECUTE_TASK,
            agent_id="complex-agent",
            payload=complex_payload,
        )

        json_bytes = msg.to_json()
        restored = NATSMessage.from_json(json_bytes)

        assert restored.payload["task"]["options"]["tools"] == ["Read", "Write", "Bash"]
        assert restored.payload["metadata"]["user_id"] == "user-123"
        assert restored.payload["arrays"][2]["nested"] is True

    def test_message_timestamp_format(self):
        """Test that timestamp is in ISO format."""
        msg = NATSMessage(
            type=MessageType.HEARTBEAT,
            agent_id="time-agent",
            payload={},
        )
        # Verify timestamp can be parsed as ISO format
        try:
            datetime.fromisoformat(msg.timestamp)
            valid_iso = True
        except ValueError:
            valid_iso = False

        assert valid_iso, f"Timestamp {msg.timestamp} is not valid ISO format"

    def test_message_all_types_serializable(self):
        """Test that all message types can be serialized."""
        for msg_type in MessageType:
            msg = NATSMessage(
                type=msg_type,
                agent_id="type-test-agent",
                payload={"type": msg_type.value},
            )
            json_bytes = msg.to_json()
            restored = NATSMessage.from_json(json_bytes)
            assert restored.type == msg_type
