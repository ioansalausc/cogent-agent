"""
Project Agent for handling individual project tasks.

Each Project Agent:
- Has isolated context for one project
- Manages its own Git workflow
- Communicates via NATS with namespaced subjects
- Reports to the Orchestrator
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

import structlog

from ..github.workflow import WorkflowConfig, WorkflowManager, WorkflowResult
from .core import AgentMessage, CogentAgent, TaskResult

if TYPE_CHECKING:
    from .orchestrator import OrchestratorAgent

logger = structlog.get_logger(__name__)


@dataclass
class ProjectConfig:
    """Configuration for a project agent."""

    project_id: str
    project_path: Path
    working_area: str

    # Git settings (defaults loaded from environment via GitConfig)
    git_author_name: str | None = None
    git_author_email: str | None = None
    default_branch: str | None = None

    # Workflow settings
    auto_commit: bool = True
    auto_commit_interval: int | None = None
    run_tests: bool = True
    auto_create_pr: bool = True
    auto_merge: bool = True

    def __post_init__(self):
        """Load defaults from environment config if not specified."""
        from .config import get_config
        git_config = get_config().git

        if self.git_author_name is None:
            self.git_author_name = git_config.author_name
        if self.git_author_email is None:
            self.git_author_email = git_config.author_email
        if self.default_branch is None:
            self.default_branch = git_config.default_branch
        if self.auto_commit_interval is None:
            self.auto_commit_interval = git_config.auto_commit_interval


@dataclass
class TaskContext:
    """Context for a running task."""

    task_id: str
    prompt: str
    started_at: datetime
    workflow_result: Optional[WorkflowResult] = None
    messages: list[AgentMessage] = field(default_factory=list)


class ProjectAgent:
    """
    Agent dedicated to a single project.

    Manages:
    - Claude Agent SDK interactions for the project
    - Git workflow (branching, commits, PRs)
    - NATS communication with namespaced subjects
    """

    def __init__(
        self,
        config: ProjectConfig,
        parent_orchestrator: Optional["OrchestratorAgent"] = None,
    ):
        """
        Initialize the project agent.

        Args:
            config: Project configuration.
            parent_orchestrator: Parent orchestrator (if any).
        """
        self.config = config
        self.orchestrator = parent_orchestrator
        self.agent_id = f"project-{config.project_id.replace('/', '-')}"

        # Core agent for Claude SDK
        self._core_agent: Optional[CogentAgent] = None

        # Workflow manager for Git/GitHub
        self._workflow: Optional[WorkflowManager] = None

        # Current task context
        self._current_task: Optional[TaskContext] = None

        # State
        self._running = False
        self._ready = False

    @property
    def is_ready(self) -> bool:
        """Check if agent is ready."""
        return self._ready and self._running

    @property
    def project_id(self) -> str:
        """Get project ID."""
        return self.config.project_id

    @property
    def project_path(self) -> Path:
        """Get project path."""
        return self.config.project_path

    async def start(self) -> None:
        """Start the project agent."""
        logger.info("Starting project agent", project=self.project_id)

        # Initialize core agent
        from .config import get_config

        agent_config = get_config()
        self._core_agent = CogentAgent(config=agent_config)

        # Initialize authentication
        auth_status = await self._core_agent.initialize()
        if not auth_status.is_valid:
            raise RuntimeError(f"Authentication failed: {auth_status.message}")

        # Initialize workflow manager
        workflow_config = WorkflowConfig(
            auto_commit_interval=self.config.auto_commit_interval if self.config.auto_commit else 0,
            run_tests_before_pr=self.config.run_tests,
            auto_create_pr=self.config.auto_create_pr,
            auto_merge=self.config.auto_merge,
            default_base_branch=self.config.default_branch,
        )

        self._workflow = WorkflowManager(
            project_dir=self.config.project_path,
            config=workflow_config,
            git_author_name=self.config.git_author_name,
            git_author_email=self.config.git_author_email,
        )

        # Register workflow event handler
        self._workflow.on_event(self._handle_workflow_event)

        self._running = True
        self._ready = True

        logger.info("Project agent started", project=self.project_id)

    async def stop(self) -> None:
        """Stop the project agent."""
        logger.info("Stopping project agent", project=self.project_id)

        self._running = False
        self._ready = False

        if self._core_agent:
            await self._core_agent.shutdown()

        logger.info("Project agent stopped", project=self.project_id)

    async def execute_task(self, prompt: str, task_id: str) -> TaskResult:
        """
        Execute a development task.

        This is the main entry point for task execution:
        1. Creates feature branch
        2. Executes the prompt via Claude
        3. Commits changes
        4. Runs tests
        5. Creates PR with auto-merge

        Args:
            prompt: Task description/instructions.
            task_id: Unique task identifier.

        Returns:
            TaskResult with execution outcome.
        """
        if not self.is_ready:
            return TaskResult(
                success=False,
                output="",
                error="Agent not ready",
            )

        # Create task context
        self._current_task = TaskContext(
            task_id=task_id,
            prompt=prompt,
            started_at=datetime.utcnow(),
        )

        logger.info(
            "Executing task",
            project=self.project_id,
            task_id=task_id,
        )

        try:
            # Step 1: Start workflow (create feature branch)
            workflow_result = await self._workflow.start_task(
                task_description=prompt[:50],
                task_id=task_id,
            )

            if not workflow_result.success:
                return TaskResult(
                    success=False,
                    output="",
                    error=f"Failed to start workflow: {workflow_result.error}",
                )

            # Step 2: Execute the task via Claude
            system_prompt = self._build_system_prompt()

            task_result = await self._core_agent.execute_task(
                prompt=prompt,
                working_dir=self.config.project_path,
                system_prompt=system_prompt,
            )

            # Collect messages
            self._current_task.messages.append(
                AgentMessage(role="user", content=prompt)
            )
            self._current_task.messages.append(
                AgentMessage(role="assistant", content=task_result.output)
            )

            if not task_result.success:
                # Still try to commit what we have
                await self._workflow.commit_changes(
                    f"WIP: {prompt[:50]} (task failed)"
                )
                return task_result

            # Step 3: Complete workflow (tests, PR, merge)
            workflow_result = await self._workflow.complete_task(
                pr_title=f"[{task_id}] {prompt[:50]}",
            )

            self._current_task.workflow_result = workflow_result

            # Build final result
            final_output = task_result.output
            if workflow_result.pull_request:
                final_output += f"\n\n---\nPR Created: {workflow_result.pull_request.url}"

            return TaskResult(
                success=workflow_result.success,
                output=final_output,
                tool_calls=task_result.tool_calls,
                metadata={
                    "task_id": task_id,
                    "branch": workflow_result.branch,
                    "pr_url": workflow_result.pull_request.url if workflow_result.pull_request else None,
                    "test_passed": workflow_result.test_result.success if workflow_result.test_result else None,
                },
            )

        except Exception as e:
            logger.exception("Task execution failed", task_id=task_id)
            return TaskResult(
                success=False,
                output="",
                error=str(e),
            )

        finally:
            self._current_task = None

    async def execute_simple_task(self, prompt: str) -> TaskResult:
        """
        Execute a simple task without Git workflow.

        For quick queries or tasks that don't need commits.
        """
        if not self.is_ready:
            return TaskResult(
                success=False,
                output="",
                error="Agent not ready",
            )

        return await self._core_agent.execute_task(
            prompt=prompt,
            working_dir=self.config.project_path,
        )

    def _build_system_prompt(self) -> str:
        """Build system prompt with project context."""
        return f"""You are a Cogent Agent working on the project: {self.config.project_id}

