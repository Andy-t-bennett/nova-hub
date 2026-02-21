"""Nova CLI — entry point for all commands."""

import subprocess
from functools import partial
from pathlib import Path

import typer
from rich.console import Console
from rich.tree import Tree

from nova.config import load_models_config, merge_preferences
from nova.models import AgentRole, ProjectPhase, TaskState
from nova.paths import PROJECTS_DIR, get_project_docs, get_project_logs, get_project_preferences, get_project_root
from nova.prompt import compose_system_prompt
from nova.runner import run_pipeline, run_task as runner_run_task
from nova.session import run_chat_session
from nova.state import get_task, init_state, load_state, save_state, transition_phase, transition_task
from nova.transitions import handle_transition

app = typer.Typer(
    name="nova",
    help="A deterministic multi-agent development runtime.",
    no_args_is_help=True,
)
console = Console()

PROJECT_DIRS = [
    "docs/brainstorm",
    "docs/spec",
    "docs/plans",
    "docs/tasks",
    "docs/decisions",
    "docs/retros",
    "logs/runs",
    "logs/sessions",
    "code",
]

STARTER_PREFERENCES = """\
# Project Preferences — {project_name}
# These override framework_preferences.yaml (deep merge, project wins).
# Prefix with must_ for hard guardrails.

# coding:
#   style_guide: "project-specific rules here"

# testing:
#   prefer_integration_tests: true
"""


@app.callback(invoke_without_command=True)
def main(version: bool = typer.Option(False, "--version", "-v", help="Show version."),) -> None:
    if version:
        from nova import __version__

        console.print(f"[bold]nova[/bold] {__version__}")
        raise typer.Exit()


@app.command()
def new( project_name: str = typer.Argument(..., help="Name of the new project."),) -> None:
    """Create a new project with the full directory structure."""
    project_root = get_project_root(project_name)

    if project_root.exists():
        console.print(f"[red]Error:[/red] Project '{project_name}' already exists at {project_root}")
        raise typer.Exit(code=1)

    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)

    for dir_path in PROJECT_DIRS:
        (project_root / dir_path).mkdir(parents=True, exist_ok=True)

    prefs_file = project_root / "preferences.yaml"
    prefs_file.write_text(STARTER_PREFERENCES.format(project_name=project_name))

    init_state(project_name)
    _git_init(project_root)
    _print_tree(project_name, project_root)

    console.print(
        f"\n[bold green]Project '{project_name}' created.[/bold green] "
        f"Start with [bold]nova brainstorm {project_name}[/bold]"
    )


# ---------------------------------------------------------------------------
# Helper: load context artifacts for prompt composition
# ---------------------------------------------------------------------------

def _load_artifact(project_name: str, *path_parts: str) -> str:
    """Load a text artifact from the project docs directory. Returns empty string if missing."""
    path = get_project_docs(project_name)
    for part in path_parts:
        path = path / part
    if path.exists():
        return path.read_text()
    return ""


def _start_session(
    project_name: str,
    phase: str,
    version: str,
    extra_context: str = "",
    initial_message: str | None = None,
) -> None:
    """Common setup for all interactive session commands."""
    state = load_state(project_name)
    models = load_models_config()
    planner_config = models.roles["planner"]
    preferences = merge_preferences(
        project_path=get_project_preferences(project_name),
    )
    logs_dir = get_project_logs(project_name)

    system_prompt = compose_system_prompt(
        role=AgentRole.PLANNER,
        preferences=preferences,
        extra_context=extra_context,
    )

    def on_transition(action: str, messages: list[dict[str, str]]) -> bool:
        return handle_transition(action, messages, state)

    run_chat_session(
        project_name=project_name,
        phase=phase,
        version=version,
        system_prompt=system_prompt,
        model_config=planner_config,
        logs_dir=logs_dir,
        on_transition=on_transition,
        initial_message=initial_message,
    )


# ---------------------------------------------------------------------------
# Session commands
# ---------------------------------------------------------------------------

