"""
CLI main entry point for Cogent Agent.

Provides both interactive REPL and single-shot command modes.
"""

import asyncio
import os
import sys
from typing import Optional

import click
from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.history import FileHistory
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.spinner import Spinner
from rich.table import Table

import re

from .client import CogentClient


def clean_content(text: str) -> str:
    """Remove terminal artifacts and box-drawing characters from content."""
    if not text:
        return ""

    # Remove ANSI escape sequences
    text = re.sub(r'\x1b\[[0-9;]*[A-Za-z]', '', text)
    text = re.sub(r'\x1b\][^\x07]*\x07', '', text)

    # Remove lines that are only box-drawing characters
    lines = text.split('\n')
    cleaned_lines = []
    for line in lines:
        stripped = line.strip()
        # Skip if line is only box-drawing chars, dashes, or empty
        if stripped and not re.match(r'^[─━│┃┌┐└┘├┤┬┴┼═║╔╗╚╝╠╣╦╩╬\-]+$', stripped):
            cleaned_lines.append(line)

    return '\n'.join(cleaned_lines).strip()


# Rich console for output
console = Console()


class CogentCLI:
    """Interactive CLI for Cogent Agent."""

    def __init__(
        self,
        nats_url: str = "nats://localhost:4222",
        agent_id: str = "cogent-agent-001",
    ):
        """Initialize the CLI."""
        self.client = CogentClient(nats_url=nats_url, agent_id=agent_id)
        self.agent_id = agent_id
        self._running = False

        # Prompt session with history
        history_file = os.path.expanduser("~/.cogent_history")
        self.session = PromptSession(
            history=FileHistory(history_file),
            auto_suggest=AutoSuggestFromHistory(),
        )

    async def connect(self) -> bool:
        """Connect to NATS and verify agent availability."""
        try:
            await self.client.connect()

            # Check agent status
            with console.status("Checking agent status..."):
                response = await self.client.get_status()

            if response.success:
                console.print(
                    f"[green]Connected to agent:[/green] {self.agent_id}"
                )
                console.print(
                    f"[dim]Agent state:[/dim] {response.data.get('state', 'unknown')}"
                )
                return True
            else:
                console.print(
                    f"[yellow]Warning:[/yellow] Agent not responding: {response.error}"
                )
                return True  # Still connected to NATS

        except Exception as e:
            console.print(f"[red]Connection failed:[/red] {e}")
            return False

    async def disconnect(self) -> None:
        """Disconnect from NATS."""
        await self.client.disconnect()

    async def execute_task(self, prompt: str) -> None:
        """Execute a task and stream the output."""
        # Send the task
        console.print(f"\n[dim]Sending task to agent...[/dim]")

        response = await self.client.execute_task(prompt)

        if not response.success:
            console.print(f"[red]Error:[/red] {response.error}")
            return

        console.print(f"[green]Task started[/green]")
        console.print(f"[dim]Correlation ID: {response.data.get('correlation_id', 'N/A')}[/dim]\n")

        # Stream events
        console.print(f"[dim]{'─' * 60}[/dim]")

        try:
            async for event in self.client.stream_events():
                event_type = event.get("type", "")
                payload = event.get("payload", {})

                if event_type == "task_progress":
                    role = payload.get("role", "")
                    content = clean_content(payload.get("content", ""))

                    # Skip empty content after cleaning
                    if not content:
                        continue

                    if role == "assistant":
                        # Render markdown content
                        md = Markdown(content)
                        console.print(md)
                    elif role == "tool_use":
                        console.print(f"[cyan]→ {content}[/cyan]")
                    elif role == "tool_result":
                        console.print(f"[dim]{content[:200]}...[/dim]" if len(content) > 200 else f"[dim]{content}[/dim]")
                    elif role == "error":
                        console.print(f"[red]Error: {content}[/red]")

                elif event_type == "task_completed":
                    console.print(f"\n[dim]{'─' * 60}[/dim]")
                    console.print("[green]Task completed[/green]")
                    break

                elif event_type == "task_failed":
                    console.print(f"\n[dim]{'─' * 60}[/dim]")
                    console.print(f"[red]Task failed:[/red] {payload.get('error', 'Unknown error')}")
                    break

        except KeyboardInterrupt:
            console.print("\n[yellow]Interrupted - cancelling task...[/yellow]")
            await self.client.cancel_task()

    async def show_status(self) -> None:
        """Show agent status."""
        response = await self.client.get_status()

        if response.success:
            table = Table(title="Agent Status")
            table.add_column("Property", style="cyan")
            table.add_column("Value", style="green")

            table.add_row("Agent ID", response.data.get("agent_id", "N/A"))
            table.add_row("State", response.data.get("state", "N/A"))
            table.add_row("Ready", "Yes" if response.data.get("is_ready") else "No")
            table.add_row("Active Task", "Yes" if response.data.get("has_active_task") else "No")

            console.print(table)
        else:
            console.print(f"[red]Error:[/red] {response.error}")

    async def handle_command(self, line: str) -> bool:
        """
        Handle a command line input.

        Returns:
            False if should exit, True otherwise.
        """
        line = line.strip()

        if not line:
            return True

        # Built-in commands
        if line.startswith("/"):
            cmd_parts = line[1:].split(maxsplit=1)
            cmd = cmd_parts[0].lower()
            args = cmd_parts[1] if len(cmd_parts) > 1 else ""

            if cmd in ("exit", "quit", "q"):
                return False

            elif cmd == "status":
                await self.show_status()

            elif cmd == "cancel":
                response = await self.client.cancel_task()
                if response.success:
                    console.print("[yellow]Task cancellation requested[/yellow]")
                else:
                    console.print(f"[red]Error:[/red] {response.error}")

            elif cmd == "help":
                self.show_help()

            elif cmd == "clear":
                console.clear()

            else:
                console.print(f"[yellow]Unknown command:[/yellow] /{cmd}")
                console.print("Type /help for available commands")

        else:
            # Execute as task
            await self.execute_task(line)

        return True

    def show_help(self) -> None:
        """Show help message."""
        help_text = """
[bold]Cogent Agent CLI[/bold]

[cyan]Commands:[/cyan]
  /status    Show agent status
  /cancel    Cancel current task
  /clear     Clear the screen
  /help      Show this help message
  /exit      Exit the CLI

[cyan]Usage:[/cyan]
  Type any text to send it as a task to the agent.
  The agent will process your request and stream the response.

[cyan]Examples:[/cyan]
  > Create a Python function that calculates fibonacci numbers
  > Read the file main.py and explain what it does
  > Fix the bug in the login function
"""
        console.print(Panel(help_text, title="Help", border_style="blue"))

    def show_banner(self) -> None:
        """Show welcome banner."""
        banner = """
[bold cyan]╔═══════════════════════════════════════════════════════════╗
║                     Cogent Agent CLI                      ║
║                                                           ║
║  Type your task or /help for commands                     ║
╚═══════════════════════════════════════════════════════════╝[/bold cyan]
"""
        console.print(banner)

    async def run_repl(self) -> None:
        """Run the interactive REPL."""
        self.show_banner()

        if not await self.connect():
            return

        self._running = True

        try:
            while self._running:
                try:
                    # Get input with prompt
                    line = await asyncio.get_event_loop().run_in_executor(
                        None,
                        lambda: self.session.prompt("cogent> "),
                    )

                    if not await self.handle_command(line):
                        break

                except KeyboardInterrupt:
                    console.print("\n[yellow]Use /exit to quit[/yellow]")
                except EOFError:
                    break

        finally:
            self._running = False
            await self.disconnect()
            console.print("[dim]Goodbye![/dim]")


