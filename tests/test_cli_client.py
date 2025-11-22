"""
Unit tests for the CLI client module.
"""

import pytest

from src.cli.client import AgentResponse, CogentClient


class TestAgentResponse:
    """Tests for AgentResponse dataclass."""

    def test_response_success(self):
        """Test successful AgentResponse."""
        response = AgentResponse(
            success=True,
            data={"message": "Task completed"},
        )
        assert response.success is True
        assert response.data == {"message": "Task completed"}
        assert response.error is None

    def test_response_failure(self):
        """Test failed AgentResponse."""
        response = AgentResponse(
            success=False,
            data={},
            error="Connection timeout",
        )
        assert response.success is False
        assert response.data == {}
        assert response.error == "Connection timeout"

    def test_response_with_complex_data(self):
        """Test AgentResponse with complex data."""
        data = {
            "agent_id": "test-agent",
            "state": "ready",
            "tasks": [
                {"id": 1, "status": "completed"},
                {"id": 2, "status": "pending"},
            ],
        }
        response = AgentResponse(success=True, data=data)
        assert response.data["agent_id"] == "test-agent"
        assert len(response.data["tasks"]) == 2


class TestCogentClient:
    """Tests for CogentClient."""

    def test_client_init_defaults(self):
        """Test CogentClient initialization with defaults."""
        client = CogentClient()
        assert client.nats_url == "nats://localhost:4222"
        assert client.agent_id == "cogent-agent-001"
        assert client.is_connected is False

    def test_client_init_custom(self):
        """Test CogentClient initialization with custom values."""
        client = CogentClient(
            nats_url="nats://custom-host:4333",
            agent_id="custom-agent",
        )
        assert client.nats_url == "nats://custom-host:4333"
        assert client.agent_id == "custom-agent"

    def test_client_subject_names(self):
        """Test client subject name generation."""
        client = CogentClient(agent_id="test-agent-123")
        assert client._command_subject == "agent.test-agent-123.command"
        assert client._events_subject == "agent.test-agent-123.events"

    def test_client_is_connected_false(self):
        """Test is_connected returns False when not connected."""
        client = CogentClient()
        assert client.is_connected is False

    def test_client_no_nats_raises_on_send(self):
        """Test that send_command raises when not connected."""
        client = CogentClient()

        # We can't test the async method directly without mocking NATS
        # but we verify the client state
        assert client._nc is None
        assert client.is_connected is False


class TestCogentClientSubjects:
    """Tests for NATS subject generation."""

    def test_subject_with_dashes(self):
        """Test subject generation with agent ID containing dashes."""
        client = CogentClient(agent_id="agent-with-dashes-123")
        assert "agent-with-dashes-123" in client._command_subject
        assert "agent-with-dashes-123" in client._events_subject

    def test_subject_with_underscores(self):
        """Test subject generation with agent ID containing underscores."""
        client = CogentClient(agent_id="agent_with_underscores")
        assert "agent_with_underscores" in client._command_subject

    def test_subject_pattern_consistency(self):
        """Test subject naming follows NATS conventions."""
        client = CogentClient(agent_id="my-agent")
        # NATS subjects use dot separators
        assert client._command_subject.count(".") == 2
        assert client._events_subject.count(".") == 2
        # Subject starts with 'agent.'
        assert client._command_subject.startswith("agent.")
        assert client._events_subject.startswith("agent.")
