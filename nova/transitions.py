"""Phase transition handling — document locking, state changes, task parsing."""

import json
from pathlib import Path

from rich.console import Console
from rich.panel import Panel

from nova.agent import _extract_json
from nova.models import ProjectPhase, ProjectState, Task, TaskState
from nova.paths import get_project_docs
from nova.state import save_state, transition_phase

console = Console()


# ---------------------------------------------------------------------------
# Document extraction from conversation
# ---------------------------------------------------------------------------

def _get_last_substantial_assistant_message(messages: list[dict[str, str]]) -> str:
    """Get the last substantial assistant response — the actual document, not a short acknowledgment.

    Walks backwards through messages looking for an assistant message that
    contains markdown headers (##) and is reasonably long. Falls back to the
    longest assistant message if no header-based match is found.
    """
    assistant_msgs = [m["content"] for m in messages if m["role"] == "assistant"]
    if not assistant_msgs:
        return ""

    for msg in reversed(assistant_msgs):
        if "## " in msg and len(msg) > 300:
            return msg

    return max(assistant_msgs, key=len)


def _get_all_assistant_content(messages: list[dict[str, str]]) -> str:
    """Concatenate all assistant messages for brainstorm notes."""
    parts = []
    for i, msg in enumerate(messages):
        if msg["role"] == "user":
            parts.append(f"**Human:** {msg['content']}")
        elif msg["role"] == "assistant":
            parts.append(f"**Planner:** {msg['content']}")
    return "\n\n---\n\n".join(parts)


# ---------------------------------------------------------------------------
# Document saving
# ---------------------------------------------------------------------------

def _save_brainstorm_notes(messages: list[dict[str, str]], state: ProjectState) -> Path:
    docs = get_project_docs(state.project_name)
    path = docs / "brainstorm" / f"{state.version}-notes.md"
    path.parent.mkdir(parents=True, exist_ok=True)

    content = f"# Brainstorm Notes — {state.project_name} ({state.version})\n\n"
    content += _get_all_assistant_content(messages)
    path.write_text(content)
    return path


def _save_spec(messages: list[dict[str, str]], state: ProjectState) -> Path:
    docs = get_project_docs(state.project_name)
    path = docs / "spec" / f"{state.version}.md"
    path.parent.mkdir(parents=True, exist_ok=True)

    spec_content = _get_last_substantial_assistant_message(messages)
    content = f"# Spec — {state.project_name} ({state.version})\n\n{spec_content}"
    path.write_text(content)
    return path


def _save_plan(messages: list[dict[str, str]], state: ProjectState) -> Path:
    docs = get_project_docs(state.project_name)
    path = docs / "plans" / f"{state.version}.md"
    path.parent.mkdir(parents=True, exist_ok=True)

    plan_content = _get_last_substantial_assistant_message(messages)
    content = f"# Plan — {state.project_name} ({state.version})\n\n{plan_content}"
    path.write_text(content)
    return path


def _save_and_parse_tasks(messages: list[dict[str, str]], state: ProjectState) -> Path:
    """Parse task JSON from the Planner's response and save to state + file."""
    docs = get_project_docs(state.project_name)
    path = docs / "tasks" / f"{state.version}.tasks.json"
    path.parent.mkdir(parents=True, exist_ok=True)

    last_response = _get_last_substantial_assistant_message(messages)

    try:
        task_data = _extract_json(last_response)
    except (ValueError, json.JSONDecodeError):
        console.print("[red]Error:[/red] Could not parse task JSON from Planner's response.")
        console.print("[yellow]Ask the Planner to regenerate the task list in JSON format.[/yellow]")
        return path

    task_list = task_data if isinstance(task_data, list) else task_data.get("tasks", [])

    tasks: list[Task] = []
    for item in task_list:
        task = Task(
            id=item["id"],
            title=item["title"],
            description=item.get("description", ""),
            acceptance_criteria=item.get("acceptance_criteria", []),
            order=item.get("order", len(tasks) + 1),
            dependencies=item.get("dependencies", []),
            version=state.version,
            state=TaskState.NEW,
        )
        tasks.append(task)

    state.tasks = tasks
    path.write_text(json.dumps(task_list, indent=2))

    return path


