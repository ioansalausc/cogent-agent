"""
Unit tests for the core agent module.
"""

import pytest

from src.agent.core import AgentMessage, AgentState, TaskResult


class TestAgentState:
    """Tests for AgentState enum."""

    def test_agent_state_values(self):
        """Test AgentState enum values."""
        assert AgentState.IDLE.value == "idle"
        assert AgentState.INITIALIZING.value == "initializing"
        assert AgentState.READY.value == "ready"
        assert AgentState.PROCESSING.value == "processing"
        assert AgentState.ERROR.value == "error"
        assert AgentState.SHUTDOWN.value == "shutdown"

    def test_agent_state_all_states(self):
        """Test all AgentState states are defined."""
        states = list(AgentState)
        assert len(states) == 6
        state_values = [s.value for s in states]
        assert "idle" in state_values
        assert "ready" in state_values
        assert "processing" in state_values


class TestAgentMessage:
    """Tests for AgentMessage dataclass."""

    def test_agent_message_creation(self):
        """Test AgentMessage creation with required fields."""
        msg = AgentMessage(role="user", content="Hello")
        assert msg.role == "user"
        assert msg.content == "Hello"
        assert msg.metadata == {}

    def test_agent_message_with_metadata(self):
        """Test AgentMessage creation with metadata."""
        metadata = {"tool_name": "Bash", "tool_id": "123"}
        msg = AgentMessage(role="tool_use", content="Running command", metadata=metadata)
        assert msg.role == "tool_use"
        assert msg.content == "Running command"
        assert msg.metadata == metadata
        assert msg.metadata["tool_name"] == "Bash"

    def test_agent_message_roles(self):
        """Test various message roles."""
        roles = ["user", "assistant", "system", "tool_result", "tool_use", "error"]
        for role in roles:
            msg = AgentMessage(role=role, content="test")
            assert msg.role == role


class TestTaskResult:
    """Tests for TaskResult dataclass."""

    def test_task_result_success(self):
        """Test successful TaskResult."""
        result = TaskResult(
            success=True,
            output="Task completed successfully",
        )
        assert result.success is True
        assert result.output == "Task completed successfully"
        assert result.error is None
        assert result.tool_calls == []
        assert result.metadata == {}

    def test_task_result_failure(self):
        """Test failed TaskResult."""
        result = TaskResult(
            success=False,
            output="",
            error="Something went wrong",
        )
        assert result.success is False
        assert result.output == ""
        assert result.error == "Something went wrong"

    def test_task_result_with_tool_calls(self):
        """Test TaskResult with tool calls."""
        tool_calls = [
            {"id": "1", "name": "Read", "input": {"file_path": "/test.py"}},
            {"id": "2", "name": "Write", "input": {"file_path": "/out.py"}},
        ]
        result = TaskResult(
            success=True,
            output="Done",
            tool_calls=tool_calls,
        )
        assert len(result.tool_calls) == 2
        assert result.tool_calls[0]["name"] == "Read"
        assert result.tool_calls[1]["name"] == "Write"

    def test_task_result_with_metadata(self):
        """Test TaskResult with metadata."""
        metadata = {"duration_ms": 1500, "tokens_used": 1000}
        result = TaskResult(
            success=True,
            output="Done",
            metadata=metadata,
        )
        assert result.metadata["duration_ms"] == 1500
        assert result.metadata["tokens_used"] == 1000

    def test_task_result_full(self):
        """Test TaskResult with all fields."""
        tool_calls = [{"id": "1", "name": "Bash", "input": {"command": "ls"}}]
        metadata = {"session_id": "sess-123"}
        result = TaskResult(
            success=True,
            output="file1.py\nfile2.py",
            error=None,
            tool_calls=tool_calls,
            metadata=metadata,
        )
        assert result.success is True
        assert "file1.py" in result.output
        assert len(result.tool_calls) == 1
        assert result.metadata["session_id"] == "sess-123"
