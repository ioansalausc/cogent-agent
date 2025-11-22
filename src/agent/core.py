"""
Core Agent wrapper for Claude Agent SDK.

Provides a high-level interface to the Claude Agent SDK with support for:
- Headless operation
- Custom tools via MCP
- Hook integration
- Session management
"""

import asyncio
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Optional

import structlog


def strip_ansi_sequences(text: str) -> str:
    """
    Strip ANSI escape sequences and terminal control codes from text.

    This removes:
    - CSI sequences (cursor positioning, colors, etc.)
    - OSC sequences (terminal title, color queries)
    - Box-drawing characters from Rich UI elements
    - Other escape sequences
    """
    # Remove OSC sequences: ESC ] ... (ST | BEL)
    # ST can be ESC \ or just \
    text = re.sub(r'\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?', '', text)
    text = re.sub(r'\]11;[^\x07\x1b\]]*\\?', '', text)  # Partial OSC color queries

    # Remove CSI sequences: ESC [ ... final_byte
    text = re.sub(r'\x1b\[[0-9;]*[A-Za-z]', '', text)
    text = re.sub(r'\[[0-9;]*[A-Za-z]', '', text)  # Without ESC prefix

    # Remove other escape sequences
    text = re.sub(r'\x1b[^[\]].?', '', text)

    # Remove raw cursor position responses like [35;22R
    text = re.sub(r'\[\d+;\d+R', '', text)

    # Remove orphaned OSC-like sequences
    text = re.sub(r'11;rgb:[0-9a-fA-F/]+', '', text)

    # Remove box-drawing characters and Rich UI artifacts
    # These include: ─ │ ┌ ┐ └ ┘ ├ ┤ ┬ ┴ ┼ ═ ║ ╔ ╗ ╚ ╝ ╠ ╣ ╦ ╩ ╬ ━ ┃
    text = re.sub(r'^[─━│┃┌┐└┘├┤┬┴┼═║╔╗╚╝╠╣╦╩╬\s]+$', '', text, flags=re.MULTILINE)

    # Remove spinner characters
    text = re.sub(r'[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏⣾⣽⣻⢿⡿⣟⣯⣷]', '', text)

    # Clean up multiple consecutive newlines
    text = re.sub(r'\n{3,}', '\n\n', text)

    # Remove lines that are only whitespace
    lines = [line for line in text.split('\n') if line.strip()]
    text = '\n'.join(lines)

    return text.strip()

# Claude Agent SDK imports
try:
    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeAgentOptions,
        ClaudeSDKClient,
        ResultMessage,
        TextBlock,
        ToolResultBlock,
        ToolUseBlock,
        query,
    )

    SDK_AVAILABLE = True
except ImportError:
    SDK_AVAILABLE = False
    # Define stubs for type checking when SDK not installed
    ClaudeAgentOptions = Any
    ClaudeSDKClient = Any

from .auth import AuthManager, AuthStatus, get_auth_manager
from .config import AgentConfig, get_config

logger = structlog.get_logger(__name__)


class AgentState(Enum):
    """Agent lifecycle states."""

    IDLE = "idle"
    INITIALIZING = "initializing"
    READY = "ready"
    PROCESSING = "processing"
    ERROR = "error"
    SHUTDOWN = "shutdown"


