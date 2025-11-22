"""
Skills Manager for self-modifying agent capabilities.

Manages:
- Skills discovery and loading
- Slash commands
- CLAUDE.md files
- PR-based workflow for skill modifications
- Import/export to assets registry
"""

import asyncio
import json
import shutil
import tarfile
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from io import BytesIO
from pathlib import Path
from typing import Any, Optional

import structlog

from ..github.git_manager import GitManager
from ..github.github_client import GitHubClient

logger = structlog.get_logger(__name__)


class AssetType(Enum):
    """Types of manageable assets."""

    SKILL = "skill"
    COMMAND = "command"
    CLAUDE_MD = "claude_md"


@dataclass
class Skill:
    """A skill definition."""

    name: str
    path: Path
    description: str = ""
    version: str = "1.0.0"
    enabled: bool = True
    scripts: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)


@dataclass
class Command:
    """A slash command definition."""

    name: str
    path: Path
    description: str = ""
    content: str = ""


@dataclass
class AssetChange:
    """A proposed change to an asset."""

    asset_type: AssetType
    asset_name: str
    action: str  # create, update, delete
    old_content: Optional[str] = None
    new_content: Optional[str] = None
    pr_number: Optional[int] = None
    pr_url: Optional[str] = None


class SkillsManager:
    """
    Manages agent skills, commands, and CLAUDE.md files.

    Features:
    - Automatic discovery of skills and commands
    - PR-based workflow for modifications
    - Hot-reload of approved changes
    - Import/export for asset sharing
    """

    def __init__(
        self,
        assets_dir: Path,
        assets_repo_url: Optional[str] = None,
    ):
        """
        Initialize the skills manager.

        Args:
            assets_dir: Path to assets directory.
            assets_repo_url: Optional Git repository URL for assets.
        """
        self.assets_dir = Path(assets_dir)
        self.assets_repo_url = assets_repo_url

        # Asset paths
        self.skills_dir = self.assets_dir / "skills"
        self.commands_dir = self.assets_dir / "commands"
        self.claude_md_path = self.assets_dir / "CLAUDE.md"

        # Git integration for PR workflow
        self._git: Optional[GitManager] = None
        self._github: Optional[GitHubClient] = None

        # Loaded assets
        self._skills: dict[str, Skill] = {}
        self._commands: dict[str, Command] = {}
        self._claude_md: Optional[str] = None

        # Pending changes (awaiting PR approval)
        self._pending_changes: list[AssetChange] = []

    async def initialize(self) -> None:
        """Initialize the skills manager."""
        logger.info("Initializing skills manager", path=str(self.assets_dir))

        # Create directories if needed
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        self.commands_dir.mkdir(parents=True, exist_ok=True)

        # Initialize Git if assets directory is a repo
        if (self.assets_dir / ".git").exists() or self.assets_repo_url:
            self._git = GitManager(
                project_dir=self.assets_dir,
                author_name="Cogent Agent",
                author_email="cogent@localhost",
            )
            self._github = GitHubClient(project_dir=self.assets_dir)

            # Clone/init repo if needed
            if self.assets_repo_url and not (self.assets_dir / ".git").exists():
                await self._clone_assets_repo()

        # Load existing assets
        await self.reload_assets()

        logger.info(
            "Skills manager initialized",
            skills=len(self._skills),
            commands=len(self._commands),
        )

    async def _clone_assets_repo(self) -> None:
        """Clone the assets repository."""
        # This would use git clone - simplified for now
        await self._git.init_repo()
        if self.assets_repo_url:
            await self._git.add_remote("origin", self.assets_repo_url)
            try:
                await self._git.pull()
            except Exception:
                pass  # May be empty repo

    async def reload_assets(self) -> None:
        """Reload all assets from disk."""
        self._skills = {}
        self._commands = {}

        # Load skills
        if self.skills_dir.exists():
            for skill_path in self.skills_dir.iterdir():
                if skill_path.is_dir():
                    skill = await self._load_skill(skill_path)
                    if skill:
                        self._skills[skill.name] = skill

        # Load commands
        if self.commands_dir.exists():
            for cmd_path in self.commands_dir.glob("*.md"):
                command = await self._load_command(cmd_path)
                if command:
                    self._commands[command.name] = command

        # Load CLAUDE.md
        if self.claude_md_path.exists():
            self._claude_md = self.claude_md_path.read_text()

    async def _load_skill(self, skill_path: Path) -> Optional[Skill]:
        """Load a skill from directory."""
        skill_md = skill_path / "SKILL.md"
        if not skill_md.exists():
            return None

        content = skill_md.read_text()

        # Parse description from first paragraph
        lines = content.split("\n")
        description = ""
        for line in lines[1:]:  # Skip title
            if line.strip():
                description = line.strip()
                break

        # Find scripts
        scripts = []
        scripts_dir = skill_path / "scripts"
        if scripts_dir.exists():
            scripts = [f.name for f in scripts_dir.glob("*") if f.is_file()]

        return Skill(
            name=skill_path.name,
            path=skill_path,
            description=description,
            scripts=scripts,
        )

    async def _load_command(self, cmd_path: Path) -> Optional[Command]:
        """Load a command from file."""
        content = cmd_path.read_text()

        # Parse description from first line after title
        lines = content.split("\n")
        description = ""
        for line in lines[1:]:
            if line.strip() and not line.startswith("#"):
                description = line.strip()
                break

        return Command(
            name=cmd_path.stem,
            path=cmd_path,
            description=description,
            content=content,
        )

    # Asset access

    def get_skill(self, name: str) -> Optional[Skill]:
        """Get a skill by name."""
        return self._skills.get(name)

    def get_command(self, name: str) -> Optional[Command]:
        """Get a command by name."""
        return self._commands.get(name)

    def list_skills(self) -> list[Skill]:
        """List all loaded skills."""
        return list(self._skills.values())

    def list_commands(self) -> list[Command]:
        """List all loaded commands."""
        return list(self._commands.values())

    def get_claude_md(self) -> Optional[str]:
        """Get the CLAUDE.md content."""
        return self._claude_md

    # Modification with PR workflow

    async def propose_skill_change(
        self,
        name: str,
        skill_md_content: str,
        scripts: Optional[dict[str, str]] = None,
        description: str = "",
    ) -> AssetChange:
        """
        Propose a skill change via PR workflow.

        Args:
            name: Skill name.
            skill_md_content: Content for SKILL.md.
            scripts: Optional dict of script_name -> content.
            description: Change description for PR.

        Returns:
            AssetChange with PR info.
        """
        if not self._git:
            raise RuntimeError("Git not configured for assets directory")

        # Create branch for the change
        branch_name = f"skill/{name}-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
        await self._git.create_branch(branch_name)

        # Make changes
        skill_path = self.skills_dir / name
        skill_path.mkdir(parents=True, exist_ok=True)

        old_content = None
        skill_md_path = skill_path / "SKILL.md"
        if skill_md_path.exists():
            old_content = skill_md_path.read_text()

        skill_md_path.write_text(skill_md_content)

        # Write scripts if provided
        if scripts:
            scripts_dir = skill_path / "scripts"
            scripts_dir.mkdir(exist_ok=True)
            for script_name, script_content in scripts.items():
                (scripts_dir / script_name).write_text(script_content)

        # Commit changes
        await self._git.stage_files()
        action = "update" if old_content else "create"
        await self._git.commit(f"{action.capitalize()} skill: {name}\n\n{description}")

        # Push and create PR
        await self._git.push(set_upstream=True)

        pr = await self._github.create_pull_request(
            title=f"[Skill] {action.capitalize()} {name}",
            body=f"""## Skill Change

**Action**: {action.capitalize()}
**Skill**: {name}

### Description
{description or "No description provided."}

### Changes
- SKILL.md: {'Updated' if old_content else 'Created'}
{chr(10).join(f'- scripts/{s}: Created' for s in (scripts or {}).keys())}

---
🤖 Auto-generated by Cogent Agent
""",
            draft=False,
        )

        # Switch back to main branch
        await self._git.checkout_branch("main")

        change = AssetChange(
            asset_type=AssetType.SKILL,
            asset_name=name,
            action=action,
            old_content=old_content,
            new_content=skill_md_content,
            pr_number=pr.number,
            pr_url=pr.url,
        )

        self._pending_changes.append(change)

        logger.info(
            "Proposed skill change",
            skill=name,
            action=action,
            pr=pr.number,
        )

        return change

    async def propose_command_change(
        self,
        name: str,
        content: str,
        description: str = "",
    ) -> AssetChange:
        """
        Propose a command change via PR workflow.

        Args:
            name: Command name (without .md extension).
            content: Command markdown content.
            description: Change description for PR.

        Returns:
            AssetChange with PR info.
        """
        if not self._git:
            raise RuntimeError("Git not configured for assets directory")

        # Create branch
        branch_name = f"command/{name}-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
        await self._git.create_branch(branch_name)

        # Make changes
        cmd_path = self.commands_dir / f"{name}.md"
        old_content = cmd_path.read_text() if cmd_path.exists() else None
        cmd_path.write_text(content)

        # Commit and PR
        await self._git.stage_files()
        action = "update" if old_content else "create"
        await self._git.commit(f"{action.capitalize()} command: {name}")
        await self._git.push(set_upstream=True)

        pr = await self._github.create_pull_request(
            title=f"[Command] {action.capitalize()} /{name}",
            body=f"## Command Change\n\n**Action**: {action}\n**Command**: /{name}\n\n{description}",
        )

        await self._git.checkout_branch("main")

        change = AssetChange(
            asset_type=AssetType.COMMAND,
            asset_name=name,
            action=action,
            old_content=old_content,
            new_content=content,
            pr_number=pr.number,
            pr_url=pr.url,
        )

        self._pending_changes.append(change)
        return change

    async def check_pending_changes(self) -> list[AssetChange]:
        """
        Check status of pending changes and apply merged ones.

        Returns:
            List of newly applied changes.
        """
        if not self._github:
            return []

        applied = []

        for change in self._pending_changes[:]:
            if change.pr_number:
                pr = await self._github.get_pull_request(change.pr_number)

                if pr.state.value == "merged":
                    # PR was merged, reload assets
                    await self._git.checkout_branch("main")
                    await self._git.pull()
                    await self.reload_assets()

                    applied.append(change)
                    self._pending_changes.remove(change)

                    logger.info(
                        "Applied merged change",
                        type=change.asset_type.value,
                        name=change.asset_name,
                    )

                elif pr.state.value == "closed":
                    # PR was closed without merging
                    self._pending_changes.remove(change)
                    logger.info(
                        "Change rejected",
                        type=change.asset_type.value,
                        name=change.asset_name,
                    )

        return applied

    # Import/Export

    async def export_assets(
        self,
        output_path: Optional[Path] = None,
        include_skills: bool = True,
        include_commands: bool = True,
        include_claude_md: bool = True,
    ) -> bytes:
        """
        Export assets as a tarball.

        Args:
            output_path: Optional path to write tarball.
            include_skills: Include skills in export.
            include_commands: Include commands in export.
            include_claude_md: Include CLAUDE.md in export.

        Returns:
            Tarball bytes.
        """
        buffer = BytesIO()

        with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
            # Add manifest
            manifest = {
                "version": "1.0",
                "exported_at": datetime.utcnow().isoformat(),
                "skills": list(self._skills.keys()) if include_skills else [],
                "commands": list(self._commands.keys()) if include_commands else [],
                "has_claude_md": include_claude_md and bool(self._claude_md),
            }
            manifest_data = json.dumps(manifest, indent=2).encode()
            manifest_info = tarfile.TarInfo(name="manifest.json")
            manifest_info.size = len(manifest_data)
            tar.addfile(manifest_info, BytesIO(manifest_data))

            # Add skills
            if include_skills:
                for skill in self._skills.values():
                    # Add skill directory recursively
                    for file_path in skill.path.rglob("*"):
                        if file_path.is_file():
                            arcname = f"skills/{skill.name}/{file_path.relative_to(skill.path)}"
                            tar.add(file_path, arcname=arcname)

            # Add commands
            if include_commands:
                for command in self._commands.values():
                    arcname = f"commands/{command.name}.md"
                    tar.add(command.path, arcname=arcname)

            # Add CLAUDE.md
            if include_claude_md and self._claude_md:
                data = self._claude_md.encode()
                info = tarfile.TarInfo(name="CLAUDE.md")
                info.size = len(data)
                tar.addfile(info, BytesIO(data))

        result = buffer.getvalue()

        if output_path:
            output_path.write_bytes(result)

        logger.info("Exported assets", size=len(result))
        return result

    async def import_assets(
        self,
        tarball: bytes,
        create_pr: bool = True,
    ) -> Optional[AssetChange]:
        """
        Import assets from a tarball.

        Args:
            tarball: Tarball bytes to import.
            create_pr: Whether to create a PR for the import.

        Returns:
            AssetChange if PR created, None if applied directly.
        """
        buffer = BytesIO(tarball)

        with tarfile.open(fileobj=buffer, mode="r:gz") as tar:
            # Read manifest
            manifest_file = tar.extractfile("manifest.json")
            if not manifest_file:
                raise ValueError("Invalid asset tarball: missing manifest")

            manifest = json.loads(manifest_file.read().decode())

            if create_pr and self._git:
                # Create branch for import
                branch_name = f"import/{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
                await self._git.create_branch(branch_name)

            # Extract files
            for member in tar.getmembers():
                if member.name == "manifest.json":
                    continue

                # Determine target path
                if member.name.startswith("skills/"):
                    target = self.skills_dir / member.name[7:]
                elif member.name.startswith("commands/"):
                    target = self.commands_dir / member.name[9:]
                elif member.name == "CLAUDE.md":
                    target = self.claude_md_path
                else:
                    continue

                # Extract
                target.parent.mkdir(parents=True, exist_ok=True)
                if member.isfile():
                    content = tar.extractfile(member)
                    if content:
                        target.write_bytes(content.read())

            if create_pr and self._git:
                # Commit and create PR
                await self._git.stage_files()
                await self._git.commit(f"Import assets ({len(manifest.get('skills', []))} skills, {len(manifest.get('commands', []))} commands)")
                await self._git.push(set_upstream=True)

                pr = await self._github.create_pull_request(
                    title="[Import] Asset import",
                    body=f"""## Asset Import

Imported:
- Skills: {', '.join(manifest.get('skills', [])) or 'None'}
- Commands: {', '.join(manifest.get('commands', [])) or 'None'}
- CLAUDE.md: {'Yes' if manifest.get('has_claude_md') else 'No'}
""",
                )

                await self._git.checkout_branch("main")

                return AssetChange(
                    asset_type=AssetType.SKILL,
                    asset_name="import",
                    action="import",
                    pr_number=pr.number,
                    pr_url=pr.url,
                )
            else:
                # Apply directly
                await self.reload_assets()
                return None

        logger.info("Imported assets", manifest=manifest)