@app.command()
def brainstorm(
    project_name: str = typer.Argument(..., help="Project to brainstorm."),
    version: str = typer.Option("v1", "--version", help="Version to brainstorm for."),
) -> None:
    """Start or resume a brainstorm session with the Planner."""
    state = load_state(project_name)

    if state.phase != ProjectPhase.BRAINSTORM:
        console.print(
            f"[yellow]Project is in {state.phase.value} phase, not brainstorm.[/yellow]\n"
            f"Brainstorm is already complete for this version."
        )
        raise typer.Exit(code=1)

    _start_session(
        project_name=project_name,
        phase="brainstorm",
        version=version,
        extra_context=(
            f"This is a new project called '{project_name}'. "
            f"You are in brainstorm mode. Help the user explore and define what they want to build. "
            f"When the conversation has covered enough ground, suggest moving to spec creation."
        ),
    )


@app.command("spec")
def spec_create(
    project_name: str = typer.Argument(..., help="Project to create spec for."),
    version: str = typer.Option("v1", "--version", help="Version."),
) -> None:
    """Create or continue drafting a spec with the Planner."""
    state = load_state(project_name)

    valid_phases = (ProjectPhase.SPEC_DRAFT, ProjectPhase.BRAINSTORM)
    if state.phase not in valid_phases:
        if state.phase == ProjectPhase.SPEC_APPROVED:
            console.print(f"[yellow]Spec for {version} is already approved and locked.[/yellow]")
        else:
            console.print(f"[yellow]Cannot create spec during {state.phase.value} phase.[/yellow]")
        raise typer.Exit(code=1)

    if state.phase == ProjectPhase.BRAINSTORM:
        transition_phase(state, ProjectPhase.SPEC_DRAFT)
        save_state(state)

    brainstorm_notes = _load_artifact(project_name, "brainstorm", f"{version}-notes.md")

    context = (
        "You are in strict spec-writing mode. Write a formal specification based on the brainstorm.\n"
        "The spec must include: overview, requirements, acceptance criteria, and out-of-scope items.\n"
        "The user will give feedback — revise until they say 'approved'."
    )
    if brainstorm_notes:
        context += f"\n\n## Brainstorm Notes\n\n{brainstorm_notes}"

    _start_session(
        project_name=project_name,
        phase="spec",
        version=version,
        extra_context=context,
        initial_message="Draft the spec based on our brainstorm.",
    )


@app.command("plan")
def plan_create(
    project_name: str = typer.Argument(..., help="Project to create plan for."),
    version: str = typer.Option("v1", "--version", help="Version."),
) -> None:
    """Create or continue drafting a plan with the Planner."""
    state = load_state(project_name)

    valid_phases = (ProjectPhase.PLAN_DRAFT, ProjectPhase.SPEC_APPROVED)
    if state.phase not in valid_phases:
        if state.phase == ProjectPhase.PLAN_APPROVED:
            console.print(f"[yellow]Plan for {version} is already approved and locked.[/yellow]")
        else:
            console.print(f"[yellow]Cannot create plan during {state.phase.value} phase.[/yellow]")
        raise typer.Exit(code=1)

    if state.phase == ProjectPhase.SPEC_APPROVED:
        transition_phase(state, ProjectPhase.PLAN_DRAFT)
        save_state(state)

    spec_content = _load_artifact(project_name, "spec", f"{version}.md")

    context = (
        "You are in strict plan-writing mode. Write an implementation plan based on the approved spec.\n"
        "The plan must include: approach, ordered implementation steps, dependencies, and risk areas.\n"
        "The user will give feedback — revise until they say 'approved'."
    )
    if spec_content:
        context += f"\n\n## Approved Spec\n\n{spec_content}"

    _start_session(
        project_name=project_name,
        phase="plan",
        version=version,
        extra_context=context,
        initial_message="Draft the implementation plan based on the approved spec.",
    )