@dataclass
class AgentMessage:
    """Unified message format for agent communication."""

    role: str  # "user", "assistant", "system", "tool_result"
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class TaskResult:
    """Result of a task execution."""

    success: bool
    output: str
    error: Optional[str] = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class CogentAgent:
    """
    High-level wrapper for Claude Agent SDK.

    Manages agent lifecycle, authentication, and provides a clean interface
    for task execution with streaming support.
    """

    def __init__(
        self,
        config: Optional[AgentConfig] = None,
        auth_manager: Optional[AuthManager] = None,
    ):
        """
        Initialize the Cogent Agent.

        Args:
            config: Agent configuration. Loads from environment if None.
            auth_manager: Authentication manager. Creates new one if None.
        """
        if not SDK_AVAILABLE:
            raise RuntimeError(
                "Claude Agent SDK not installed. Run: pip install claude-agent-sdk"
            )

        self.config = config or get_config()
        self.auth = auth_manager or get_auth_manager()
        self._state = AgentState.IDLE
        self._client: Optional[ClaudeSDKClient] = None
        self._session_id: Optional[str] = None

        # Callbacks for events
        self._on_tool_use: Optional[Callable] = None
        self._on_message: Optional[Callable] = None

    @property
    def state(self) -> AgentState:
        """Get current agent state."""
        return self._state

    @property
    def agent_id(self) -> str:
        """Get agent identifier."""
        return self.config.agent_id

    @property
    def is_ready(self) -> bool:
        """Check if agent is ready to process tasks."""
        return self._state == AgentState.READY

    def _build_options(
        self,
        working_dir: Optional[Path] = None,
        system_prompt: Optional[str] = None,
        allowed_tools: Optional[list[str]] = None,
        max_turns: Optional[int] = None,
    ) -> "ClaudeAgentOptions":
        """
        Build ClaudeAgentOptions for SDK calls.

        Args:
            working_dir: Working directory for the agent.
            system_prompt: Custom system prompt to use.
            allowed_tools: List of allowed tools (None = all).
            max_turns: Maximum conversation turns.

        Returns:
            Configured ClaudeAgentOptions instance.
        """
        # Default tools for code operations
        default_tools = [
            "Read",
            "Write",
            "Edit",
            "Bash",
            "Glob",
            "Grep",
            "WebSearch",
            "WebFetch",
        ]

        options_kwargs = {
            "cwd": str(working_dir or self.config.workspace_dir),
            "allowed_tools": allowed_tools or default_tools,
        }

        # Add permission mode for headless operation
        if self.config.claude_code_skip_permissions:
            options_kwargs["permission_mode"] = "bypassPermissions"

        # Add system prompt if provided
        if system_prompt:
            options_kwargs["system_prompt"] = system_prompt

        # Add max turns if specified
        if max_turns:
            options_kwargs["max_turns"] = max_turns

        return ClaudeAgentOptions(**options_kwargs)

    async def initialize(self) -> AuthStatus:
        """
        Initialize the agent, including authentication.

        Returns:
            AuthStatus with authentication result.
        """
        self._state = AgentState.INITIALIZING

        try:
            # Initialize authentication
            auth_status = await self.auth.initialize()

            if not auth_status.is_valid:
                self._state = AgentState.ERROR
                logger.error("Authentication failed", message=auth_status.message)
                return auth_status

            logger.info(
                "Agent initialized",
                agent_id=self.agent_id,
                auth_method=auth_status.method.value,
            )

            self._state = AgentState.READY
            return auth_status

        except Exception as e:
            self._state = AgentState.ERROR
            logger.exception("Failed to initialize agent")
            raise

    async def execute_task(
        self,
        prompt: str,
        working_dir: Optional[Path] = None,
        system_prompt: Optional[str] = None,
        max_turns: Optional[int] = None,
    ) -> TaskResult:
        """
        Execute a task and return the complete result.

        Args:
            prompt: The task prompt/instructions.
            working_dir: Working directory for the task.
            system_prompt: Custom system prompt.
            max_turns: Maximum conversation turns.

        Returns:
            TaskResult with the execution outcome.
        """
        if not self.is_ready:
            raise RuntimeError(f"Agent not ready. Current state: {self._state.value}")

        self._state = AgentState.PROCESSING
        output_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []

        try:
            options = self._build_options(
                working_dir=working_dir,
                system_prompt=system_prompt,
                max_turns=max_turns,
            )

            async for message in query(prompt=prompt, options=options):
                # Process different message types
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            clean_text = strip_ansi_sequences(block.text)
                            if clean_text:
                                output_parts.append(clean_text)
                                if self._on_message:
                                    await self._on_message(
                                        AgentMessage(
                                            role="assistant",
                                            content=clean_text,
                                        )
                                    )
                        elif isinstance(block, ToolUseBlock):
                            tool_call = {
                                "id": block.id,
                                "name": block.name,
                                "input": block.input,
                            }
                            tool_calls.append(tool_call)
                            if self._on_tool_use:
                                await self._on_tool_use(tool_call)

                elif isinstance(message, ResultMessage):
                    # Final result message
                    if hasattr(message, "result"):
                        output_parts.append(str(message.result))

            self._state = AgentState.READY
            return TaskResult(
                success=True,
                output="\n".join(output_parts),
                tool_calls=tool_calls,
            )

        except Exception as e:
            self._state = AgentState.READY
            logger.exception("Task execution failed", prompt=prompt[:100])
            return TaskResult(
                success=False,
                output="",
                error=str(e),
            )

    async def stream_task(
        self,
        prompt: str,
        working_dir: Optional[Path] = None,
        system_prompt: Optional[str] = None,
        max_turns: Optional[int] = None,
    ) -> AsyncIterator[AgentMessage]:
        """
        Execute a task with streaming responses.

        Yields:
            AgentMessage instances as the agent processes.
        """
        if not self.is_ready:
            raise RuntimeError(f"Agent not ready. Current state: {self._state.value}")

        self._state = AgentState.PROCESSING

        try:
            options = self._build_options(
                working_dir=working_dir,
                system_prompt=system_prompt,
                max_turns=max_turns,
            )

            async for message in query(prompt=prompt, options=options):
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            clean_text = strip_ansi_sequences(block.text)
                            if clean_text:
                                yield AgentMessage(
                                    role="assistant",
                                    content=clean_text,
                                )
                        elif isinstance(block, ToolUseBlock):
                            yield AgentMessage(
                                role="tool_use",
                                content=f"Using tool: {block.name}",
                                metadata={
                                    "tool_id": block.id,
                                    "tool_name": block.name,
                                    "tool_input": block.input,
                                },
                            )
                        elif isinstance(block, ToolResultBlock):
                            clean_content = strip_ansi_sequences(str(block.content))
                            if clean_content:
                                yield AgentMessage(
                                    role="tool_result",
                                    content=clean_content,
                                    metadata={"tool_use_id": block.tool_use_id},
                                )

        except Exception as e:
            yield AgentMessage(
                role="error",
                content=str(e),
                metadata={"error_type": type(e).__name__},
            )
        finally:
            self._state = AgentState.READY

    @asynccontextmanager
    async def interactive_session(
        self,
        working_dir: Optional[Path] = None,
        system_prompt: Optional[str] = None,
    ) -> AsyncIterator["InteractiveSession"]:
        """
        Create an interactive session for multi-turn conversations.

        Usage:
            async with agent.interactive_session() as session:
                response = await session.send("Hello")
                response = await session.send("Follow up question")
        """
        if not self.is_ready:
            raise RuntimeError(f"Agent not ready. Current state: {self._state.value}")

        options = self._build_options(
            working_dir=working_dir,
            system_prompt=system_prompt,
        )

        async with ClaudeSDKClient(options=options) as client:
            session = InteractiveSession(client, self)
            try:
                yield session
            finally:
                pass  # Cleanup handled by context manager

    def on_tool_use(self, callback: Callable) -> None:
        """Register callback for tool use events."""
        self._on_tool_use = callback

    def on_message(self, callback: Callable) -> None:
        """Register callback for message events."""
        self._on_message = callback

    async def shutdown(self) -> None:
        """Shutdown the agent gracefully."""
        self._state = AgentState.SHUTDOWN
        logger.info("Agent shutdown", agent_id=self.agent_id)