# Click CLI commands

@click.group(invoke_without_command=True)
@click.option(
    "--nats-url",
    envvar="NATS_URL",
    default="nats://localhost:4222",
    help="NATS server URL",
)
@click.option(
    "--agent-id",
    envvar="AGENT_ID",
    default="cogent-agent-001",
    help="Target agent ID",
)
@click.pass_context
def cli(ctx, nats_url: str, agent_id: str):
    """Cogent Agent CLI - Interact with AI agents via NATS."""
    ctx.ensure_object(dict)
    ctx.obj["nats_url"] = nats_url
    ctx.obj["agent_id"] = agent_id

    if ctx.invoked_subcommand is None:
        # Run interactive REPL
        cogent_cli = CogentCLI(nats_url=nats_url, agent_id=agent_id)
        asyncio.run(cogent_cli.run_repl())


@cli.command()
@click.argument("prompt")
@click.pass_context
def run(ctx, prompt: str):
    """Execute a single task and exit."""
    async def execute():
        cogent_cli = CogentCLI(
            nats_url=ctx.obj["nats_url"],
            agent_id=ctx.obj["agent_id"],
        )

        if await cogent_cli.connect():
            await cogent_cli.execute_task(prompt)
            await cogent_cli.disconnect()

    asyncio.run(execute())


@cli.command()
@click.pass_context
def status(ctx):
    """Get agent status."""
    async def get_status():
        cogent_cli = CogentCLI(
            nats_url=ctx.obj["nats_url"],
            agent_id=ctx.obj["agent_id"],
        )

        if await cogent_cli.connect():
            await cogent_cli.show_status()
            await cogent_cli.disconnect()

    asyncio.run(get_status())


def main():
    """Main entry point."""
    cli()


if __name__ == "__main__":
    main()
