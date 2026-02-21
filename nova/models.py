"""Core data models for the Nova framework."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class TaskState(str, Enum):
    NEW = "new"
    READY = "ready"
    IN_PROGRESS = "in_progress"
    IN_REVIEW = "in_review"
    IN_QA = "in_qa"
    DONE = "done"
    BLOCKED = "blocked"
    ARCHIVED = "archived"


class ProjectPhase(str, Enum):
    BRAINSTORM = "brainstorm"
    SPEC_DRAFT = "spec_draft"
    SPEC_APPROVED = "spec_approved"
    PLAN_DRAFT = "plan_draft"
    PLAN_APPROVED = "plan_approved"
    TASKS_GENERATED = "tasks_generated"
    EXECUTING = "executing"
    COMPLETE = "complete"


class AgentRole(str, Enum):
    PLANNER = "planner"
    CODER = "coder"
    REVIEWER = "reviewer"
    QA = "qa"
    DISTILLER = "distiller"


class AgentStatus(str, Enum):
    COMPLETE = "complete"
    PASSED = "passed"
    FAILED = "failed"
    BLOCKED = "blocked"


# ---------------------------------------------------------------------------
# Task
# ---------------------------------------------------------------------------

class Task(BaseModel):
    id: str                                          # e.g. "v1-001"
    title: str
    description: str = ""
    acceptance_criteria: list[str] = Field(default_factory=list)
    state: TaskState = TaskState.NEW
    version: str = "v1"
    order: int = 0                                   # execution order
    blocked_reason: str | None = None
    escalation_id: str | None = None
    attempt: int = 0
    dependencies: list[str] = Field(default_factory=list)  # task IDs this depends on
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# ---------------------------------------------------------------------------
# File operations (Coder structured output)
# ---------------------------------------------------------------------------

class FileOperation(BaseModel):
    action: str                                      # "create", "edit", "delete"
    path: str
    content: str = ""


class CommandResult(BaseModel):
    command: str
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""


# ---------------------------------------------------------------------------
# Agent outputs (role-specific)
# ---------------------------------------------------------------------------

class AgentOutput(BaseModel):
    """Common base for all agent responses."""
    role: AgentRole
    status: AgentStatus
    summary: str
    next_action: str = ""
    files_touched: list[str] = Field(default_factory=list)


class CoderOutput(AgentOutput):
    role: AgentRole = AgentRole.CODER
    file_operations: list[FileOperation] = Field(default_factory=list)
    commands: list[str] = Field(default_factory=list)


class ReviewerOutput(AgentOutput):
    role: AgentRole = AgentRole.REVIEWER
    verdict: str = ""                                # "pass" or "fail"
    violations: list[str] = Field(default_factory=list)
    notes: str = ""


class QAOutput(AgentOutput):
    role: AgentRole = AgentRole.QA
    verdict: str = ""
    commands_run: list[CommandResult] = Field(default_factory=list)
    violations: list[str] = Field(default_factory=list)
    notes: str = ""


class PlannerOutput(AgentOutput):
    role: AgentRole = AgentRole.PLANNER
    artifact_content: str = ""                       # spec, plan, or task list content
    decisions: list[str] = Field(default_factory=list)
    # Escalation resolution fields
    resolution: str = ""                             # "retry" or "human_needed"
    guidance: str = ""                               # instructions for Coder on retry


class DistillerOutput(AgentOutput):
    role: AgentRole = AgentRole.DISTILLER
    retro_content: str = ""
    proposed_lessons: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Run log
# ---------------------------------------------------------------------------

class RunLog(BaseModel):
    role: AgentRole
    task_id: str
    attempt: int
    status: AgentStatus
    summary: str = ""
    next_action: str = ""
    files_touched: list[str] = Field(default_factory=list)
    commands: list[CommandResult] = Field(default_factory=list)
    token_usage: dict[str, int] = Field(default_factory=dict)  # {"input": n, "output": n}
    duration_ms: int = 0
    git_commit: str | None = None
    model_used: str = ""
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# ---------------------------------------------------------------------------
# Escalation
# ---------------------------------------------------------------------------

class Escalation(BaseModel):
    id: str
    task_id: str
    from_role: AgentRole
    to_role: AgentRole | None = None                 # None = routed to human
    reason: str
    resolved: bool = False
    resolution: str = ""
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    resolved_at: str | None = None


# ---------------------------------------------------------------------------
# Project state
# ---------------------------------------------------------------------------

class ProjectState(BaseModel):
    project_name: str
    version: str = "v1"
    phase: ProjectPhase = ProjectPhase.BRAINSTORM
    spec_approved: bool = False
    plan_approved: bool = False
    tasks_approved: bool = False
    tasks: list[Task] = Field(default_factory=list)
    escalations: list[Escalation] = Field(default_factory=list)
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
