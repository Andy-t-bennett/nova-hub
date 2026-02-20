"""State machine and persistence for tasks and projects."""

import json
from pathlib import Path

from nova.models import (
    Escalation,
    ProjectPhase,
    ProjectState,
    Task,
    TaskState,
)
from nova.paths import get_project_root


# ---------------------------------------------------------------------------
# Valid transitions
# ---------------------------------------------------------------------------

TASK_TRANSITIONS: dict[TaskState, set[TaskState]] = {
    TaskState.NEW:         {TaskState.READY},
    TaskState.READY:       {TaskState.IN_PROGRESS},
    TaskState.IN_PROGRESS: {TaskState.IN_REVIEW, TaskState.BLOCKED},
    TaskState.IN_REVIEW:   {TaskState.IN_QA, TaskState.IN_PROGRESS, TaskState.BLOCKED},
    TaskState.IN_QA:       {TaskState.DONE, TaskState.IN_PROGRESS, TaskState.BLOCKED},
    TaskState.DONE:        {TaskState.ARCHIVED},
    TaskState.BLOCKED:     {TaskState.READY},
    TaskState.ARCHIVED:    set(),
}

PHASE_TRANSITIONS: dict[ProjectPhase, set[ProjectPhase]] = {
    ProjectPhase.BRAINSTORM:       {ProjectPhase.SPEC_DRAFT},
    ProjectPhase.SPEC_DRAFT:       {ProjectPhase.SPEC_APPROVED},
    ProjectPhase.SPEC_APPROVED:    {ProjectPhase.PLAN_DRAFT},
    ProjectPhase.PLAN_DRAFT:       {ProjectPhase.PLAN_APPROVED},
    ProjectPhase.PLAN_APPROVED:    {ProjectPhase.TASKS_GENERATED},
    ProjectPhase.TASKS_GENERATED:  {ProjectPhase.EXECUTING},
    ProjectPhase.EXECUTING:        {ProjectPhase.COMPLETE},
    ProjectPhase.COMPLETE:         set(),
}


# ---------------------------------------------------------------------------
# State machine operations
# ---------------------------------------------------------------------------

def can_transition_task(current: TaskState, target: TaskState) -> bool:
    return target in TASK_TRANSITIONS.get(current, set())


def transition_task(task: Task, target: TaskState) -> Task:
    """Transition a task to a new state. Raises ValueError if invalid."""
    if not can_transition_task(task.state, target):
        raise ValueError(
            f"Invalid task transition: {task.state.value} → {target.value} "
            f"(task {task.id}). Valid targets: "
            f"{[s.value for s in TASK_TRANSITIONS.get(task.state, set())]}"
        )

    coming_from = task.state
    task.state = target

    if target == TaskState.IN_PROGRESS and coming_from in (TaskState.IN_REVIEW, TaskState.IN_QA):
        task.attempt += 1
    elif target == TaskState.BLOCKED:
        pass  # blocked_reason and escalation_id set by caller
    elif target == TaskState.READY and coming_from == TaskState.BLOCKED:
        task.attempt = 0
        task.blocked_reason = None
        task.escalation_id = None

    from datetime import datetime, timezone
    task.updated_at = datetime.now(timezone.utc).isoformat()
    return task


def can_transition_phase(current: ProjectPhase, target: ProjectPhase) -> bool:
    return target in PHASE_TRANSITIONS.get(current, set())


def transition_phase(state: ProjectState, target: ProjectPhase) -> ProjectState:
    """Transition a project to a new phase. Raises ValueError if invalid."""
    if not can_transition_phase(state.phase, target):
        raise ValueError(
            f"Invalid phase transition: {state.phase.value} → {target.value} "
            f"(project {state.project_name}). Valid targets: "
            f"{[p.value for p in PHASE_TRANSITIONS.get(state.phase, set())]}"
        )

    if target == ProjectPhase.SPEC_APPROVED:
        state.spec_approved = True
    elif target == ProjectPhase.PLAN_APPROVED:
        state.plan_approved = True
    elif target == ProjectPhase.TASKS_GENERATED:
        state.tasks_approved = True

    state.phase = target

    from datetime import datetime, timezone
    state.updated_at = datetime.now(timezone.utc).isoformat()
    return state


# ---------------------------------------------------------------------------
# Task lookup helpers
# ---------------------------------------------------------------------------

def get_task(state: ProjectState, task_id: str) -> Task:
    for task in state.tasks:
        if task.id == task_id:
            return task
    raise KeyError(f"Task '{task_id}' not found in project '{state.project_name}'")


def get_next_ready_task(state: ProjectState) -> Task | None:
    """Return the next READY task by execution order, or None."""
    ready = [t for t in state.tasks if t.state == TaskState.READY]
    if not ready:
        return None
    return sorted(ready, key=lambda t: t.order)[0]


def all_tasks_done(state: ProjectState) -> bool:
    return all(t.state in (TaskState.DONE, TaskState.ARCHIVED) for t in state.tasks)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def _state_file(project_name: str) -> Path:
    return get_project_root(project_name) / "state.json"


def save_state(state: ProjectState) -> Path:
    path = _state_file(state.project_name)
    path.write_text(state.model_dump_json(indent=2))
    return path


def load_state(project_name: str) -> ProjectState:
    path = _state_file(project_name)
    if not path.exists():
        raise FileNotFoundError(
            f"No state file for project '{project_name}'. "
            f"Run 'nova new {project_name}' first."
        )
    data = json.loads(path.read_text())
    return ProjectState.model_validate(data)


def init_state(project_name: str, version: str = "v1") -> ProjectState:
    """Create and save initial project state."""
    state = ProjectState(project_name=project_name, version=version)
    save_state(state)
    return state