@app.command("tasks")
def tasks_generate(
    project_name: str = typer.Argument(..., help="Project to generate tasks for."),
    version: str = typer.Option("v1", "--version", help="Version."),
) -> None:
    """Generate tasks from the approved spec and plan."""
    state = load_state(project_name)

    valid_phases = (ProjectPhase.TASKS_GENERATED, ProjectPhase.PLAN_APPROVED)
    if state.phase not in valid_phases:
        if state.tasks_approved:
            console.print(f"[yellow]Tasks for {version} are already approved and locked.[/yellow]")
        else:
            console.print(f"[yellow]Cannot generate tasks during {state.phase.value} phase.[/yellow]")
        raise typer.Exit(code=1)

    if state.phase == ProjectPhase.PLAN_APPROVED:
        transition_phase(state, ProjectPhase.TASKS_GENERATED)
        save_state(state)

    spec_content = _load_artifact(project_name, "spec", f"{version}.md")
    plan_content = _load_artifact(project_name, "plans", f"{version}.md")

    context = (
        "You are in task generation mode. Read the approved spec and plan, "
        "then produce a structured JSON task list.\n"
        "Each task must have: id, title, description, acceptance_criteria, order, dependencies.\n"
        "Return the tasks as a JSON array inside a ```json code fence.\n"
        "The user will review and may ask you to adjust. Revise until they say 'approved'."
    )
    if spec_content:
        context += f"\n\n## Approved Spec\n\n{spec_content}"
    if plan_content:
        context += f"\n\n## Approved Plan\n\n{plan_content}"

    _start_session(
        project_name=project_name,
        phase="tasks",
        version=version,
        extra_context=context,
        initial_message="Generate the task list based on the approved spec and plan.",
    )


# ---------------------------------------------------------------------------
# Execution commands
# ---------------------------------------------------------------------------

@app.command("run")
def run_project(
    project_name: str = typer.Argument(..., help="Project to run."),
    version: str = typer.Option("v1", "--version", help="Version."),
) -> None:
    """Run the pipeline — execute tasks through Coder → Lint/Build → QA."""
    state = load_state(project_name)

    valid_phases = (ProjectPhase.TASKS_GENERATED, ProjectPhase.EXECUTING)
    if state.phase not in valid_phases:
        console.print(f"[yellow]Cannot run during {state.phase.value} phase.[/yellow]")
        if state.phase.value in ("brainstorm", "spec_draft", "spec_approved", "plan_draft", "plan_approved"):
            console.print("[dim]Complete the planning phases first (brainstorm → spec → plan → tasks).[/dim]")
        raise typer.Exit(code=1)

    if not state.tasks_approved:
        console.print("[yellow]Tasks must be approved before running. Use `nova tasks` first.[/yellow]")
        raise typer.Exit(code=1)

    if state.phase == ProjectPhase.TASKS_GENERATED:
        transition_phase(state, ProjectPhase.EXECUTING)
        save_state(state)

    models = load_models_config()
    preferences = merge_preferences(
        project_path=get_project_preferences(project_name),
    )

    run_pipeline(state, models, preferences)


@app.command("task")
def task_run(
    project_name: str = typer.Argument(..., help="Project name."),
    task_id: str = typer.Argument(..., help="Task ID to run (e.g., v1-001)."),
) -> None:
    """Run a single task by ID."""
    state = load_state(project_name)

    if state.phase not in (ProjectPhase.TASKS_GENERATED, ProjectPhase.EXECUTING):
        console.print(f"[yellow]Cannot run tasks during {state.phase.value} phase.[/yellow]")
        raise typer.Exit(code=1)

    if state.phase == ProjectPhase.TASKS_GENERATED:
        transition_phase(state, ProjectPhase.EXECUTING)
        save_state(state)

    try:
        task = get_task(state, task_id)
    except KeyError:
        console.print(f"[red]Task '{task_id}' not found.[/red]")
        raise typer.Exit(code=1)

    if task.state not in (TaskState.READY, TaskState.BLOCKED):
        console.print(f"[yellow]Task {task_id} is in {task.state.value} state, not READY.[/yellow]")
        raise typer.Exit(code=1)

    if task.state == TaskState.BLOCKED:
        from nova.state import transition_task as tt
        tt(task, TaskState.READY)
        save_state(state)

    models = load_models_config()
    preferences = merge_preferences(
        project_path=get_project_preferences(project_name),
    )

    runner_run_task(task, state, models, preferences)


