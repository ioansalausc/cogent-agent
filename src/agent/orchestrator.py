"""
Orchestrator Agent for multi-project management.

The Orchestrator is the root agent that:
- Manages multiple working areas and projects
- Routes commands to appropriate project agents
- Aggregates events from sub-agents
- Handles cross-project coordination
"""

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Optional

import structlog

from ..communication.nats_handler import MessageType, NATSHandler, NATSMessage
from .config import AgentConfig, get_config
from .project_agent import ProjectAgent, ProjectConfig

logger = structlog.get_logger(__name__)


class ProjectState(Enum):
    """State of a project agent."""

    PENDING = "pending"
    RUNNING = "running"
    IDLE = "idle"
    ERROR = "error"
    STOPPED = "stopped"


@dataclass
class ProjectInfo:
    """Information about a registered project."""

    project_id: str
    name: str
    working_area: str
    path: Path
    state: ProjectState
    agent: Optional[ProjectAgent] = None
    current_task: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    last_activity: datetime = field(default_factory=datetime.utcnow)


@dataclass
class WorkingArea:
    """A working area containing multiple projects."""

    name: str
    path: Path
    projects: dict[str, ProjectInfo] = field(default_factory=dict)


class OrchestratorAgent:
    """
    Root orchestrator that manages multiple project agents.

    Responsibilities:
    - Discover and register projects
    - Spawn and manage project agents
    - Route commands to appropriate agents
    - Aggregate and forward events
    """

    def __init__(
        self,
        config: Optional[AgentConfig] = None,
        nats_handler: Optional[NATSHandler] = None,
    ):
        """
        Initialize the orchestrator.

        Args:
            config: Agent configuration.
            nats_handler: NATS handler for communication.
        """
        self.config = config or get_config()
        self.nats = nats_handler
        self.orchestrator_id = f"orchestrator-{self.config.agent_id}"

        # Working areas and projects
        self._working_areas: dict[str, WorkingArea] = {}
        self._projects: dict[str, ProjectInfo] = {}

        # Command routing
        self._command_handlers = {
            "list_projects": self._handle_list_projects,
            "create_project": self._handle_create_project,
            "assign_task": self._handle_assign_task,
            "get_project_status": self._handle_get_project_status,
            "stop_project": self._handle_stop_project,
        }

        # Background tasks
        self._monitor_task: Optional[asyncio.Task] = None

    async def initialize(self) -> None:
        """Initialize the orchestrator."""
        logger.info("Initializing orchestrator", id=self.orchestrator_id)

        # Discover working areas and projects
        await self._discover_projects()

        # Setup NATS subscriptions for orchestrator commands
        if self.nats:
            await self._setup_subscriptions()

        # Start monitoring loop
        self._monitor_task = asyncio.create_task(self._monitor_loop())

        logger.info(
            "Orchestrator initialized",
            working_areas=len(self._working_areas),
            projects=len(self._projects),
        )

    async def shutdown(self) -> None:
        """Shutdown the orchestrator and all project agents."""
        logger.info("Shutting down orchestrator")

        # Stop monitoring
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass

        # Stop all project agents
        for project in self._projects.values():
            if project.agent:
                await project.agent.stop()

        logger.info("Orchestrator shutdown complete")

    async def _discover_projects(self) -> None:
        """Discover working areas and projects in the workspace."""
        workspace = self.config.workspace_dir

        if not workspace.exists():
            workspace.mkdir(parents=True)
            logger.info("Created workspace directory", path=str(workspace))
            return

        # Scan for working areas (first level directories)
        for area_path in workspace.iterdir():
            if area_path.is_dir() and not area_path.name.startswith("."):
                area = WorkingArea(
                    name=area_path.name,
                    path=area_path,
                )

                # Scan for projects (second level directories)
                for project_path in area_path.iterdir():
                    if project_path.is_dir() and not project_path.name.startswith("."):
                        project_id = f"{area.name}/{project_path.name}"
                        project_info = ProjectInfo(
                            project_id=project_id,
                            name=project_path.name,
                            working_area=area.name,
                            path=project_path,
                            state=ProjectState.PENDING,
                        )
                        area.projects[project_path.name] = project_info
                        self._projects[project_id] = project_info

                self._working_areas[area.name] = area

        logger.info(
            "Project discovery complete",
            areas=list(self._working_areas.keys()),
            projects=list(self._projects.keys()),
        )

    async def _setup_subscriptions(self) -> None:
        """Setup NATS subscriptions for orchestrator commands."""
        if not self.nats or not self.nats._nc:
            return

        # Subscribe to orchestrator command subject
        orchestrator_subject = f"orchestrator.{self.orchestrator_id}.command"
        await self.nats._nc.subscribe(
            orchestrator_subject,
            cb=self._handle_orchestrator_command,
        )

        # Subscribe to all project events for aggregation
        await self.nats._nc.subscribe(
            "agent.*.events.>",
            cb=self._handle_project_event,
        )

        logger.debug("Orchestrator subscriptions setup", subject=orchestrator_subject)

    async def _handle_orchestrator_command(self, msg) -> None:
        """Handle incoming orchestrator commands."""
        try:
            nats_msg = NATSMessage.from_json(msg.data)
            command = nats_msg.payload.get("command")

            handler = self._command_handlers.get(command)
            if handler:
                response = await handler(nats_msg.payload)
            else:
                response = {"success": False, "error": f"Unknown command: {command}"}

            if msg.reply:
                reply_msg = NATSMessage(
                    type=MessageType.TASK_COMPLETED,
                    agent_id=self.orchestrator_id,
                    payload=response,
                    correlation_id=nats_msg.correlation_id,
                )
                await self.nats._nc.publish(msg.reply, reply_msg.to_json())

        except Exception as e:
            logger.exception("Error handling orchestrator command")

    async def _handle_project_event(self, msg) -> None:
        """Handle and aggregate events from project agents."""
        try:
            # Parse subject to get agent ID
            parts = msg.subject.split(".")
            if len(parts) >= 2:
                agent_id = parts[1]

                # Forward to orchestrator event stream
                if self.nats:
                    await self.nats.publish_event(
                        MessageType.AGENT_MESSAGE,
                        {
                            "source_agent": agent_id,
                            "original_subject": msg.subject,
                            "payload": msg.data.decode(),
                        },
                    )

        except Exception as e:
            logger.error("Error handling project event", error=str(e))

    async def _monitor_loop(self) -> None:
        """Monitor project agents and handle health checks."""
        while True:
            try:
                await asyncio.sleep(60)  # Check every minute

                for project in self._projects.values():
                    if project.agent and project.state == ProjectState.RUNNING:
                        # Check if agent is still responsive
                        if not project.agent.is_ready:
                            project.state = ProjectState.ERROR
                            logger.warning(
                                "Project agent unresponsive",
                                project=project.project_id,
                            )

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Monitor loop error", error=str(e))

    # Command handlers

    async def _handle_list_projects(self, payload: dict) -> dict:
        """List all registered projects."""
        projects = []
        for project in self._projects.values():
            projects.append({
                "project_id": project.project_id,
                "name": project.name,
                "working_area": project.working_area,
                "state": project.state.value,
                "current_task": project.current_task,
                "last_activity": project.last_activity.isoformat(),
            })

        return {"success": True, "projects": projects}

    async def _handle_create_project(self, payload: dict) -> dict:
        """Create a new project."""
        working_area = payload.get("working_area", "default")
        name = payload.get("name")

        if not name:
            return {"success": False, "error": "Project name required"}

        # Create working area if needed
        area_path = self.config.workspace_dir / working_area
        if working_area not in self._working_areas:
            area_path.mkdir(parents=True, exist_ok=True)
            self._working_areas[working_area] = WorkingArea(
                name=working_area,
                path=area_path,
            )

        # Create project directory
        project_path = area_path / name
        if project_path.exists():
            return {"success": False, "error": f"Project {name} already exists"}

        project_path.mkdir(parents=True)

        # Register project
        project_id = f"{working_area}/{name}"
        project_info = ProjectInfo(
            project_id=project_id,
            name=name,
            working_area=working_area,
            path=project_path,
            state=ProjectState.PENDING,
        )
        self._projects[project_id] = project_info
        self._working_areas[working_area].projects[name] = project_info

        logger.info("Created project", project_id=project_id)

        return {
            "success": True,
            "project_id": project_id,
            "path": str(project_path),
        }

    async def _handle_assign_task(self, payload: dict) -> dict:
        """Assign a task to a project agent."""
        project_id = payload.get("project_id")
        prompt = payload.get("prompt")

        if not project_id or not prompt:
            return {"success": False, "error": "project_id and prompt required"}

        project = self._projects.get(project_id)
        if not project:
            return {"success": False, "error": f"Project not found: {project_id}"}

        # Start project agent if needed
        if not project.agent:
            await self._start_project_agent(project)

        # Assign task
        task_id = str(uuid.uuid4())[:8]
        project.current_task = task_id
        project.last_activity = datetime.utcnow()

        # Execute task via project agent
        asyncio.create_task(
            self._execute_project_task(project, prompt, task_id)
        )

        return {
            "success": True,
            "task_id": task_id,
            "project_id": project_id,
        }

    async def _handle_get_project_status(self, payload: dict) -> dict:
        """Get status of a specific project."""
        project_id = payload.get("project_id")

        project = self._projects.get(project_id)
        if not project:
            return {"success": False, "error": f"Project not found: {project_id}"}

        return {
            "success": True,
            "project_id": project.project_id,
            "name": project.name,
            "state": project.state.value,
            "current_task": project.current_task,
            "has_agent": project.agent is not None,
        }

    async def _handle_stop_project(self, payload: dict) -> dict:
        """Stop a project agent."""
        project_id = payload.get("project_id")

        project = self._projects.get(project_id)
        if not project:
            return {"success": False, "error": f"Project not found: {project_id}"}

        if project.agent:
            await project.agent.stop()
            project.agent = None
            project.state = ProjectState.STOPPED

        return {"success": True, "project_id": project_id}

    async def _start_project_agent(self, project: ProjectInfo) -> None:
        """Start a project agent."""
        logger.info("Starting project agent", project=project.project_id)

        project_config = ProjectConfig(
            project_id=project.project_id,
            project_path=project.path,
            working_area=project.working_area,
        )

        project.agent = ProjectAgent(
            config=project_config,
            parent_orchestrator=self,
        )

        await project.agent.start()
        project.state = ProjectState.RUNNING

    async def _execute_project_task(
        self,
        project: ProjectInfo,
        prompt: str,
        task_id: str,
    ) -> None:
        """Execute a task via project agent."""
        try:
            if project.agent:
                result = await project.agent.execute_task(prompt, task_id)

                # Publish completion event
                if self.nats:
                    await self.nats.publish_event(
                        MessageType.TASK_COMPLETED,
                        {
                            "project_id": project.project_id,
                            "task_id": task_id,
                            "success": result.success,
                        },
                    )

        except Exception as e:
            logger.exception("Task execution failed", project=project.project_id)
            project.state = ProjectState.ERROR

        finally:
            project.current_task = None
            project.last_activity = datetime.utcnow()