class InteractiveSession:
    """
    Interactive conversation session with stateful context.

    Allows multiple turns of conversation while maintaining context.
    """

    def __init__(self, client: "ClaudeSDKClient", agent: CogentAgent):
        self._client = client
        self._agent = agent
        self._turn_count = 0

    @property
    def turn_count(self) -> int:
        """Get the number of conversation turns."""
        return self._turn_count

    async def send(self, message: str) -> str:
        """
        Send a message and get the response.

        Args:
            message: The message to send.

        Returns:
            The assistant's response text.
        """
        self._agent._state = AgentState.PROCESSING
        self._turn_count += 1

        try:
            await self._client.query(message)

            response_parts: list[str] = []
            async for msg in self._client.receive_response():
                if isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, TextBlock):
                            response_parts.append(block.text)

            return "\n".join(response_parts)

        finally:
            self._agent._state = AgentState.READY

    async def send_streaming(self, message: str) -> AsyncIterator[str]:
        """
        Send a message and stream the response.

        Yields:
            Text chunks as they arrive.
        """
        self._agent._state = AgentState.PROCESSING
        self._turn_count += 1

        try:
            await self._client.query(message)

            async for msg in self._client.receive_response():
                if isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, TextBlock):
                            yield block.text

        finally:
            self._agent._state = AgentState.READY