@app.command("status")
def status(
    project_name: str = typer.Argument(..., help="Project to inspect."),
) -> None:
    """Show a dashboard of project phase, task states, and attempt counts."""
    from rich.table import Table

    state = load_state(project_name)

    console.print()
    console.print(f"[bold]{project_name}[/bold]  [dim]version {state.version}[/dim]")
    console.print(f"  Phase: [cyan]{state.phase.value}[/cyan]")
    console.print(
        f"  Spec: {'[green]approved[/green]' if state.spec_approved else '[dim]pending[/dim]'}  "
        f"Plan: {'[green]approved[/green]' if state.plan_approved else '[dim]pending[/dim]'}  "
        f"Tasks: {'[green]approved[/green]' if state.tasks_approved else '[dim]pending[/dim]'}"
    )

    if not state.tasks:
        console.print("\n  [dim]No tasks generated yet.[/dim]")
        return

    table = Table(title="Tasks", show_lines=False, padding=(0, 1))
    table.add_column("ID", style="bold")
    table.add_column("Title")
    table.add_column("State")
    table.add_column("Attempts", justify="right")
    table.add_column("Blocked Reason")

    state_colors = {
        TaskState.NEW: "dim",
        TaskState.READY: "white",
        TaskState.IN_PROGRESS: "yellow",
        TaskState.IN_REVIEW: "blue",
        TaskState.IN_QA: "magenta",
        TaskState.DONE: "green",
        TaskState.BLOCKED: "red",
        TaskState.ARCHIVED: "dim",
    }

    done_count = 0
    blocked_count = 0
    for t in sorted(state.tasks, key=lambda t: t.order):
        color = state_colors.get(t.state, "white")
        table.add_row(
            t.id,
            t.title[:50],
            f"[{color}]{t.state.value}[/{color}]",
            str(t.attempt),
            (t.blocked_reason or "")[:60] if t.state == TaskState.BLOCKED else "",
        )
        if t.state == TaskState.DONE:
            done_count += 1
        elif t.state == TaskState.BLOCKED:
            blocked_count += 1

    console.print()
    console.print(table)
    console.print(
        f"\n  [green]{done_count}[/green] done  "
        f"[red]{blocked_count}[/red] blocked  "
        f"[dim]{len(state.tasks) - done_count - blocked_count} remaining[/dim]"
    )

    if state.escalations:
        console.print(f"\n  Escalations: {len(state.escalations)} "
                      f"({sum(1 for e in state.escalations if e.resolved)} resolved)")


@app.command("distill")
def distill_project(
    project_name: str = typer.Argument(..., help="Project to distill."),
) -> None:
    """Run the Distiller to produce a retrospective and extract lessons."""
    from nova.runner import run_distiller

    state = load_state(project_name)

    if state.phase != ProjectPhase.COMPLETE:
        console.print(f"[yellow]Distiller runs after all tasks complete (phase: {state.phase.value}).[/yellow]")
        console.print("[dim]Finish all tasks with `nova run` first.[/dim]")
        raise typer.Exit(code=1)

    models = load_models_config()
    preferences = merge_preferences(
        project_path=get_project_preferences(project_name),
    )

    run_distiller(state, models, preferences)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _git_init(project_root: Path) -> None:
    try:
        subprocess.run(
            ["git", "init"],
            cwd=project_root,
            capture_output=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        console.print(f"[yellow]Warning:[/yellow] Could not initialize git repo: {e}")


def _print_tree(project_name: str, project_root: Path) -> None:
    tree = Tree(f"[bold]{project_name}/[/bold]")
    _add_to_tree(tree, project_root, project_root)
    console.print()
    console.print(tree)


def _add_to_tree(tree: Tree, directory: Path, root: Path) -> None:
    entries = sorted(directory.iterdir(), key=lambda p: (p.is_file(), p.name))
    for entry in entries:
        if entry.name.startswith("."):
            continue
        if entry.is_dir():
            branch = tree.add(f"[bold]{entry.name}/[/bold]")
            _add_to_tree(branch, entry, root)
        else:
            tree.add(entry.name)


if __name__ == "__main__":
    app()