Project Path: {self.config.project_path}
Working Area: {self.config.working_area}

Your changes will be automatically committed and a pull request will be created.

Guidelines:
- Focus on the specific task at hand
- Write clean, well-documented code
- Follow existing project conventions
- Add tests for new functionality
- Make atomic, focused changes

After completing your work, summarize what you did and any follow-up items.
"""

    async def _handle_workflow_event(self, event: str, data: dict) -> None:
        """Handle workflow events and forward to orchestrator."""
        logger.debug(
            "Workflow event",
            project=self.project_id,
            event=event,
            data=data,
        )

        # Could forward to orchestrator via NATS here
        if self.orchestrator and self.orchestrator.nats:
            from ..communication.nats_handler import MessageType

            await self.orchestrator.nats.publish_event(
                MessageType.AGENT_MESSAGE,
                {
                    "project_id": self.project_id,
                    "event": event,
                    "data": data,
                },
            )

    async def get_status(self) -> dict[str, Any]:
        """Get project agent status."""
        workflow_status = {}
        if self._workflow:
            workflow_status = await self._workflow.get_status()

        return {
            "project_id": self.project_id,
            "ready": self.is_ready,
            "running": self._running,
            "current_task": self._current_task.task_id if self._current_task else None,
            "workflow": workflow_status,
        }
