"""Interactive chat session engine for Planner conversations."""

import json
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

from nova.agent import call_agent_stream
from nova.config import ModelConfig
from nova.models import AgentRole

console = Console()

TRANSITION_KEYWORDS: dict[str, str] = {
    "approved": "approved",
    "approve": "approved",
    "lock it in": "approved",
    "lock it": "approved",
    "lgtm": "approved",
    "ready for spec": "ready_for_spec",
    "ready for plan": "ready_for_plan",
    "ready for tasks": "ready_for_tasks",
}


# ---------------------------------------------------------------------------
# Session persistence
# ---------------------------------------------------------------------------

def _session_path(project_name: str, phase: str, version: str, logs_dir: Path) -> Path:
    return logs_dir / "sessions" / f"{project_name}-{phase}-{version}.json"


def save_session(
    messages: list[dict[str, str]],
    project_name: str,
    phase: str,
    version: str,
    logs_dir: Path,
) -> Path:
    path = _session_path(project_name, phase, version, logs_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(messages, indent=2))
    return path


def load_session(
    project_name: str,
    phase: str,
    version: str,
    logs_dir: Path,
) -> list[dict[str, str]]:
    path = _session_path(project_name, phase, version, logs_dir)
    if not path.exists():
        return []
    return json.loads(path.read_text())


# ---------------------------------------------------------------------------
# Transition detection
# ---------------------------------------------------------------------------

def detect_transition(user_input: str) -> str | None:
    """Check if user input contains a phase transition keyword.

    Returns the transition action string or None.
    """
    lower = user_input.strip().lower()
    for keyword, action in TRANSITION_KEYWORDS.items():
        if keyword in lower:
            return action
    return None


# ---------------------------------------------------------------------------
# Chat loop
# ---------------------------------------------------------------------------

def run_chat_session(
    project_name: str,
    phase: str,
    version: str,
    system_prompt: str,
    model_config: ModelConfig,
    logs_dir: Path,
    on_transition: Callable | None = None,
    initial_message: str | None = None,
) -> list[dict[str, str]]:
    """Run an interactive chat session with the Planner.

    Args:
        project_name: Name of the project.
        phase: Current phase (brainstorm, spec, plan, tasks).
        version: Version string (v1, v2, etc.).
        system_prompt: Assembled system prompt for the Planner.
        model_config: Model configuration for the Planner.
        logs_dir: Path to the project's logs directory.
        on_transition: Callback when a phase transition is detected.
            Receives (action, messages) and returns True to end the session.
        initial_message: If set and session is new, auto-sends this as the first
            user message so the Planner starts working immediately.

    Returns:
        The full conversation history.
    """
    messages = load_session(project_name, phase, version, logs_dir)

    if messages:
        console.print(
            Panel(
                f"Resuming {phase} session for [bold]{project_name}[/bold] ({version})\n"
                f"{len(messages)} messages loaded from previous session.",
                title="[bold]nova[/bold]",
                border_style="blue",
            )
        )
        _display_history_summary(messages)
    else:
        console.print(
            Panel(
                f"Starting {phase} session for [bold]{project_name}[/bold] ({version})\n"
                f"Type your thoughts, give feedback, or ask questions.\n"
                f"Type [bold]'exit'[/bold] to save and quit. "
                f"Type [bold]'approved'[/bold] when ready to move on.",
                title="[bold]nova[/bold]",
                border_style="green",
            )
        )

        if initial_message:
            messages.append({"role": "user", "content": initial_message})

            console.print()
            console.print("[bold green]nova (planner):[/bold green]")

            gen = call_agent_stream(
                role=AgentRole.PLANNER,
                system_prompt=system_prompt,
                model_config=model_config,
                messages=messages,
            )

            full_response = ""
            try:
                while True:
                    chunk = next(gen)
                    console.print(chunk, end="", highlight=False)
                    full_response += chunk
            except StopIteration as e:
                full_response_final, usage_meta = e.value
                if full_response_final:
                    full_response = full_response_final

            console.print()

            messages.append({"role": "assistant", "content": full_response})
            save_session(messages, project_name, phase, version, logs_dir)

    while True:
        console.print()
        try:
            user_input = console.input("[bold cyan]you:[/bold cyan] ")
        except (KeyboardInterrupt, EOFError):
            console.print("\n[yellow]Session interrupted. Saving...[/yellow]")
            save_session(messages, project_name, phase, version, logs_dir)
            break

        stripped = user_input.strip()

        if not stripped:
            continue

        if stripped.lower() == "exit":
            save_session(messages, project_name, phase, version, logs_dir)
            console.print("[green]Session saved.[/green]")
            break

        transition = detect_transition(stripped)
        if transition and on_transition:
            messages.append({"role": "user", "content": stripped})
            save_session(messages, project_name, phase, version, logs_dir)
            should_end = on_transition(transition, messages)
            if should_end:
                break
            continue

        messages.append({"role": "user", "content": stripped})

        console.print()
        console.print("[bold green]nova (planner):[/bold green]")

        gen = call_agent_stream(
            role=AgentRole.PLANNER,
            system_prompt=system_prompt,
            model_config=model_config,
            messages=messages,
        )

        full_response = ""
        try:
            while True:
                chunk = next(gen)
                console.print(chunk, end="", highlight=False)
                full_response += chunk
        except StopIteration as e:
            full_response_final, usage_meta = e.value
            if full_response_final:
                full_response = full_response_final

        console.print()

        messages.append({"role": "assistant", "content": full_response})
        save_session(messages, project_name, phase, version, logs_dir)

    return messages


def _display_history_summary(messages: list[dict[str, str]]) -> None:
    """Show a brief summary of the conversation so far."""
    user_count = sum(1 for m in messages if m["role"] == "user")
    assistant_count = sum(1 for m in messages if m["role"] == "assistant")
    console.print(f"  [dim]({user_count} messages from you, {assistant_count} from planner)[/dim]")

    if messages:
        last = messages[-1]
        role_label = "[cyan]you[/cyan]" if last["role"] == "user" else "[green]planner[/green]"
        preview = last["content"][:150].replace("\n", " ")
        console.print(f"  [dim]Last message ({role_label}): {preview}...[/dim]")