# ---------------------------------------------------------------------------
# Transition mapping
# ---------------------------------------------------------------------------

PHASE_ACTIONS: dict[tuple[ProjectPhase, str], dict] = {
    # Brainstorm phase
    (ProjectPhase.BRAINSTORM, "approved"): {
        "save_fn": _save_brainstorm_notes,
        "target_phase": ProjectPhase.SPEC_DRAFT,
        "message": "Brainstorm notes saved. Moving to spec creation.",
        "next_hint": "nova spec {project} --version {version}",
    },
    (ProjectPhase.BRAINSTORM, "ready_for_spec"): {
        "save_fn": _save_brainstorm_notes,
        "target_phase": ProjectPhase.SPEC_DRAFT,
        "message": "Brainstorm notes saved. Moving to spec creation.",
        "next_hint": "nova spec {project} --version {version}",
    },

    # Spec phase
    (ProjectPhase.SPEC_DRAFT, "approved"): {
        "save_fn": _save_spec,
        "target_phase": ProjectPhase.SPEC_APPROVED,
        "message": "Spec approved and locked. Ready to create a plan.",
        "next_hint": "nova plan {project} --version {version}",
    },

    # Plan phase (entered via SPEC_APPROVED → PLAN_DRAFT by the CLI command)
    (ProjectPhase.PLAN_DRAFT, "approved"): {
        "save_fn": _save_plan,
        "target_phase": ProjectPhase.PLAN_APPROVED,
        "message": "Plan approved and locked. Ready to generate tasks.",
        "next_hint": "nova tasks {project} --version {version}",
    },
    (ProjectPhase.PLAN_DRAFT, "ready_for_tasks"): {
        "save_fn": _save_plan,
        "target_phase": ProjectPhase.PLAN_APPROVED,
        "message": "Plan approved and locked. Ready to generate tasks.",
        "next_hint": "nova tasks {project} --version {version}",
    },

    # Tasks phase (Planner has produced tasks, human reviews)
    (ProjectPhase.TASKS_GENERATED, "approved"): {
        "save_fn": _save_and_parse_tasks,
        "target_phase": None,
        "message": "Tasks approved and locked. Ready to execute.",
        "next_hint": "nova run {project} --version {version}",
    },
}


# ---------------------------------------------------------------------------
# Main handler
# ---------------------------------------------------------------------------

def handle_transition(
    action: str,
    messages: list[dict[str, str]],
    state: ProjectState,
) -> bool:
    """Handle a phase transition. Returns True if the session should end.

    Called by the session chat loop when a transition keyword is detected.
    """
    key = (state.phase, action)

    if key not in PHASE_ACTIONS:
        console.print(
            f"[yellow]Cannot transition with '{action}' during {state.phase.value} phase.[/yellow]"
        )
        return False

    config = PHASE_ACTIONS[key]

    # Save the document artifact
    if config["save_fn"]:
        path = config["save_fn"](messages, state)
        console.print(f"[dim]Saved: {path.name}[/dim]")

    # Transition the project phase
    if config["target_phase"]:
        transition_phase(state, config["target_phase"])

    # Special handling for task approval
    if key == (ProjectPhase.TASKS_GENERATED, "approved"):
        state.tasks_approved = True
        for task in state.tasks:
            if task.state == TaskState.NEW:
                task.state = TaskState.READY

    save_state(state)

    # Display result
    next_hint = config["next_hint"].format(
        project=state.project_name,
        version=state.version,
    )
    console.print()
    console.print(
        Panel(
            f"{config['message']}\n\n"
            f"Next: [bold]{next_hint}[/bold]",
            title="[bold]nova[/bold]",
            border_style="green",
        )
    )

    return True
